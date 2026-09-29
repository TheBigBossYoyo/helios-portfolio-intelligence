"""Keeping the database small: retention for raw data nothing replays, and VACUUM.

Raw-first storage keeps the exact bytes every source served, which is what makes a parser fix
re-runnable. Two kinds of raw rows are only ever needed recently:

* **Feed bodies** (``raw_news``). The articles parsed out of them live on in ``news_items``; the
  bodies only matter for re-parsing recent fetches, so rows older than
  ``raw_news_retention_days`` are dropped (their articles keep everything but the link back).
* **Reference and live-state snapshots.** ``/equity/metadata/instruments`` is a full catalogue
  (several MB each) where only the newest matters, and ``/equity/positions`` is live state the
  reparse never replays. The newest few of each are kept.

History snapshots (orders, transactions, dividends) are the ledger's record of truth and are
never pruned.

Deleting rows frees pages inside the file but does not shrink it; ``VACUUM`` does, once enough of
the file is free to be worth rewriting it. VACUUM needs a moment with no other writer, so it
retries briefly and otherwise waits for the next run.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .config import Settings
from .portfolio_repository import METADATA_ENDPOINT
from .portfolio_sync import POSITIONS_ENDPOINT
from .rate_limit import Clock, SystemClock
from .t212_reparse import classify_endpoint

#: How many of the newest snapshots to keep for endpoints nothing replays.
KEEP_LATEST_SNAPSHOTS = {METADATA_ENDPOINT: 2, POSITIONS_ENDPOINT: 24}
#: VACUUM once this share of the file is free pages...
VACUUM_FREE_RATIO = 0.2
#: ...and at least this much would be returned to the disk.
VACUUM_MIN_FREE_BYTES = 4 * 1024 * 1024
VACUUM_ATTEMPTS = 3
VACUUM_RETRY_SECONDS = 2.0
LOCK_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class StorageStatus:
    database_bytes: int
    wal_bytes: int
    free_bytes: int
    raw_news_rows: int
    raw_snapshot_rows: int

    @property
    def total_bytes(self) -> int:
        return self.database_bytes + self.wal_bytes


@dataclass(frozen=True)
class CompactResult:
    raw_news_pruned: int
    snapshots_pruned: int
    vacuumed: bool
    bytes_before: int
    bytes_after: int


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


def _wal_path(path: Path) -> Path:
    return path.with_name(path.name + "-wal")


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=LOCK_TIMEOUT_SECONDS, isolation_level=None)
    connection.execute(f"PRAGMA busy_timeout = {int(LOCK_TIMEOUT_SECONDS * 1000)}")
    return connection


def _free_bytes(connection: sqlite3.Connection) -> tuple[int, int]:
    """(free bytes, total bytes) inside the main database file."""

    page_size = connection.execute("PRAGMA page_size").fetchone()[0]
    page_count = connection.execute("PRAGMA page_count").fetchone()[0]
    freelist = connection.execute("PRAGMA freelist_count").fetchone()[0]
    return freelist * page_size, page_count * page_size


def _count(connection: sqlite3.Connection, table: str) -> int:
    try:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except sqlite3.OperationalError:
        return 0


def storage_status(path: Path) -> StorageStatus:
    if not path.exists():
        return StorageStatus(0, 0, 0, 0, 0)
    connection = _connect(path)
    try:
        free, _total = _free_bytes(connection)
        return StorageStatus(
            database_bytes=_file_size(path),
            wal_bytes=_file_size(_wal_path(path)),
            free_bytes=free,
            raw_news_rows=_count(connection, "raw_news"),
            raw_snapshot_rows=_count(connection, "raw_snapshots"),
        )
    finally:
        connection.close()


def prune_raw_news(connection: sqlite3.Connection, *, before: datetime) -> int:
    cutoff = before.astimezone(UTC).replace(tzinfo=None).isoformat(sep=" ", timespec="microseconds")
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            "UPDATE news_items SET raw_news_id = NULL "
            "WHERE raw_news_id IN (SELECT id FROM raw_news WHERE ts < ?)",
            (cutoff,),
        )
        deleted = connection.execute("DELETE FROM raw_news WHERE ts < ?", (cutoff,)).rowcount
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    connection.execute("COMMIT")
    return int(deleted)


def prune_snapshots(connection: sqlite3.Connection, keep: dict[str, int]) -> int:
    """Delete all but the newest ``keep[endpoint]`` snapshots of each listed endpoint."""

    ids_by_endpoint: dict[str, list[int]] = {}
    for row_id, endpoint in connection.execute("SELECT id, endpoint FROM raw_snapshots"):
        _category, label = classify_endpoint(endpoint)
        if label in keep:
            ids_by_endpoint.setdefault(label, []).append(int(row_id))
    doomed = [
        row_id
        for label, ids in ids_by_endpoint.items()
        for row_id in sorted(ids, reverse=True)[keep[label] :]
    ]
    if not doomed:
        return 0
    connection.execute("BEGIN IMMEDIATE")
    try:
        for start in range(0, len(doomed), 500):
            chunk = doomed[start : start + 500]
            placeholders = ",".join("?" * len(chunk))
            connection.execute(f"DELETE FROM raw_snapshots WHERE id IN ({placeholders})", chunk)
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    connection.execute("COMMIT")
    return len(doomed)


def vacuum_if_worth_it(connection: sqlite3.Connection, *, force: bool = False) -> bool:
    free, total = _free_bytes(connection)
    worth_it = free >= VACUUM_MIN_FREE_BYTES and total > 0 and free / total >= VACUUM_FREE_RATIO
    if not (force or worth_it):
        return False
    for attempt in range(VACUUM_ATTEMPTS):
        try:
            connection.execute("VACUUM")
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            return True
        except sqlite3.OperationalError:
            # Another connection is mid-write; try again shortly, else leave it for next time.
            if attempt + 1 < VACUUM_ATTEMPTS:
                time.sleep(VACUUM_RETRY_SECONDS)
    return False


def compact_database(
    path: Path,
    *,
    now: datetime,
    raw_news_retention_days: int,
    force_vacuum: bool = False,
) -> CompactResult:
    before_bytes = _file_size(path) + _file_size(_wal_path(path))
    connection = _connect(path)
    try:
        news = prune_raw_news(connection, before=now - timedelta(days=raw_news_retention_days))
        snapshots = prune_snapshots(connection, KEEP_LATEST_SNAPSHOTS)
        vacuumed = vacuum_if_worth_it(connection, force=force_vacuum)
    finally:
        connection.close()
    return CompactResult(
        raw_news_pruned=news,
        snapshots_pruned=snapshots,
        vacuumed=vacuumed,
        bytes_before=before_bytes,
        bytes_after=_file_size(path) + _file_size(_wal_path(path)),
    )


class StorageService:
    def __init__(self, settings: Settings, clock: Clock | None = None) -> None:
        self._settings = settings
        self._clock = clock or SystemClock()

    def status(self) -> StorageStatus:
        return storage_status(self._settings.sqlite_path)

    async def compact(self, *, force_vacuum: bool = False) -> CompactResult:
        """Prune and, when enough is free, VACUUM (in a thread: both block)."""

        return await asyncio.to_thread(
            compact_database,
            self._settings.sqlite_path,
            now=self._clock.utcnow(),
            raw_news_retention_days=self._settings.raw_news_retention_days,
            force_vacuum=force_vacuum,
        )


__all__ = [
    "CompactResult",
    "StorageService",
    "StorageStatus",
    "compact_database",
    "storage_status",
]
