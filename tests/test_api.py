from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from pydantic import SecretStr

from helios.app import create_app
from helios.client import Trading212TransportError
from helios.config import Settings
from helios.dependencies import (
    get_container,
    get_news_sync_service,
    get_performance_replay_service,
    get_portfolio_quality_report_service,
    get_portfolio_sync_service,
)
from helios.performance import NoPerformanceDataError
from helios.portfolio_repository import SyncAlreadyRunningError
from helios.reporting import NoQualityReportDataError
from helios.schemas import (
    NewsSyncSummaryModel,
    PerformanceReplaySummaryModel,
    PortfolioSyncSummary,
    QualityReport,
)


@dataclass
class FakeSyncService:
    result: PortfolioSyncSummary | None = None
    error: Exception | None = None
    force_metadata_calls: list[bool] | None = None

    async def sync(self, *, force_metadata: bool = False) -> PortfolioSyncSummary:
        if self.force_metadata_calls is not None:
            self.force_metadata_calls.append(force_metadata)
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


@dataclass
class FakeQualityService:
    result: QualityReport | None = None
    error: Exception | None = None

    async def get_report(self) -> QualityReport:
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


@dataclass
class FakePerformanceService:
    result: object | None = None
    error: Exception | None = None

    async def replay(self, *, as_of: object | None = None) -> object:
        del as_of
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result

    async def get_report(self) -> object:
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def test_health_reports_local_configuration(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)

    with TestClient(create_app(settings)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "trading212Configured": False,
        "databaseReady": True,
    }


def test_positions_are_unavailable_without_credentials(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)

    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/t212/positions")

    assert response.status_code == 503
    assert response.json() == {"detail": "Trading 212 credentials are not configured"}


def test_portfolio_sync_endpoint_returns_summary_and_force_flag(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, t212_api_key="key", t212_api_secret=SecretStr("secret"))
    calls: list[bool] = []
    sync_service = FakeSyncService(
        result=PortfolioSyncSummary.model_validate(
            {
                "asOf": "2024-01-01T00:00:00Z",
                "metadataFetched": True,
                "endpoints": [{"endpoint": "/equity/positions", "fetched": True, "itemCount": 1}],
            }
        ),
        force_metadata_calls=calls,
    )
    app = create_app(settings)
    app.dependency_overrides[get_container] = lambda: SimpleNamespace(settings=settings)
    app.dependency_overrides[get_portfolio_sync_service] = lambda: sync_service

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/portfolio/sync?force_metadata=true",
            headers={"X-Helios-Local-Action": "sync"},
        )

    assert response.status_code == 200
    assert response.json()["metadataFetched"] is True
    assert calls == [True]


def test_portfolio_sync_endpoint_maps_safe_errors(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, t212_api_key="key", t212_api_secret=SecretStr("secret"))
    app = create_app(settings)
    app.dependency_overrides[get_container] = lambda: SimpleNamespace(settings=settings)

    scenarios = [
        (SyncAlreadyRunningError("busy"), 409, "Portfolio sync already running"),
        (Trading212TransportError("boom"), 502, "Trading 212 request failed"),
    ]
    for error, status_code, detail in scenarios:
        app.dependency_overrides[get_portfolio_sync_service] = lambda error=error: FakeSyncService(
            error=error
        )
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/portfolio/sync",
                headers={"X-Helios-Local-Action": "sync"},
            )
        assert response.status_code == status_code
        assert response.json() == {"detail": detail}


