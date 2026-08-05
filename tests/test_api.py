from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from helios.app import create_app
from helios.config import Settings


def test_health_reports_local_configuration(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)

    with TestClient(create_app(settings)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "trading212Configured": False,
        "sqlitePath": str(tmp_path / "helios.sqlite3"),
    }


def test_positions_are_unavailable_without_credentials(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)

    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/t212/positions")

    assert response.status_code == 503
    assert response.json() == {"detail": "Trading 212 credentials are not configured"}
