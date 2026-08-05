from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from pydantic import SecretStr

from helios.app import create_app
from helios.client import Trading212TransportError
from helios.config import Settings
from helios.dependencies import (
    get_container,
    get_portfolio_quality_report_service,
    get_portfolio_sync_service,
)
from helios.portfolio_repository import SyncAlreadyRunningError
from helios.reporting import NoQualityReportDataError
from helios.schemas import PortfolioSyncSummary, QualityReport


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
                "endpoints": [
                    {"endpoint": "/equity/positions", "fetched": True, "itemCount": 1}
                ],
            }
        ),
        force_metadata_calls=calls,
    )
    app = create_app(settings)
    app.dependency_overrides[get_container] = lambda: SimpleNamespace(settings=settings)
    app.dependency_overrides[get_portfolio_sync_service] = lambda: sync_service

    with TestClient(app) as client:
        response = client.post("/api/v1/portfolio/sync?force_metadata=true")

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
            response = client.post("/api/v1/portfolio/sync")
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
        response = client.post("/api/v1/portfolio/sync")

    assert response.status_code == 503
    assert response.json() == {"detail": "Trading 212 credentials are not configured"}


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
