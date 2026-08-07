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
class FakeReplayService:
    calls: int = 0

    async def replay(self, *, as_of: object | None = None) -> None:
        del as_of
        self.calls += 1


@dataclass
class FakeNewsService:
    calls: int = 0
    error: Exception | None = None

    async def sync(self) -> None:
        self.calls += 1
        if self.error is not None:
            raise self.error


@dataclass
class FakeContainer:
    sync_service: FakeSyncService
    replay_service: FakeReplayService = field(default_factory=FakeReplayService)
    news_service: FakeNewsService = field(default_factory=FakeNewsService)
    startup_calls: int = 0
    shutdown_calls: int = 0

    @property
    def portfolio_sync_service(self) -> SyncService:
        return self.sync_service

    @property
    def performance_replay_service(self) -> FakeReplayService:
        return self.replay_service

    @property
    def news_sync_service(self) -> FakeNewsService:
        return self.news_service

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
    await asyncio.sleep(0)
    try:
        assert started_scheduler.running is True
        # Portfolio sync needs credentials and is skipped; news does not, so it still runs.
        assert [job["id"] for job in scheduler.jobs] == ["news-sync"]
        assert container.sync_service.calls == 0
        assert container.news_service.calls == 1
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
        assert [job["id"] for job in scheduler.jobs] == ["portfolio-sync", "news-sync"]
        job = scheduler.jobs[0]
        assert job["trigger"] == "interval"
        assert job["minutes"] == 60
        assert job["max_instances"] == 1
        assert job["coalesce"] is True
        news_job = scheduler.jobs[1]
        assert news_job["minutes"] == 180
        assert news_job["max_instances"] == 1
        assert news_job["coalesce"] is True
        assert sync_service.calls == 1
        assert container.replay_service.calls == 1
        assert container.news_service.calls == 1
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
        assert fake_logger.warning_calls == [("worker_sync_failed", {"error": "RuntimeError"})]
    finally:
        await worker.shutdown()


@pytest.mark.asyncio
async def test_worker_news_failure_does_not_stop_portfolio_sync(tmp_path: Path) -> None:
    """A publisher being unreachable must not take the portfolio loop down with it."""
    scheduler = FakeScheduler()
    sync_service = FakeSyncService()
    container = FakeContainer(
        sync_service=sync_service,
        news_service=FakeNewsService(error=RuntimeError("feed down")),
    )
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
        assert sync_service.calls == 1
        assert container.replay_service.calls == 1
        # Logged under its own event name, and only the class name — never the message.
        assert fake_logger.warning_calls == [("worker_news_sync_failed", {"error": "RuntimeError"})]
    finally:
        await worker.shutdown()
