"""The phone-access gateway: the one Helios process a phone can reach.

Everything else in Helios listens on loopback. When phone access is switched on in Settings,
the desktop launcher runs this gateway on ``0.0.0.0:<phone_access_port>``; otherwise it binds
loopback and only says that phone access is off.

Every request must carry a paired device's token (an HttpOnly cookie set at pairing). The
gateway checks it with the API, then forwards the request to the dashboard, adding
``X-Helios-Remote: 1`` so the dashboard knows a phone is asking and keeps settings changes to
the computer. The API itself is never exposed.

Pairing: the computer shows a code (and a QR code for ``/__helios/pair?code=...``); the phone
submits it here, the API exchanges it for a token, and the gateway stores that in the cookie.
Wrong codes are rate limited per client address.
"""

from __future__ import annotations

import html
import os
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from http.cookies import SimpleCookie
from urllib.parse import parse_qs

import httpx
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from starlette.routing import Route

from .config import Settings, load_settings

COOKIE = "helios_device"
COOKIE_MAX_AGE = 400 * 24 * 3600  # the longest browsers keep a cookie
LOCAL_ACTION_HEADER = "X-Helios-Local-Action"
REMOTE_HEADER = "x-helios-remote"
VERIFY_TTL_SECONDS = 60.0
REJECT_TTL_SECONDS = 10.0
FAILED_CLAIMS_LIMIT = 10
FAILED_CLAIMS_WINDOW_SECONDS = 600.0
#: Files a home-screen icon needs before the phone is paired; they reveal nothing.
PUBLIC_PATHS = frozenset(
    {
        "/manifest.webmanifest",
        "/icon.svg",
        "/apple-icon.png",
        "/icon-192.png",
        "/icon-512.png",
        "/icon-maskable-512.png",
        "/favicon.ico",
    }
)
HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "host",
    }
)
#: Response headers the gateway's own server sets (or recomputes while streaming).
RESPONSE_OWN_HEADERS = frozenset({"content-length", "date", "server"})
#: Headers a phone must never be able to set for the dashboard.
STRIPPED_REQUEST_HEADERS = frozenset({REMOTE_HEADER, LOCAL_ACTION_HEADER.lower(), "cookie"})

Verifier = Callable[[str], Awaitable[bool]]
Claimer = Callable[[str, str], Awaitable[str | None]]


@dataclass
class _Cached:
    ok: bool
    until: float


class ApiDeviceClient:
    """Asks the loopback API about tokens and codes; remembers answers briefly."""

    def __init__(self, api_url: str, client: httpx.AsyncClient | None = None) -> None:
        self._api_url = api_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=10.0)
        self._cache: dict[str, _Cached] = {}

    async def verify(self, token: str) -> bool:
        now = time.monotonic()
        cached = self._cache.get(token)
        if cached is not None and cached.until > now:
            return cached.ok
        try:
            response = await self._client.post(
                f"{self._api_url}/api/v1/devices/verify",
                json={"token": token},
                headers={LOCAL_ACTION_HEADER: "device-verify"},
            )
        except httpx.HTTPError:
            return False  # API restarting: refuse now, ask again next request
        ok = response.status_code == 200
        self._cache[token] = _Cached(ok, now + (VERIFY_TTL_SECONDS if ok else REJECT_TTL_SECONDS))
        if len(self._cache) > 256:
            self._cache = {key: value for key, value in self._cache.items() if value.until > now}
        return ok

    async def claim(self, code: str, name: str) -> str | None:
        try:
            response = await self._client.post(
                f"{self._api_url}/api/v1/devices/claim",
                json={"code": code, "name": name},
                headers={LOCAL_ACTION_HEADER: "device-claim"},
            )
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        token = response.json().get("token")
        return token if isinstance(token, str) else None


def device_name(user_agent: str) -> str:
    agent = user_agent.lower()
    if "iphone" in agent:
        return "iPhone"
    if "ipad" in agent:
        return "iPad"
    if "android" in agent:
        return "Android phone" if "mobile" in agent else "Android tablet"
    if "windows" in agent:
        return "Windows computer"
    if "mac os" in agent:
        return "Mac"
    return "Phone"


class FailedClaims:
    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def blocked(self, client: str) -> bool:
        events = self._events[client]
        cutoff = time.monotonic() - FAILED_CLAIMS_WINDOW_SECONDS
        while events and events[0] < cutoff:
            events.popleft()
        return len(events) >= FAILED_CLAIMS_LIMIT

    def record(self, client: str) -> None:
        self._events[client].append(time.monotonic())


