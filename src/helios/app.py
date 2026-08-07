from __future__ import annotations

import traceback
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .api import router
from .config import Settings
from .dependencies import build_container
from .logging import configure_logging, get_logger

logger = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container = build_container(resolved_settings)
        configure_logging(container.settings)
        app.state.container = container
        try:
            await container.startup()
            yield
        finally:
            await container.shutdown()

    app = FastAPI(title="Helios", lifespan=lifespan)

    @app.exception_handler(Exception)
    async def log_unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        """Guarantee an unhandled error is logged with its traceback.

        Without this, structlog's reconfiguration swallowed the traceback uvicorn would have
        printed, so a 500 reached the client with nothing at all in the container logs -- the
        failure was visible but not diagnosable. The response body stays generic; the detail
        goes to the log, not to the caller.
        """
        logger.error(
            "helios.unhandled_error",
            path=request.url.path,
            method=request.method,
            error_type=exc.__class__.__name__,
            error=str(exc),
            traceback=traceback.format_exc(),
        )
        return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})

    app.include_router(router)
    return app


app = create_app()
