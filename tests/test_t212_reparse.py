from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
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
    Transaction,
)
from helios.portfolio_repository import (
    METADATA_ENDPOINT,
    PortfolioRepository,
    SyncAlreadyRunningError,
)
from helios.portfolio_sync import (
    DIVIDENDS_ENDPOINT,
    ORDERS_ENDPOINT,
    POSITIONS_ENDPOINT,
    TRANSACTIONS_ENDPOINT,
)
from helios.rate_limit import Clock
from helios.raw_snapshots import JsonValue, RawSnapshotRepository
from helios.resolver import OpenFigiResolver
from helios.schemas import DividendItem, HistoricalOrderItem, InstrumentMetadata, TransactionItem
from helios.t212_reparse import T212ReparseService

NOW = datetime(2024, 5, 1, 12, 0, tzinfo=UTC)


class FixedClock(Clock):
    def now(self) -> float:
        return NOW.timestamp()

    def utcnow(self) -> datetime:
        return NOW

    async def sleep(self, seconds: float) -> None:
        del seconds


async def _repositories(
    tmp_path: Path, filename: str
) -> tuple[PortfolioRepository, RawSnapshotRepository, async_sessionmaker[AsyncSession]]:
    settings = Settings(data_dir=tmp_path, sqlite_filename=filename)
    await migrate_database(settings)
    engine = create_async_engine(settings.sqlite_url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return (
        PortfolioRepository(session_factory),
        RawSnapshotRepository(session_factory),
        session_factory,
    )


def _service(
    repository: PortfolioRepository,
    snapshot_repository: RawSnapshotRepository,
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> T212ReparseService:
    return T212ReparseService(
        repository,
        snapshot_repository,
        Settings(data_dir=tmp_path),
        session_factory,
        clock=FixedClock(),
    )


def _order_item_dump(
    *,
    fill_id: str,
    order_id: str,
    side: str,
    ticker: str = "TSLA_US_EQ",
    fill_quantity: str = "1.000",
    order_filled_quantity: str = "1.000",
) -> dict[str, JsonValue]:
    item = HistoricalOrderItem.model_validate(
        {
            "fill": {
                "id": fill_id,
                "filledAt": "2024-01-04T00:00:00Z",
                "price": "205.0000",
                "quantity": fill_quantity,
                "type": "TRADE",
            },
            "order": {
                "id": order_id,
                "filledQuantity": order_filled_quantity,
                "quantity": "1.000",
                "instrument": {
                    "ticker": ticker,
                    "isin": "US88160R1014",
                    "name": "Tesla",
                    "currency": "USD",
                },
                "side": side,
                "type": "MARKET",
                "currency": "USD",
            },
        }
    )
    return item.model_dump(mode="json", by_alias=True)


def _dividend_item_dump(
    *, reference: str = "div-1", ticker: str = "TSLA_US_EQ"
) -> dict[str, JsonValue]:
    item = DividendItem.model_validate(
        {
            "reference": reference,
            "paidOn": "2024-01-08T00:00:00Z",
            "instrument": {
                "ticker": ticker,
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
    return item.model_dump(mode="json", by_alias=True)


def _transaction_item_dump(*, reference: str = "txn-1") -> dict[str, JsonValue]:
    item = TransactionItem.model_validate(
        {
            "reference": reference,
            "dateTime": "2024-01-09T10:11:12Z",
            "currency": "EUR",
            "amount": "100.00",
            "type": "DEPOSIT",
        }
    )
    return item.model_dump(mode="json", by_alias=True)


def _instrument_metadata_dump(*, ticker: str = "TSLA_US_EQ") -> dict[str, JsonValue]:
    item = InstrumentMetadata.model_validate(
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
    return item.model_dump(mode="json", by_alias=True)


def _position_item_dump(*, ticker: str = "TSLA_US_EQ") -> dict[str, JsonValue]:
    return {
        "instrument": {
            "ticker": ticker,
            "isin": "US88160R1014",
            "name": "Tesla",
            "currency": "USD",
        },
        "quantity": "1.000",
    }


def _history_page(items: list[dict[str, JsonValue]]) -> dict[str, JsonValue]:
    return {"items": list(items), "nextPagePath": None}


async def _seed_history(
    snapshot_repository: RawSnapshotRepository,
    *,
    endpoint: str,
    items: list[dict[str, JsonValue]],
    http_status: int = 200,
    ts: datetime = NOW,
) -> None:
    await snapshot_repository.append_snapshot(
        endpoint=endpoint,
        recorded_at=ts,
        http_status=http_status,
        content_type="application/json",
        payload=_history_page(items),
    )


async def test_reparse_reports_when_there_are_no_snapshots(tmp_path: Path) -> None:
    repository, snapshot_repository, session_factory = await _repositories(
        tmp_path, "empty.sqlite3"
    )
    service = _service(repository, snapshot_repository, session_factory, tmp_path)

    summary = await service.reparse()

    assert summary.snapshots_read == 0
    assert summary.rows_written == 0
    assert any("run `helios sync`" in note for note in summary.notes)


async def test_reparse_rebuilds_ledger_from_snapshots(tmp_path: Path) -> None:
    repository, snapshot_repository, session_factory = await _repositories(
        tmp_path, "rebuild.sqlite3"
    )
    await _seed_history(
        snapshot_repository,
        endpoint=ORDERS_ENDPOINT,
        items=[_order_item_dump(fill_id="fill-1", order_id="order-1", side="BUY")],
    )
    await _seed_history(
        snapshot_repository, endpoint=DIVIDENDS_ENDPOINT, items=[_dividend_item_dump()]
    )
    await _seed_history(
        snapshot_repository, endpoint=TRANSACTIONS_ENDPOINT, items=[_transaction_item_dump()]
    )
    await snapshot_repository.append_snapshot(
        endpoint=METADATA_ENDPOINT,
        recorded_at=NOW,
        http_status=200,
        content_type="application/json",
        payload=[_instrument_metadata_dump()],
    )
    service = _service(repository, snapshot_repository, session_factory, tmp_path)

    summary = await service.reparse()

    assert summary.snapshots_read == 4
    assert summary.items_parsed == 4
    assert summary.rows_written == 4
    assert summary.duplicates_unchanged == 0
    endpoints = {entry.endpoint: entry for entry in summary.endpoints}
    assert endpoints[ORDERS_ENDPOINT].replayed == 1
    assert endpoints[DIVIDENDS_ENDPOINT].replayed == 1
    assert endpoints[TRANSACTIONS_ENDPOINT].replayed == 1
    assert endpoints[METADATA_ENDPOINT].replayed == 1

    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(OrderHistory)) == 1
        assert await session.scalar(select(func.count()).select_from(Dividend)) == 1
        assert await session.scalar(select(func.count()).select_from(Transaction)) == 1
        instrument = await session.get(Instrument, "TSLA_US_EQ")
    assert instrument is not None
    assert instrument.mapping_status == "not_required"
    assert instrument.name == "Tesla"


async def test_reparse_is_idempotent(tmp_path: Path) -> None:
    repository, snapshot_repository, session_factory = await _repositories(
        tmp_path, "idempotent.sqlite3"
    )
    await _seed_history(
        snapshot_repository,
        endpoint=ORDERS_ENDPOINT,
        items=[_order_item_dump(fill_id="fill-1", order_id="order-1", side="BUY")],
    )
    await _seed_history(
        snapshot_repository, endpoint=DIVIDENDS_ENDPOINT, items=[_dividend_item_dump()]
    )
    service = _service(repository, snapshot_repository, session_factory, tmp_path)

    first = await service.reparse()
    second = await service.reparse()

    assert first.rows_written == 2
    assert second.rows_written == 0
    assert second.duplicates_unchanged == 2

    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(OrderHistory)) == 1
        assert await session.scalar(select(func.count()).select_from(Dividend)) == 1


async def test_reparse_recovers_a_fill_an_older_parser_dropped(tmp_path: Path) -> None:
    """Simulates a parser fix: the ledger already has fill-1; the raw page also carries fill-2,
    which an older (buggy) parser never wrote. Replaying with the current parser recovers it."""
    repository, snapshot_repository, session_factory = await _repositories(
        tmp_path, "recover.sqlite3"
    )
    async with session_factory() as session, session.begin():
        session.add(OrderHistory(fill_id="fill-1", t212_ticker="TSLA_US_EQ"))
    await _seed_history(
        snapshot_repository,
        endpoint=ORDERS_ENDPOINT,
        items=[
            _order_item_dump(fill_id="fill-1", order_id="order-1", side="BUY"),
            _order_item_dump(fill_id="fill-2", order_id="order-1", side="BUY"),
        ],
    )
    service = _service(repository, snapshot_repository, session_factory, tmp_path)

    summary = await service.reparse()

    assert summary.items_parsed == 2
    assert summary.rows_written == 1
    assert summary.duplicates_unchanged == 1
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(OrderHistory)) == 2
        recovered = await session.get(OrderHistory, "fill-2")
    assert recovered is not None
    assert recovered.fill_price == Decimal("205.0000")


async def test_reparse_skips_non_2xx_snapshots(tmp_path: Path) -> None:
    repository, snapshot_repository, session_factory = await _repositories(
        tmp_path, "non2xx.sqlite3"
    )
    await snapshot_repository.append_snapshot(
        endpoint=ORDERS_ENDPOINT,
        recorded_at=NOW,
        http_status=500,
        content_type="application/json",
        payload=_history_page([]),
    )
    service = _service(repository, snapshot_repository, session_factory, tmp_path)

    summary = await service.reparse()

    assert summary.snapshots_read == 1
    assert summary.rows_written == 0
    entry = summary.endpoints[0]
    assert entry.endpoint == ORDERS_ENDPOINT
    assert entry.replayed == 0
    assert any("non-2xx" in reason for reason in entry.skipped)


async def test_reparse_skips_positions_as_live_state(tmp_path: Path) -> None:
    repository, snapshot_repository, session_factory = await _repositories(
        tmp_path, "positions.sqlite3"
    )
    await snapshot_repository.append_snapshot(
        endpoint=POSITIONS_ENDPOINT,
        recorded_at=NOW,
        http_status=200,
        content_type="application/json",
        payload=[_position_item_dump()],
    )
    service = _service(repository, snapshot_repository, session_factory, tmp_path)

    summary = await service.reparse()

    entry = summary.endpoints[0]
    assert entry.endpoint == POSITIONS_ENDPOINT
    assert entry.replayed == 0
    assert any("live-state" in reason for reason in entry.skipped)
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(PositionLive)) == 0


async def test_reparse_skips_unrecognised_endpoints(tmp_path: Path) -> None:
    repository, snapshot_repository, session_factory = await _repositories(
        tmp_path, "unknown.sqlite3"
    )
    await snapshot_repository.append_snapshot(
        endpoint="/equity/account/summary",
        recorded_at=NOW,
        http_status=200,
        content_type="application/json",
        payload={"cash": "100.00"},
    )
    service = _service(repository, snapshot_repository, session_factory, tmp_path)

    summary = await service.reparse()

    entry = summary.endpoints[0]
    assert entry.endpoint == "/equity/account/summary"
    assert any("unsupported or unrecognised" in reason for reason in entry.skipped)


async def test_reparse_counts_unparseable_payload_as_failure_without_aborting(
    tmp_path: Path,
) -> None:
    repository, snapshot_repository, session_factory = await _repositories(
        tmp_path, "badpayload.sqlite3"
    )
    await snapshot_repository.append_snapshot(
        endpoint=ORDERS_ENDPOINT,
        recorded_at=NOW,
        http_status=200,
        content_type="application/json",
        payload={"not": "a history page"},
    )
    await _seed_history(
        snapshot_repository, endpoint=DIVIDENDS_ENDPOINT, items=[_dividend_item_dump()]
    )
    service = _service(repository, snapshot_repository, session_factory, tmp_path)

    summary = await service.reparse()

    assert len(summary.failures) == 1
    assert ORDERS_ENDPOINT in summary.failures[0]
    endpoints = {entry.endpoint: entry for entry in summary.endpoints}
    assert endpoints[ORDERS_ENDPOINT].failed == 1
    assert endpoints[ORDERS_ENDPOINT].replayed == 0
    assert endpoints[DIVIDENDS_ENDPOINT].replayed == 1
    # The bad orders row did not abort the run: the dividend alongside it was still written.
    assert summary.rows_written == 1

    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Dividend)) == 1
        assert await session.scalar(select(func.count()).select_from(OrderHistory)) == 0


