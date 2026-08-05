from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from helios.client import (
    Trading212Client,
    Trading212HTTPError,
    Trading212MethodNotAllowedError,
    Trading212ParseError,
    build_basic_auth_header,
)
from helios.config import Settings, T212Credentials
from helios.rate_limit import Clock, EndpointLimiter, default_rate_limit_policies
from helios.raw_snapshots import JsonValue, SnapshotWriter


class MemorySnapshotWriter(SnapshotWriter):
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def append_snapshot(
        self,
        *,
        endpoint: str,
        recorded_at: datetime,
        http_status: int,
        content_type: str | None,
        payload: JsonValue,
    ) -> None:
        self.calls.append(
            {
                "endpoint": endpoint,
                "recorded_at": recorded_at,
                "http_status": http_status,
                "content_type": content_type,
                "payload": payload,
            }
        )


@dataclass
class FakeClock(Clock):
    current: float = 0.0
    slept: list[float] = field(default_factory=list)

    def now(self) -> float:
        return self.current

    def utcnow(self) -> datetime:
        return datetime(2024, 1, 1, tzinfo=UTC) + timedelta(seconds=self.current)

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.current += seconds


def make_settings() -> Settings:
    return Settings(
        t212_api_key="key",
        t212_api_secret=SecretStr("secret"),
        data_dir=Path("data"),
        t212_max_retries=2,
    )


def test_build_basic_auth_header() -> None:
    header = build_basic_auth_header(T212Credentials(api_key="hello", api_secret="world"))
    assert header == "Basic aGVsbG86d29ybGQ="


@pytest.mark.asyncio
async def test_client_rejects_non_get_requests() -> None:
    client = Trading212Client(settings=make_settings(), snapshot_writer=MemorySnapshotWriter())

    with pytest.raises(Trading212MethodNotAllowedError):
        await client.request_json("POST", "/equity/history/exports")

    await client.aclose()


@pytest.mark.asyncio
async def test_snapshot_persisted_before_parse_failure() -> None:
    writer = MemorySnapshotWriter()

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json", headers={"content-type": "text/plain"})

    client = Trading212Client(
        settings=make_settings(),
        snapshot_writer=writer,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://demo.trading212.com"),
    )

    with pytest.raises(Trading212ParseError):
        await client.request_json("GET", "/equity/positions")

    assert writer.calls[0]["http_status"] == 200
    assert writer.calls[0]["payload"] == {
        "kind": "non_json",
        "contentType": "text/plain",
        "bodyBase64": "bm90LWpzb24=",
    }


@pytest.mark.asyncio
async def test_snapshot_persisted_before_status_failure() -> None:
    writer = MemorySnapshotWriter()

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "forbidden"})

    client = Trading212Client(
        settings=make_settings(),
        snapshot_writer=writer,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://demo.trading212.com"),
    )

    with pytest.raises(Trading212HTTPError):
        await client.request_json("GET", "/equity/positions")

    assert writer.calls[0]["http_status"] == 403
    assert writer.calls[0]["payload"] == {"error": "forbidden"}


@pytest.mark.asyncio
async def test_client_uses_header_aware_backoff_and_retries() -> None:
    writer = MemorySnapshotWriter()
    clock = FakeClock()
    attempts = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(
                429,
                json={"error": "slow down"},
                headers={
                    "retry-after": "2",
                    "x-ratelimit-reset": str(int(clock.utcnow().timestamp()) + 2),
                    "x-ratelimit-remaining": "0",
                },
            )
        return httpx.Response(200, json=[])

    client = Trading212Client(
        settings=make_settings(),
        snapshot_writer=writer,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://demo.trading212.com"),
        clock=clock,
        limiter=EndpointLimiter(clock, default_rate_limit_policies()),
    )

    result = await client.get_positions()

    assert result == []
    assert clock.slept == [2.0]
    assert len(writer.calls) == 2


@pytest.mark.asyncio
async def test_client_retries_transport_errors() -> None:
    writer = MemorySnapshotWriter()
    clock = FakeClock()
    attempts = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, json=[])

    client = Trading212Client(
        settings=make_settings(),
        snapshot_writer=writer,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://demo.trading212.com"),
        clock=clock,
    )

    result = await client.get_positions()

    assert result == []
    assert clock.slept == [0.5]


