"""The settings surface the dashboard writes through.

This module exists because credentials now arrive over HTTP, which is a materially different
threat model from an operator editing `.env` in a text editor. Three rules follow from that:

* **Nothing here ever returns a secret.** The read model reports presence and a four-character
  tail. A caller who did not already know a key learns nothing from reading this endpoint.
* **A credential is validated before it is stored.** A key that does not work is a key the
  operator will spend an afternoon debugging; better to refuse it at the door and say why.
* **The Trading 212 base URL is an allowlist, not a free-text field.** It is the one setting
  where a rewrite turns the next sync into credential exfiltration, so it may only ever name
  one of the two hosts Trading 212 actually operates.

Non-credential settings still go to `.env`, written atomically and with owner-only permissions.
Credentials go to the OS keyring. When no usable keyring exists the write is refused and the
operator is told to use `.env` themselves: silently dropping a secret into a plaintext file is
the one fallback this module will not make on anyone's behalf.

Where no write could ever take effect -- under Docker Compose, whose injected environment
outranks any file the container writes -- `Settings.settings_writable` is off and the API refuses
every change with :data:`SETTINGS_READ_ONLY_DETAIL`.
"""

from __future__ import annotations

import base64
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import httpx
from pydantic import SecretStr

from .config import BENCHMARK_KEYS, MARKET_DATA_PROVIDERS, Settings
from .logging import get_logger
from .secrets import (
    CREDENTIAL_KEYS,
    CredentialStoreError,
    credential_hint,
    delete_credential,
    get_credential,
    keyring_available,
    set_credential,
)

logger = get_logger(__name__)

#: The only hosts Trading 212 serves the API from. A base URL outside this set is rejected
#: outright: rewriting it to an attacker-controlled host would send the next sync's Basic-auth
#: header — key and secret both — somewhere else entirely. No other setting has that property,
#: which is why this one is an allowlist while the rest are free text.
ALLOWED_T212_BASE_URLS: Final[frozenset[str]] = frozenset(
    {
        "https://demo.trading212.com/api/v0",
        "https://live.trading212.com/api/v0",
    }
)

#: The two Trading 212 environments, by the name the dashboard uses for them.
T212_ENVIRONMENT_URLS: Final[dict[str, str]] = {
    "demo": "https://demo.trading212.com/api/v0",
    "live": "https://live.trading212.com/api/v0",
}

#: Settings the dashboard may write that are not credentials. Everything else in `Settings` is
#: either derived, a constant of the deployment (bind host/port), or a knob whose misuse would
#: silently corrupt analytics — those stay editable only by someone with file access.
EDITABLE_SETTINGS: Final[dict[str, str]] = {
    "t212_base_url": "HELIOS_T212_BASE_URL",
    "news_sec_user_agent": "HELIOS_NEWS_SEC_USER_AGENT",
    "market_data_provider": "HELIOS_MARKET_DATA_PROVIDER",
    "market_data_fallback_provider": "HELIOS_MARKET_DATA_FALLBACK_PROVIDER",
    "anthropic_model": "HELIOS_ANTHROPIC_MODEL",
    "analytics_passive_benchmark_key": "HELIOS_ANALYTICS_PASSIVE_BENCHMARK_KEY",
}

#: Environment variable name per credential field, for the `.env` fallback path.
CREDENTIAL_ENV_NAMES: Final[dict[str, str]] = {
    field: f"HELIOS_{field.upper()}" for field in CREDENTIAL_KEYS
}

