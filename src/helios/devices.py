"""Pairing phones with Helios, for the phone-access gateway (see ``helios.gateway``).

The computer shows a one-time code (and a QR code carrying it). A phone that presents the code
within ``PAIRING_MINUTES`` becomes a paired device and receives a long random token, kept in
its cookie; only the token's SHA-256 is stored here. Every request through the gateway presents
the token, and a revoked or unknown token is refused.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import secrets
import shutil
import socket
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .models import DevicePairing, PairedDevice
from .rate_limit import Clock, SystemClock

PAIRING_MINUTES = 10
#: Unambiguous characters only: no 0/O or 1/I/L to misread off a screen.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8
#: ``last_seen_at`` is refreshed at most this often, not on every request.
SEEN_RESOLUTION = timedelta(minutes=5)
MAX_NAME_LENGTH = 120


@dataclass(frozen=True)
class Pairing:
    code: str
    expires_at: datetime


@dataclass(frozen=True)
class DeviceInfo:
    id: int
    name: str
    created_at: datetime
    last_seen_at: datetime | None


@dataclass(frozen=True)
class ClaimedDevice:
    id: int
    token: str


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalise_code(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch.isalnum())


def pick_addresses(primary: str | None, others: list[str]) -> list[str]:
    """The address a phone on the same Wi-Fi can use, then any Tailscale address.

    Only the interface the computer routes through counts as "the Wi-Fi": Windows also has
    virtual adapters (WSL, Hyper-V, VPNs) with private addresses no phone can reach.
    """

    addresses: list[str] = []
    for raw in [primary, *others]:
        if raw is None or raw in addresses:
            continue
        try:
            address = ipaddress.IPv4Address(raw)
        except ValueError:
            continue
        if address.is_loopback:
            continue
        if (raw == primary and address.is_private) or is_tailscale(raw):
            addresses.append(raw)
    return addresses


def lan_addresses() -> list[str]:
    """This computer's address on its network, and its Tailscale address if it has one."""

    primary: str | None = None
    try:
        # A UDP "connection" sends nothing; it only asks the OS which interface would route out.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            primary = str(probe.getsockname()[0])
    except OSError:
        pass
    others: list[str] = []
    try:
        others = [
            str(info[4][0])
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
        ]
    except OSError:
        pass
    return pick_addresses(primary, others)


def is_tailscale(address: str) -> bool:
    try:
        return ipaddress.IPv4Address(address) in ipaddress.IPv4Network("100.64.0.0/10")
    except ValueError:
        return False


class DeviceService:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], clock: Clock | None = None
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock or SystemClock()

    async def create_pairing(self) -> Pairing:
        """A fresh code; any earlier unused one stops working."""

        now = self._clock.utcnow()
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        expires = now + timedelta(minutes=PAIRING_MINUTES)
        async with self._session_factory() as session, session.begin():
            await session.execute(
                update(DevicePairing).where(DevicePairing.used_at.is_(None)).values(expires_at=now)
            )
            session.add(DevicePairing(code=code, created_at=now, expires_at=expires))
        return Pairing(code=code, expires_at=expires)

    async def active_pairing(self) -> Pairing | None:
        now = self._clock.utcnow()
        async with self._session_factory() as session:
            row = await session.scalar(
                select(DevicePairing)
                .where(DevicePairing.used_at.is_(None), DevicePairing.expires_at > now)
                .order_by(DevicePairing.created_at.desc())
                .limit(1)
            )
        return None if row is None else Pairing(code=row.code, expires_at=row.expires_at)

    async def claim(self, code: str, name: str) -> ClaimedDevice | None:
        """Exchange a valid, unused, unexpired code for a device token. One use per code."""

        now = self._clock.utcnow()
        wanted = normalise_code(code)
        if len(wanted) != CODE_LENGTH:
            return None
        token = secrets.token_urlsafe(32)
        async with self._session_factory() as session, session.begin():
            pairing = await session.get(DevicePairing, wanted)
            if pairing is None or pairing.used_at is not None or pairing.expires_at <= now:
                return None
            pairing.used_at = now
            device = PairedDevice(
                name=(name.strip() or "Phone")[:MAX_NAME_LENGTH],
                token_hash=token_hash(token),
                created_at=now,
                last_seen_at=now,
            )
            session.add(device)
            await session.flush()
            device_id = device.id
        return ClaimedDevice(id=device_id, token=token)

    async def verify(self, token: str) -> DeviceInfo | None:
        if not token:
            return None
        now = self._clock.utcnow()
        async with self._session_factory() as session, session.begin():
            device = await session.scalar(
                select(PairedDevice).where(PairedDevice.token_hash == token_hash(token))
            )
            if device is None or device.revoked_at is not None:
                return None
            if device.last_seen_at is None or now - device.last_seen_at >= SEEN_RESOLUTION:
                device.last_seen_at = now
            return DeviceInfo(
                id=device.id,
                name=device.name,
                created_at=device.created_at,
                last_seen_at=device.last_seen_at,
            )

    async def devices(self) -> list[DeviceInfo]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(PairedDevice)
                .where(PairedDevice.revoked_at.is_(None))
                .order_by(PairedDevice.created_at)
            )
            return [
                DeviceInfo(
                    id=row.id,
                    name=row.name,
                    created_at=row.created_at,
                    last_seen_at=row.last_seen_at,
                )
                for row in rows
            ]

    async def revoke(self, device_id: int) -> bool:
        now = self._clock.utcnow()
        async with self._session_factory() as session, session.begin():
            device = await session.get(PairedDevice, device_id)
            if device is None or device.revoked_at is not None:
                return False
            device.revoked_at = now
            return True


