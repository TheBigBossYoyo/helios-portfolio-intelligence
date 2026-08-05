from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Protocol, cast

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .config import Settings, load_settings
from .dependencies import Container, build_container
from .logging import configure_logging, get_logger


class SyncService(Protocol):
    async def sync(self, *, force_metadata: bool = False) -> object: ...


class WorkerContainer(Protocol):
    @property
    def portfolio_sync_service(self) -> SyncService: ...

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
        self._container_factory: Callable[[Settings], WorkerContainer] = (
            container_factory or _build_worker_container
        )
        self._scheduler = scheduler or cast(Scheduler, AsyncIOScheduler(timezone="UTC"))
        self._container: WorkerContainer | None = None
        self._initial_sync_task: asyncio.Task[None] | None = None

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
            self._scheduler.start()
        except BaseException:
            await container.shutdown()
            self._container = None
            raise
        if self._settings.t212_credentials() is not None:
            self._initial_sync_task = asyncio.create_task(self._run_scheduled_sync())
            self._logger.info("worker_started", sync_enabled=True)
        return self._scheduler

    async def shutdown(self) -> None:
        if self._initial_sync_task is not None:
            self._initial_sync_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._initial_sync_task
            self._initial_sync_task = None
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
        except Exception as exc:
            self._logger.warning("worker_sync_failed", error=exc.__class__.__name__)


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
