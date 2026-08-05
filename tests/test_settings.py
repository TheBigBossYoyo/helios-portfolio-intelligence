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
