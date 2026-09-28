from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.exc import SQLAlchemyError

from .ai import AiAnalysisService, AiUnavailableError
from .api_guards import (
    rate_limited,
    require_loopback_client,
    require_same_origin,
    restart_limiter,
    settings_write_limiter,
)
from .card_history import CardHistoryService, ExportFormatError, summarise_card_history
from .client import (
    Trading212CredentialsError,
    Trading212Error,
    Trading212HTTPError,
    Trading212ParseError,
    Trading212TransportError,
)
from .config import Settings, env_file_path
from .database_admin import (
    DatabaseAdminError,
    create_database,
    list_databases,
    resolve_switch_target,
)
from .db import ping
from .dependencies import (
    Container,
    get_ai_analysis_service,
    get_card_history_service,
    get_container,
    get_instrument_detail_service,
    get_news_sync_service,
    get_performance_replay_service,
    get_portfolio_quality_report_service,
    get_portfolio_sync_service,
    get_t212_service,
    get_thesis_service,
)
from .instrument_detail import InstrumentDetailService, UnknownInstrumentError
from .logging import get_logger
from .news import NewsSyncService
from .performance import (
    MarketDataProviderError,
    NoPerformanceDataError,
    PerformanceReplayService,
)
from .portfolio_repository import SyncAlreadyRunningError
from .portfolio_sync import PortfolioSyncService
from .reporting import NoQualityReportDataError, PortfolioQualityReportService
from .schemas import (
    AccountSummary,
    AiAnalysisModel,
    CardHistoryModel,
    CardRefreshModel,
    CredentialWriteRequest,
    CredentialWriteResponse,
    DatabaseActionResponse,
    DatabaseCreateRequest,
    DatabaseInfoModel,
    DatabaseListModel,
    DatabaseSwitchRequest,
    EditableSettingRequest,
    EditableSettingResponse,
    HealthResponse,
    InstrumentDetailModel,
    JournalCreateRequest,
    JournalEntryModel,
    NewsItemModel,
    NewsSyncSummaryModel,
    PerformanceReplaySummaryModel,
    PerformanceReportModel,
    PortfolioSyncSummary,
    Position,
    QualityReport,
    RestartResponse,
    SettingsSnapshotModel,
    ThesisCreateRequest,
    ThesisDetailModel,
    ThesisEditRequest,
    ThesisModel,
    ThesisTransitionRequest,
    Trading212ConnectRequest,
    Trading212ConnectResponse,
)
from .services import Trading212Service
from .settings_service import (
    SETTINGS_READ_ONLY_DETAIL,
    T212_ENVIRONMENT_URLS,
    SettingsWriteError,
    apply_editable_setting,
    audit_settings_change,
    effective_t212_base_url,
    read_snapshot,
    store_credential,
    verify_t212_credentials,
    write_env_settings,
)
from .thesis import (
    ThesisError,
    ThesisNotFoundError,
    ThesisService,
    allowed_transitions,
    is_editable,
)

logger = get_logger(__name__)

router = APIRouter()

LOCAL_ACTION_HEADER = "X-Helios-Local-Action"


def require_local_action(expected: str) -> Callable[[str | None], None]:
    """Build a dependency asserting the caller named the action it is performing.

    The boundary this guards is *mutating vs non-mutating*, not *expensive vs cheap*: a
    browser that wandered onto a loopback page can POST a simple form, but it cannot set a
    custom header cross-origin without a passing CORS preflight, and Helios configures none.
    So the header marks a caller as Helios' own server-side code rather than ambient traffic.

    It is a local-caller gate, not authentication. If Helios ever stops binding to loopback
    or gains a second user, this must be replaced with real auth and CSRF protection.
    """

    def guard(
        local_action: Annotated[str | None, Header(alias=LOCAL_ACTION_HEADER)] = None,
    ) -> None:
        if local_action != expected:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Missing required local action confirmation",
            )

    return guard


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
        # Which Trading 212 account the credentials belong to. Shown in the dashboard's header,
        # because demo and live numbers look identical and mixing them up is the costly mistake.
        "trading212Environment": (
            "demo" if "demo." in container.settings.t212_base_url else "live"
        ),
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


@router.get("/api/v1/t212/account", response_model=AccountSummary)
async def get_account_summary(
    service: Annotated[Trading212Service, Depends(get_t212_service)],
) -> AccountSummary:
    """Trading 212's own current totals: the single source for every "now" figure."""
    try:
        return await service.get_account_summary()
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


