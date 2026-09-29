"""The calendar: earnings dates, declared and estimated dividends, dividend income."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from helios.config import Settings
from helios.db import migrate_database
from helios.events import (
    MarketEventsService,
    build_calendar,
    parse_dividends_json,
    parse_earnings_csv,
    payments_per_year,
)
from helios.models import (
    Dividend,
    DividendEvent,
    EarningsEvent,
    FxRateDaily,
    Instrument,
    PositionLive,
    WatchlistItem,
)
from helios.portfolio_repository import PortfolioRepository
from helios.rate_limit import Clock

D = Decimal
TODAY = date(2026, 9, 29)
NOW = datetime(2026, 9, 29, 10, 0, tzinfo=UTC)

EARNINGS_CSV = (
    "symbol,name,reportDate,fiscalDateEnding,estimate,currency,timeOfTheDay\n"
    "JPM,JPMorgan Chase,2026-10-14,2026-09-30,4.85,USD,pre-market\n"
    "NVDA,NVIDIA,2026-11-19,2026-10-31,,USD,post-market\n"
    "AAPL,Apple,2026-10-30,2026-09-30,1.60,USD,\n"
)


class FixedClock(Clock):
    def __init__(self, current: datetime) -> None:
        self.current = current

    def now(self) -> float:
        return self.current.timestamp()

    def utcnow(self) -> datetime:
        return self.current

    async def sleep(self, seconds: float) -> None:
        return None


def _fx(currency: str, rate: str, day: date = TODAY) -> FxRateDaily:
    return FxRateDaily(
        rate_date=day,
        currency_code=currency,
        eur_per_unit=D(rate),
        provider="ecb",
        source_date=day,
        provenance="exact",
        stale=False,
    )


def _position(ticker: str, quantity: str, value: str) -> PositionLive:
    return PositionLive(
        ts=NOW,
        t212_ticker=ticker,
        quantity=D(quantity),
        instrument_currency="USD",
        wallet_current_value=D(value),
    )


def _own(ticker: str, paid: date, amount_eur: str, quantity: str, per_share: str) -> Dividend:
    return Dividend(
        reference=f"{ticker}-{paid.isoformat()}",
        paid_on=datetime(paid.year, paid.month, paid.day, 15, tzinfo=UTC),
        t212_ticker=ticker,
        amount_in_euro=D(amount_eur),
        quantity=D(quantity),
        gross_amount_per_share=D(per_share),
    )


def test_the_earnings_csv_and_dividend_json_are_read_as_typed_rows() -> None:
    rows = parse_earnings_csv(EARNINGS_CSV)
    assert [row["symbol"] for row in rows] == ["JPM", "NVDA", "AAPL"]
    assert rows[0]["report_date"] == date(2026, 10, 14)
    assert rows[0]["estimate_eps"] == D("4.85")
    assert rows[0]["time_of_day"] == "pre-market"
    assert rows[1]["estimate_eps"] is None
    assert rows[2]["time_of_day"] is None

    dividends = parse_dividends_json(
        {
            "symbol": "JPM",
            "data": [
                {
                    "ex_dividend_date": "2026-10-06",
                    "declaration_date": "2026-09-15",
                    "record_date": "2026-10-06",
                    "payment_date": "2026-10-31",
                    "amount": "1.50",
                },
                {"ex_dividend_date": "2026-07-03", "payment_date": "None", "amount": "1.40"},
                {"ex_dividend_date": "None", "amount": "1.40"},
            ],
        }
    )
    assert [row["amount_per_share"] for row in dividends] == [D("1.50"), D("1.40")]
    assert dividends[1]["payment_date"] is None
    assert parse_dividends_json({"Information": "rate limit"}) == []


def test_the_rhythm_comes_from_the_gaps_between_payments() -> None:
    quarterly = [date(2026, 1, 31), date(2026, 4, 30), date(2026, 7, 31), date(2026, 10, 31)]
    assert payments_per_year(quarterly) == 4
    assert payments_per_year([date(2025, 6, 1), date(2026, 6, 1)]) == 1
    assert payments_per_year([date(2026, 6, 1)]) is None  # one payment has no rhythm
    assert payments_per_year([date(2026, 1, 1), date(2026, 3, 1), date(2026, 9, 1)]) is None


def test_the_calendar_confirms_declared_payments_and_estimates_the_rest() -> None:
    positions = [
        _position("JPM_US_EQ", "10", "2500"),
        _position("NVDA_US_EQ", "5", "900"),
        _position("VUAGl_EQ", "3", "300"),
    ]
    own = [
        # JPM: 10 shares x 1.40 USD x 0.9 EUR/USD = 12.60 gross; 10.71 arrived -> 85% net.
        _own("JPM_US_EQ", date(2026, 4, 30), "10.71", "10", "1.40"),
        _own("JPM_US_EQ", date(2026, 7, 31), "10.71", "10", "1.40"),
        # NVDA: a single payment, so no rhythm to project.
        _own("NVDA_US_EQ", date(2026, 6, 26), "0.95", "5", "0.25"),
    ]
    declared = [
        DividendEvent(
            t212_ticker="JPM_US_EQ",
            ex_date=date(2026, 7, 3),
            payment_date=date(2026, 7, 31),
            amount_per_share=D("1.40"),
        ),
        DividendEvent(
            t212_ticker="JPM_US_EQ",
            ex_date=date(2026, 10, 6),
            payment_date=date(2026, 10, 31),
            amount_per_share=D("1.50"),
        ),
    ]
    earnings = [
        EarningsEvent(
            t212_ticker="JPM_US_EQ", report_date=date(2026, 10, 14), time_of_day="pre-market"
        ),
        EarningsEvent(t212_ticker="AAPL_US_EQ", report_date=date(2026, 10, 30)),
        EarningsEvent(t212_ticker="MSFT_US_EQ", report_date=date(2026, 10, 28)),
    ]
    calendar = build_calendar(
        today=TODAY,
        positions=positions,
        watched=["AAPL_US_EQ"],
        instruments={},
        own_dividends=own,
        declared=declared,
        earnings=earnings,
        fx={"USD": [_fx("USD", "0.9", date(2026, 1, 1))]},
        provider_available=True,
        earnings_fetched_at=NOW,
    )

    jpm_payments = [event for event in calendar.events if event.kind == "dividend"]
    # The declared October payment is confirmed. January's, projected from the rhythm, is past
    # the calendar's 120 days but still counts in the year's income below.
    assert [(event.day, event.confirmed) for event in jpm_payments] == [(date(2026, 10, 31), True)]
    assert jpm_payments[0].amount_eur == D("11.48")  # 10 x 1.50 x 0.9 x 0.85
    assert jpm_payments[0].after_tax is True

    reports = [event for event in calendar.events if event.kind == "earnings"]
    # Held and watched companies only; MSFT is neither.
    assert [(event.ticker, event.held) for event in reports] == [
        ("JPM_US_EQ", True),
        ("AAPL_US_EQ", False),
    ]

    by_ticker = {row.ticker: row for row in calendar.holdings}
    jpm = by_ticker["JPM_US_EQ"]
    assert jpm.payments_per_year == 4 and jpm.source == "declared"
    assert jpm.next_payment_date == date(2026, 10, 31) and jpm.next_confirmed is True
    # Four quarterly payments in the coming year: one declared at 1.50, three estimated at 1.50.
    assert jpm.annual_eur == D("11.48") * 4
    assert by_ticker["NVDA_US_EQ"].annual_eur is None  # one payment: not projected
    assert by_ticker["VUAGl_EQ"].annual_eur == D(0) and by_ticker["VUAGl_EQ"].source == "none"

    assert calendar.received_12m_eur == D("22.37")
    months = {month.month: month for month in calendar.months}
    assert months["2026-07"].received_eur == D("10.71")
    assert months["2026-10"].projected_eur == D("11.48")
    assert len(calendar.months) == 24


async def _factory(tmp_path: Path) -> async_sessionmaker[AsyncSession]:
    settings = Settings(data_dir=tmp_path, sqlite_filename="events.sqlite3")
    await migrate_database(settings)
    engine = create_async_engine(settings.sqlite_url, poolclass=NullPool)
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_refresh_spends_the_quota_once_and_stores_what_it_learned(tmp_path: Path) -> None:
    factory = await _factory(tmp_path)
    async with factory() as session, session.begin():
        session.add_all(
            [
                Instrument(
                    t212_ticker="JPM_US_EQ",
                    name="JPMorgan Chase",
                    currency_code="USD",
                    instrument_type="STOCK",
                    yahoo_ticker="JPM",
                    mapping_status="resolved",
                ),
                Instrument(
                    t212_ticker="VUAGl_EQ",
                    name="Vanguard S&P 500",
                    currency_code="GBP",
                    instrument_type="ETF",
                    yahoo_ticker="VUAG.L",
                    mapping_status="resolved",
                ),
                _position("JPM_US_EQ", "10", "2500"),
                _position("VUAGl_EQ", "3", "300"),
            ]
        )
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        function = request.url.params["function"]
        if function == "EARNINGS_CALENDAR":
            return httpx.Response(200, text=EARNINGS_CSV)
        return httpx.Response(
            200,
            json={
                "symbol": request.url.params["symbol"],
                "data": [
                    {
                        "ex_dividend_date": "2026-10-06",
                        "payment_date": "2026-10-31",
                        "amount": "1.50",
                    }
                ],
            },
        )

    settings = Settings(
        data_dir=tmp_path,
        market_data_fallback_api_key=SecretStr("av-key"),
        alphavantage_min_interval_seconds=0,
    )
    service = MarketEventsService(
        factory,
        settings,
        clock=FixedClock(NOW),
        client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )

    counts = await service.refresh()
    again = await service.refresh()

    # One calendar request and one dividends request (the ETF has neither); nothing re-asked.
    assert [request.url.params["function"] for request in requests] == [
        "EARNINGS_CALENDAR",
        "DIVIDENDS",
    ]
    assert requests[1].url.params["symbol"] == "JPM"
    assert counts == {"earnings": 1, "dividends": 1}
    assert again == {"earnings": 0, "dividends": 0}

    calendar = await service.calendar()
    assert [(event.kind, event.ticker) for event in calendar.events][:2] == [
        ("earnings", "JPM_US_EQ"),
        ("dividend", "JPM_US_EQ"),
    ]


@pytest.mark.asyncio
async def test_without_an_alpha_vantage_key_nothing_is_fetched(tmp_path: Path) -> None:
    factory = await _factory(tmp_path)

    def fail(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    service = MarketEventsService(
        factory,
        Settings(data_dir=tmp_path),
        client=httpx.AsyncClient(transport=httpx.MockTransport(fail)),
    )
    assert await service.refresh() == {"earnings": 0, "dividends": 0}
    calendar = await service.calendar()
    assert calendar.provider_available is False
    assert calendar.notes and "Alpha Vantage" in calendar.notes[0]


@pytest.mark.asyncio
async def test_earnings_tomorrow_and_a_fresh_dividend_notify_once(tmp_path: Path) -> None:
    factory = await _factory(tmp_path)
    async with factory() as session, session.begin():
        session.add_all(
            [
                Instrument(
                    t212_ticker="JPM_US_EQ", name="JPMorgan Chase", mapping_status="resolved"
                ),
                _position("JPM_US_EQ", "10", "2500"),
                WatchlistItem(t212_ticker="AAPL_US_EQ", added_at=NOW),
                EarningsEvent(
                    t212_ticker="JPM_US_EQ",
                    report_date=TODAY + timedelta(days=1),
                    estimate_eps=D("4.85"),
                    currency_code="USD",
                    time_of_day="pre-market",
                    fetched_at=NOW,
                ),
                _own("JPM_US_EQ", TODAY - timedelta(days=1), "10.71", "10", "1.40"),
            ]
        )
    repository = PortfolioRepository(factory)
    service = MarketEventsService(factory, Settings(data_dir=tmp_path), clock=FixedClock(NOW))

    first = await service.notify(repository)
    second = await service.notify(repository)

    assert [notice.title for notice in first] == [
        "JPMorgan Chase reports tomorrow",
        "Dividend from JPMorgan Chase",
    ]
    assert "before the market opens" in first[0].body and "4.85 USD" in first[0].body
    assert first[1].body == "€10.71 arrived in your account."
    assert second == []
