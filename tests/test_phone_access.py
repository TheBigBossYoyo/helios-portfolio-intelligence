"""Phone access: pairing codes, device tokens, and the gateway that admits only paired phones."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient as StarletteClient

from helios.app import create_app as create_api
from helios.config import Settings
from helios.db import migrate_database
from helios.devices import (
    CODE_LENGTH,
    DeviceService,
    normalise_code,
    pick_addresses,
    serve_url_for,
)
from helios.gateway import COOKIE, REMOTE_HEADER, create_app, device_name
from helios.rate_limit import Clock

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


class MovableClock(Clock):
    def __init__(self, current: datetime) -> None:
        self.current = current

    def now(self) -> float:
        return self.current.timestamp()

    def utcnow(self) -> datetime:
        return self.current

    async def sleep(self, seconds: float) -> None:
        return None


async def _service(tmp_path: Path, clock: Clock) -> DeviceService:
    settings = Settings(data_dir=tmp_path, sqlite_filename="devices.sqlite3")
    await migrate_database(settings)
    engine = create_async_engine(settings.sqlite_url, poolclass=NullPool)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)
    return DeviceService(factory, clock)


@pytest.mark.asyncio
async def test_a_code_pairs_one_phone_once_and_the_token_then_verifies(tmp_path: Path) -> None:
    clock = MovableClock(NOW)
    service = await _service(tmp_path, clock)

    pairing = await service.create_pairing()
    assert len(pairing.code) == CODE_LENGTH
    assert (await service.active_pairing()) == pairing

    # Codes are typed off a screen: case and separators do not matter.
    typed = f"{pairing.code[:4].lower()}-{pairing.code[4:]}"
    claimed = await service.claim(typed, "iPhone")
    assert claimed is not None
    assert await service.claim(pairing.code, "Second phone") is None  # one use only
    assert await service.active_pairing() is None

    device = await service.verify(claimed.token)
    assert device is not None and device.name == "iPhone"
    assert await service.verify("not-a-token") is None
    assert [item.name for item in await service.devices()] == ["iPhone"]

    assert await service.revoke(claimed.id) is True
    assert await service.verify(claimed.token) is None
    assert await service.devices() == []


@pytest.mark.asyncio
async def test_codes_expire_and_a_new_code_retires_the_old_one(tmp_path: Path) -> None:
    clock = MovableClock(NOW)
    service = await _service(tmp_path, clock)

    first = await service.create_pairing()
    second = await service.create_pairing()
    assert await service.claim(first.code, "Old") is None

    clock.current = NOW + timedelta(minutes=11)
    assert await service.claim(second.code, "Late") is None
    assert normalise_code(" ab-cd ") == "ABCD"


def test_only_the_routed_address_and_tailscale_are_offered() -> None:
    # WSL and Hyper-V adapters have private addresses too; no phone can reach them.
    others = ["172.27.16.1", "192.168.1.9", "100.101.102.103", "127.0.0.1"]
    assert pick_addresses("192.168.1.9", others) == ["192.168.1.9", "100.101.102.103"]
    assert pick_addresses(None, others) == ["100.101.102.103"]
    assert pick_addresses("8.8.8.8", []) == []  # a public address is not a home network


def test_the_tailscale_serve_address_is_found_for_the_gateway_port() -> None:
    serve = {
        "TCP": {"443": {"HTTPS": True}},
        "Web": {
            "helios-pc.tail1234.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8787"}}}
        },
    }
    assert serve_url_for(serve, 8787) == "https://helios-pc.tail1234.ts.net"
    assert serve_url_for(serve, 9999) is None
    assert serve_url_for(None, 8787) is None


def test_device_names_come_from_the_user_agent() -> None:
    assert device_name("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)") == "iPhone"
    assert device_name("Mozilla/5.0 (Linux; Android 15; Pixel 9) Mobile Safari") == "Android phone"
    assert device_name("") == "Phone"


# --- gateway ---------------------------------------------------------------------------------


def _gateway(*, enabled: bool = True, seen: list[httpx.Request] | None = None):  # type: ignore[no-untyped-def]
    requests = seen if seen is not None else []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = f"dashboard {request.url.path}".encode()
        return httpx.Response(200, stream=httpx.ByteStream(body), headers={"x-up": "1"})

    async def verify(token: str) -> bool:
        return token == "good-token"

    async def claim(code: str, name: str) -> str | None:
        return "good-token" if normalise_code(code) == "ABCDEFGH" else None

    client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    app = create_app(
        enabled=enabled, upstream="http://web.test", verify=verify, claim=claim, client=client
    )
    return StarletteClient(app, base_url="http://192.168.1.20:8787"), requests


def test_an_unpaired_phone_gets_the_pairing_page_and_never_reaches_the_dashboard() -> None:
    client, seen = _gateway()

    page = client.get("/", headers={"accept": "text/html"})
    data = client.get("/holdings?_rsc=1")

    assert page.status_code == 401 and "Pair this phone" in page.text
    assert data.status_code == 401
    assert seen == []
    # A home-screen icon still loads before pairing.
    assert client.get("/apple-icon.png").status_code == 200


def test_pairing_sets_the_cookie_and_a_paired_phone_is_forwarded_marked_remote() -> None:
    client, seen = _gateway()

    shown = client.get("/__helios/pair?code=ABCDEFGH")
    assert "ABCDEFGH" in shown.text  # a GET never spends the code
    paired = client.post(
        "/__helios/pair",
        content="code=abcd-efgh",
        headers={"content-type": "application/x-www-form-urlencoded", "user-agent": "iPhone"},
        follow_redirects=False,
    )
    assert paired.status_code == 303
    set_cookie = paired.headers["set-cookie"]
    assert f"{COOKIE}=good-token" in set_cookie and "HttpOnly" in set_cookie

    client.cookies.set(COOKIE, "good-token")
    page = client.get(
        "/performance?period=1M",
        headers={
            REMOTE_HEADER: "0",
            "X-Helios-Local-Action": "settings-write",
            "cookie": f"{COOKIE}=good-token; other=1",
            "accept-encoding": "br",
        },
    )

    assert page.status_code == 200 and page.text == "dashboard /performance"
    forwarded = seen[-1]
    assert str(forwarded.url) == "http://web.test/performance?period=1M"
    assert forwarded.headers[REMOTE_HEADER] == "1"  # the phone cannot claim to be local
    assert "x-helios-local-action" not in forwarded.headers
    assert forwarded.headers["host"] == "192.168.1.20:8787"
    assert "good-token" not in forwarded.headers.get("cookie", "")
    # Compression exactly as the phone asked for it.
    assert forwarded.headers["accept-encoding"] == "br"


def test_behind_tailscale_serve_the_https_address_is_kept() -> None:
    """``tailscale serve`` relays from this computer: its forwarded headers are believed."""
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, stream=httpx.ByteStream(b"ok"))

    async def verify(token: str) -> bool:
        return token == "good-token"

    async def claim(code: str, name: str) -> str | None:
        return "good-token"

    app = create_app(
        enabled=True,
        upstream="http://web.test",
        verify=verify,
        claim=claim,
        client=httpx.AsyncClient(transport=httpx.MockTransport(upstream)),
    )
    relayed = {
        "host": "helios-pc.tail1234.ts.net",
        "x-forwarded-host": "helios-pc.tail1234.ts.net",
        "x-forwarded-proto": "https",
        "x-forwarded-for": "100.64.0.7",
    }
    local = StarletteClient(app, base_url="http://127.0.0.1:8787", client=("127.0.0.1", 50000))
    paired = local.post(
        "/__helios/pair",
        content="code=ABCDEFGH",
        headers={**relayed, "content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert "Secure" in paired.headers["set-cookie"]  # the phone is on HTTPS
    local.cookies.set(COOKIE, "good-token")
    local.get("/", headers=relayed)
    assert seen[-1].headers["x-forwarded-proto"] == "https"
    assert seen[-1].headers["x-forwarded-host"] == "helios-pc.tail1234.ts.net"

    # The same headers from a phone on the Wi-Fi are its own invention and are dropped.
    remote = StarletteClient(
        app, base_url="http://192.168.1.9:8787", client=("192.168.1.30", 50000)
    )
    remote.cookies.set(COOKIE, "good-token")
    remote.get("/", headers=relayed)
    assert seen[-1].headers["x-forwarded-proto"] == "http"
    assert "x-forwarded-host" not in seen[-1].headers


def test_wrong_codes_are_refused_and_rate_limited() -> None:
    client, _seen = _gateway()
    form = {"content-type": "application/x-www-form-urlencoded"}
    statuses = [
        client.post("/__helios/pair", content="code=WRONGWRONG", headers=form).status_code
        for _ in range(11)
    ]
    assert statuses[:10] == [400] * 10
    assert statuses[10] == 429


def test_with_phone_access_off_the_gateway_serves_nothing() -> None:
    client, seen = _gateway(enabled=False)
    client.cookies.set(COOKIE, "good-token")
    assert client.get("/").status_code == 503
    assert client.get("/__helios/pair").status_code == 503
    assert seen == []


# --- API ---------------------------------------------------------------------------------------


def test_the_api_pairs_claims_verifies_and_revokes(tmp_path: Path) -> None:
    app = create_api(Settings(data_dir=tmp_path, phone_access_enabled=True))
    with TestClient(app) as client:
        state = client.get("/api/v1/devices").json()
        assert state["enabled"] is True and state["pairing"] is None and state["devices"] == []

        created = client.post(
            "/api/v1/devices/pairing", headers={"X-Helios-Local-Action": "device-pair"}
        ).json()
        code = created["pairing"]["code"]
        urls = created["pairing"]["urls"]
        assert all(url.endswith(f"/__helios/pair?code={code}") for url in urls)

        bad = client.post(
            "/api/v1/devices/claim",
            json={"code": "NOPE1234", "name": "x"},
            headers={"X-Helios-Local-Action": "device-claim"},
        )
        assert bad.status_code == 400
        claimed = client.post(
            "/api/v1/devices/claim",
            json={"code": code, "name": "iPhone"},
            headers={"X-Helios-Local-Action": "device-claim"},
        ).json()
        verified = client.post(
            "/api/v1/devices/verify",
            json={"token": claimed["token"]},
            headers={"X-Helios-Local-Action": "device-verify"},
        )
        assert verified.status_code == 200 and verified.json()["name"] == "iPhone"

        revoked = client.delete(
            f"/api/v1/devices/{claimed['id']}",
            headers={"X-Helios-Local-Action": "device-revoke"},
        )
        assert revoked.status_code == 204
        again = client.post(
            "/api/v1/devices/verify",
            json={"token": claimed["token"]},
            headers={"X-Helios-Local-Action": "device-verify"},
        )
        assert again.status_code == 401
