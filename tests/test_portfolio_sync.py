from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from helios.config import Settings
from helios.db import migrate_database
from helios.models import (
    Dividend,
    Instrument,
    OrderHistory,
    PositionLive,
    PositionReconciliation,
    SyncStatus,
    Transaction,
)
from helios.portfolio_repository import (
    METADATA_ENDPOINT,
    PORTFOLIO_SYNC_LEASE_ENDPOINT,
    PortfolioRepository,
    SyncAlreadyRunningError,
)
from helios.portfolio_sync import (
    DIVIDENDS_ENDPOINT,
    ORDERS_ENDPOINT,
    POSITIONS_ENDPOINT,
    TRANSACTIONS_ENDPOINT,
    PortfolioSyncService,
    build_reconciliations,
)
from helios.portfolio_transforms import (
    DomainTransformError,
    InstrumentSeed,
    dividend_from_dto,
    order_history_from_dto,
    position_live_from_dto,
    transaction_from_dto,
)
from helios.rate_limit import Clock
from helios.reporting import PortfolioQualityReportService
from helios.resolver import (
    InstrumentMappingResult,
    InstrumentResolutionRequest,
    InstrumentResolver,
)
from helios.schemas import (
    DividendItem,
    HistoricalOrderItem,
    InstrumentMetadata,
    Position,
    TransactionItem,
)


@dataclass
class FixedClock(Clock):
    current: datetime
    sleeps: list[float] = field(default_factory=list)

    def now(self) -> float:
        return self.current.timestamp()

    def utcnow(self) -> datetime:
        return self.current

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.current += timedelta(seconds=seconds)


class StubResolver(InstrumentResolver):
    def __init__(self, results: dict[str, InstrumentMappingResult] | None = None) -> None:
        self.results = results or {}
        self.requests: list[InstrumentResolutionRequest] = []

    async def resolve(self, request: InstrumentResolutionRequest) -> InstrumentMappingResult:
        self.requests.append(request)
        return self.results.get(
            request.t212_ticker,
            InstrumentMappingResult(
                status="resolved",
                source="override",
                yahoo_ticker=request.t212_ticker.replace("_US_EQ", ""),
                details={"resolver": "stub"},
            ),
        )


class FakeTrading212Client:
    def __init__(
        self,
        *,
        instruments: list[InstrumentMetadata] | None = None,
        positions: list[Position] | None = None,
        orders: list[HistoricalOrderItem] | None = None,
        dividends: list[DividendItem] | None = None,
        transactions: list[TransactionItem] | None = None,
        failures: dict[str, Exception] | None = None,
    ) -> None:
        self.instruments = instruments or []
        self.positions = positions or []
        self.orders = orders or []
        self.dividends = dividends or []
        self.transactions = transactions or []
        self.failures = failures or {}
        self.calls: list[str] = []

    async def get_instruments(self) -> list[InstrumentMetadata]:
        self.calls.append(METADATA_ENDPOINT)
        if METADATA_ENDPOINT in self.failures:
            raise self.failures[METADATA_ENDPOINT]
        return self.instruments

    async def get_positions(self) -> list[Position]:
        self.calls.append(POSITIONS_ENDPOINT)
        if POSITIONS_ENDPOINT in self.failures:
            raise self.failures[POSITIONS_ENDPOINT]
        return self.positions

    async def get_history_orders(self) -> list[HistoricalOrderItem]:
        self.calls.append(ORDERS_ENDPOINT)
        if ORDERS_ENDPOINT in self.failures:
            raise self.failures[ORDERS_ENDPOINT]
        return self.orders

    async def get_history_dividends(self) -> list[DividendItem]:
        self.calls.append(DIVIDENDS_ENDPOINT)
        if DIVIDENDS_ENDPOINT in self.failures:
            raise self.failures[DIVIDENDS_ENDPOINT]
        return self.dividends

    async def get_history_transactions(self) -> list[TransactionItem]:
        self.calls.append(TRANSACTIONS_ENDPOINT)
        if TRANSACTIONS_ENDPOINT in self.failures:
            raise self.failures[TRANSACTIONS_ENDPOINT]
        return self.transactions


