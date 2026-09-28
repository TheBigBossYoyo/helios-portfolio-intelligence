"""What happened to the portfolio over a period, with money moved separated from money made.

A portfolio's value changes for two unrelated reasons: the owner moves money in or out, and the
investments earn or lose. Showing only the change in value mixes them -- a EUR 500 deposit reads
as a EUR 500 "gain" -- which is exactly the confusion this module exists to remove. Every period
summary splits the change into:

* **Money moved** -- deposits and withdrawals (external flows), with card spending shown as
  its own part of the withdrawals once a Trading 212 export has labelled it. Not performance.
* **Investment result** -- everything else, itself split into market movement (prices and FX),
  dividends, interest, card cashback and fees.

The identity always holds: ``value change = net deposits + investment result``, and
``investment result = market + dividends + interest + cashback + fees``. Market movement is
the residual, so the parts always add up to the whole.

The investment result is also split **by holding**: for each stock, what it is worth at the end
minus what it was worth at the start, minus what was spent buying more, plus what selling
returned, plus its dividends. A stock bought mid-period therefore shows only what it did after
the purchase, and one sold shows what it did until the sale -- the purchase and the sale
themselves are neither gains nor losses. What no holding explains (interest, fees, a dividend
without a ticker) is reported as such, so the per-stock rows and the headline reconcile.

Periods start from a *closing* value: the last valued day on or before the period's nominal
start. A period that begins before the first deposit starts from zero ("since inception"), so
its investment result is simply value minus everything ever put in. Days Helios could not value
are never estimated: flows on those days still count (the money really moved), the return links
the valued days either side, and the summary says how many days were unvalued.
"""

from __future__ import annotations

import calendar
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import Decimal
from typing import Final, Protocol

ZERO = Decimal("0")

#: (key, label) in display order. Keys are stable identifiers for the API and URL.
PERIODS: Final[tuple[tuple[str, str], ...]] = (
    ("1D", "Last trading day"),
    ("1W", "1 week"),
    ("1M", "1 month"),
    ("3M", "3 months"),
    ("YTD", "Year to date"),
    ("1Y", "1 year"),
    ("ALL", "Since you started"),
)


class NavRow(Protocol):
    """The replayed daily row, as far as this module needs it (read-only)."""

    @property
    def as_of_date(self) -> date: ...

    @property
    def nav_eur(self) -> Decimal | None: ...

    @property
    def external_flow_eur(self) -> Decimal: ...

    @property
    def dividend_eur(self) -> Decimal | None: ...

    @property
    def interest_eur(self) -> Decimal | None: ...

    @property
    def fee_eur(self) -> Decimal | None: ...

    @property
    def deposit_eur(self) -> Decimal | None: ...

    @property
    def withdrawal_eur(self) -> Decimal | None: ...

    @property
    def card_spending_eur(self) -> Decimal | None: ...

    @property
    def cashback_eur(self) -> Decimal | None: ...


class HoldingRow(Protocol):
    """One holding on one replayed day (read-only)."""

    @property
    def as_of_date(self) -> date: ...

    @property
    def t212_ticker(self) -> str: ...

    @property
    def quantity(self) -> Decimal: ...

    @property
    def close_price(self) -> Decimal | None: ...

    @property
    def fx_rate_to_eur(self) -> Decimal | None: ...

    @property
    def market_value_eur(self) -> Decimal | None: ...


class HoldingFlowRow(Protocol):
    """Cash in and out of one holding on one day (read-only, positive magnitudes)."""

    @property
    def as_of_date(self) -> date: ...

    @property
    def t212_ticker(self) -> str: ...

    @property
    def bought_eur(self) -> Decimal: ...

    @property
    def sold_eur(self) -> Decimal: ...

    @property
    def dividend_eur(self) -> Decimal: ...


