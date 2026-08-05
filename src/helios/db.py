from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import Settings


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
    project_root = Path(__file__).resolve().parents[2]
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.sqlite_url.replace("+aiosqlite", ""))
    command.upgrade(config, "head")


async def migrate_database(settings: Settings) -> None:
    settings.ensure_directories()
    await asyncio.to_thread(_upgrade_database, settings)


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