@pytest.mark.asyncio
async def test_exact_dto_transforms() -> None:
    synced_at = datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)
    position = position_live_from_dto(_position_payload(), synced_at=synced_at)
    order = order_history_from_dto(_order_payload(fill_id=11, order_id=22, side="SELL"))
    dividend = dividend_from_dto(_dividend_payload())
    transaction = transaction_from_dto(_transaction_payload())

    assert position.ts == synced_at
    assert position.wallet_currency == "EUR"
    assert order.fill_id == "11"
    assert order.order_id == "22"
    assert order.fill_type == "TRADE"
    assert order.instrument_currency_code == "USD"
    assert order.wallet_taxes_json == [
        {"currency": "USD", "name": "STAMP_DUTY", "quantity": "0.50"}
    ]
    assert dividend.paid_on == datetime(2024, 1, 8, tzinfo=UTC)
    assert dividend.gross_amount_per_share == Decimal("0.1234")
    assert dividend.ticker_currency == "USD"
    assert transaction.reference == "txn-1"
    assert transaction.ts == datetime(2024, 1, 9, 10, 11, 12, tzinfo=UTC)
    assert transaction.currency_code == "EUR"


def test_order_transform_uses_fill_quantity_for_partial_fills() -> None:
    first_fill = order_history_from_dto(
        _order_payload(
            fill_id="fill-1",
            order_id="order-1",
            side="BUY",
            fill_quantity="1.000",
            order_filled_quantity="2.000",
        )
    )
    second_fill = order_history_from_dto(
        _order_payload(
            fill_id="fill-2",
            order_id="order-1",
            side="BUY",
            fill_quantity="1.000",
            order_filled_quantity="2.000",
        )
    )

    rows = build_reconciliations(
        synced_at=datetime(2024, 1, 4, tzinfo=UTC),
        positions=[],
        orders=[first_fill, second_fill],
        tolerance=Decimal("0.001"),
    )

    assert first_fill.filled_quantity == Decimal("1.000")
    assert second_fill.filled_quantity == Decimal("1.000")
    assert rows[0].replayed_quantity == Decimal("2.000")


@pytest.mark.asyncio
async def test_repository_upsert_is_idempotent(tmp_path: Path) -> None:
    repository, session_factory = await _repository_and_session_factory(
        tmp_path,
        "idempotent.sqlite3",
    )
    synced_at = datetime(2024, 2, 1, tzinfo=UTC)
    position_row = position_live_from_dto(_position_payload(), synced_at=synced_at)
    order_row = order_history_from_dto(
        _order_payload(fill_id="fill-1", order_id="order-1", side="BUY")
    )
    transaction_row = transaction_from_dto(_transaction_payload())
    dividend_row = dividend_from_dto(_dividend_payload())
    sync_status_row = SyncStatus(
        endpoint=POSITIONS_ENDPOINT,
        last_attempt_at=synced_at,
        last_success_at=synced_at,
        last_status="success",
        item_count=1,
        last_error=None,
    )
    reconciliation_rows = build_reconciliations(
        synced_at=synced_at,
        positions=[position_row],
        orders=[order_row],
        tolerance=Decimal("0.001"),
    )

    async with session_factory() as session:
        async with session.begin():
            await repository.ingest_domain_snapshot(
                session,
                instrument_seeds=[_instrument_seed(synced_at)],
                positions=[position_row],
                transactions=[transaction_row],
                orders=[order_row],
                dividends=[dividend_row],
                sync_statuses=[sync_status_row],
                reconciliations=reconciliation_rows,
            )
        async with session.begin():
            await repository.ingest_domain_snapshot(
                session,
                instrument_seeds=[_instrument_seed(synced_at)],
                positions=[position_row],
                transactions=[transaction_row],
                orders=[order_row],
                dividends=[dividend_row],
                sync_statuses=[sync_status_row],
                reconciliations=reconciliation_rows,
            )

    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Instrument)) == 1
        assert await session.scalar(select(func.count()).select_from(PositionLive)) == 1
        assert await session.scalar(select(func.count()).select_from(Transaction)) == 1
        assert await session.scalar(select(func.count()).select_from(OrderHistory)) == 1
        assert await session.scalar(select(func.count()).select_from(Dividend)) == 1
        assert await session.scalar(select(func.count()).select_from(PositionReconciliation)) == 1


