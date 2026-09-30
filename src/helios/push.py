"""Web Push: Helios's notifications on a paired phone, even with the app closed.

A phone subscribes from the installed app (Settings on the phone). Its browser hands back an
endpoint at its push service (Apple, Google, Mozilla) and two keys. To notify it, Helios
encrypts the message for that phone alone (RFC 8291, "aes128gcm") and posts it to the endpoint,
signed with Helios's own VAPID key (RFC 8292) so the push service knows who is sending. The
push service can neither read the message nor send one as Helios.

The VAPID private key is created on first use and kept in the OS keyring (a file in the data
folder only when no keyring exists). Its ``sub`` contact is the project page, not your email:
nothing personal is sent to Apple or Google.

Push needs a secure page, so it works through the Tailscale HTTPS address (or localhost), not
the plain http:// Wi-Fi address. On an iPhone the app must be on the home screen (iOS 16.4+).
"""

from __future__ import annotations

import base64
import json
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import keyring
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .config import Settings
from .logging import get_logger
from .models import Notification, PushSubscription
from .rate_limit import Clock, SystemClock
from .secrets import KEYRING_SERVICE

logger = get_logger(__name__)

VAPID_KEYRING_USER = "push_vapid_private_key"
VAPID_FILE = "push-vapid.pem"
#: Identifies the sender to push services. A public page, deliberately not an email address.
VAPID_SUBJECT = "https://github.com/TheBigBossYoyo/helios-portfolio-intelligence"
RECORD_SIZE = 4096
TTL_SECONDS = 12 * 3600
#: Notifications older than this are not pushed late: stale news is noise.
PUSH_WINDOW = timedelta(hours=24)
#: A subscription failing this many times in a row is dropped.
MAX_FAILURES = 5


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def _public_bytes(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )


def encrypt(
    payload: bytes,
    p256dh: str,
    auth: str,
    *,
    sender: ec.EllipticCurvePrivateKey | None = None,
    salt: bytes | None = None,
) -> bytes:
    """The request body for one subscription: RFC 8188 header + a single aes128gcm record."""

    receiver_bytes = b64url_decode(p256dh)
    receiver = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), receiver_bytes)
    sender = sender or ec.generate_private_key(ec.SECP256R1())
    sender_bytes = _public_bytes(sender.public_key())
    salt = salt or os.urandom(16)
    shared = sender.exchange(ec.ECDH(), receiver)
    ikm = _hkdf(
        b64url_decode(auth), shared, b"WebPush: info\x00" + receiver_bytes + sender_bytes, 32
    )
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    record = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    header = salt + RECORD_SIZE.to_bytes(4, "big") + bytes([len(sender_bytes)]) + sender_bytes
    return header + record


def decrypt(body: bytes, receiver: ec.EllipticCurvePrivateKey, auth: str) -> bytes:
    """The reverse of :func:`encrypt`, as a phone's browser does it (used by the tests)."""

    salt, id_length = body[:16], body[20]
    sender_bytes = body[21 : 21 + id_length]
    sender = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), sender_bytes)
    receiver_bytes = _public_bytes(receiver.public_key())
    shared = receiver.exchange(ec.ECDH(), sender)
    ikm = _hkdf(
        b64url_decode(auth), shared, b"WebPush: info\x00" + receiver_bytes + sender_bytes, 32
    )
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    plain = AESGCM(cek).decrypt(nonce, body[21 + id_length :], None)
    return plain.rstrip(b"\x00")[:-1]


