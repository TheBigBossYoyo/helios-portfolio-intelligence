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
class FakeCardOutcome:
    action: str


@dataclass
class FakeCardService:
    action: str = "up_to_date"
    calls: int = 0

    async def refresh(self, *, force: bool = False) -> FakeCardOutcome:
        del force
        self.calls += 1
        return FakeCardOutcome(self.action)


@dataclass
class FakeAlertService:
    calls: int = 0

    async def evaluate(self) -> list[object]:
        self.calls += 1
        return []


@dataclass
class FakeSummaryService:
    calls: int = 0

    async def maybe_create(self) -> None:
        self.calls += 1


@dataclass
class FakeWeeklyReview:
    calls: int = 0

    async def maybe_run_scheduled(self) -> None:
        self.calls += 1


@dataclass
class FakeBackups:
    calls: int = 0

    async def run_if_due(self) -> None:
        self.calls += 1


@dataclass
class FakeBudgets:
    calls: int = 0

    async def check(self) -> list[object]:
        self.calls += 1
        return []


@dataclass
class FakeStorage:
    calls: int = 0

    async def compact(self, *, force_vacuum: bool = False) -> None:
        del force_vacuum
        self.calls += 1


@dataclass
class FakeEvents:
    refreshes: int = 0
    notices: int = 0

    async def refresh(self, *, force: bool = False) -> None:
        del force
        self.refreshes += 1

    async def notify(self, sink: object) -> list[object]:
        del sink
        self.notices += 1
        return []


@dataclass
class FakeSec:
    calls: int = 0

    async def refresh(self, *, force: bool = False) -> None:
        del force
        self.calls += 1


@dataclass
class FakePush:
    calls: int = 0

    async def deliver_pending(self) -> None:
        self.calls += 1


@dataclass
class FakeContainer:
    sync_service: FakeSyncService
    replay_service: FakeReplayService = field(default_factory=FakeReplayService)
    news_service: FakeNewsService = field(default_factory=FakeNewsService)
    card_service: FakeCardService = field(default_factory=FakeCardService)
    alerts: FakeAlertService = field(default_factory=FakeAlertService)
    summary: FakeSummaryService = field(default_factory=FakeSummaryService)
    weekly: FakeWeeklyReview = field(default_factory=FakeWeeklyReview)
    backups: FakeBackups = field(default_factory=FakeBackups)
    budgets: FakeBudgets = field(default_factory=FakeBudgets)
    storage: FakeStorage = field(default_factory=FakeStorage)
    events: FakeEvents = field(default_factory=FakeEvents)
    sec: FakeSec = field(default_factory=FakeSec)
    push: FakePush = field(default_factory=FakePush)
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

    @property
    def card_history_service(self) -> FakeCardService:
        return self.card_service

    @property
    def alert_service(self) -> FakeAlertService:
        return self.alerts

    @property
    def daily_summary_service(self) -> FakeSummaryService:
        return self.summary

    @property
    def weekly_review_service(self) -> FakeWeeklyReview:
        return self.weekly

    @property
    def backup_service(self) -> FakeBackups:
        return self.backups

    @property
    def budget_notifier(self) -> FakeBudgets:
        return self.budgets

    @property
    def storage_service(self) -> FakeStorage:
        return self.storage

    @property
    def market_events_service(self) -> FakeEvents:
        return self.events

    @property
    def portfolio_repository(self) -> object:
        return object()

    @property
    def sec_data_service(self) -> FakeSec:
        return self.sec

    @property
    def push_service(self) -> FakePush:
        return self.push

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
        # Without credentials there is no sync; alerts (on stored closes), the summary and news
        # still run.
        assert [job["id"] for job in scheduler.jobs] == [
            "price-alerts",
            "daily-summary",
            "backup",
            "market-events",
            "sec-data",
            "storage-compact",
            "news-sync",
        ]
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
        assert [job["id"] for job in scheduler.jobs] == [
            "portfolio-sync",
            "price-alerts",
            "daily-summary",
            "backup",
            "market-events",
            "sec-data",
            "storage-compact",
            "news-sync",
        ]
        job = scheduler.jobs[0]
        assert job["trigger"] == "interval"
        assert job["minutes"] == 60
        assert job["max_instances"] == 1
        assert job["coalesce"] is True
        news_job = next(job for job in scheduler.jobs if job["id"] == "news-sync")
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


