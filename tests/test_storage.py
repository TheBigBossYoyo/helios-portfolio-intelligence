"""Compressed raw columns, the 0017 migration, retention and VACUUM."""

from __future__ import annotations

import os
import sqlite3
import zlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from helios.compression import MAGIC, compress_text, decompress_text
from helios.config import Settings
from helios.db import migrate_database
from helios.models import NewsItem, RawNews, RawSnapshot
from helios.news import NewsSyncService, NewsSyncSummary
from helios.portfolio_repository import InstrumentNewsTarget
from helios.storage import StorageService, compact_database, storage_status

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def _alembic_config(settings: Settings) -> Config:
    project_root = Path(__file__).resolve().parents[1]
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.sqlite_url.replace("+aiosqlite", ""))
    return config


def _sync_url(settings: Settings) -> str:
    return settings.sqlite_url.replace("+aiosqlite", "")


def _raw_news(ts: datetime, body: str = "<rss>" + "x" * 2000 + "</rss>") -> RawNews:
    return RawNews(
        feed_key="yahoo",
        url="https://example.test/feed",
        ts=ts,
        http_status=200,
        content_type="application/rss+xml",
        body=body,
    )


def _snapshot(endpoint: str, ts: datetime, payload: object) -> RawSnapshot:
    return RawSnapshot(
        endpoint=endpoint,
        ts=ts,
        http_status=200,
        content_type="application/json",
        payload_json=payload,
    )


def _news_item(key: str, raw_id: int) -> NewsItem:
    return NewsItem(
        dedupe_key=key,
        feed_key="yahoo",
        provider="rss",
        source_label="Yahoo",
        headline=f"Headline {key}",
        url=f"https://example.test/{key}",
        canonical_url=f"https://example.test/{key}",
        title_key=f"headline {key}",
        published_at=NOW,
        fetched_at=NOW,
        raw_news_id=raw_id,
    )


def test_compression_round_trips_and_reads_legacy_values() -> None:
    text_value = "Café ✓ " * 500
    packed = compress_text(text_value)
    assert packed.startswith(MAGIC)
    assert len(packed) < len(text_value.encode()) / 10
    assert decompress_text(packed) == text_value
    # Rows written before compression come back unchanged, as str or bytes.
    assert decompress_text("plain") == "plain"
    assert decompress_text(b"plain") == "plain"


@pytest.mark.asyncio
async def test_raw_columns_are_stored_compressed_and_read_back_as_written(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, sqlite_filename="c.sqlite3")
    await migrate_database(settings)
    payload = {"items": [{"ticker": "AAPL_US_EQ", "name": "Apple"}] * 50}
    engine = create_sync_engine(_sync_url(settings))
    try:
        with Session(engine) as session, session.begin():
            session.add(_raw_news(NOW, body="<rss>é</rss>"))
            session.add(_snapshot("/equity/metadata/instruments", NOW, payload))
        with engine.connect() as connection:
            stored = connection.execute(text("SELECT payload_json FROM raw_snapshots")).scalar_one()
            body = connection.execute(text("SELECT body FROM raw_news")).scalar_one()
        assert isinstance(stored, bytes) and stored.startswith(MAGIC)
        assert isinstance(body, bytes) and body.startswith(MAGIC)
        with Session(engine) as session:
            assert session.scalars(select(RawSnapshot.payload_json)).one() == payload
            assert session.scalars(select(RawNews.body)).one() == "<rss>é</rss>"
    finally:
        engine.dispose()


