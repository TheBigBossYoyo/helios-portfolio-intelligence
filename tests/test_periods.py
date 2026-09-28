"""Money moved vs money made, per period -- checked against a hand-worked week.

The week (Mon 2024-01-01 .. Fri 2024-01-05):

    Mon  deposit 1,000                          NAV 1,000
    Tue  market +20                             NAV 1,020
    Wed  deposit 500, market +10                NAV 1,530
    Thu  dividend 5 (cash), market +5           NAV 1,540
    Fri  fee -2, withdrawal -100, market -5     NAV 1,433

So since inception: 1,500 in, 100 out (net 1,400), value 1,433, investment result +33 =
market +30, dividends +5, fees -2.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest

from helios.periods import (
    PeriodSummary,
    compute_monthly_summaries,
    compute_period_summaries,
    cumulative_net_deposits,
    summarise_window,
    with_holding_movements,
)

D = Decimal
NIL = Decimal("0")
ONE = Decimal("1")


@dataclass(frozen=True)
class Row:
    as_of_date: date
    nav_eur: Decimal | None
    external_flow_eur: Decimal = NIL
    dividend_eur: Decimal | None = NIL
    interest_eur: Decimal | None = NIL
    fee_eur: Decimal | None = NIL
    deposit_eur: Decimal | None = None
    withdrawal_eur: Decimal | None = None
    card_spending_eur: Decimal | None = NIL
    cashback_eur: Decimal | None = NIL


WEEK = [
    Row(date(2024, 1, 1), D("1000"), external_flow_eur=D("1000")),
    Row(date(2024, 1, 2), D("1020")),
    Row(date(2024, 1, 3), D("1530"), external_flow_eur=D("500")),
    Row(date(2024, 1, 4), D("1540"), dividend_eur=D("5")),
    Row(date(2024, 1, 5), D("1433"), external_flow_eur=D("-100"), fee_eur=D("-2")),
]
# flow_at_close daily TWR, by hand: (NAV_t - F_t) / NAV_t-1 - 1
TWR = {
    date(2024, 1, 2): 1020 / 1000 - 1,
    date(2024, 1, 3): (1530 - 500) / 1020 - 1,
    date(2024, 1, 4): 1540 / 1530 - 1,
    date(2024, 1, 5): (1433 + 100) / 1540 - 1,
}


def _by_key(rows: list[Row] = WEEK) -> dict[str, PeriodSummary]:
    return {summary.key: summary for summary in compute_period_summaries(rows, TWR)}


def test_since_inception_splits_money_moved_from_money_made() -> None:
    summary = _by_key()["ALL"]

    assert summary.start_value_eur == D("0")
    assert summary.end_value_eur == D("1433")
    assert summary.deposits_eur == D("1500")
    assert summary.withdrawals_eur == D("-100")
    assert summary.net_deposits_eur == D("1400")
    assert summary.investment_result_eur == D("33")
    assert summary.dividends_eur == D("5")
    assert summary.fees_eur == D("-2")
    assert summary.market_eur == D("30")


def test_the_parts_always_add_up_to_the_change_in_value() -> None:
    for summary in compute_period_summaries(WEEK, TWR):
        if summary.status != "ok":
            continue
        assert summary.value_change_eur == summary.net_deposits_eur + (
            summary.investment_result_eur or D("0")
        )
        assert summary.investment_result_eur == (
            (summary.market_eur or D("0"))
            + summary.dividends_eur
            + summary.interest_eur
            + summary.fees_eur
        )


def test_last_trading_day_compares_the_last_two_weekdays() -> None:
    """Friday vs Thursday: value -107, of which only -7 is investment; the rest was withdrawn."""
    summary = _by_key()["1D"]

    assert (summary.start_date, summary.end_date) == (date(2024, 1, 4), date(2024, 1, 5))
    assert summary.value_change_eur == D("-107")
    assert summary.net_deposits_eur == D("-100")
    assert summary.investment_result_eur == D("-7")
    assert summary.twr == pytest.approx((1433 + 100) / 1540 - 1)


def test_a_weekend_end_still_compares_real_trading_days() -> None:
    """On Saturday the replay carries Friday's close; "last day" must not read as flat."""
    rows = [*WEEK, Row(date(2024, 1, 6), D("1433"))]

    summary = {s.key: s for s in compute_period_summaries(rows, TWR)}["1D"]

    assert summary.end_date == date(2024, 1, 5)
    assert summary.value_change_eur == D("-107")


