from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import SQLAlchemyError

from .client import Trading212CredentialsError, Trading212Error, Trading212HTTPError
from .db import ping
from .dependencies import Container, get_container, get_t212_service
from .schemas import HealthResponse, Position
from .services import Trading212Service

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health(container: Annotated[Container, Depends(get_container)]) -> HealthResponse:
    try:
        await ping(container.engine)
        database_ready = True
    except SQLAlchemyError:
        database_ready = False
    return HealthResponse(
        status="ok" if database_ready else "degraded",
        trading212Configured=container.settings.t212_credentials() is not None,
        databaseReady=database_ready,
    )


@router.get("/api/v1/t212/positions", response_model=list[Position])
async def get_positions(
    service: Annotated[Trading212Service, Depends(get_t212_service)],
) -> list[Position]:
    try:
        return await service.get_positions()
    except Trading212CredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Trading 212 credentials are not configured",
        ) from exc
    except Trading212HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Trading 212 upstream error: {exc.status_code}",
        ) from exc
    except Trading212Error as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Trading 212 request failed",
        ) from exc
