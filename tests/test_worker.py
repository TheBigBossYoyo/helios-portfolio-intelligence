from __future__ import annotations

from pathlib import Path

import pytest

from helios.config import Settings
from helios.worker import HeliosWorker


@pytest.mark.asyncio
async def test_worker_starts_without_credentials(tmp_path: Path) -> None:
    worker = HeliosWorker(Settings(data_dir=tmp_path))

    scheduler = worker.start()
    try:
        assert scheduler.running
        assert scheduler.get_jobs() == []
    finally:
        worker.shutdown()
