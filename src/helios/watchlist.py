"""Instruments followed without owning them.

A watched instrument joins everything a holding gets that does not depend on owning it: daily
prices (the replay prices every instrument with a market symbol), news (the news targets include
the watchlist), price alerts (with live quotes where the price provider offers them), and the
same detail page. Adding one resolves its market symbol first, through the same resolver a sync
uses, so the next replay can price it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from .models import Instrument, MarketPriceDaily
from .periods import months_back
from .portfolio_repository import PortfolioRepository
from .rate_limit import Clock, SystemClock
from .resolver import InstrumentResolutionRequest, InstrumentResolver


class UnknownInstrumentError(LookupError):
    pass


@dataclass(frozen=True)
class InstrumentMatch:
    ticker: str
    name: str | None
    isin: str | None
    currency: str | None
    instrument_type: str | None
    watched: bool
    held: bool


@dataclass(frozen=True)
class WatchEntry:
    ticker: str
    name: str | None
    currency: str | None
    instrument_type: str | None
    note: str | None
    added_at: datetime
    held: bool
    #: False when no market symbol could be found for the listing: no prices, no alerts.
    priced: bool
    last_close: Decimal | None
    last_date: date | None
    day_change_pct: float | None
    month_change_pct: float | None
    active_alerts: int


class WatchlistService:
    def __init__(
        self,
        repository: PortfolioRepository,
        resolver: InstrumentResolver,
        clock: Clock | None = None,
    ) -> None:
        self._repository = repository
        self._resolver = resolver
        self._clock = clock or SystemClock()

    async def search(self, query: str, *, limit: int = 20) -> list[InstrumentMatch]:
        found = await self._repository.search_instruments(query, limit=limit)
        watched = {item.t212_ticker for item in await self._repository.list_watchlist()}
        held = await self._repository.held_tickers()
        return [
            InstrumentMatch(
                ticker=item.t212_ticker,
                name=item.name,
                isin=item.isin,
                currency=item.currency_code,
                instrument_type=item.instrument_type,
                watched=item.t212_ticker in watched,
                held=item.t212_ticker in held,
            )
            for item in found
        ]

    async def add(self, ticker: str, *, note: str | None = None) -> None:
        instruments = await self._repository.get_cached_instruments_by_tickers({ticker})
        instrument = instruments.get(ticker)
        if instrument is None:
            raise UnknownInstrumentError(ticker)
        now = self._clock.utcnow()
        if instrument.yahoo_ticker is None:
            await self._resolve(instrument, now)
        await self._repository.add_watch(ticker, note=note, now=now)

    async def remove(self, ticker: str) -> bool:
        return await self._repository.remove_watch(ticker)

    async def entries(self) -> list[WatchEntry]:
        items = await self._repository.list_watchlist()
        if not items:
            return []
        tickers = {item.t212_ticker for item in items}
        instruments = await self._repository.get_cached_instruments_by_tickers(tickers)
        held = await self._repository.held_tickers()
        alerts = await self._repository.list_price_alerts(active_only=True)
        entries: list[WatchEntry] = []
        for item in items:
            instrument = instruments.get(item.t212_ticker)
            prices = await self._repository.list_prices_for(item.t212_ticker)
            entries.append(
                WatchEntry(
                    ticker=item.t212_ticker,
                    name=instrument.name if instrument else None,
                    currency=(instrument.currency_code if instrument else None)
                    or (prices[-1].currency_code if prices else None),
                    instrument_type=instrument.instrument_type if instrument else None,
                    note=item.note,
                    added_at=item.added_at,
                    held=item.t212_ticker in held,
                    priced=instrument is not None and instrument.yahoo_ticker is not None,
                    last_close=prices[-1].close_price if prices else None,
                    last_date=prices[-1].price_date if prices else None,
                    day_change_pct=_change(prices, days=None),
                    month_change_pct=_change(prices, days=30),
                    active_alerts=sum(1 for alert in alerts if alert.ticker == item.t212_ticker),
                )
            )
        return entries

    async def _resolve(self, instrument: Instrument, now: datetime) -> None:
        result = await self._resolver.resolve(
            InstrumentResolutionRequest(
                t212_ticker=instrument.t212_ticker,
                isin=instrument.isin,
                name=instrument.name,
                currency_code=instrument.currency_code,
            )
        )
        await self._repository.set_instrument_mapping(
            instrument.t212_ticker,
            yahoo_ticker=result.yahoo_ticker,
            status=result.status,
            source=result.source,
            details=result.details,
            now=now,
        )


def _change(prices: list[MarketPriceDaily], *, days: int | None) -> float | None:
    """The move to the last close from the previous close (``days`` None) or a month back."""

    if len(prices) < 2:
        return None
    last = prices[-1]
    if days is None:
        start: MarketPriceDaily | None = prices[-2]
    else:
        nominal = months_back(last.price_date, 1)
        earlier = [row for row in prices if row.price_date <= nominal]
        start = earlier[-1] if earlier else None
    if start is None or start.close_price == 0:
        return None
    return float(last.close_price / start.close_price - 1)
