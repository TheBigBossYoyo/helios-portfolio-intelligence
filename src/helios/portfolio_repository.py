from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import Select, case, delete, func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .models import (
    AiObservation,
    AiRun,
    AllocationTarget,
    CardBudget,
    DailyHolding,
    DailyHoldingFlow,
    DailyNav,
    Dividend,
    FactorReturnDaily,
    FxRateDaily,
    Instrument,
    JournalEntry,
    MarketPriceDaily,
    NewsItem,
    Notification,
    OrderHistory,
    PositionLive,
    PositionReconciliation,
    PriceAlert,
    SyncStatus,
    T212Export,
    T212ExportRow,
    Thesis,
    Transaction,
    WatchlistItem,
)
from .portfolio_transforms import InstrumentSeed
from .raw_store import RawNewsRecord, add_raw_news, list_raw_news

METADATA_ENDPOINT = "/equity/metadata/instruments"
PORTFOLIO_SYNC_LEASE_ENDPOINT = "__portfolio_sync__"
#: Replay rewrites the whole daily_holdings/daily_nav pair, so two concurrent runs duplicate
#: every provider fetch and race to be the last writer. It takes its own lease under a
#: separate key so a running replay never blocks a sync, or the reverse.
PERFORMANCE_REPLAY_LEASE_ENDPOINT = "__performance_replay__"
#: Lease keys are internal bookkeeping rows in ``sync_status``, not real upstream endpoints,
#: so every report that lists endpoint health filters them out.
LEASE_ENDPOINTS = frozenset({PORTFOLIO_SYNC_LEASE_ENDPOINT, PERFORMANCE_REPLAY_LEASE_ENDPOINT})
INSTRUMENT_UPSERT_CHUNK_SIZE = 500


@dataclass(frozen=True)
class MetadataFreshness:
    endpoint: str
    checked_at: datetime
    ttl_hours: int
    last_success_at: datetime | None
    fresh: bool


@dataclass(frozen=True)
class SyncLease:
    endpoint: str
    token: str
    acquired_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class InstrumentNewsTarget:
    """Everything a news source template may substitute, from data Helios already holds."""

    t212_ticker: str
    isin: str | None
    yahoo_ticker: str | None
    name: str | None


@dataclass(frozen=True)
class ReplayInputData:
    instruments: list[Instrument]
    orders: list[OrderHistory]
    dividends: list[Dividend]
    transactions: list[Transaction]
    #: Transaction reference -> export action ("Card debit", "Spending cashback", ...), for
    #: the references a Trading 212 CSV export has labelled.
    export_actions: dict[str, str] = field(default_factory=dict)


class SyncAlreadyRunningError(RuntimeError):
    pass


def _title_already_stored(
    row: NewsItem, titles_by_key: dict[str, list[datetime | None]], window: timedelta
) -> bool:
    """True when this headline is already stored close enough in time to be the same story."""
    if not row.title_key:
        return False
    for published in titles_by_key.get(row.title_key, []):
        if published is None or row.published_at is None:
            # One side undated: the headline is all we have, so treat a match as the same story.
            return True
        if abs(published - row.published_at) <= window:
            return True
    return False


class PortfolioRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_metadata_freshness(
        self,
        *,
        checked_at: datetime,
        ttl_hours: int,
    ) -> MetadataFreshness:
        async with self._session_factory() as session:
            status = await session.get(SyncStatus, METADATA_ENDPOINT)
        last_success_at = status.last_success_at if status is not None else None
        fresh = False
        if last_success_at is not None and status is not None and status.last_status == "success":
            fresh = checked_at - last_success_at <= timedelta(hours=ttl_hours)
        return MetadataFreshness(
            endpoint=METADATA_ENDPOINT,
            checked_at=checked_at,
            ttl_hours=ttl_hours,
            last_success_at=last_success_at,
            fresh=fresh,
        )

    async def record_sync_failure(
        self,
        *,
        endpoint: str,
        attempted_at: datetime,
        error_message: str,
    ) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                status = await session.get(SyncStatus, endpoint)
                if status is None:
                    status = SyncStatus(endpoint=endpoint)
                    session.add(status)
                status.last_attempt_at = attempted_at
                status.last_status = "failed"
                status.item_count = None
                status.last_error = error_message

    async def get_cached_instruments_by_tickers(self, tickers: set[str]) -> dict[str, Instrument]:
        if not tickers:
            return {}
        async with self._session_factory() as session:
            result = await session.scalars(
                select(Instrument)
                .where(Instrument.t212_ticker.in_(sorted(tickers)))
                .order_by(Instrument.t212_ticker)
            )
            return {instrument.t212_ticker: instrument for instrument in result}

    async def get_latest_positions(self) -> list[PositionLive]:
        """The most recently stored live-positions snapshot, or `[]` when none has synced yet.

        Used by `t212_reparse` to recompute reconciliation against a replayed order ledger
        without any network access: the positions themselves are a moment-in-time snapshot only
        a live sync can refresh, but reconciling them against a corrected order history is a
        pure, offline computation reusing `portfolio_sync.build_reconciliations`.
        """
        async with self._session_factory() as session:
            latest_ts = await session.scalar(select(func.max(PositionLive.ts)))
            if latest_ts is None:
                return []
            result = await session.scalars(
                select(PositionLive)
                .where(PositionLive.ts == latest_ts)
                .order_by(PositionLive.t212_ticker)
            )
            return list(result)

    async def acquire_lease(
        self,
        *,
        endpoint: str,
        acquired_at: datetime,
        lease_minutes: int,
        busy_message: str,
    ) -> SyncLease:
        """Take an exclusive lease on ``endpoint``, or refuse.

        The claim is a single conditional UPDATE followed by a read-back of the token we
        wrote: whoever's token survives holds the lease, so two callers racing on the same
        key cannot both believe they won. A lease older than ``lease_minutes`` is treated as
        abandoned, which is what stops a process killed mid-run from locking the key forever.
        """
        expires_at = acquired_at + timedelta(minutes=lease_minutes)
        stale_before = acquired_at - timedelta(minutes=lease_minutes)
        token = uuid4().hex
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    sqlite_insert(SyncStatus)
                    .values(endpoint=endpoint)
                    .on_conflict_do_nothing(index_elements=[SyncStatus.endpoint])
                )
                await session.execute(
                    update(SyncStatus)
                    .where(SyncStatus.endpoint == endpoint)
                    .where(
                        (SyncStatus.last_status != "running")
                        | (SyncStatus.last_status.is_(None))
                        | (SyncStatus.last_attempt_at.is_(None))
                        | (SyncStatus.last_attempt_at < stale_before)
                    )
                    .values(
                        last_attempt_at=acquired_at,
                        last_status="running",
                        item_count=None,
                        last_error=token,
                    )
                )
                lease_status = await session.get(SyncStatus, endpoint)
                if (
                    lease_status is None
                    or lease_status.last_status != "running"
                    or lease_status.last_attempt_at != acquired_at
                    or lease_status.last_error != token
                ):
                    raise SyncAlreadyRunningError(busy_message)
        return SyncLease(
            endpoint=endpoint,
            token=token,
            acquired_at=acquired_at,
            expires_at=expires_at,
        )

    async def release_lease(
        self,
        *,
        lease: SyncLease,
        completed_at: datetime,
        succeeded: bool,
        error_message: str | None,
    ) -> None:
        """Release a lease, but only if we still hold it.

        The token check matters: if this run overran its lease and another already claimed
        the key, releasing unconditionally would hand that live run's lease away.
        """
        async with self._session_factory() as session:
            async with session.begin():
                values: dict[str, object] = {
                    "last_attempt_at": completed_at,
                    "last_status": "success" if succeeded else "failed",
                    "last_error": error_message,
                }
                if succeeded:
                    values["last_success_at"] = completed_at
                await session.execute(
                    update(SyncStatus)
                    .where(SyncStatus.endpoint == lease.endpoint)
                    .where(SyncStatus.last_status == "running")
                    .where(SyncStatus.last_error == lease.token)
                    .values(values)
                )

    async def acquire_portfolio_sync_lease(
        self,
        *,
        acquired_at: datetime,
        lease_minutes: int,
    ) -> SyncLease:
        return await self.acquire_lease(
            endpoint=PORTFOLIO_SYNC_LEASE_ENDPOINT,
            acquired_at=acquired_at,
            lease_minutes=lease_minutes,
            busy_message="Portfolio sync already running",
        )

    async def release_portfolio_sync_lease(
        self,
        *,
        lease: SyncLease,
        completed_at: datetime,
        succeeded: bool,
        error_message: str | None,
    ) -> None:
        await self.release_lease(
            lease=lease,
            completed_at=completed_at,
            succeeded=succeeded,
            error_message=error_message,
        )

    async def acquire_performance_replay_lease(
        self,
        *,
        acquired_at: datetime,
        lease_minutes: int,
    ) -> SyncLease:
        return await self.acquire_lease(
            endpoint=PERFORMANCE_REPLAY_LEASE_ENDPOINT,
            acquired_at=acquired_at,
            lease_minutes=lease_minutes,
            busy_message="Performance replay already running",
        )

    async def release_performance_replay_lease(
        self,
        *,
        lease: SyncLease,
        completed_at: datetime,
        succeeded: bool,
        error_message: str | None,
    ) -> None:
        await self.release_lease(
            lease=lease,
            completed_at=completed_at,
            succeeded=succeeded,
            error_message=error_message,
        )

    async def ingest_domain_snapshot(
        self,
        session: AsyncSession,
        *,
        instrument_seeds: list[InstrumentSeed],
        positions: list[PositionLive],
        transactions: list[Transaction],
        orders: list[OrderHistory],
        dividends: list[Dividend],
        sync_statuses: list[SyncStatus],
        reconciliations: list[PositionReconciliation],
    ) -> None:
        await self._upsert_instrument_seeds(session, instrument_seeds)

        for position_row in positions:
            await session.merge(position_row)
        for transaction_row in transactions:
            await session.merge(transaction_row)
        for order_row in orders:
            await session.merge(order_row)
        for dividend_row in dividends:
            await session.merge(dividend_row)
        for sync_status_row in sync_statuses:
            await session.merge(sync_status_row)
        for reconciliation_row in reconciliations:
            await session.merge(reconciliation_row)

    async def list_endpoint_statuses(self) -> list[SyncStatus]:
        async with self._session_factory() as session:
            result = await session.scalars(
                select(SyncStatus)
                .where(SyncStatus.endpoint.not_in(sorted(LEASE_ENDPOINTS)))
                .order_by(SyncStatus.endpoint)
            )
            return list(result)

    async def load_replay_inputs(self) -> ReplayInputData:
        async with self._session_factory() as session:
            instruments = list(
                await session.scalars(select(Instrument).order_by(Instrument.t212_ticker))
            )
            orders = list(
                await session.scalars(
                    select(OrderHistory).order_by(OrderHistory.fill_timestamp, OrderHistory.fill_id)
                )
            )
            dividends = list(
                await session.scalars(
                    select(Dividend).order_by(Dividend.paid_on, Dividend.reference)
                )
            )
            transactions = list(
                await session.scalars(
                    select(Transaction).order_by(Transaction.ts, Transaction.reference)
                )
            )
            export_actions = {
                row_id: action
                for row_id, action in (
                    await session.execute(select(T212ExportRow.row_id, T212ExportRow.action))
                ).all()
            }
            return ReplayInputData(
                instruments=instruments,
                orders=orders,
                dividends=dividends,
                transactions=transactions,
                export_actions=export_actions,
            )

    # -- Card history (Trading 212 CSV exports) ---------------------------------------------

    async def latest_export(self) -> T212Export | None:
        async with self._session_factory() as session:
            latest: T212Export | None = await session.scalar(
                select(T212Export).order_by(T212Export.requested_at.desc(), T212Export.id.desc())
            )
            return latest

    async def latest_downloaded_export(self) -> T212Export | None:
        async with self._session_factory() as session:
            latest: T212Export | None = await session.scalar(
                select(T212Export)
                .where(T212Export.downloaded_at.is_not(None))
                .order_by(T212Export.downloaded_at.desc(), T212Export.id.desc())
            )
            return latest

    async def add_export(self, export: T212Export) -> int:
        async with self._session_factory() as session:
            async with session.begin():
                session.add(export)
            return export.id

    async def update_export(self, export_id: int, **values: object) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    update(T212Export).where(T212Export.id == export_id).values(**values)
                )

    async def upsert_export_rows(self, rows: Sequence[T212ExportRow]) -> int:
        async with self._session_factory() as session:
            async with session.begin():
                for row in rows:
                    await session.merge(row)
        return len(rows)

    # -- Watchlist ---------------------------------------------------------------------------

    async def list_watchlist(self) -> list[WatchlistItem]:
        async with self._session_factory() as session:
            return list(
                await session.scalars(select(WatchlistItem).order_by(WatchlistItem.added_at))
            )

    async def add_watch(self, ticker: str, *, note: str | None, now: datetime) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                await session.merge(WatchlistItem(t212_ticker=ticker, added_at=now, note=note))

    async def remove_watch(self, ticker: str) -> bool:
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    delete(WatchlistItem).where(WatchlistItem.t212_ticker == ticker)
                )
            return bool(getattr(result, "rowcount", 0))

    async def search_instruments(self, query: str, *, limit: int = 20) -> list[Instrument]:
        """Catalogue search by ticker, short name or name; exact and prefix matches first."""
        text = query.strip()
        if not text:
            return []
        pattern = f"%{text}%"
        upper = text.upper()
        rank = case(
            (func.upper(Instrument.short_name) == upper, 0),
            (func.upper(Instrument.t212_ticker).like(f"{upper}\\_%", escape="\\"), 1),
            (func.upper(Instrument.name).like(f"{upper}%"), 2),
            else_=3,
        )
        async with self._session_factory() as session:
            return list(
                await session.scalars(
                    select(Instrument)
                    .where(
                        Instrument.name.ilike(pattern)
                        | Instrument.short_name.ilike(pattern)
                        | Instrument.t212_ticker.ilike(pattern)
                        | (Instrument.isin == upper)
                    )
                    .order_by(rank, func.length(Instrument.name), Instrument.t212_ticker)
                    .limit(limit)
                )
            )

    async def set_instrument_mapping(
        self,
        ticker: str,
        *,
        yahoo_ticker: str | None,
        status: str,
        source: str | None,
        details: dict[str, object] | None,
        now: datetime,
    ) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    update(Instrument)
                    .where(Instrument.t212_ticker == ticker)
                    .values(
                        yahoo_ticker=yahoo_ticker,
                        mapping_status=status,
                        mapping_source=source,
                        mapping_details_json=details,
                        mapped_at=now,
                    )
                )

    async def held_tickers(self) -> set[str]:
        async with self._session_factory() as session:
            latest_ts = await session.scalar(select(func.max(PositionLive.ts)))
            if latest_ts is None:
                return set()
            return set(
                await session.scalars(
                    select(PositionLive.t212_ticker).where(PositionLive.ts == latest_ts)
                )
            )

    # -- Alerts and notifications ------------------------------------------------------------

    async def list_price_alerts(
        self, *, active_only: bool = False, ticker: str | None = None
    ) -> list[PriceAlert]:
        async with self._session_factory() as session:
            statement = select(PriceAlert).order_by(PriceAlert.created_at.desc(), PriceAlert.id)
            if active_only:
                statement = statement.where(PriceAlert.active.is_(True))
            if ticker is not None:
                statement = statement.where(PriceAlert.ticker == ticker)
            return list(await session.scalars(statement))

    async def add_price_alert(self, alert: PriceAlert) -> PriceAlert:
        async with self._session_factory() as session:
            async with session.begin():
                session.add(alert)
            return alert

    async def delete_price_alert(self, alert_id: int) -> bool:
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(delete(PriceAlert).where(PriceAlert.id == alert_id))
            return bool(getattr(result, "rowcount", 0))

    async def fire_price_alert(self, alert_id: int, *, price: Decimal, now: datetime) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    update(PriceAlert)
                    .where(PriceAlert.id == alert_id)
                    .values(active=False, triggered_at=now, triggered_price=price)
                )

    async def add_notification(self, notification: Notification) -> bool:
        """Store a notification unless one with the same dedupe key exists. True if stored."""
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    sqlite_insert(Notification)
                    .values(
                        kind=notification.kind,
                        title=notification.title,
                        body=notification.body,
                        url=notification.url,
                        created_at=notification.created_at,
                        delivered_at=None,
                        dedupe_key=notification.dedupe_key,
                    )
                    .on_conflict_do_nothing(index_elements=[Notification.dedupe_key])
                )
            return bool(getattr(result, "rowcount", 0))

    async def has_notification(self, dedupe_key: str) -> bool:
        async with self._session_factory() as session:
            found = await session.scalar(
                select(Notification.id).where(Notification.dedupe_key == dedupe_key)
            )
            return found is not None

    async def list_notifications(
        self, *, limit: int = 20, pending_only: bool = False
    ) -> list[Notification]:
        async with self._session_factory() as session:
            statement = select(Notification).order_by(
                Notification.created_at.desc(), Notification.id.desc()
            )
            if pending_only:
                statement = statement.where(Notification.delivered_at.is_(None))
            return list(await session.scalars(statement.limit(limit)))

    async def mark_notification_delivered(self, notification_id: int, *, now: datetime) -> bool:
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    update(Notification)
                    .where(Notification.id == notification_id)
                    .where(Notification.delivered_at.is_(None))
                    .values(delivered_at=now)
                )
            return bool(getattr(result, "rowcount", 0))

    async def list_card_budgets(self) -> list[CardBudget]:
        async with self._session_factory() as session:
            return list(await session.scalars(select(CardBudget).order_by(CardBudget.category)))

    async def set_card_budget(
        self, category: str, monthly_limit: Decimal | None, *, now: datetime
    ) -> None:
        """Set a category's monthly limit; ``None`` removes it."""
        async with self._session_factory() as session:
            async with session.begin():
                if monthly_limit is None:
                    await session.execute(delete(CardBudget).where(CardBudget.category == category))
                    return
                await session.merge(
                    CardBudget(category=category, monthly_limit=monthly_limit, updated_at=now)
                )

    async def list_allocation_targets(self) -> list[AllocationTarget]:
        async with self._session_factory() as session:
            return list(
                await session.scalars(
                    select(AllocationTarget).order_by(AllocationTarget.t212_ticker)
                )
            )

    async def replace_allocation_targets(
        self, targets: dict[str, Decimal], *, now: datetime
    ) -> None:
        """The whole plan at once: tickers left out lose their target."""
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(delete(AllocationTarget))
                session.add_all(
                    AllocationTarget(t212_ticker=ticker, target_weight=weight, updated_at=now)
                    for ticker, weight in targets.items()
                    if weight > 0
                )

    async def list_withdrawals_since(self, since: datetime) -> list[Transaction]:
        """Cash leaving the account from ``since`` on, with anything sharing their instants."""
        async with self._session_factory() as session:
            return list(
                await session.scalars(
                    select(Transaction)
                    .where(Transaction.ts >= since)
                    .order_by(Transaction.ts, Transaction.reference)
                )
            )

    async def list_export_rows(self) -> list[T212ExportRow]:
        async with self._session_factory() as session:
            return list(
                await session.scalars(select(T212ExportRow).order_by(T212ExportRow.ts.desc()))
            )

    async def count_export_rows(self) -> tuple[int, int]:
        """(card rows, all cash rows) stored from exports."""
        async with self._session_factory() as session:
            total = await session.scalar(select(func.count()).select_from(T212ExportRow)) or 0
            card = (
                await session.scalar(
                    select(func.count())
                    .select_from(T212ExportRow)
                    .where(func.lower(T212ExportRow.action).like("card %"))
                )
                or 0
            )
            return int(card), int(total)

    async def first_ledger_event_at(self) -> datetime | None:
        """The earliest cash transaction or fill: where a first export should start."""
        async with self._session_factory() as session:
            first_transaction = await session.scalar(select(func.min(Transaction.ts)))
            first_fill = await session.scalar(select(func.min(OrderHistory.fill_timestamp)))
        candidates = [value for value in (first_transaction, first_fill) if value is not None]
        return min(candidates) if candidates else None

    async def list_market_prices(
        self,
        *,
        start_date: date,
        end_date: date,
    ) -> dict[str, list[MarketPriceDaily]]:
        async with self._session_factory() as session:
            rows = list(
                await session.scalars(
                    select(MarketPriceDaily)
                    .where(MarketPriceDaily.price_date >= start_date)
                    .where(MarketPriceDaily.price_date <= end_date)
                    .order_by(MarketPriceDaily.t212_ticker, MarketPriceDaily.price_date)
                )
            )
        grouped: dict[str, list[MarketPriceDaily]] = {}
        for row in rows:
            grouped.setdefault(row.t212_ticker, []).append(row)
        return grouped

    async def upsert_market_prices(self, rows: list[MarketPriceDaily]) -> None:
        if not rows:
            return
        async with self._session_factory() as session:
            async with session.begin():
                for row in rows:
                    await session.merge(row)

    async def list_fx_rates(
        self,
        *,
        start_date: date,
        end_date: date,
    ) -> dict[str, list[FxRateDaily]]:
        async with self._session_factory() as session:
            rows = list(
                await session.scalars(
                    select(FxRateDaily)
                    .where(FxRateDaily.rate_date >= start_date)
                    .where(FxRateDaily.rate_date <= end_date)
                    .order_by(FxRateDaily.currency_code, FxRateDaily.rate_date)
                )
            )
        grouped: dict[str, list[FxRateDaily]] = {}
        for row in rows:
            grouped.setdefault(row.currency_code, []).append(row)
        return grouped

    async def upsert_fx_rates(self, rows: list[FxRateDaily]) -> None:
        if not rows:
            return
        async with self._session_factory() as session:
            async with session.begin():
                for row in rows:
                    await session.merge(row)

    async def list_factor_returns(
        self,
        *,
        start_date: date,
        end_date: date,
    ) -> list[FactorReturnDaily]:
        async with self._session_factory() as session:
            return list(
                await session.scalars(
                    select(FactorReturnDaily)
                    .where(FactorReturnDaily.as_of_date >= start_date)
                    .where(FactorReturnDaily.as_of_date <= end_date)
                    .order_by(FactorReturnDaily.as_of_date)
                )
            )

    async def upsert_factor_returns(self, rows: list[FactorReturnDaily]) -> None:
        if not rows:
            return
        async with self._session_factory() as session:
            async with session.begin():
                for row in rows:
                    await session.merge(row)

    async def replace_daily_replay(
        self,
        *,
        holdings: list[DailyHolding],
        nav_rows: list[DailyNav],
        flows: Sequence[DailyHoldingFlow] = (),
    ) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(delete(DailyHolding))
                await session.execute(delete(DailyNav))
                await session.execute(delete(DailyHoldingFlow))
                session.add_all(holdings)
                session.add_all(nav_rows)
                session.add_all(flows)

    async def list_daily_nav(self) -> list[DailyNav]:
        async with self._session_factory() as session:
            return list(await session.scalars(select(DailyNav).order_by(DailyNav.as_of_date)))

    # -- One instrument, for its detail page ------------------------------------------------

    async def list_prices_for(self, ticker: str) -> list[MarketPriceDaily]:
        async with self._session_factory() as session:
            return list(
                await session.scalars(
                    select(MarketPriceDaily)
                    .where(MarketPriceDaily.t212_ticker == ticker)
                    .order_by(MarketPriceDaily.price_date)
                )
            )

    async def list_daily_holdings_for(self, ticker: str) -> list[DailyHolding]:
        async with self._session_factory() as session:
            return list(
                await session.scalars(
                    select(DailyHolding)
                    .where(DailyHolding.t212_ticker == ticker)
                    .order_by(DailyHolding.as_of_date)
                )
            )

    async def list_daily_holding_flows_for(self, ticker: str) -> list[DailyHoldingFlow]:
        async with self._session_factory() as session:
            return list(
                await session.scalars(
                    select(DailyHoldingFlow)
                    .where(DailyHoldingFlow.t212_ticker == ticker)
                    .order_by(DailyHoldingFlow.as_of_date)
                )
            )

    async def list_orders_for(self, ticker: str) -> list[OrderHistory]:
        async with self._session_factory() as session:
            return list(
                await session.scalars(
                    select(OrderHistory)
                    .where(OrderHistory.t212_ticker == ticker)
                    .order_by(OrderHistory.fill_timestamp, OrderHistory.fill_id)
                )
            )

    async def list_dividends_for(self, ticker: str) -> list[Dividend]:
        async with self._session_factory() as session:
            return list(
                await session.scalars(
                    select(Dividend)
                    .where(Dividend.t212_ticker == ticker)
                    .order_by(Dividend.paid_on)
                )
            )

    async def list_daily_holding_flows(self) -> list[DailyHoldingFlow]:
        async with self._session_factory() as session:
            return list(
                await session.scalars(
                    select(DailyHoldingFlow).order_by(
                        DailyHoldingFlow.as_of_date, DailyHoldingFlow.t212_ticker
                    )
                )
            )

    async def list_daily_holdings(self) -> list[DailyHolding]:
        async with self._session_factory() as session:
            return list(
                await session.scalars(
                    select(DailyHolding).order_by(DailyHolding.as_of_date, DailyHolding.t212_ticker)
                )
            )

    async def insert_thesis(self, row: Thesis) -> Thesis:
        async with self._session_factory() as session:
            async with session.begin():
                session.add(row)
            return row

    async def get_thesis(self, thesis_id: int) -> Thesis | None:
        async with self._session_factory() as session:
            return await session.get(Thesis, thesis_id)

    async def update_thesis(self, thesis_id: int, **changes: object) -> Thesis:
        """Apply only the fields actually supplied; `None` means 'leave alone'.

        `closed_at` is the exception — it is set explicitly by a transition and may legitimately
        be cleared, so it is applied whenever the caller passes the key at all.
        """
        async with self._session_factory() as session:
            async with session.begin():
                thesis = await session.get(Thesis, thesis_id)
                if thesis is None:
                    raise LookupError(f"no thesis with id {thesis_id}")
                for field, value in changes.items():
                    if value is None and field != "closed_at":
                        continue
                    setattr(thesis, field, value)
            return thesis

    async def list_theses(self, *, status: str | None = None) -> list[Thesis]:
        async with self._session_factory() as session:
            statement = select(Thesis)
            if status is not None:
                statement = statement.where(Thesis.status == status)
            return list(
                await session.scalars(statement.order_by(Thesis.created_at.desc(), Thesis.id))
            )

    async def insert_journal_entry(self, row: JournalEntry) -> JournalEntry:
        async with self._session_factory() as session:
            async with session.begin():
                session.add(row)
            return row

    async def list_journal_entries(
        self, *, thesis_id: int | None = None, limit: int = 100
    ) -> list[JournalEntry]:
        async with self._session_factory() as session:
            statement = select(JournalEntry)
            if thesis_id is not None:
                statement = statement.where(JournalEntry.thesis_id == thesis_id)
            return list(
                await session.scalars(
                    statement.order_by(
                        JournalEntry.created_at.desc(), JournalEntry.id.desc()
                    ).limit(limit)
                )
            )

    async def latest_holding_weight(self, ticker: str) -> tuple[float | None, float | None]:
        """Current NAV weight for one holding, or (None, None) when it is not held."""
        async with self._session_factory() as session:
            latest_date = await session.scalar(select(func.max(DailyHolding.as_of_date)))
            if latest_date is None:
                return None, None
            rows = list(
                await session.scalars(
                    select(DailyHolding).where(DailyHolding.as_of_date == latest_date)
                )
            )
        valued = [row for row in rows if row.market_value_eur is not None]
        total = sum(float(row.market_value_eur or 0) for row in valued)
        if total == 0.0:
            return None, None
        match = next((row for row in valued if row.t212_ticker == ticker), None)
        if match is None:
            return None, None
        return float(match.market_value_eur or 0) / total, None

    async def insert_ai_run(self, row: AiRun) -> int:
        async with self._session_factory() as session:
            async with session.begin():
                session.add(row)
            return row.id

    async def insert_ai_observations(self, rows: list[AiObservation]) -> None:
        if not rows:
            return
        async with self._session_factory() as session:
            async with session.begin():
                session.add_all(rows)

    async def latest_ai_run(self, kind: str = "analysis") -> AiRun | None:
        async with self._session_factory() as session:
            result = await session.scalars(
                select(AiRun).where(AiRun.kind == kind).order_by(AiRun.ts.desc()).limit(1)
            )
            return result.first()

    async def list_ai_observations(self, run_id: int) -> list[AiObservation]:
        async with self._session_factory() as session:
            return list(
                await session.scalars(
                    select(AiObservation)
                    .where(AiObservation.run_id == run_id)
                    .order_by(AiObservation.rank)
                )
            )

    async def list_instrument_news_targets(self) -> list[InstrumentNewsTarget]:
        """Instruments you hold or watch, with the fields a news template can substitute.

        Scoped to live positions and the watchlist on purpose. The instruments table is the full
        Trading 212 catalogue -- around 17,000 rows -- and a news source is fetched once per
        target per feed, so using it would issue tens of thousands of outbound requests per sync
        and get the deployment rate-limited or blocked by the publisher. It would also be useless:
        news about instruments you neither own nor follow is noise.
        """
        async with self._session_factory() as session:
            latest_ts = await session.scalar(select(func.max(PositionLive.ts)))
            watched = select(WatchlistItem.t212_ticker)
            held = (
                select(PositionLive.t212_ticker).where(PositionLive.ts == latest_ts)
                if latest_ts is not None
                else select(PositionLive.t212_ticker).where(PositionLive.ts.is_(None))
            )
            rows = await session.execute(
                select(
                    Instrument.t212_ticker,
                    Instrument.isin,
                    Instrument.yahoo_ticker,
                    Instrument.name,
                )
                .where(Instrument.t212_ticker.in_(held) | Instrument.t212_ticker.in_(watched))
                .order_by(Instrument.t212_ticker)
            )
            return [
                InstrumentNewsTarget(t212_ticker=ticker, isin=isin, yahoo_ticker=yahoo, name=name)
                for ticker, isin, yahoo, name in rows.all()
            ]

    async def insert_raw_news(self, record: RawNewsRecord) -> int:
        """Store a feed body, compressed against the previous fetch of the same feed."""
        async with self._session_factory() as session:
            async with session.begin():
                row = await add_raw_news(session, record)
            return row.id

    async def list_raw_news(self) -> list[RawNewsRecord]:
        """Every stored raw feed body, oldest first -- the replay input for `news-reparse`."""
        async with self._session_factory() as session:
            return await list_raw_news(session)

    async def list_all_instrument_news_targets(self) -> list[InstrumentNewsTarget]:
        """Every instrument Helios has metadata for, not just ones currently held.

        `list_instrument_news_targets` is scoped to live positions to bound how many outbound
        requests a live sync issues. Replay makes no outbound requests at all, so it can attribute
        a `url_template` feed's stored URL against the full instrument table -- including a
        position closed since the row was fetched -- rather than losing that attribution just
        because the holding is gone today.
        """
        async with self._session_factory() as session:
            rows = await session.execute(
                select(
                    Instrument.t212_ticker,
                    Instrument.isin,
                    Instrument.yahoo_ticker,
                    Instrument.name,
                ).order_by(Instrument.t212_ticker)
            )
            return [
                InstrumentNewsTarget(t212_ticker=ticker, isin=isin, yahoo_ticker=yahoo, name=name)
                for ticker, isin, yahoo, name in rows.all()
            ]

    async def upsert_news_items(
        self, rows: list[NewsItem], *, dedupe_window_hours: int = 48
    ) -> int:
        """Insert new articles, skipping ones already stored. Returns the number written.

        Three checks, because a story can already be present under a different identity:
        its exact `dedupe_key`, its canonical URL (same article, different tracking params), or
        its title key within the dedupe window (same article, different source and URL).

        Conflicts are ignored rather than updated: a re-fetch must not overwrite the
        `fetched_at` of the first sighting, nor demote an item to a lower-trust source's copy.
        """
        if not rows:
            return 0
        keys = [row.dedupe_key for row in rows]
        canonical_urls = [row.canonical_url for row in rows if row.canonical_url]
        title_keys = [row.title_key for row in rows if row.title_key]
        window = timedelta(hours=dedupe_window_hours)
        async with self._session_factory() as session:
            async with session.begin():
                existing = set(
                    await session.scalars(
                        select(NewsItem.dedupe_key).where(NewsItem.dedupe_key.in_(keys))
                    )
                )
                existing_urls = set(
                    await session.scalars(
                        select(NewsItem.canonical_url).where(
                            NewsItem.canonical_url.in_(canonical_urls)
                        )
                    )
                )
                seen_titles = (
                    await session.execute(
                        select(NewsItem.title_key, NewsItem.published_at).where(
                            NewsItem.title_key.in_(title_keys)
                        )
                    )
                ).all()
                titles_by_key: dict[str, list[datetime | None]] = {}
                for key, published in seen_titles:
                    titles_by_key.setdefault(key, []).append(published)

                fresh: list[NewsItem] = []
                for row in rows:
                    if row.dedupe_key in existing or row.canonical_url in existing_urls:
                        continue
                    if _title_already_stored(row, titles_by_key, window):
                        continue
                    fresh.append(row)
                    existing.add(row.dedupe_key)
                    existing_urls.add(row.canonical_url)
                    titles_by_key.setdefault(row.title_key, []).append(row.published_at)
                if fresh:
                    await session.execute(
                        sqlite_insert(NewsItem)
                        .values(
                            [
                                {
                                    "dedupe_key": row.dedupe_key,
                                    "feed_key": row.feed_key,
                                    "provider": row.provider,
                                    "source_label": row.source_label,
                                    "t212_ticker": row.t212_ticker,
                                    "isin": row.isin,
                                    "headline": row.headline,
                                    "summary": row.summary,
                                    "url": row.url,
                                    "canonical_url": row.canonical_url,
                                    "title_key": row.title_key,
                                    "published_at": row.published_at,
                                    "fetched_at": row.fetched_at,
                                    "raw_news_id": row.raw_news_id,
                                }
                                for row in fresh
                            ]
                        )
                        .on_conflict_do_nothing(index_elements=[NewsItem.dedupe_key])
                    )
        return len(fresh)

    async def list_news_items(
        self,
        *,
        t212_ticker: str | None = None,
        isin: str | None = None,
        limit: int = 50,
    ) -> list[NewsItem]:
        async with self._session_factory() as session:
            statement = select(NewsItem)
            if t212_ticker is not None:
                statement = statement.where(NewsItem.t212_ticker == t212_ticker)
            if isin is not None:
                statement = statement.where(NewsItem.isin == isin)
            # Undated items sort last rather than being dropped or dated by guesswork.
            statement = statement.order_by(
                NewsItem.published_at.is_(None),
                NewsItem.published_at.desc(),
                NewsItem.headline,
            ).limit(limit)
            return list(await session.scalars(statement))

    async def list_instruments_with_status(self, status: str) -> list[Instrument]:
        async with self._session_factory() as session:
            result = await session.scalars(
                select(Instrument)
                .where(Instrument.mapping_status == status)
                .order_by(Instrument.t212_ticker)
            )
            return list(result)

    async def list_reconciliation_by_status(self, status: str) -> list[PositionReconciliation]:
        async with self._session_factory() as session:
            latest_ts = await session.scalar(select(func.max(PositionReconciliation.ts)))
            if latest_ts is None:
                return []
            result = await session.scalars(
                select(PositionReconciliation)
                .where(PositionReconciliation.ts == latest_ts)
                .where(PositionReconciliation.status == status)
                .order_by(PositionReconciliation.t212_ticker)
            )
            return list(result)

    async def latest_report_timestamp(self) -> datetime | None:
        async with self._session_factory() as session:
            statement: Select[tuple[datetime | None]] = select(
                func.max(SyncStatus.last_attempt_at)
            ).where(SyncStatus.endpoint.not_in(sorted(LEASE_ENDPOINTS)))
            return await session.scalar(statement)

    async def _upsert_instrument_seeds(
        self,
        session: AsyncSession,
        seeds: list[InstrumentSeed],
    ) -> None:
        if not seeds:
            return

        instrument_table = Instrument.__table__
        for start in range(0, len(seeds), INSTRUMENT_UPSERT_CHUNK_SIZE):
            chunk = seeds[start : start + INSTRUMENT_UPSERT_CHUNK_SIZE]
            values = [
                {
                    "t212_ticker": seed.t212_ticker,
                    "isin": seed.isin,
                    "name": seed.name,
                    "short_name": seed.short_name,
                    "currency_code": seed.currency_code,
                    "instrument_type": seed.instrument_type,
                    "added_on": seed.added_on,
                    "extended_hours": seed.extended_hours,
                    "max_open_quantity": seed.max_open_quantity,
                    "working_schedule_id": seed.working_schedule_id,
                    "exchange_id": seed.exchange_id,
                    "yahoo_ticker": seed.mapping_result.yahoo_ticker,
                    "mapping_status": seed.mapping_result.status,
                    "mapping_source": seed.mapping_result.source,
                    "mapping_details_json": seed.mapping_result.details,
                    "mapped_at": seed.mapped_at,
                }
                for seed in chunk
            ]
            insert_stmt = sqlite_insert(Instrument).values(values)
            preserved_resolved = instrument_table.c.mapping_status == "resolved"
            degraded_mapping = insert_stmt.excluded.mapping_status.in_(
                ["not_required", "unresolved", "ambiguous", "override_required"]
            )
            update_stmt = insert_stmt.on_conflict_do_update(
                index_elements=[instrument_table.c.t212_ticker],
                set_={
                    "isin": func.coalesce(insert_stmt.excluded.isin, instrument_table.c.isin),
                    "name": func.coalesce(insert_stmt.excluded.name, instrument_table.c.name),
                    "short_name": func.coalesce(
                        insert_stmt.excluded.short_name,
                        instrument_table.c.short_name,
                    ),
                    "currency_code": func.coalesce(
                        insert_stmt.excluded.currency_code,
                        instrument_table.c.currency_code,
                    ),
                    "instrument_type": func.coalesce(
                        insert_stmt.excluded.instrument_type,
                        instrument_table.c.instrument_type,
                    ),
                    "added_on": func.coalesce(
                        insert_stmt.excluded.added_on,
                        instrument_table.c.added_on,
                    ),
                    "extended_hours": func.coalesce(
                        insert_stmt.excluded.extended_hours,
                        instrument_table.c.extended_hours,
                    ),
                    "max_open_quantity": func.coalesce(
                        insert_stmt.excluded.max_open_quantity,
                        instrument_table.c.max_open_quantity,
                    ),
                    "working_schedule_id": func.coalesce(
                        insert_stmt.excluded.working_schedule_id,
                        instrument_table.c.working_schedule_id,
                    ),
                    "exchange_id": func.coalesce(
                        insert_stmt.excluded.exchange_id,
                        instrument_table.c.exchange_id,
                    ),
                    "yahoo_ticker": case(
                        (preserved_resolved & degraded_mapping, instrument_table.c.yahoo_ticker),
                        else_=insert_stmt.excluded.yahoo_ticker,
                    ),
                    "mapping_status": case(
                        (preserved_resolved & degraded_mapping, instrument_table.c.mapping_status),
                        else_=insert_stmt.excluded.mapping_status,
                    ),
                    "mapping_source": case(
                        (preserved_resolved & degraded_mapping, instrument_table.c.mapping_source),
                        else_=insert_stmt.excluded.mapping_source,
                    ),
                    "mapping_details_json": case(
                        (
                            preserved_resolved & degraded_mapping,
                            instrument_table.c.mapping_details_json,
                        ),
                        else_=insert_stmt.excluded.mapping_details_json,
                    ),
                    "mapped_at": case(
                        (preserved_resolved & degraded_mapping, instrument_table.c.mapped_at),
                        else_=insert_stmt.excluded.mapped_at,
                    ),
                },
            )
            await session.execute(update_stmt)