def test_money_moved_on_an_unvalued_day_still_counts_and_is_flagged() -> None:
    rows = [
        Row(date(2024, 1, 1), D("1000"), external_flow_eur=D("1000")),
        Row(date(2024, 1, 2), None, external_flow_eur=D("500")),  # no prices that day
        Row(date(2024, 1, 3), D("1560")),
    ]

    summary = {s.key: s for s in compute_period_summaries(rows, {})}["ALL"]

    assert summary.net_deposits_eur == D("1500")
    assert summary.investment_result_eur == D("60")
    assert summary.unvalued_days == 1
    assert summary.detail is not None and "could not be valued" in summary.detail


def test_months_run_from_the_previous_month_close() -> None:
    rows = [
        Row(date(2024, 1, 30), D("1000"), external_flow_eur=D("1000")),
        Row(date(2024, 1, 31), D("1010")),
        Row(date(2024, 2, 1), D("1310"), external_flow_eur=D("300")),
        Row(date(2024, 2, 29), D("1330")),
    ]

    months = compute_monthly_summaries(rows, {})

    assert [month.key for month in months] == ["2024-01", "2024-02"]
    january, february = months
    assert (january.net_deposits_eur, january.investment_result_eur) == (D("1000"), D("10"))
    assert february.start_value_eur == D("1010")
    assert (february.net_deposits_eur, february.investment_result_eur) == (D("300"), D("20"))


def test_net_deposits_to_date_is_a_running_total() -> None:
    running = cumulative_net_deposits(WEEK)

    assert running[date(2024, 1, 3)] == D("1500")
    assert running[date(2024, 1, 5)] == D("1400")


def test_nothing_valued_yet_reports_insufficient_data_for_every_period() -> None:
    summaries = compute_period_summaries([Row(date(2024, 1, 1), None)], {})

    assert {summary.status for summary in summaries} == {"insufficient_data"}


# ---------------------------------------------------------------------------
# By holding. A second hand-worked week, with trades:
#
#   Mon  deposit 1,000; buy 10 AAA @ 50 (500)          AAA 500              NAV 1,000
#   Tue  AAA 55                                         AAA 550              NAV 1,050
#   Wed  buy 4 BBB @ 25 (100); AAA 52                   AAA 520, BBB 100     NAV 1,020
#   Thu  sell all AAA @ 54 (540); BBB 30; BBB pays 2    BBB 120              NAV 1,062
#   Fri  BBB 28; interest 1                             BBB 112              NAV 1,055
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Holding:
    as_of_date: date
    t212_ticker: str
    quantity: Decimal
    close_price: Decimal | None
    fx_rate_to_eur: Decimal | None = ONE

    @property
    def market_value_eur(self) -> Decimal | None:
        if self.close_price is None or self.fx_rate_to_eur is None:
            return None
        return self.quantity * self.close_price * self.fx_rate_to_eur


@dataclass(frozen=True)
class Flow:
    as_of_date: date
    t212_ticker: str
    bought_eur: Decimal = NIL
    sold_eur: Decimal = NIL
    dividend_eur: Decimal = NIL


