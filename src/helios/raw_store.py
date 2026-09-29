"""Reading and writing the raw tables through their delta encoding (see ``helios.compression``).

Callers deal in plain records -- the body text, the JSON payload -- and never see a frame. A new
row is encoded against the newest row of its stream when that is smaller; reading decodes along
``delta_base_id`` back to the stream's last keyframe.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from .compression import (
    KEYFRAME_INTERVAL,
    RawDataCorruptError,
    decode_bytes,
    encode_delta,
    encode_standalone,
    json_bytes,
    stream_key,
)
from .models import RawNews, RawSnapshot

#: Keep a delta only when it is well under this share of a standalone frame's input.
DELTA_WORTH_IT = 0.125


@dataclass(frozen=True)
class RawNewsRecord:
    feed_key: str
    url: str
    ts: datetime
    http_status: int
    content_type: str | None
    body: str
    id: int | None = None


@dataclass(frozen=True)
class RawSnapshotRecord:
    endpoint: str
    ts: datetime
    http_status: int
    content_type: str | None
    payload_json: object
    id: int | None = None


def news_stream(feed_key: str, url: str) -> str:
    return stream_key("news", feed_key, url)


def snapshot_stream(endpoint: str) -> str:
    return stream_key("t212", endpoint)


type RawModel = type[RawNews] | type[RawSnapshot]


def _blob_column(model: RawModel) -> InstrumentedAttribute[bytes]:
    return RawNews.body_blob if model is RawNews else RawSnapshot.payload_blob


async def _decode_row(session: AsyncSession, model: RawModel, row_id: int) -> tuple[bytes, int]:
    """(content, depth): the decoded row and how many deltas it sits past a keyframe."""

    chain: list[bytes] = []
    current: int | None = row_id
    while current is not None:
        found = (
            await session.execute(
                select(_blob_column(model), model.delta_base_id).where(model.id == current)
            )
        ).first()
        if found is None:
            raise RawDataCorruptError(f"{model.__tablename__} row {current} is missing")
        blob, base_id = found
        chain.append(blob)
        current = base_id
        if len(chain) > KEYFRAME_INTERVAL * 4:
            raise RawDataCorruptError(f"{model.__tablename__} row {row_id}: delta chain too long")
    content: bytes | None = None
    for blob in reversed(chain):
        content = decode_bytes(blob, content)
    assert content is not None
    return content, len(chain) - 1


async def _encode(
    session: AsyncSession, model: RawModel, key: str, content: bytes
) -> tuple[bytes, int | None]:
    latest = await session.scalar(
        select(model.id).where(model.stream_key == key).order_by(model.id.desc()).limit(1)
    )
    if latest is not None:
        try:
            base, depth = await _decode_row(session, model, latest)
        except RawDataCorruptError:
            base, depth = b"", KEYFRAME_INTERVAL  # never build on a damaged row
        if depth + 1 < KEYFRAME_INTERVAL:
            delta = encode_delta(content, base)
            if len(delta) <= max(len(content), 1) * DELTA_WORTH_IT:
                return delta, latest
            standalone = encode_standalone(content)
            return (delta, latest) if len(delta) < len(standalone) else (standalone, None)
    return encode_standalone(content), None


async def add_raw_news(session: AsyncSession, record: RawNewsRecord) -> RawNews:
    key = news_stream(record.feed_key, record.url)
    blob, base_id = await _encode(session, RawNews, key, record.body.encode("utf-8"))
    row = RawNews(
        feed_key=record.feed_key,
        url=record.url,
        ts=record.ts,
        http_status=record.http_status,
        content_type=record.content_type,
        body_blob=blob,
        stream_key=key,
        delta_base_id=base_id,
    )
    session.add(row)
    return row


async def add_raw_snapshot(session: AsyncSession, record: RawSnapshotRecord) -> RawSnapshot:
    key = snapshot_stream(record.endpoint)
    blob, base_id = await _encode(session, RawSnapshot, key, json_bytes(record.payload_json))
    row = RawSnapshot(
        endpoint=record.endpoint,
        ts=record.ts,
        http_status=record.http_status,
        content_type=record.content_type,
        payload_blob=blob,
        stream_key=key,
        delta_base_id=base_id,
    )
    session.add(row)
    return row


class _Decoder:
    """Decodes a whole table read in id order, remembering each stream's latest content.

    A row's base is (almost always) the previous row of its stream, so holding one decoded
    body per stream makes a full read one decompression per row.
    """

    def __init__(self, rows: Iterable[tuple[int, bytes, int | None]]) -> None:
        self._rows = {row_id: (blob, base) for row_id, blob, base in rows}
        self._memo: dict[int, bytes] = {}
        self._latest: dict[str, int] = {}

    def decode(self, row_id: int) -> bytes:
        chain: list[int] = []
        current: int | None = row_id
        while current is not None and current not in self._memo:
            if current not in self._rows:
                raise RawDataCorruptError(f"row {row_id}: base row {current} is missing")
            chain.append(current)
            current = self._rows[current][1]
        content = self._memo.get(current) if current is not None else None
        for link in reversed(chain):
            content = decode_bytes(self._rows[link][0], content)
            self._memo[link] = content
        assert content is not None
        return content

    def keep_latest(self, stream: str | None, row_id: int) -> None:
        """Drop decoded bodies no later row of any stream will be based on."""

        if stream is not None:
            previous = self._latest.get(stream)
            self._latest[stream] = row_id
            if previous is not None:
                self._memo.pop(previous, None)
        wanted = set(self._latest.values())
        if len(self._memo) > len(wanted) * 2 + 64:
            self._memo = {key: value for key, value in self._memo.items() if key in wanted}


async def list_raw_news(session: AsyncSession) -> list[RawNewsRecord]:
    rows = (await session.execute(select(RawNews).order_by(RawNews.id))).scalars().all()
    decoder = _Decoder((row.id, row.body_blob, row.delta_base_id) for row in rows)
    records: list[RawNewsRecord] = []
    for row in rows:
        records.append(
            RawNewsRecord(
                id=row.id,
                feed_key=row.feed_key,
                url=row.url,
                ts=row.ts,
                http_status=row.http_status,
                content_type=row.content_type,
                body=decoder.decode(row.id).decode("utf-8"),
            )
        )
        decoder.keep_latest(row.stream_key, row.id)
    return records


async def list_raw_snapshots(session: AsyncSession) -> list[RawSnapshotRecord]:
    rows = (await session.execute(select(RawSnapshot).order_by(RawSnapshot.id))).scalars().all()
    decoder = _Decoder((row.id, row.payload_blob, row.delta_base_id) for row in rows)
    records: list[RawSnapshotRecord] = []
    for row in rows:
        records.append(
            RawSnapshotRecord(
                id=row.id,
                endpoint=row.endpoint,
                ts=row.ts,
                http_status=row.http_status,
                content_type=row.content_type,
                payload_json=json.loads(decoder.decode(row.id)),
            )
        )
        decoder.keep_latest(row.stream_key, row.id)
    return records


async def read_raw_news_body(session: AsyncSession, row_id: int) -> str:
    content, _depth = await _decode_row(session, RawNews, row_id)
    return content.decode("utf-8")


async def read_raw_snapshot(session: AsyncSession, row_id: int) -> object:
    content, _depth = await _decode_row(session, RawSnapshot, row_id)
    return json.loads(content)
