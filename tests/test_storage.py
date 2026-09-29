"""Lossless raw storage: zstd frames, delta chains, the 0018 migration and compaction."""

from __future__ import annotations

import os
import sqlite3
import zlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from helios.compression import (
    DELTA,
    KEYFRAME_INTERVAL,
    MAGIC,
    STANDALONE,
    RawDataCorruptError,
    compress_text,
    decode_bytes,
    decompress_text,
    encode_delta,
)
from helios.config import Settings
from helios.db import migrate_database
from helios.portfolio_repository import PortfolioRepository
from helios.raw_snapshots import JsonValue, RawSnapshotRepository
from helios.raw_store import RawNewsRecord, read_raw_news_body, read_raw_snapshot
from helios.storage import StorageService, compact_database, storage_status

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def _alembic_config(settings: Settings) -> Config:
    project_root = Path(__file__).resolve().parents[1]
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.sqlite_url.replace("+aiosqlite", ""))
    return config


async def _factory(settings: Settings) -> async_sessionmaker[AsyncSession]:
    await migrate_database(settings)
    engine = create_async_engine(settings.sqlite_url, poolclass=NullPool)
    return async_sessionmaker(engine, expire_on_commit=False)


def _feed(day: int, items: int = 40) -> str:
    """A feed that changes a little between fetches, like a real one."""

    entries = "".join(
        f"<item><title>Story {n} about the market</title><link>https://news.test/{n}</link>"
        f"<pubDate>2026-09-{(n % 28) + 1:02d}</pubDate></item>"
        for n in range(day, day + items)
    )
    return f"<rss><channel><title>Markets</title>{entries}</channel></rss>"


def test_frames_round_trip_and_older_formats_still_read() -> None:
    text_value = "Café ✓ " * 500
    packed = compress_text(text_value)
    assert packed.startswith(STANDALONE)
    assert len(packed) < len(text_value.encode()) / 10
    assert decompress_text(packed) == text_value
    # zlib rows from migration 0017, and rows from before any compression.
    assert decompress_text(MAGIC + zlib.compress(b"legacy")) == "legacy"
    assert decompress_text(b"plain") == "plain"
    assert decompress_text("plain") == "plain"


def test_a_delta_needs_its_base_and_a_damaged_frame_is_caught() -> None:
    base = _feed(0).encode()
    content = _feed(1).encode()
    delta = encode_delta(content, base)
    assert delta.startswith(DELTA)
    assert len(delta) < len(content) / 20
    assert decode_bytes(delta, base) == content
    with pytest.raises(RawDataCorruptError):
        decode_bytes(delta)
    damaged = delta[:-6] + bytes(6)
    with pytest.raises(RawDataCorruptError):
        decode_bytes(damaged, base)


@pytest.mark.asyncio
async def test_news_bodies_are_stored_as_deltas_and_read_back_exactly(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, sqlite_filename="n.sqlite3")
    factory = await _factory(settings)
    repository = PortfolioRepository(factory)
    bodies = [_feed(day) for day in range(KEYFRAME_INTERVAL + 5)]
    ids = [
        await repository.insert_raw_news(
            RawNewsRecord(
                feed_key="yahoo",
                url="https://feeds.test/aapl",
                ts=NOW + timedelta(hours=index),
                http_status=200,
                content_type="application/rss+xml",
                body=body,
            )
        )
        for index, body in enumerate(bodies)
    ]
    # A second feed is its own stream: its first row is a keyframe, not a delta of Yahoo's.
    other = await repository.insert_raw_news(
        RawNewsRecord(
            feed_key="sec",
            url="https://feeds.test/sec",
            ts=NOW,
            http_status=200,
            content_type=None,
            body=_feed(3),
        )
    )

    stored = await repository.list_raw_news()
    assert [record.body for record in stored] == [*bodies, _feed(3)]
    assert [record.id for record in stored] == [*ids, other]

    connection = sqlite3.connect(settings.sqlite_path)
    try:
        rows = connection.execute(
            "SELECT id, delta_base_id, substr(body, 1, 3), length(body) FROM raw_news ORDER BY id"
        ).fetchall()
    finally:
        connection.close()
    kinds = [row[2] for row in rows]
    assert kinds[0] == STANDALONE
    assert kinds[1] == DELTA and rows[1][1] == ids[0]
    # A fresh keyframe every KEYFRAME_INTERVAL rows keeps every chain short.
    assert kinds[KEYFRAME_INTERVAL] == STANDALONE
    assert kinds[-1] == STANDALONE and rows[-1][1] is None
    raw_total = sum(len(body.encode()) for body in bodies)
    assert sum(row[3] for row in rows[:-1]) < raw_total / 30

    async with factory() as session:
        assert await read_raw_news_body(session, ids[KEYFRAME_INTERVAL - 1]) == bodies[-6]