# --- pages -------------------------------------------------------------------------------------

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#ffffff" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#141820" media="(prefers-color-scheme: dark)">
<link rel="apple-touch-icon" href="/apple-icon.png">
<link rel="manifest" href="/manifest.webmanifest">
<title>Helios</title>
<style>
:root{{--bg:#f5f6f8;--surface:#fff;--border:#e6e8ec;--ink:#101828;--ink3:#667085;--accent:#4f46e5;
--neg:#b42318;--negsoft:#fef3f2;--oncolor:#fff;color-scheme:light dark}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0b0e14;--surface:#141820;--border:#252b37;
--ink:#f2f4f7;--ink3:#939cad;--accent:#818cf8;--neg:#f97066;--negsoft:rgba(249,112,102,.12);
--oncolor:#0b0e14}}}}
*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:flex;align-items:center;
justify-content:center;background:var(--bg);color:var(--ink);padding:24px;
font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,sans-serif}}
main{{width:100%;max-width:380px;background:var(--surface);border:1px solid var(--border);
border-radius:24px;padding:28px 24px;box-shadow:0 12px 32px -8px rgba(16,24,40,.18)}}
h1{{font-size:22px;margin:16px 0 6px;letter-spacing:-.01em}}
p{{margin:0 0 14px;color:var(--ink3);font-size:15px}}
label{{display:block;font-size:14px;font-weight:600;margin:18px 0 8px}}
input{{width:100%;font:600 22px/1 ui-monospace,Menlo,Consolas,monospace;letter-spacing:.18em;
text-transform:uppercase;text-align:center;padding:14px;border-radius:14px;
border:1px solid var(--border);
background:var(--bg);color:var(--ink)}}
button{{width:100%;margin-top:14px;padding:14px;border:0;border-radius:14px;background:var(--accent);
color:var(--oncolor);font-family:inherit;font-size:16px;font-weight:600}}
.err{{background:var(--negsoft);color:var(--neg);padding:10px 12px;border-radius:12px;
font-size:14px}}
ol{{color:var(--ink3);font-size:14px;padding-left:20px;margin:0}}
</style></head><body><main>
<svg width="44" height="44" viewBox="0 0 32 32" aria-hidden="true">
<rect width="32" height="32" rx="9" fill="#101828"/>
<circle cx="16" cy="16" r="6" fill="#f59e0b"/>
<g stroke="#f59e0b" stroke-linecap="round" stroke-width="2">
<path d="M16 4.5v3M16 24.5v3M4.5 16h3M24.5 16h3M7.9 7.9l2.1 2.1M22 22l2.1 2.1
M24.1 7.9 22 10M10 22l-2.1 2.1"/></g></svg>
{body}
</main></body></html>"""


def _page(body: str, status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(
        _PAGE.format(body=body),
        status_code=status_code,
        headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY"},
    )


def pair_form(code: str = "", error: str | None = None, status_code: int = 200) -> HTMLResponse:
    notice = f'<p class="err">{html.escape(error)}</p>' if error else ""
    return _page(
        f"""<h1>Pair this phone</h1>
<p>Helios on your computer shows a code under <b>Settings &rarr; Phone</b>.</p>
<ol><li>Press <b>Pair a phone</b> there.</li><li>Scan the QR code, or type the code below.</li></ol>
{notice}
<form method="post" action="/__helios/pair" autocomplete="off">
<label for="code">Pairing code</label>
<input id="code" name="code" maxlength="12" value="{html.escape(code)}" autocapitalize="characters"
 autocorrect="off" spellcheck="false" inputmode="text" required>
<button type="submit">Pair</button></form>""",
        status_code,
    )


def phone_access_off() -> HTMLResponse:
    return _page(
        """<h1>Phone access is off</h1>