def test_migration_compresses_existing_rows_and_downgrade_restores_them(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, sqlite_filename="m.sqlite3")
    settings.ensure_directories()
    config = _alembic_config(settings)
    command.upgrade(config, "0016_ai_run_kind")
    engine = create_sync_engine(_sync_url(settings))
    body = "<rss>" + "headline " * 300 + "</rss>"
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO raw_news (feed_key, url, ts, http_status, content_type, body) "
                    "VALUES ('yahoo', 'u', '2026-09-28 10:00:00.000000', 200, NULL, :body)"
                ),
                {"body": body},
            )
            connection.execute(
                text(
                    "INSERT INTO raw_snapshots"
                    " (endpoint, ts, http_status, content_type, payload_json) VALUES"
                    " ('/equity/positions', '2026-09-28 10:00:00.000000', 200, NULL, :p)"
                ),
                {"p": '[{"ticker":"AAPL_US_EQ"}]'},
            )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            stored = connection.execute(text("SELECT body FROM raw_news")).scalar_one()
        assert stored.startswith(MAGIC)
        assert zlib.decompress(stored[len(MAGIC) :]).decode() == body
        with Session(engine) as session:
            assert session.scalars(select(RawNews.body)).one() == body
            assert session.scalars(select(RawSnapshot.payload_json)).one() == [
                {"ticker": "AAPL_US_EQ"}
            ]
        # Re-running over already-compressed rows leaves them alone.
        command.downgrade(config, "0016_ai_run_kind")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT body FROM raw_news")).scalar_one() == body
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_compact_prunes_old_raw_news_and_unreplayed_snapshots(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, sqlite_filename="p.sqlite3")
    await migrate_database(settings)
    engine = create_sync_engine(_sync_url(settings))
    try:
        with Session(engine) as session, session.begin():
            old = _raw_news(NOW - timedelta(days=10))
            recent = _raw_news(NOW - timedelta(days=1))
            session.add_all([old, recent])
            session.flush()
            session.add_all([_news_item("old", old.id), _news_item("recent", recent.id)])
            recent_id = recent.id
            for day in range(4):
                ts = NOW - timedelta(days=day)
                session.add(_snapshot("/equity/metadata/instruments", ts, [{"day": day}]))
                session.add(_snapshot("/equity/history/orders", ts, [{"day": day}]))
            for hour in range(30):
                session.add(_snapshot("/api/v0/equity/positions", NOW - timedelta(hours=hour), []))
        result = compact_database(settings.sqlite_path, now=NOW, raw_news_retention_days=7)
        assert result.raw_news_pruned == 1
        # 2 of 4 catalogues and 6 of 30 position snapshots go; order history is never pruned.
        assert result.snapshots_pruned == 2 + 6
        with Session(engine) as session:
            assert session.scalars(select(RawNews.id)).all() == [recent_id]
            links = {
                key: raw_id
                for key, raw_id in session.execute(
                    select(NewsItem.dedupe_key, NewsItem.raw_news_id)
                ).all()
            }
            assert links == {"old": None, "recent": recent_id}
            endpoints = session.scalars(select(RawSnapshot.endpoint)).all()
            assert endpoints.count("/equity/history/orders") == 4
            assert endpoints.count("/equity/metadata/instruments") == 2
            kept = session.scalars(
                select(RawSnapshot.payload_json).where(
                    RawSnapshot.endpoint == "/equity/metadata/instruments"
                )
            ).all()
            # The two newest ids: days 2 and 3 were inserted last.
            assert sorted(cast(list[dict[str, int]], item)[0]["day"] for item in kept) == [2, 3]
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_vacuum_shrinks_the_file_once_enough_is_free(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, sqlite_filename="v.sqlite3")
    await migrate_database(settings)
    connection = sqlite3.connect(settings.sqlite_path)
    try:
        # Incompressible bodies so the file actually grows.
        rows = [
            ("yahoo", "u", "2026-09-01 00:00:00.000000", 200, os.urandom(50_000))
            for _ in range(200)
        ]
        connection.executemany(
            "INSERT INTO raw_news (feed_key, url, ts, http_status, body) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()
    grown = storage_status(settings.sqlite_path)
    assert grown.raw_news_rows == 200
    assert grown.database_bytes > 9_000_000

    service = StorageService(settings.model_copy(update={"raw_news_retention_days": 7}))
    result = await service.compact()

    assert result.raw_news_pruned == 200
    assert result.vacuumed is True
    assert result.bytes_after < result.bytes_before / 5
    after = service.status()
    assert after.raw_news_rows == 0
    assert after.free_bytes == 0


@pytest.mark.asyncio
async def test_nothing_to_free_skips_the_vacuum(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, sqlite_filename="n.sqlite3")
    await migrate_database(settings)
    result = compact_database(settings.sqlite_path, now=NOW, raw_news_retention_days=7)
    assert (result.raw_news_pruned, result.snapshots_pruned, result.vacuumed) == (0, 0, False)
    forced = compact_database(
        settings.sqlite_path, now=NOW, raw_news_retention_days=7, force_vacuum=True
    )
    assert forced.vacuumed is True


class _Targets:
    def __init__(self, tickers: list[str]) -> None:
        self.tickers = tickers

    async def list_instrument_news_targets(self) -> list[InstrumentNewsTarget]:
        return [
            InstrumentNewsTarget(t212_ticker=t, isin=None, yahoo_ticker=None, name=None)
            for t in self.tickers
        ]


class _CountingNews(NewsSyncService):
    runs = 0

    async def sync(self) -> NewsSyncSummary:
        self.runs += 1
        return await super().sync()


@pytest.mark.asyncio
async def test_news_after_a_sync_only_runs_when_holdings_changed(tmp_path: Path) -> None:
    repository = _Targets(["AAPL_US_EQ"])
    settings = Settings(data_dir=tmp_path, news_feeds_path=tmp_path / "feeds.yaml")
    service = _CountingNews(repository, settings)  # type: ignore[arg-type]

    assert await service.sync_if_targets_changed() is not None  # nothing seen yet
    assert await service.sync_if_targets_changed() is None
    repository.tickers.append("MSFT_US_EQ")
    assert await service.sync_if_targets_changed() is not None
    assert await service.sync_if_targets_changed() is None
    assert service.runs == 2
