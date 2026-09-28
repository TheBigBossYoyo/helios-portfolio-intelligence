"""Card history from Trading 212's CSV export: parsing, the export pipeline, and the replay.

The CSV fixture below uses the exact column layout of a real export (checked 2026-09-27); the
IDs of card, cashback and deposit rows are the transactions API's own references.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from helios.card_history import (
    CardHistoryService,
    ExportFormatError,
    card_label,
    parse_export_csv,
    summarise_card_history,
)
from helios.client import (
    EXPORTS_PATH,
    Trading212Client,
    Trading212MethodNotAllowedError,
)
from helios.config import Settings
from helios.db import migrate_database
from helios.models import Transaction
from helios.performance import (
    FxRatePoint,
    NullMarketDataProvider,
    PerformanceReplayService,
    _currency_conversion_legs,
)
from helios.portfolio_repository import PortfolioRepository
from helios.rate_limit import Clock
from helios.raw_snapshots import JsonValue, SnapshotWriter
from helios.schemas import ExportReport

HEADER = (
    "Action,Time (UTC),ISIN,Ticker,Name,Notes,ID,No. of shares,Price / share,"
    "Currency (Price / share),Exchange rate,Result,Currency (Result),Total,Currency (Total),"
    "Withholding tax,Currency (Withholding tax),Charge amount,Currency (Charge amount),"
    "Deposit fee,Currency (Deposit fee),Currency conversion from amount,"
    "Currency (Currency conversion from amount),Currency conversion to amount,"
    "Currency (Currency conversion to amount),Currency conversion fee,"
    "Currency (Currency conversion fee),Merchant name,Merchant category"
)


def _row(action: str, time: str, row_id: str, total: str, **extra: str) -> str:
    cells = {
        "Action": action,
        "Time (UTC)": time,
        "ID": row_id,
        "Total": total,
        "Currency (Total)": "EUR",
        **extra,
    }
    return ",".join(cells.get(name, "") for name in HEADER.split(","))


CSV = "\n".join(
    [
        HEADER,
        _row("Deposit", "2024-01-01 09:00:00+00:00", "dep-1", "1000.00", Notes="Transaction ID: X"),
        _row(
            "Card debit",
            "2024-01-02 10:00:00+00:00",
            "card-1",
            "-40.00",
            **{"Merchant name": "Apple", "Merchant category": "MISCELLANEOUS"},
        ),
        _row(
            "Card debit",
            "2024-02-03 10:00:00+00:00",
            "card-2",
            "-60.00",
            **{"Merchant name": "Amazon", "Merchant category": "OTHER"},
        ),
        _row("Spending cashback", "2024-02-04 01:00:00+00:00", "cb-1", "0.50"),
        _row(
            "Market buy",
            "2024-01-01 10:00:00+00:00",
            "EOF1",
            "-500.00",
            Ticker="AAPL",
            Name="Apple",
        ),
        _row("Dividend (Dividend)", "2024-01-05 10:00:00+00:00", "", "1.00"),
    ]
)


def test_parse_keeps_cash_rows_and_skips_trades_and_dividends() -> None:
    rows = parse_export_csv(CSV)

    assert [row.row_id for row in rows] == ["dep-1", "card-1", "card-2", "cb-1"]
    card = rows[1]
    assert card.action == "Card debit"
    assert card.total == Decimal("-40.00")
    assert card.merchant_name == "Apple"
    assert card.merchant_category == "MISCELLANEOUS"
    assert card.ts == datetime(2024, 1, 2, 10, tzinfo=UTC)


def test_parse_rejects_a_file_that_is_not_an_export() -> None:
    with pytest.raises(ExportFormatError):
        parse_export_csv("a,b\n1,2\n")


@pytest.mark.parametrize(
    ("action", "label"),
    [
        ("Card debit", "card"),
        ("Card credit", "card"),
        ("Spending cashback", "cashback"),
        ("Deposit", None),
        ("Interest on cash", None),
    ],
)
def test_card_label(action: str, label: str | None) -> None:
    assert card_label(action) == label


def test_summary_by_month_category_and_merchant() -> None:
    summary = summarise_card_history(parse_export_csv(CSV))

    assert summary.spent == Decimal("100.00")
    assert summary.cashback == Decimal("0.50")
    assert summary.cashback_rate == pytest.approx(0.005)
    assert [(month.label, month.spent, month.cashback) for month in summary.months] == [
        ("Jan 2024", Decimal("40.00"), Decimal("0")),
        ("Feb 2024", Decimal("60.00"), Decimal("0.50")),
    ]
    assert [group.key for group in summary.merchants] == ["Amazon", "Apple"]
    assert summary.categories[0].key == "OTHER"
    # Newest first, every payment and every cashback entry.
    assert [item.row_id for item in summary.transactions] == ["card-2", "card-1"]
    assert [entry.amount for entry in summary.cashback_entries] == [Decimal("0.50")]


# ---------------------------------------------------------------------------
# The export pipeline
# ---------------------------------------------------------------------------


@dataclass
class FakeExportClient:
    status: str = "Queued"
    requested: list[tuple[datetime, datetime]] = field(default_factory=list)
    downloads: int = 0

    async def request_export(self, *, time_from: datetime, time_to: datetime) -> int:
        self.requested.append((time_from, time_to))
        return 42

    async def list_exports(self) -> list[ExportReport]:
        link = "https://files.example.com/r.csv?sig=1" if self.status == "Finished" else None
        return [ExportReport(report_id=42, status=self.status, download_link=link)]

    async def download_export(self, url: str) -> str:
        self.downloads += 1
        return CSV


class StepClock(Clock):
    def __init__(self) -> None:
        self.current = datetime(2024, 3, 1, 12, tzinfo=UTC)

    def now(self) -> float:
        return self.current.timestamp()

    def utcnow(self) -> datetime:
        return self.current

    async def sleep(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)


async def _repository(tmp_path: Path) -> tuple[PortfolioRepository, Settings]:
    settings = Settings(
        data_dir=tmp_path,
        sqlite_filename="card.sqlite3",
        t212_api_key="k",
        t212_api_secret="s",
        card_history_enabled=True,
    )
    await migrate_database(settings)
    engine = create_async_engine(settings.sqlite_url, poolclass=NullPool)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)
    return PortfolioRepository(factory), settings


@pytest.mark.asyncio
async def test_pipeline_requests_waits_downloads_then_rests(tmp_path: Path) -> None:
    repository, settings = await _repository(tmp_path)
    client = FakeExportClient()
    clock = StepClock()
    service = CardHistoryService(repository, client, settings, clock)

    first = await service.refresh()
    second = await service.refresh()
    client.status = "Finished"
    third = await service.refresh()
    fourth = await service.refresh()

    assert [first.action, second.action, third.action, fourth.action] == [
        "requested",
        "waiting",
        "downloaded",
        "up_to_date",
    ]
    assert third.rows_stored == 4
    assert len(client.requested) == 1
    # With no ledger yet, the first export reaches back a year.
    assert client.requested[0][0] < clock.current - timedelta(days=364)
    status = await service.status()
    assert (status.card_rows, status.cash_rows, status.pending) == (2, 4, False)
    stored = await repository.latest_downloaded_export()
    assert stored is not None and stored.body == CSV

    # A day later a new export is due, overlapping the previous one by a week.
    clock.current += timedelta(hours=settings.card_export_cadence_hours + 1)
    client.status = "Queued"
    assert (await service.refresh()).action == "requested"
    assert client.requested[1][0] == stored.time_to - timedelta(days=7)


@pytest.mark.asyncio
async def test_a_failed_report_is_not_retried_until_the_cadence_allows(tmp_path: Path) -> None:
    repository, settings = await _repository(tmp_path)
    client = FakeExportClient(status="Failed")
    service = CardHistoryService(repository, client, settings, StepClock())

    assert (await service.refresh()).action == "requested"
    assert (await service.refresh()).action == "failed"
    assert (await service.refresh()).action == "up_to_date"
    assert len(client.requested) == 1


@pytest.mark.asyncio
async def test_disabled_without_credentials_or_when_switched_off(tmp_path: Path) -> None:
    repository, _ = await _repository(tmp_path)
    off = Settings(data_dir=tmp_path, t212_api_key="k", t212_api_secret="s")
    service = CardHistoryService(repository, FakeExportClient(), off, StepClock())

    assert (await service.refresh()).action == "disabled"


# ---------------------------------------------------------------------------
# The replay: card spending, cashback and currency conversions
# ---------------------------------------------------------------------------


class NoFx:
    async def fetch_eur_base_rates(
        self, *, currencies: set[str], start_date: date, end_date: date
    ) -> dict[str, list[FxRatePoint]]:
        return {}


def _transaction(
    reference: str, ts: datetime, kind: str, amount: str, currency: str
) -> Transaction:
    return Transaction(
        reference=reference,
        ts=ts,
        transaction_type=kind,
        currency_code=currency,
        amount=Decimal(amount),
    )


@pytest.mark.asyncio
async def test_replay_labels_card_spending_cashback_and_conversions(tmp_path: Path) -> None:
    repository, settings = await _repository(tmp_path)
    day = datetime(2024, 1, 2, 10, tzinfo=UTC)
    async with repository._session_factory() as session, session.begin():
        session.add_all(
            [
                _transaction(
                    "dep-1", datetime(2024, 1, 1, 9, tzinfo=UTC), "DEPOSIT", "1000", "EUR"
                ),
                # Same day: a card payment and a (separate) deposit. Gross figures keep both.
                _transaction("card-1", day, "WITHDRAW", "-40", "EUR"),
                _transaction("dep-2", day + timedelta(hours=1), "DEPOSIT", "100", "EUR"),
                _transaction("cb-1", datetime(2024, 1, 4, 1, tzinfo=UTC), "DEPOSIT", "0.50", "EUR"),
            ]
        )
    service = CardHistoryService(
        repository, FakeExportClient(status="Finished"), settings, StepClock()
    )
    await service.refresh()
    await service.refresh()

    replay = PerformanceReplayService(repository, settings, NullMarketDataProvider(), NoFx())
    await replay.replay(as_of=datetime(2024, 1, 5, tzinfo=UTC).date())
    nav = {row.as_of_date.day: row for row in await repository.list_daily_nav()}

    # Card payment: still money out, now labelled; the same-day deposit is not netted away.
    assert nav[2].card_spending_eur == Decimal("-40")
    assert (nav[2].deposit_eur, nav[2].withdrawal_eur) == (Decimal("100"), Decimal("-40"))
    # Cashback: income, not money added.
    assert nav[4].cashback_eur == Decimal("0.50")
    assert nav[4].external_flow_eur == Decimal("0")


def test_conversion_legs_need_opposite_directions_in_different_currencies_at_one_instant() -> None:
    instant = datetime(2024, 1, 3, 9, tzinfo=UTC)
    transactions = [
        # GBP cash converted to EUR: a GBP withdrawal and an EUR deposit, same instant (+ fee).
        _transaction("out", instant, "WITHDRAW", "-469", "GBP"),
        _transaction("in", instant, "DEPOSIT", "538.21", "EUR"),
        _transaction("fee", instant, "FEE", "-0.81", "EUR"),
        # Same instant, same currency: a real deposit and a real card payment, not a conversion.
        _transaction("dep", instant + timedelta(hours=1), "DEPOSIT", "100", "EUR"),
        _transaction("card", instant + timedelta(hours=1), "WITHDRAW", "-5", "EUR"),
        # A lone foreign-currency deposit is money added.
        _transaction("gbp-dep", instant + timedelta(hours=2), "DEPOSIT", "50", "GBP"),
    ]

    assert _currency_conversion_legs(transactions) == {"out", "in"}


# ---------------------------------------------------------------------------
# The client: the one POST, and a download that never carries the API key
# ---------------------------------------------------------------------------


class MemorySnapshots(SnapshotWriter):
    def __init__(self) -> None:
        self.payloads: list[tuple[str, JsonValue]] = []

    async def append_snapshot(
        self,
        *,
        endpoint: str,
        recorded_at: datetime,
        http_status: int,
        content_type: str | None,
        payload: JsonValue,
    ) -> None:
        self.payloads.append((endpoint, payload))


def _client(handler: object, snapshots: SnapshotWriter) -> Trading212Client:
    settings = Settings(t212_api_key="key", t212_api_secret="secret", data_dir=Path("data"))
    return Trading212Client(
        settings=settings,
        snapshot_writer=snapshots,
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
            base_url="https://demo.trading212.com/api/v0",
        ),
    )


@pytest.mark.asyncio
async def test_request_export_posts_the_fixed_body_and_returns_the_report_id() -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"reportId": 7})

    client = _client(handler, MemorySnapshots())
    report_id = await client.request_export(
        time_from=datetime(2024, 1, 1, tzinfo=UTC), time_to=datetime(2024, 2, 1, tzinfo=UTC)
    )
    await client.aclose()

    assert report_id == 7
    [request] = seen
    assert request.method == "POST"
    assert request.url.path.endswith(EXPORTS_PATH)
    assert json.loads(request.content) == {
        "dataIncluded": {
            "includeDividends": True,
            "includeInterest": True,
            "includeOrders": True,
            "includeTransactions": True,
        },
        "timeFrom": "2024-01-01T00:00:00Z",
        "timeTo": "2024-02-01T00:00:00Z",
    }


@pytest.mark.asyncio
async def test_no_other_post_is_possible() -> None:
    client = _client(lambda request: httpx.Response(200, json={}), MemorySnapshots())

    with pytest.raises(Trading212MethodNotAllowedError):
        await client.request_json("POST", EXPORTS_PATH)
    with pytest.raises(Trading212MethodNotAllowedError):
        await client._request("POST", "/equity/orders/market", json_body={"x": 1})
    await client.aclose()


@pytest.mark.asyncio
async def test_listing_stores_links_without_their_signature() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "reportId": 7,
                    "status": "Finished",
                    "downloadLink": "https://files.example.com/r.csv?X-Amz-Signature=secret",
                }
            ],
        )

    snapshots = MemorySnapshots()
    client = _client(handler, snapshots)
    [report] = await client.list_exports()
    await client.aclose()

    # The caller gets the working link; the stored snapshot does not keep the signature.
    assert report.download_link is not None and "Signature" in report.download_link
    assert "secret" not in json.dumps(snapshots.payloads)


@pytest.mark.asyncio
async def test_download_rejects_non_https_links() -> None:
    client = _client(lambda request: httpx.Response(200), MemorySnapshots())

    with pytest.raises(Exception, match="https"):
        await client.download_export("http://files.example.com/r.csv")
    await client.aclose()
