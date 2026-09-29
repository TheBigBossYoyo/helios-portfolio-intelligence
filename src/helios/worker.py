from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from typing import Any, Protocol, cast

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .config import Settings, load_settings
from .dependencies import Container, build_container
from .logging import configure_logging, get_logger

#: How often the worker looks at the clock for the daily summary.
SUMMARY_CHECK_MINUTES = 10
#: How often the worker checks whether this week's review is due (only when switched on).
WEEKLY_REVIEW_CHECK_MINUTES = 30
#: How often the worker checks whether a backup is due (it runs once the last is a day old).
BACKUP_CHECK_MINUTES = 60
#: Compaction runs once a day, and once this long after the worker starts.
STORAGE_COMPACT_MINUTES = 24 * 60
STORAGE_STARTUP_DELAY_SECONDS = 300
#: How often the worker checks whether a calendar source is due.
EVENTS_CHECK_MINUTES = 180


class SyncService(Protocol):
    async def sync(self, *, force_metadata: bool = False) -> object: ...


class ReplayService(Protocol):
    async def replay(self, *, as_of: object | None = None) -> object: ...


class NewsService(Protocol):
    async def sync(self) -> object: ...


class EventsRefresher(Protocol):
    async def refresh(self, *, force: bool = False) -> object: ...

    async def notify(self, sink: Any) -> Sequence[object]: ...


class StorageCompactor(Protocol):
    async def compact(self, *, force_vacuum: bool = False) -> object: ...


class AlertChecker(Protocol):
    async def evaluate(self) -> Sequence[object]: ...


class SummaryWriter(Protocol):
    async def maybe_create(self) -> object | None: ...


class WeeklyReviewWriter(Protocol):
    async def maybe_run_scheduled(self) -> object | None: ...


class BackupRunner(Protocol):
    async def run_if_due(self) -> object | None: ...


class BudgetChecker(Protocol):
    async def check(self) -> Sequence[object]: ...


class CardRefreshOutcome(Protocol):
    @property
    def action(self) -> str: ...


class CardService(Protocol):
    async def refresh(self, *, force: bool = False) -> CardRefreshOutcome: ...


class WorkerContainer(Protocol):
    @property
    def portfolio_sync_service(self) -> SyncService: ...

    @property
    def performance_replay_service(self) -> ReplayService: ...

    @property
    def news_sync_service(self) -> NewsService: ...

    @property
    def card_history_service(self) -> CardService: ...

    @property
    def alert_service(self) -> AlertChecker: ...

    @property
    def daily_summary_service(self) -> SummaryWriter: ...

    @property
    def weekly_review_service(self) -> WeeklyReviewWriter: ...

    @property
    def backup_service(self) -> BackupRunner: ...

    @property
    def budget_notifier(self) -> BudgetChecker: ...

    @property
    def storage_service(self) -> StorageCompactor: ...

    @property
    def market_events_service(self) -> EventsRefresher: ...

    @property
    def portfolio_repository(self) -> Any: ...

    async def startup(self) -> None: ...

    async def shutdown(self) -> None: ...


class Scheduler(Protocol):
    @property
    def running(self) -> bool: ...

    def start(self) -> None: ...

    def add_job(
        self,
        func: Callable[[], Awaitable[None]],
        *,
        trigger: str,
        minutes: int,
        id: str,
        max_instances: int,
        coalesce: bool,
    ) -> object: ...

    def shutdown(self, wait: bool = False) -> None: ...


class WorkerLogger(Protocol):
    def info(self, event: str, **kwargs: object) -> object: ...

    def warning(self, event: str, **kwargs: object) -> object: ...