async def test_reparse_refuses_when_a_sync_lease_is_held(tmp_path: Path) -> None:
    repository, snapshot_repository, session_factory = await _repositories(
        tmp_path, "lease.sqlite3"
    )
    await _seed_history(
        snapshot_repository,
        endpoint=ORDERS_ENDPOINT,
        items=[_order_item_dump(fill_id="fill-1", order_id="order-1", side="BUY")],
    )
    await repository.acquire_portfolio_sync_lease(acquired_at=NOW, lease_minutes=15)
    service = _service(repository, snapshot_repository, session_factory, tmp_path)

    with pytest.raises(SyncAlreadyRunningError):
        await service.reparse()

    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(OrderHistory)) == 0


async def test_reparse_makes_no_network_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("t212-reparse must never touch the network")

    monkeypatch.setattr(httpx.AsyncClient, "request", _boom)
    monkeypatch.setattr(OpenFigiResolver, "resolve", _boom)

    repository, snapshot_repository, session_factory = await _repositories(
        tmp_path, "no_network.sqlite3"
    )
    await _seed_history(
        snapshot_repository,
        endpoint=ORDERS_ENDPOINT,
        items=[_order_item_dump(fill_id="fill-1", order_id="order-1", side="BUY")],
    )
    await snapshot_repository.append_snapshot(
        endpoint=METADATA_ENDPOINT,
        recorded_at=NOW,
        http_status=200,
        content_type="application/json",
        payload=[_instrument_metadata_dump()],
    )
    service = _service(repository, snapshot_repository, session_factory, tmp_path)

    summary = await service.reparse()

    assert summary.rows_written == 2


