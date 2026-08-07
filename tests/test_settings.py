from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import SecretStr
from pytest import MonkeyPatch

from helios.config import Settings


def test_settings_use_keyring_secret_fallback(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("HELIOS_T212_API_KEY", "demo-key")
    monkeypatch.delenv("HELIOS_T212_API_SECRET", raising=False)

    def fake_get_password(service: str, username: str) -> str | None:
        assert service == "helios.t212"
        assert username == "demo-key"
        return "secret-from-keyring"

    monkeypatch.setattr("helios.config.keyring.get_password", fake_get_password)

    settings = Settings()

    assert settings.t212_api_secret == SecretStr("secret-from-keyring")


def test_explicit_secret_beats_keyring(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("HELIOS_T212_API_KEY", "demo-key")
    monkeypatch.setenv("HELIOS_T212_API_SECRET", "secret-from-env")
    monkeypatch.setattr(
        "helios.config.keyring.get_password",
        lambda _service, _username: "secret-from-keyring",
    )

    settings = Settings()

    assert settings.t212_api_secret == SecretStr("secret-from-env")


def test_blank_environment_credentials_are_unconfigured(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("HELIOS_T212_API_KEY", "")
    monkeypatch.setenv("HELIOS_T212_API_SECRET", "")

    settings = Settings()

    assert settings.t212_credentials() is None


def test_m2_settings_defaults() -> None:
    settings = Settings()

    assert settings.instrument_metadata_ttl_hours == 24
    assert settings.reconciliation_tolerance == Decimal("0.001")
    assert settings.instrument_overrides_path == Path("config/instrument_overrides.yaml")
    assert settings.openfigi_base_url == "https://api.openfigi.com/v3"
    assert settings.openfigi_api_key is None
    assert settings.resolver_timeout_seconds == 10.0
    assert settings.sync_cadence_minutes == 60


def test_blank_openfigi_key_is_unset(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("HELIOS_OPENFIGI_API_KEY", "   ")

    settings = Settings()

    assert settings.openfigi_api_key is None


def test_openfigi_key_is_secret_and_masked(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("HELIOS_OPENFIGI_API_KEY", "super-secret")

    settings = Settings()

    assert settings.openfigi_api_key == SecretStr("super-secret")
    assert "super-secret" not in repr(settings)
    assert str(settings.model_dump()["openfigi_api_key"]) == "**********"


def test_instrument_metadata_ttl_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        Settings(instrument_metadata_ttl_hours=0)


def test_resolver_timeout_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        Settings(resolver_timeout_seconds=0.0)


def test_sync_cadence_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        Settings(sync_cadence_minutes=0)


def test_reconciliation_tolerance_must_be_nonnegative() -> None:
    with pytest.raises(ValueError, match="nonnegative"):
        Settings(reconciliation_tolerance=Decimal("-0.001"))


def test_m3_analytics_settings_defaults() -> None:
    settings = Settings()

    assert settings.analytics_flow_timing == "flow_at_close"
    assert settings.analytics_max_price_stale_days == 10
    assert settings.analytics_max_fx_stale_days == 10
    assert settings.analytics_passive_benchmark_key == "vwrp"
    assert settings.market_data_provider == "disabled"
    assert settings.factor_data_provider == "disabled"
    # Helios never guesses a benchmark's listing currency.
    assert settings.benchmark_cspx_currency is None
    assert settings.benchmark_swda_currency is None
    assert settings.benchmark_vwrp_currency is None


def test_base_currency_must_be_eur() -> None:
    with pytest.raises(ValueError, match="base_currency must be EUR"):
        Settings(base_currency="USD")

    assert Settings(base_currency="eur").base_currency == "EUR"


def test_flow_timing_must_be_a_known_convention() -> None:
    with pytest.raises(ValueError):
        Settings(analytics_flow_timing="flow_whenever")

    assert Settings(analytics_flow_timing="intraday_split").analytics_flow_timing == (
        "intraday_split"
    )


def test_stale_day_cutoffs_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        Settings(analytics_max_price_stale_days=0)
    with pytest.raises(ValueError, match="positive"):
        Settings(analytics_max_fx_stale_days=-1)


def test_data_provider_settings_are_enumerated() -> None:
    with pytest.raises(ValueError, match="market_data_provider must be one of"):
        Settings(market_data_provider="yfinance")
    with pytest.raises(ValueError, match="factor_data_provider must be one of"):
        Settings(factor_data_provider="kenneth-french")


def test_passive_benchmark_key_must_be_a_known_proxy() -> None:
    with pytest.raises(ValueError, match="analytics_passive_benchmark_key must be one of"):
        Settings(analytics_passive_benchmark_key="sp500")

    assert Settings(analytics_passive_benchmark_key="CSPX").analytics_passive_benchmark_key == (
        "cspx"
    )


def test_blank_benchmark_currency_is_unset(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("HELIOS_BENCHMARK_VWRP_CURRENCY", "  ")

    assert Settings().benchmark_vwrp_currency is None


def test_settings_has_no_ambient_clock() -> None:
    # The replay/report pipeline must take its clock by injection, never from date.today().
    assert not hasattr(Settings(), "today")