@pytest.mark.asyncio
async def test_repository_preserves_verified_mapping_on_not_required_upsert(tmp_path: Path) -> None:
    repository, session_factory = await _repository_and_session_factory(
        tmp_path,
        "preserve.sqlite3",
    )
    synced_at = datetime(2024, 2, 2, tzinfo=UTC)

    async with session_factory() as session:
        async with session.begin():
            session.add(
                Instrument(
                    t212_ticker="TSLA_US_EQ",
                    yahoo_ticker="TSLA",
                    mapping_status="resolved",
                    mapping_source="override",
                    mapping_details_json={"reason": "verified"},
                    mapped_at=datetime(2024, 1, 1, tzinfo=UTC),
                )
            )

    async with session_factory() as session:
        async with session.begin():
            await repository.ingest_domain_snapshot(
                session,
                instrument_seeds=[
                    InstrumentSeed(
                        t212_ticker="TSLA_US_EQ",
                        isin="US88160R1014",
                        name="Tesla",
                        short_name="Tesla",
                        currency_code="USD",
                        instrument_type="STOCK",
                        added_on=synced_at,
                        extended_hours=True,
                        max_open_quantity=Decimal("10.0000"),
                        working_schedule_id=7,
                        exchange_id=None,
                        mapping_result=InstrumentMappingResult(
                            status="not_required",
                            source=None,
                            yahoo_ticker=None,
                            details={"reason": "unobserved_metadata"},
                        ),
                        mapped_at=synced_at,
                        observed=False,
                    )
                ],
                positions=[],
                transactions=[],
                orders=[],
                dividends=[],
                sync_statuses=[],
                reconciliations=[],
            )

    async with session_factory() as session:
        instrument = await session.get(Instrument, "TSLA_US_EQ")

    assert instrument is not None
    assert instrument.mapping_status == "resolved"
    assert instrument.yahoo_ticker == "TSLA"
    assert instrument.mapping_details_json == {"reason": "verified"}


@pytest.mark.asyncio
async def test_sync_lease_conflict_and_stale_recovery(tmp_path: Path) -> None:
    repository, session_factory = await _repository_and_session_factory(
        tmp_path,
        "lease.sqlite3",
    )
    acquired_at = datetime(2024, 2, 3, tzinfo=UTC)

    first_lease = await repository.acquire_portfolio_sync_lease(
        acquired_at=acquired_at,
        lease_minutes=15,
    )

    with pytest.raises(SyncAlreadyRunningError):
        await repository.acquire_portfolio_sync_lease(
            acquired_at=acquired_at + timedelta(minutes=1),
            lease_minutes=15,
        )

    stale_lease = await repository.acquire_portfolio_sync_lease(
        acquired_at=acquired_at + timedelta(minutes=16),
        lease_minutes=15,
    )

    assert first_lease.endpoint == PORTFOLIO_SYNC_LEASE_ENDPOINT
    assert stale_lease.acquired_at == acquired_at + timedelta(minutes=16)

    async with session_factory() as session:
        lease_status = await session.get(SyncStatus, PORTFOLIO_SYNC_LEASE_ENDPOINT)

    assert lease_status is not None
    assert lease_status.last_status == "running"


