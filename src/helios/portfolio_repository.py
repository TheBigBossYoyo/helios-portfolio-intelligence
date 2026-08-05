from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import Select, case, func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .models import (
    Dividend,
    Instrument,
    OrderHistory,
    PositionLive,
    PositionReconciliation,
    SyncStatus,
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


class SyncAlreadyRunningError(RuntimeError):
    pass


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
