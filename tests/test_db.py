from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from helios.config import Settings
from helios.db import create_engine, fetch_journal_mode, migrate_database


@pytest.mark.asyncio
async def test_sqlite_wal_mode_is_enabled(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, sqlite_filename="test.sqlite3")
    await migrate_database(settings)
    engine = create_engine(settings)

    assert await fetch_journal_mode(engine) == "wal"
    async with engine.connect() as connection:
        revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
    assert revision == "0001_create_raw_snapshots"

    await engine.dispose()
