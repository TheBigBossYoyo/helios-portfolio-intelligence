from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import SecretStr
from pytest import MonkeyPatch

from helios.config import Settings


def test_settings_use_keyring_secret_fallback(monkeypatch: MonkeyPatch) -> None:
    # The suite disables keyring reads session-wide so no test can adopt the developer's real
    # credentials. This test is about the fallback itself, so it opts back in -- against a fake
    # backend, which is what makes re-enabling safe here and nowhere else.
    monkeypatch.delenv("HELIOS_DISABLE_KEYRING", raising=False)
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
    # Free, official, no account -- so it is on by default. Prices still need a key.
    assert settings.factor_data_provider == "kenfrench"
    # Benchmark proxies default to US listings, which the free price tier covers. Their currency
    # is declared rather than left blank so a provider that disagrees is caught as a mismatch
    # instead of being taken at its word.
    assert settings.benchmark_cspx_symbol == "IVV"
    assert settings.benchmark_swda_symbol == "URTH"
    assert settings.benchmark_vwrp_symbol == "VT"
    assert settings.benchmark_cspx_currency == "USD"
    assert settings.benchmark_swda_currency == "USD"
    assert settings.benchmark_vwrp_currency == "USD"


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


def test_market_data_fallback_provider_defaults_disabled_and_is_enumerated() -> None:
    settings = Settings()

    assert settings.market_data_fallback_provider == "disabled"
    assert settings.market_data_fallback_api_key is None

    with pytest.raises(ValueError, match="market_data_fallback_provider must be one of"):
        Settings(market_data_fallback_provider="yfinance")

    assert (
        Settings(market_data_fallback_provider="alphavantage").market_data_fallback_provider
        == "alphavantage"
    )


def test_blank_market_data_fallback_api_key_is_unset(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("HELIOS_MARKET_DATA_FALLBACK_API_KEY", "   ")

    assert Settings().market_data_fallback_api_key is None


def test_market_data_fallback_api_key_is_secret_and_masked(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("HELIOS_MARKET_DATA_FALLBACK_API_KEY", "av-secret")

    settings = Settings()

    assert settings.market_data_fallback_api_key == SecretStr("av-secret")
    assert "av-secret" not in repr(settings)


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


def test_env_example_lists_every_setting() -> None:
    """`.env.example` promises that nothing is configurable-but-undiscoverable. Hold it to that.

    It drifted once already -- 25 real settings were missing from it -- and nothing noticed,
    because nothing checked. A commented-out line counts: some settings are only worth touching
    outside Docker and are shown commented for that reason.
    """
    example = Path(__file__).resolve().parents[1] / ".env.example"
    listed = set(
        re.findall(r"^#?\s*(HELIOS_[A-Z0-9_]+)=", example.read_text(encoding="utf-8"), re.M)
    )
    declared = {f"HELIOS_{name.upper()}" for name in Settings.model_fields}

    assert declared - listed == set(), "settings missing from .env.example"


def test_dotenv_is_read_and_environment_outranks_it(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """A bare install must see what the settings page saved to `.env`.

    Before this, nothing loaded `.env` at all: a provider change saved from the dashboard was
    written, the restart went through, and the old value came back. Environment variables still
    win, so exporting one to override the file keeps working.
    """
    monkeypatch.delenv("HELIOS_DISABLE_DOTENV", raising=False)
    monkeypatch.delenv("HELIOS_MARKET_DATA_PROVIDER", raising=False)
    monkeypatch.delenv("HELIOS_ANTHROPIC_MODEL", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "HELIOS_MARKET_DATA_PROVIDER=twelvedata\nHELIOS_ANTHROPIC_MODEL=from-file\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HELIOS_ANTHROPIC_MODEL", "from-environment")

    settings = Settings()

    assert settings.market_data_provider == "twelvedata"
    assert settings.anthropic_model == "from-environment"


def test_dotenv_switch_keeps_a_developer_env_out_of_tests(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """The suite runs from the repository root, where the developer's real `.env` lives."""
    monkeypatch.setenv("HELIOS_DISABLE_DOTENV", "1")
    monkeypatch.delenv("HELIOS_MARKET_DATA_PROVIDER", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("HELIOS_MARKET_DATA_PROVIDER=twelvedata\n", encoding="utf-8")

    assert Settings().market_data_provider == "disabled"


def test_env_file_override_is_independent_of_the_working_directory(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """A shortcut does not choose where a process starts; `HELIOS_ENV_FILE` pins the file."""
    elsewhere = tmp_path / "config-home"
    elsewhere.mkdir()
    (elsewhere / "helios.env").write_text("HELIOS_ANTHROPIC_MODEL=pinned\n", encoding="utf-8")
    monkeypatch.delenv("HELIOS_DISABLE_DOTENV", raising=False)
    monkeypatch.delenv("HELIOS_ANTHROPIC_MODEL", raising=False)
    monkeypatch.setenv("HELIOS_ENV_FILE", str(elsewhere / "helios.env"))
    monkeypatch.chdir(tmp_path)

    assert Settings().anthropic_model == "pinned"


def test_a_saved_fallback_key_alone_enables_alpha_vantage() -> None:
    """Found live: the key was saved, the second dropdown was not, and London stayed unpriced."""
    assert Settings().effective_market_data_fallback_provider == "disabled"
    assert (
        Settings(
            market_data_fallback_api_key=SecretStr("av-key")
        ).effective_market_data_fallback_provider
        == "alphavantage"
    )
    explicit = Settings(
        market_data_fallback_api_key=SecretStr("td-key"), market_data_fallback_provider="twelvedata"
    )
    assert explicit.effective_market_data_fallback_provider == "twelvedata"
