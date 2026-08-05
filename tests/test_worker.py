from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from pydantic import SecretStr

from helios.config import Settings
from helios.worker import HeliosWorker, SyncService


@dataclass
class FakeSyncService:
    calls: int = 0
    error: Exception | None = None

    async def sync(self, *, force_metadata: bool = False) -> None:
        del force_metadata
        self.calls += 1
        if self.error is not None:
            raise self.error


@dataclass
class FakeContainer:
    sync_service: FakeSyncService
    startup_calls: int = 0
    shutdown_calls: int = 0

    @property
    def portfolio_sync_service(self) -> SyncService:
        return self.sync_service

    async def startup(self) -> None:
        self.startup_calls += 1

    async def shutdown(self) -> None:
        self.shutdown_calls += 1


@dataclass
class FakeScheduler:
    _running: bool = False
    jobs: list[dict[str, object]] = field(default_factory=list)
    shutdown_calls: int = 0

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        self._running = True

    def add_job(self, func: object, **kwargs: object) -> None:
        self.jobs.append({"func": func, **kwargs})

    def get_jobs(self) -> list[dict[str, object]]:
        return self.jobs

    def shutdown(self, wait: bool = False) -> None:
        self._running = False
        self.shutdown_calls += 1


@dataclass
class FakeLogger:
    info_calls: list[tuple[str, dict[str, object]]] = field(default_factory=list)
    warning_calls: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    def info(self, event: str, **kwargs: object) -> None:
        self.info_calls.append((event, kwargs))

    def warning(self, event: str, **kwargs: object) -> None:
        self.warning_calls.append((event, kwargs))


@pytest.mark.asyncio
async def test_worker_starts_without_credentials(tmp_path: Path) -> None:
    scheduler = FakeScheduler()
    container = FakeContainer(sync_service=FakeSyncService())
    worker = HeliosWorker(
        Settings(data_dir=tmp_path),
        container_factory=lambda _settings: container,
        scheduler=scheduler,
    )
    worker._logger = FakeLogger()

    started_scheduler = await worker.start()
    try:
        assert started_scheduler.running is True
        assert scheduler.jobs == []
        assert container.startup_calls == 1
    finally:
        await worker.shutdown()

    assert container.shutdown_calls == 1


@pytest.mark.asyncio
async def test_worker_registers_interval_job_and_runs_initial_sync(tmp_path: Path) -> None:
    scheduler = FakeScheduler()
    sync_service = FakeSyncService()
    container = FakeContainer(sync_service=sync_service)
    worker = HeliosWorker(
        Settings(
            data_dir=tmp_path,
            t212_api_key="key",
            t212_api_secret=SecretStr("secret"),
        ),
        container_factory=lambda _settings: container,
        scheduler=scheduler,
    )
    worker._logger = FakeLogger()

    started_scheduler = await worker.start()
    await asyncio.sleep(0)
    try:
        assert started_scheduler.running is True
        assert len(scheduler.jobs) == 1
        job = scheduler.jobs[0]
        assert job["id"] == "portfolio-sync"
        assert job["trigger"] == "interval"
        assert job["minutes"] == 60
        assert job["max_instances"] == 1
        assert job["coalesce"] is True
        assert sync_service.calls == 1
    finally:
        await worker.shutdown()


@pytest.mark.asyncio
async def test_worker_logs_safe_error_class_and_survives(tmp_path: Path) -> None:
    scheduler = FakeScheduler()
    sync_service = FakeSyncService(error=RuntimeError("boom"))
    container = FakeContainer(sync_service=sync_service)
    worker = HeliosWorker(
        Settings(
            data_dir=tmp_path,
            t212_api_key="key",
            t212_api_secret=SecretStr("secret"),
        ),
        container_factory=lambda _settings: container,
        scheduler=scheduler,
    )
    fake_logger = FakeLogger()
    worker._logger = fake_logger

    await worker.start()
    await asyncio.sleep(0)
    try:
        assert scheduler.running is True
        assert fake_logger.warning_calls == [
            ("worker_sync_failed", {"error": "RuntimeError"})
        ]
    finally:
        await worker.shutdown()