@pytest.mark.asyncio
async def test_each_sync_cycle_also_refreshes_news_when_enabled(tmp_path: Path) -> None:
    """Sync, replay, then news: new holdings get their headlines now, not three hours later."""
    container = FakeContainer(sync_service=FakeSyncService())
    worker = HeliosWorker(
        Settings(
            data_dir=tmp_path,
            t212_api_key="key",
            t212_api_secret=SecretStr("secret"),
            refresh_after_sync=True,
        ),
        container_factory=lambda _settings: container,
        scheduler=FakeScheduler(),
    )
    worker._logger = FakeLogger()
    worker._container = container

    await worker._run_scheduled_sync()

    assert container.sync_service.calls == 1
    assert container.replay_service.calls == 1
    assert container.news_service.calls == 1


@pytest.mark.asyncio
async def test_a_failed_sync_does_not_refresh_anything(tmp_path: Path) -> None:
    container = FakeContainer(sync_service=FakeSyncService(error=RuntimeError("down")))
    worker = HeliosWorker(
        Settings(data_dir=tmp_path, refresh_after_sync=True),
        container_factory=lambda _settings: container,
        scheduler=FakeScheduler(),
    )
    worker._logger = FakeLogger()
    worker._container = container

    await worker._run_scheduled_sync()

    assert container.replay_service.calls == 0
    assert container.news_service.calls == 0


@pytest.mark.asyncio
async def test_card_history_is_scheduled_and_a_new_export_triggers_a_replay(
    tmp_path: Path,
) -> None:
    scheduler = FakeScheduler()
    container = FakeContainer(
        sync_service=FakeSyncService(), card_service=FakeCardService(action="downloaded")
    )
    worker = HeliosWorker(
        Settings(
            data_dir=tmp_path,
            t212_api_key="key",
            t212_api_secret=SecretStr("secret"),
            card_history_enabled=True,
        ),
        container_factory=lambda _settings: container,
        scheduler=scheduler,
    )
    worker._logger = FakeLogger()

    await worker.start()
    await asyncio.sleep(0)
    try:
        card_job = next(job for job in scheduler.jobs if job["id"] == "card-history")
        assert card_job["minutes"] == 15
        replays_before = container.replay_service.calls
        await worker._run_scheduled_card_history()
        assert container.card_service.calls == 1
        # The export relabelled card payments and cashback, so history is recomputed.
        assert container.replay_service.calls == replays_before + 1
    finally:
        await worker.shutdown()


@pytest.mark.asyncio
async def test_alert_and_summary_jobs_call_their_services(tmp_path: Path) -> None:
    container = FakeContainer(sync_service=FakeSyncService())
    worker = HeliosWorker(
        Settings(data_dir=tmp_path),
        container_factory=lambda _settings: container,
        scheduler=FakeScheduler(),
    )
    worker._logger = FakeLogger()

    await worker.start()
    try:
        await worker._run_scheduled_alerts()
        await worker._run_scheduled_summary()
        await worker._run_scheduled_backup()
    finally:
        await worker.shutdown()

    assert (container.alerts.calls, container.summary.calls) == (1, 1)
    # The alerts job also checks budgets.
    assert (container.budgets.calls, container.backups.calls) == (1, 1)


@pytest.mark.asyncio
async def test_storage_compaction_is_scheduled_daily_and_can_be_switched_off(
    tmp_path: Path,
) -> None:
    container = FakeContainer(sync_service=FakeSyncService())
    scheduler = FakeScheduler()
    worker = HeliosWorker(
        Settings(data_dir=tmp_path),
        container_factory=lambda _settings: container,
        scheduler=scheduler,
    )
    worker._logger = FakeLogger()
    await worker.start()
    try:
        job = next(job for job in scheduler.jobs if job["id"] == "storage-compact")
        assert job["minutes"] == 24 * 60
        assert worker._initial_compact_task is not None  # plus once shortly after start
        await worker._run_scheduled_compact()
        assert container.storage.calls == 1
    finally:
        await worker.shutdown()

    off = FakeScheduler()
    quiet = HeliosWorker(
        Settings(data_dir=tmp_path, storage_compact_enabled=False),
        container_factory=lambda _settings: FakeContainer(sync_service=FakeSyncService()),
        scheduler=off,
    )
    quiet._logger = FakeLogger()
    await quiet.start()
    try:
        assert "storage-compact" not in [job["id"] for job in off.jobs]
    finally:
        await quiet.shutdown()