@pytest.mark.asyncio
async def test_sync_lease_release_preserves_last_success_on_failure(tmp_path: Path) -> None:
    repository, session_factory = await _repository_and_session_factory(
        tmp_path,
        "lease_release.sqlite3",
    )
    success_at = datetime(2024, 2, 4, tzinfo=UTC)

    success_lease = await repository.acquire_portfolio_sync_lease(
        acquired_at=success_at,
        lease_minutes=15,
    )
    await repository.release_portfolio_sync_lease(
        lease=success_lease,
        completed_at=success_at,
        succeeded=True,
        error_message=None,
    )
    failed_lease = await repository.acquire_portfolio_sync_lease(
        acquired_at=success_at + timedelta(minutes=1),
        lease_minutes=15,
    )
    await repository.release_portfolio_sync_lease(
        lease=failed_lease,
        completed_at=success_at + timedelta(minutes=2),
        succeeded=False,
        error_message="RuntimeError",
    )

    async with session_factory() as session:
        lease_status = await session.get(SyncStatus, PORTFOLIO_SYNC_LEASE_ENDPOINT)

    assert lease_status is not None
    assert lease_status.last_status == "failed"
    assert lease_status.last_success_at == success_at
    assert lease_status.last_error == "RuntimeError"


@pytest.mark.asyncio
async def test_stale_lease_owner_cannot_release_new_owner(tmp_path: Path) -> None:
    repository, session_factory = await _repository_and_session_factory(
        tmp_path,
        "lease_owner.sqlite3",
    )
    started_at = datetime(2024, 2, 5, tzinfo=UTC)
    stale_lease = await repository.acquire_portfolio_sync_lease(
        acquired_at=started_at,
        lease_minutes=15,
    )
    current_lease = await repository.acquire_portfolio_sync_lease(
        acquired_at=started_at + timedelta(minutes=16),
        lease_minutes=15,
    )

    await repository.release_portfolio_sync_lease(
        lease=stale_lease,
        completed_at=started_at + timedelta(minutes=17),
        succeeded=True,
        error_message=None,
    )

    async with session_factory() as session:
        lease_status = await session.get(SyncStatus, PORTFOLIO_SYNC_LEASE_ENDPOINT)

    assert lease_status is not None
    assert lease_status.last_status == "running"
    assert lease_status.last_error == current_lease.token


def test_reconciliation_buy_sell_and_live_replay_edges() -> None:
    synced_at = datetime(2024, 3, 1, tzinfo=UTC)
    positions = [
        PositionLive(ts=synced_at, t212_ticker="AAPL_US_EQ", quantity=Decimal("1.000")),
        PositionLive(ts=synced_at, t212_ticker="LIVE_ONLY", quantity=Decimal("3.000")),
    ]
    orders = [
        OrderHistory(
            fill_id="1",
            t212_ticker="AAPL_US_EQ",
            fill_type="TRADE",
            side="BUY",
            filled_quantity=Decimal("2.000"),
        ),
        OrderHistory(
            fill_id="2",
            t212_ticker="AAPL_US_EQ",
            fill_type="TRADE",
            side="SELL",
            filled_quantity=Decimal("1.000"),
        ),
        OrderHistory(
            fill_id="3",
            t212_ticker="REPLAY_ONLY",
            fill_type="TRADE",
            side="BUY",
            filled_quantity=Decimal("4.000"),
        ),
    ]

    rows = build_reconciliations(
        synced_at=synced_at,
        positions=positions,
        orders=orders,
        tolerance=Decimal("0.001"),
    )

    assert [(row.t212_ticker, row.status, row.difference_quantity) for row in rows] == [
        ("AAPL_US_EQ", "MATCH", Decimal("0.000")),
        ("LIVE_ONLY", "MISMATCH", Decimal("3.000")),
        ("REPLAY_ONLY", "MISMATCH", Decimal("-4.000")),
    ]


def test_reconciliation_tolerance_boundary_and_unsupported_actions() -> None:
    synced_at = datetime(2024, 3, 2, tzinfo=UTC)
    rows = build_reconciliations(
        synced_at=synced_at,
        positions=[PositionLive(ts=synced_at, t212_ticker="TOL", quantity=Decimal("1.001"))],
        orders=[
            OrderHistory(
                fill_id="1",
                t212_ticker="TOL",
                fill_type="TRADE",
                side="BUY",
                filled_quantity=Decimal("1.000"),
            ),
            OrderHistory(
                fill_id="2",
                t212_ticker="CORP",
                fill_type="STOCK_SPLIT",
                side="BUY",
                filled_quantity=Decimal("1.000"),
            ),
        ],
        tolerance=Decimal("0.001"),
    )

    assert [(row.t212_ticker, row.status) for row in rows] == [
        ("CORP", "UNSUPPORTED_ACTION"),
        ("TOL", "MATCH"),
    ]


