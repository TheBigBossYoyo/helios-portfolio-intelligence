from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy import event, inspect, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import Settings, load_settings


def _configure_sqlite(dbapi_connection: Any, _: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.close()


def create_engine(settings: Settings) -> AsyncEngine:
    engine = create_async_engine(settings.sqlite_url, future=True)
    event.listen(engine.sync_engine, "connect", _configure_sqlite)
    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


def _upgrade_database(settings: Settings) -> None:
    source_root = Path(__file__).resolve().parents[2]
    project_root = next(
        (
            candidate
            for candidate in (Path.cwd(), source_root)
            if (candidate / "alembic.ini").is_file()
        ),
        source_root,
    )
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "alembic"))
    sync_url = settings.sqlite_url.replace("+aiosqlite", "")
    config.set_main_option("sqlalchemy.url", sync_url)
    _stamp_legacy_snapshot_schema(config, sync_url)
    command.upgrade(config, "head")


def _stamp_legacy_snapshot_schema(config: Config, sync_url: str) -> None:
    engine = create_sync_engine(sync_url)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        if "raw_snapshots" not in tables:
            return
        if "alembic_version" in tables:
            with engine.connect() as connection:
                revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
            if revision is not None:
                return
        columns = {column["name"] for column in inspector.get_columns("raw_snapshots")}
    finally:
        engine.dispose()

    expected_columns = {
        "id",
        "endpoint",
        "ts",
        "http_status",
        "content_type",
        "payload_json",
    }
    if columns == expected_columns:
        command.stamp(config, "0001_create_raw_snapshots")


async def migrate_database(settings: Settings) -> None:
    settings.ensure_directories()
    await asyncio.to_thread(_upgrade_database, settings)


def migration_main() -> None:
    _upgrade_database(load_settings())


async def dispose_engine(engine: AsyncEngine) -> None:
    await engine.dispose()


async def fetch_journal_mode(engine: AsyncEngine) -> str:
    async with engine.connect() as connection:
        result = await connection.execute(text("PRAGMA journal_mode;"))
        mode = result.scalar_one()
    return str(mode)


async def ping(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
