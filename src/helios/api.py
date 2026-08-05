from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import SQLAlchemyError

from .client import (
    Trading212CredentialsError,
    Trading212Error,
    Trading212HTTPError,
    Trading212ParseError,
    Trading212TransportError,
)
from .db import ping
from .dependencies import (
    Container,
    get_container,
    get_portfolio_quality_report_service,
    get_portfolio_sync_service,
    get_t212_service,
)
from .portfolio_repository import SyncAlreadyRunningError
from .portfolio_sync import PortfolioSyncService
from .reporting import NoQualityReportDataError, PortfolioQualityReportService
from .schemas import HealthResponse, PortfolioSyncSummary, Position, QualityReport
from .services import Trading212Service

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health(container: Annotated[Container, Depends(get_container)]) -> HealthResponse:
    try:
        await ping(container.engine)
        database_ready = True
    except SQLAlchemyError:
        database_ready = False
    return HealthResponse.model_validate(
        {
            "status": "ok" if database_ready else "degraded",
            "trading212Configured": container.settings.t212_credentials() is not None,
            "databaseReady": database_ready,
        }
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


@router.post("/api/v1/portfolio/sync", response_model=PortfolioSyncSummary)
async def run_portfolio_sync(
    container: Annotated[Container, Depends(get_container)],
    service: Annotated[PortfolioSyncService, Depends(get_portfolio_sync_service)],
    force_metadata: bool = False,
) -> PortfolioSyncSummary:
    if container.settings.t212_credentials() is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Trading 212 credentials are not configured",
        )
    try:
        return await service.sync(force_metadata=force_metadata)
    except SyncAlreadyRunningError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Portfolio sync already running",
        ) from exc
    except Trading212CredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Trading 212 credentials are not configured",
        ) from exc
    except (
        Trading212HTTPError,
        Trading212TransportError,
        Trading212ParseError,
        Trading212Error,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Trading 212 request failed",
        ) from exc


@router.get("/api/v1/portfolio/data-quality", response_model=QualityReport)
async def get_portfolio_data_quality(
    service: Annotated[
        PortfolioQualityReportService,
        Depends(get_portfolio_quality_report_service),
    ],
) -> QualityReport:
    try:
        return await service.get_report()
    except NoQualityReportDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No portfolio quality report available",
        ) from exc
