"""The instrument detail page's data, checked against a hand-worked three-day history.

    Mon  deposit 1,000; buy 10 EXQ @ 50 (500)          price 50
    Tue                                                 price 55
    Wed  sell 4 @ 60 (240)                              price 60

So: bought 500, sold 240, 6 left worth 360 -> the holding made 100.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from helios.config import Settings
from helios.instrument_detail import InstrumentDetailService, UnknownInstrumentError
from helios.models import Instrument, OrderHistory, Transaction
from helios.performance import DailyPricePoint, PerformanceReplayService
from helios.schemas import InstrumentDetailModel
from test_performance import (
    FakeFxRateProvider,
    FakeMarketDataProvider,
    FixedClock,
    _repository_and_session_factory,
)

D = Decimal
MONDAY = date(2024, 1, 1)
TUESDAY = MONDAY + timedelta(days=1)
WEDNESDAY = MONDAY + timedelta(days=2)


def _at(day: date) -> datetime:
    return datetime.combine(day, datetime.min.time(), tzinfo=UTC)


async def _replayed(tmp_path: Path) -> InstrumentDetailService:
    repository, session_factory = await _repository_and_session_factory(tmp_path, "detail.sqlite3")
    async with session_factory() as session, session.begin():
        session.add(
            Instrument(
                t212_ticker="EXQ_EQ",
                name="Example Quoted",
                isin="DE000EXQ0001",
                yahoo_ticker="EXQ.DE",
                currency_code="EUR",
                mapping_status="resolved",
            )
        )
        session.add(
            Transaction(
                reference="dep",
                ts=_at(MONDAY),
                transaction_type="DEPOSIT",
                currency_code="EUR",
                amount=D("1000"),
            )
        )
        for fill_id, day, side, quantity, value, price in (
            ("buy-1", MONDAY, "BUY", D("10"), D("500"), D("50")),
            ("sell-1", WEDNESDAY, "SELL", D("-4"), D("240"), D("60")),
        ):
            session.add(
                OrderHistory(
                    fill_id=fill_id,
                    fill_timestamp=_at(day),
                    t212_ticker="EXQ_EQ",
                    side=side,
                    fill_type="TRADE",
                    filled_quantity=quantity,
                    fill_price=price,
                    wallet_currency="EUR",
                    wallet_net_value=value,
                    wallet_realised_profit_loss=D("40") if side == "SELL" else None,
                )
            )
    prices = {MONDAY: D("50"), TUESDAY: D("55"), WEDNESDAY: D("60")}
    settings = Settings(data_dir=tmp_path, sqlite_filename="detail.sqlite3")
    replay = PerformanceReplayService(
        repository,
        settings,
        FakeMarketDataProvider(
            {
                "EXQ_EQ": [
                    DailyPricePoint(day, price, "EUR", "fixture", day, "EXACT")
                    for day, price in prices.items()
                ]
            }
        ),
        FakeFxRateProvider({}),
        clock=FixedClock(_at(WEDNESDAY)),
    )
    await replay.replay(as_of=WEDNESDAY)
    return InstrumentDetailService(repository, settings)


@pytest.mark.asyncio
async def test_detail_reports_position_trades_and_result(tmp_path: Path) -> None:
    detail = await (await _replayed(tmp_path)).detail("EXQ_EQ")

    assert (detail.name, detail.currency, detail.isin) == ("Example Quoted", "EUR", "DE000EXQ0001")
    assert detail.quantity == D("6")
    assert (detail.bought_eur, detail.sold_eur, detail.value_eur) == (D("500"), D("240"), D("360"))
    assert detail.result_eur == D("100")
    assert detail.realised_eur == D("40")
    assert detail.first_bought == _at(MONDAY)
    assert [(trade.side, trade.quantity, trade.price) for trade in detail.trades] == [
        ("BUY", D("10"), D("50")),
        # Trading 212 reports a sale's quantity as negative; the page shows shares sold.
        ("SELL", D("4"), D("60")),
    ]


@pytest.mark.asyncio
async def test_detail_prices_extremes_and_position_history(tmp_path: Path) -> None:
    detail = await (await _replayed(tmp_path)).detail("EXQ_EQ")

    assert [(point.as_of_date, point.close, point.close_eur) for point in detail.prices] == [
        (MONDAY, D("50"), D("50")),
        (TUESDAY, D("55"), D("55")),
        (WEDNESDAY, D("60"), D("60")),
    ]
    assert detail.high is not None and detail.high.close == D("60")
    assert detail.low is not None and detail.low.close == D("50")
    # Money in the holding steps down on the sale; its value follows the price.
    assert [(point.value_eur, point.invested_eur) for point in detail.positions] == [
        (D("500"), D("500")),
        (D("550"), D("500")),
        (D("360"), D("260")),
    ]
    since_start = next(item for item in detail.price_returns if item.key == "ALL")
    assert since_start.change_pct == pytest.approx(0.2)
    # Three days of history cannot answer "one year".
    assert next(item for item in detail.price_returns if item.key == "1Y").change_pct is None


@pytest.mark.asyncio
async def test_detail_period_results_match_the_overview_split(tmp_path: Path) -> None:
    detail = await (await _replayed(tmp_path)).detail("EXQ_EQ")

    since_start = next(item for item in detail.periods if item.key == "ALL")
    assert since_start.result_eur == D("100")
    payload = InstrumentDetailModel.model_validate(detail, from_attributes=True).model_dump(
        by_alias=True, mode="json"
    )
    assert payload["resultEur"] == "100"
    assert payload["priceReturns"][0]["key"] == "1W"


@pytest.mark.asyncio
async def test_unknown_ticker_is_reported_as_such(tmp_path: Path) -> None:
    service = await _replayed(tmp_path)

    with pytest.raises(UnknownInstrumentError):
        await service.detail("NOPE_EQ")
