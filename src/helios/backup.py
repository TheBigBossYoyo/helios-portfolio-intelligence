"""Backing up the live SQLite database, and how to get one back.

Helios keeps its one database file in WAL mode (see `db._configure_sqlite`) with the API and the
background worker holding it open continuously. A plain file copy of that file is unsafe at any
moment a writer is mid-transaction: WAL mode splits committed data across the main file and a
`-wal` sidecar, and a copy that catches them in an inconsistent pairing is not a valid database.
So this module never touches the file with `shutil.copy` or similar -- it drives SQLite's own
*online backup API* (`sqlite3.Connection.backup`), which reads through the same MVCC snapshot a
concurrent reader would see and is documented as safe to run against a database open elsewhere,
including one being written to.

Three more rules this module enforces:

* **Every backup is verified before it is trusted.** `PRAGMA integrity_check` runs against the
  produced copy, not the source -- a `sqlite3.backup()` call can itself return a truncated or
  corrupt file if, for example, the disk fills mid-copy. A backup that fails the check is deleted
  immediately rather than left on disk looking usable.
* **Retention only ever removes files this module wrote.** Pruning matches on
  `<source stem>-<UTC timestamp>.sqlite3` exactly; a file that merely lives in the backups
  directory but does not fit that shape -- someone's manual copy, a different database's backups
  -- is never touched.
* **Restore is deliberately not automated here.** Restoring means the process that holds the live
  file open must not be running while it is replaced, which this module cannot safely arrange by
  itself. To restore: stop the API and worker (`docker compose down`, or stop both processes if
  running outside Docker), copy the chosen `<data_dir>/backups/*.sqlite3` file over
  `<data_dir>/<sqlite_filename>`, delete any `-wal`/`-shm` sidecars left next to the live file, and
  start the stack again.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

#: The timestamp component of a backup filename: sortable lexically, so newest-last on listing.
_TIMESTAMP_FORMAT: Final = "%Y%m%dT%H%M%SZ"
_TIMESTAMP_PATTERN: Final = r"\d{8}T\d{6}Z"


class BackupError(RuntimeError):
    """A backup could not be produced, or the copy it produced did not pass its integrity check.

    The message is safe to show the operator.
    """


@dataclass(frozen=True)
class BackupResult:
    path: Path
    size_bytes: int


def backup_filename_pattern(stem: str) -> re.Pattern[str]:
    """Match only backups this module made of the database named ``stem`` (no `.sqlite3`)."""
    return re.compile(rf"^{re.escape(stem)}-{_TIMESTAMP_PATTERN}\.sqlite3$")


def backup_database(
    source: Path,
    *,
    dest_dir: Path,
    keep: int = 14,
    now: datetime | None = None,
) -> BackupResult:
    """Back up ``source`` into ``dest_dir``, verify it, and prune old backups of this database.

    Safe to call while ``source`` is open elsewhere under WAL (an API request or worker sync in
    flight): the copy is made through SQLite's online backup API, never a raw file copy. Raises
    `BackupError` -- and removes the partial file -- if the source does not exist, the backup
    directory cannot be created, or the produced copy fails `PRAGMA integrity_check`.
    """
    if not source.is_file():
        raise BackupError(f"{source} does not exist; there is nothing to back up.")

    stem = source.stem
    moment = now if now is not None else datetime.now(UTC)
    timestamp = moment.astimezone(UTC).strftime(_TIMESTAMP_FORMAT)
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / f"{stem}-{timestamp}.sqlite3"
    if target.exists():
        raise BackupError(
            f"{target} already exists. Backups are timestamped to the second; wait a moment and "
            "retry."
        )

    _copy_via_backup_api(source, target)
    try:
        _verify_integrity(target)
    except BackupError:
        target.unlink(missing_ok=True)
        raise

    result = BackupResult(path=target, size_bytes=target.stat().st_size)
    _prune_old_backups(dest_dir, stem=stem, keep=keep)
    return result


def _copy_via_backup_api(source: Path, target: Path) -> None:
    source_conn = sqlite3.connect(str(source))
    try:
        dest_conn = sqlite3.connect(str(target))
        try:
            with dest_conn:
                source_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        source_conn.close()


def _verify_integrity(path: Path) -> None:
    connection = sqlite3.connect(str(path))
    try:
        try:
            row = connection.execute("PRAGMA integrity_check;").fetchone()
        except sqlite3.DatabaseError as error:
            # A file so broken SQLite cannot even read its header raises here instead of
            # returning a row -- still an integrity failure, just one that never gets a status
            # string to report.
            status: object = str(error)
        else:
            status = row[0] if row else None
    finally:
        connection.close()
    if status != "ok":
        raise BackupError(
            f"backup at {path.name} failed its integrity check ({status!r}); the file has been "
            "removed. The source database and any earlier backups are untouched."
        )


def _prune_old_backups(dest_dir: Path, *, stem: str, keep: int) -> None:
    """Delete this database's own backups beyond the most recent ``keep``.

    Only files matching `backup_filename_pattern(stem)` are candidates, so anything else in
    ``dest_dir`` -- another database's backups, a file an operator put there by hand -- is left
    alone regardless of its name or age.
    """
    if keep < 0:
        raise BackupError("keep must be zero or greater")
    pattern = backup_filename_pattern(stem)
    candidates = sorted(
        (path for path in dest_dir.iterdir() if path.is_file() and pattern.match(path.name)),
        key=lambda path: path.name,
        reverse=True,
    )
    for stale in candidates[keep:]:
        stale.unlink(missing_ok=True)


def format_size(size_bytes: int) -> str:
    """A human-readable size for the CLI's confirmation line, e.g. ``4.2 MB``."""
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"
