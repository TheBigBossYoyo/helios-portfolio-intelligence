"""Keeping the database file small without ever dropping data.

Nothing here deletes a row. Space is saved by compression (``helios.compression``) and returned
to the disk here:

* the write-ahead log is checkpointed and truncated, so it does not sit at its high-water mark;
* the database uses incremental auto-vacuum, so pages freed by an update (a re-encoded row, a
  replay rewriting its tables) are handed back to the disk by ``PRAGMA incremental_vacuum``;
* a full ``VACUUM`` rewrites the file when a real share of it is free, and once to switch an
  older database to incremental auto-vacuum.

The worker runs this shortly after it starts and then every day. VACUUM needs a moment with no
other writer, so it retries briefly and otherwise waits for the next run.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Settings

#: Rewrite the whole file once this share of it is free pages...
VACUUM_FREE_RATIO = 0.1
#: ...and at least this much would be returned to the disk.
VACUUM_MIN_FREE_BYTES = 1024 * 1024
VACUUM_ATTEMPTS = 3
VACUUM_RETRY_SECONDS = 2.0
LOCK_TIMEOUT_SECONDS = 30.0
#: PRAGMA auto_vacuum value for INCREMENTAL.
AUTO_VACUUM_INCREMENTAL = 2


@dataclass(frozen=True)
class StorageStatus:
    database_bytes: int
    wal_bytes: int
    free_bytes: int
    raw_news_rows: int
    raw_snapshot_rows: int
    raw_stored_bytes: int

    @property
    def total_bytes(self) -> int:
        return self.database_bytes + self.wal_bytes


@dataclass(frozen=True)
class CompactResult:
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


def _pragma(connection: sqlite3.Connection, name: str) -> int:
    return int(connection.execute(f"PRAGMA {name}").fetchone()[0])


def _free_bytes(connection: sqlite3.Connection) -> tuple[int, int]:
    """(free bytes, total bytes) inside the main database file."""

    page_size = _pragma(connection, "page_size")
    return _pragma(connection, "freelist_count") * page_size, _pragma(
        connection, "page_count"
    ) * page_size


def _scalar(connection: sqlite3.Connection, sql: str) -> int:
    try:
        return int(connection.execute(sql).fetchone()[0] or 0)
    except sqlite3.OperationalError:
        return 0


def storage_status(path: Path) -> StorageStatus:
    if not path.exists():
        return StorageStatus(0, 0, 0, 0, 0, 0)
    connection = _connect(path)
    try:
        free, _total = _free_bytes(connection)
        return StorageStatus(
            database_bytes=_file_size(path),
            wal_bytes=_file_size(_wal_path(path)),
            free_bytes=free,
            raw_news_rows=_scalar(connection, "SELECT COUNT(*) FROM raw_news"),
            raw_snapshot_rows=_scalar(connection, "SELECT COUNT(*) FROM raw_snapshots"),
            raw_stored_bytes=_scalar(connection, "SELECT SUM(LENGTH(body)) FROM raw_news")
            + _scalar(connection, "SELECT SUM(LENGTH(payload_json)) FROM raw_snapshots"),
        )
    finally:
        connection.close()


def _vacuum(connection: sqlite3.Connection) -> bool:
    for attempt in range(VACUUM_ATTEMPTS):
        try:
            connection.execute("VACUUM")
            return True
        except sqlite3.OperationalError:
            # Another connection is mid-write; try again shortly, else leave it for next time.
            if attempt + 1 < VACUUM_ATTEMPTS:
                time.sleep(VACUUM_RETRY_SECONDS)
    return False


def compact_database(path: Path, *, force_vacuum: bool = False) -> CompactResult:
    """Return free space to the disk. Lossless: no row is changed or removed."""

    before_bytes = _file_size(path) + _file_size(_wal_path(path))
    connection = _connect(path)
    vacuumed = False
    try:
        if _pragma(connection, "auto_vacuum") != AUTO_VACUUM_INCREMENTAL:
            # Takes effect only through a VACUUM; from then on freed pages can be released
            # without rewriting the file.
            connection.execute(f"PRAGMA auto_vacuum = {AUTO_VACUUM_INCREMENTAL}")
            force_vacuum = True
        free, total = _free_bytes(connection)
        worth_it = free >= VACUUM_MIN_FREE_BYTES and total > 0 and free / total >= VACUUM_FREE_RATIO
        if force_vacuum or worth_it:
            vacuumed = _vacuum(connection)
        if not vacuumed:
            connection.execute("PRAGMA incremental_vacuum").fetchall()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
    finally:
        connection.close()
    return CompactResult(
        vacuumed=vacuumed,
        bytes_before=before_bytes,
        bytes_after=_file_size(path) + _file_size(_wal_path(path)),
    )


class StorageService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def status(self) -> StorageStatus:
        return storage_status(self._settings.sqlite_path)

    async def compact(self, *, force_vacuum: bool = False) -> CompactResult:
        """Checkpoint, release free pages, VACUUM when worth it (in a thread: all block)."""

        return await asyncio.to_thread(
            compact_database, self._settings.sqlite_path, force_vacuum=force_vacuum
        )


__all__ = [
    "CompactResult",
    "StorageService",
    "StorageStatus",
    "compact_database",
    "storage_status",
]
