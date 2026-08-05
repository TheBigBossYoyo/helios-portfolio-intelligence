from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .models import RawSnapshot

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]


class SnapshotWriter(Protocol):
    async def append_snapshot(
        self,
        *,
        endpoint: str,
        recorded_at: datetime,
        http_status: int,
        content_type: str | None,
        payload: JsonValue,
    ) -> None: ...


class RawSnapshotRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def append_snapshot(
        self,
        *,
        endpoint: str,
        recorded_at: datetime,
        http_status: int,
        content_type: str | None,
        payload: JsonValue,
    ) -> None:
        async with self._session_factory() as session:
            session.add(
                RawSnapshot(
                    endpoint=endpoint,
                    ts=recorded_at,
                    http_status=http_status,
                    content_type=content_type,
                    payload_json=payload,
                )
            )
            await session.commit()


def encode_payload(content: bytes, content_type: str | None) -> JsonValue:
    try:
        decoded = json.loads(content)
    except json.JSONDecodeError:
        return {
            "kind": "non_json",
            "contentType": content_type,
            "bodyBase64": base64.b64encode(content).decode("ascii"),
        }
    return normalize_json(decoded)


def normalize_json(value: object) -> JsonValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [normalize_json(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): normalize_json(item) for key, item in value.items()}
    raise TypeError(f"Unsupported JSON value type: {type(value)!r}")