#: What each credential unlocks, and what is lost without it. The dashboard renders this rather
#: than hardcoding it in TSX, so the backend stays the single source of truth about degradation.
CREDENTIAL_IMPACT: Final[dict[str, dict[str, str]]] = {
    "t212_api_key": {
        "label": "Trading 212 API key",
        "requirement": "required",
        "unlocks": "Positions, order history, dividends, the entire ledger",
        "without": "Nothing works — Helios has no data source without this",
        "signup": "https://www.trading212.com",
    },
    "t212_api_secret": {
        "label": "Trading 212 API secret",
        "requirement": "required",
        "unlocks": "Paired with the key to authenticate every request",
        "without": "Nothing works — the API rejects a key without its secret",
        "signup": "https://www.trading212.com",
    },
    "market_data_api_key": {
        "label": "Price data API key (main source)",
        "requirement": "recommended",
        "unlocks": (
            "Daily prices from the main source picked above (Twelve Data, EODHD or Alpha "
            "Vantage), and through them TWR, Sharpe, VaR, drawdown, beta, NAV chart"
        ),
        "without": "Every price-dependent metric reports unavailable",
        "signup": "https://twelvedata.com/pricing",
    },
    "market_data_fallback_api_key": {
        "label": "Price data API key (fallback source)",
        "requirement": "optional",
        "unlocks": (
            "A second market-data account for symbols the primary provider's plan does not "
            "cover -- e.g. London-listed holdings when the primary is Twelve Data's free tier"
        ),
        "without": (
            "Those symbols keep reporting unavailable unless the primary provider covers them"
        ),
        "signup": "https://www.alphavantage.co/support/#api-key",
    },
    "anthropic_api_key": {
        "label": "Anthropic API key",
        "requirement": "optional",
        "unlocks": "The Insights page",
        "without": "Insights reports unavailable; nothing else changes",
        "signup": "https://console.anthropic.com",
    },
    "news_marketaux_api_key": {
        "label": "Marketaux API key",
        "requirement": "optional",
        "unlocks": "Ticker-tagged international news with sentiment",
        "without": "That one feed is skipped; Yahoo, Google News and SEC still run",
        "signup": "https://www.marketaux.com",
    },
    "openfigi_api_key": {
        "label": "OpenFIGI API key",
        "requirement": "optional",
        "unlocks": "Higher instrument-mapping rate limits",
        "without": "Mapping still works, just slower",
        "signup": "https://www.openfigi.com/api",
    },
}


#: Shown whenever `settings_writable` is off. Names the file the operator actually has to edit,
#: because under Compose the `.env` the container can see is not the one that configures it.
SETTINGS_READ_ONLY_DETAIL: Final = (
    "Settings are read-only in this deployment. Helios is running with configuration injected "
    "by its supervisor (under Docker Compose, the .env file next to compose.yaml on the host), "
    "and a change saved from here would never be read. Edit that file, then recreate the "
    "containers with `docker compose up -d`."
)


class SettingsWriteError(RuntimeError):
    """A settings change was refused. The message is safe to show the operator."""


@dataclass(frozen=True)
class CredentialStatus:
    """Whether a credential exists, and just enough of it to recognise — never the value."""

    field: str
    label: str
    requirement: str
    present: bool
    hint: str | None
    source: str
    unlocks: str
    without: str
    signup: str


@dataclass(frozen=True)
class SettingsSnapshot:
    """Everything the settings page reads. Contains no secret values by construction."""

    credentials: tuple[CredentialStatus, ...]
    editable: dict[str, str]
    keyring_backend: str
    keyring_available: bool
    keyring_detail: str
    env_path: str
    allowed_t212_base_urls: tuple[str, ...]
    active_database: str
    restart_required: bool
    writable: bool
    read_only_reason: str | None
    #: The environment the running process syncs from.
    t212_environment: str
    #: The environment Helios will sync from after its next start. Differs from
    #: ``t212_environment`` exactly when a change has been saved but not yet applied.
    t212_pending_environment: str


def read_snapshot(
    settings: Settings, *, env_path: Path, restart_required: bool
) -> SettingsSnapshot:
    """Describe current configuration without disclosing a single credential value."""

    status = keyring_available()
    rows: list[CredentialStatus] = []
    for field, impact in CREDENTIAL_IMPACT.items():
        raw = getattr(settings, field, None)
        value = raw.get_secret_value() if isinstance(raw, SecretStr) else raw
        stored_in_keyring = get_credential(field) is not None
        if value:
            source = "keyring" if stored_in_keyring else "environment"
        else:
            source = "unset"
        rows.append(
            CredentialStatus(
                field=field,
                label=impact["label"],
                requirement=impact["requirement"],
                present=bool(value),
                hint=credential_hint(value if isinstance(value, str) else None),
                source=source,
                unlocks=impact["unlocks"],
                without=impact["without"],
                signup=impact["signup"],
            )
        )

    editable = {field: str(getattr(settings, field, "") or "") for field in EDITABLE_SETTINGS}
    # Show the fallback actually in use, so the dropdown never says "Disabled" while a saved
    # Alpha Vantage key is pricing London listings.
    editable["market_data_fallback_provider"] = settings.effective_market_data_fallback_provider

    return SettingsSnapshot(
        credentials=tuple(rows),
        editable=editable,
        keyring_backend=status.backend,
        keyring_available=status.available,
        keyring_detail=status.detail,
        env_path=str(env_path),
        allowed_t212_base_urls=tuple(sorted(ALLOWED_T212_BASE_URLS)),
        active_database=str(settings.sqlite_path),
        restart_required=restart_required,
        writable=settings.settings_writable,
        read_only_reason=None if settings.settings_writable else SETTINGS_READ_ONLY_DETAIL,
        t212_environment=environment_name(settings.t212_base_url),
        t212_pending_environment=environment_name(
            effective_t212_base_url(settings, env_path=env_path)
        ),
    )


