from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api import router
from .config import Settings
from .dependencies import build_container
from .logging import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container = build_container(resolved_settings)
        configure_logging(container.settings)
        await container.startup()
        app.state.container = container
        try:
            yield
        finally:
            await container.shutdown()

    app = FastAPI(title="Helios", lifespan=lifespan)
    app.include_router(router)
    return app


app = create_app()
