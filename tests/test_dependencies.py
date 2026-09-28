from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from helios.config import Settings
from helios.dependencies import build_container
from helios.performance import (
    AlphaVantageMarketDataProvider,
    CompositeMarketDataProvider,
    NullMarketDataProvider,
    TwelveDataMarketDataProvider,
)


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
    assert container.performance_replay_service is not None

    order: list[str] = []

    async def close_resolver() -> None:
        order.append("resolver")

    async def close_client() -> None:
        order.append("client")

    monkeypatch.setattr(container.instrument_resolver, "aclose", close_resolver)
    monkeypatch.setattr(container.t212_client, "aclose", close_client)

    await container.shutdown()

    assert order == ["resolver", "client"]


@pytest.mark.asyncio
async def test_market_data_provider_defaults_to_null_without_a_provider_configured(
    tmp_path: Path,
) -> None:
    container = build_container(Settings(data_dir=tmp_path))

    assert isinstance(container.market_data_provider, NullMarketDataProvider)


@pytest.mark.asyncio
async def test_market_data_fallback_disabled_yields_a_bare_primary_provider(
    tmp_path: Path,
) -> None:
    container = build_container(
        Settings(
            data_dir=tmp_path,
            market_data_provider="twelvedata",
            market_data_api_key="primary-key",
        )
    )

    assert isinstance(container.market_data_provider, TwelveDataMarketDataProvider)


@pytest.mark.asyncio
async def test_market_data_fallback_wires_a_composite_provider_with_its_own_credential(
    tmp_path: Path,
) -> None:
    """The recommended pairing: Twelve Data (US) as primary, Alpha Vantage (London) as fallback.

    Each provider must read its own credential -- constructing both against the same Settings
    object without separating the keys would have the fallback silently reading the primary's
    key (or vice versa).
    """
    container = build_container(
        Settings(
            data_dir=tmp_path,
            market_data_provider="twelvedata",
            market_data_api_key="primary-key",
            market_data_fallback_provider="alphavantage",
            market_data_fallback_api_key="fallback-key",
        )
    )

    provider = container.market_data_provider
    assert isinstance(provider, CompositeMarketDataProvider)
    primary = provider._primary
    fallback = provider._fallback
    assert isinstance(primary, TwelveDataMarketDataProvider)
    assert isinstance(fallback, AlphaVantageMarketDataProvider)
    assert primary._api_key == SecretStr("primary-key")
    assert fallback._api_key == SecretStr("fallback-key")
