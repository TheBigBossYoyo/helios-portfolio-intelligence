from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
import pytest

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
        t212_api_secret="secret",
        data_dir="data",
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