@pytest.mark.asyncio
async def test_sync_respects_metadata_ttl_and_force(tmp_path: Path) -> None:
    repository, session_factory = await _repository_and_session_factory(
        tmp_path,
        "ttl.sqlite3",
    )
    synced_at = datetime(2024, 4, 1, tzinfo=UTC)
    async with session_factory() as session:
        async with session.begin():
            session.add(
                SyncStatus(
                    endpoint=METADATA_ENDPOINT,
                    last_attempt_at=synced_at,
                    last_success_at=synced_at,
                    last_status="success",
                    item_count=1,
                    last_error=None,
                )
            )

    client = FakeTrading212Client(
        instruments=[_instrument_metadata()],
        positions=[_position_payload()],
        orders=[_order_payload(fill_id="fill-1", order_id="order-1", side="BUY")],
        dividends=[_dividend_payload()],
        transactions=[_transaction_payload()],
    )
    resolver = StubResolver()
    service = PortfolioSyncService(
        settings=Settings(data_dir=tmp_path),
        session_factory=session_factory,
        client=client,
        repository=repository,
        resolver=resolver,
        clock=FixedClock(synced_at),
    )

    summary = await service.sync()
    forced = await service.sync(force_metadata=True)

    assert summary.metadata_fetched is False
    assert forced.metadata_fetched is True
    assert client.calls.count(METADATA_ENDPOINT) == 1


@pytest.mark.asyncio
async def test_sync_resolves_only_observed_instruments_and_caches_full_metadata(
    tmp_path: Path,
) -> None:
    repository, session_factory = await _repository_and_session_factory(
        tmp_path,
        "metadata_scope.sqlite3",
    )
    synced_at = datetime(2024, 4, 2, tzinfo=UTC)
    metadata_items = [_instrument_metadata(ticker="TSLA_US_EQ")]
    metadata_items.extend(
        [_instrument_metadata(ticker=f"BULK_{index:04d}") for index in range(2000)]
    )
    client = FakeTrading212Client(
        instruments=metadata_items,
        positions=[_position_payload()],
        orders=[_order_payload(fill_id="fill-1", order_id="order-1", side="BUY")],
        dividends=[_dividend_payload()],
        transactions=[_transaction_payload()],
    )
    resolver = StubResolver(
        {
            "TSLA_US_EQ": InstrumentMappingResult(
                status="resolved",
                source="override",
                yahoo_ticker="TSLA",
                details={"reason": "verified"},
            )
        }
    )
    service = PortfolioSyncService(
        settings=Settings(data_dir=tmp_path),
        session_factory=session_factory,
        client=client,
        repository=repository,
        resolver=resolver,
        clock=FixedClock(synced_at),
    )

    await service.sync(force_metadata=True)

    assert [request.t212_ticker for request in resolver.requests] == ["TSLA_US_EQ"]
    async with session_factory() as session:
        total = await session.scalar(select(func.count()).select_from(Instrument))
        not_required = await session.scalar(
            select(func.count())
            .select_from(Instrument)
            .where(Instrument.mapping_status == "not_required")
        )
        resolved = await session.get(Instrument, "TSLA_US_EQ")

    assert total == 2001
    assert not_required == 2000
    assert resolved is not None
    assert resolved.mapping_status == "resolved"
    assert resolved.yahoo_ticker == "TSLA"