@dataclass(frozen=True)
class HoldingMovement:
    """What one holding did over a period."""

    ticker: str
    status: str
    #: Zero when the holding was not owned at that end of the period.
    start_value_eur: Decimal | None
    end_value_eur: Decimal | None
    start_quantity: Decimal
    end_quantity: Decimal
    bought_eur: Decimal
    sold_eur: Decimal
    dividends_eur: Decimal
    #: end - start - bought + sold + dividends: the holding's share of the investment result.
    result_eur: Decimal | None
    #: result / (start value + bought): the result relative to the money that was at work.
    return_pct: float | None
    #: The EUR price move (close x FX) between the two ends, when held at both.
    price_change_pct: float | None
    detail: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class PeriodSummary:
    key: str
    label: str
    status: str
    #: The closing value the period starts from; None when it starts at inception (value 0).
    start_date: date | None
    end_date: date | None
    start_value_eur: Decimal | None
    end_value_eur: Decimal | None
    value_change_eur: Decimal | None
    deposits_eur: Decimal
    #: Negative (money out).
    withdrawals_eur: Decimal
    net_deposits_eur: Decimal
    investment_result_eur: Decimal | None
    market_eur: Decimal | None
    dividends_eur: Decimal
    interest_eur: Decimal
    #: Negative (a cost).
    fees_eur: Decimal
    #: The card-payment part of ``withdrawals_eur`` (negative); the rest went to a bank.
    card_spending_eur: Decimal
    #: Card cashback: part of the investment result, not money added.
    cashback_eur: Decimal
    #: Time-weighted return: what the investments did, independent of when money moved.
    twr: float | None
    unvalued_days: int
    detail: str | None
    #: The investment result split by holding, largest move first.
    holdings: list[HoldingMovement] = field(default_factory=list)
    #: Investment result no holding accounts for: interest, fees, unattributed dividends.
    unattributed_eur: Decimal | None = None


def _money(value: Decimal | None) -> Decimal:
    return value if value is not None else ZERO


def _money_in(row: NavRow) -> Decimal:
    if row.deposit_eur is not None and row.withdrawal_eur is not None:
        return row.deposit_eur
    return max(row.external_flow_eur, ZERO)


def _money_out(row: NavRow) -> Decimal:
    if row.deposit_eur is not None and row.withdrawal_eur is not None:
        return row.withdrawal_eur
    return min(row.external_flow_eur, ZERO)


def months_back(day: date, months: int) -> date:
    """The same day ``months`` earlier, clamped to that month's length (31 Mar -> 28 Feb)."""
    return _months_back(day, months)


def _months_back(day: date, months: int) -> date:
    month_index = day.year * 12 + (day.month - 1) - months
    year, month = divmod(month_index, 12)
    last_day = calendar.monthrange(year, month + 1)[1]
    return date(year, month + 1, min(day.day, last_day))


def _nominal_start(key: str, end: date) -> date | None:
    """The date whose *closing* value a period starts from. None means inception."""

    if key == "1W":
        return end - timedelta(days=7)
    if key == "1M":
        return _months_back(end, 1)
    if key == "3M":
        return _months_back(end, 3)
    if key == "YTD":
        return date(end.year, 1, 1) - timedelta(days=1)
    if key == "1Y":
        return _months_back(end, 12)
    return None


def summarise_window(
    rows: Sequence[NavRow],
    twr_by_date: Mapping[date, float],
    *,
    key: str,
    label: str,
    start: NavRow | None,
    end: NavRow,
) -> PeriodSummary:
    """Summarise everything after ``start`` (exclusive; inception if None) up to ``end``."""

    lower = start.as_of_date if start is not None else date.min
    window = [row for row in rows if lower < row.as_of_date <= end.as_of_date]
    # Gross money in and out per day when the replay recorded them; a day's net otherwise.
    deposits = sum((_money_in(row) for row in window), ZERO)
    withdrawals = sum((_money_out(row) for row in window), ZERO)
    dividends = sum((_money(row.dividend_eur) for row in window), ZERO)
    interest = sum((_money(row.interest_eur) for row in window), ZERO)
    fees = sum((_money(row.fee_eur) for row in window), ZERO)
    card = sum((_money(row.card_spending_eur) for row in window), ZERO)
    cashback = sum((_money(row.cashback_eur) for row in window), ZERO)
    net_deposits = deposits + withdrawals

    start_value = start.nav_eur if start is not None else ZERO
    end_value = end.nav_eur
    value_change = investment = market = None
    if start_value is not None and end_value is not None:
        value_change = end_value - start_value
        investment = value_change - net_deposits
        market = investment - dividends - interest - cashback - fees

    returns = [twr_by_date[row.as_of_date] for row in window if row.as_of_date in twr_by_date]
    twr = math.prod(1.0 + value for value in returns) - 1.0 if returns else None
    unvalued = sum(1 for row in window if row.nav_eur is None)
    detail = None
    if unvalued:
        detail = (
            f"{unvalued} day(s) in this period could not be valued (missing prices). Money moved "
            "on those days is still counted; the return links the valued days either side and "
            "excludes them rather than estimating."
        )
    return PeriodSummary(
        key=key,
        label=label,
        status="ok" if value_change is not None else "insufficient_data",
        start_date=start.as_of_date if start is not None else None,
        end_date=end.as_of_date,
        start_value_eur=start_value,
        end_value_eur=end_value,
        value_change_eur=value_change,
        deposits_eur=deposits,
        withdrawals_eur=withdrawals,
        net_deposits_eur=net_deposits,
        investment_result_eur=investment,
        market_eur=market,
        dividends_eur=dividends,
        interest_eur=interest,
        fees_eur=fees,
        card_spending_eur=card,
        cashback_eur=cashback,
        twr=twr,
        unvalued_days=unvalued,
        detail=detail,
    )


