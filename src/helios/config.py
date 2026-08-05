from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import keyring
from keyring.errors import KeyringError
from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HELIOS_", extra="ignore")

    app_name: str = "helios"
    bind_host: str = "127.0.0.1"
    bind_port: int = 8000
    log_level: str = "INFO"
    base_currency: str = "EUR"
    data_dir: Path = Path("data")
    sqlite_filename: str = "helios.sqlite3"
    t212_base_url: str = "https://demo.trading212.com/api/v0"
    t212_api_key: str | None = None
    t212_api_secret: SecretStr | None = None
    t212_keyring_service: str = "helios.t212"
    t212_keyring_username: str | None = None
    t212_timeout_seconds: float = 10.0
    t212_max_retries: int = 3
    instrument_metadata_ttl_hours: int = 24
    reconciliation_tolerance: Decimal = Decimal("0.001")
    instrument_overrides_path: Path = Path("config/instrument_overrides.yaml")
    openfigi_base_url: str = "https://api.openfigi.com/v3"
    openfigi_api_key: SecretStr | None = None
    resolver_timeout_seconds: float = 10.0
    sync_cadence_minutes: int = 60

    @field_validator(
        "t212_api_key",
        "t212_api_secret",
        "t212_keyring_username",
        "openfigi_api_key",
        mode="before",
    )
    @classmethod
    def blank_credentials_are_unset(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator(
        "instrument_metadata_ttl_hours",
        "resolver_timeout_seconds",
        "sync_cadence_minutes",
    )
    @classmethod
    def positive_numeric_settings(cls, value: int | float) -> int | float:
        if value <= 0:
            raise ValueError("value must be positive")
        return value

    @field_validator("reconciliation_tolerance")
    @classmethod
    def nonnegative_reconciliation_tolerance(cls, value: Decimal) -> Decimal:
        if value < Decimal("0"):
            raise ValueError("reconciliation tolerance must be nonnegative")
        return value

    @model_validator(mode="after")
    def resolve_keyring_secret(self) -> Settings:
        if self.t212_api_secret is not None:
            return self
        username = self.t212_keyring_username or self.t212_api_key
        if username is None:
            return self
        try:
            secret = keyring.get_password(self.t212_keyring_service, username)
        except KeyringError:
            return self
        if secret:
            self.t212_api_secret = SecretStr(secret)
        return self

    @property
    def sqlite_path(self) -> Path:
        return self.data_dir / self.sqlite_filename

    @property
    def sqlite_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.sqlite_path.as_posix()}"

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def t212_credentials(self) -> T212Credentials | None:
        if self.t212_api_key is None or self.t212_api_secret is None:
            return None
        return T212Credentials(
            api_key=self.t212_api_key,
            api_secret=self.t212_api_secret.get_secret_value(),
        )


@dataclass(frozen=True)
class T212Credentials:
    api_key: str
    api_secret: str


def load_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings
