from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.exc import SQLAlchemyError

from .ai import AiAnalysisService, AiUnavailableError
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
    get_ai_analysis_service,
    get_container,
    get_news_sync_service,
    get_performance_replay_service,
    get_portfolio_quality_report_service,
    get_portfolio_sync_service,
    get_t212_service,
    get_thesis_service,
)
from .news import NewsSyncService
from .performance import NoPerformanceDataError, PerformanceReplayService
from .portfolio_repository import SyncAlreadyRunningError
from .portfolio_sync import PortfolioSyncService
from .reporting import NoQualityReportDataError, PortfolioQualityReportService
from .schemas import (
    AiAnalysisModel,
    HealthResponse,
    JournalCreateRequest,
    JournalEntryModel,
    NewsItemModel,
    NewsSyncSummaryModel,
    PerformanceReplaySummaryModel,
    PerformanceReportModel,
    PortfolioSyncSummary,
    Position,
    QualityReport,
    ThesisCreateRequest,
    ThesisDetailModel,
    ThesisEditRequest,
    ThesisModel,
    ThesisTransitionRequest,
)
from .services import Trading212Service
from .thesis import (
    ThesisError,
    ThesisNotFoundError,
    ThesisService,
    allowed_transitions,
    is_editable,
)

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health(container: Annotated[Container, Depends(get_container)]) -> HealthResponse:
    try:
        await ping(container.engine)
        database_ready = True
    except SQLAlchemyError:
        database_ready = False
    payload = {
        "status": "ok" if database_ready else "degraded",
        "trading212Configured": container.settings.t212_credentials() is not None,
        "databaseReady": database_ready,
    }
    return HealthResponse(**payload)


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
    local_action: Annotated[str | None, Header(alias="X-Helios-Local-Action")] = None,
    force_metadata: bool = False,
) -> PortfolioSyncSummary:
    if local_action != "sync":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required local action confirmation",
        )
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


@router.post("/api/v1/performance/replay", response_model=PerformanceReplaySummaryModel)
async def run_performance_replay(
    service: Annotated[PerformanceReplayService, Depends(get_performance_replay_service)],
    local_action: Annotated[str | None, Header(alias="X-Helios-Local-Action")] = None,
) -> PerformanceReplaySummaryModel:
    if local_action != "replay":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required local action confirmation",
        )
    summary = await service.replay()
    return PerformanceReplaySummaryModel.model_validate(summary, from_attributes=True)


@router.post("/api/v1/news/sync", response_model=NewsSyncSummaryModel)
async def run_news_sync(
    service: Annotated[NewsSyncService, Depends(get_news_sync_service)],
    local_action: Annotated[str | None, Header(alias="X-Helios-Local-Action")] = None,
) -> NewsSyncSummaryModel:
    if local_action != "news-sync":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required local action confirmation",
        )
    summary = await service.sync()
    return NewsSyncSummaryModel.model_validate(summary, from_attributes=True)


@router.get("/api/v1/news", response_model=list[NewsItemModel])
async def get_news(
    service: Annotated[NewsSyncService, Depends(get_news_sync_service)],
    ticker: str | None = None,
    isin: str | None = None,
    limit: int = 50,
) -> list[NewsItemModel]:
    if limit < 1 or limit > 200:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="limit must be between 1 and 200",
        )
    items = await service.list_news(t212_ticker=ticker, isin=isin, limit=limit)
    return [NewsItemModel.model_validate(item, from_attributes=True) for item in items]


@router.post("/api/v1/ai/analyse", response_model=AiAnalysisModel)
async def run_ai_analysis(
    service: Annotated[AiAnalysisService, Depends(get_ai_analysis_service)],
    local_action: Annotated[str | None, Header(alias="X-Helios-Local-Action")] = None,
) -> AiAnalysisModel:
    # This endpoint spends money, so it carries the same local-action guard as sync.
    if local_action != "ai-analyse":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required local action confirmation",
        )
    try:
        analysis = await service.analyse()
    except AiUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return AiAnalysisModel.model_validate(analysis, from_attributes=True)


@router.get("/api/v1/ai/latest", response_model=AiAnalysisModel)
async def get_latest_ai_analysis(
    service: Annotated[AiAnalysisService, Depends(get_ai_analysis_service)],
) -> AiAnalysisModel:
    analysis = await service.latest()
    if analysis is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No AI analysis has been run yet"
        )
    return AiAnalysisModel.model_validate(analysis, from_attributes=True)