def _empty(key: str, label: str, detail: str) -> PeriodSummary:
    return PeriodSummary(
        key=key,
        label=label,
        status="insufficient_data",
        start_date=None,
        end_date=None,
        start_value_eur=None,
        end_value_eur=None,
        value_change_eur=None,
        deposits_eur=ZERO,
        withdrawals_eur=ZERO,
        net_deposits_eur=ZERO,
        investment_result_eur=None,
        market_eur=None,
        dividends_eur=ZERO,
        interest_eur=ZERO,
        fees_eur=ZERO,
        card_spending_eur=ZERO,
        cashback_eur=ZERO,
        twr=None,
        unvalued_days=0,
        detail=detail,
    )


def compute_period_summaries(
    rows: Sequence[NavRow], twr_by_date: Mapping[date, float]
) -> list[PeriodSummary]:
    """One summary per entry in :data:`PERIODS`, all ending on the last valued day.

    "Last trading day" ends on the last valued *weekday* and starts from the valued weekday
    before it: on a weekend the replay carries Friday's close, and a Saturday-vs-Friday
    comparison would always read as "nothing happened".
    """

    ordered = sorted(rows, key=lambda row: row.as_of_date)
    valued = [row for row in ordered if row.nav_eur is not None]
    if not valued:
        return [
            _empty(key, label, "No valued day yet: add a price source and replay.")
            for key, label in PERIODS
        ]
    end = valued[-1]
    summaries: list[PeriodSummary] = []
    for key, label in PERIODS:
        if key == "1D":
            weekdays = [row for row in valued if row.as_of_date.weekday() < 5]
            if len(weekdays) < 2:
                summaries.append(_empty(key, label, "Needs two valued trading days."))
                continue
            summaries.append(
                summarise_window(
                    ordered, twr_by_date, key=key, label=label, start=weekdays[-2], end=weekdays[-1]
                )
            )
            continue
        nominal = _nominal_start(key, end.as_of_date)
        start: NavRow | None = None
        if nominal is not None:
            candidates = [row for row in valued if row.as_of_date <= nominal]
            start = candidates[-1] if candidates else None
        summaries.append(
            summarise_window(ordered, twr_by_date, key=key, label=label, start=start, end=end)
        )
    return summaries


def compute_monthly_summaries(
    rows: Sequence[NavRow], twr_by_date: Mapping[date, float]
) -> list[PeriodSummary]:
    """One summary per calendar month, oldest first, each from the previous month's close."""

    ordered = sorted(rows, key=lambda row: row.as_of_date)
    valued = [row for row in ordered if row.nav_eur is not None]
    if not ordered or not valued:
        return []
    first, last = ordered[0].as_of_date, valued[-1].as_of_date
    summaries: list[PeriodSummary] = []
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        month_end = date(year, month, calendar.monthrange(year, month)[1])
        month_start = date(year, month, 1)
        ends = [row for row in valued if row.as_of_date <= month_end]
        starts = [row for row in valued if row.as_of_date < month_start]
        key = f"{year:04d}-{month:02d}"
        if ends and ends[-1].as_of_date >= month_start:
            summaries.append(
                summarise_window(
                    ordered,
                    twr_by_date,
                    key=key,
                    label=f"{calendar.month_abbr[month]} {year}",
                    start=starts[-1] if starts else None,
                    end=ends[-1],
                )
            )
        else:
            summaries.append(
                _empty(key, f"{calendar.month_abbr[month]} {year}", "No valued day this month.")
            )
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return summaries