#: Background refreshes started after a manual sync, kept referenced so they are not collected.
_background_refreshes: set[asyncio.Task[None]] = set()


async def _refresh_after_sync(container: Container) -> None:
    """After new ledger data lands, recompute history and refresh news -- without being asked.

    A sync changes what every page shows; leaving the replay and the news for separate clicks
    meant the dashboard sat half-updated. Runs in the background so the Sync button answers as
    soon as the data is in. Each step is best effort: the replay takes its own lease (a
    concurrent run is refused, not duplicated) and a failing publisher cannot stop the replay.
    """

    try:
        await container.performance_replay_service.replay()
    except Exception as exc:
        logger.warning("helios.refresh_replay_failed", error=exc.__class__.__name__)
    try:
        await container.news_sync_service.sync()
    except Exception as exc:
        logger.warning("helios.refresh_news_failed", error=exc.__class__.__name__)


@router.post("/api/v1/portfolio/sync", response_model=PortfolioSyncSummary)
async def run_portfolio_sync(
    container: Annotated[Container, Depends(get_container)],
    service: Annotated[PortfolioSyncService, Depends(get_portfolio_sync_service)],
    _guard: Annotated[None, Depends(require_local_action("sync"))],
    force_metadata: bool = False,
) -> PortfolioSyncSummary:
    if container.settings.t212_credentials() is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Trading 212 credentials are not configured",
        )
    try:
        summary = await service.sync(force_metadata=force_metadata)
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
    # Fresh ledger data: drop the cached live reads, then bring everything derived up to date.
    container.t212_service.invalidate()
    if container.settings.refresh_after_sync:
        task = asyncio.create_task(_refresh_after_sync(container))
        _background_refreshes.add(task)
        task.add_done_callback(_background_refreshes.discard)
    return summary


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
    _guard: Annotated[None, Depends(require_local_action("replay"))],
) -> PerformanceReplaySummaryModel:
    try:
        summary = await service.replay()
    except SyncAlreadyRunningError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Performance replay already running",
        ) from exc
    except MarketDataProviderError as exc:
        # The provider's own words (already URL- and key-free), not "Internal Server Error".
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from None
    return PerformanceReplaySummaryModel.model_validate(summary, from_attributes=True)


@router.post("/api/v1/news/sync", response_model=NewsSyncSummaryModel)
async def run_news_sync(
    service: Annotated[NewsSyncService, Depends(get_news_sync_service)],
    _guard: Annotated[None, Depends(require_local_action("news-sync"))],
) -> NewsSyncSummaryModel:
    summary = await service.sync()
    return NewsSyncSummaryModel.model_validate(summary, from_attributes=True)


async def _replay_after_card_export(container: Container) -> None:
    """A downloaded export relabels card payments and cashback: recompute history with it."""

    try:
        await container.performance_replay_service.replay()
    except Exception as exc:
        logger.warning("helios.card_replay_failed", error=exc.__class__.__name__)


@router.get("/api/v1/instruments/{ticker}", response_model=InstrumentDetailModel)
async def get_instrument_detail(
    ticker: str,
    service: Annotated[InstrumentDetailService, Depends(get_instrument_detail_service)],
) -> InstrumentDetailModel:
    """Price history, your position, trades, dividends and per-period results for one ticker."""

    try:
        detail = await service.detail(ticker)
    except UnknownInstrumentError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unknown instrument"
        ) from exc
    return InstrumentDetailModel.model_validate(detail, from_attributes=True)


@router.get("/api/v1/card", response_model=CardHistoryModel)
async def get_card_history(
    container: Annotated[Container, Depends(get_container)],
    service: Annotated[CardHistoryService, Depends(get_card_history_service)],
) -> CardHistoryModel:
    rows = await container.portfolio_repository.list_export_rows()
    return CardHistoryModel.model_validate(
        {"status": await service.status(), "summary": summarise_card_history(rows)},
        from_attributes=True,
    )


@router.post("/api/v1/card/refresh", response_model=CardRefreshModel)
async def refresh_card_history(
    container: Annotated[Container, Depends(get_container)],
    service: Annotated[CardHistoryService, Depends(get_card_history_service)],
    _guard: Annotated[None, Depends(require_local_action("card-refresh"))],
) -> CardRefreshModel:
    """Collect a finished export, or ask Trading 212 for a new one now.

    Asking sends a notification to the Trading 212 app, which is why this is a deliberate
    button press and the schedule only does it once per `card_export_cadence_hours`.
    """

    try:
        result = await service.refresh(force=True)
    except (Trading212Error, ExportFormatError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Card history refresh failed: {exc.__class__.__name__}",
        ) from exc
    if result.action == "downloaded":
        task = asyncio.create_task(_replay_after_card_export(container))
        _background_refreshes.add(task)
        task.add_done_callback(_background_refreshes.discard)
    return CardRefreshModel.model_validate(result, from_attributes=True)


@router.get("/api/v1/news", response_model=list[NewsItemModel])
async def get_news(
    service: Annotated[NewsSyncService, Depends(get_news_sync_service)],
    ticker: str | None = None,
    isin: str | None = None,
    limit: int = 50,
    held_only: Annotated[bool, Query(alias="heldOnly")] = False,
    mentions_only: Annotated[bool, Query(alias="mentionsOnly")] = False,
) -> list[NewsItemModel]:
    if limit < 1 or limit > 200:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="limit must be between 1 and 200",
        )
    ranked = await service.list_ranked_news(
        t212_ticker=ticker,
        isin=isin,
        limit=limit,
        held_only=held_only,
        mentions_only=mentions_only,
    )
    return [
        NewsItemModel.model_validate(entry.item, from_attributes=True).model_copy(
            update={
                "relevance": entry.relevance,
                "matched_term": entry.matched_term,
                "held": entry.held,
            }
        )
        for entry in ranked
    ]


@router.post("/api/v1/ai/analyse", response_model=AiAnalysisModel)
async def run_ai_analysis(
    service: Annotated[AiAnalysisService, Depends(get_ai_analysis_service)],
    _guard: Annotated[None, Depends(require_local_action("ai-analyse"))],
) -> AiAnalysisModel:
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
    _guard: Annotated[None, Depends(require_local_action("thesis-write"))],
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
    _guard: Annotated[None, Depends(require_local_action("thesis-write"))],
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
    _guard: Annotated[None, Depends(require_local_action("thesis-write"))],
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
    _guard: Annotated[None, Depends(require_local_action("journal-write"))],
) -> JournalEntryModel:
    try:
        entry = await service.add_journal_entry(
            note=payload.note, thesis_id=payload.thesis_id, tags=payload.tags
        )
    except (ThesisError, ThesisNotFoundError) as exc:
        raise _thesis_http_error(exc) from exc
    return JournalEntryModel.model_validate(entry, from_attributes=True)


# --- Settings ---------------------------------------------------------------------------
#
# Every route below stacks four checks: the local-action header, a loopback peer address, fetch
# metadata, and a rate limit. That is heavier than the rest of the API on purpose -- these are
# the only routes that can rewrite a brokerage credential or bounce the process.

#: Settings are read into a frozen `Settings` object at startup, so a saved value does not take
#: effect until the process restarts. Rather than pretend otherwise, the snapshot reports this
#: and the dashboard shows a banner until the restart happens.
_restart_required = False


def _mark_restart_required(settings: Settings | None = None) -> None:
    """Note that saved settings are not live yet, and -- if enabled -- apply them shortly.

    Applying means restarting: settings are read once at startup. The restart is debounced:
    each save pushes it back by :data:`AUTO_APPLY_DELAY_SECONDS`, so entering a key, choosing a
    provider and entering a second key costs one restart, not three (and stays inside the
    restart route's rate limit). After the restart the worker's startup cycle syncs, replays and
    refreshes news, so the change is visible everywhere without another click.
    """

    global _restart_required, _auto_apply_handle
    _restart_required = True
    if settings is None or not settings.auto_apply_settings or not settings.settings_writable:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _auto_apply_handle is not None:
        _auto_apply_handle.cancel()
    _auto_apply_handle = loop.call_later(AUTO_APPLY_DELAY_SECONDS, _auto_apply)


def _apply_hint(settings: Settings) -> str:
    """What happens next, in the words the operator reads under the Save button."""

    if settings.auto_apply_settings and settings.settings_writable:
        return "Helios applies it in a few seconds, then syncs and recomputes automatically."
    return "Restart to apply."


#: Quiet period after the last settings save before Helios restarts to apply it.
AUTO_APPLY_DELAY_SECONDS = 8.0

_auto_apply_handle: asyncio.TimerHandle | None = None


def _auto_apply() -> None:
    global _auto_apply_handle
    _auto_apply_handle = None
    _clear_restart_required()
    audit_settings_change(field="process", action="restart", destination="auto-apply")
    _perform_restart()