class HeliosWorker:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        container_factory: Callable[[Settings], WorkerContainer] | None = None,
        scheduler: Scheduler | None = None,
    ) -> None:
        self._settings = settings or load_settings()
        configure_logging(self._settings)
        self._logger: WorkerLogger = get_logger(__name__)
        self._container_factory = cast(
            Callable[[Settings], WorkerContainer],
            container_factory or _build_worker_container,
        )
        self._scheduler = scheduler or cast(Scheduler, AsyncIOScheduler(timezone="UTC"))
        self._container: WorkerContainer | None = None
        self._initial_sync_task: asyncio.Task[None] | None = None
        self._initial_news_task: asyncio.Task[None] | None = None
        self._initial_compact_task: asyncio.Task[None] | None = None

    async def start(self) -> Scheduler:
        container = self._container_factory(self._settings)
        self._container = container
        try:
            await container.startup()
            if self._settings.t212_credentials() is None:
                self._logger.info("worker_sync_disabled", reason="missing_credentials")
            else:
                self._scheduler.add_job(
                    self._run_scheduled_sync,
                    trigger="interval",
                    minutes=self._settings.sync_cadence_minutes,
                    id="portfolio-sync",
                    max_instances=1,
                    coalesce=True,
                )
            if (
                self._settings.card_history_enabled
                and self._settings.t212_credentials() is not None
            ):
                self._scheduler.add_job(
                    self._run_scheduled_card_history,
                    trigger="interval",
                    minutes=self._settings.card_history_poll_minutes,
                    id="card-history",
                    max_instances=1,
                    coalesce=True,
                )
            # Alerts fall back to stored daily closes without credentials, and the summary
            # needs none, so both run either way.
            self._scheduler.add_job(
                self._run_scheduled_alerts,
                trigger="interval",
                minutes=self._settings.alerts_poll_minutes,
                id="price-alerts",
                max_instances=1,
                coalesce=True,
            )
            if self._settings.daily_summary_enabled:
                self._scheduler.add_job(
                    self._run_scheduled_summary,
                    trigger="interval",
                    minutes=SUMMARY_CHECK_MINUTES,
                    id="daily-summary",
                    max_instances=1,
                    coalesce=True,
                )
            if self._settings.backup_enabled:
                self._scheduler.add_job(
                    self._run_scheduled_backup,
                    trigger="interval",
                    minutes=BACKUP_CHECK_MINUTES,
                    id="backup",
                    max_instances=1,
                    coalesce=True,
                )
            # Earnings dates and declared dividends: due sources only, so most runs ask
            # nothing (the earnings calendar daily, each company's dividends weekly).
            self._scheduler.add_job(
                self._run_scheduled_events,
                trigger="interval",
                minutes=EVENTS_CHECK_MINUTES,
                id="market-events",
                max_instances=1,
                coalesce=True,
            )
            if self._settings.storage_compact_enabled:
                self._scheduler.add_job(
                    self._run_scheduled_compact,
                    trigger="interval",
                    minutes=STORAGE_COMPACT_MINUTES,
                    id="storage-compact",
                    max_instances=1,
                    coalesce=True,
                )
            if self._settings.weekly_review_enabled:
                self._scheduler.add_job(
                    self._run_scheduled_weekly_review,
                    trigger="interval",
                    minutes=WEEKLY_REVIEW_CHECK_MINUTES,
                    id="weekly-review",
                    max_instances=1,
                    coalesce=True,
                )
            # News needs no Trading 212 credentials, so it is scheduled either way. With no
            # feeds configured it is a no-op that reports why.
            self._scheduler.add_job(
                self._run_scheduled_news_sync,
                trigger="interval",
                minutes=self._settings.news_sync_cadence_minutes,
                id="news-sync",
                max_instances=1,
                coalesce=True,
            )
            self._scheduler.start()
        except BaseException:
            await container.shutdown()
            self._container = None
            raise
        if self._settings.t212_credentials() is not None:
            self._initial_sync_task = asyncio.create_task(self._run_scheduled_sync())
            self._logger.info("worker_started", sync_enabled=True)
        self._initial_news_task = asyncio.create_task(self._run_scheduled_news_sync())
        if self._settings.storage_compact_enabled:
            self._initial_compact_task = asyncio.create_task(self._compact_after_start())
        return self._scheduler

    async def shutdown(self) -> None:
        for task in (self._initial_sync_task, self._initial_news_task, self._initial_compact_task):
            if task is None:
                continue
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self._initial_sync_task = None
        self._initial_news_task = None
        self._initial_compact_task = None
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        if self._container is not None:
            await self._container.shutdown()
            self._container = None

    async def _run_scheduled_sync(self) -> None:
        if self._container is None:
            return
        try:
            await self._container.portfolio_sync_service.sync()
            await self._container.performance_replay_service.replay()
        except Exception as exc:
            self._logger.warning("worker_sync_failed", error=exc.__class__.__name__)
            return
        if self._settings.refresh_after_sync:
            # New holdings deserve their headlines now, not at the next three-hourly news run.
            await self._run_scheduled_news_sync()

    async def _run_scheduled_alerts(self) -> None:
        if self._container is None:
            return
        try:
            await self._container.alert_service.evaluate()
        except Exception as exc:
            self._logger.warning("worker_alerts_failed", error=exc.__class__.__name__)
        try:
            await self._container.budget_notifier.check()
        except Exception as exc:
            self._logger.warning("worker_budgets_failed", error=exc.__class__.__name__)
        try:
            await self._container.market_events_service.notify(
                self._container.portfolio_repository
            )
        except Exception as exc:
            self._logger.warning("worker_event_notices_failed", error=exc.__class__.__name__)

    async def _run_scheduled_events(self) -> None:
        if self._container is None:
            return
        try:
            await self._container.market_events_service.refresh()
        except Exception as exc:
            self._logger.warning("worker_events_failed", error=exc.__class__.__name__)

    async def _run_scheduled_backup(self) -> None:
        """A verified copy of the database, once the last one is a day old."""

        if self._container is None:
            return
        try:
            await self._container.backup_service.run_if_due()
        except Exception as exc:
            self._logger.warning("worker_backup_failed", error=exc.__class__.__name__)

    async def _compact_after_start(self) -> None:
        """Compact once soon after start, so a migration's freed space goes back to the disk."""

        await asyncio.sleep(STORAGE_STARTUP_DELAY_SECONDS)
        await self._run_scheduled_compact()

    async def _run_scheduled_compact(self) -> None:
        """Return free space to the disk (lossless: see helios.storage)."""

        if self._container is None:
            return
        try:
            await self._container.storage_service.compact()
        except Exception as exc:
            self._logger.warning("worker_storage_compact_failed", error=exc.__class__.__name__)

    async def _run_scheduled_summary(self) -> None:
        """Write today's summary once it is past the configured time (a no-op otherwise)."""

        if self._container is None:
            return
        try:
            await self._container.daily_summary_service.maybe_create()
        except Exception as exc:
            self._logger.warning("worker_summary_failed", error=exc.__class__.__name__)

    async def _run_scheduled_weekly_review(self) -> None:
        if self._container is None:
            return
        try:
            await self._container.weekly_review_service.maybe_run_scheduled()
        except Exception as exc:
            self._logger.warning("worker_weekly_review_failed", error=exc.__class__.__name__)

    async def _run_scheduled_card_history(self) -> None:
        """Collect a finished export, or request one when a day has passed since the last.

        A newly downloaded export relabels card payments and cashback, so history is replayed
        straight after it lands.
        """

        if self._container is None:
            return
        try:
            result = await self._container.card_history_service.refresh()
            if result.action == "downloaded":
                await self._container.performance_replay_service.replay()
        except Exception as exc:
            self._logger.warning("worker_card_history_failed", error=exc.__class__.__name__)

    async def _run_scheduled_news_sync(self) -> None:
        if self._container is None:
            return
        try:
            await self._container.news_sync_service.sync()
        except Exception as exc:
            # A publisher being down must never take the portfolio sync loop with it.
            self._logger.warning("worker_news_sync_failed", error=exc.__class__.__name__)


async def run_worker(settings: Settings | None = None) -> None:
    worker = HeliosWorker(settings)
    try:
        await worker.start()
        await asyncio.Event().wait()
    finally:
        await worker.shutdown()


def _build_worker_container(settings: Settings) -> Container:
    return build_container(settings)


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
