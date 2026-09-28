from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from helios.app import create_app
from helios.client import Trading212TransportError
from helios.config import Settings
from helios.dependencies import (
    get_container,
    get_news_sync_service,
    get_performance_replay_service,
    get_portfolio_quality_report_service,
    get_portfolio_sync_service,
    get_t212_service,
)
from helios.performance import NoPerformanceDataError
from helios.portfolio_repository import SyncAlreadyRunningError
from helios.reporting import NoQualityReportDataError
from helios.schemas import (
    NewsSyncSummaryModel,
    PerformanceReplaySummaryModel,
    PortfolioSyncSummary,
    QualityReport,
)


def _fake_container(settings: Settings, **services: object) -> SimpleNamespace:
    """A container stand-in: settings, a no-op live-read cache, and any services a test needs."""
    return SimpleNamespace(
        settings=settings, t212_service=SimpleNamespace(invalidate=lambda: None), **services
    )


@dataclass
class FakeSyncService:
    result: PortfolioSyncSummary | None = None
    error: Exception | None = None
    force_metadata_calls: list[bool] | None = None

    async def sync(self, *, force_metadata: bool = False) -> PortfolioSyncSummary:
        if self.force_metadata_calls is not None:
            self.force_metadata_calls.append(force_metadata)
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


@dataclass
class FakeQualityService:
    result: QualityReport | None = None
    error: Exception | None = None

    async def get_report(self) -> QualityReport:
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


@dataclass
class FakePerformanceService:
    result: object | None = None
    error: Exception | None = None

    async def replay(self, *, as_of: object | None = None) -> object:
        del as_of
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result

    async def get_report(self) -> object:
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> None:
    """Each test gets the settings routes' full budget; the limiters are process-global."""
    from helios.api_guards import restart_limiter, settings_write_limiter

    settings_write_limiter.reset()
    restart_limiter.reset()


def test_health_reports_local_configuration(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)

    with TestClient(create_app(settings)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "trading212Configured": False,
        "databaseReady": True,
        "trading212Environment": "demo",
    }


def test_health_names_the_live_environment(tmp_path: Path) -> None:
    """The header badge must never say "demo" while Helios is reading a real-money account."""
    settings = Settings(data_dir=tmp_path, t212_base_url="https://live.trading212.com/api/v0")

    with TestClient(create_app(settings)) as client:
        response = client.get("/health")

    assert response.json()["trading212Environment"] == "live"


def test_positions_are_unavailable_without_credentials(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)

    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/t212/positions")

    assert response.status_code == 503
    assert response.json() == {"detail": "Trading 212 credentials are not configured"}


def test_portfolio_sync_endpoint_returns_summary_and_force_flag(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, t212_api_key="key", t212_api_secret=SecretStr("secret"))
    calls: list[bool] = []
    sync_service = FakeSyncService(
        result=PortfolioSyncSummary.model_validate(
            {
                "asOf": "2024-01-01T00:00:00Z",
                "metadataFetched": True,
                "endpoints": [{"endpoint": "/equity/positions", "fetched": True, "itemCount": 1}],
            }
        ),
        force_metadata_calls=calls,
    )
    app = create_app(settings)
    app.dependency_overrides[get_container] = lambda: _fake_container(settings)
    app.dependency_overrides[get_portfolio_sync_service] = lambda: sync_service

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/portfolio/sync?force_metadata=true",
            headers={"X-Helios-Local-Action": "sync"},
        )

    assert response.status_code == 200
    assert response.json()["metadataFetched"] is True
    assert calls == [True]


def test_portfolio_sync_endpoint_maps_safe_errors(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, t212_api_key="key", t212_api_secret=SecretStr("secret"))
    app = create_app(settings)
    app.dependency_overrides[get_container] = lambda: _fake_container(settings)

    scenarios = [
        (SyncAlreadyRunningError("busy"), 409, "Portfolio sync already running"),
        (Trading212TransportError("boom"), 502, "Trading 212 request failed"),
    ]
    for error, status_code, detail in scenarios:
        app.dependency_overrides[get_portfolio_sync_service] = lambda error=error: FakeSyncService(
            error=error
        )
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/portfolio/sync",
                headers={"X-Helios-Local-Action": "sync"},
            )
        assert response.status_code == status_code
        assert response.json() == {"detail": detail}