@pytest.mark.asyncio
async def test_sync_preserves_existing_resolved_mapping_when_instrument_is_unobserved(
    tmp_path: Path,
) -> None:
    repository, session_factory = await _repository_and_session_factory(
        tmp_path,
        "mapping_preserve.sqlite3",
    )
    synced_at = datetime(2024, 4, 3, tzinfo=UTC)
    async with session_factory() as session:
        async with session.begin():
            session.add(
                Instrument(
                    t212_ticker="TSLA_US_EQ",
                    yahoo_ticker="TSLA",
                    mapping_status="resolved",
                    mapping_source="override",
                    mapping_details_json={"reason": "verified"},
                    mapped_at=datetime(2024, 1, 1, tzinfo=UTC),
                )
            )

    service = PortfolioSyncService(
        settings=Settings(data_dir=tmp_path),
        session_factory=session_factory,
        client=FakeTrading212Client(
            instruments=[_instrument_metadata(ticker="TSLA_US_EQ")],
            positions=[],
            orders=[],
            dividends=[],
            transactions=[],
        ),
        repository=repository,
        resolver=StubResolver(),
        clock=FixedClock(synced_at),
    )

    await service.sync(force_metadata=True)

    async with session_factory() as session:
        instrument = await session.get(Instrument, "TSLA_US_EQ")

    assert instrument is not None
    assert instrument.mapping_status == "resolved"
    assert instrument.yahoo_ticker == "TSLA"


@pytest.mark.asyncio
async def test_sync_failure_records_status_without_partial_domain_writes(tmp_path: Path) -> None:
    repository, session_factory = await _repository_and_session_factory(
        tmp_path,
        "failure.sqlite3",
    )
    synced_at = datetime(2024, 5, 1, tzinfo=UTC)
    client = FakeTrading212Client(
        instruments=[_instrument_metadata()],
        failures={POSITIONS_ENDPOINT: RuntimeError("boom")},
    )
    service = PortfolioSyncService(
        settings=Settings(data_dir=tmp_path),
        session_factory=session_factory,
        client=client,
        repository=repository,
        resolver=StubResolver(),
        clock=FixedClock(synced_at),
    )

    with pytest.raises(RuntimeError):
        await service.sync(force_metadata=True)

    async with session_factory() as session:
        failed_status = await session.get(SyncStatus, POSITIONS_ENDPOINT)
        assert failed_status is not None
        assert failed_status.last_status == "failed"
        assert await session.scalar(select(func.count()).select_from(Instrument)) == 0
        assert await session.scalar(select(func.count()).select_from(PositionLive)) == 0
        assert await session.scalar(select(func.count()).select_from(Transaction)) == 0
        assert await session.scalar(select(func.count()).select_from(OrderHistory)) == 0
        assert await session.scalar(select(func.count()).select_from(Dividend)) == 0


@pytest.mark.asyncio
async def test_sync_rolls_back_when_required_ids_are_invalid(tmp_path: Path) -> None:
    repository, session_factory = await _repository_and_session_factory(
        tmp_path,
        "rollback.sqlite3",
    )
    synced_at = datetime(2024, 6, 1, tzinfo=UTC)
    client = FakeTrading212Client(
        instruments=[_instrument_metadata()],
        positions=[_position_payload()],
        orders=[_order_payload(fill_id=None, order_id="order-1", side="BUY")],
        dividends=[_dividend_payload()],
        transactions=[_transaction_payload()],
    )
    service = PortfolioSyncService(
        settings=Settings(data_dir=tmp_path),
        session_factory=session_factory,
        client=client,
        repository=repository,
        resolver=StubResolver(),
        clock=FixedClock(synced_at),
    )

    with pytest.raises(DomainTransformError):
        await service.sync(force_metadata=True)

    async with session_factory() as session:
        failed_status = await session.get(SyncStatus, ORDERS_ENDPOINT)
        assert await session.scalar(select(func.count()).select_from(Instrument)) == 0
        assert await session.scalar(select(func.count()).select_from(PositionLive)) == 0
        assert failed_status is not None
        assert failed_status.last_status == "failed"
        assert failed_status.last_error == "DomainTransformError"


