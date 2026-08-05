from __future__ import annotations

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
