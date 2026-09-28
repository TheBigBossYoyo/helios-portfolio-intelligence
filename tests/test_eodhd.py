"""EODHD end-of-day provider: symbol mapping, the request on the wire, and failure handling."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from helios.config import Settings
from helios.dependencies import _build_market_data_provider
from helios.performance import (
    CompositeMarketDataProvider,
    EodhdMarketDataProvider,
    MarketDataProviderError,
    PriceRequest,
    _eodhd_symbol,
)


def _provider(tmp_path: Path, handler: object) -> EodhdMarketDataProvider:
    settings = Settings(
        data_dir=tmp_path, market_data_provider="eodhd", market_data_api_key="eod-key"
    )
    provider = EodhdMarketDataProvider(settings)
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
    return provider


BARS = [
    {"date": "2026-04-07", "open": 1, "high": 1, "low": 1, "close": 99.5, "adjusted_close": 90.0},
    {"date": "2026-04-08", "open": 1, "high": 1, "low": 1, "close": 100.25, "adjusted_close": 91.0},
    {"date": "2026-05-06", "open": 1, "high": 1, "low": 1, "close": 101, "adjusted_close": 92.0},
]


@pytest.mark.parametrize(
    ("yahoo", "eodhd"),
    [("VUAG.L", "VUAG.LSE"), ("MU", "MU.US"), ("SAP.DE", "SAP.XETRA"), ("AIR.PA", "AIR.PA")],
)
def test_symbols_map_yahoo_suffixes_to_eodhd_exchanges(yahoo: str, eodhd: str) -> None:
    assert _eodhd_symbol(yahoo) == eodhd


def test_an_unmapped_exchange_is_not_guessed() -> None:
    assert _eodhd_symbol("XYZ.ZZ") is None


@pytest.mark.asyncio
async def test_one_request_covers_the_whole_window_with_raw_closes(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=BARS)

    provider = _provider(tmp_path, handler)
    result = await provider.fetch_daily_closes(
        requests=[PriceRequest("holding:VUAG", "VUAG.L", "GBP")],
        start_date=date(2026, 4, 8),
        end_date=date(2026, 5, 6),
    )
    await provider.aclose()

    [request] = seen
    assert request.url.path == "/api/eod/VUAG.LSE"
    assert request.url.params["from"] == "2026-04-08"
    assert request.url.params["to"] == "2026-05-06"
    assert request.url.params["fmt"] == "json"
    # Raw close (not adjusted), in the instrument's own currency, outside-window bars dropped.
    assert [(point.as_of_date, point.close_price) for point in result["holding:VUAG"]] == [
        (date(2026, 4, 8), Decimal("100.25")),
        (date(2026, 5, 6), Decimal("101")),
    ]
    assert {point.currency_code for point in result["holding:VUAG"]} == {"GBP"}


@pytest.mark.asyncio
async def test_unknown_or_unentitled_symbols_are_skipped_not_fatal(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("NOPE.US"):
            return httpx.Response(404, text="Ticker Not Found")
        if request.url.path.endswith("VUAG.LSE"):
            return httpx.Response(403, text="Forbidden")
        return httpx.Response(200, json=BARS)

    provider = _provider(tmp_path, handler)
    notes: dict[str, str] = {}
    result = await provider.fetch_daily_closes(
        requests=[
            PriceRequest("a", "NOPE", "USD"),
            PriceRequest("b", "VUAG.L", "GBP"),
            PriceRequest("c", "MU", "USD"),
        ],
        start_date=date(2026, 4, 1),
        end_date=date(2026, 5, 31),
        skip_notes=notes,
    )
    await provider.aclose()

    assert set(result) == {"c"}
    assert "not found" in notes["a"]
    assert "not included" in notes["b"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 402])
async def test_account_level_refusals_stop_the_fetch(tmp_path: Path, status: int) -> None:
    provider = _provider(tmp_path, lambda request: httpx.Response(status))

    with pytest.raises(MarketDataProviderError) as raised:
        await provider.fetch_daily_closes(
            requests=[PriceRequest("c", "MU", "USD")],
            start_date=date(2026, 4, 1),
            end_date=date(2026, 5, 31),
        )
    await provider.aclose()
    # The error names the provider and status, never the URL or the token.
    assert "eod-key" not in str(raised.value)


@pytest.mark.asyncio
async def test_no_currency_means_no_guess(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=BARS)

    provider = _provider(tmp_path, handler)
    notes: dict[str, str] = {}
    result = await provider.fetch_daily_closes(
        requests=[PriceRequest("x", "VUAG.L", None)],
        start_date=date(2026, 4, 1),
        end_date=date(2026, 5, 31),
        skip_notes=notes,
    )
    await provider.aclose()

    assert result == {} and calls == []
    assert "refusing to guess" in notes["x"]


def test_eodhd_can_be_the_fallback_behind_twelve_data(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        market_data_provider="twelvedata",
        market_data_api_key="td",
        market_data_fallback_provider="eodhd",
        market_data_fallback_api_key="eod",
    )

    provider = _build_market_data_provider(settings)

    assert isinstance(provider, CompositeMarketDataProvider)
    assert isinstance(provider._fallback, EodhdMarketDataProvider)