@pytest.mark.asyncio
async def test_quality_report_statuses(tmp_path: Path) -> None:
    repository, session_factory = await _repository_and_session_factory(
        tmp_path,
        "report.sqlite3",
    )
    as_of = datetime(2024, 7, 1, tzinfo=UTC)
    async with session_factory() as session:
        async with session.begin():
            session.add_all(
                [
                    SyncStatus(
                        endpoint=METADATA_ENDPOINT,
                        last_attempt_at=as_of,
                        last_success_at=as_of,
                        last_status="success",
                        item_count=1,
                        last_error=None,
                    ),
                    SyncStatus(
                        endpoint=POSITIONS_ENDPOINT,
                        last_attempt_at=as_of,
                        last_success_at=as_of,
                        last_status="failed",
                        item_count=None,
                        last_error="RuntimeError",
                    ),
                    Instrument(
                        t212_ticker="MISS",
                        mapping_status="unresolved",
                        mapped_at=as_of,
                    ),
                    Instrument(
                        t212_ticker="AMB",
                        mapping_status="ambiguous",
                        mapped_at=as_of,
                    ),
                    Instrument(
                        t212_ticker="GBX",
                        mapping_status="override_required",
                        mapped_at=as_of,
                    ),
                    PositionReconciliation(
                        ts=as_of,
                        t212_ticker="BAD",
                        replayed_quantity=Decimal("1"),
                        live_quantity=Decimal("2"),
                        difference_quantity=Decimal("1"),
                        tolerance_quantity=Decimal("0.001"),
                        status="MISMATCH",
                    ),
                    PositionReconciliation(
                        ts=as_of,
                        t212_ticker="CORP",
                        replayed_quantity=Decimal("0"),
                        live_quantity=Decimal("0"),
                        difference_quantity=Decimal("0"),
                        tolerance_quantity=Decimal("0.001"),
                        status="UNSUPPORTED_ACTION",
                    ),
                ]
            )

    report_service = PortfolioQualityReportService(
        repository,
        Settings(data_dir=tmp_path),
        clock=FixedClock(as_of),
    )
    report = await report_service.get_report()

    assert report.overall_status == "ERROR"
    assert [item.t212_ticker for item in report.unresolved_instruments] == ["MISS"]
    assert [item.t212_ticker for item in report.ambiguous_instruments] == ["AMB"]
    assert [item.t212_ticker for item in report.override_required_instruments] == ["GBX"]
    assert [item.t212_ticker for item in report.reconciliation_mismatches] == ["BAD"]
    assert [item.t212_ticker for item in report.unsupported_actions] == ["CORP"]


@pytest.mark.asyncio
async def test_quality_report_warns_on_stale_metadata(tmp_path: Path) -> None:
    repository, session_factory = await _repository_and_session_factory(
        tmp_path,
        "stale_report.sqlite3",
    )
    last_attempt = datetime(2024, 7, 1, tzinfo=UTC)
    report_now = last_attempt + timedelta(hours=25)
    async with session_factory() as session:
        async with session.begin():
            session.add(
                SyncStatus(
                    endpoint=METADATA_ENDPOINT,
                    last_attempt_at=last_attempt,
                    last_success_at=last_attempt,
                    last_status="success",
                    item_count=1,
                    last_error=None,
                )
            )

    report = await PortfolioQualityReportService(
        repository,
        Settings(data_dir=tmp_path),
        clock=FixedClock(report_now),
    ).get_report()

    assert report.as_of == last_attempt
    assert report.metadata_freshness.checked_at == report_now
    assert report.metadata_freshness.fresh is False
    assert report.overall_status == "WARNING"


async def _repository_and_session_factory(
    tmp_path: Path,
    sqlite_filename: str,
) -> tuple[PortfolioRepository, async_sessionmaker[AsyncSession]]:
    settings = Settings(data_dir=tmp_path, sqlite_filename=sqlite_filename)
    await migrate_database(settings)
    engine = create_async_engine(settings.sqlite_url, poolclass=NullPool)
    return (
        PortfolioRepository(async_sessionmaker(engine, expire_on_commit=False)),
        async_sessionmaker(engine, expire_on_commit=False),
    )


