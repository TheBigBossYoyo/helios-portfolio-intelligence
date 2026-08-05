from __future__ import annotations

from pathlib import Path

import pytest

from helios.config import Settings
from helios.db import create_engine, fetch_journal_mode, initialize_database


@pytest.mark.asyncio
async def test_sqlite_wal_mode_is_enabled(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, sqlite_filename="test.sqlite3")
    engine = create_engine(settings)
    await initialize_database(engine)

    assert await fetch_journal_mode(engine) == "wal"

    await engine.dispose()
