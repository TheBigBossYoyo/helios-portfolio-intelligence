"""Tests for the surface that accepts credentials over HTTP.

The properties asserted here are the reason the settings page is safe to expose at all:

* a snapshot never contains a secret, only presence and a four-character tail;
* the Trading 212 base URL is an allowlist, so a rewrite cannot redirect the next sync's
  Basic-auth header to a host of someone else's choosing;
* an `.env` rewrite is atomic and preserves the operator's own lines;
* a credential is verified against the real API before it is stored, and a demo key aimed at
  live is rejected with a message that says which environment it belongs to.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from pytest import MonkeyPatch

from helios.config import Settings
from helios.database_admin import (
    DatabaseAdminError,
    create_database,
    list_databases,
    resolve_switch_target,
    validate_filename,
)
from helios.secrets import credential_hint
from helios.settings_service import (
    ALLOWED_T212_BASE_URLS,
    CREDENTIAL_IMPACT,
    SettingsWriteError,
    apply_editable_setting,
    read_snapshot,
    store_credential,
    validate_t212_base_url,
    verify_t212_credentials,
    write_env_settings,
)

# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


def test_snapshot_never_carries_a_secret_value(tmp_path: Path) -> None:
    """The whole reason this endpoint can be read without the action header."""

    settings = Settings(
        data_dir=tmp_path,
        t212_api_key="t212-key-abcdefgh1234",
        t212_api_secret=SecretStr("t212-secret-zyxwvu9876"),
        anthropic_api_key=SecretStr("sk-ant-supersecret-4321"),
    )

    snapshot = read_snapshot(settings, env_path=tmp_path / ".env", restart_required=False)
    rendered = repr(snapshot)

    for secret in ("t212-key-abcdefgh1234", "t212-secret-zyxwvu9876", "sk-ant-supersecret-4321"):
        assert secret not in rendered

    by_field = {row.field: row for row in snapshot.credentials}
    assert by_field["t212_api_key"].present is True
    assert by_field["t212_api_key"].hint == "…1234"
    assert by_field["market_data_api_key"].present is False
    assert by_field["market_data_api_key"].hint is None


def test_hint_reveals_nothing_useful_about_a_short_secret() -> None:
    """Four characters of a six-character secret is most of the secret."""

    assert credential_hint("abcdefghijkl") == "…ijkl"
    assert credential_hint("short") == "…"
    assert credential_hint("") is None
    assert credential_hint(None) is None


def test_every_credential_states_what_it_costs_to_omit() -> None:
    """The dashboard renders this rather than hardcoding degradation in TSX."""

    for field, impact in CREDENTIAL_IMPACT.items():
        assert impact["requirement"] in {"required", "recommended", "optional"}
        assert impact["without"], f"{field} does not say what happens without it"
        assert impact["signup"].startswith("https://")


# ---------------------------------------------------------------------------
# Base-URL allowlist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        "https://evil.example.com/api/v0",
        "http://live.trading212.com/api/v0",
        "https://live.trading212.com.evil.example/api/v0",
        "https://live.trading212.com/api/v0/../../x",
        "",
        "   ",
    ],
)
def test_base_url_allowlist_refuses_anything_not_trading212(hostile: str) -> None:
    """A rewritten base URL sends the next sync's key and secret wherever it names.

    No other setting has that property, which is why this one is an allowlist rather than a
    validated free-text field.
    """

    with pytest.raises(SettingsWriteError, match="must be one of"):
        validate_t212_base_url(hostile)


@pytest.mark.parametrize("allowed", sorted(ALLOWED_T212_BASE_URLS))
def test_base_url_allowlist_accepts_the_two_real_hosts(allowed: str) -> None:
    assert validate_t212_base_url(allowed) == allowed
    assert validate_t212_base_url(f"{allowed}/") == allowed


def test_editable_settings_reject_an_unlisted_field() -> None:
    """Only the named settings are writable; everything else needs file access."""

    with pytest.raises(SettingsWriteError, match="not a setting"):
        apply_editable_setting("bind_port", "9999")
    with pytest.raises(SettingsWriteError, match="not a setting"):
        apply_editable_setting("data_dir", "/etc")


def test_editable_settings_validate_their_values() -> None:
    with pytest.raises(SettingsWriteError, match="market_data_provider must be one of"):
        apply_editable_setting("market_data_provider", "yfinance")
    with pytest.raises(SettingsWriteError, match="analytics_passive_benchmark_key must be one of"):
        apply_editable_setting("analytics_passive_benchmark_key", "sp500")

    assert apply_editable_setting("market_data_provider", " twelvedata ") == (
        "HELIOS_MARKET_DATA_PROVIDER",
        "twelvedata",
    )
    assert apply_editable_setting("analytics_passive_benchmark_key", "CSPX") == (
        "HELIOS_ANALYTICS_PASSIVE_BENCHMARK_KEY",
        "cspx",
    )


def test_market_data_fallback_provider_is_editable_and_validated() -> None:
    """Lets an operator set 'twelvedata (US) + alphavantage (London)' from the dashboard."""

    with pytest.raises(
        SettingsWriteError, match="market_data_fallback_provider must be one of"
    ):
        apply_editable_setting("market_data_fallback_provider", "yfinance")

    assert apply_editable_setting("market_data_fallback_provider", " alphavantage ") == (
        "HELIOS_MARKET_DATA_FALLBACK_PROVIDER",
        "alphavantage",
    )


def test_storing_an_unknown_credential_is_refused() -> None:
    with pytest.raises(SettingsWriteError, match="not a credential"):
        store_credential("aws_secret_access_key", "value")


# ---------------------------------------------------------------------------
# .env writing
# ---------------------------------------------------------------------------


def test_env_write_preserves_comments_and_replaces_in_place(tmp_path: Path) -> None:
    """An operator's notes in this file are theirs; a settings write is not a rewrite."""

    env_path = tmp_path / ".env"
    env_path.write_text(
        "# my own note\nHELIOS_LOG_LEVEL=INFO\n\nHELIOS_BASE_CURRENCY=EUR\n", encoding="utf-8"
    )

    write_env_settings(
        env_path, {"HELIOS_LOG_LEVEL": "DEBUG", "HELIOS_SQLITE_FILENAME": "x.sqlite3"}
    )

    body = env_path.read_text(encoding="utf-8")
    assert "# my own note" in body
    assert "HELIOS_LOG_LEVEL=DEBUG" in body
    assert "HELIOS_LOG_LEVEL=INFO" not in body
    assert "HELIOS_BASE_CURRENCY=EUR" in body
    # A key that was not already present is appended rather than dropped.
    assert "HELIOS_SQLITE_FILENAME=x.sqlite3" in body