@pytest.mark.asyncio
async def test_history_orders_follow_next_page_path_and_snapshot_each_page() -> None:
    writer = MemorySnapshotWriter()
    seen_urls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        if (
            request.url.path == "/api/v0/equity/history/orders"
            and request.url.params.get("cursor") == "1"
        ):
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "fill": {
                                "id": "fill-2",
                                "quantity": "2.0",
                                "price": "101.00",
                                "filledAt": "2024-01-02T00:00:00Z",
                                "type": "TRADE",
                                "walletImpact": {"netValue": "202.00"},
                            },
                            "order": {
                                "id": "order-2",
                                "instrument": {"ticker": "AAPL_US_EQ", "currency": "USD"},
                                "side": "SELL",
                                "type": "MARKET",
                            },
                        }
                    ],
                    "nextPagePath": None,
                },
            )
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "fill": {
                            "id": "fill-1",
                            "quantity": "1.0",
                            "price": "100.00",
                            "filledAt": "2024-01-01T00:00:00Z",
                            "type": "TRADE",
                            "walletImpact": {"netValue": "100.00"},
                        },
                        "order": {
                            "id": "order-1",
                            "instrument": {"ticker": "AAPL_US_EQ", "currency": "USD"},
                            "side": "BUY",
                            "type": "MARKET",
                        },
                    }
                ],
                "nextPagePath": "/api/v0/equity/history/orders?cursor=1",
            },
        )

    client = Trading212Client(
        settings=make_settings(),
        snapshot_writer=writer,
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://demo.trading212.com/api/v0",
        ),
    )

    items = await client.get_history_orders()

    assert [item.fill.id for item in items] == ["fill-1", "fill-2"]
    assert seen_urls == [
        "https://demo.trading212.com/api/v0/equity/history/orders?limit=50",
        "https://demo.trading212.com/api/v0/equity/history/orders?cursor=1",
    ]
    assert [call["endpoint"] for call in writer.calls] == [
        "/equity/history/orders",
        "/api/v0/equity/history/orders?cursor=1",
    ]


@pytest.mark.asyncio
async def test_history_transactions_stops_on_nullable_terminal_next_page_path() -> None:
    writer = MemorySnapshotWriter()

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "reference": "txn-1",
                        "amount": "12.34",
                        "currency": "EUR",
                        "dateTime": "2024-02-03T04:05:06Z",
                        "type": "DEPOSIT",
                    }
                ],
                "nextPagePath": None,
            },
        )

    client = Trading212Client(
        settings=make_settings(),
        snapshot_writer=writer,
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://demo.trading212.com/api/v0",
        ),
    )

    items = await client.get_history_transactions()

    assert [item.reference for item in items] == ["txn-1"]
    assert len(writer.calls) == 1
    assert writer.calls[0]["endpoint"] == "/equity/history/transactions"


@pytest.mark.asyncio
async def test_pagination_rejects_external_next_page_url() -> None:
    writer = MemorySnapshotWriter()

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"items": [], "nextPagePath": "https://attacker.example/steal"},
        )

    client = Trading212Client(
        settings=make_settings(),
        snapshot_writer=writer,
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://demo.trading212.com/api/v0",
        ),
    )

    with pytest.raises(Trading212ParseError, match="escaped the API origin"):
        await client.get_history_orders()


@pytest.mark.asyncio
async def test_pagination_rejects_repeat_next_page_path_cycle() -> None:
    writer = MemorySnapshotWriter()
    seen_urls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(
            200,
            json={"items": [], "nextPagePath": "/api/v0/equity/history/orders?cursor=1"},
        )

    client = Trading212Client(
        settings=make_settings(),
        snapshot_writer=writer,
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://demo.trading212.com/api/v0",
        ),
    )

    with pytest.raises(Trading212ParseError, match="pagination cycle"):
        await client.get_history_orders()

    assert seen_urls == [
        "https://demo.trading212.com/api/v0/equity/history/orders?limit=50",
        "https://demo.trading212.com/api/v0/equity/history/orders?cursor=1",
    ]
    assert len(writer.calls) == 2


@pytest.mark.asyncio
async def test_pagination_rejects_excessive_page_count(monkeypatch: pytest.MonkeyPatch) -> None:
    writer = MemorySnapshotWriter()
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={"items": [], "nextPagePath": f"/api/v0/equity/history/orders?cursor={calls}"},
        )

    monkeypatch.setattr("helios.client.MAX_HISTORY_PAGES", 2)
    client = Trading212Client(
        settings=make_settings(),
        snapshot_writer=writer,
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://demo.trading212.com/api/v0",
        ),
    )

    with pytest.raises(Trading212ParseError, match="maximum page count"):
        await client.get_history_orders()

    assert calls == 2
    assert len(writer.calls) == 2