def _clear_restart_required() -> None:
    global _restart_required
    _restart_required = False


def _env_path(settings: Settings) -> Path:
    """Where `.env` lives: exactly the file `Settings` reads (see `config.env_file_path`)."""

    del settings
    return env_file_path()


#: Seconds to wait before replacing the process. Long enough for the HTTP response to leave the
#: socket, short enough that the operator does not wonder whether the click registered.
RESTART_DELAY_SECONDS = 0.5


def schedule_restart(delay_seconds: float = RESTART_DELAY_SECONDS) -> None:
    """Replace this process shortly after the current response is sent.

    Settings are frozen at startup, so a saved credential does nothing until the process comes
    back. Doing that in-band -- exiting inside the request handler -- would drop the response and
    leave the dashboard unable to say whether the restart was accepted, so the exit is deferred
    onto the event loop instead.

    Two mechanisms, because two deployments:

    * **Under a supervisor** (Docker, systemd) the right move is to exit and let the restart
      policy start a clean process. `os.execv` inside a container works but keeps the old PID 1
      semantics, and a supervisor that is already watching should be the thing that restarts us.
    * **Run bare** there is no supervisor, so `os.execv` replaces the image in place -- the only
      way a `uvicorn` started by hand comes back at all.

    `HELIOS_RESTART_MODE=exit` forces the first. Anything else re-execs.
    """

    loop = asyncio.get_running_loop()
    loop.call_later(delay_seconds, _perform_restart)


def restart_argv() -> list[str]:
    """The argument vector that re-runs this process, quoted for the platform's exec.

    Built from ``sys.orig_argv`` rather than ``sys.argv``. Under ``python -m uvicorn`` the latter
    has already been rewritten to uvicorn's ``__main__.py``, and running that file as a script
    puts uvicorn's own package directory first on the import path, where its ``logging.py``
    shadows the standard library and the new process dies on import. The original vector keeps
    ``-m uvicorn`` and any interpreter flags exactly as the operator typed them.

    On Windows the CRT joins exec arguments with spaces and does *not* quote them, so an
    interpreter under a path with a space in it -- a project folder like `Portolio Tracker` --
    was split at the space and the restart killed the API instead of bouncing it. Quoting each
    argument the way `subprocess` does for a command line restores the original vector.
    """

    argv = [sys.executable, *sys.orig_argv[1:]]
    if os.name == "nt":
        return [subprocess.list2cmdline([arg]) for arg in argv]
    return argv


def _perform_restart() -> None:
    """Actually bounce the process. Never raises into the caller -- there is nobody to tell."""

    if os.environ.get("HELIOS_RESTART_MODE", "").strip().lower() == "exit":
        # SIGTERM rather than os._exit so uvicorn runs its shutdown handlers and the container's
        # restart policy sees a clean stop rather than a crash.
        os.kill(os.getpid(), signal.SIGTERM)
        return
    try:
        os.execv(sys.executable, restart_argv())
    except OSError:
        # An exec that fails leaves the process alive but with settings the operator believes
        # were applied. Falling back to a clean stop is the honest outcome.
        logger.error("helios.restart_exec_failed", executable=sys.executable, argv=sys.argv)
        os.kill(os.getpid(), signal.SIGTERM)


def require_writable_settings(container: Annotated[Container, Depends(get_container)]) -> None:
    """Refuse a settings change the deployment could never apply.

    Checked after the caller guards, so an off-machine caller still learns nothing beyond "no".
    409 rather than 403: the caller is allowed, the deployment is simply in a state where this
    write would be a lie -- saved somewhere, and never read.
    """

    if not container.settings.settings_writable:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=SETTINGS_READ_ONLY_DETAIL)


SettingsGuards = [
    Depends(require_local_action("settings-write")),
    Depends(require_loopback_client),
    Depends(require_same_origin),
    Depends(rate_limited(settings_write_limiter, "settings")),
    Depends(require_writable_settings),
]


@router.get("/api/v1/settings", response_model=SettingsSnapshotModel)
async def get_settings(
    container: Annotated[Container, Depends(get_container)],
    _loopback: Annotated[None, Depends(require_loopback_client)],
    _origin: Annotated[None, Depends(require_same_origin)],
) -> SettingsSnapshotModel:
    """Describe configuration without disclosing a credential.

    Read is guarded by loopback and origin but not by the action header, because the dashboard
    renders this on page load like any other read. It is safe to expose to a local caller
    precisely because it contains no secret -- only presence, a four-character tail, and what
    each missing credential costs.
    """

    snapshot = read_snapshot(
        container.settings,
        env_path=_env_path(container.settings),
        restart_required=_restart_required,
    )
    return SettingsSnapshotModel.model_validate(snapshot, from_attributes=True)


