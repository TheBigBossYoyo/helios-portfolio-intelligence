from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import time as dt_time
from decimal import Decimal
from pathlib import Path
from typing import Final, Literal

import keyring
from keyring.errors import KeyringError
from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from .secrets import CREDENTIAL_KEYS, get_credential

#: Environment values that switch the OS keyring off entirely.
#:
#: A test run must never inherit the developer's real brokerage credentials: a suite that passes
#: because a key happened to be in Credential Manager is exactly the "works on my machine"
#: failure this whole surface exists to prevent. `tests/conftest.py` sets this, and CI inherits
#: it from there rather than relying on the runner having no keyring.
KEYRING_DISABLED_VALUES = frozenset({"1", "true", "yes", "on"})


def keyring_disabled() -> bool:
    """Whether ``HELIOS_DISABLE_KEYRING`` asks Helios to ignore the OS credential store."""

    return os.environ.get("HELIOS_DISABLE_KEYRING", "").strip().lower() in KEYRING_DISABLED_VALUES


#: The dotenv file Helios reads, relative to the directory the process starts in, unless
#: ``HELIOS_ENV_FILE`` names one explicitly. The settings page writes to this same path, which is
#: the whole point: a value saved there is a value read back on the next start.
ENV_FILE: Final = ".env"


def env_file_path() -> Path:
    """The one `.env` both `Settings` reads and the settings page writes.

    ``HELIOS_ENV_FILE`` pins it regardless of working directory -- the desktop launcher sets it,
    because a shortcut does not guarantee where a process starts. Resolved per call so a test
    can point it at a temporary file.
    """

    override = os.environ.get("HELIOS_ENV_FILE", "").strip()
    return Path(override) if override else Path.cwd() / ENV_FILE


def dotenv_disabled() -> bool:
    """Whether ``HELIOS_DISABLE_DOTENV`` asks Helios to ignore `.env`.

    Same reasoning as the keyring switch: the test suite sets it so a developer's own `.env`
    cannot change what a test sees.
    """

    return os.environ.get("HELIOS_DISABLE_DOTENV", "").strip().lower() in KEYRING_DISABLED_VALUES


SUPPORTED_BASE_CURRENCY = "EUR"
MARKET_DATA_PROVIDERS = frozenset({"disabled", "alphavantage", "twelvedata", "eodhd"})
FACTOR_DATA_PROVIDERS = frozenset({"disabled", "kenfrench"})
FlowTiming = Literal["flow_at_open", "flow_at_close", "intraday_split"]
AnthropicEffort = Literal["low", "medium", "high", "xhigh", "max"]
BENCHMARK_KEYS = frozenset({"cspx", "swda", "vwrp"})