def effective_t212_base_url(settings: Settings, *, env_path: Path) -> str:
    """The Trading 212 base URL Helios will use after its next start.

    Settings are frozen at startup, so after the operator picks a different environment the
    running process still holds the old URL. Anything that must act on the operator's *current*
    choice -- verifying a key, labelling what is saved -- has to ask this instead, or a live key
    gets checked against demo and rejected. Precedence mirrors startup: an exported environment
    variable, then `.env`, then the running value.
    """

    for candidate in (
        os.environ.get("HELIOS_T212_BASE_URL"),
        read_env_value(env_path, "HELIOS_T212_BASE_URL"),
    ):
        if candidate and candidate.strip().rstrip("/") in ALLOWED_T212_BASE_URLS:
            return candidate.strip().rstrip("/")
    return settings.t212_base_url


def read_env_value(env_path: Path, name: str) -> str | None:
    """One assignment from a dotenv file, unquoted; None when absent."""

    if not env_path.is_file():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() == name:
            return value.strip().strip('"').strip("'") or None
    return None


def environment_name(base_url: str) -> str:
    """`demo` or `live`, for the dashboard. Unknown hosts read as live: never claim safety."""

    return "demo" if "demo." in base_url else "live"


def validate_t212_base_url(value: str) -> str:
    """Accept only a host Trading 212 actually operates."""

    normalised = value.strip().rstrip("/")
    if normalised not in ALLOWED_T212_BASE_URLS:
        allowed = ", ".join(sorted(ALLOWED_T212_BASE_URLS))
        raise SettingsWriteError(
            f"Trading 212 base URL must be one of: {allowed}. "
            "This is an allowlist because a rewritten base URL would send your credentials "
            "to whatever host it names."
        )
    return normalised


async def verify_t212_credentials(
    *, api_key: str, api_secret: str, base_url: str, request_timeout_seconds: float = 15.0
) -> str:
    """Confirm a Trading 212 key pair works against the chosen environment before storing it.

    Trading 212 documents Basic auth with the key as username and the secret as password. A demo
    key returns 401 against the live host and vice versa, so this also catches the most common
    setup mistake — pasting a Practice-account key while pointing at live — and says which
    environment the credential actually belongs to.
    """

    base = validate_t212_base_url(base_url)
    combined = base64.b64encode(f"{api_key}:{api_secret}".encode()).decode("ascii")
    url = f"{base}/equity/account/info"
    try:
        async with httpx.AsyncClient(timeout=request_timeout_seconds) as client:
            response = await client.get(
                url, headers={"Authorization": f"Basic {combined}", "Accept": "application/json"}
            )
    except httpx.HTTPError as exc:
        raise SettingsWriteError(
            f"Could not reach Trading 212 to verify the credential: {exc}"
        ) from exc

    if response.status_code == 401:
        other = "live" if _environment_name(base) == "demo" else "demo"
        raise SettingsWriteError(
            f"Trading 212 rejected this key pair on the {_environment_name(base)} environment. "
            f"A key issued for a {other} account will not work here — generate the key from the "
            f"account you are pointing at."
        )
    if response.status_code == 403:
        raise SettingsWriteError(
            "Trading 212 accepted the credential but refused the account-info scope. "
            "Re-issue the key with read permissions."
        )
    if response.status_code >= 400:
        raise SettingsWriteError(
            f"Trading 212 returned HTTP {response.status_code} while verifying the credential."
        )

    try:
        payload = response.json()
    except ValueError:
        return _environment_name(base)
    account_id = payload.get("id") if isinstance(payload, dict) else None
    currency = payload.get("currencyCode") if isinstance(payload, dict) else None
    detail = _environment_name(base)
    if account_id is not None:
        detail = f"{detail} account {account_id}"
    if currency:
        detail = f"{detail} ({currency})"
    return detail