@router.put(
    "/api/v1/settings/credential",
    response_model=CredentialWriteResponse,
    dependencies=SettingsGuards,
)
async def put_credential(
    payload: CredentialWriteRequest,
    container: Annotated[Container, Depends(get_container)],
) -> CredentialWriteResponse:
    """Store one credential, verifying it first where verification is possible.

    The Trading 212 pair is checked against the live API before it is written, because a key
    that silently does not work costs an afternoon to diagnose. The other providers are stored
    unverified: a probe would either cost money (Anthropic) or spend a metered call for no
    diagnostic gain.
    """

    settings = container.settings
    verified: str | None = None

    if payload.field in {"t212_api_key", "t212_api_secret"}:
        # Verification needs both halves. Take the incoming one and pair it with what is already
        # configured; if the counterpart is missing, store without verifying and say so.
        api_key = payload.value if payload.field == "t212_api_key" else settings.t212_api_key
        secret_source = (
            payload.value
            if payload.field == "t212_api_secret"
            else (settings.t212_api_secret.get_secret_value() if settings.t212_api_secret else None)
        )
        if payload.value.strip() and api_key and secret_source:
            try:
                # The environment the operator has chosen, not the one this process
                # started with: after switching to live, a live key checked against the
                # frozen demo URL is rejected for being exactly right.
                verified = await verify_t212_credentials(
                    api_key=api_key,
                    api_secret=secret_source,
                    base_url=effective_t212_base_url(
                        settings, env_path=_env_path(settings)
                    ),
                )
            except SettingsWriteError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
                ) from exc

    try:
        destination = store_credential(payload.field, payload.value)
    except SettingsWriteError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    _mark_restart_required(container.settings)

    if destination == "cleared":
        detail = f"Credential cleared. {_apply_hint(settings)}"
    elif verified:
        detail = (
            f"Stored in the OS keyring and verified against Trading 212 {verified}. "
            f"{_apply_hint(settings)}"
        )
    else:
        detail = f"Stored in the OS keyring. {_apply_hint(settings)}"

    return CredentialWriteResponse.model_validate(
        {
            "field": payload.field,
            "storedIn": destination,
            "verified": verified,
            "restartRequired": True,
            "detail": detail,
        }
    )


@router.put(
    "/api/v1/settings/trading212",
    response_model=Trading212ConnectResponse,
    dependencies=SettingsGuards,
)
async def put_trading212(
    payload: Trading212ConnectRequest,
    container: Annotated[Container, Depends(get_container)],
) -> Trading212ConnectResponse:
    """Connect a Trading 212 account in one step: environment, key and secret together.

    The three only mean anything as a set -- a key belongs to exactly one environment, and a key
    without its secret authenticates nothing -- so they are verified together against the
    environment being chosen, and nothing is stored unless that verification passes. Saving them
    one field at a time is how a live key ended up checked against the demo host.
    """

    api_key = payload.api_key.strip()
    api_secret = payload.api_secret.strip()
    if not api_key or not api_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Paste both the API key and the API secret -- Trading 212 issues them as a pair."
            ),
        )
    base_url = T212_ENVIRONMENT_URLS[payload.environment]

    try:
        verified = await verify_t212_credentials(
            api_key=api_key, api_secret=api_secret, base_url=base_url
        )
        store_credential("t212_api_key", api_key)
        store_credential("t212_api_secret", api_secret)
        write_env_settings(_env_path(container.settings), {"HELIOS_T212_BASE_URL": base_url})
    except SettingsWriteError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not write .env: {exc}",
        ) from exc

    audit_settings_change(field="t212_connection", action="stored", destination="keyring")
    _mark_restart_required(container.settings)
    account = "Practice" if payload.environment == "demo" else "Live"
    return Trading212ConnectResponse.model_validate(
        {
            "environment": payload.environment,
            "verified": verified,
            "restartRequired": True,
            "detail": (
                f"Verified against your {account} account ({verified}) and saved. "
                f"{_apply_hint(container.settings)}"
            ),
        }
    )