class Settings(BaseSettings):
    # `.env` is read so a bare install picks up what the settings page saved. Real environment
    # variables still outrank it, so an exported override behaves as expected, and under Compose
    # (which injects the host `.env` as environment) the container has no `.env` of its own.
    model_config = SettingsConfigDict(env_prefix="HELIOS_", extra="ignore")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Decided per instantiation rather than at import, so the test suite's session-wide
        # switch applies even though test modules import this one during collection.
        if dotenv_disabled():
            return (init_settings, env_settings, file_secret_settings)
        dotenv = DotEnvSettingsSource(
            settings_cls, env_file=env_file_path(), env_file_encoding="utf-8"
        )
        return (init_settings, env_settings, dotenv, file_secret_settings)

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
    # After any successful portfolio sync (scheduled or from the dashboard), recompute the
    # replayed history and refresh news straight away, so no page is left half-updated.
    refresh_after_sync: bool = True
    # Apply dashboard settings changes by restarting automatically, a few seconds after
    # the last save (so several saves in a row cost one restart). The restart runs the
    # worker's startup sync, replay and news refresh, so a new key takes effect at once.
    auto_apply_settings: bool = True
    # Card history: ask Trading 212 for a CSV export (the only source that labels card payments
    # and cashback) at most this often. Each export sends a notification to the Trading 212
    # app, so the cadence is deliberately slow; the worker checks for a finished report on
    # `card_history_poll_minutes`.
    card_history_enabled: bool = True
    card_export_cadence_hours: int = 24
    card_history_poll_minutes: int = 15
    # Price alerts are checked this often against Trading 212's live prices.
    alerts_poll_minutes: int = 5
    # One notification a day, after this local time: the day's result, movers, card spending
    # and news about your holdings. Shown by the desktop tray.
    daily_summary_enabled: bool = True
    daily_summary_time: str = "21:00"
    # The weekly AI review writes itself on this weekday (0 = Monday ... 6 = Sunday) after this
    # local time -- only when switched on, because every run bills the Anthropic account.
    weekly_review_enabled: bool = False
    weekly_review_weekday: int = 6
    weekly_review_time: str = "19:00"
    # Daily verified database backups. The folder can be a synced one (OneDrive, Dropbox) to
    # keep a copy off this machine; unset means <data_dir>/backups.
    backup_enabled: bool = True
    backup_dir: Path | None = None
    backup_keep: int = 14
    backup_interval_hours: int = 24
    # Raw feed bodies are only needed to re-parse recent fetches; the articles themselves are
    # kept. Older bodies are dropped by the daily storage job (see helios.storage).
    raw_news_retention_days: int = 7
    storage_compact_enabled: bool = True
    sync_lease_minutes: int = 15
    ecb_base_url: str = "https://data-api.ecb.europa.eu/service/data"
    market_data_provider: str = "disabled"
    market_data_base_url: str = "https://www.alphavantage.co/query"
    market_data_api_key: SecretStr | None = None
    market_data_timeout_seconds: float = 15.0
    twelvedata_base_url: str = "https://api.twelvedata.com/time_series"
    # EODHD end-of-day API: full daily history for US and international listings (LSE, XETRA,
    # Euronext...) on its paid plans; the free plan gives 20 calls a day and one year of history.
    eodhd_base_url: str = "https://eodhd.com/api"
    eodhd_min_interval_seconds: float = 0.2
    # Exchanges the configured Twelve Data plan can serve, comma-separated, as the exchange keys
    # in performance.YAHOO_EXCHANGE_SUFFIXES ("US" for listings without a suffix). The free Basic
    # plan is US-only, so a London symbol is not even requested there -- it goes straight to the
    # fallback provider instead of spending one of the free plan's 8 requests a minute on a
    # guaranteed refusal. A paid plan that reaches London: "US,LSE".
    twelvedata_exchanges: str = "US"
    # Minimum seconds between one provider's requests. Twelve Data's free plan allows 8 per
    # minute (60 / 8 = 7.5, plus margin); Alpha Vantage's free key allows 25 a day and throttles
    # bursts. Requests sent faster are answered with HTTP 429 and the replay fails.
    twelvedata_min_interval_seconds: float = 7.6
    alphavantage_min_interval_seconds: float = 12.5
    # Optional second market-data account, asked only for symbols the primary could not price
    # (see CompositeMarketDataProvider in performance.py). The intended pairing is Twelve Data as
    # the primary (US exchanges, free) with Alpha Vantage as the fallback (non-US listings such
    # as London, free but a much smaller 25-requests/day quota) -- but any provider may fill
    # either role. 'disabled' by default: two providers only make sense once an operator actually
    # holds a second account.
    market_data_fallback_provider: str = "disabled"
    market_data_fallback_api_key: SecretStr | None = None
    # On by default: the Kenneth French library is the official source, free, and needs no
    # account, so there is nothing for an operator to opt into.
    factor_data_provider: str = "kenfrench"
    ken_french_five_factor_url: str = (
        "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
        "F-F_Research_Data_5_Factors_2x3_daily_CSV.zip"
    )
    ken_french_momentum_url: str = (
        "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
        "F-F_Momentum_Factor_daily_CSV.zip"
    )
    # US-listed benchmark proxies, chosen so the free market-data tier covers them: the free plan
    # serves US exchanges only, and an LSE symbol would return nothing.
    #
    # These are proxies for the index, not the UCITS ETF an EU investor would actually buy. IVV
    # (US-domiciled) and CSPX (Irish-domiciled) track the same index but face different dividend
    # withholding, so the "you, but passive" counterfactual is an approximation of what you could
    # have held rather than a quote for it. With a provider that covers the LSE, set these back to
    # CSPX.LON / SWDA.LON / VWRP.LON and give each its listing currency.
    benchmark_cspx_symbol: str = "IVV"
    benchmark_swda_symbol: str = "URTH"
    benchmark_vwrp_symbol: str = "VT"
    # Quotation currency of each benchmark proxy. Helios never *infers* a listing currency, but a
    # provider that reports one (Twelve Data does) is trusted over silence -- see
    # TwelveDataMarketDataProvider._resolve_currency. Setting these makes the value authoritative
    # and turns a provider disagreement into a skip rather than a silent mis-valuation.
    benchmark_cspx_currency: str | None = "USD"
    benchmark_swda_currency: str | None = "USD"
    benchmark_vwrp_currency: str | None = "USD"
    # Brinson-Fachler sector attribution: benchmark sector weights/return proxies, declared by the
    # operator (see the file's own header). Empty by default, so attribution reports "unavailable"
    # rather than a number nobody supplied.
    benchmark_sectors_path: Path = Path("config/benchmark_sectors.yaml")
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
    # --- Dashboard settings surface ------------------------------------------------------
    #: Whether the dashboard may change configuration at all. Off under Docker Compose, where a
    #: write could never take effect: compose injects the host `.env` as environment variables,
    #: which outrank any file the container writes, and the slim image has no OS keyring. The
    #: page then shows configuration read-only and says where to change it instead.
    settings_writable: bool = True
    #: Hostnames, comma-separated, allowed to reach the settings routes besides loopback.
    #: Compose names its own `web` service here, because the dashboard's server-side fetches
    #: arrive from that container's bridge address rather than from 127.0.0.1. Resolved per
    #: request, so a restarted container with a new address is still recognised.
    settings_trusted_peers: str = ""

    @property
    def effective_market_data_fallback_provider(self) -> str:
        """The fallback provider actually used.

        A saved fallback key with no provider chosen means Alpha Vantage: that key is labelled
        "Alpha Vantage API key (London listings)" on the settings page, so saving one is the
        intent, and leaving it inert because a second dropdown was not also changed left a real
        portfolio's London holdings unpriced. An explicit choice always wins.
        """

        if self.market_data_fallback_provider != "disabled":
            return self.market_data_fallback_provider
        return "alphavantage" if self.market_data_fallback_api_key is not None else "disabled"

    @property
    def twelvedata_exchange_keys(self) -> frozenset[str]:
        return frozenset(
            item.strip().upper() for item in self.twelvedata_exchanges.split(",") if item.strip()
        )

    @property
    def settings_trusted_peer_hosts(self) -> tuple[str, ...]:
        return tuple(
            host.strip() for host in self.settings_trusted_peers.split(",") if host.strip()
        )

    @field_validator(
        "t212_api_key",
        "t212_api_secret",
        "t212_keyring_username",
        "openfigi_api_key",
        "market_data_api_key",
        "market_data_fallback_api_key",
        "news_marketaux_api_key",
        "anthropic_api_key",
        # An empty backup folder means "the default", not the current directory.
        "backup_dir",
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
        "card_export_cadence_hours",
        "card_history_poll_minutes",
        "alerts_poll_minutes",
        "backup_keep",
        "backup_interval_hours",
        "raw_news_retention_days",
        "anthropic_max_tokens",
        "anthropic_timeout_seconds",
        "ai_max_news_items",
    )
    @classmethod
    def positive_numeric_settings(cls, value: int | float) -> int | float:
        if value <= 0:
            raise ValueError("value must be positive")
        return value

    @field_validator("weekly_review_weekday")
    @classmethod
    def valid_weekday(cls, value: int) -> int:
        if not 0 <= value <= 6:
            raise ValueError("weekly_review_weekday must be 0 (Monday) to 6 (Sunday)")
        return value

    @property
    def weekly_review_at(self) -> dt_time:
        hours, minutes = (int(part) for part in self.weekly_review_time.split(":"))
        return dt_time(hours, minutes)

    @field_validator("daily_summary_time", "weekly_review_time")
    @classmethod
    def valid_summary_time(cls, value: str) -> str:
        try:
            hours, minutes = (int(part) for part in value.strip().split(":"))
            dt_time(hours, minutes)
        except ValueError as exc:
            raise ValueError("times must be HH:MM, e.g. 21:00") from exc
        return f"{hours:02d}:{minutes:02d}"

    @property
    def daily_summary_at(self) -> dt_time:
        hours, minutes = (int(part) for part in self.daily_summary_time.split(":"))
        return dt_time(hours, minutes)

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

    @field_validator("market_data_fallback_provider")
    @classmethod
    def known_market_data_fallback_provider(cls, value: str) -> str:
        if value not in MARKET_DATA_PROVIDERS:
            raise ValueError(
                f"market_data_fallback_provider must be one of {sorted(MARKET_DATA_PROVIDERS)}"
            )
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
        """Fill the T212 secret from the legacy per-account keyring entry.

        This predates the dashboard's credential store and is kept because existing installs
        have secrets filed under ``helios.t212`` keyed by API key. It runs before
        :meth:`resolve_stored_credentials` so the newer store wins only where this leaves a gap.
        """

        if self.t212_api_secret is not None:
            return self
        if keyring_disabled():
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

    @model_validator(mode="after")
    def resolve_stored_credentials(self) -> Settings:
        """Fill any still-absent credential from the dashboard's keyring store.

        Environment wins over the keyring, deliberately: an operator who exports a variable to
        override a stored key expects that to take effect. A field is only consulted when it is
        ``None``, so this never overwrites a real value.

        Setting ``HELIOS_DISABLE_KEYRING=1`` skips the store entirely. The test suite sets this,
        because otherwise constructing ``Settings()`` in a test would silently adopt whatever
        real credentials the developer has in their OS keyring -- so a test could pass locally
        for a reason that does not exist in CI. That is the exact class of environment drift
        this project just spent a milestone eliminating.
        """

        if keyring_disabled():
            return self

        for field in CREDENTIAL_KEYS:
            if getattr(self, field) is not None:
                continue
            stored = get_credential(field)
            if not stored:
                continue
            # `t212_api_key` is a plain string; every other credential is a SecretStr.
            current_is_secret = field != "t212_api_key"
            object.__setattr__(self, field, SecretStr(stored) if current_is_secret else stored)
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