<p>Switch it on in Helios on your computer, under <b>Settings &rarr; Phone</b>, then restart
Helios from there.</p>""",
        503,
    )


# --- the app -----------------------------------------------------------------------------------


def _forward_headers(request: Request, remote_host: str) -> list[tuple[str, str]]:
    headers = [
        (key, value)
        for key, value in request.headers.items()
        if key.lower() not in HOP_BY_HOP and key.lower() not in STRIPPED_REQUEST_HEADERS
    ]
    # The dashboard's own cookies pass through; the device token never leaves the gateway.
    cookie = SimpleCookie()
    cookie.load(request.headers.get("cookie", ""))
    kept = "; ".join(f"{key}={morsel.value}" for key, morsel in cookie.items() if key != COOKIE)
    if kept:
        headers.append(("cookie", kept))
    # Server actions compare Origin with Host, so the phone's own Host is kept.
    headers.append(("host", request.headers.get("host", remote_host)))
    # The body is passed through as the dashboard encodes it, so ask for exactly what the phone
    # accepts (the HTTP client would otherwise add its own gzip preference).
    if "accept-encoding" not in request.headers:
        headers.append(("accept-encoding", "identity"))
    headers.append((REMOTE_HEADER, "1"))
    headers.append(("x-forwarded-for", request.client.host if request.client else ""))
    headers.append(("x-forwarded-proto", request.url.scheme))
    return headers


def create_app(
    *,
    enabled: bool,
    upstream: str,
    verify: Verifier,
    claim: Claimer,
    client: httpx.AsyncClient | None = None,
) -> Starlette:
    upstream = upstream.rstrip("/")
    http = client or httpx.AsyncClient(
        timeout=httpx.Timeout(connect=5.0, read=180.0, write=60.0, pool=5.0),
        follow_redirects=False,
    )
    failures = FailedClaims()

    async def pair(request: Request) -> Response:
        if not enabled:
            return phone_access_off()
        if request.method == "GET":
            # A GET never spends the code (link previews and prefetchers issue GETs).
            return pair_form(request.query_params.get("code", ""))
        who = request.client.host if request.client else "?"
        if failures.blocked(who):
            return pair_form(error="Too many wrong codes. Wait ten minutes.", status_code=429)
        fields = parse_qs((await request.body())[:1024].decode("utf-8", "replace"))
        code = (fields.get("code") or [""])[0]
        token = await claim(code, device_name(request.headers.get("user-agent", "")))
        if token is None:
            failures.record(who)
            return pair_form(
                code,
                "That code is wrong or has expired. Make a new one on the computer.",
                400,
            )
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            COOKIE,
            token,
            max_age=COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https"
            or request.headers.get("x-forwarded-proto") == "https",
            path="/",
        )
        return response

    async def proxy(request: Request) -> Response:
        if not enabled:
            return phone_access_off()
        path = request.url.path
        if path not in PUBLIC_PATHS:
            token = request.cookies.get(COOKIE, "")
            if not token or not await verify(token):
                if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
                    return pair_form(status_code=401)
                return Response("Not paired", status_code=401)
        url = upstream + path + (f"?{request.url.query}" if request.url.query else "")
        body = await request.body()
        upstream_request = http.build_request(
            request.method,
            url,
            headers=_forward_headers(request, request.url.netloc),
            content=body,
        )
        try:
            upstream_response = await http.send(upstream_request, stream=True)
        except httpx.HTTPError:
            return _page("<h1>Helios is starting</h1><p>Try again in a few seconds.</p>", 502)
        headers = {
            key: value
            for key, value in upstream_response.headers.items()
            if key.lower() not in HOP_BY_HOP and key.lower() not in RESPONSE_OWN_HEADERS
        }
        return StreamingResponse(
            upstream_response.aiter_raw(),
            status_code=upstream_response.status_code,
            headers=headers,
            background=BackgroundTask(upstream_response.aclose),
        )

    methods = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    return Starlette(
        routes=[
            Route("/__helios/pair", pair, methods=["GET", "POST"]),
            Route("/{path:path}", proxy, methods=methods),
        ]
    )


def build_from_settings(settings: Settings) -> Starlette:
    api = os.environ.get("HELIOS_API_URL", "http://127.0.0.1:8000")
    web = os.environ.get("HELIOS_WEB_URL", "http://127.0.0.1:3000")
    devices = ApiDeviceClient(api)
    return create_app(
        enabled=settings.phone_access_enabled,
        upstream=web,
        verify=devices.verify,
        claim=devices.claim,
    )


def main() -> None:
    import uvicorn

    settings = load_settings()
    host = "0.0.0.0" if settings.phone_access_enabled else "127.0.0.1"
    uvicorn.run(
        build_from_settings(settings),
        host=host,
        port=settings.phone_access_port,
        log_level="warning",
        proxy_headers=False,
    )


if __name__ == "__main__":
    main()
