from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal

import keyring
from keyring.errors import KeyringError
from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SUPPORTED_BASE_CURRENCY = "EUR"
MARKET_DATA_PROVIDERS = frozenset({"disabled", "alphavantage"})
FACTOR_DATA_PROVIDERS = frozenset({"disabled"})
FlowTiming = Literal["flow_at_open", "flow_at_close", "intraday_split"]
AnthropicEffort = Literal["low", "medium", "high", "xhigh", "max"]
BENCHMARK_KEYS = frozenset({"cspx", "swda", "vwrp"})


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
    sync_lease_minutes: int = 15
    ecb_base_url: str = "https://data-api.ecb.europa.eu/service/data"
    market_data_provider: str = "disabled"
    market_data_base_url: str = "https://www.alphavantage.co/query"
    market_data_api_key: SecretStr | None = None
    market_data_timeout_seconds: float = 15.0
    factor_data_provider: str = "disabled"
    benchmark_cspx_symbol: str = "CSPX.LON"
    benchmark_swda_symbol: str = "SWDA.LON"
    benchmark_vwrp_symbol: str = "VWRP.LON"
    # Quotation currency of each benchmark proxy. Left unset on purpose: Helios never guesses a
    # listing currency, and a provider request without a trusted currency is skipped instead.
    benchmark_cspx_currency: str | None = None
    benchmark_swda_currency: str | None = None
    benchmark_vwrp_currency: str | None = None
    analytics_flow_timing: FlowTiming = "flow_at_close"
    analytics_max_price_stale_days: int = 10
    analytics_max_fx_stale_days: int = 10
    analytics_passive_benchmark_key: str = "vwrp"
    analytics_cluster_distance_threshold: float = 1.0
    analytics_min_cluster_observations: int = 20
    analytics_risk_free_rate: Decimal = Decimal("0")
    news_feeds_path: Path = Path("config/news_feeds.yaml")
    news_timeout_seconds: float = 15.0
    news_max_items_per_feed: int = 100
    news_max_body_bytes: int = 5_000_000
    news_sync_cadence_minutes: int = 180
    news_user_agent: str = "helios-local/0.1 (personal read-only portfolio tracker)"
    # SEC requires a User-Agent carrying a real contact address. Without one, the SEC feeds
    # are skipped rather than sent with a fake identity.
    news_sec_user_agent: str | None = None
    news_marketaux_base_url: str = "https://api.marketaux.com/v1"
    news_marketaux_api_key: SecretStr | None = None
    news_dedupe_window_hours: int = 48
    # --- Milestone 6: Claude analysis -------------------------------------------------
    # Disabled until a key is present, so the app runs end-to-end with no AI account.
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-opus-5"
    anthropic_max_tokens: int = 8000
    anthropic_effort: AnthropicEffort = "high"
    anthropic_timeout_seconds: float = 120.0
    #: Claude Opus 5's safety classifiers can decline a request. When they do, Anthropic re-runs
    #: it on this model server-side rather than returning the refusal.
    anthropic_fallback_model: str = "claude-opus-4-8"
    ai_max_news_items: int = 40

    @field_validator(
        "t212_api_key",
        "t212_api_secret",
        "t212_keyring_username",
        "openfigi_api_key",
        "market_data_api_key",
        "news_marketaux_api_key",
        "anthropic_api_key",
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
        "market_data_timeout_seconds",
        "sync_cadence_minutes",
        "sync_lease_minutes",
        "analytics_max_price_stale_days",
        "analytics_max_fx_stale_days",
        "analytics_cluster_distance_threshold",
        "analytics_min_cluster_observations",
        "news_timeout_seconds",
        "news_max_items_per_feed",
        "news_max_body_bytes",
        "news_sync_cadence_minutes",
        "anthropic_max_tokens",
        "anthropic_timeout_seconds",
        "ai_max_news_items",
    )
    @classmethod
    def positive_numeric_settings(cls, value: int | float) -> int | float:
        if value <= 0:
            raise ValueError("value must be positive")
        return value

    @field_validator("base_currency")
    @classmethod
    def base_currency_must_be_eur(cls, value: str) -> str:
        if value.upper() != SUPPORTED_BASE_CURRENCY:
            raise ValueError(
                f"base_currency must be {SUPPORTED_BASE_CURRENCY}; "
                "the analytics pipeline only reconstructs EUR-denominated NAV"
            )
        return value.upper()

    @field_validator("market_data_provider")
    @classmethod
    def known_market_data_provider(cls, value: str) -> str:
        if value not in MARKET_DATA_PROVIDERS:
            raise ValueError(f"market_data_provider must be one of {sorted(MARKET_DATA_PROVIDERS)}")
        return value

    @field_validator("factor_data_provider")
    @classmethod
    def known_factor_data_provider(cls, value: str) -> str:
        if value not in FACTOR_DATA_PROVIDERS:
            raise ValueError(f"factor_data_provider must be one of {sorted(FACTOR_DATA_PROVIDERS)}")
        return value

    @field_validator("analytics_passive_benchmark_key")
    @classmethod
    def known_passive_benchmark_key(cls, value: str) -> str:
        normalised = value.strip().lower()
        if normalised not in BENCHMARK_KEYS:
            raise ValueError(
                f"analytics_passive_benchmark_key must be one of {sorted(BENCHMARK_KEYS)}"
            )
        return normalised

    @field_validator(
        "benchmark_cspx_currency",
        "benchmark_swda_currency",
        "benchmark_vwrp_currency",
        mode="before",
    )
    @classmethod
    def normalise_benchmark_currency(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip().upper()
            return stripped or None
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