def test_portfolio_sync_endpoint_rejects_missing_credentials_before_sync(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    app = create_app(settings)
    app.dependency_overrides[get_container] = lambda: _fake_container(settings)
    app.dependency_overrides[get_portfolio_sync_service] = lambda: FakeSyncService(
        error=AssertionError("should not run")
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/portfolio/sync",
            headers={"X-Helios-Local-Action": "sync"},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "Trading 212 credentials are not configured"}


def test_portfolio_sync_endpoint_requires_local_action_header(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, t212_api_key="key", t212_api_secret=SecretStr("secret"))
    app = create_app(settings)
    app.dependency_overrides[get_container] = lambda: _fake_container(settings)
    app.dependency_overrides[get_portfolio_sync_service] = lambda: FakeSyncService(
        error=AssertionError("should not run")
    )

    with TestClient(app) as client:
        missing = client.post("/api/v1/portfolio/sync")
        wrong = client.post(
            "/api/v1/portfolio/sync",
            headers={"X-Helios-Local-Action": "wrong"},
        )

    assert missing.status_code == 403
    assert missing.json() == {"detail": "Missing required local action confirmation"}
    assert wrong.status_code == 403
    assert wrong.json() == {"detail": "Missing required local action confirmation"}


def test_quality_report_endpoint_maps_found_and_not_found(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    report = QualityReport.model_validate(
        {
            "asOf": "2024-01-01T00:00:00Z",
            "overallStatus": "OK",
            "endpointStatuses": [],
            "metadataFreshness": {
                "endpoint": "/equity/metadata/instruments",
                "fresh": True,
                "ttlHours": 24,
                "checkedAt": "2024-01-01T00:00:00Z",
                "lastSuccessAt": "2024-01-01T00:00:00Z",
            },
            "unresolvedInstruments": [],
            "ambiguousInstruments": [],
            "overrideRequiredInstruments": [],
            "reconciliationMismatches": [],
            "unsupportedActions": [],
        }
    )
    app = create_app(settings)
    app.dependency_overrides[get_portfolio_quality_report_service] = lambda: FakeQualityService(
        result=report
    )

    with TestClient(app) as client:
        ok_response = client.get("/api/v1/portfolio/data-quality")

    assert ok_response.status_code == 200
    assert ok_response.json()["overallStatus"] == "OK"

    app.dependency_overrides[get_portfolio_quality_report_service] = lambda: FakeQualityService(
        error=NoQualityReportDataError("missing")
    )
    with TestClient(app) as client:
        missing_response = client.get("/api/v1/portfolio/data-quality")

    assert missing_response.status_code == 404
    assert missing_response.json() == {"detail": "No portfolio quality report available"}


def test_performance_replay_endpoint_requires_local_action_header(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    app = create_app(settings)
    app.dependency_overrides[get_performance_replay_service] = lambda: FakePerformanceService(
        result=PerformanceReplaySummaryModel.model_validate(
            {
                "asOf": date(2024, 1, 2),
                "startDate": date(2024, 1, 1),
                "endDate": date(2024, 1, 2),
                "flowTiming": "flow_at_close",
                "holdingsWritten": 1,
                "navWritten": 1,
                "unsupportedQuantityEvents": [],
                "missingPriceSymbols": [],
                "stalePriceSymbols": [],
                "missingFxCurrencies": [],
                "staleFxCurrencies": [],
                "excludedFlowCurrencies": [],
                "notes": [],
            }
        )
    )

    with TestClient(app) as client:
        denied = client.post("/api/v1/performance/replay")
        allowed = client.post(
            "/api/v1/performance/replay",
            headers={"X-Helios-Local-Action": "replay"},
        )

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json()["holdingsWritten"] == 1


@dataclass
class FakeNewsService:
    items: list[object] | None = None
    summary: object | None = None
    seen: dict[str, object] | None = None

    async def sync(self) -> object:
        assert self.summary is not None
        return self.summary

    async def list_ranked_news(
        self,
        *,
        t212_ticker: str | None = None,
        isin: str | None = None,
        limit: int = 50,
        held_only: bool = False,
        mentions_only: bool = False,
    ) -> list[object]:
        if self.seen is not None:
            self.seen.update(
                {
                    "ticker": t212_ticker,
                    "isin": isin,
                    "limit": limit,
                    "held_only": held_only,
                    "mentions_only": mentions_only,
                }
            )
        return [
            SimpleNamespace(item=item, relevance="headline", matched_term="Apple", held=True)
            for item in self.items or []
        ]


def test_news_sync_endpoint_requires_local_action_header(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    app = create_app(settings)
    summary = NewsSyncSummaryModel.model_validate(
        {
            "asOf": "2024-05-01T12:00:00Z",
            "feedsConfigured": 0,
            "feedsFetched": 0,
            "rawStored": 0,
            "itemsParsed": 0,
            "itemsWritten": 0,
            "duplicatesSkipped": 0,
            "crossSourceMerges": 0,
            "sourcesUsed": [],
            "failures": [],
            "notes": ["No news sources configured."],
        }
    )
    app.dependency_overrides[get_news_sync_service] = lambda: FakeNewsService(summary=summary)

    with TestClient(app) as client:
        denied = client.post("/api/v1/news/sync")
        allowed = client.post("/api/v1/news/sync", headers={"X-Helios-Local-Action": "news-sync"})

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json()["notes"] == ["No news sources configured."]


def test_news_list_endpoint_passes_filters_and_validates_limit(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    app = create_app(settings)
    seen: dict[str, object] = {}
    item = SimpleNamespace(
        dedupe_key="abc",
        feed_key="mkt",
        provider="rss",
        source_label="Example Markets",
        t212_ticker="AAPL_US_EQ",
        isin="US037",
        headline="Apple beats expectations",
        summary=None,
        url="https://example.com/a",
        published_at=None,
        fetched_at="2024-05-01T12:00:00Z",
    )
    app.dependency_overrides[get_news_sync_service] = lambda: FakeNewsService(
        items=[item], seen=seen
    )

    with TestClient(app) as client:
        ok = client.get("/api/v1/news?ticker=AAPL_US_EQ&limit=5")
        first_call = dict(seen)
        too_many = client.get("/api/v1/news?limit=500")
        filtered = client.get("/api/v1/news?heldOnly=true&mentionsOnly=true")

    assert ok.status_code == 200
    assert first_call == {
        "ticker": "AAPL_US_EQ",
        "isin": None,
        "limit": 5,
        "held_only": False,
        "mentions_only": False,
    }
    assert (seen["held_only"], seen["mentions_only"]) == (True, True)
    body = ok.json()
    assert body[0]["sourceLabel"] == "Example Markets"
    assert body[0]["url"] == "https://example.com/a"
    # Every item says whether, and by which words, it names the holding.
    assert body[0]["relevance"] == "headline"
    assert body[0]["matchedTerm"] == "Apple"
    assert body[0]["held"] is True
    assert filtered.status_code == 200
    assert too_many.status_code == 422


def test_performance_report_endpoint_maps_not_found(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    app = create_app(settings)
    app.dependency_overrides[get_performance_replay_service] = lambda: FakePerformanceService(
        error=NoPerformanceDataError("missing")
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/performance/report")

    assert response.status_code == 404
    assert response.json() == {"detail": "No performance report available"}


# ---------------------------------------------------------------------------
# Local-action guard
# ---------------------------------------------------------------------------


#: Every mutating route and the action it requires. Kept as data so a route added without a
#: guard fails the coverage test below rather than shipping open.
MUTATING_ROUTES: list[tuple[str, str, str]] = [
    ("POST", "/api/v1/portfolio/sync", "sync"),
    ("POST", "/api/v1/performance/replay", "replay"),
    ("POST", "/api/v1/news/sync", "news-sync"),
    ("POST", "/api/v1/card/refresh", "card-refresh"),
    ("PUT", "/api/v1/card/budgets", "card-budget"),
    ("POST", "/api/v1/alerts", "alerts-write"),
    ("POST", "/api/v1/watchlist", "watchlist-write"),
    ("DELETE", "/api/v1/watchlist/AAPL_US_EQ", "watchlist-write"),
    ("DELETE", "/api/v1/alerts/1", "alerts-write"),
    ("POST", "/api/v1/notifications/1/delivered", "notifications-ack"),
    ("POST", "/api/v1/ai/analyse", "ai-analyse"),
    ("POST", "/api/v1/theses", "thesis-write"),
    ("PATCH", "/api/v1/theses/1", "thesis-write"),
    ("POST", "/api/v1/theses/1/transition", "thesis-write"),
    ("POST", "/api/v1/journal", "journal-write"),
    # The settings routes stack further checks on top of the header -- loopback peer address,
    # fetch metadata, a rate limit -- but the header is still the first gate, and these assert
    # it alone. The stacked guards get their own tests below.
    ("PUT", "/api/v1/settings/credential", "settings-write"),
    ("PUT", "/api/v1/settings/value", "settings-write"),
    ("PUT", "/api/v1/settings/trading212", "settings-write"),
    ("POST", "/api/v1/settings/databases", "settings-write"),
    ("PUT", "/api/v1/settings/databases/active", "settings-write"),
    ("POST", "/api/v1/settings/restart", "restart"),
]


@pytest.mark.parametrize(("method", "path", "action"), MUTATING_ROUTES)
def test_every_mutating_route_refuses_without_the_local_action_header(
    tmp_path: Path, method: str, path: str, action: str
) -> None:
    """The guarded boundary is mutating vs non-mutating, not expensive vs cheap.

    A thesis write is as much a state change as a sync, so it carries the same gate. The
    header is what distinguishes Helios' own server-side caller from ambient loopback
    traffic; a browser cannot set it cross-origin without a CORS preflight Helios never
    grants.
    """
    app = create_app(Settings(data_dir=tmp_path))

    with TestClient(app) as client:
        missing = client.request(method, path, json={})
        wrong = client.request(method, path, json={}, headers={"X-Helios-Local-Action": "nope"})

    for response in (missing, wrong):
        assert response.status_code == 403, f"{method} {path} accepted an unconfirmed call"
        assert response.json() == {"detail": "Missing required local action confirmation"}


def test_guard_covers_every_mutating_route_the_router_declares(tmp_path: Path) -> None:
    """Fail when a mutating route is added without being listed and guarded above.

    Without this, the next POST added to the router inherits no protection and nothing says
    so. The OpenAPI schema is the source of truth rather than ``app.routes``: FastAPI mounts
    an included router as a single opaque entry, so walking the route list would silently see
    nothing and let this assertion pass vacuously.
    """
    app = create_app(Settings(data_dir=tmp_path))
    declared: set[tuple[str, str]] = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
        if method.upper() in {"POST", "PATCH", "PUT", "DELETE"}
    }

    covered = {
        ("POST", "/api/v1/portfolio/sync"),
        ("POST", "/api/v1/performance/replay"),
        ("POST", "/api/v1/news/sync"),
        ("POST", "/api/v1/card/refresh"),
        ("PUT", "/api/v1/card/budgets"),
        ("POST", "/api/v1/alerts"),
        ("POST", "/api/v1/watchlist"),
        ("DELETE", "/api/v1/watchlist/{ticker}"),
        ("DELETE", "/api/v1/alerts/{alert_id}"),
        ("POST", "/api/v1/notifications/{notification_id}/delivered"),
        ("POST", "/api/v1/ai/analyse"),
        ("POST", "/api/v1/theses"),
        ("PATCH", "/api/v1/theses/{thesis_id}"),
        ("POST", "/api/v1/theses/{thesis_id}/transition"),
        ("POST", "/api/v1/journal"),
        ("PUT", "/api/v1/settings/credential"),
        ("PUT", "/api/v1/settings/value"),
        ("PUT", "/api/v1/settings/trading212"),
        ("POST", "/api/v1/settings/databases"),
        ("PUT", "/api/v1/settings/databases/active"),
        ("POST", "/api/v1/settings/restart"),
    }

    assert declared == covered, (
        "A mutating route is missing from the guard coverage list. Add it to MUTATING_ROUTES "
        "and give it a require_local_action dependency."
    )


def test_read_routes_do_not_require_the_header(tmp_path: Path) -> None:
    """The gate must not leak onto reads: a GET with no header still has to work."""
    app = create_app(Settings(data_dir=tmp_path))

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Settings routes
#
# These carry more protection than the rest of the API because they are the only ones that can
# rewrite a brokerage credential or bounce the process. Each test below pins one of those extra
# checks, so weakening one fails here rather than in a security review after the fact.
# ---------------------------------------------------------------------------


#: Every settings route that changes something, with the action it names.
SETTINGS_WRITE_ROUTES: list[tuple[str, str, str, dict[str, str]]] = [
    ("PUT", "/api/v1/settings/credential", "settings-write", {"field": "x", "value": "y"}),
    ("PUT", "/api/v1/settings/value", "settings-write", {"field": "x", "value": "y"}),
    (
        "PUT",
        "/api/v1/settings/trading212",
        "settings-write",
        {"environment": "live", "apiKey": "k", "apiSecret": "s"},
    ),
    ("POST", "/api/v1/settings/databases", "settings-write", {"filename": "x.sqlite3"}),
    (
        "PUT",
        "/api/v1/settings/databases/active",
        "settings-write",
        {"filename": "x.sqlite3"},
    ),
    ("POST", "/api/v1/settings/restart", "restart", {}),
]


@pytest.mark.parametrize(("method", "path", "action", "body"), SETTINGS_WRITE_ROUTES)
def test_settings_writes_refuse_a_non_loopback_caller(
    tmp_path: Path, method: str, path: str, action: str, body: dict[str, str]
) -> None:
    """A credential write must be impossible from off-machine, whatever the bind address is.

    The peer check is deliberately independent of `HELIOS_BIND_HOST`: an operator who binds
    0.0.0.0 to reach the dashboard from a phone should not thereby expose the routes that
    rewrite their brokerage key.
    """
    app = create_app(Settings(data_dir=tmp_path))

    with TestClient(app, client=("10.0.0.5", 4444)) as client:
        response = client.request(
            method, path, json=body, headers={"X-Helios-Local-Action": action}
        )

    assert response.status_code == 403, f"{method} {path} accepted an off-machine caller"
    assert "only be changed from this machine" in response.json()["detail"]


@pytest.mark.parametrize(("method", "path", "action", "body"), SETTINGS_WRITE_ROUTES)
def test_settings_writes_refuse_a_foreign_origin(
    tmp_path: Path, method: str, path: str, action: str, body: dict[str, str]
) -> None:
    """A page on another site must not drive these routes through an already-open browser.

    The action header alone does not cover this case, which is why the origin check exists
    alongside it rather than instead of it.
    """
    app = create_app(Settings(data_dir=tmp_path))

    with TestClient(app, client=("127.0.0.1", 4444)) as client:
        response = client.request(
            method,
            path,
            json=body,
            headers={
                "X-Helios-Local-Action": action,
                "Origin": "https://evil.example.com",
                # Claiming same-origin must not help: the Origin value is checked on its own.
                "Sec-Fetch-Site": "same-origin",
            },
        )

    assert response.status_code == 403, f"{method} {path} accepted a foreign origin"
    assert "evil.example.com" in response.json()["detail"]


@pytest.mark.parametrize(("method", "path", "action", "body"), SETTINGS_WRITE_ROUTES)
def test_settings_writes_refuse_a_cross_site_fetch(
    tmp_path: Path, method: str, path: str, action: str, body: dict[str, str]
) -> None:
    """`Sec-Fetch-Site: cross-site` is the browser telling us this was not our own page."""
    app = create_app(Settings(data_dir=tmp_path))

    with TestClient(app, client=("127.0.0.1", 4444)) as client:
        response = client.request(
            method,
            path,
            json=body,
            headers={"X-Helios-Local-Action": action, "Sec-Fetch-Site": "cross-site"},
        )

    assert response.status_code == 403, f"{method} {path} accepted a cross-site fetch"
    assert "Cross-origin request refused" in response.json()["detail"]


def test_settings_read_never_returns_a_credential_value(tmp_path: Path) -> None:
    """The whole justification for an unauthenticated local read is that it holds no secret.

    If this ever fails, the settings page stops being safe to render and the read route needs
    the write guards too.
    """
    settings = Settings(
        data_dir=tmp_path,
        t212_api_key="key-abcdefgh1234",
        t212_api_secret=SecretStr("secret-abcdefgh5678"),
        anthropic_api_key=SecretStr("sk-ant-abcdefgh9012"),
    )
    app = create_app(settings)

    with TestClient(app, client=("127.0.0.1", 4444)) as client:
        response = client.get("/api/v1/settings")

    assert response.status_code == 200
    body = response.text
    for secret in ("key-abcdefgh1234", "secret-abcdefgh5678", "sk-ant-abcdefgh9012"):
        assert secret not in body, "a credential value reached the settings response"

    payload = response.json()
    by_field = {row["field"]: row for row in payload["credentials"]}
    assert by_field["t212_api_key"]["present"] is True
    # Four characters is enough to recognise a key you pasted and useless to anyone else.
    assert by_field["t212_api_key"]["hint"] == "…1234"
    assert by_field["market_data_api_key"]["present"] is False
    assert by_field["market_data_api_key"]["hint"] is None


def test_settings_read_refuses_a_non_loopback_caller(tmp_path: Path) -> None:
    """Even a secret-free read stays on this machine: it names the operator's configuration."""
    app = create_app(Settings(data_dir=tmp_path))

    with TestClient(app, client=("10.0.0.5", 4444)) as client:
        response = client.get("/api/v1/settings")

    assert response.status_code == 403


def _resolve_web_to(address: str) -> Callable[..., list[tuple[object, ...]]]:
    """A stand-in for `socket.getaddrinfo` that knows exactly one host, the Compose `web`."""

    def fake_getaddrinfo(host: str, *_args: object, **_kwargs: object) -> list[tuple[object, ...]]:
        if host != "web":
            raise OSError(f"cannot resolve {host}")
        return [(2, 1, 6, "", (address, 0))]

    return fake_getaddrinfo


def test_settings_read_admits_the_named_compose_peer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under Compose the dashboard calls from its own container, never from 127.0.0.1.

    Naming that container is what lets the settings page render there at all; before this, every
    Compose deployment showed "Settings unavailable".
    """
    monkeypatch.setattr("helios.api_guards.socket.getaddrinfo", _resolve_web_to("172.18.0.3"))
    app = create_app(Settings(data_dir=tmp_path, settings_trusted_peers="web"))

    with TestClient(app, client=("172.18.0.3", 4444)) as client:
        response = client.get("/api/v1/settings")

    assert response.status_code == 200


def test_trusted_peer_admits_only_its_own_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Naming a peer trusts that host, not its network: a neighbour on the bridge is refused."""
    monkeypatch.setattr("helios.api_guards.socket.getaddrinfo", _resolve_web_to("172.18.0.3"))
    app = create_app(Settings(data_dir=tmp_path, settings_trusted_peers="web"))

    with TestClient(app, client=("172.18.0.9", 4444)) as client:
        response = client.get("/api/v1/settings")

    assert response.status_code == 403


def test_unresolvable_trusted_peer_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A name that does not resolve must admit nobody, not everybody."""
    monkeypatch.setattr("helios.api_guards.socket.getaddrinfo", _resolve_web_to("172.18.0.3"))
    app = create_app(Settings(data_dir=tmp_path, settings_trusted_peers="db"))

    with TestClient(app, client=("172.18.0.3", 4444)) as client:
        response = client.get("/api/v1/settings")

    assert response.status_code == 403


@pytest.mark.parametrize(("method", "path", "action", "body"), SETTINGS_WRITE_ROUTES)
def test_read_only_deployment_refuses_every_settings_write(
    tmp_path: Path, method: str, path: str, action: str, body: dict[str, str]
) -> None:
    """Under Compose a saved setting would never be read, so saving one must not be possible.

    The environment compose injects outranks anything the container writes to `.env`, and there
    is no keyring in the image. Accepting the write would report success for a change that never
    happens -- the exact dishonesty this surface exists to avoid.
    """
    app = create_app(Settings(data_dir=tmp_path, settings_writable=False))

    with TestClient(app, client=("127.0.0.1", 4444)) as client:
        response = client.request(
            method, path, json=body, headers={"X-Helios-Local-Action": action}
        )

    assert response.status_code == 409, f"{method} {path} accepted a write it cannot apply"
    assert "read-only in this deployment" in response.json()["detail"]
    assert not (tmp_path / "x.sqlite3").exists()


def test_read_only_deployment_says_so_in_the_snapshot(tmp_path: Path) -> None:
    """The page needs to know before the operator types a key, not after they submit it."""
    writable = create_app(Settings(data_dir=tmp_path))
    read_only = create_app(Settings(data_dir=tmp_path, settings_writable=False))

    with TestClient(writable, client=("127.0.0.1", 4444)) as client:
        open_payload = client.get("/api/v1/settings").json()
    with TestClient(read_only, client=("127.0.0.1", 4444)) as client:
        closed_payload = client.get("/api/v1/settings").json()

    assert open_payload["writable"] is True
    assert open_payload["readOnlyReason"] is None
    assert closed_payload["writable"] is False
    assert "docker compose up -d" in closed_payload["readOnlyReason"]


def test_settings_write_rejects_an_unknown_field(tmp_path: Path) -> None:
    """An unrecognised field means caller and server disagree about what is being written."""
    app = create_app(Settings(data_dir=tmp_path))

    with TestClient(app, client=("127.0.0.1", 4444)) as client:
        unknown_credential = client.put(
            "/api/v1/settings/credential",
            json={"field": "not_a_credential", "value": "x"},
            headers={"X-Helios-Local-Action": "settings-write"},
        )
        extra_key = client.put(
            "/api/v1/settings/value",
            json={"field": "t212_base_url", "value": "x", "surprise": "y"},
            headers={"X-Helios-Local-Action": "settings-write"},
        )

    assert unknown_credential.status_code == 400
    assert "not a credential" in unknown_credential.json()["detail"]
    # `extra="forbid"` on the write models: a stray key is a 422, not a silent drop.
    assert extra_key.status_code == 422


def test_base_url_write_refuses_a_host_trading212_does_not_operate(tmp_path: Path) -> None:
    """The substitution attack: a rewritten base URL sends the next sync's credentials away."""
    app = create_app(Settings(data_dir=tmp_path))

    with TestClient(app, client=("127.0.0.1", 4444)) as client:
        response = client.put(
            "/api/v1/settings/value",
            json={"field": "t212_base_url", "value": "https://evil.example.com/api/v0"},
            headers={"X-Helios-Local-Action": "settings-write"},
        )

    assert response.status_code == 400
    assert "must be one of" in response.json()["detail"]


def test_database_list_reports_the_active_file(tmp_path: Path) -> None:
    """The listing is a read, so it is loopback-guarded but needs no action header."""
    settings = Settings(data_dir=tmp_path, sqlite_filename="helios.sqlite3")
    app = create_app(settings)

    with TestClient(app, client=("127.0.0.1", 4444)) as client:
        response = client.get("/api/v1/settings/databases")

    assert response.status_code == 200
    payload = response.json()
    assert payload["active"] == "helios.sqlite3"
    # The app's own startup migration created it, so it lists and is marked active.
    active_rows = [row for row in payload["databases"] if row["active"]]
    assert [row["filename"] for row in active_rows] == ["helios.sqlite3"]


def test_database_switch_refuses_a_path_instead_of_a_filename(tmp_path: Path) -> None:
    """Containment for the switch: only a bare name inside the data directory is accepted."""
    app = create_app(Settings(data_dir=tmp_path))

    with TestClient(app, client=("127.0.0.1", 4444)) as client:
        traversal = client.put(
            "/api/v1/settings/databases/active",
            json={"filename": "../../elsewhere.sqlite3"},
            headers={"X-Helios-Local-Action": "settings-write"},
        )
        missing = client.put(
            "/api/v1/settings/databases/active",
            json={"filename": "nope.sqlite3"},
            headers={"X-Helios-Local-Action": "settings-write"},
        )

    assert traversal.status_code == 400
    assert "looks like a path" in traversal.json()["detail"]
    assert missing.status_code == 400
    assert "does not exist" in missing.json()["detail"]


def test_a_saved_setting_is_what_the_next_start_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The round trip the settings page promises: save, restart, and the new value is live.

    Each half had a test; nothing checked they met. The write went to `.env` and nothing read
    `.env`, so every saved non-credential setting was silently discarded on restart.
    """
    # Pinned rather than chdir'd: an installed (non-editable) Helios finds its migrations
    # relative to the working directory, so moving it would test the wrong thing.
    monkeypatch.setenv("HELIOS_ENV_FILE", str(tmp_path / ".env"))
    monkeypatch.delenv("HELIOS_DISABLE_DOTENV", raising=False)
    monkeypatch.delenv("HELIOS_MARKET_DATA_PROVIDER", raising=False)
    app = create_app(Settings(data_dir=tmp_path / "data"))

    with TestClient(app, client=("127.0.0.1", 4444)) as client:
        response = client.put(
            "/api/v1/settings/value",
            json={"field": "market_data_provider", "value": "twelvedata"},
            headers={"X-Helios-Local-Action": "settings-write"},
        )

    assert response.status_code == 200
    assert (tmp_path / ".env").is_file()
    # A fresh Settings() is exactly what the restarted process builds.
    assert Settings().market_data_provider == "twelvedata"


def test_restart_argv_survives_a_path_with_a_space(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows exec does not quote its arguments, so an unquoted space splits the interpreter path.

    Found by pressing Restart on a real install under `Portolio Tracker/`: the process exited
    trying to run a truncated `.../Portolio` interpreter path and never came back.
    """
    import os
    import sys

    from helios.api import restart_argv

    interpreter = r"C:\Apps\My Project\.venv\Scripts\python.exe"
    monkeypatch.setattr(sys, "executable", interpreter)
    # What `python -m uvicorn helios.app:app` leaves behind: sys.argv[0] has been rewritten to
    # uvicorn's __main__.py, and re-running *that* shadows the stdlib `logging` with uvicorn's.
    monkeypatch.setattr(sys, "argv", [r"C:\Apps\My Project\uvicorn\__main__.py", "x"])
    monkeypatch.setattr(sys, "orig_argv", ["python", "-m", "uvicorn", "helios.app:app"])

    monkeypatch.setattr(os, "name", "nt")
    windows = restart_argv()
    monkeypatch.setattr(os, "name", "posix")
    posix = restart_argv()

    assert windows[0] == f'"{interpreter}"'
    # The module form survives, and an argument without whitespace is left alone -- quoting it
    # would hand uvicorn literal quote characters.
    assert windows[1:] == ["-m", "uvicorn", "helios.app:app"]
    # POSIX exec passes the vector through untouched, so quoting there would corrupt it.
    assert posix == [interpreter, "-m", "uvicorn", "helios.app:app"]


# ---------------------------------------------------------------------------
# Connecting Trading 212 in one step
# ---------------------------------------------------------------------------


def _connect_harness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, verify_error: str | None = None
) -> tuple[list[tuple[str, str]], list[str]]:
    """Record Trading 212 verification and keyring writes instead of performing them."""
    from helios.settings_service import SettingsWriteError

    stored: list[tuple[str, str]] = []
    verified_against: list[str] = []

    async def fake_verify(*, api_key: str, api_secret: str, base_url: str) -> str:
        del api_key, api_secret
        verified_against.append(base_url)
        if verify_error is not None:
            raise SettingsWriteError(verify_error)
        return "live account 4242 (EUR)" if "live." in base_url else "demo account 7 (EUR)"

    def fake_store(field: str, value: str) -> str:
        stored.append((field, value))
        return "keyring"

    monkeypatch.setattr("helios.api.verify_t212_credentials", fake_verify)
    monkeypatch.setattr("helios.api.store_credential", fake_store)
    monkeypatch.setenv("HELIOS_ENV_FILE", str(tmp_path / ".env"))
    monkeypatch.delenv("HELIOS_T212_BASE_URL", raising=False)
    return stored, verified_against


def _local_client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings), client=("127.0.0.1", 4444))


def test_connecting_live_verifies_against_live_and_stores_the_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bug this replaces: Live was selected, yet the key was checked against demo."""
    stored, verified_against = _connect_harness(tmp_path, monkeypatch)

    with _local_client(Settings(data_dir=tmp_path / "data")) as client:
        response = client.put(
            "/api/v1/settings/trading212",
            json={"environment": "live", "apiKey": " key-1 ", "apiSecret": "secret-1"},
            headers={"X-Helios-Local-Action": "settings-write"},
        )
        snapshot = client.get("/api/v1/settings").json()

    assert response.status_code == 200
    assert verified_against == ["https://live.trading212.com/api/v0"]
    assert stored == [("t212_api_key", "key-1"), ("t212_api_secret", "secret-1")]
    assert "HELIOS_T212_BASE_URL=https://live.trading212.com/api/v0" in (
        tmp_path / ".env"
    ).read_text(encoding="utf-8")
    assert "Live account" in response.json()["detail"]
    # Still running on demo until the restart; the page must be able to say so honestly.
    assert snapshot["t212Environment"] == "demo"
    assert snapshot["t212PendingEnvironment"] == "live"
    assert snapshot["restartRequired"] is True


def test_a_rejected_key_pair_stores_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Half-saved credentials are worse than none: the secret used to land without its key."""
    stored, _ = _connect_harness(
        tmp_path, monkeypatch, verify_error="Trading 212 rejected this key pair on live."
    )

    with _local_client(Settings(data_dir=tmp_path / "data")) as client:
        response = client.put(
            "/api/v1/settings/trading212",
            json={"environment": "live", "apiKey": "key-1", "apiSecret": "secret-1"},
            headers={"X-Helios-Local-Action": "settings-write"},
        )

    assert response.status_code == 400
    assert "rejected" in response.json()["detail"]
    assert stored == []
    assert not (tmp_path / ".env").exists()


def test_connecting_needs_both_halves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stored, verified_against = _connect_harness(tmp_path, monkeypatch)

    with _local_client(Settings(data_dir=tmp_path / "data")) as client:
        response = client.put(
            "/api/v1/settings/trading212",
            json={"environment": "demo", "apiKey": "key-1", "apiSecret": "  "},
            headers={"X-Helios-Local-Action": "settings-write"},
        )

    assert response.status_code == 400
    assert "both" in response.json()["detail"]
    assert stored == [] and verified_against == []


def test_single_key_rotation_verifies_against_the_saved_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After switching to live (saved, not yet restarted), a rotated key is checked on live."""
    _, verified_against = _connect_harness(tmp_path, monkeypatch)
    (tmp_path / ".env").write_text(
        "HELIOS_T212_BASE_URL=https://live.trading212.com/api/v0\n", encoding="utf-8"
    )
    settings = Settings(data_dir=tmp_path / "data", t212_api_secret=SecretStr("existing-secret"))

    with _local_client(settings) as client:
        response = client.put(
            "/api/v1/settings/credential",
            json={"field": "t212_api_key", "value": "rotated-key"},
            headers={"X-Helios-Local-Action": "settings-write"},
        )

    assert response.status_code == 200
    assert verified_against == ["https://live.trading212.com/api/v0"]


def test_account_summary_is_served_as_trading212_reports_it(tmp_path: Path) -> None:
    """One source for every "now" figure, so the pages cannot disagree with each other."""
    from helios.schemas import AccountSummary

    class FakeT212:
        async def get_account_summary(self) -> AccountSummary:
            return AccountSummary.model_validate(
                {
                    "id": 1,
                    "currency": "EUR",
                    "totalValue": 1358.69,
                    "cash": {"availableToTrade": 0.21, "reservedForOrders": 0, "inPies": 2.96},
                    "investments": {"currentValue": 1162.80, "totalCost": 907.53},
                }
            )

    app = create_app(Settings(data_dir=tmp_path))
    app.dependency_overrides[get_t212_service] = FakeT212

    with TestClient(app) as client:
        body = client.get("/api/v1/t212/account").json()

    assert Decimal(body["totalValue"]) == Decimal("1358.69")
    assert Decimal(body["investments"]["currentValue"]) == Decimal("1162.80")
    assert Decimal(body["cash"]["inPies"]) == Decimal("2.96")


def test_a_manual_sync_refreshes_history_and_news_in_the_background(tmp_path: Path) -> None:
    """Found live: after pressing Sync, the history stayed stale until Replay was pressed too."""
    import asyncio

    calls: list[str] = []

    class Replay:
        async def replay(self) -> None:
            calls.append("replay")

    class News:
        async def sync(self) -> None:
            calls.append("news")

    settings = Settings(
        data_dir=tmp_path,
        t212_api_key="key",
        t212_api_secret=SecretStr("secret"),
        refresh_after_sync=True,
    )
    sync_service = FakeSyncService(
        result=PortfolioSyncSummary.model_validate(
            {"asOf": "2024-01-01T00:00:00Z", "metadataFetched": False, "endpoints": []}
        )
    )
    app = create_app(settings)
    app.dependency_overrides[get_container] = lambda: _fake_container(
        settings, performance_replay_service=Replay(), news_sync_service=News()
    )
    app.dependency_overrides[get_portfolio_sync_service] = lambda: sync_service

    with TestClient(app) as client:
        response = client.post("/api/v1/portfolio/sync", headers={"X-Helios-Local-Action": "sync"})
        # The refresh runs on the app's event loop after the response; give it a turn.
        client.portal.call(asyncio.sleep, 0.05)  # type: ignore[union-attr]

    assert response.status_code == 200
    assert calls == ["replay", "news"]


@pytest.mark.parametrize("enabled", [True, False])
def test_settings_saves_apply_with_one_debounced_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    """Saving a key, a provider and a second key must cost one restart, not three."""
    import asyncio

    from helios import api

    restarts: list[str] = []
    monkeypatch.setattr(api, "_perform_restart", lambda: restarts.append("restart"))
    monkeypatch.setattr(api, "AUTO_APPLY_DELAY_SECONDS", 0.05)
    monkeypatch.setenv("HELIOS_ENV_FILE", str(tmp_path / ".env"))
    app = create_app(Settings(data_dir=tmp_path / "data", auto_apply_settings=enabled))

    with TestClient(app, client=("127.0.0.1", 4444)) as client:
        for value in ("twelvedata", "alphavantage"):
            response = client.put(
                "/api/v1/settings/value",
                json={"field": "market_data_provider", "value": value},
                headers={"X-Helios-Local-Action": "settings-write"},
            )
            assert response.status_code == 200
        client.portal.call(asyncio.sleep, 0.2)  # type: ignore[union-attr]

    assert restarts == (["restart"] if enabled else [])
    assert ("applies it in a few seconds" in response.json()["detail"]) is enabled


def test_card_history_reads_freely_but_refreshing_needs_the_local_action(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path))

    with TestClient(app) as client:
        history = client.get("/api/v1/card")
        denied = client.post("/api/v1/card/refresh")
        allowed = client.post(
            "/api/v1/card/refresh", headers={"X-Helios-Local-Action": "card-refresh"}
        )

    assert history.status_code == 200
    body = history.json()
    assert body["status"]["cardRows"] == 0
    assert body["summary"]["spent"] == "0"
    assert body["summary"]["transactions"] == []
    assert denied.status_code == 403
    # No credentials in this test: nothing is requested, and the answer says why.
    assert allowed.status_code == 200
    assert allowed.json()["action"] == "disabled"


def test_card_budgets_are_set_listed_and_removed(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path))
    headers = {"X-Helios-Local-Action": "card-budget"}

    with TestClient(app) as client:
        denied = client.put(
            "/api/v1/card/budgets", json={"category": "MEMBERSHIPS", "monthlyLimit": "50"}
        )
        created = client.put(
            "/api/v1/card/budgets",
            json={"category": "memberships", "monthlyLimit": "50"},
            headers=headers,
        )
        listed = client.get("/api/v1/card").json()["budgets"]
        negative = client.put(
            "/api/v1/card/budgets", json={"category": "X", "monthlyLimit": "-1"}, headers=headers
        )
        removed = client.put(
            "/api/v1/card/budgets", json={"category": "MEMBERSHIPS"}, headers=headers
        )

    assert denied.status_code == 403
    assert created.status_code == 200
    # Categories are stored in Trading 212's own upper-case codes.
    assert listed == [{"category": "MEMBERSHIPS", "monthlyLimit": "50"}]
    assert negative.status_code == 422
    assert removed.json() == []


def test_alerts_are_created_listed_and_deleted(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path))
    headers = {"X-Helios-Local-Action": "alerts-write"}

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/alerts",
            json={"ticker": "MU_US_EQ", "kind": "below", "threshold": "900", "note": " dip "},
            headers=headers,
        )
        bad_kind = client.post(
            "/api/v1/alerts",
            json={"ticker": "MU_US_EQ", "kind": "sideways", "threshold": "1"},
            headers=headers,
        )
        listed = client.get("/api/v1/alerts?ticker=MU_US_EQ").json()
        deleted = client.delete(f"/api/v1/alerts/{created.json()['id']}", headers=headers)
        gone = client.delete(f"/api/v1/alerts/{created.json()['id']}", headers=headers)
        notifications = client.get("/api/v1/notifications?pending=true")

    assert created.status_code == 200
    assert created.json()["note"] == "dip"
    assert created.json()["active"] is True
    assert bad_kind.status_code == 422
    assert [alert["kind"] for alert in listed] == ["below"]
    assert (deleted.status_code, gone.status_code) == (204, 404)
    assert notifications.json() == []
