"""The calendar: upcoming earnings and dividends for what you hold and watch, and dividend income.

**Sources.** Alpha Vantage's free key (the one saved for London prices) offers two things this
needs: ``EARNINGS_CALENDAR``, every US company's next reports in one CSV request, and
``DIVIDENDS``, one company's declared dividends -- past ones and those announced but not yet
paid. The free plan allows 25 requests a day, shared with price backfills, so the earnings
calendar is fetched once a day and each company's dividends at most once a week, a few per run.

**Confirmed vs estimated.** A dividend Alpha Vantage lists with a future payment date is
*declared*: the company has announced it. Beyond those, Helios projects the next payments from
the company's own rhythm (the median gap between its past dividends) and its latest amount --
labelled as an estimate, never shown as fact. A holding with a single past dividend and no
declared history has no rhythm to follow, so it is not projected at all.

**Money.** Projections are in euros at the latest ECB rate, after the share of tax that was
withheld from your own past dividends for that holding (US stocks usually lose 15%); without
any past payment to learn from, the amount is shown before tax and says so.
"""

from __future__ import annotations

import calendar
import csv
import io
import itertools
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Protocol

import httpx
from pydantic import SecretStr
from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .config import Settings
from .logging import get_logger
from .models import (
    Dividend,
    DividendEvent,
    EarningsEvent,
    EventFetch,
    FxRateDaily,
    Instrument,
    Notification,
    PositionLive,
    WatchlistItem,
)
from .performance import RequestPacer, _alphavantage_symbol, _provider_get
from .rate_limit import Clock, SystemClock


class NotificationSink(Protocol):
    async def add_notification(self, notification: Notification) -> bool: ...


logger = get_logger(__name__)

EARNINGS_KEY = "earnings-calendar"
EARNINGS_REFRESH = timedelta(hours=20)
DIVIDENDS_REFRESH = timedelta(days=7)
#: Companies whose dividends are refreshed per run: the rest wait for the next run.
DIVIDEND_FETCHES_PER_RUN = 4
#: How far ahead the calendar looks.
HORIZON_DAYS = 120
#: Gap (days) -> payments a year. Anything else is "irregular".
CADENCES = ((25, 40, 12), (80, 105, 4), (160, 200, 2), (330, 400, 1))
#: How many payments back the rhythm is read from.
RHYTHM_WINDOW = 8


def alphavantage_key(settings: Settings) -> SecretStr | None:
    """The Alpha Vantage key, whether it is the primary price key or the fallback one."""

    if settings.market_data_provider == "alphavantage" and settings.market_data_api_key:
        return settings.market_data_api_key
    if settings.effective_market_data_fallback_provider == "alphavantage":
        return settings.market_data_fallback_api_key
    return None


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "-"}:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text or text.lower() == "none":
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def parse_earnings_csv(text: str) -> list[dict[str, object]]:
    """Rows of Alpha Vantage's EARNINGS_CALENDAR CSV, with typed values."""

    rows: list[dict[str, object]] = []
    for raw in csv.DictReader(io.StringIO(text)):
        symbol = (raw.get("symbol") or "").strip()
        report = _date(raw.get("reportDate"))
        if not symbol or report is None:
            continue
        timing = (raw.get("timeOfTheDay") or "").strip().lower() or None
        rows.append(
            {
                "symbol": symbol,
                "report_date": report,
                "fiscal_date_ending": _date(raw.get("fiscalDateEnding")),
                "estimate_eps": _decimal(raw.get("estimate")),
                "currency_code": (raw.get("currency") or "").strip() or None,
                "time_of_day": timing,
            }
        )
    return rows