@pytest.mark.asyncio
async def test_snapshots_round_trip_through_their_deltas(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, sqlite_filename="s.sqlite3")
    factory = await _factory(settings)
    snapshots = RawSnapshotRepository(factory)
    payloads: list[JsonValue] = [
        [
            {"ticker": f"T{n}_US_EQ", "quantity": str(n * day), "name": "Ünïcode Corp"}
            for n in range(50)
        ]
        for day in range(6)
    ]
    for day, payload in enumerate(payloads):
        await snapshots.append_snapshot(
            endpoint="/equity/positions",
            recorded_at=NOW + timedelta(days=day),
            http_status=200,
            content_type="application/json",
            payload=payload,
        )

    records = await snapshots.list_snapshots()
    assert [record.payload_json for record in records] == payloads
    async with factory() as session:
        assert await read_raw_snapshot(session, records[3].id or 0) == payloads[3]


def test_migration_delta_encodes_existing_rows_losslessly_and_downgrades(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, sqlite_filename="m.sqlite3")
    settings.ensure_directories()
    config = _alembic_config(settings)
    command.upgrade(config, "0016_ai_run_kind")
    engine = create_sync_engine(settings.sqlite_url.replace("+aiosqlite", ""))
    bodies = [_feed(day) for day in range(10)]
    try:
        with engine.begin() as connection:
            for index, body in enumerate(bodies):
                connection.execute(
                    text(
                        "INSERT INTO raw_news (feed_key, url, ts, http_status, content_type, body)"
                        " VALUES ('yahoo', 'u', :ts, 200, NULL, :body)"
                    ),
                    {"ts": f"2026-09-{index + 1:02d} 10:00:00.000000", "body": body},
                )
            connection.execute(
                text(
                    "INSERT INTO raw_snapshots"
                    " (endpoint, ts, http_status, content_type, payload_json) VALUES"
                    " ('/equity/positions', '2026-09-28 10:00:00.000000', 200, NULL, :p)"
                ),
                {"p": '[{"ticker": "AAPL_US_EQ"}]'},
            )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            rows = connection.execute(
                text("SELECT substr(body, 1, 3), delta_base_id FROM raw_news ORDER BY id")
            ).all()
            snapshot = connection.execute(text("SELECT payload_json FROM raw_snapshots")).scalar()
        assert tuple(rows[0]) == (STANDALONE, None)
        assert all(kind == DELTA for kind, _base in rows[1:])
        # The snapshot keeps its exact original bytes, spacing included.
        assert isinstance(snapshot, bytes)
        assert decode_bytes(snapshot) == b'[{"ticker": "AAPL_US_EQ"}]'

        command.downgrade(config, "0016_ai_run_kind")
        with engine.connect() as connection:
            restored = connection.execute(text("SELECT body FROM raw_news ORDER BY id")).scalars()
            assert list(restored) == bodies
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_compaction_returns_free_space_and_never_removes_a_row(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, sqlite_filename="v.sqlite3")
    await migrate_database(settings)
    connection = sqlite3.connect(settings.sqlite_path)
    try:
        connection.executemany(
            "INSERT INTO raw_news (feed_key, url, ts, http_status, body) VALUES (?, ?, ?, ?, ?)",
            [("yahoo", "u", "2026-09-01 00:00:00", 200, os.urandom(50_000)) for _ in range(200)],
        )
        connection.commit()
        # Rewrite the rows smaller, as the migration does: the file keeps its size until
        # compaction hands the space back.
        connection.execute("UPDATE raw_news SET body = substr(body, 1, 100)")
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()
    grown = storage_status(settings.sqlite_path)
    assert grown.free_bytes > 8_000_000

    service = StorageService(settings)
    result = await service.compact()

    assert result.vacuumed is True
    assert result.bytes_after < result.bytes_before / 5
    after = service.status()
    assert after.raw_news_rows == 200
    assert after.free_bytes == 0
    connection = sqlite3.connect(settings.sqlite_path)
    try:
        assert connection.execute("PRAGMA auto_vacuum").fetchone()[0] == 2
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_compaction_with_nothing_free_only_checkpoints(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, sqlite_filename="q.sqlite3")
    await migrate_database(settings)
    first = compact_database(settings.sqlite_path)  # switches to incremental auto-vacuum
    assert first.vacuumed is True
    second = compact_database(settings.sqlite_path)
    assert second.vacuumed is False
    assert compact_database(settings.sqlite_path, force_vacuum=True).vacuumed is True