def _environment_name(base_url: str) -> str:
    return "demo" if "demo." in base_url else "live"


def store_credential(field: str, value: str) -> str:
    """Persist one credential, preferring the keyring. Returns where it landed.

    A blank value clears the credential rather than storing an empty string, which matches how
    `Settings` already treats blank environment variables.
    """

    if field not in CREDENTIAL_KEYS:
        raise SettingsWriteError(f"{field} is not a credential the dashboard can set")

    if not value.strip():
        delete_credential(field)
        audit_settings_change(field=field, action="cleared", destination="keyring")
        return "cleared"

    status = keyring_available()
    if not status.available:
        raise SettingsWriteError(
            f"No usable OS keyring is available ({status.backend}: {status.detail}). "
            "Set this credential in .env instead, or install a keyring backend."
        )
    try:
        set_credential(field, value.strip())
    except CredentialStoreError as exc:
        raise SettingsWriteError(str(exc)) from exc
    audit_settings_change(field=field, action="stored", destination="keyring")
    return "keyring"


def audit_settings_change(*, field: str, action: str, destination: str) -> None:
    """Record that a setting changed, naming the field and never the value.

    The value is deliberately absent. An audit trail that logs the secret it is auditing turns
    the log file into a second copy of the credential store, which is the opposite of the point.
    Field name, action and destination are enough to answer "when did my Anthropic key change?"
    without answering "what is it?".
    """

    logger.info(
        "helios.settings_changed",
        field=field,
        action=action,
        destination=destination,
    )


def write_env_settings(env_path: Path, updates: dict[str, str]) -> None:
    """Rewrite `.env` with the given HELIOS_* assignments, atomically and owner-only.

    Atomic because a crash midway through a naive rewrite leaves a truncated `.env`, which on the
    next start means Helios silently comes up with half its configuration missing. Owner-only
    because the fallback path may hold a credential and a world-readable file defeats the point.

    Comments and unmanaged lines are preserved: an operator's notes in this file are theirs.
    """

    existing_lines: list[str] = []
    if env_path.exists():
        existing_lines = env_path.read_text(encoding="utf-8").splitlines()

    remaining = dict(updates)
    output: list[str] = []
    for line in existing_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            output.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in remaining:
            output.append(f"{key}={remaining.pop(key)}")
        else:
            output.append(line)

    for key, value in remaining.items():
        output.append(f"{key}={value}")

    body = "\n".join(output) + "\n"

    env_path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=str(env_path.parent), prefix=".env.", suffix=".tmp")
    temp_path = Path(temp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        # Owner read/write only. On Windows this is advisory rather than enforced, so the
        # keyring — not this file — is what actually protects a credential there.
        os.chmod(temp_path, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(temp_path, env_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def apply_editable_setting(field: str, value: str) -> tuple[str, str]:
    """Validate one non-credential setting and return its (env name, normalised value)."""

    env_name = EDITABLE_SETTINGS.get(field)
    if env_name is None:
        raise SettingsWriteError(f"{field} is not a setting the dashboard can change")

    if field == "t212_base_url":
        return env_name, validate_t212_base_url(value)

    normalised = value.strip()
    if field == "market_data_provider":
        if normalised not in MARKET_DATA_PROVIDERS:
            raise SettingsWriteError(
                f"market_data_provider must be one of {sorted(MARKET_DATA_PROVIDERS)}"
            )
    if field == "market_data_fallback_provider":
        if normalised not in MARKET_DATA_PROVIDERS:
            raise SettingsWriteError(
                f"market_data_fallback_provider must be one of {sorted(MARKET_DATA_PROVIDERS)}"
            )
    if field == "analytics_passive_benchmark_key":
        if normalised.lower() not in BENCHMARK_KEYS:
            raise SettingsWriteError(
                f"analytics_passive_benchmark_key must be one of {sorted(BENCHMARK_KEYS)}"
            )
        normalised = normalised.lower()

    return env_name, normalised
