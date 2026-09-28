"""Managing which SQLite file Helios reads and writes.

Switching database is the one settings change with a real risk of data loss, so this module is
conservative in three specific ways:

* **It never deletes.** Switching away from a database leaves the file exactly where it is. The
  demo history you accumulated before pointing Helios at a live account stays on disk under its
  own name, and going back is a switch rather than a restore.
* **It never mixes.** A live account replayed into a database holding demo history produces a
  portfolio that is neither, so creating a database is a distinct action from switching to one
  and the dashboard makes you do both.
* **A filename is not a path.** Only a bare `*.sqlite3` name inside the configured data
  directory is accepted. Without that, a settings write could point the engine at any file the
  process can reach and migrations would run against it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy import inspect, text

from .config import Settings
from .db import migrate_database

#: A database filename must look like this. Deliberately strict: no separators, no traversal, no
#: leading dot, and an explicit extension so a stray file in `data/` is never mistaken for one.
FILENAME_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\.sqlite3$")

#: SQLite's sidecar files. Listed so they are not reported as databases in their own right.
SIDECAR_SUFFIXES: Final = ("-wal", "-shm", "-journal")


class DatabaseAdminError(RuntimeError):
    """A database operation was refused. The message is safe to show the operator."""


@dataclass(frozen=True)
class DatabaseInfo:
    """One database file in the data directory."""

    filename: str
    size_bytes: int
    modified_at: datetime | None
    active: bool
    schema_version: str | None


def validate_filename(filename: str) -> str:
    """Accept only a bare SQLite filename, rejecting anything path-like.

    This is the whole containment boundary for database switching: everything downstream joins
    the result onto `settings.data_dir` and trusts it.
    """

    candidate = filename.strip()
    if not candidate:
        raise DatabaseAdminError("A database filename is required.")
    if candidate != Path(candidate).name:
        raise DatabaseAdminError(
            f"{filename!r} looks like a path. Give a bare filename; databases always live in "
            "the configured data directory."
        )
    if not FILENAME_PATTERN.match(candidate):
        raise DatabaseAdminError(
            f"{filename!r} is not a valid database name. Use letters, digits, dot, dash or "
            "underscore, and end with .sqlite3 — for example helios-live.sqlite3."
        )
    return candidate


def _schema_version(path: Path) -> str | None:
    """The Alembic revision a database is stamped at, or None when it has no schema yet."""

    engine = create_sync_engine(f"sqlite:///{path.as_posix()}")
    try:
        inspector = inspect(engine)
        if "alembic_version" not in set(inspector.get_table_names()):
            return None
        with engine.connect() as connection:
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
    except Exception:
        # A file that is not a readable SQLite database still deserves a row in the listing,
        # reported as having no schema rather than crashing the settings page.
        return None
    finally:
        engine.dispose()
    return str(revision) if revision is not None else None


def list_databases(settings: Settings) -> tuple[str, tuple[DatabaseInfo, ...]]:
    """Every database in the data directory, with the active one marked.

    Returns the active filename alongside the listing so a caller does not have to re-derive it.
    """

    data_dir = settings.data_dir
    active = settings.sqlite_filename
    rows: list[DatabaseInfo] = []
    if data_dir.is_dir():
        for path in sorted(data_dir.iterdir()):
            if not path.is_file() or path.name.endswith(SIDECAR_SUFFIXES):
                continue
            if not FILENAME_PATTERN.match(path.name):
                continue
            stat_result = path.stat()
            rows.append(
                DatabaseInfo(
                    filename=path.name,
                    size_bytes=stat_result.st_size,
                    modified_at=datetime.fromtimestamp(stat_result.st_mtime, tz=UTC),
                    active=path.name == active,
                    schema_version=_schema_version(path),
                )
            )
    return active, tuple(rows)


async def create_database(settings: Settings, filename: str) -> str:
    """Create an empty database at the current schema head. Refuses to overwrite.

    Migrations run against the new file only, so the active database is untouched: creating is
    safe to do while Helios is serving, and the switch is a separate, explicit step.
    """

    name = validate_filename(filename)
    settings.ensure_directories()
    target = settings.data_dir / name
    if target.exists():
        raise DatabaseAdminError(
            f"{name} already exists. Pick another name, or switch to it if it is the one you want."
        )

    # A shallow copy pointed at the new filename: migrations take their URL from the settings
    # object, so this runs the schema onto the new file without disturbing the live one.
    target_settings = settings.model_copy(update={"sqlite_filename": name})
    try:
        await migrate_database(target_settings)
    except Exception as exc:
        # A half-migrated file is worse than none: it would list as a real database and fail on
        # first use. Remove it and report the failure.
        target.unlink(missing_ok=True)
        raise DatabaseAdminError(f"Could not create {name}: {exc}") from exc
    return name


def resolve_switch_target(settings: Settings, filename: str) -> str:
    """Validate a switch target without performing it.

    The switch itself is a settings write plus a restart — the engine is built once at startup,
    so nothing changes until the process comes back. This only confirms the target is real and
    usable, which is what makes the eventual restart safe.
    """

    name = validate_filename(filename)
    target = settings.data_dir / name
    if not target.is_file():
        raise DatabaseAdminError(
            f"{name} does not exist in {settings.data_dir}. Create it first, or pick one from "
            "the list."
        )
    if _schema_version(target) is None:
        raise DatabaseAdminError(
            f"{name} has no Helios schema. Run migrations against it, or create a new database "
            "instead of adopting this file."
        )
    return name
