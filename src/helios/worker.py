from __future__ import annotations

import asyncio

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .config import Settings, load_settings
from .logging import configure_logging, get_logger


class HeliosWorker:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or load_settings()
        configure_logging(self._settings)
        self._logger = get_logger(__name__)
        self._scheduler = AsyncIOScheduler(timezone="UTC")

    def start(self) -> AsyncIOScheduler:
        self._scheduler.start()
        if self._settings.t212_credentials() is None:
            self._logger.info("worker_sync_disabled", reason="missing_credentials")
            return self._scheduler
        self._logger.info("worker_started", sync_enabled=False)
        return self._scheduler

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)


def start_worker(settings: Settings | None = None) -> AsyncIOScheduler:
    worker = HeliosWorker(settings)
    return worker.start()


async def run_worker(settings: Settings | None = None) -> None:
    worker = HeliosWorker(settings)
    worker.start()
    try:
        await asyncio.Event().wait()
    finally:
        worker.shutdown()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