@router.put(
    "/api/v1/settings/value",
    response_model=EditableSettingResponse,
    dependencies=SettingsGuards,
)
async def put_editable_setting(
    payload: EditableSettingRequest,
    container: Annotated[Container, Depends(get_container)],
) -> EditableSettingResponse:
    """Write one non-credential setting to `.env`, validated against its allowed values."""

    try:
        env_name, normalised = apply_editable_setting(payload.field, payload.value)
        write_env_settings(_env_path(container.settings), {env_name: normalised})
    except SettingsWriteError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not write .env: {exc}",
        ) from exc

    audit_settings_change(field=payload.field, action="stored", destination="env")
    _mark_restart_required(container.settings)

    return EditableSettingResponse.model_validate(
        {
            "field": payload.field,
            "envName": env_name,
            "value": normalised,
            "restartRequired": True,
            "detail": f"{env_name} set to {normalised}. {_apply_hint(container.settings)}",
        }
    )


@router.get("/api/v1/settings/databases", response_model=DatabaseListModel)
async def get_databases(
    container: Annotated[Container, Depends(get_container)],
    _loopback: Annotated[None, Depends(require_loopback_client)],
    _origin: Annotated[None, Depends(require_same_origin)],
) -> DatabaseListModel:
    active, rows = list_databases(container.settings)
    return DatabaseListModel.model_validate(
        {
            "dataDir": str(container.settings.data_dir),
            "active": active,
            "databases": [
                DatabaseInfoModel.model_validate(row, from_attributes=True) for row in rows
            ],
        }
    )


@router.post(
    "/api/v1/settings/databases",
    response_model=DatabaseActionResponse,
    dependencies=SettingsGuards,
)
async def post_database(
    payload: DatabaseCreateRequest,
    container: Annotated[Container, Depends(get_container)],
) -> DatabaseActionResponse:
    """Create an empty database at the current schema head, without switching to it."""

    try:
        name = await create_database(container.settings, payload.filename)
    except DatabaseAdminError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    audit_settings_change(field="database", action="created", destination=name)
    return DatabaseActionResponse.model_validate(
        {
            "filename": name,
            "created": True,
            "active": False,
            "restartRequired": False,
            "detail": f"{name} created at the current schema. Switch to it when you are ready.",
        }
    )


@router.put(
    "/api/v1/settings/databases/active",
    response_model=DatabaseActionResponse,
    dependencies=SettingsGuards,
)
async def put_active_database(
    payload: DatabaseSwitchRequest,
    container: Annotated[Container, Depends(get_container)],
) -> DatabaseActionResponse:
    """Point Helios at a different database file. Takes effect on restart.

    The previous database is left untouched on disk, so switching back is another switch rather
    than a restore.
    """

    try:
        name = resolve_switch_target(container.settings, payload.filename)
        write_env_settings(_env_path(container.settings), {"HELIOS_SQLITE_FILENAME": name})
    except DatabaseAdminError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not write .env: {exc}",
        ) from exc

    audit_settings_change(field="database", action="switched", destination=name)
    _mark_restart_required(container.settings)

    return DatabaseActionResponse.model_validate(
        {
            "filename": name,
            "created": False,
            "active": True,
            "restartRequired": True,
            "detail": (
                f"Helios will use {name} from now on. {_apply_hint(container.settings)} "
                "The previous database is kept."
            ),
        }
    )


@router.post(
    "/api/v1/settings/restart",
    response_model=RestartResponse,
    dependencies=[
        Depends(require_local_action("restart")),
        Depends(require_loopback_client),
        Depends(require_same_origin),
        Depends(rate_limited(restart_limiter, "restart")),
        # Nothing the dashboard could have saved needs applying, and under a supervisor a
        # restart only drops in-flight requests.
        Depends(require_writable_settings),
    ],
)
async def post_restart() -> RestartResponse:
    """Re-exec the API process so saved settings take effect.

    The response is sent first and the re-exec happens after, so the caller learns the restart
    was accepted rather than seeing a dropped connection. In-flight requests are lost -- that is
    inherent to restarting, which is why this route carries the tightest rate limit in the API
    and why the dashboard asks before calling it.

    Under Docker this exits and the container's restart policy brings it back; run bare, the
    re-exec replaces the process in place.
    """

    _clear_restart_required()
    audit_settings_change(field="process", action="restart", destination="self")
    schedule_restart()
    return RestartResponse.model_validate(
        {
            "scheduled": True,
            "detail": "Restarting now. The dashboard will reconnect in a few seconds.",
        }
    )
