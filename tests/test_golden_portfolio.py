"""Golden-portfolio regression test for the performance replay/report pipeline.

Helios's whole pitch is "the numbers are honest". ``test_performance.py`` checks individual
functions (``compute_daily_twr``, ``compute_drawdown``, ``compute_xirr``, ...) in isolation, but
nothing end-to-end pins what a *realistic* portfolio, replayed through the real
``PerformanceReplayService``, should actually produce. A refactor that quietly changes the flow
convention, the forward-fill cutoff, a sign, or a rounding rule can leave every unit test green
while every NAV/TWR/XIRR figure on the dashboard moves. This file is that missing end-to-end
guard: one small, deterministic, hand-verified portfolio, replayed through the real
``PerformanceReplayService.replay()`` / ``get_report()`` path, with the headline outputs pinned as
exact values.

The dataset (module-level constants below): two non-EUR instruments (USD and GBP, so ECB-style FX
conversion is exercised for both), ~3 months of *weekly* price/FX points with everything else
forward-filled (which is how real market data behaves across weekends and non-trading days), two
deposits, one withdrawal, two buys, one partial sell, and one dividend.

How the pinned values were produced (not just "whatever the code output at the time"):

1. ``_reference_nav_series()`` below is a second, independent implementation of the replay's cash
   + mark-to-market arithmetic, written from scratch in plain ``Decimal`` -- it does not import or
   call anything from ``helios.performance``.
2. ``test_reference_simulation_matches_hand_worked_nav_dates`` hand-derives three of those NAV
   figures with literal arithmetic (shown in the assertions themselves) and cross-checks them
   against ``_reference_nav_series()``, and separately hand-decomposes the cumulative TWR as the
   product of exactly the days that can move it (see that test for the reasoning).
3. ``test_golden_portfolio_replay_matches_frozen_reference_values`` seeds a real repository and
   runs the actual ``PerformanceReplayService``, and asserts its output against the *same* frozen
   constants -- so the real engine, the hand arithmetic, and the independent reference
   implementation all have to agree.

A real bug was found while building this dataset: the first draft used a EUR-denominated second
instrument, and ``_build_daily_replay``'s holdings-valuation loop looked up an FX series for every
holding's currency unconditionally -- including EUR itself, for which the FX provider (correctly)
never fetches a series. A EUR holding therefore read as ``MISSING_FX`` on every day and NAV went
``PARTIAL`` for good from the day it was bought. That is fixed (a base-currency holding now
converts at exactly 1) and pinned by ``test_a_base_currency_holding_is_valued_without_fx`` at the
bottom of this file. The golden dataset keeps USD and GBP so both FX paths stay exercised.

Updating the pins: if a change to the replay/report methodology is intentional (a new flow-timing
default, a different annualisation basis, a rounding fix, etc.), regenerate every constant below
from the real engine's output, re-verify the hand-worked dates and the TWR decomposition still
foot, and say so explicitly in the commit message (e.g. "golden portfolio: re-pin NAV/TWR after
switching the default flow-timing convention"). Never update a pin just to make a failing test
pass without re-doing that verification.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from helios.config import Settings
from helios.models import Dividend, Instrument, OrderHistory, Transaction
from helios.performance import DailyPricePoint, FxRatePoint, PerformanceReplayService
from test_performance import (
    FakeFxRateProvider,
    FakeMarketDataProvider,
    FixedClock,
    _repository_and_session_factory,
)

D = Decimal
ZERO = Decimal("0")

# ---------------------------------------------------------------------------
# The frozen golden dataset
# ---------------------------------------------------------------------------
#
# Weekly (Monday) closing prices; every other calendar day -- weekends included -- is valued by
# the replay's own forward-fill, exactly as it would be for a real instrument that only trades on
# business days. USD_TECH_EQ and GBP_INDU_EQ are both fictional; the tickers are chosen to read
# clearly rather than to resemble a real listing.
USD_TECH_PRICES: dict[date, Decimal] = {
    date(2024, 1, 1): D("100.00"),
    date(2024, 1, 8): D("105.00"),
    date(2024, 1, 15): D("110.00"),
    date(2024, 1, 22): D("108.00"),
    date(2024, 1, 29): D("95.00"),
    date(2024, 2, 5): D("90.00"),
    date(2024, 2, 12): D("100.00"),
    date(2024, 2, 19): D("115.00"),
    date(2024, 2, 26): D("120.00"),
    date(2024, 3, 4): D("118.00"),
    date(2024, 3, 11): D("125.00"),
    date(2024, 3, 18): D("130.00"),
    date(2024, 3, 25): D("128.00"),
}

GBP_INDU_PRICES: dict[date, Decimal] = {
    date(2024, 1, 1): D("50.00"),
    date(2024, 1, 8): D("51.00"),
    date(2024, 1, 15): D("52.00"),
    date(2024, 1, 22): D("50.00"),
    date(2024, 1, 29): D("49.00"),
    date(2024, 2, 5): D("48.00"),
    date(2024, 2, 12): D("50.00"),
    date(2024, 2, 19): D("53.00"),
    date(2024, 2, 26): D("55.00"),
    date(2024, 3, 4): D("54.00"),
    date(2024, 3, 11): D("56.00"),
    date(2024, 3, 18): D("58.00"),
    date(2024, 3, 25): D("57.00"),
}

# Constant FX throughout the window -- still real ECB-style conversion (a non-EUR holding must go
# through a rate to reach EUR), just chosen constant so the hand arithmetic below stays tractable.
USD_EUR_RATE = D("0.90")  # EUR per USD
GBP_EUR_RATE = D("1.15")  # EUR per GBP

START_DATE = date(2024, 1, 2)
END_DATE = date(2024, 3, 29)

# All cash-flow and trade events. Deliberately never on a Monday (the weekly price-update day), so
# every price-update day's return is driven purely by the price move and every flow/trade day's
# return is driven purely by the flow-neutral (or fair-value-trade) property -- see the TWR
# decomposition in test_reference_simulation_matches_hand_worked_nav_dates.
DEPOSIT_1 = (date(2024, 1, 2), D("10000.00"))
BUY_USD = (date(2024, 1, 3), D("10"), D("900.00"))  # 10 sh @ $100.00 * 0.90 EUR/USD
BUY_GBP = (date(2024, 1, 17), D("20"), D("1196.00"))  # 20 sh @ GBP52.00 * 1.15 EUR/GBP
DEPOSIT_2 = (date(2024, 2, 1), D("5000.00"))
DIVIDEND = (date(2024, 2, 14), D("12.00"))  # USD, converted at the pay-date FX
SELL_USD = (date(2024, 3, 6), D("4"), D("424.80"))  # 4 sh @ $118.00 * 0.90 EUR/USD
WITHDRAWAL = (date(2024, 3, 20), D("2000.00"))


# ---------------------------------------------------------------------------
# Frozen pins -- the numbers under test
# ---------------------------------------------------------------------------
#
# NAV at: the first day, a weekend (carried forward), a deposit day, the dividend day, the
# withdrawal day, and the last day.
PINNED_NAV: dict[date, Decimal] = {
    date(2024, 1, 2): D("10000.00"),  # first day: the EUR 10,000 deposit, no holdings yet
    date(2024, 1, 6): D("10000.00"),  # Saturday: carried forward from Jan 3
    date(2024, 2, 1): D("14886.00"),  # a deposit day
    date(2024, 2, 14): D("14964.80"),  # the dividend day
    date(2024, 3, 20): D("13375.60"),  # the withdrawal day
    date(2024, 3, 29): D("13341.80"),  # last day (as_of), forward-filled from Mar 25
}

PINNED_CUMULATIVE_TWR = 0.018534671266450475
PINNED_XIRR = 0.11450964649407087
PINNED_ANNUALIZED_RETURN = 0.08009451712833893
PINNED_VOLATILITY = 0.053151843863394305
PINNED_DOWNSIDE_VOLATILITY = 0.08992845164985407
PINNED_SHARPE = 1.476001090101396
PINNED_SORTINO = 0.8723844127632825
PINNED_CALMAR = 3.243516202812547
# Re-pinned 2026-09-27 for a deliberate methodology change: drawdown is now measured on the
# time-weighted index instead of raw NAV, so the EUR 2,000 withdrawal on 2024-03-20 no longer
# reads as a 13% "loss". Verified independently of the engine by compounding flow_at_close
# returns over _reference_nav_series(): peak 2024-01-21, trough 2024-02-05, -2.469373116091933%.
PINNED_MAX_DRAWDOWN = -0.02469373116091933
PINNED_TIME_UNDERWATER_DAYS = 36.0  # 2024-01-21 peak -> 2024-02-26 recovery
PINNED_NAV_ROWS_WRITTEN = 88
PINNED_HOLDINGS_ROWS_WRITTEN = 160

# Ratios are floats (per the project's own "Decimal for money, floats only in the statistical
# layer" rule -- see the module docstring of helios.performance). 1e-9 is tight enough to catch a
# methodology change but loose enough to absorb float-order-of-operations noise between numpy/
# platform versions; XIRR's root-finder (scipy.brentq) converges far tighter than that by default,
# so the same tolerance is safe for it too.
FLOAT_TOLERANCE = 1e-9


# ---------------------------------------------------------------------------
# Independent reference simulation (plain Decimal, no helios.performance imports)
# ---------------------------------------------------------------------------


def _forward_fill(series: dict[date, Decimal], day: date) -> Decimal:
    """The same "carry the last observation forward" rule the replay uses for prices/FX."""
    candidate_dates = [observed for observed in series if observed <= day]
    return series[max(candidate_dates)]


@dataclass(frozen=True)
class _ReferenceResult:
    nav_by_day: dict[date, Decimal]
    external_flow_by_day: dict[date, Decimal]
    ordered_days: list[date]


def _reference_nav_series() -> _ReferenceResult:
    """Rebuild the golden ledger's daily NAV from scratch, independently of the replay engine.

    This mirrors the *economics* of ``_build_daily_replay`` (cash ledger + mark-to-market
    valuation with forward-filled prices/FX) but is a separate implementation: it does not import
    or call any helios.performance code. Used to hand-verify the frozen pins above rather than
    just trusting whatever the engine happens to emit.
    """
    cash = ZERO
    qty_usd = ZERO
    qty_gbp = ZERO
    nav_by_day: dict[date, Decimal] = {}
    external_flow_by_day: dict[date, Decimal] = {}

    day = START_DATE
    while day <= END_DATE:
        flow = ZERO
        if day == DEPOSIT_1[0]:
            cash += DEPOSIT_1[1]
            flow += DEPOSIT_1[1]
        if day == BUY_USD[0]:
            cash -= BUY_USD[2]
            qty_usd += BUY_USD[1]
        if day == BUY_GBP[0]:
            cash -= BUY_GBP[2]
            qty_gbp += BUY_GBP[1]
        if day == DEPOSIT_2[0]:
            cash += DEPOSIT_2[1]
            flow += DEPOSIT_2[1]
        if day == DIVIDEND[0]:
            cash += DIVIDEND[1] * _forward_fill({d: USD_EUR_RATE for d in USD_TECH_PRICES}, day)
        if day == SELL_USD[0]:
            cash += SELL_USD[2]
            qty_usd -= SELL_USD[1]
        if day == WITHDRAWAL[0]:
            cash -= WITHDRAWAL[1]
            flow -= WITHDRAWAL[1]

        external_flow_by_day[day] = flow
        usd_price = _forward_fill(USD_TECH_PRICES, day)
        gbp_price = _forward_fill(GBP_INDU_PRICES, day)
        securities = qty_usd * usd_price * USD_EUR_RATE + qty_gbp * gbp_price * GBP_EUR_RATE
        nav_by_day[day] = cash + securities
        day += timedelta(days=1)

    return _ReferenceResult(
        nav_by_day=nav_by_day,
        external_flow_by_day=external_flow_by_day,
        ordered_days=sorted(nav_by_day),
    )


def _reference_daily_twr(reference: _ReferenceResult) -> list[float]:
    """``flow_at_close`` daily TWR: r_t = (NAV_t - external_flow_t) / NAV_(t-1) - 1."""
    returns: list[float] = []
    for previous_day, current_day in zip(
        reference.ordered_days, reference.ordered_days[1:], strict=False
    ):
        previous_nav = reference.nav_by_day[previous_day]
        current_nav = reference.nav_by_day[current_day]
        flow = reference.external_flow_by_day[current_day]
        returns.append(float((current_nav - flow) / previous_nav - 1))
    return returns


# ---------------------------------------------------------------------------
# Part 1: hand-verify the frozen pins before the engine ever runs
# ---------------------------------------------------------------------------


def test_reference_simulation_matches_hand_worked_nav_dates() -> None:
    """Hand-derive three NAV figures with literal arithmetic and cross-check the reference sim.

    If this test and the golden-portfolio replay test below ever disagree, the bug is in the
    reference simulation or the hand arithmetic here -- not in the engine.
    """
    reference = _reference_nav_series()

    # 2024-01-02: the only event is the EUR 10,000 deposit; nothing is held yet.
    hand_nav_jan2 = D("10000.00")
    assert hand_nav_jan2 == reference.nav_by_day[date(2024, 1, 2)] == PINNED_NAV[date(2024, 1, 2)]

    # 2024-02-01: cash = 10000 (deposit) - 900 (buy 10 USD_TECH) - 1196 (buy 20 GBP_INDU)
    #                    + 5000 (deposit) = 12904.00
    # securities = 10 * 95.00 * 0.90   USD_TECH, forward-filled from the 2024-01-29 price
    #            + 20 * 49.00 * 1.15   GBP_INDU, forward-filled from the 2024-01-29 price
    #            = 855.00 + 1127.00 = 1982.00
    # NAV = 12904.00 + 1982.00 = 14886.00
    hand_cash_feb1 = D("10000.00") - D("900.00") - D("1196.00") + D("5000.00")
    hand_securities_feb1 = D("10") * D("95.00") * D("0.90") + D("20") * D("49.00") * D("1.15")
    hand_nav_feb1 = hand_cash_feb1 + hand_securities_feb1
    assert hand_nav_feb1 == D("14886.00")
    assert hand_nav_feb1 == reference.nav_by_day[date(2024, 2, 1)] == PINNED_NAV[date(2024, 2, 1)]

    # 2024-03-20 (the withdrawal day): cash after the deposits/buys above, plus the Feb-14
    # dividend and the Mar-6 partial-sale proceeds, minus the EUR 2,000 withdrawal.
    #   cash = 12904.00 + 12.00*0.90 (dividend) + 424.80 (sale proceeds) - 2000.00 = 11339.60
    # securities = 6 * 130.00 * 0.90   USD_TECH, 6 remaining after the sale, fwd-filled from Mar 18
    #            + 20 * 58.00 * 1.15   GBP_INDU, fwd-filled from Mar 18
    #            = 702.00 + 1334.00 = 2036.00
    # NAV = 11339.60 + 2036.00 = 13375.60
    hand_cash_mar20 = D("12904.00") + D("12.00") * D("0.90") + D("424.80") - D("2000.00")
    hand_securities_mar20 = D("6") * D("130.00") * D("0.90") + D("20") * D("58.00") * D("1.15")
    hand_nav_mar20 = hand_cash_mar20 + hand_securities_mar20
    assert hand_nav_mar20 == D("13375.60")
    assert (
        hand_nav_mar20 == reference.nav_by_day[date(2024, 3, 20)] == PINNED_NAV[date(2024, 3, 20)]
    )

    # Every other pinned checkpoint also matches the reference simulation.
    for checkpoint_date, expected_nav in PINNED_NAV.items():
        assert reference.nav_by_day[checkpoint_date] == expected_nav, checkpoint_date


def test_reference_simulation_cumulative_twr_decomposition() -> None:
    """Hand-decompose the cumulative TWR instead of trusting a single compounded number.

    Every event date above (both deposits, both trades, the withdrawal) was deliberately placed
    on a day that is *not* a weekly price-update Monday. Under ``flow_at_close``:

    * A day whose only event is an external flow (deposit/withdrawal) returns exactly 0.0 -- the
      flow is subtracted from the numerator before dividing by yesterday's NAV, so a same-day
      deposit/withdrawal cannot move the reported return (see
      ``test_daily_twr_is_exactly_zero_on_a_deposit_only_day`` in test_performance.py).
    * A buy/sell executed at exactly the prevailing forward-filled price is a fair-value exchange
      of cash for securities: NAV is unchanged, so that day's return is also exactly 0.0.

    That leaves only the 12 weekly Monday price-update days (2024-01-08 .. 2024-03-25 minus the
    2024-01-01 anchor, which predates the ledger's start date) and the one dividend day
    (dividends are *internal* cash flow, not external, so they DO count towards TWR) as days that
    can move the cumulative return at all. Confirming there are exactly 13 non-zero days -- no
    more, no fewer -- is itself part of the hand-check: it would catch a stray event landing on a
    Monday, or a flow/trade day picking up an unintended price change.
    """
    reference = _reference_nav_series()
    returns = _reference_daily_twr(reference)

    non_zero_returns = [value for value in returns if value != 0.0]
    assert len(non_zero_returns) == 13

    cumulative = 1.0
    for value in returns:
        cumulative *= 1.0 + value
    cumulative -= 1.0

    assert cumulative == pytest.approx(PINNED_CUMULATIVE_TWR, abs=FLOAT_TOLERANCE)


# ---------------------------------------------------------------------------
# Part 2: the real engine, replayed end-to-end, must match the same frozen pins
# ---------------------------------------------------------------------------


def _seed_prices(ticker: str, currency: str, series: dict[date, Decimal]) -> list[DailyPricePoint]:
    return [
        DailyPricePoint(day, price, currency, "golden-fixture", day, "EXACT")
        for day, price in series.items()
    ]


def _seed_fx(currency: str, rate: Decimal, days: Iterable[date]) -> list[FxRatePoint]:
    return [FxRatePoint(day, currency, rate, "golden-fixture", day, "EXACT", False) for day in days]


async def test_golden_portfolio_replay_matches_frozen_reference_values(tmp_path: Path) -> None:
    """End-to-end: seed the golden ledger, run the real replay + report, check every frozen pin."""
    repository, session_factory = await _repository_and_session_factory(
        tmp_path, "golden_portfolio.sqlite3"
    )
    async with session_factory() as session:
        async with session.begin():
            session.add(
                Instrument(
                    t212_ticker="USD_TECH_EQ",
                    yahoo_ticker="UTQ",
                    currency_code="USD",
                    mapping_status="resolved",
                )
            )
            session.add(
                Instrument(
                    t212_ticker="GBP_INDU_EQ",
                    yahoo_ticker="GIQ",
                    currency_code="GBP",
                    mapping_status="resolved",
                )
            )
            session.add(
                Transaction(
                    reference="dep-1",
                    ts=datetime.combine(DEPOSIT_1[0], datetime.min.time(), tzinfo=UTC),
                    transaction_type="DEPOSIT",
                    currency_code="EUR",
                    amount=DEPOSIT_1[1],
                )
            )
            session.add(
                Transaction(
                    reference="dep-2",
                    ts=datetime.combine(DEPOSIT_2[0], datetime.min.time(), tzinfo=UTC),
                    transaction_type="DEPOSIT",
                    currency_code="EUR",
                    amount=DEPOSIT_2[1],
                )
            )
            session.add(
                Transaction(
                    reference="wd-1",
                    ts=datetime.combine(WITHDRAWAL[0], datetime.min.time(), tzinfo=UTC),
                    transaction_type="WITHDRAWAL",
                    currency_code="EUR",
                    # Withdrawals arrive as a negative amount on Trading 212's own ledger; the
                    # replay adds a transaction's amount to the external flow as-is (see
                    # _build_daily_replay in helios/performance.py), never sign-flipping it.
                    amount=-WITHDRAWAL[1],
                )
            )
            session.add(
                OrderHistory(
                    fill_id="buy-usd-1",
                    fill_timestamp=datetime.combine(BUY_USD[0], datetime.min.time(), tzinfo=UTC),
                    t212_ticker="USD_TECH_EQ",
                    side="BUY",
                    fill_type="TRADE",
                    filled_quantity=BUY_USD[1],
                    wallet_currency="EUR",
                    wallet_net_value=BUY_USD[2],
                )
            )
            session.add(
                OrderHistory(
                    fill_id="buy-gbp-1",
                    fill_timestamp=datetime.combine(BUY_GBP[0], datetime.min.time(), tzinfo=UTC),
                    t212_ticker="GBP_INDU_EQ",
                    side="BUY",
                    fill_type="TRADE",
                    filled_quantity=BUY_GBP[1],
                    wallet_currency="EUR",
                    wallet_net_value=BUY_GBP[2],
                )
            )
            session.add(
                OrderHistory(
                    fill_id="sell-usd-1",
                    fill_timestamp=datetime.combine(SELL_USD[0], datetime.min.time(), tzinfo=UTC),
                    t212_ticker="USD_TECH_EQ",
                    side="SELL",
                    fill_type="TRADE",
                    filled_quantity=SELL_USD[1],
                    wallet_currency="EUR",
                    wallet_net_value=SELL_USD[2],
                )
            )
            session.add(
                Dividend(
                    reference="div-1",
                    paid_on=datetime.combine(DIVIDEND[0], datetime.min.time(), tzinfo=UTC),
                    t212_ticker="USD_TECH_EQ",
                    currency_code="USD",
                    amount=DIVIDEND[1],
                    # No amount_in_euro on purpose: this exercises the FX-conversion path for
                    # dividend cash rather than the pass-through path already covered by
                    # test_replay_does_not_double_count_dividends in test_performance.py.
                    amount_in_euro=None,
                )
            )

    market_data = FakeMarketDataProvider(
        {
            "USD_TECH_EQ": _seed_prices("USD_TECH_EQ", "USD", USD_TECH_PRICES),
            "GBP_INDU_EQ": _seed_prices("GBP_INDU_EQ", "GBP", GBP_INDU_PRICES),
        }
    )
    fx_provider = FakeFxRateProvider(
        {
            "USD": _seed_fx("USD", USD_EUR_RATE, USD_TECH_PRICES),
            "GBP": _seed_fx("GBP", GBP_EUR_RATE, GBP_INDU_PRICES),
        }
    )
    service = PerformanceReplayService(
        repository,
        Settings(data_dir=tmp_path),
        market_data,
        fx_provider,
        clock=FixedClock(datetime.combine(END_DATE, datetime.min.time(), tzinfo=UTC)),
    )

    summary = await service.replay(as_of=END_DATE)

    # Nothing was fabricated or silently dropped: every input needed was actually available.
    assert summary.start_date == START_DATE
    assert summary.end_date == END_DATE
    assert summary.missing_price_symbols == []
    assert summary.stale_price_symbols == []
    assert summary.missing_fx_currencies == []
    assert summary.stale_fx_currencies == []
    assert summary.excluded_flow_currencies == []
    assert summary.nav_written == PINNED_NAV_ROWS_WRITTEN
    assert summary.holdings_written == PINNED_HOLDINGS_ROWS_WRITTEN

    report = await service.get_report()

    assert report.flow_timing == "flow_at_close"
    assert report.annualization_days == 365
    assert report.start_date == START_DATE
    assert report.end_date == END_DATE
    assert len(report.nav_series) == PINNED_NAV_ROWS_WRITTEN

    nav_by_date = {point.as_of_date: point for point in report.nav_series}
    for checkpoint_date, expected_nav in PINNED_NAV.items():
        actual = nav_by_date[checkpoint_date].nav_eur
        assert actual == expected_nav, (
            f"NAV on {checkpoint_date} was {actual}, expected the frozen {expected_nav}"
        )

    assert report.cumulative_twr.status == "ok"
    assert report.cumulative_twr.value == pytest.approx(PINNED_CUMULATIVE_TWR, abs=FLOAT_TOLERANCE)

    assert report.xirr.status == "ok"
    assert report.xirr.value == pytest.approx(PINNED_XIRR, abs=FLOAT_TOLERANCE)

    assert report.annualized_return.status == "ok"
    assert report.annualized_return.value == pytest.approx(
        PINNED_ANNUALIZED_RETURN, abs=FLOAT_TOLERANCE
    )

    assert report.volatility.status == "ok"
    assert report.volatility.value == pytest.approx(PINNED_VOLATILITY, abs=FLOAT_TOLERANCE)

    assert report.downside_volatility.status == "ok"
    assert report.downside_volatility.value == pytest.approx(
        PINNED_DOWNSIDE_VOLATILITY, abs=FLOAT_TOLERANCE
    )

    assert report.sharpe.status == "ok"
    assert report.sharpe.value == pytest.approx(PINNED_SHARPE, abs=FLOAT_TOLERANCE)

    assert report.sortino.status == "ok"
    assert report.sortino.value == pytest.approx(PINNED_SORTINO, abs=FLOAT_TOLERANCE)

    assert report.calmar.status == "ok"
    assert report.calmar.value == pytest.approx(PINNED_CALMAR, abs=FLOAT_TOLERANCE)

    assert report.max_drawdown.status == "ok"
    assert report.max_drawdown.value == pytest.approx(PINNED_MAX_DRAWDOWN, abs=FLOAT_TOLERANCE)

    assert report.time_underwater_days.status == "ok"
    assert report.time_underwater_days.value == pytest.approx(
        PINNED_TIME_UNDERWATER_DAYS, abs=FLOAT_TOLERANCE
    )

    # The worst drawdown on the time-weighted index (2024-01-21 peak, 2024-02-05 trough) was
    # recovered on 2024-02-26: 21 days, verified independently from _reference_nav_series().
    # The old "not yet recovered" came from the 2024-03-20 withdrawal, which is not a loss.
    assert report.recovery_days.status == "ok"
    assert report.recovery_days.value == pytest.approx(21.0, abs=FLOAT_TOLERANCE)

    # No benchmark price data was seeded, so every benchmark-relative and factor-model figure must
    # say so explicitly rather than inventing a beta/alpha/regression against nothing.
    assert all(item.beta.status == "insufficient_data" for item in report.beta_vs_benchmarks)
    assert report.passive_counterfactual.status == "unavailable"
    assert report.ff5_momentum_regression.status == "unavailable"


async def test_a_base_currency_holding_is_valued_without_fx(tmp_path: Path) -> None:
    """A EUR-priced holding converts at exactly 1 and never needs an FX series.

    Regression for the bug described in the module docstring: every Euronext or Xetra position
    used to read as MISSING_FX, so NAV went PARTIAL on the day it was bought and never recovered.
    Hand-worked: EUR 1,000 deposited, EUR 500 spent on 10 shares at EUR 50, so NAV is
    500 cash + 10 x price -- 1,000 at EUR 50 and 1,100 once the price reaches EUR 60.
    """
    start = date(2024, 1, 1)
    repository, session_factory = await _repository_and_session_factory(
        tmp_path, "base_currency.sqlite3"
    )
    async with session_factory() as session:
        async with session.begin():
            session.add(
                Instrument(
                    t212_ticker="EUR_XETRA_EQ",
                    yahoo_ticker="EXQ",
                    currency_code="EUR",
                    mapping_status="resolved",
                )
            )
            session.add(
                Transaction(
                    reference="dep-eur",
                    ts=datetime.combine(start, datetime.min.time(), tzinfo=UTC),
                    transaction_type="DEPOSIT",
                    currency_code="EUR",
                    amount=D("1000.00"),
                )
            )
            session.add(
                OrderHistory(
                    fill_id="buy-eur-1",
                    fill_timestamp=datetime.combine(start, datetime.min.time(), tzinfo=UTC),
                    t212_ticker="EUR_XETRA_EQ",
                    side="BUY",
                    fill_type="TRADE",
                    filled_quantity=D("10"),
                    wallet_currency="EUR",
                    wallet_net_value=D("500.00"),
                )
            )

    prices = {start: D("50.00"), start + timedelta(days=2): D("60.00")}
    end = start + timedelta(days=3)
    service = PerformanceReplayService(
        repository,
        Settings(data_dir=tmp_path),
        FakeMarketDataProvider({"EUR_XETRA_EQ": _seed_prices("EUR_XETRA_EQ", "EUR", prices)}),
        # Deliberately empty: a EUR holding must not need one.
        FakeFxRateProvider({}),
        clock=FixedClock(datetime.combine(end, datetime.min.time(), tzinfo=UTC)),
    )

    summary = await service.replay(as_of=end)
    report = await service.get_report()

    assert summary.missing_fx_currencies == []
    nav = {point.as_of_date: point.nav_eur for point in report.nav_series}
    assert nav == {
        start: D("1000.00"),
        start + timedelta(days=1): D("1000.00"),  # forward-filled close, still fully valued
        start + timedelta(days=2): D("1100.00"),
        start + timedelta(days=3): D("1100.00"),
    }


async def test_a_negative_sell_quantity_reduces_the_holding(tmp_path: Path) -> None:
    """Trading 212 sends SELL quantities as negative numbers; a sale must still reduce units.

    Hand-worked: EUR 1,000 in, 10 shares bought at EUR 50 (EUR 500), then 4 sold at EUR 60
    (EUR 240 back, reported as quantity -4). Left: 6 shares at EUR 60 = EUR 360, plus
    EUR 740 cash = EUR 1,100. Counting the sale as a purchase would have shown 14 shares and a
    NAV of EUR 1,580.
    """
    start = date(2024, 1, 1)
    sell_day = start + timedelta(days=2)
    repository, session_factory = await _repository_and_session_factory(
        tmp_path, "negative_sell.sqlite3"
    )
    async with session_factory() as session:
        async with session.begin():
            session.add(
                Instrument(
                    t212_ticker="EUR_XETRA_EQ",
                    yahoo_ticker="EXQ",
                    currency_code="EUR",
                    mapping_status="resolved",
                )
            )
            session.add(
                Transaction(
                    reference="dep-eur",
                    ts=datetime.combine(start, datetime.min.time(), tzinfo=UTC),
                    transaction_type="DEPOSIT",
                    currency_code="EUR",
                    amount=D("1000.00"),
                )
            )
            for fill_id, day, side, quantity, value in (
                ("buy-1", start, "BUY", D("10"), D("500.00")),
                ("sell-1", sell_day, "SELL", D("-4"), D("240.00")),
            ):
                session.add(
                    OrderHistory(
                        fill_id=fill_id,
                        fill_timestamp=datetime.combine(day, datetime.min.time(), tzinfo=UTC),
                        t212_ticker="EUR_XETRA_EQ",
                        side=side,
                        fill_type="TRADE",
                        filled_quantity=quantity,
                        wallet_currency="EUR",
                        wallet_net_value=value,
                    )
                )

    prices = {start: D("50.00"), sell_day: D("60.00")}
    service = PerformanceReplayService(
        repository,
        Settings(data_dir=tmp_path),
        FakeMarketDataProvider({"EUR_XETRA_EQ": _seed_prices("EUR_XETRA_EQ", "EUR", prices)}),
        FakeFxRateProvider({}),
        clock=FixedClock(datetime.combine(sell_day, datetime.min.time(), tzinfo=UTC)),
    )

    await service.replay(as_of=sell_day)
    report = await service.get_report()

    nav = {point.as_of_date: point for point in report.nav_series}
    assert nav[sell_day].nav_eur == D("1100.00")
    assert nav[sell_day].securities_value_eur == D("360.00")
    assert nav[sell_day].cash_balance_eur == D("740.00")

    # Each holding's own cash is recorded, so the period result can be split by stock: bought
    # for 500, sold 4 for 240, 6 left worth 360 -> the holding made 100, all of the result.
    flows = await repository.list_daily_holding_flows()
    assert [(flow.as_of_date, flow.bought_eur, flow.sold_eur) for flow in flows] == [
        (start, D("500.00"), D("0")),
        (sell_day, D("0"), D("240.00")),
    ]
    since_start = next(item for item in report.period_summaries if item.key == "ALL")
    [holding] = since_start.holdings
    assert holding.ticker == "EUR_XETRA_EQ"
    assert holding.result_eur == D("100.00")
    assert since_start.investment_result_eur == D("100.00")
    assert since_start.unattributed_eur == D("0")


class WindowedFxProvider:
    """Serves only fixes inside the requested window, like the real ECB API does.

    The shared FakeFxRateProvider ignores the window, which is why a weekend-start bug in the
    replay's FX window was invisible to every other test.
    """

    def __init__(self, points: list[FxRatePoint]) -> None:
        self.points = points

    async def fetch_eur_base_rates(
        self, *, currencies: set[str], start_date: date, end_date: date
    ) -> dict[str, list[FxRatePoint]]:
        return {
            currency: [
                point
                for point in self.points
                if point.currency_code == currency and start_date <= point.as_of_date <= end_date
            ]
            for currency in currencies
        }


async def test_a_weekend_first_deposit_in_a_foreign_currency_is_converted(tmp_path: Path) -> None:
    """Found on a real account: the first GBP deposit fell on Saturday 3 January.

    ECB publishes on business days, and the replay's FX window opened on that Saturday, so there
    was no earlier fix to carry forward and the deposit was excluded. Friday's fix (1.15 EUR per
    GBP) must be carried into the weekend: GBP 100 deposited = EUR 115.
    """
    saturday = date(2026, 1, 3)
    friday = date(2026, 1, 2)
    repository, session_factory = await _repository_and_session_factory(
        tmp_path, "weekend_fx.sqlite3"
    )
    async with session_factory() as session:
        async with session.begin():
            session.add(
                Transaction(
                    reference="dep-gbp",
                    ts=datetime.combine(saturday, datetime.min.time(), tzinfo=UTC),
                    transaction_type="DEPOSIT",
                    currency_code="GBP",
                    amount=D("100.00"),
                )
            )

    fx = WindowedFxProvider([FxRatePoint(friday, "GBP", D("1.15"), "ecb", friday, "EXACT", False)])
    service = PerformanceReplayService(
        repository,
        Settings(data_dir=tmp_path),
        FakeMarketDataProvider({}),
        fx,
        clock=FixedClock(datetime.combine(saturday, datetime.min.time(), tzinfo=UTC)),
    )

    summary = await service.replay(as_of=saturday)
    report = await service.get_report()

    assert summary.excluded_flow_currencies == []
    assert {point.as_of_date: point.nav_eur for point in report.nav_series}[saturday] == D(
        "115.0000"
    )


async def test_fees_and_interest_move_cash_but_are_not_external_flows(tmp_path: Path) -> None:
    """Trading 212 lists FEE and INTEREST_ON_FREE_CASH beside deposits; they used to be ignored.

    Hand-worked: EUR 1,000 deposited on day 1; a EUR 2.00 fee and EUR 0.50 of interest on day 2.
    Cash is 998.50. The deposit is the only external flow, so day 2's return is the fee net of
    interest: -1.50 / 1,000 = -0.15%.
    """
    day1 = date(2024, 1, 1)
    day2 = day1 + timedelta(days=1)
    repository, session_factory = await _repository_and_session_factory(
        tmp_path, "fees_interest.sqlite3"
    )
    async with session_factory() as session:
        async with session.begin():
            for reference, day, kind, amount in (
                ("dep", day1, "DEPOSIT", D("1000.00")),
                ("fee", day2, "FEE", D("-2.00")),
                ("int", day2, "INTEREST_ON_FREE_CASH", D("0.50")),
            ):
                session.add(
                    Transaction(
                        reference=reference,
                        ts=datetime.combine(day, datetime.min.time(), tzinfo=UTC),
                        transaction_type=kind,
                        currency_code="EUR",
                        amount=amount,
                    )
                )

    service = PerformanceReplayService(
        repository,
        Settings(data_dir=tmp_path),
        FakeMarketDataProvider({}),
        FakeFxRateProvider({}),
        clock=FixedClock(datetime.combine(day2, datetime.min.time(), tzinfo=UTC)),
    )

    await service.replay(as_of=day2)
    report = await service.get_report()

    nav = {point.as_of_date: point for point in report.nav_series}
    assert nav[day2].cash_balance_eur == D("998.50")
    assert nav[day2].external_flow_eur == D("0")
    assert report.cumulative_twr.value == pytest.approx(-0.0015, abs=FLOAT_TOLERANCE)