def _instrument_seed(mapped_at: datetime) -> InstrumentSeed:
    return InstrumentSeed(
        t212_ticker="TSLA_US_EQ",
        isin="US88160R1014",
        name="Tesla",
        short_name="Tesla",
        currency_code="USD",
        instrument_type="STOCK",
        added_on=datetime(2024, 1, 1, tzinfo=UTC),
        extended_hours=True,
        max_open_quantity=Decimal("10.0000"),
        working_schedule_id=7,
        exchange_id=None,
        mapping_result=InstrumentMappingResult(
            status="resolved",
            source="override",
            yahoo_ticker="TSLA",
            details={"reason": "test"},
        ),
        mapped_at=mapped_at,
        observed=True,
    )


def _instrument_metadata(ticker: str = "TSLA_US_EQ") -> InstrumentMetadata:
    return InstrumentMetadata.model_validate(
        {
            "ticker": ticker,
            "isin": "US88160R1014",
            "name": "Tesla",
            "shortName": "Tesla",
            "currencyCode": "USD",
            "type": "STOCK",
            "addedOn": "2024-01-01T00:00:00Z",
            "extendedHours": True,
            "maxOpenQuantity": "10.0000",
            "workingScheduleId": 7,
        }
    )


def _position_payload() -> Position:
    return Position.model_validate(
        {
            "instrument": {
                "ticker": "TSLA_US_EQ",
                "isin": "US88160R1014",
                "name": "Tesla",
                "currency": "USD",
            },
            "quantity": "1.000",
            "quantityAvailableForTrading": "1.000",
            "quantityInPies": "0.100",
            "averagePricePaid": "200.0000",
            "currentPrice": "210.0000",
            "createdAt": "2024-01-03T00:00:00Z",
            "walletImpact": {
                "currency": "EUR",
                "currentValue": "210.0000",
                "fxImpact": "1.5000",
                "totalCost": "200.0000",
                "unrealizedProfitLoss": "10.0000",
            },
        }
    )


def _order_payload(
    *,
    fill_id: int | str | None,
    order_id: int | str | None,
    side: str,
    fill_quantity: str = "1.000",
    order_filled_quantity: str = "1.000",
) -> HistoricalOrderItem:
    return HistoricalOrderItem.model_validate(
        {
            "fill": {
                "id": fill_id,
                "filledAt": "2024-01-04T00:00:00Z",
                "price": "205.0000",
                "quantity": fill_quantity,
                "type": "TRADE",
                "walletImpact": {
                    "currency": "USD",
                    "netValue": "205.0000",
                    "fxRate": "0.9100",
                    "realisedProfitLoss": "5.0000",
                    "taxes": [
                        {
                            "name": "STAMP_DUTY",
                            "currency": "USD",
                            "quantity": "0.50",
                        }
                    ],
                },
            },
            "order": {
                "id": order_id,
                "filledValue": "205.0000",
                "filledQuantity": order_filled_quantity,
                "quantity": "1.000",
                "instrument": {
                    "ticker": "TSLA_US_EQ",
                    "isin": "US88160R1014",
                    "name": "Tesla",
                    "currency": "USD",
                },
                "side": side,
                "type": "MARKET",
                "limitPrice": "0",
                "stopPrice": "0",
                "currency": "USD",
            },
        }
    )


def _dividend_payload() -> DividendItem:
    return DividendItem.model_validate(
        {
            "reference": "div-1",
            "paidOn": "2024-01-08T00:00:00Z",
            "instrument": {
                "ticker": "TSLA_US_EQ",
                "isin": "US88160R1014",
                "name": "Tesla",
                "currency": "USD",
            },
            "type": "DIVIDEND",
            "currency": "USD",
            "tickerCurrency": "USD",
            "quantity": "1.000",
            "amount": "2.5000",
            "amountInEuro": "2.3000",
            "grossAmountPerShare": "0.1234",
        }
    )


def _transaction_payload() -> TransactionItem:
    return TransactionItem.model_validate(
        {
            "reference": "txn-1",
            "dateTime": "2024-01-09T10:11:12Z",
            "currency": "EUR",
            "amount": "100.00",
            "type": "DEPOSIT",
        }
    )
