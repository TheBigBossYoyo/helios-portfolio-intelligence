"""The watchlist: search, following an instrument, and what following it switches on."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from helios.alerts import AlertService
from helios.config import Settings
from helios.db import migrate_database
from helios.models import Instrument, MarketPriceDaily, PositionLive, PriceAlert
from helios.performance import PriceRequest
from helios.portfolio_repository import PortfolioRepository
from helios.rate_limit import Clock
from helios.resolver import InstrumentMappingResult, InstrumentResolutionRequest
from helios.schemas import Position
from helios.watchlist import UnknownInstrumentError, WatchlistService

D = Decimal
NOW = datetime(2026, 9, 28, 12, tzinfo=UTC)


class FixedClock(Clock):
    def now(self) -> float:
        return NOW.timestamp()

    def utcnow(self) -> datetime:
        return NOW

    async def sleep(self, seconds: float) -> None:
        return None


@dataclass
class FakeResolver:
    calls: list[str] = field(default_factory=list)

    async def resolve(self, request: InstrumentResolutionRequest) -> InstrumentMappingResult:
        self.calls.append(request.t212_ticker)
        return InstrumentMappingResult(
            status="resolved", source="openfigi", yahoo_ticker="AAPL", details={"figi": "x"}
        )


async def _seeded(tmp_path: Path) -> PortfolioRepository:
    settings = Settings(data_dir=tmp_path, sqlite_filename="watch.sqlite3")
    await migrate_database(settings)
    engine = create_async_engine(settings.sqlite_url, poolclass=NullPool)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        session.add_all(
            [
                Instrument(
                    t212_ticker="AAPL_US_EQ",
                    name="Apple",
                    short_name="AAPL",
                    isin="US0378331005",
                    currency_code="USD",
                    instrument_type="STOCK",
                    mapping_status="unresolved",
                ),
                Instrument(
                    t212_ticker="APLE_US_EQ",
                    name="Apple Hospitality REIT",
                    short_name="APLE",
                    currency_code="USD",
                    mapping_status="unresolved",
                ),
                Instrument(
                    t212_ticker="MU_US_EQ",
                    name="Micron Technology",
                    short_name="MU",
                    yahoo_ticker="MU",
                    currency_code="USD",
                    mapping_status="resolved",
                ),
                PositionLive(ts=NOW, t212_ticker="MU_US_EQ", quantity=D("1")),
            ]
        )
    return PortfolioRepository(factory)


@pytest.mark.asyncio
async def test_search_ranks_the_exact_symbol_first_and_flags_held(tmp_path: Path) -> None:
    repository = await _seeded(tmp_path)
    service = WatchlistService(repository, FakeResolver(), FixedClock())

    apple = await service.search("aapl")
    micron = await service.search("micron")

    assert [match.ticker for match in apple][:1] == ["AAPL_US_EQ"]
    assert micron[0].held is True and micron[0].watched is False


@pytest.mark.asyncio
async def test_following_resolves_the_symbol_and_adds_it_to_news(tmp_path: Path) -> None:
    repository = await _seeded(tmp_path)
    resolver = FakeResolver()
    service = WatchlistService(repository, resolver, FixedClock())

    await service.add("AAPL_US_EQ", note="wait for a dip")
    await service.add("MU_US_EQ")  # already resolved: no second lookup

    assert resolver.calls == ["AAPL_US_EQ"]
    instrument = (await repository.get_cached_instruments_by_tickers({"AAPL_US_EQ"}))["AAPL_US_EQ"]
    assert (instrument.yahoo_ticker, instrument.mapping_status) == ("AAPL", "resolved")
    targets = {target.t212_ticker for target in await repository.list_instrument_news_targets()}
    assert targets == {"AAPL_US_EQ", "MU_US_EQ"}
    entries = await service.entries()
    assert [(entry.ticker, entry.note, entry.priced, entry.held) for entry in entries] == [
        ("AAPL_US_EQ", "wait for a dip", True, False),
        ("MU_US_EQ", None, True, True),
    ]
    assert await service.remove("AAPL_US_EQ") is True
    assert await service.remove("AAPL_US_EQ") is False


@pytest.mark.asyncio
async def test_unknown_tickers_are_refused(tmp_path: Path) -> None:
    service = WatchlistService(await _seeded(tmp_path), FakeResolver(), FixedClock())

    with pytest.raises(UnknownInstrumentError):
        await service.add("NOPE_EQ")


@pytest.mark.asyncio
async def test_entries_report_the_last_close_and_its_moves(tmp_path: Path) -> None:
    repository = await _seeded(tmp_path)
    service = WatchlistService(repository, FakeResolver(), FixedClock())
    await service.add("AAPL_US_EQ")
    await repository.upsert_market_prices(
        [
            MarketPriceDaily(
                price_date=day,
                t212_ticker="AAPL_US_EQ",
                provider_symbol="AAPL",
                currency_code="USD",
                close_price=D(price),
                provider="fixture",
                source_date=day,
                provenance="EXACT",
            )
            for day, price in (
                (date(2026, 8, 25), "200"),
                (date(2026, 9, 24), "210"),
                (date(2026, 9, 25), "220"),
            )
        ]
    )

    [entry] = await service.entries()

    assert (entry.last_close, entry.last_date) == (D("220"), date(2026, 9, 25))
    assert entry.day_change_pct == pytest.approx(220 / 210 - 1)
    assert entry.month_change_pct == pytest.approx(0.10)


@dataclass
class FakeQuotes:
    seen: list[PriceRequest] = field(default_factory=list)

    async def latest_price(self, request: PriceRequest) -> Decimal | None:
        self.seen.append(request)
        return D("190.5")


class NoPositions:
    async def get_positions(self) -> list[Position]:
        return []


@pytest.mark.asyncio
async def test_a_watched_stock_alert_uses_a_live_quote(tmp_path: Path) -> None:
    repository = await _seeded(tmp_path)
    await WatchlistService(repository, FakeResolver(), FixedClock()).add("AAPL_US_EQ")
    await repository.add_price_alert(
        PriceAlert(
            ticker="AAPL_US_EQ", kind="below", threshold=D("195"), created_at=NOW, active=True
        )
    )
    quotes = FakeQuotes()
    service = AlertService(
        repository,
        NoPositions(),
        Settings(data_dir=tmp_path, t212_api_key="k", t212_api_secret="s"),
        FixedClock(),
        quotes=quotes,
    )

    [fired] = await service.evaluate()

    assert quotes.seen == [PriceRequest("AAPL_US_EQ", "AAPL", "USD")]
    assert fired.body == "Apple is at 190.5 USD."