def test_portfolio_sync_endpoint_rejects_missing_credentials_before_sync(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    app = create_app(settings)
    app.dependency_overrides[get_container] = lambda: SimpleNamespace(settings=settings)
    app.dependency_overrides[get_portfolio_sync_service] = lambda: FakeSyncService(
        error=AssertionError("should not run")
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/portfolio/sync",
            headers={"X-Helios-Local-Action": "sync"},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "Trading 212 credentials are not configured"}


def test_portfolio_sync_endpoint_requires_local_action_header(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, t212_api_key="key", t212_api_secret=SecretStr("secret"))
    app = create_app(settings)
    app.dependency_overrides[get_container] = lambda: SimpleNamespace(settings=settings)
    app.dependency_overrides[get_portfolio_sync_service] = lambda: FakeSyncService(
        error=AssertionError("should not run")
    )

    with TestClient(app) as client:
        missing = client.post("/api/v1/portfolio/sync")
        wrong = client.post(
            "/api/v1/portfolio/sync",
            headers={"X-Helios-Local-Action": "wrong"},
        )

    assert missing.status_code == 403
    assert missing.json() == {"detail": "Missing required local action confirmation"}
    assert wrong.status_code == 403
    assert wrong.json() == {"detail": "Missing required local action confirmation"}


def test_quality_report_endpoint_maps_found_and_not_found(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    report = QualityReport.model_validate(
        {
            "asOf": "2024-01-01T00:00:00Z",
            "overallStatus": "OK",
            "endpointStatuses": [],
            "metadataFreshness": {
                "endpoint": "/equity/metadata/instruments",
                "fresh": True,
                "ttlHours": 24,
                "checkedAt": "2024-01-01T00:00:00Z",
                "lastSuccessAt": "2024-01-01T00:00:00Z",
            },
            "unresolvedInstruments": [],
            "ambiguousInstruments": [],
            "overrideRequiredInstruments": [],
            "reconciliationMismatches": [],
            "unsupportedActions": [],
        }
    )
    app = create_app(settings)
    app.dependency_overrides[get_portfolio_quality_report_service] = lambda: FakeQualityService(
        result=report
    )

    with TestClient(app) as client:
        ok_response = client.get("/api/v1/portfolio/data-quality")

    assert ok_response.status_code == 200
    assert ok_response.json()["overallStatus"] == "OK"

    app.dependency_overrides[get_portfolio_quality_report_service] = lambda: FakeQualityService(
        error=NoQualityReportDataError("missing")
    )
    with TestClient(app) as client:
        missing_response = client.get("/api/v1/portfolio/data-quality")

    assert missing_response.status_code == 404
    assert missing_response.json() == {"detail": "No portfolio quality report available"}


def test_performance_replay_endpoint_requires_local_action_header(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    app = create_app(settings)
    app.dependency_overrides[get_performance_replay_service] = lambda: FakePerformanceService(
        result=PerformanceReplaySummaryModel.model_validate(
            {
                "asOf": date(2024, 1, 2),
                "startDate": date(2024, 1, 1),
                "endDate": date(2024, 1, 2),
                "flowTiming": "flow_at_close",
                "holdingsWritten": 1,
                "navWritten": 1,
                "unsupportedQuantityEvents": [],
                "missingPriceSymbols": [],
                "stalePriceSymbols": [],
                "missingFxCurrencies": [],
                "staleFxCurrencies": [],
                "excludedFlowCurrencies": [],
                "notes": [],
            }
        )
    )

    with TestClient(app) as client:
        denied = client.post("/api/v1/performance/replay")
        allowed = client.post(
            "/api/v1/performance/replay",
            headers={"X-Helios-Local-Action": "replay"},
        )

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json()["holdingsWritten"] == 1


@dataclass
class FakeNewsService:
    items: list[object] | None = None
    summary: object | None = None
    seen: dict[str, object] | None = None

    async def sync(self) -> object:
        assert self.summary is not None
        return self.summary

    async def list_news(
        self, *, t212_ticker: str | None = None, isin: str | None = None, limit: int = 50
    ) -> list[object]:
        if self.seen is not None:
            self.seen.update({"ticker": t212_ticker, "isin": isin, "limit": limit})
        return self.items or []


def test_news_sync_endpoint_requires_local_action_header(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    app = create_app(settings)
    summary = NewsSyncSummaryModel.model_validate(
        {
            "asOf": "2024-05-01T12:00:00Z",
            "feedsConfigured": 0,
            "feedsFetched": 0,
            "rawStored": 0,
            "itemsParsed": 0,
            "itemsWritten": 0,
            "duplicatesSkipped": 0,
            "crossSourceMerges": 0,
            "sourcesUsed": [],
            "failures": [],
            "notes": ["No news sources configured."],
        }
    )
    app.dependency_overrides[get_news_sync_service] = lambda: FakeNewsService(summary=summary)

    with TestClient(app) as client:
        denied = client.post("/api/v1/news/sync")
        allowed = client.post("/api/v1/news/sync", headers={"X-Helios-Local-Action": "news-sync"})

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json()["notes"] == ["No news sources configured."]


def test_news_list_endpoint_passes_filters_and_validates_limit(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    app = create_app(settings)
    seen: dict[str, object] = {}
    item = SimpleNamespace(
        dedupe_key="abc",
        feed_key="mkt",
        provider="rss",
        source_label="Example Markets",
        t212_ticker="AAPL_US_EQ",
        isin="US037",
        headline="Apple beats expectations",
        summary=None,
        url="https://example.com/a",
        published_at=None,
        fetched_at="2024-05-01T12:00:00Z",
    )
    app.dependency_overrides[get_news_sync_service] = lambda: FakeNewsService(
        items=[item], seen=seen
    )

    with TestClient(app) as client:
        ok = client.get("/api/v1/news?ticker=AAPL_US_EQ&limit=5")
        too_many = client.get("/api/v1/news?limit=500")

    assert ok.status_code == 200
    assert seen == {"ticker": "AAPL_US_EQ", "isin": None, "limit": 5}
    body = ok.json()
    assert body[0]["sourceLabel"] == "Example Markets"
    assert body[0]["url"] == "https://example.com/a"
    assert too_many.status_code == 422


def test_performance_report_endpoint_maps_not_found(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    app = create_app(settings)
    app.dependency_overrides[get_performance_replay_service] = lambda: FakePerformanceService(
        error=NoPerformanceDataError("missing")
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/performance/report")

    assert response.status_code == 404
    assert response.json() == {"detail": "No performance report available"}