def parse_dividends_json(payload: object) -> list[dict[str, object]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return []
    rows: list[dict[str, object]] = []
    for raw in payload["data"]:
        if not isinstance(raw, dict):
            continue
        ex_date = _date(raw.get("ex_dividend_date"))
        amount = _decimal(raw.get("amount"))
        if ex_date is None or amount is None or amount <= 0:
            continue
        rows.append(
            {
                "ex_date": ex_date,
                "payment_date": _date(raw.get("payment_date")),
                "declaration_date": _date(raw.get("declaration_date")),
                "amount_per_share": amount,
            }
        )
    return rows


# --- the calendar model ------------------------------------------------------------------------


@dataclass(frozen=True)
class CalendarEvent:
    day: date
    kind: str  # "earnings" | "dividend"
    ticker: str
    name: str | None
    held: bool
    confirmed: bool
    time_of_day: str | None = None
    estimate_eps: Decimal | None = None
    eps_currency: str | None = None
    ex_date: date | None = None
    amount_per_share: Decimal | None = None
    currency_code: str | None = None
    amount_eur: Decimal | None = None
    after_tax: bool = False


@dataclass(frozen=True)
class IncomeMonth:
    month: str  # "2026-10"
    received_eur: Decimal
    projected_eur: Decimal


@dataclass(frozen=True)
class HoldingIncome:
    ticker: str
    name: str | None
    shares: Decimal
    payments_per_year: int | None
    amount_per_share: Decimal | None
    currency_code: str | None
    annual_eur: Decimal | None
    yield_pct: float | None
    next_ex_date: date | None
    next_payment_date: date | None
    next_confirmed: bool
    after_tax: bool
    received_12m_eur: Decimal
    source: str  # "declared" | "history" | "none"


@dataclass(frozen=True)
class Calendar:
    as_of: date
    provider_available: bool
    earnings_fetched_at: datetime | None
    events: list[CalendarEvent]
    months: list[IncomeMonth]
    holdings: list[HoldingIncome]
    received_12m_eur: Decimal
    projected_12m_eur: Decimal
    portfolio_value_eur: Decimal | None
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Payment:
    ex_date: date | None
    pay_date: date
    amount: Decimal


def payments_per_year(dates: Sequence[date]) -> int | None:
    """Payments a year read from the median gap between recent dividends, if regular."""

    ordered = sorted(set(dates))[-RHYTHM_WINDOW:]
    if len(ordered) < 2:
        return None
    gap = statistics.median((b - a).days for a, b in itertools.pairwise(ordered))
    for low, high, per_year in CADENCES:
        if low <= gap <= high:
            return per_year
    return None


def _add_months(day: date, months: int) -> date:
    """The same day ``months`` later, clamped to the month's end (31 Jan + 1 -> 28/29 Feb)."""

    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _month_key(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def _fx(rows: dict[str, list[FxRateDaily]], currency: str | None) -> Decimal | None:
    if currency is None:
        return None
    code = currency.upper()
    if code == "EUR":
        return Decimal(1)
    divisor = Decimal(1)
    if code == "GBX":
        code, divisor = "GBP", Decimal(100)
    series = rows.get(code)
    if not series:
        return None
    return series[-1].eur_per_unit / divisor


def _fx_on(rows: dict[str, list[FxRateDaily]], currency: str | None, day: date) -> Decimal | None:
    if currency is None:
        return None
    code = currency.upper()
    if code == "EUR":
        return Decimal(1)
    divisor = Decimal(1)
    if code == "GBX":
        code, divisor = "GBP", Decimal(100)
    candidates = [row for row in rows.get(code, []) if row.rate_date <= day]
    if not candidates:
        return None
    return candidates[-1].eur_per_unit / divisor


def _paid_date(dividend: Dividend) -> date:
    assert dividend.paid_on is not None
    return dividend.paid_on.astimezone(UTC).date()


def _net_share(
    own: Sequence[Dividend], fx: dict[str, list[FxRateDaily]], currency: str | None
) -> Decimal | None:
    """Of what past dividends were worth gross, the share that reached the account."""

    ratios: list[Decimal] = []
    for dividend in own[-RHYTHM_WINDOW:]:
        if (
            dividend.amount_in_euro is None
            or dividend.quantity is None
            or dividend.gross_amount_per_share is None
            or dividend.paid_on is None
        ):
            continue
        rate = _fx_on(fx, currency, _paid_date(dividend))
        gross = dividend.quantity * dividend.gross_amount_per_share * (rate or Decimal(0))
        if gross > 0:
            ratios.append(dividend.amount_in_euro / gross)
    if not ratios:
        return None
    return min(max(Decimal(str(statistics.median(ratios))), Decimal("0.5")), Decimal(1))


@dataclass(frozen=True)
class _Rhythm:
    source: str
    per_year: int | None
    latest_amount: Decimal | None
    payments: list[_Payment]


def _rhythm(own: Sequence[Dividend], declared: Sequence[DividendEvent]) -> _Rhythm:
    """Past (and announced) payments, their cadence and the latest amount per share."""

    if declared:
        payments = [
            _Payment(item.ex_date, item.payment_date or item.ex_date, item.amount_per_share)
            for item in declared
        ]
        return _Rhythm(
            "declared",
            payments_per_year([payment.pay_date for payment in payments]),
            declared[-1].amount_per_share,
            payments,
        )
    paid = [item for item in own if item.paid_on is not None]
    if paid:
        payments = [
            _Payment(None, _paid_date(item), item.gross_amount_per_share)
            for item in paid
            if item.gross_amount_per_share is not None
        ]
        return _Rhythm(
            "history",
            payments_per_year([_paid_date(item) for item in paid]),
            paid[-1].gross_amount_per_share,
            payments,
        )
    return _Rhythm("none", None, None, [])


def _upcoming(rhythm: _Rhythm, today: date, until: date) -> list[tuple[_Payment, bool]]:
    """Announced payments still to come, then the rhythm projected forward (estimates)."""

    upcoming = [(payment, True) for payment in rhythm.payments if payment.pay_date > today]
    if rhythm.per_year and rhythm.latest_amount and rhythm.payments:
        step = 12 // rhythm.per_year
        next_date = _add_months(max(payment.pay_date for payment in rhythm.payments), step)
        while next_date <= until:
            clash = any(abs((known.pay_date - next_date).days) < 20 for known, _ in upcoming)
            if next_date > today and not clash:
                upcoming.append((_Payment(None, next_date, rhythm.latest_amount), False))
            next_date = _add_months(next_date, step)
    return sorted(upcoming, key=lambda item: item[0].pay_date)


def build_calendar(
    *,
    today: date,
    positions: Sequence[PositionLive],
    watched: Sequence[str],
    instruments: dict[str, Instrument],
    own_dividends: Sequence[Dividend],
    declared: Sequence[DividendEvent],
    earnings: Sequence[EarningsEvent],
    fx: dict[str, list[FxRateDaily]],
    provider_available: bool,
    earnings_fetched_at: datetime | None,
) -> Calendar:
    horizon = today + timedelta(days=HORIZON_DAYS)
    year_ahead = _add_months(today, 12)
    year_back = _add_months(today, -12)
    held = {row.t212_ticker: row for row in positions if row.quantity > 0}
    names: dict[str, str | None] = {}
    for ticker in {*held, *watched}:
        instrument = instruments.get(ticker)
        position = held.get(ticker)
        names[ticker] = (instrument.name if instrument else None) or (
            position.instrument_name if position else None
        )

    own_by_ticker: dict[str, list[Dividend]] = {}
    for dividend in sorted(
        (item for item in own_dividends if item.paid_on is not None), key=_paid_date
    ):
        if dividend.t212_ticker:
            own_by_ticker.setdefault(dividend.t212_ticker, []).append(dividend)
    declared_by_ticker: dict[str, list[DividendEvent]] = {}
    for announced in sorted(declared, key=lambda item: item.ex_date):
        declared_by_ticker.setdefault(announced.t212_ticker, []).append(announced)

    # What actually arrived, month by month: net, in euros, as Trading 212 booked it.
    received: dict[str, Decimal] = {}
    received_by_ticker: dict[str, Decimal] = {}
    for dividends in own_by_ticker.values():
        for dividend in dividends:
            paid = _paid_date(dividend)
            if dividend.amount_in_euro is None or not year_back <= paid <= today:
                continue
            received[_month_key(paid)] = (
                received.get(_month_key(paid), Decimal(0)) + dividend.amount_in_euro
            )
            ticker = dividend.t212_ticker or ""
            received_by_ticker[ticker] = (
                received_by_ticker.get(ticker, Decimal(0)) + dividend.amount_in_euro
            )

    events: list[CalendarEvent] = []
    projected: dict[str, Decimal] = {}
    holdings: list[HoldingIncome] = []
    for ticker, position in sorted(held.items()):
        instrument = instruments.get(ticker)
        currency = (
            instrument.currency_code if instrument else None
        ) or position.instrument_currency
        own = own_by_ticker.get(ticker, [])
        rate = _fx(fx, currency)
        net_share = _net_share(own, fx, currency)
        rhythm = _rhythm(own, declared_by_ticker.get(ticker, []))
        upcoming = _upcoming(rhythm, today, year_ahead)

        annual = Decimal(0)
        for payment, confirmed in upcoming:
            amount = (
                None
                if rate is None
                else (
                    payment.amount * position.quantity * rate * (net_share or Decimal(1))
                ).quantize(Decimal("0.01"))
            )
            month = _month_key(payment.pay_date)
            if amount is not None:
                projected[month] = projected.get(month, Decimal(0)) + amount
                annual += amount
            if payment.pay_date <= horizon:
                events.append(
                    CalendarEvent(
                        day=payment.pay_date,
                        kind="dividend",
                        ticker=ticker,
                        name=names.get(ticker),
                        held=True,
                        confirmed=confirmed,
                        ex_date=payment.ex_date,
                        amount_per_share=payment.amount,
                        currency_code=currency,
                        amount_eur=amount,
                        after_tax=net_share is not None,
                    )
                )

        value = position.wallet_current_value
        first = upcoming[0] if upcoming else None
        holdings.append(
            HoldingIncome(
                ticker=ticker,
                name=names.get(ticker),
                shares=position.quantity,
                payments_per_year=rhythm.per_year,
                amount_per_share=rhythm.latest_amount,
                currency_code=currency,
                annual_eur=annual
                if upcoming
                else (Decimal(0) if rhythm.source == "none" else None),
                yield_pct=float(annual / value) if upcoming and value and value > 0 else None,
                next_ex_date=first[0].ex_date if first else None,
                next_payment_date=first[0].pay_date if first else None,
                next_confirmed=first[1] if first else False,
                after_tax=net_share is not None,
                received_12m_eur=received_by_ticker.get(ticker, Decimal(0)),
                source=rhythm.source,
            )
        )

    for report in earnings:
        followed = report.t212_ticker in held or report.t212_ticker in watched
        if followed and today <= report.report_date <= horizon:
            events.append(
                CalendarEvent(
                    day=report.report_date,
                    kind="earnings",
                    ticker=report.t212_ticker,
                    name=names.get(report.t212_ticker),
                    held=report.t212_ticker in held,
                    confirmed=True,
                    time_of_day=report.time_of_day,
                    estimate_eps=report.estimate_eps,
                    eps_currency=report.currency_code,
                )
            )
    events.sort(key=lambda item: (item.day, item.kind != "earnings", item.ticker))

    months: list[IncomeMonth] = []
    cursor = _add_months(date(today.year, today.month, 1), -11)
    for _ in range(24):
        key = _month_key(cursor)
        months.append(
            IncomeMonth(
                month=key,
                received_eur=received.get(key, Decimal(0)),
                projected_eur=projected.get(key, Decimal(0)),
            )
        )
        cursor = _add_months(cursor, 1)

    values = [
        row.wallet_current_value for row in held.values() if row.wallet_current_value is not None
    ]
    notes: list[str] = []
    if not provider_available:
        notes.append(
            "Add an Alpha Vantage key in Settings to see earnings dates and declared dividends. "
            "Dividend estimates below come from your own past payments only."
        )
    return Calendar(
        as_of=today,
        provider_available=provider_available,
        earnings_fetched_at=earnings_fetched_at,
        events=events,
        months=months,
        holdings=sorted(holdings, key=lambda row: row.annual_eur or Decimal(0), reverse=True),
        received_12m_eur=sum(received.values(), Decimal(0)),
        projected_12m_eur=sum(projected.values(), Decimal(0)),
        portfolio_value_eur=sum(values, Decimal(0)) if values else None,
        notes=notes,
    )


# --- the service -------------------------------------------------------------------------------


async def _held_tickers(session: AsyncSession) -> set[str]:
    """Tickers in the latest positions snapshot with shares still held.

    Quantities are exact decimals stored as text, so the comparison happens here, not in SQL.
    """

    latest = await session.scalar(select(PositionLive.ts).order_by(PositionLive.ts.desc()).limit(1))
    if latest is None:
        return set()
    rows = await session.scalars(select(PositionLive).where(PositionLive.ts == latest))
    return {row.t212_ticker for row in rows if row.quantity > 0}


class MarketEventsService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        clock: Clock | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._clock = clock or SystemClock()
        self._client = client
        self._pacer = RequestPacer(settings.alphavantage_min_interval_seconds)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._settings.market_data_timeout_seconds)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def _targets(self, session: AsyncSession) -> dict[str, Instrument]:
        held = await _held_tickers(session)
        watched = set(await session.scalars(select(WatchlistItem.t212_ticker)))
        instruments = await session.scalars(
            select(Instrument).where(Instrument.t212_ticker.in_(held | watched))
        )
        return {row.t212_ticker: row for row in instruments}

    async def _last_fetch(self, session: AsyncSession, key: str) -> EventFetch | None:
        return await session.get(EventFetch, key)

    async def _record(self, key: str, status: str, detail: str | None = None) -> None:
        async with self._session_factory() as session, session.begin():
            await session.merge(
                EventFetch(key=key, fetched_at=self._clock.utcnow(), status=status, detail=detail)
            )

    async def refresh(self, *, force: bool = False) -> dict[str, int]:
        """Fetch what is due: the earnings calendar daily, each company's dividends weekly."""

        key = alphavantage_key(self._settings)
        if key is None:
            return {"earnings": 0, "dividends": 0}
        now = self._clock.utcnow()
        async with self._session_factory() as session:
            targets = await self._targets(session)
            earnings_fetch = await self._last_fetch(session, EARNINGS_KEY)
            dividend_fetches = {
                row.key: row
                for row in await session.scalars(
                    select(EventFetch).where(EventFetch.key.like("dividends:%"))
                )
            }
        # US-listed stocks only: ETFs report no earnings, and the free calendar covers the US.
        stocks = {
            ticker: instrument
            for ticker, instrument in targets.items()
            if (instrument.instrument_type or "").upper() == "STOCK" and instrument.yahoo_ticker
        }
        counts = {"earnings": 0, "dividends": 0}
        if stocks and (
            force or earnings_fetch is None or now - earnings_fetch.fetched_at >= EARNINGS_REFRESH
        ):
            counts["earnings"] = await self._refresh_earnings(key, stocks)
        due = sorted(
            (
                ticker
                for ticker in stocks
                if force
                or f"dividends:{ticker}" not in dividend_fetches
                or now - dividend_fetches[f"dividends:{ticker}"].fetched_at >= DIVIDENDS_REFRESH
            ),
            key=lambda ticker: (
                dividend_fetches[f"dividends:{ticker}"].fetched_at
                if f"dividends:{ticker}" in dividend_fetches
                else datetime.min.replace(tzinfo=UTC)
            ),
        )
        for ticker in due[:DIVIDEND_FETCHES_PER_RUN]:
            counts["dividends"] += await self._refresh_dividends(key, ticker, stocks[ticker])
        return counts

    async def _get(self, key: SecretStr, params: dict[str, str]) -> httpx.Response:
        return await _provider_get(
            self._http(),
            self._settings.market_data_base_url,
            params={**params, "apikey": key.get_secret_value()},
            provider="Alpha Vantage",
            pacer=self._pacer,
        )

    async def _refresh_earnings(self, key: SecretStr, stocks: dict[str, Instrument]) -> int:
        try:
            response = await self._get(key, {"function": "EARNINGS_CALENDAR", "horizon": "3month"})
        except Exception as exc:  # the provider's own error is already URL-free
            await self._record(EARNINGS_KEY, "failed", str(exc))
            return 0
        text = response.text
        if text.lstrip().startswith("{"):
            # A JSON body here is Alpha Vantage saying no: quota used up or a bad key.
            await self._record(EARNINGS_KEY, "refused", text[:300])
            return 0
        by_symbol = {
            _alphavantage_symbol(instrument.yahoo_ticker or ""): ticker
            for ticker, instrument in stocks.items()
        }
        now = self._clock.utcnow()
        rows = [row for row in parse_earnings_csv(text) if row["symbol"] in by_symbol]
        async with self._session_factory() as session, session.begin():
            tickers = sorted(set(by_symbol.values()))
            # The calendar replaces what it said before about these companies' future reports.
            await session.execute(
                delete(EarningsEvent).where(
                    EarningsEvent.t212_ticker.in_(tickers),
                    EarningsEvent.report_date >= now.date(),
                )
            )
            for row in rows:
                statement = insert(EarningsEvent).values(
                    t212_ticker=by_symbol[str(row["symbol"])],
                    report_date=row["report_date"],
                    fiscal_date_ending=row["fiscal_date_ending"],
                    estimate_eps=row["estimate_eps"],
                    currency_code=row["currency_code"],
                    time_of_day=row["time_of_day"],
                    fetched_at=now,
                )
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=["t212_ticker", "report_date"],
                        set_={
                            "fiscal_date_ending": statement.excluded.fiscal_date_ending,
                            "estimate_eps": statement.excluded.estimate_eps,
                            "currency_code": statement.excluded.currency_code,
                            "time_of_day": statement.excluded.time_of_day,
                            "fetched_at": statement.excluded.fetched_at,
                        },
                    )
                )
        await self._record(EARNINGS_KEY, "ok", f"{len(rows)} report(s)")
        return len(rows)

    async def _refresh_dividends(self, key: SecretStr, ticker: str, instrument: Instrument) -> int:
        fetch_key = f"dividends:{ticker}"
        symbol = _alphavantage_symbol(instrument.yahoo_ticker or "")
        try:
            response = await self._get(key, {"function": "DIVIDENDS", "symbol": symbol})
            payload = response.json()
        except Exception as exc:
            await self._record(fetch_key, "failed", str(exc)[:300])
            return 0
        if isinstance(payload, dict) and ("Information" in payload or "Note" in payload):
            await self._record(fetch_key, "refused", str(payload)[:300])
            return 0
        rows = parse_dividends_json(payload)
        now = self._clock.utcnow()
        async with self._session_factory() as session, session.begin():
            for row in rows:
                statement = insert(DividendEvent).values(
                    t212_ticker=ticker,
                    currency_code=instrument.currency_code,
                    fetched_at=now,
                    **row,
                )
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=["t212_ticker", "ex_date"],
                        set_={
                            "payment_date": statement.excluded.payment_date,
                            "declaration_date": statement.excluded.declaration_date,
                            "amount_per_share": statement.excluded.amount_per_share,
                            "fetched_at": statement.excluded.fetched_at,
                        },
                    )
                )
        await self._record(fetch_key, "ok", f"{len(rows)} dividend(s)")
        return len(rows)

    async def calendar(self) -> Calendar:
        today = self._clock.utcnow().astimezone().date()
        async with self._session_factory() as session:
            latest = await session.scalar(
                select(PositionLive.ts).order_by(PositionLive.ts.desc()).limit(1)
            )
            positions = (
                list(await session.scalars(select(PositionLive).where(PositionLive.ts == latest)))
                if latest
                else []
            )
            watched = list(await session.scalars(select(WatchlistItem.t212_ticker)))
            tickers = {row.t212_ticker for row in positions} | set(watched)
            instruments = {
                row.t212_ticker: row
                for row in await session.scalars(
                    select(Instrument).where(Instrument.t212_ticker.in_(tickers))
                )
            }
            own = list(await session.scalars(select(Dividend)))
            declared = list(
                await session.scalars(
                    select(DividendEvent).where(DividendEvent.t212_ticker.in_(tickers))
                )
            )
            earnings = list(
                await session.scalars(
                    select(EarningsEvent).where(
                        EarningsEvent.t212_ticker.in_(tickers),
                        EarningsEvent.report_date >= today,
                    )
                )
            )
            fx_rows = list(
                await session.scalars(
                    select(FxRateDaily)
                    .where(FxRateDaily.rate_date >= today - timedelta(days=800))
                    .order_by(FxRateDaily.currency_code, FxRateDaily.rate_date)
                )
            )
            fetched = await self._last_fetch(session, EARNINGS_KEY)
        fx: dict[str, list[FxRateDaily]] = {}
        for row in fx_rows:
            fx.setdefault(row.currency_code, []).append(row)
        return build_calendar(
            today=today,
            positions=positions,
            watched=watched,
            instruments=instruments,
            own_dividends=own,
            declared=declared,
            earnings=earnings,
            fx=fx,
            provider_available=alphavantage_key(self._settings) is not None,
            earnings_fetched_at=fetched.fetched_at if fetched else None,
        )

    async def notify(self, sink: NotificationSink) -> list[Notification]:
        """Earnings tomorrow, and dividends that just arrived: one notification each."""

        now = self._clock.utcnow()
        today = now.astimezone().date()
        created: list[Notification] = []
        async with self._session_factory() as session:
            held = await _held_tickers(session)
            watched = set(await session.scalars(select(WatchlistItem.t212_ticker)))
            soon = list(
                await session.scalars(
                    select(EarningsEvent).where(
                        EarningsEvent.report_date == today + timedelta(days=1),
                        EarningsEvent.t212_ticker.in_(held | watched),
                    )
                )
            )
            arrived = list(
                await session.scalars(
                    select(Dividend).where(Dividend.paid_on >= now - timedelta(days=3))
                )
            )
            names = {
                row.t212_ticker: row.name
                for row in await session.scalars(
                    select(Instrument).where(
                        Instrument.t212_ticker.in_(
                            {event.t212_ticker for event in soon}
                            | {row.t212_ticker for row in arrived if row.t212_ticker}
                        )
                    )
                )
            }
        for event in soon:
            name = names.get(event.t212_ticker) or event.t212_ticker
            when = {
                "pre-market": " before the market opens",
                "post-market": " after the close",
            }.get(event.time_of_day or "", "")
            unit = f" {event.currency_code}" if event.currency_code else ""
            estimate = (
                f" Analysts expect {event.estimate_eps}{unit} a share."
                if event.estimate_eps is not None
                else ""
            )
            notification = Notification(
                kind="earnings",
                title=f"{name} reports tomorrow",
                body=f"{name} publishes its results tomorrow{when}.{estimate}",
                url=f"/holdings/{event.t212_ticker}",
                created_at=now,
                dedupe_key=f"earnings:{event.t212_ticker}:{event.report_date.isoformat()}",
            )
            if await sink.add_notification(notification):
                created.append(notification)
        for dividend in arrived:
            if dividend.amount_in_euro is None or dividend.amount_in_euro <= 0:
                continue
            name = names.get(dividend.t212_ticker or "") or dividend.t212_ticker or "A holding"
            notification = Notification(
                kind="dividend",
                title=f"Dividend from {name}",
                body=f"€{dividend.amount_in_euro:,.2f} arrived in your account.",
                url="/calendar",
                created_at=now,
                dedupe_key=f"dividend:{dividend.reference}",
            )
            if await sink.add_notification(notification):
                created.append(notification)
        return created


__all__ = [
    "Calendar",
    "CalendarEvent",
    "HoldingIncome",
    "IncomeMonth",
    "MarketEventsService",
    "alphavantage_key",
    "build_calendar",
    "parse_dividends_json",
    "parse_earnings_csv",
    "payments_per_year",
]
