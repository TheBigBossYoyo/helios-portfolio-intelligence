"""Request guards for the routes that can change credentials or restart the process.

The existing `X-Helios-Local-Action` header is a local-caller gate: a browser cannot attach a
custom header to a cross-origin request without a CORS preflight Helios never grants, so its
presence means the caller is Helios' own server-side code. That is proportionate for "replay my
NAV". It is not proportionate for "overwrite the credential that reads my brokerage account".

The settings routes therefore stack three further checks, each closing a gap the others leave:

* **Peer address** — the connection must originate from loopback, or from a host the operator
  named in `HELIOS_SETTINGS_TRUSTED_PEERS` (Compose names its own `web` service, whose requests
  arrive over the bridge network). Independent of what address uvicorn was told to bind, so a
  misconfigured `HELIOS_BIND_HOST=0.0.0.0` cannot silently expose credential writes to the
  network.
* **Fetch metadata** — `Sec-Fetch-Site` must be same-origin or absent, and any `Origin` must be a
  loopback origin. This is what stops a page on another site from driving these routes through a
  browser the operator already has open.
* **Rate limit** — a small fixed budget per window. Credential verification calls Trading 212,
  and the restart route bounces the process; neither should be reachable in a tight loop.

None of this is authentication. It is defence in depth for a single-user loopback deployment, and
the README says so plainly. A multi-user Helios needs real auth and CSRF tokens instead.
"""

from __future__ import annotations

import socket
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final
from urllib.parse import urlparse

from fastapi import HTTPException, Request, status

#: Hostnames that count as loopback for an `Origin` header. `localhost` is included because
#: browsers resolve it to loopback and the dashboard is commonly opened that way.
LOOPBACK_HOSTS: Final[frozenset[str]] = frozenset({"127.0.0.1", "::1", "localhost", "[::1]"})

#: `Sec-Fetch-Site` values a legitimate caller can present. Server-side fetches omit the header
#: entirely; a browser on the dashboard's own origin sends `same-origin`. Anything else —
#: `cross-site`, `same-site` — is a request initiated from somewhere Helios does not serve.
ALLOWED_FETCH_SITES: Final[frozenset[str]] = frozenset({"same-origin", "none"})


def is_loopback_host(host: str | None) -> bool:
    """Whether a bare hostname or address is loopback."""

    if not host:
        return False
    return host.strip().lower().strip("[]") in {h.strip("[]") for h in LOOPBACK_HOSTS}


def trusted_peer_addresses(hosts: tuple[str, ...]) -> frozenset[str]:
    """Every address the named hosts currently resolve to.

    Resolved on each call rather than cached: under Compose a recreated container comes back on
    a new bridge address, and a stale cache would lock the dashboard out until the API restarts.
    Settings routes are rare and rate limited, so the lookup cost does not matter. A name that
    does not resolve contributes nothing -- failing closed, never open.
    """

    addresses: set[str] = set()
    for host in hosts:
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError:
            continue
        addresses.update(str(info[4][0]) for info in infos)
    return frozenset(addresses)


def _configured_trusted_peers(request: Request) -> tuple[str, ...]:
    container = getattr(request.app.state, "container", None)
    settings = getattr(container, "settings", None)
    hosts = getattr(settings, "settings_trusted_peer_hosts", ())
    return tuple(hosts)


def require_loopback_client(request: Request) -> None:
    """Reject any request that did not arrive over loopback or from a configured trusted peer.

    This runs as a plain ``def`` so FastAPI executes it in the threadpool: resolving a trusted
    peer is a blocking DNS lookup and must not stall the event loop.
    """

    client = request.client
    if client is not None and is_loopback_host(client.host):
        return
    trusted = _configured_trusted_peers(request)
    if client is None or not trusted or client.host not in trusted_peer_addresses(trusted):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Settings can only be changed from this machine. This request arrived from "
                f"{client.host if client else 'an unknown address'}."
            ),
        )


def require_same_origin(request: Request) -> None:
    """Reject browser-initiated cross-origin requests.

    Checked in addition to the custom action header rather than instead of it: the header proves
    the caller could set arbitrary headers, this proves the request was not smuggled through a
    browser session the operator happens to have open.
    """

    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is not None and fetch_site.lower() not in ALLOWED_FETCH_SITES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Cross-origin request refused (Sec-Fetch-Site: {fetch_site}).",
        )

    origin = request.headers.get("origin")
    if origin is None:
        return
    parsed = urlparse(origin)
    if not is_loopback_host(parsed.hostname):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Refused a request originating from {origin}.",
        )


@dataclass
class RateLimiter:
    """A fixed request budget per sliding window, keyed by name.

    Deliberately in-process and unshared: it protects a single local API process from a runaway
    caller, not a fleet from a botnet. Restarting Helios clears it, which is acceptable because
    the restart itself is the expensive operation being limited.
    """

    limit: int
    window_seconds: float
    _events: dict[str, deque[float]] = field(default_factory=dict)

    def check(self, key: str, *, now: float | None = None) -> None:
        current = time.monotonic() if now is None else now
        bucket = self._events.setdefault(key, deque())
        cutoff = current - self.window_seconds
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= self.limit:
            retry_after = max(1, int(bucket[0] + self.window_seconds - current) + 1)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Too many attempts. {self.limit} allowed per "
                    f"{int(self.window_seconds)}s; try again in {retry_after}s."
                ),
                headers={"Retry-After": str(retry_after)},
            )
        bucket.append(current)

    def reset(self) -> None:
        self._events.clear()


#: Credential writes reach out to Trading 212 to verify, so they are both slow and externally
#: visible. Ten per minute is far above deliberate use and far below anything resembling a probe.
settings_write_limiter: Final = RateLimiter(limit=10, window_seconds=60.0)

#: A restart drops in-flight requests. Three per five minutes is enough to recover from a
#: mistyped setting and not enough to hold the process in a reboot loop.
restart_limiter: Final = RateLimiter(limit=3, window_seconds=300.0)


def rate_limited(limiter: RateLimiter, key: str) -> Callable[[Request], None]:
    """Build a dependency that spends one unit of `limiter` per request."""

    def dependency(request: Request) -> None:
        limiter.check(key)

    return dependency