TRADES_NAV = [
    Row(date(2024, 1, 1), D("1000"), external_flow_eur=D("1000")),
    Row(date(2024, 1, 2), D("1050")),
    Row(date(2024, 1, 3), D("1020")),
    Row(date(2024, 1, 4), D("1062"), dividend_eur=D("2")),
    Row(date(2024, 1, 5), D("1055"), interest_eur=D("1")),
]
TRADES_HOLDINGS = [
    Holding(date(2024, 1, 1), "AAA", D("10"), D("50")),
    Holding(date(2024, 1, 2), "AAA", D("10"), D("55")),
    Holding(date(2024, 1, 3), "AAA", D("10"), D("52")),
    Holding(date(2024, 1, 3), "BBB", D("4"), D("25")),
    Holding(date(2024, 1, 4), "BBB", D("4"), D("30")),
    Holding(date(2024, 1, 5), "BBB", D("4"), D("28")),
]
TRADES_FLOWS = [
    Flow(date(2024, 1, 1), "AAA", bought_eur=D("500")),
    Flow(date(2024, 1, 3), "BBB", bought_eur=D("100")),
    Flow(date(2024, 1, 4), "AAA", sold_eur=D("540")),
    Flow(date(2024, 1, 4), "BBB", dividend_eur=D("2")),
]


def _window(start: int | None, end: int) -> PeriodSummary:
    by_day = {row.as_of_date.day: row for row in TRADES_NAV}
    summary = summarise_window(
        TRADES_NAV,
        {},
        key="X",
        label="X",
        start=by_day[start] if start is not None else None,
        end=by_day[end],
    )
    [attached] = with_holding_movements(
        [summary], TRADES_HOLDINGS, TRADES_FLOWS, {"AAA": "Alpha Inc.", "BBB": None}
    )
    return attached


def test_a_purchase_and_a_sale_are_not_gains_or_losses() -> None:
    period = _window(2, 5)
    by_ticker = {item.ticker: item for item in period.holdings}

    # AAA: worth 550 at the start, sold for 540 -> lost 10 while held.
    aaa = by_ticker["AAA"]
    assert (aaa.start_value_eur, aaa.end_value_eur, aaa.sold_eur) == (D("550"), NIL, D("540"))
    assert aaa.result_eur == D("-10")
    assert aaa.return_pct == pytest.approx(-10 / 550)
    assert aaa.name == "Alpha Inc."
    # BBB: bought for 100, worth 112 now, paid 2 -> made 14, not 112.
    bbb = by_ticker["BBB"]
    assert (bbb.bought_eur, bbb.dividends_eur, bbb.result_eur) == (D("100"), D("2"), D("14"))
    assert bbb.return_pct == pytest.approx(0.14)
    # Neither was held at both ends, so there is no like-for-like price move.
    assert aaa.price_change_pct is None
    assert bbb.price_change_pct is None


def test_holdings_plus_unattributed_reconcile_to_the_investment_result() -> None:
    for start in (None, 1, 2, 3, 4):
        period = _window(start, 5)
        total = sum((item.result_eur or NIL for item in period.holdings), NIL)
        assert period.unattributed_eur is not None
        assert total + period.unattributed_eur == period.investment_result_eur
        # The only cash no holding explains in this week is Friday's interest.
        assert period.unattributed_eur == D("1")


def test_largest_move_comes_first_and_the_price_move_is_reported_when_held_throughout() -> None:
    period = _window(3, 5)

    assert [item.ticker for item in period.holdings] == ["AAA", "BBB"]
    bbb = period.holdings[1]
    assert bbb.price_change_pct == pytest.approx(28 / 25 - 1)


def test_an_unpriced_holding_makes_the_split_incomplete_not_wrong() -> None:
    holdings = [*TRADES_HOLDINGS[:-1], Holding(date(2024, 1, 5), "BBB", D("4"), None)]
    summary = summarise_window(TRADES_NAV, {}, key="X", label="X", start=None, end=TRADES_NAV[-1])

    [period] = with_holding_movements([summary], holdings, TRADES_FLOWS)

    bbb = next(item for item in period.holdings if item.ticker == "BBB")
    assert bbb.status == "insufficient_data"
    assert bbb.result_eur is None
    assert period.holdings[-1].ticker == "BBB"
    assert period.unattributed_eur is None


def test_no_split_before_the_replay_has_recorded_any_flow() -> None:
    summary = summarise_window(TRADES_NAV, {}, key="X", label="X", start=None, end=TRADES_NAV[-1])

    [period] = with_holding_movements([summary], TRADES_HOLDINGS, [])

    assert period.holdings == []
    assert period.unattributed_eur is None