def cumulative_net_deposits(rows: Sequence[NavRow]) -> dict[date, Decimal]:
    """Running total of money put in (net of withdrawals), per day -- the "money in" line."""

    total = ZERO
    result: dict[date, Decimal] = {}
    for row in sorted(rows, key=lambda item: item.as_of_date):
        total += row.external_flow_eur
        result[row.as_of_date] = total
    return result


def holding_movements(
    holdings: Sequence[HoldingRow],
    flows: Sequence[HoldingFlowRow],
    *,
    start_date: date | None,
    end_date: date,
    names: Mapping[str, str | None] | None = None,
) -> list[HoldingMovement]:
    """Every holding owned at either end of ``(start_date, end_date]`` or traded inside it."""

    lower = start_date if start_date is not None else date.min
    at_start = {
        row.t212_ticker: row
        for row in holdings
        if start_date is not None and row.as_of_date == start_date and row.quantity != 0
    }
    at_end = {
        row.t212_ticker: row for row in holdings if row.as_of_date == end_date and row.quantity != 0
    }
    window_flows = [flow for flow in flows if lower < flow.as_of_date <= end_date]
    tickers = set(at_start) | set(at_end) | {flow.t212_ticker for flow in window_flows}

    movements: list[HoldingMovement] = []
    for ticker in tickers:
        start_row, end_row = at_start.get(ticker), at_end.get(ticker)
        own = [flow for flow in window_flows if flow.t212_ticker == ticker]
        bought = sum((flow.bought_eur for flow in own), ZERO)
        sold = sum((flow.sold_eur for flow in own), ZERO)
        dividends = sum((flow.dividend_eur for flow in own), ZERO)
        start_value = start_row.market_value_eur if start_row is not None else ZERO
        end_value = end_row.market_value_eur if end_row is not None else ZERO
        result: Decimal | None = None
        return_pct: float | None = None
        detail: str | None = None
        if start_value is None or end_value is None:
            detail = "No price for this holding at one end of the period."
        else:
            result = end_value - start_value - bought + sold + dividends
            capital = start_value + bought
            if capital > 0:
                return_pct = float(result / capital)
        movements.append(
            HoldingMovement(
                ticker=ticker,
                status="ok" if result is not None else "insufficient_data",
                start_value_eur=start_value,
                end_value_eur=end_value,
                start_quantity=start_row.quantity if start_row is not None else ZERO,
                end_quantity=end_row.quantity if end_row is not None else ZERO,
                bought_eur=bought,
                sold_eur=sold,
                dividends_eur=dividends,
                result_eur=result,
                return_pct=return_pct,
                price_change_pct=_price_change(start_row, end_row),
                detail=detail,
                name=(names or {}).get(ticker),
            )
        )
    # Biggest move first, either direction; unpriced holdings last.
    movements.sort(
        key=lambda item: (
            item.result_eur is None,
            -abs(item.result_eur) if item.result_eur is not None else ZERO,
            item.ticker,
        )
    )
    return movements


def _price_change(start: HoldingRow | None, end: HoldingRow | None) -> float | None:
    if start is None or end is None:
        return None
    prices = [
        row.close_price * row.fx_rate_to_eur
        for row in (start, end)
        if row.close_price is not None and row.fx_rate_to_eur is not None
    ]
    if len(prices) != 2 or prices[0] == 0:
        return None
    return float(prices[1] / prices[0] - 1)


def with_holding_movements(
    summaries: Sequence[PeriodSummary],
    holdings: Sequence[HoldingRow],
    flows: Sequence[HoldingFlowRow],
    names: Mapping[str, str | None] | None = None,
) -> list[PeriodSummary]:
    """Attach the per-holding split, and what it leaves unexplained, to each summary.

    Holdings with no recorded flows at all means the replay predates flow recording (a database
    upgraded but not yet replayed): every purchase would read as a gain, so no split is given
    until the next replay writes the flows.
    """

    if holdings and not flows:
        return list(summaries)
    result: list[PeriodSummary] = []
    for summary in summaries:
        if summary.status != "ok" or summary.end_date is None:
            result.append(summary)
            continue
        movements = holding_movements(
            holdings, flows, start_date=summary.start_date, end_date=summary.end_date, names=names
        )
        unattributed: Decimal | None = None
        if summary.investment_result_eur is not None and all(
            item.result_eur is not None for item in movements
        ):
            unattributed = summary.investment_result_eur - sum(
                (item.result_eur for item in movements if item.result_eur is not None), ZERO
            )
        result.append(replace(summary, holdings=movements, unattributed_eur=unattributed))
    return result
