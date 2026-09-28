"""Price alerts, notifications and the daily summary."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from helios.alerts import (
    MINUS,
    AlertService,
    DailySummaryService,
    Quote,
    SummaryFacts,
    alert_met,
    compose_summary,
)
from helios.config import Settings
from helios.db import migrate_database
from helios.models import MarketPriceDaily, PriceAlert
from helios.performance import NoPerformanceDataError
from helios.periods import HoldingMovement, PeriodSummary
from helios.portfolio_repository import PortfolioRepository
from helios.rate_limit import Clock
from helios.schemas import Position

D = Decimal
NOW = datetime(2026, 9, 28, 19, 30, tzinfo=UTC)


class FixedClock(Clock):
    def now(self) -> float:
        return NOW.timestamp()

    def utcnow(self) -> datetime:
        return NOW

    async def sleep(self, seconds: float) -> None:
        return None


@dataclass
class FakePositions:
    positions: list[Position] = field(default_factory=list)

    async def get_positions(self) -> list[Position]:
        return self.positions


def _position(ticker: str, price: str, average: str) -> Position:
    return Position.model_validate(
        {
            "instrument": {"ticker": ticker, "name": "Micron Technology", "currency": "USD"},
            "quantity": "1",
            "currentPrice": price,
            "averagePricePaid": average,
        }
    )


async def _repository(tmp_path: Path) -> tuple[PortfolioRepository, Settings]:
    settings = Settings(
        data_dir=tmp_path, sqlite_filename="alerts.sqlite3", t212_api_key="k", t212_api_secret="s"
    )
    await migrate_database(settings)
    engine = create_async_engine(settings.sqlite_url, poolclass=NullPool)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)
    return PortfolioRepository(factory), settings


def _alert(ticker: str, kind: str, threshold: str, note: str | None = None) -> PriceAlert:
    return PriceAlert(
        ticker=ticker, kind=kind, threshold=D(threshold), note=note, created_at=NOW, active=True
    )


@pytest.mark.parametrize(
    ("kind", "threshold", "met"),
    [
        ("above", "1000", True),
        ("above", "1100", False),
        ("below", "1100", True),
        ("gain_pct", "200", True),  # 1054 on 325.60 is +223.7%
        ("gain_pct", "250", False),
        ("loss_pct", "5", False),
    ],
)
def test_alert_conditions(kind: str, threshold: str, met: bool) -> None:
    quote = Quote(price=D("1054"), currency="USD", average_cost=D("325.60"), name=None, live=True)

    assert alert_met(kind, D(threshold), quote) is met


def test_gain_alerts_need_an_average_cost() -> None:
    quote = Quote(price=D("10"), currency="USD", average_cost=None, name=None, live=False)

    assert alert_met("gain_pct", D("1"), quote) is False


@pytest.mark.asyncio
async def test_a_met_alert_fires_once_and_switches_itself_off(tmp_path: Path) -> None:
    repository, settings = await _repository(tmp_path)
    await repository.add_price_alert(_alert("MU_US_EQ", "above", "1000", note="take profit?"))
    await repository.add_price_alert(_alert("MU_US_EQ", "below", "900"))
    service = AlertService(
        repository,
        FakePositions([_position("MU_US_EQ", "1054", "325.60")]),
        settings,
        FixedClock(),
    )

    first = await service.evaluate()
    second = await service.evaluate()

    assert [item.title for item in first] == ["MU: price at or above 1,000 USD"]
    assert "Micron Technology is at 1,054 USD (+223.7% on your average cost)" in first[0].body
    assert "take profit?" in first[0].body
    assert second == []
    alerts = await repository.list_price_alerts(ticker="MU_US_EQ")
    fired = next(alert for alert in alerts if alert.kind == "above")
    assert (fired.active, fired.triggered_price) == (False, D("1054"))
    [pending] = await repository.list_notifications(pending_only=True)
    assert pending.url == "/holdings/MU_US_EQ"


@pytest.mark.asyncio
async def test_a_ticker_not_held_is_checked_against_its_last_close(tmp_path: Path) -> None:
    repository, settings = await _repository(tmp_path)
    await repository.upsert_market_prices(
        [
            MarketPriceDaily(
                price_date=date(2026, 9, 25),
                t212_ticker="VWRPl_EQ",
                provider_symbol="VWRP.L",
                currency_code="GBP",
                close_price=D("146.12"),
                provider="fixture",
                source_date=date(2026, 9, 25),
                provenance="EXACT",
            )
        ]
    )
    await repository.add_price_alert(_alert("VWRPl_EQ", "above", "145"))
    service = AlertService(repository, FakePositions(), settings, FixedClock())

    [fired] = await service.evaluate()

    assert fired.body.endswith("at the last daily close.")


@pytest.mark.asyncio
async def test_notifications_are_deduplicated_and_acknowledged(tmp_path: Path) -> None:
    repository, _ = await _repository(tmp_path)
    await repository.add_price_alert(_alert("MU_US_EQ", "above", "1"))
    service = AlertService(
        repository,
        FakePositions([_position("MU_US_EQ", "5", "4")]),
        Settings(data_dir=tmp_path, t212_api_key="k", t212_api_secret="s"),
        FixedClock(),
    )
    [notification] = await service.evaluate()
    [stored] = await repository.list_notifications(pending_only=True)

    assert await repository.add_notification(notification) is False  # same dedupe key
    assert await repository.mark_notification_delivered(stored.id, now=NOW) is True
    assert await repository.mark_notification_delivered(stored.id, now=NOW) is False
    assert await repository.list_notifications(pending_only=True) == []


# ---------------------------------------------------------------------------
# Daily summary
# ---------------------------------------------------------------------------


def test_summary_wording() -> None:
    title, body = compose_summary(
        SummaryFacts(
            day_result=D("-10.26"),
            day_return=-0.0075,
            value=D("1355.51"),
            best=("JPM", D("0.69")),
            worst=("ACHV", D("-11.67")),
            card_spent=D("9.94"),
            card_payments=2,
            stories=3,
        )
    )

    assert title == f"Today: {MINUS}€10.26 (-0.75%)"
    assert body == (
        f"Portfolio worth €1,355.51. Movers: best JPM +€0.69, worst ACHV {MINUS}€11.67. "
        "Card: €9.94 in 2 payment(s). 3 new story(ies) about your holdings."
    )


def _period(result: str, holdings: list[HoldingMovement]) -> PeriodSummary:
    zero = D("0")
    return PeriodSummary(
        key="1D",
        label="Last trading day",
        status="ok",
        start_date=date(2026, 9, 24),
        end_date=date(2026, 9, 25),
        start_value_eur=D("1365"),
        end_value_eur=D("1355"),
        value_change_eur=D(result),
        deposits_eur=zero,
        withdrawals_eur=zero,
        net_deposits_eur=zero,
        investment_result_eur=D(result),
        market_eur=D(result),
        dividends_eur=zero,
        interest_eur=zero,
        fees_eur=zero,
        card_spending_eur=zero,
        cashback_eur=zero,
        twr=-0.0075,
        unvalued_days=0,
        detail=None,
        holdings=holdings,
    )


@dataclass
class FakeReport:
    period_summaries: list[PeriodSummary]


@dataclass
class FakeReportSource:
    report: FakeReport | None

    async def get_report(self) -> FakeReport:
        if self.report is None:
            raise NoPerformanceDataError("none")
        return self.report


class FakeNews:
    async def list_ranked_news(self, **_: object) -> list[object]:
        return []


@pytest.mark.asyncio
async def test_one_summary_a_day_and_only_after_its_time(tmp_path: Path) -> None:
    repository, settings = await _repository(tmp_path)
    movement = HoldingMovement(
        ticker="ACHV_US_EQ",
        status="ok",
        start_value_eur=D("291"),
        end_value_eur=D("280"),
        start_quantity=D("1"),
        end_quantity=D("1"),
        bought_eur=D("0"),
        sold_eur=D("0"),
        dividends_eur=D("0"),
        result_eur=D("-11"),
        return_pct=-0.04,
        price_change_pct=-0.04,
    )
    service = DailySummaryService(
        repository,
        FakeReportSource(FakeReport([_period("-10.26", [movement])])),  # type: ignore[arg-type]
        FakeNews(),  # type: ignore[arg-type]
        settings,
        FixedClock(),
    )
    evening = datetime.combine(date(2026, 9, 28), time(21, 5)).astimezone()
    afternoon = datetime.combine(date(2026, 9, 28), time(15, 0)).astimezone()

    assert await service.maybe_create(now_local=afternoon) is None
    created = await service.maybe_create(now_local=evening)
    again = await service.maybe_create(now_local=evening)

    assert created is not None and created.title.startswith(f"Today: {MINUS}€10.26")
    assert "worst ACHV" in created.body
    assert again is None


@pytest.mark.asyncio
async def test_a_summary_before_any_replay_says_so(tmp_path: Path) -> None:
    repository, settings = await _repository(tmp_path)
    service = DailySummaryService(
        repository,
        FakeReportSource(None),  # type: ignore[arg-type]
        FakeNews(),  # type: ignore[arg-type]
        settings,
        FixedClock(),
    )

    created = await service.maybe_create(
        now_local=datetime.combine(date(2026, 9, 28), time(22, 0)).astimezone()
    )

    assert created is not None
    assert created.body == "No valued trading day to report yet."
