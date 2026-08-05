from __future__ import annotations

from pathlib import Path

import pytest

from helios.config import Settings
from helios.dependencies import build_container


@pytest.mark.asyncio
async def test_container_wires_m2_services_and_shutdown_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = build_container(Settings(data_dir=tmp_path))

    assert container.portfolio_repository is not None
    assert container.instrument_resolver is not None
    assert container.portfolio_sync_service is not None
    assert container.portfolio_quality_report_service is not None

    order: list[str] = []

    async def close_resolver() -> None:
        order.append("resolver")

    async def close_client() -> None:
        order.append("client")

    monkeypatch.setattr(container.instrument_resolver, "aclose", close_resolver)
    monkeypatch.setattr(container.t212_client, "aclose", close_client)

    await container.shutdown()

    assert order == ["resolver", "client"]
