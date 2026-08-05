from __future__ import annotations

from .client import Trading212Client
from .schemas import Position


class Trading212Service:
    def __init__(self, client: Trading212Client) -> None:
        self._client = client

    async def get_positions(self) -> list[Position]:
        return await self._client.get_positions()