@router.get("/api/v1/performance/report", response_model=PerformanceReportModel)
async def get_performance_report(
    service: Annotated[PerformanceReplayService, Depends(get_performance_replay_service)],
) -> PerformanceReportModel:
    try:
        report = await service.get_report()
    except NoPerformanceDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No performance report available",
        ) from exc
    return PerformanceReportModel.model_validate(report, from_attributes=True)


# --- Milestone 7: theses and journal --------------------------------------------------


def _thesis_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ThesisNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.get("/api/v1/theses", response_model=list[ThesisModel])
async def list_theses(
    service: Annotated[ThesisService, Depends(get_thesis_service)],
    status_filter: str | None = None,
) -> list[ThesisModel]:
    try:
        rows = await service.list_theses(status=status_filter)
    except ThesisError as exc:
        raise _thesis_http_error(exc) from exc
    return [ThesisModel.model_validate(row, from_attributes=True) for row in rows]


@router.post("/api/v1/theses", response_model=ThesisModel)
async def create_thesis(
    payload: ThesisCreateRequest,
    service: Annotated[ThesisService, Depends(get_thesis_service)],
) -> ThesisModel:
    try:
        thesis = await service.create(
            title=payload.title,
            body=payload.body,
            t212_ticker=payload.t212_ticker,
            isin=payload.isin,
            conviction=payload.conviction,
            opened_on=payload.opened_on,
        )
    except ThesisError as exc:
        raise _thesis_http_error(exc) from exc
    return ThesisModel.model_validate(thesis, from_attributes=True)


@router.get("/api/v1/theses/{thesis_id}", response_model=ThesisDetailModel)
async def get_thesis(
    thesis_id: int,
    service: Annotated[ThesisService, Depends(get_thesis_service)],
) -> ThesisDetailModel:
    try:
        thesis = await service.get(thesis_id)
    except ThesisNotFoundError as exc:
        raise _thesis_http_error(exc) from exc
    context = await service.context_for(thesis)
    journal = await service.list_journal(thesis_id=thesis_id)
    return ThesisDetailModel.model_validate(
        {
            "thesis": ThesisModel.model_validate(thesis, from_attributes=True),
            "context": context,
            "allowedTransitions": allowed_transitions(thesis.status),
            "editable": is_editable(thesis.status),
            "journal": [
                JournalEntryModel.model_validate(row, from_attributes=True) for row in journal
            ],
        }
    )


@router.patch("/api/v1/theses/{thesis_id}", response_model=ThesisModel)
async def edit_thesis(
    thesis_id: int,
    payload: ThesisEditRequest,
    service: Annotated[ThesisService, Depends(get_thesis_service)],
) -> ThesisModel:
    try:
        thesis = await service.edit(
            thesis_id,
            title=payload.title,
            body=payload.body,
            conviction=payload.conviction,
        )
    except (ThesisError, ThesisNotFoundError) as exc:
        raise _thesis_http_error(exc) from exc
    return ThesisModel.model_validate(thesis, from_attributes=True)


@router.post("/api/v1/theses/{thesis_id}/transition", response_model=ThesisModel)
async def transition_thesis(
    thesis_id: int,
    payload: ThesisTransitionRequest,
    service: Annotated[ThesisService, Depends(get_thesis_service)],
) -> ThesisModel:
    try:
        thesis = await service.transition(
            thesis_id, to_status=payload.to_status, outcome_note=payload.outcome_note
        )
    except (ThesisError, ThesisNotFoundError) as exc:
        raise _thesis_http_error(exc) from exc
    return ThesisModel.model_validate(thesis, from_attributes=True)


@router.get("/api/v1/journal", response_model=list[JournalEntryModel])
async def list_journal(
    service: Annotated[ThesisService, Depends(get_thesis_service)],
    thesis_id: int | None = None,
    limit: int = 100,
) -> list[JournalEntryModel]:
    if limit < 1 or limit > 500:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="limit must be between 1 and 500",
        )
    rows = await service.list_journal(thesis_id=thesis_id, limit=limit)
    return [JournalEntryModel.model_validate(row, from_attributes=True) for row in rows]


@router.post("/api/v1/journal", response_model=JournalEntryModel)
async def create_journal_entry(
    payload: JournalCreateRequest,
    service: Annotated[ThesisService, Depends(get_thesis_service)],
) -> JournalEntryModel:
    try:
        entry = await service.add_journal_entry(
            note=payload.note, thesis_id=payload.thesis_id, tags=payload.tags
        )
    except (ThesisError, ThesisNotFoundError) as exc:
        raise _thesis_http_error(exc) from exc
    return JournalEntryModel.model_validate(entry, from_attributes=True)
