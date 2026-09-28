from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Final

from .client import Trading212Client
from .schemas import AccountSummary, Position

#: How long a live positions read is reused. Trading 212 allows one positions request per
#: second, and every dashboard page that shows holdings asks for them; without this, clicking
#: between pages (or the Overview and Holdings rendering together) spent that budget and, on a
#: real account, collided with the worker's sync. Fifteen seconds is invisible to a person and
#: still "live" by any reasonable reading.
POSITIONS_CACHE_SECONDS: Final = 15.0


class Trading212Service:
    """Dashboard-facing reads of live Trading 212 state, with a short shared cache."""

    def __init__(
        self,
        client: Trading212Client,
        *,
        cache_seconds: float = POSITIONS_CACHE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._cache_seconds = cache_seconds
        self._clock = clock
        self._cached: tuple[float, list[Position]] | None = None
        self._cached_summary: tuple[float, AccountSummary] | None = None
        # Concurrent requests share one upstream call instead of racing to make several.
        self._lock = asyncio.Lock()

    async def get_positions(self) -> list[Position]:
        async with self._lock:
            now = self._clock()
            if self._cached is not None and now - self._cached[0] < self._cache_seconds:
                return self._cached[1]
            positions = await self._client.get_positions()
            self._cached = (self._clock(), positions)
            return positions

    async def get_account_summary(self) -> AccountSummary:
        """The account totals, with the same short shared cache as positions."""

        async with self._lock:
            now = self._clock()
            if (
                self._cached_summary is not None
                and now - self._cached_summary[0] < self._cache_seconds
            ):
                return self._cached_summary[1]
            summary = await self._client.get_account_summary()
            self._cached_summary = (self._clock(), summary)
            return summary

    def invalidate(self) -> None:
        """Forget the cached reads, e.g. after a sync has just fetched fresher state."""

        self._cached = None
        self._cached_summary = None