def vapid_header(
    key: ec.EllipticCurvePrivateKey, endpoint: str, *, now: float | None = None
) -> str:
    """``Authorization: vapid t=<JWT>, k=<public key>`` for this endpoint's push service."""

    parts = urlsplit(endpoint)
    claims = {
        "aud": f"{parts.scheme}://{parts.netloc}",
        "exp": int((now or time.time()) + TTL_SECONDS),
        "sub": VAPID_SUBJECT,
    }
    header = b64url(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    body = b64url(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{header}.{body}".encode()
    r, s = decode_dss_signature(key.sign(signing_input, ec.ECDSA(hashes.SHA256())))
    signature = b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    public = b64url(_public_bytes(key.public_key()))
    return f"vapid t={header}.{body}.{signature}, k={public}"


class VapidKeys:
    """Helios's push-sender key: in the keyring when there is one, else a file beside the data."""

    def __init__(self, data_dir: Path) -> None:
        self._file = data_dir / VAPID_FILE
        self._key: ec.EllipticCurvePrivateKey | None = None

    def _load_pem(self) -> str | None:
        try:
            stored = keyring.get_password(KEYRING_SERVICE, VAPID_KEYRING_USER)
            if stored:
                return stored
        except Exception:
            pass
        if self._file.is_file():
            return self._file.read_text(encoding="ascii")
        return None

    def _store_pem(self, pem: str) -> None:
        try:
            keyring.set_password(KEYRING_SERVICE, VAPID_KEYRING_USER, pem)
            if keyring.get_password(KEYRING_SERVICE, VAPID_KEYRING_USER) == pem:
                return
        except Exception:
            pass
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._file.write_text(pem, encoding="ascii")
        try:
            self._file.chmod(0o600)
        except OSError:
            pass

    @property
    def key(self) -> ec.EllipticCurvePrivateKey:
        if self._key is None:
            pem = self._load_pem()
            if pem is None:
                created = ec.generate_private_key(ec.SECP256R1())
                pem = created.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                ).decode("ascii")
                self._store_pem(pem)
            loaded = serialization.load_pem_private_key(pem.encode("ascii"), password=None)
            if not isinstance(loaded, ec.EllipticCurvePrivateKey):
                raise ValueError("stored push key is not an EC key")
            self._key = loaded
        return self._key

    @property
    def public_key(self) -> str:
        """The ``applicationServerKey`` a phone subscribes with."""

        return b64url(_public_bytes(self.key.public_key()))


@dataclass(frozen=True)
class DeliveryResult:
    sent: int
    failed: int
    removed: int


class PushService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        clock: Clock | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._keys = VapidKeys(settings.data_dir)
        self._clock = clock or SystemClock()
        self._client = client

    @property
    def public_key(self) -> str:
        return self._keys.public_key

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=15.0)
        return self._client

    async def subscribe(self, *, endpoint: str, p256dh: str, auth: str, label: str | None) -> int:
        if not endpoint.startswith("https://"):
            raise ValueError("a push endpoint must be https")
        b64url_decode(p256dh)
        b64url_decode(auth)
        async with self._session_factory() as session, session.begin():
            existing = await session.scalar(
                select(PushSubscription).where(PushSubscription.endpoint == endpoint)
            )
            if existing is not None:
                existing.p256dh, existing.auth, existing.label = p256dh, auth, label
                existing.failures = 0
                await session.flush()
                return existing.id
            row = PushSubscription(
                endpoint=endpoint,
                p256dh=p256dh,
                auth=auth,
                label=label,
                created_at=self._clock.utcnow(),
                failures=0,
            )
            session.add(row)
            await session.flush()
            return row.id

    async def unsubscribe(self, endpoint: str) -> bool:
        async with self._session_factory() as session, session.begin():
            result = await session.execute(
                delete(PushSubscription).where(PushSubscription.endpoint == endpoint)
            )
            return bool(getattr(result, "rowcount", 0))

    async def subscriptions(self) -> list[PushSubscription]:
        async with self._session_factory() as session:
            return list(await session.scalars(select(PushSubscription)))

    async def count(self) -> int:
        async with self._session_factory() as session:
            return len(list(await session.scalars(select(PushSubscription.id))))

    async def send(
        self, message: dict[str, object], subscriptions: Sequence[PushSubscription] | None = None
    ) -> DeliveryResult:
        """Encrypt and post one message to each subscription; drop the ones that are gone."""

        if subscriptions is None:
            async with self._session_factory() as session:
                subscriptions = list(await session.scalars(select(PushSubscription)))
        payload = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        sent = failed = removed = 0
        now = self._clock.utcnow()
        outcomes: dict[int, str] = {}
        for subscription in subscriptions:
            try:
                response = await self._http().post(
                    subscription.endpoint,
                    content=encrypt(payload, subscription.p256dh, subscription.auth),
                    headers={
                        "Content-Encoding": "aes128gcm",
                        "Content-Type": "application/octet-stream",
                        "TTL": str(TTL_SECONDS),
                        "Urgency": "normal",
                        "Authorization": vapid_header(self._keys.key, subscription.endpoint),
                    },
                )
                status = response.status_code
            except httpx.HTTPError as exc:
                logger.warning("push_failed", error=exc.__class__.__name__)
                status = 0
            if 200 <= status < 300:
                outcomes[subscription.id] = "ok"
                sent += 1
            elif status in {404, 410}:
                outcomes[subscription.id] = "gone"
                removed += 1
            else:
                outcomes[subscription.id] = "failed"
                failed += 1
        async with self._session_factory() as session, session.begin():
            for subscription_id, outcome in outcomes.items():
                row = await session.get(PushSubscription, subscription_id)
                if row is None:
                    continue
                if outcome == "gone":
                    await session.delete(row)
                elif outcome == "ok":
                    row.last_success_at, row.failures = now, 0
                else:
                    row.failures += 1
                    if row.failures >= MAX_FAILURES:
                        await session.delete(row)
        return DeliveryResult(sent, failed, removed)

    async def deliver_pending(self) -> DeliveryResult:
        """Push every recent notification not yet pushed, once, to every subscribed phone."""

        now = self._clock.utcnow()
        async with self._session_factory() as session:
            subscriptions = list(await session.scalars(select(PushSubscription)))
            pending = list(
                await session.scalars(
                    select(Notification)
                    .where(Notification.pushed_at.is_(None))
                    .order_by(Notification.created_at)
                )
            )
        if not pending:
            return DeliveryResult(0, 0, 0)
        totals = DeliveryResult(0, 0, 0)
        if subscriptions:
            for notification in pending:
                if now - notification.created_at > PUSH_WINDOW:
                    continue
                result = await self.send(
                    {
                        "title": notification.title,
                        "body": notification.body,
                        "url": notification.url or "/",
                        "tag": notification.dedupe_key,
                    },
                    subscriptions,
                )
                totals = DeliveryResult(
                    totals.sent + result.sent,
                    totals.failed + result.failed,
                    totals.removed + result.removed,
                )
        # Marked either way: with no phone subscribed there is nobody to catch up later.
        async with self._session_factory() as session, session.begin():
            for notification in pending:
                row = await session.get(Notification, notification.id)
                if row is not None:
                    row.pushed_at = now
        return totals


__all__ = ["PushService", "VapidKeys", "decrypt", "encrypt", "vapid_header"]
