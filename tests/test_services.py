"""The live positions read the dashboard uses: a short shared cache over Trading 212.

Trading 212 allows one positions request per second. Every page that shows holdings asks for
them, so without a cache ordinary clicking spent that budget and collided with syncs.
"""

from __future__ import annotations

import asyncio
from typing import cast

import pytest

from helios.client import Trading212Client
from helios.schemas import Position
from helios.services import Trading212Service


class CountingClient:
    def __init__(self) -> None:
        self.calls = 0

    async def get_positions(self) -> list[Position]:
        self.calls += 1
        await asyncio.sleep(0)
        return []


class ManualClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _service(client: CountingClient, clock: ManualClock) -> Trading212Service:
    return Trading212Service(cast(Trading212Client, client), cache_seconds=15.0, clock=clock)


@pytest.mark.asyncio
async def test_reads_within_the_window_reuse_one_upstream_call() -> None:
    client, clock = CountingClient(), ManualClock()
    service = _service(client, clock)

    await service.get_positions()
    clock.now += 14.9
    await service.get_positions()

    assert client.calls == 1


@pytest.mark.asyncio
async def test_the_cache_expires() -> None:
    client, clock = CountingClient(), ManualClock()
    service = _service(client, clock)

    await service.get_positions()
    clock.now += 15.0
    await service.get_positions()

    assert client.calls == 2


@pytest.mark.asyncio
async def test_concurrent_renders_share_one_call() -> None:
    """The Overview and Holdings rendering together must not race two requests upstream."""
    client, clock = CountingClient(), ManualClock()
    service = _service(client, clock)

    await asyncio.gather(*(service.get_positions() for _ in range(5)))

    assert client.calls == 1


@pytest.mark.asyncio
async def test_invalidate_forces_a_fresh_read() -> None:
    client, clock = CountingClient(), ManualClock()
    service = _service(client, clock)

    await service.get_positions()
    service.invalidate()
    await service.get_positions()

    assert client.calls == 2
