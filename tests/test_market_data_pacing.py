"""Free price plans are metered per minute; failures must never leak the API key.

Found on a real first replay: fifteen symbols sent back to back hit Twelve Data's 8-per-minute
limit, the resulting HTTP 429 escaped as an unhandled exception, the dashboard showed "Internal
Server Error", and the exception text -- the full request URL, key included -- was written to the
API log.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from helios.app import create_app
from helios.config import Settings
from helios.dependencies import get_performance_replay_service
from helios.performance import (
    MarketDataProviderError,
    PriceRequest,
    RequestPacer,
    TwelveDataMarketDataProvider,
    _provider_get,
)

SECRET = "td-secret-key-0123456789"


class FakeTime:
    def __init__(self) -> None:
        self.now = 100.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.mark.asyncio
async def test_pacer_spaces_requests_and_never_waits_first() -> None:
    time = FakeTime()
    pacer = RequestPacer(7.6, clock=time.clock, sleep=time.sleep)

    await pacer.wait()
    time.now += 2.0
    await pacer.wait()
    time.now += 10.0
    await pacer.wait()

    assert time.slept == [pytest.approx(5.6)]


def _client(statuses: list[int]) -> tuple[httpx.AsyncClient, list[str]]:
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        status = statuses.pop(0)
        return httpx.Response(status, json={"values": []})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), urls


@pytest.mark.asyncio
async def test_a_429_waits_for_the_window_then_retries_once() -> None:
    time = FakeTime()
    client, urls = _client([429, 200])

    response = await _provider_get(
        client,
        "https://api.example/time_series",
        params={"symbol": "NVDA", "apikey": SECRET},
        provider="Twelve Data",
        pacer=RequestPacer(0),
        sleep=time.sleep,
    )

    assert response.status_code == 200
    assert len(urls) == 2
    assert time.slept == [61.0]


@pytest.mark.asyncio
async def test_a_persistent_429_explains_itself_without_the_key() -> None:
    time = FakeTime()
    client, _ = _client([429, 429])

    with pytest.raises(MarketDataProviderError) as caught:
        await _provider_get(
            client,
            "https://api.example/time_series",
            params={"symbol": "NVDA", "apikey": SECRET},
            provider="Twelve Data",
            pacer=RequestPacer(0),
            sleep=time.sleep,
        )

    message = str(caught.value)
    assert "rate-limiting" in message
    assert SECRET not in message
    assert "apikey" not in message
    # No chained exception carrying the URL either: that is what reached the log before.
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None or caught.value.__suppress_context__


@pytest.mark.asyncio
async def test_other_http_errors_name_only_the_status() -> None:
    client, _ = _client([500])

    with pytest.raises(MarketDataProviderError) as caught:
        await _provider_get(
            client,
            "https://api.example/time_series",
            params={"apikey": SECRET},
            provider="Twelve Data",
            pacer=RequestPacer(0),
        )

    assert str(caught.value) == "Twelve Data returned HTTP 500."


@pytest.mark.asyncio
async def test_london_symbols_are_not_sent_to_a_us_only_plan() -> None:
    """The free plan cannot serve London; asking spends one of its eight requests a minute."""
    settings = Settings(market_data_api_key=SecretStr(SECRET), twelvedata_exchanges="US")
    provider = TwelveDataMarketDataProvider(settings)
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.url.params["symbol"])
        return httpx.Response(200, json={"status": "ok", "meta": {"currency": "USD"}, "values": []})

    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    notes: dict[str, str] = {}

    await provider.fetch_daily_closes(
        requests=[
            PriceRequest("NVDA_US_EQ", "NVDA", "USD"),
            PriceRequest("VUAGl_EQ", "VUAG.L", "GBP"),
        ],
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 31),
        skip_notes=notes,
    )

    assert sent == ["NVDA"]
    assert "outside the configured plan" in notes["VUAGl_EQ"]


def test_replay_reports_a_provider_refusal_as_a_readable_502(tmp_path: Path) -> None:
    class RefusingReplay:
        async def replay(self) -> object:
            raise MarketDataProviderError("Twelve Data is rate-limiting requests (HTTP 429).")

    app = create_app(Settings(data_dir=tmp_path))
    app.dependency_overrides[get_performance_replay_service] = RefusingReplay

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/performance/replay", headers={"X-Helios-Local-Action": "replay"}
        )

    assert response.status_code == 502
    assert response.json() == {"detail": "Twelve Data is rate-limiting requests (HTTP 429)."}