async def test_reparse_recomputes_reconciliation_against_stored_positions(tmp_path: Path) -> None:
    repository, snapshot_repository, session_factory = await _repositories(
        tmp_path, "reconcile.sqlite3"
    )
    position_ts = datetime(2024, 6, 1, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(
            PositionLive(ts=position_ts, t212_ticker="TSLA_US_EQ", quantity=Decimal("1.000"))
        )
    await _seed_history(
        snapshot_repository,
        endpoint=ORDERS_ENDPOINT,
        items=[
            _order_item_dump(
                fill_id="fill-1",
                order_id="order-1",
                side="BUY",
                fill_quantity="1.000",
                order_filled_quantity="1.000",
            )
        ],
    )
    service = _service(repository, snapshot_repository, session_factory, tmp_path)

    summary = await service.reparse()

    assert summary.reconciliation_rows_written == 1
    async with session_factory() as session:
        row = await session.get(PositionReconciliation, (position_ts, "TSLA_US_EQ"))
    assert row is not None
    assert row.status == "MATCH"


async def test_reparse_skips_reconciliation_without_stored_positions(tmp_path: Path) -> None:
    repository, snapshot_repository, session_factory = await _repositories(
        tmp_path, "no_positions.sqlite3"
    )
    await _seed_history(
        snapshot_repository,
        endpoint=ORDERS_ENDPOINT,
        items=[_order_item_dump(fill_id="fill-1", order_id="order-1", side="BUY")],
    )
    service = _service(repository, snapshot_repository, session_factory, tmp_path)

    summary = await service.reparse()

    assert summary.reconciliation_rows_written == 0
    assert any("No stored positions snapshot" in note for note in summary.notes)
