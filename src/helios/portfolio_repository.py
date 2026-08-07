from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import uuid4

from sqlalchemy import Select, case, delete, func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .models import (
    AiObservation,
    AiRun,
    DailyHolding,
    DailyNav,
    Dividend,
    FactorReturnDaily,
    FxRateDaily,
    Instrument,
    JournalEntry,
    MarketPriceDaily,
    NewsItem,
    OrderHistory,
    PositionLive,
    PositionReconciliation,
    RawNews,
    SyncStatus,
    Thesis,
    Transaction,
)
from .portfolio_transforms import InstrumentSeed

METADATA_ENDPOINT = "/equity/metadata/instruments"
PORTFOLIO_SYNC_LEASE_ENDPOINT = "__portfolio_sync__"
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

    async def acquire_portfolio_sync_lease(
        self,
        *,
        acquired_at: datetime,
        lease_minutes: int,
    ) -> SyncLease:
        expires_at = acquired_at + timedelta(minutes=lease_minutes)
        stale_before = acquired_at - timedelta(minutes=lease_minutes)
        token = uuid4().hex
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    sqlite_insert(SyncStatus)
                    .values(endpoint=PORTFOLIO_SYNC_LEASE_ENDPOINT)
                    .on_conflict_do_nothing(index_elements=[SyncStatus.endpoint])
                )
                await session.execute(
                    update(SyncStatus)
                    .where(SyncStatus.endpoint == PORTFOLIO_SYNC_LEASE_ENDPOINT)
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
                lease_status = await session.get(SyncStatus, PORTFOLIO_SYNC_LEASE_ENDPOINT)
                if (
                    lease_status is None
                    or lease_status.last_status != "running"
                    or lease_status.last_attempt_at != acquired_at
                    or lease_status.last_error != token
                ):
                    raise SyncAlreadyRunningError("Portfolio sync already running")
        return SyncLease(
            endpoint=PORTFOLIO_SYNC_LEASE_ENDPOINT,
            token=token,
            acquired_at=acquired_at,
            expires_at=expires_at,
        )

    async def release_portfolio_sync_lease(
        self,
        *,
        lease: SyncLease,
        completed_at: datetime,
        succeeded: bool,
        error_message: str | None,
    ) -> None:
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
                    .where(SyncStatus.endpoint == PORTFOLIO_SYNC_LEASE_ENDPOINT)
                    .where(SyncStatus.last_status == "running")
                    .where(SyncStatus.last_error == lease.token)
                    .values(values)
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
                .where(SyncStatus.endpoint != PORTFOLIO_SYNC_LEASE_ENDPOINT)
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
            return ReplayInputData(
                instruments=instruments,
                orders=orders,
                dividends=dividends,
                transactions=transactions,
            )

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
    ) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(delete(DailyHolding))
                await session.execute(delete(DailyNav))
                session.add_all(holdings)
                session.add_all(nav_rows)

    async def list_daily_nav(self) -> list[DailyNav]:
        async with self._session_factory() as session:
            return list(await session.scalars(select(DailyNav).order_by(DailyNav.as_of_date)))

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

    async def latest_ai_run(self) -> AiRun | None:
        async with self._session_factory() as session:
            result = await session.scalars(select(AiRun).order_by(AiRun.ts.desc()).limit(1))
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
        """Every known instrument, with the fields a news source template can substitute."""
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

    async def insert_raw_news(self, row: RawNews) -> int:
        async with self._session_factory() as session:
            async with session.begin():
                session.add(row)
            return row.id

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
            ).where(SyncStatus.endpoint != PORTFOLIO_SYNC_LEASE_ENDPOINT)
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