def test_env_write_creates_the_file_when_absent(tmp_path: Path) -> None:
    env_path = tmp_path / "nested" / ".env"

    write_env_settings(env_path, {"HELIOS_LOG_LEVEL": "WARNING"})

    assert env_path.read_text(encoding="utf-8") == "HELIOS_LOG_LEVEL=WARNING\n"


def test_env_write_leaves_no_temp_file_behind(tmp_path: Path) -> None:
    """Atomic means rename-into-place, which must not litter the data directory."""

    env_path = tmp_path / ".env"
    for value in ("A", "B", "C"):
        write_env_settings(env_path, {"HELIOS_LOG_LEVEL": value})

    assert sorted(p.name for p in tmp_path.iterdir()) == [".env"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits are advisory on Windows")
def test_env_write_is_owner_only(tmp_path: Path) -> None:
    """The fallback path may hold a credential; a world-readable file defeats the point."""

    env_path = tmp_path / ".env"
    write_env_settings(env_path, {"HELIOS_LOG_LEVEL": "INFO"})

    mode = stat.S_IMODE(env_path.stat().st_mode)
    assert mode == stat.S_IRUSR | stat.S_IWUSR


def test_env_write_does_not_truncate_when_the_write_fails(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """A crash mid-write must not leave Helios with half its configuration."""

    env_path = tmp_path / ".env"
    original = "HELIOS_LOG_LEVEL=INFO\nHELIOS_BASE_CURRENCY=EUR\n"
    env_path.write_text(original, encoding="utf-8")

    def explode(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("helios.settings_service.os.replace", explode)

    with pytest.raises(OSError, match="disk full"):
        write_env_settings(env_path, {"HELIOS_LOG_LEVEL": "DEBUG"})

    assert env_path.read_text(encoding="utf-8") == original
    assert sorted(p.name for p in tmp_path.iterdir()) == [".env"]


# ---------------------------------------------------------------------------
# Trading 212 verification
# ---------------------------------------------------------------------------


def _stub_client(monkeypatch: MonkeyPatch, handler: object) -> None:
    """Route `verify_t212_credentials` at a mock transport instead of the network.

    The real class is captured *before* the patch lands. Patching the name and then calling
    `httpx.AsyncClient` inside the replacement would re-enter the replacement, which is both
    infinite and a confusing `multiple values for transport` error rather than a stack overflow.
    """

    real_client = httpx.AsyncClient

    def build(**kwargs: object) -> httpx.AsyncClient:
        return real_client(transport=httpx.MockTransport(handler), **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("helios.settings_service.httpx.AsyncClient", build)


@pytest.mark.asyncio
async def test_verification_names_the_environment_and_account(monkeypatch: MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        return httpx.Response(200, json={"id": 50126415, "currencyCode": "EUR"})

    _stub_client(monkeypatch, handler)

    detail = await verify_t212_credentials(
        api_key="key", api_secret="secret", base_url="https://demo.trading212.com/api/v0"
    )

    assert detail == "demo account 50126415 (EUR)"
    assert seen["url"] == "https://demo.trading212.com/api/v0/equity/account/info"
    # Basic auth with the key as username and the secret as password, per the official spec.
    assert seen["auth"].startswith("Basic ")


@pytest.mark.asyncio
async def test_a_401_says_which_account_the_key_must_come_from(monkeypatch: MonkeyPatch) -> None:
    """The most common setup mistake is a Practice key aimed at live. Say so, don't just fail."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json=[])

    _stub_client(monkeypatch, handler)

    with pytest.raises(SettingsWriteError) as excinfo:
        await verify_t212_credentials(
            api_key="demo-key", api_secret="secret", base_url="https://live.trading212.com/api/v0"
        )

    message = str(excinfo.value)
    assert "live environment" in message
    assert "demo account will not work here" in message


@pytest.mark.asyncio
async def test_verification_refuses_a_base_url_outside_the_allowlist() -> None:
    """Verification must not become a way to make Helios call an arbitrary host."""

    with pytest.raises(SettingsWriteError, match="must be one of"):
        await verify_t212_credentials(
            api_key="k", api_secret="s", base_url="https://evil.example.com/api/v0"
        )


@pytest.mark.asyncio
async def test_a_transport_failure_is_reported_not_swallowed(monkeypatch: MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    _stub_client(monkeypatch, handler)

    with pytest.raises(SettingsWriteError, match="Could not reach Trading 212"):
        await verify_t212_credentials(
            api_key="k", api_secret="s", base_url="https://demo.trading212.com/api/v0"
        )


# ---------------------------------------------------------------------------
# Database administration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        "../../etc/passwd",
        "data/other.sqlite3",
        "C:\\windows\\system32\\x.sqlite3",
        "/absolute/x.sqlite3",
        ".hidden.sqlite3",
        "helios.db",
        "helios",
        "",
    ],
)
def test_database_filename_is_contained_to_the_data_directory(hostile: str) -> None:
    """Everything downstream joins this onto data_dir and trusts it."""

    with pytest.raises(DatabaseAdminError):
        validate_filename(hostile)


def test_a_plain_sqlite_name_is_accepted() -> None:
    assert validate_filename(" helios-live.sqlite3 ") == "helios-live.sqlite3"


@pytest.mark.asyncio
async def test_creating_a_database_stamps_the_current_schema(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, sqlite_filename="helios.sqlite3")

    name = await create_database(settings, "helios-live.sqlite3")

    assert (tmp_path / name).is_file()
    _, rows = list_databases(settings)
    created = next(row for row in rows if row.filename == name)
    assert created.schema_version is not None
    assert created.active is False


@pytest.mark.asyncio
async def test_creating_never_overwrites_an_existing_database(tmp_path: Path) -> None:
    """The one operation here with real data-loss potential, so it simply refuses."""

    settings = Settings(data_dir=tmp_path, sqlite_filename="helios.sqlite3")
    await create_database(settings, "helios-live.sqlite3")

    with pytest.raises(DatabaseAdminError, match="already exists"):
        await create_database(settings, "helios-live.sqlite3")


@pytest.mark.asyncio
async def test_switching_requires_a_real_database_with_a_schema(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, sqlite_filename="helios.sqlite3")
    settings.ensure_directories()
    (tmp_path / "empty.sqlite3").write_bytes(b"")

    with pytest.raises(DatabaseAdminError, match="does not exist"):
        resolve_switch_target(settings, "absent.sqlite3")
    with pytest.raises(DatabaseAdminError, match="no Helios schema"):
        resolve_switch_target(settings, "empty.sqlite3")

    await create_database(settings, "helios-live.sqlite3")
    assert resolve_switch_target(settings, "helios-live.sqlite3") == "helios-live.sqlite3"