__all__ = [
    "ClaimedDevice",
    "DeviceInfo",
    "DeviceService",
    "Pairing",
    "is_tailscale",
    "lan_addresses",
]


# --- Tailscale: phone access away from home -----------------------------------------------------

TAILSCALE_PATHS = (
    r"C:\Program Files\Tailscale\tailscale.exe",
    "/usr/bin/tailscale",
    "/usr/local/bin/tailscale",
    "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
)


@dataclass(frozen=True)
class TailscaleStatus:
    installed: bool
    connected: bool
    #: This computer's name on the tailnet, e.g. "helios-pc.tail1234.ts.net".
    dns_name: str | None
    ip: str | None
    #: The HTTPS address when ``tailscale serve`` forwards to the phone gateway.
    serve_url: str | None


def _tailscale_binary() -> str | None:
    found = shutil.which("tailscale")
    if found:
        return found
    return next((path for path in TAILSCALE_PATHS if os.path.isfile(path)), None)


def _run_json(command: list[str]) -> object | None:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            timeout=4,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    try:
        parsed: object = json.loads(completed.stdout or b"null")
    except ValueError:
        return None
    return parsed


def serve_url_for(serve: object, port: int) -> str | None:
    """The https:// address ``tailscale serve`` publishes for the gateway's port, if any."""

    if not isinstance(serve, dict):
        return None
    web = serve.get("Web")
    if not isinstance(web, dict):
        return None
    for host_port, config in web.items():
        handlers = config.get("Handlers") if isinstance(config, dict) else None
        if not isinstance(handlers, dict):
            continue
        for handler in handlers.values():
            proxy = handler.get("Proxy") if isinstance(handler, dict) else None
            if isinstance(proxy, str) and proxy.rstrip("/").endswith(f":{port}"):
                host, _, listen = str(host_port).rpartition(":")
                return f"https://{host}" if listen in {"443", ""} else f"https://{host}:{listen}"
    return None


def tailscale_status(port: int) -> TailscaleStatus:
    binary = _tailscale_binary()
    if binary is None:
        return TailscaleStatus(False, False, None, None, None)
    status = _run_json([binary, "status", "--json"])
    if not isinstance(status, dict):
        return TailscaleStatus(True, False, None, None, None)
    connected = status.get("BackendState") == "Running"
    this = status.get("Self") if isinstance(status.get("Self"), dict) else {}
    assert isinstance(this, dict)
    dns = str(this.get("DNSName") or "").rstrip(".") or None
    ips = [ip for ip in this.get("TailscaleIPs") or [] if isinstance(ip, str) and ":" not in ip]
    serve = _run_json([binary, "serve", "status", "--json"]) if connected else None
    return TailscaleStatus(
        installed=True,
        connected=connected,
        dns_name=dns,
        ip=ips[0] if ips else None,
        serve_url=serve_url_for(serve, port),
    )
