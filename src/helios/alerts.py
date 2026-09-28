"""Price alerts, the daily summary, and the notifications that carry them to the desktop.

Both features end in the same place: a row in ``notifications``. The desktop launcher polls
for undelivered rows and shows each as a Windows notification from the tray, then marks it
delivered, so nothing depends on the browser being open and nothing is shown twice.

Alerts are checked against Trading 212's live price for holdings (the same figure the Holdings
page shows), and against the last stored daily close for anything not held. A triggered alert
fires once and switches itself off; the owner re-arms it from the stock page if they want.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time
from decimal import Decimal
from typing import Literal, Protocol

from .config import Settings
from .models import Notification
from .news import RankedNewsItem
from .performance import (
    MarketDataProviderError,
    NoPerformanceDataError,
    PerformanceReport,
    PriceRequest,
    QuoteProvider,
)
from .periods import PeriodSummary
from .portfolio_repository import PortfolioRepository
from .rate_limit import Clock, SystemClock
from .schemas import Position

AlertKind = Literal["above", "below", "gain_pct", "loss_pct"]
ALERT_KINDS: frozenset[str] = frozenset({"above", "below", "gain_pct", "loss_pct"})

HUNDRED = Decimal("100")
#: The typographic minus the dashboard prints, so the notification reads the same.
MINUS = chr(0x2212)


class PositionSource(Protocol):
    async def get_positions(self) -> list[Position]: ...


@dataclass(frozen=True)
class Quote:
    price: Decimal
    currency: str | None
    #: Average price paid, for gain/loss alerts; None when the ticker is not held.
    average_cost: Decimal | None
    name: str | None
    live: bool


def alert_met(kind: str, threshold: Decimal, quote: Quote) -> bool:
    """Whether the quote satisfies the alert. Gain/loss need an average cost to compare to."""

    if kind == "above":
        return quote.price >= threshold
    if kind == "below":
        return quote.price <= threshold
    if quote.average_cost is None or quote.average_cost <= 0:
        return False
    change = (quote.price / quote.average_cost - 1) * HUNDRED
    if kind == "gain_pct":
        return change >= threshold
    if kind == "loss_pct":
        return change <= -threshold
    return False


def describe_alert(kind: str, threshold: Decimal, currency: str | None) -> str:
    unit = f" {currency}" if currency else ""
    if kind == "above":
        return f"price at or above {_number(threshold)}{unit}"
    if kind == "below":
        return f"price at or below {_number(threshold)}{unit}"
    if kind == "gain_pct":
        return f"up {_number(threshold)}% or more on your average cost"
    return f"down {_number(threshold)}% or more on your average cost"


def _number(value: Decimal) -> str:
    return f"{value:,.2f}".rstrip("0").rstrip(".") if value % 1 else f"{value:,.0f}"


class AlertService:
    def __init__(
        self,
        repository: PortfolioRepository,
        positions: PositionSource,
        settings: Settings,
        clock: Clock | None = None,
        quotes: QuoteProvider | None = None,
    ) -> None:
        self._repository = repository
        self._positions = positions
        self._settings = settings
        self._clock = clock or SystemClock()
        self._live_quotes = quotes

    async def evaluate(self) -> list[Notification]:
        """Check every active alert once; fire (and switch off) those that are met."""

        alerts = await self._repository.list_price_alerts(active_only=True)
        if not alerts:
            return []
        quotes = await self._quotes({alert.ticker for alert in alerts})
        now = self._clock.utcnow()
        fired: list[Notification] = []
        for alert in alerts:
            quote = quotes.get(alert.ticker)
            if quote is None or not alert_met(alert.kind, alert.threshold, quote):
                continue
            symbol = alert.ticker.split("_")[0]
            title = f"{symbol}: {describe_alert(alert.kind, alert.threshold, quote.currency)}"
            body = f"{quote.name or symbol} is at {_number(quote.price)}"
            body += f" {quote.currency}" if quote.currency else ""
            if quote.average_cost:
                change = (quote.price / quote.average_cost - 1) * HUNDRED
                body += f" ({change:+.1f}% on your average cost)"
            if not quote.live:
                body += ", at the last daily close"
            body += "."
            if alert.note:
                body += f" Your note: {alert.note}"
            notification = Notification(
                kind="alert",
                title=title,
                body=body,
                url=f"/holdings/{alert.ticker}",
                created_at=now,
                dedupe_key=f"alert:{alert.id}",
            )
            await self._repository.fire_price_alert(alert.id, price=quote.price, now=now)
            if await self._repository.add_notification(notification):
                fired.append(notification)
        return fired

    async def _quotes(self, tickers: set[str]) -> dict[str, Quote]:
        quotes: dict[str, Quote] = {}
        if self._settings.t212_credentials() is not None:
            for position in await self._positions.get_positions():
                ticker = position.instrument.ticker
                if ticker in tickers and position.current_price is not None:
                    quotes[ticker] = Quote(
                        price=position.current_price,
                        currency=position.instrument.currency,
                        average_cost=position.average_price_paid,
                        name=position.instrument.name,
                        live=True,
                    )
        missing = tickers - set(quotes)
        instruments = await self._repository.get_cached_instruments_by_tickers(missing)
        for ticker in sorted(missing):
            # Not held: a live (usually delayed) quote from the price provider where it offers
            # one, so a watchlist alert fires during the day rather than on the next close.
            instrument = instruments.get(ticker)
            if self._live_quotes is not None and instrument and instrument.yahoo_ticker:
                currency = (instrument.currency_code or "").upper() or None
                try:
                    price = await self._live_quotes.latest_price(
                        PriceRequest(ticker, instrument.yahoo_ticker, currency)
                    )
                except MarketDataProviderError:
                    price = None
                if price is not None:
                    quotes[ticker] = Quote(
                        price=price,
                        currency=currency,
                        average_cost=None,
                        name=instrument.name,
                        live=True,
                    )
                    continue
            prices = await self._repository.list_prices_for(ticker)
            if prices:
                quotes[ticker] = Quote(
                    price=prices[-1].close_price,
                    currency=prices[-1].currency_code,
                    average_cost=None,
                    name=instrument.name if instrument else None,
                    live=False,
                )
        return quotes


# ---------------------------------------------------------------------------
# Daily summary
# ---------------------------------------------------------------------------


class ReportSource(Protocol):
    async def get_report(self) -> PerformanceReport: ...


class NewsSource(Protocol):
    async def list_ranked_news(
        self,
        *,
        t212_ticker: str | None = None,
        isin: str | None = None,
        limit: int = 50,
        held_only: bool = False,
        mentions_only: bool = False,
    ) -> list[RankedNewsItem]: ...


@dataclass(frozen=True)
class SummaryFacts:
    """What the evening notification says, gathered before it is worded."""

    day_result: Decimal | None
    day_return: float | None
    value: Decimal | None
    best: tuple[str, Decimal] | None
    worst: tuple[str, Decimal] | None
    card_spent: Decimal
    card_payments: int
    stories: int


def compose_summary(facts: SummaryFacts) -> tuple[str, str]:
    """Title and body for the daily notification, in plain words."""

    if facts.day_result is None:
        title = "Helios: today"
        lines = ["No valued trading day to report yet."]
    else:
        sign = "+" if facts.day_result >= 0 else MINUS
        pct = f" ({facts.day_return * 100:+.2f}%)" if facts.day_return is not None else ""
        title = f"Today: {sign}€{abs(facts.day_result):,.2f}{pct}"
        lines = []
        if facts.value is not None:
            lines.append(f"Portfolio worth €{facts.value:,.2f}.")
        movers = []
        if facts.best is not None and facts.best[1] > 0:
            movers.append(f"best {facts.best[0]} +€{facts.best[1]:,.2f}")
        if facts.worst is not None and facts.worst[1] < 0:
            movers.append(f"worst {facts.worst[0]} {MINUS}€{abs(facts.worst[1]):,.2f}")
        if movers:
            lines.append(f"Movers: {', '.join(movers)}.")
    if facts.card_payments:
        lines.append(f"Card: €{facts.card_spent:,.2f} in {facts.card_payments} payment(s).")
    if facts.stories:
        lines.append(f"{facts.stories} new story(ies) about your holdings.")
    return title, " ".join(lines)


def summary_due(now_local: datetime, at: time) -> bool:
    return now_local.time() >= at


class DailySummaryService:
    """Writes one summary notification per day, after the configured local time."""

    def __init__(
        self,
        repository: PortfolioRepository,
        report_source: ReportSource,
        news: NewsSource,
        settings: Settings,
        clock: Clock | None = None,
    ) -> None:
        self._repository = repository
        self._report_source = report_source
        self._news = news
        self._settings = settings
        self._clock = clock or SystemClock()

    async def maybe_create(self, *, now_local: datetime | None = None) -> Notification | None:
        if not self._settings.daily_summary_enabled:
            return None
        local = now_local or self._clock.utcnow().astimezone()
        if not summary_due(local, self._settings.daily_summary_at):
            return None
        key = f"daily:{local.date().isoformat()}"
        if await self._repository.has_notification(key):
            return None
        facts = await self._facts(local)
        title, body = compose_summary(facts)
        notification = Notification(
            kind="daily_summary",
            title=title,
            body=body,
            url="/?period=1D",
            created_at=self._clock.utcnow(),
            dedupe_key=key,
        )
        return notification if await self._repository.add_notification(notification) else None

    async def _facts(self, local: datetime) -> SummaryFacts:
        day_result: Decimal | None = None
        day_return: float | None = None
        value: Decimal | None = None
        best = worst = None
        periods: Sequence[PeriodSummary] = []
        try:
            periods = (await self._report_source.get_report()).period_summaries
        except NoPerformanceDataError:  # no replay yet: say so rather than skip the day
            periods = []
        one_day = next((item for item in periods if item.key == "1D"), None)
        if one_day is not None and one_day.status == "ok":
            day_result = one_day.investment_result_eur
            day_return = one_day.twr
            value = one_day.end_value_eur
            movers = [
                (item.ticker.split("_")[0], item.result_eur)
                for item in one_day.holdings
                if item.result_eur is not None
            ]
            if movers:
                best = max(movers, key=lambda mover: mover[1])
                worst = min(movers, key=lambda mover: mover[1])

        start = datetime.combine(local.date(), time(0), tzinfo=local.tzinfo).astimezone(UTC)
        card_rows = [
            row
            for row in await self._repository.list_export_rows()
            if row.ts >= start and row.action.lower().startswith("card ") and row.total is not None
        ]
        withdrawals = [
            item
            for item in await self._repository.list_withdrawals_since(start)
            if (item.transaction_type or "").upper() in {"WITHDRAW", "WITHDRAWAL"}
            and item.amount is not None
            and item.reference not in {row.row_id for row in card_rows}
        ]
        card_spent = -sum((row.total or Decimal(0) for row in card_rows), Decimal(0)) - sum(
            (item.amount or Decimal(0) for item in withdrawals), Decimal(0)
        )
        ranked = await self._news.list_ranked_news(limit=200, held_only=True, mentions_only=True)
        stories = sum(
            1
            for entry in ranked
            if entry.item.published_at is not None and entry.item.published_at >= start
        )
        return SummaryFacts(
            day_result=day_result,
            day_return=day_return,
            value=value,
            best=best,
            worst=worst,
            card_spent=card_spent,
            card_payments=len(card_rows) + len(withdrawals),
            stories=stories,
        )

