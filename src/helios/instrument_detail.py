"""Everything Helios knows about one instrument, for its detail page.

Price history comes from the closes the replay already fetched (``market_prices_daily``), so no
provider request is made to open a page. Holdings, trades, dividends and the per-period result
come from the replay and the ledger -- the same figures the Overview's "stock by stock" split
uses, so the two pages always agree.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from .config import Settings
from .models import (
    DailyHolding,
    DailyHoldingFlow,
    DailyNav,
    FxRateDaily,
    MarketPriceDaily,
    OrderHistory,
)
from .performance import BASE_CURRENCY, _lookup_fx
from .periods import compute_period_summaries, holding_movements, months_back
from .portfolio_repository import PortfolioRepository

ZERO = Decimal("0")

#: Price-return windows shown on the page, in display order.
PRICE_WINDOWS: tuple[tuple[str, str], ...] = (
    ("1W", "1 week"),
    ("1M", "1 month"),
    ("3M", "3 months"),
    ("6M", "6 months"),
    ("YTD", "Year to date"),
    ("1Y", "1 year"),
    ("ALL", "All history"),
)


#: How far after a window's nominal start the first stored close may be and still stand for it.
WINDOW_GRACE_DAYS = 7


class UnknownInstrumentError(LookupError):
    pass


@dataclass(frozen=True)
class PricePoint:
    as_of_date: date
    close: Decimal
    currency: str
    #: The close in EUR at that day's ECB rate; None when no rate was available.
    close_eur: Decimal | None


@dataclass(frozen=True)
class PositionPoint:
    as_of_date: date
    quantity: Decimal
    value_eur: Decimal | None
    #: Bought minus sold to date: the money that is in this holding.
    invested_eur: Decimal


@dataclass(frozen=True)
class TradePoint:
    ts: datetime
    side: str
    quantity: Decimal | None
    #: Per share, in the instrument's currency.
    price: Decimal | None
    #: Cash moved, in EUR (the wallet currency), positive.
    value_eur: Decimal | None
    realised_eur: Decimal | None


@dataclass(frozen=True)
class DividendPoint:
    paid_on: datetime
    amount_eur: Decimal | None
    quantity: Decimal | None
    per_share: Decimal | None


@dataclass(frozen=True)
class PriceReturn:
    key: str
    label: str
    start_date: date | None
    change_pct: float | None


@dataclass(frozen=True)
class PeriodResult:
    key: str
    label: str
    result_eur: Decimal | None
    return_pct: float | None
    price_change_pct: float | None


@dataclass(frozen=True)
class InstrumentDetail:
    ticker: str
    name: str | None
    isin: str | None
    currency: str | None
    instrument_type: str | None
    exchange: str | None
    market_symbol: str | None
    sector: str | None
    quantity: Decimal
    first_bought: datetime | None
    bought_eur: Decimal
    sold_eur: Decimal
    dividends_eur: Decimal
    realised_eur: Decimal
    #: The last replayed value; the live one comes from Trading 212's positions.
    value_eur: Decimal | None
    #: value - bought + sold + dividends: everything this holding has made or lost.
    result_eur: Decimal | None
    high: PricePoint | None
    low: PricePoint | None
    prices: list[PricePoint] = field(default_factory=list)
    positions: list[PositionPoint] = field(default_factory=list)
    trades: list[TradePoint] = field(default_factory=list)
    dividends: list[DividendPoint] = field(default_factory=list)
    price_returns: list[PriceReturn] = field(default_factory=list)
    periods: list[PeriodResult] = field(default_factory=list)


class InstrumentDetailService:
    def __init__(self, repository: PortfolioRepository, settings: Settings) -> None:
        self._repository = repository
        self._settings = settings

    async def detail(self, ticker: str) -> InstrumentDetail:
        instruments = await self._repository.get_cached_instruments_by_tickers({ticker})
        instrument = instruments.get(ticker)
        prices = await self._repository.list_prices_for(ticker)
        holdings = await self._repository.list_daily_holdings_for(ticker)
        flows = await self._repository.list_daily_holding_flows_for(ticker)
        orders = await self._repository.list_orders_for(ticker)
        dividends = await self._repository.list_dividends_for(ticker)
        if instrument is None and not (prices or holdings or orders):
            raise UnknownInstrumentError(ticker)

        currency = (instrument.currency_code if instrument else None) or (
            prices[-1].currency_code if prices else None
        )
        fx_rows: Sequence[FxRateDaily] = []
        if prices and currency and currency.upper() != BASE_CURRENCY:
            fx_map = await self._repository.list_fx_rates(
                start_date=prices[0].price_date
                - timedelta(days=self._settings.analytics_max_fx_stale_days),
                end_date=prices[-1].price_date,
            )
            fx_rows = fx_map.get(currency.upper(), [])
        points = [self._price_point(row, currency, fx_rows) for row in prices]

        nav_rows = await self._repository.list_daily_nav()
        periods = _period_results(nav_rows, holdings, flows, ticker)
        bought = sum((flow.bought_eur for flow in flows), ZERO)
        sold = sum((flow.sold_eur for flow in flows), ZERO)
        dividend_total = sum((flow.dividend_eur for flow in flows), ZERO)
        last_row = holdings[-1] if holdings else None
        # Held now only if the last replayed day still has a row for it (sold holdings stop).
        held_now = last_row is not None and (
            not nav_rows or last_row.as_of_date == nav_rows[-1].as_of_date
        )
        value = last_row.market_value_eur if held_now and last_row else (ZERO if holdings else None)
        fills = [order for order in orders if order.fill_timestamp is not None]
        buy_times = [
            order.fill_timestamp
            for order in fills
            if order.fill_timestamp is not None and (order.side or "").upper() == "BUY"
        ]

        return InstrumentDetail(
            ticker=ticker,
            name=instrument.name if instrument else (orders[0].instrument_name if orders else None),
            isin=instrument.isin if instrument else None,
            currency=currency,
            instrument_type=instrument.instrument_type if instrument else None,
            exchange=instrument.exchange_id if instrument else None,
            market_symbol=instrument.yahoo_ticker if instrument else None,
            sector=instrument.sector if instrument else None,
            quantity=last_row.quantity if held_now and last_row else ZERO,
            first_bought=min(buy_times, default=None),
            bought_eur=bought,
            sold_eur=sold,
            dividends_eur=dividend_total,
            realised_eur=sum(
                (order.wallet_realised_profit_loss or ZERO for order in orders), ZERO
            ),
            value_eur=value,
            result_eur=value - bought + sold + dividend_total if value is not None else None,
            high=max(points, key=lambda point: point.close, default=None),
            low=min(points, key=lambda point: point.close, default=None),
            prices=points,
            positions=_position_points(holdings, flows),
            trades=[_trade_point(order) for order in fills],
            dividends=[
                DividendPoint(
                    paid_on=item.paid_on,
                    amount_eur=item.amount_in_euro,
                    quantity=item.quantity,
                    per_share=item.gross_amount_per_share,
                )
                for item in dividends
                if item.paid_on is not None
            ],
            price_returns=_price_returns(points),
            periods=periods,
        )

    def _price_point(
        self, row: MarketPriceDaily, currency: str | None, fx_rows: Sequence[FxRateDaily]
    ) -> PricePoint:
        code = (currency or row.currency_code).upper()
        if code == BASE_CURRENCY:
            close_eur: Decimal | None = row.close_price
        else:
            rate = _lookup_fx(
                fx_rows, row.price_date, self._settings.analytics_max_fx_stale_days
            ).rate
            close_eur = row.close_price * rate if rate is not None else None
        return PricePoint(
            as_of_date=row.price_date,
            close=row.close_price,
            currency=row.currency_code,
            close_eur=close_eur,
        )


def _trade_point(order: OrderHistory) -> TradePoint:
    assert order.fill_timestamp is not None
    return TradePoint(
        ts=order.fill_timestamp,
        side=(order.side or "").upper(),
        quantity=abs(order.filled_quantity) if order.filled_quantity is not None else None,
        price=order.fill_price,
        value_eur=abs(order.wallet_net_value) if order.wallet_net_value is not None else None,
        realised_eur=order.wallet_realised_profit_loss,
    )


def _position_points(
    holdings: Sequence[DailyHolding], flows: Sequence[DailyHoldingFlow]
) -> list[PositionPoint]:
    """Daily quantity and value, with the money in the holding (bought - sold) to that day."""

    net_by_date: dict[date, Decimal] = {}
    for flow in flows:
        net_by_date[flow.as_of_date] = (
            net_by_date.get(flow.as_of_date, ZERO) + flow.bought_eur - flow.sold_eur
        )
    points: list[PositionPoint] = []
    invested = ZERO
    flow_dates = sorted(net_by_date)
    index = 0
    for row in holdings:
        while index < len(flow_dates) and flow_dates[index] <= row.as_of_date:
            invested += net_by_date[flow_dates[index]]
            index += 1
        points.append(
            PositionPoint(
                as_of_date=row.as_of_date,
                quantity=row.quantity,
                value_eur=row.market_value_eur,
                invested_eur=invested,
            )
        )
    return points


def _window_start(key: str, end: date) -> date | None:
    if key == "1W":
        return end - timedelta(days=7)
    if key == "1M":
        return months_back(end, 1)
    if key == "3M":
        return months_back(end, 3)
    if key == "6M":
        return months_back(end, 6)
    if key == "YTD":
        return date(end.year, 1, 1) - timedelta(days=1)
    if key == "1Y":
        return months_back(end, 12)
    return None


def _price_returns(points: Sequence[PricePoint]) -> list[PriceReturn]:
    """The price move over each window, from the last close on or before its start."""

    if not points:
        return [PriceReturn(key, label, None, None) for key, label in PRICE_WINDOWS]
    last = points[-1]
    results: list[PriceReturn] = []
    for key, label in PRICE_WINDOWS:
        nominal = _window_start(key, last.as_of_date)
        if nominal is None:
            start: PricePoint | None = points[0]
        else:
            candidates = [point for point in points if point.as_of_date <= nominal]
            start = candidates[-1] if candidates else None
            # History that begins within a week of the window's start (a first close on
            # 5 January for "year to date") still answers the question; anything shorter than
            # the window does not, and is left empty rather than passed off as the window.
            if start is None and (points[0].as_of_date - nominal).days <= WINDOW_GRACE_DAYS:
                start = points[0]
        change = (
            float(last.close / start.close - 1) if start is not None and start.close != 0 else None
        )
        results.append(PriceReturn(key, label, start.as_of_date if start else None, change))
    return results


def _period_results(
    nav_rows: Sequence[DailyNav],
    holdings: Sequence[DailyHolding],
    flows: Sequence[DailyHoldingFlow],
    ticker: str,
) -> list[PeriodResult]:
    """This holding's line of the Overview's stock-by-stock split, for every period."""

    results: list[PeriodResult] = []
    for summary in compute_period_summaries(nav_rows, {}):
        movement = None
        if summary.status == "ok" and summary.end_date is not None and flows:
            movement = next(
                (
                    item
                    for item in holding_movements(
                        holdings, flows, start_date=summary.start_date, end_date=summary.end_date
                    )
                    if item.ticker == ticker
                ),
                None,
            )
        results.append(
            PeriodResult(
                key=summary.key,
                label=summary.label,
                result_eur=movement.result_eur if movement else None,
                return_pct=movement.return_pct if movement else None,
                price_change_pct=movement.price_change_pct if movement else None,
            )
        )
    return results

