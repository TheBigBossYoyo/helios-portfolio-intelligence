"""Web Push: encryption (RFC 8291), VAPID signing (RFC 8292), subscriptions and delivery."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from helios.app import create_app
from helios.config import Settings
from helios.db import migrate_database
from helios.models import Notification
from helios.push import PushService, b64url, b64url_decode, decrypt, encrypt, vapid_header
from helios.rate_limit import Clock

NOW = datetime(2026, 9, 30, 10, 0, tzinfo=UTC)


class FixedClock(Clock):
    def __init__(self, current: datetime) -> None:
        self.current = current

    def now(self) -> float:
        return self.current.timestamp()

    def utcnow(self) -> datetime:
        return self.current

    async def sleep(self, seconds: float) -> None:
        return None


def _key(value: str) -> ec.EllipticCurvePrivateKey:
    return ec.derive_private_key(int.from_bytes(b64url_decode(value), "big"), ec.SECP256R1())


def test_encryption_matches_the_rfc_8291_example() -> None:
    """Appendix A of RFC 8291, byte for byte."""
    receiver = _key("q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94")
    body = encrypt(
        b"When I grow up, I want to be a watermelon",
        "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
        "BTBZMqHH6r4Tts7J_aSIgg",
        sender=_key("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"),
        salt=b64url_decode("DGv6ra1nlYgDCS1FRnbzlw"),
    )
    assert b64url(body) == (
        "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocIn"
        "mYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGN"
        "WQexSgSxsj_Qulcy4a-fN"
    )
    assert decrypt(body, receiver, "BTBZMqHH6r4Tts7J_aSIgg") == (
        b"When I grow up, I want to be a watermelon"
    )


def test_the_vapid_token_is_signed_for_the_push_service_and_names_no_person() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    header = vapid_header(key, "https://web.push.apple.com/QGsd9/abc", now=1_000_000)
    token = header.split("t=")[1].split(",")[0]
    head, claims, signature = token.split(".")
    raw = b64url_decode(signature)
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    key.public_key().verify(der, f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
    payload = json.loads(b64url_decode(claims))
    assert payload["aud"] == "https://web.push.apple.com"
    assert payload["exp"] == 1_000_000 + 12 * 3600
    assert payload["sub"].startswith("https://") and "@" not in payload["sub"]


def _phone() -> tuple[ec.EllipticCurvePrivateKey, str, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return key, b64url(public), b64url(os.urandom(16))


async def _factory(tmp_path: Path) -> async_sessionmaker[AsyncSession]:
    settings = Settings(data_dir=tmp_path, sqlite_filename="push.sqlite3")
    await migrate_database(settings)
    engine = create_async_engine(settings.sqlite_url, poolclass=NullPool)
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_new_notifications_reach_each_phone_once_and_dead_phones_are_dropped(
    tmp_path: Path,
) -> None:
    factory = await _factory(tmp_path)
    delivered: dict[str, list[bytes]] = {}

    def push_service(request: httpx.Request) -> httpx.Response:
        delivered.setdefault(str(request.url), []).append(request.content)
        assert request.headers["content-encoding"] == "aes128gcm"
        assert request.headers["authorization"].startswith("vapid t=")
        return httpx.Response(410 if "gone" in str(request.url) else 201)

    service = PushService(
        factory,
        Settings(data_dir=tmp_path),
        clock=FixedClock(NOW),
        client=httpx.AsyncClient(transport=httpx.MockTransport(push_service)),
    )
    phone, p256dh, auth = _phone()
    await service.subscribe(
        endpoint="https://push.test/phone", p256dh=p256dh, auth=auth, label="iPhone"
    )
    _, other_key, other_auth = _phone()
    await service.subscribe(
        endpoint="https://push.test/gone", p256dh=other_key, auth=other_auth, label=None
    )
    assert await service.count() == 2

    async with factory() as session, session.begin():
        session.add_all(
            [
                Notification(
                    kind="alert",
                    title="NVDA above 200",
                    body="It crossed.",
                    url="/holdings/NVDA_US_EQ",
                    created_at=NOW - timedelta(minutes=5),
                    dedupe_key="a1",
                ),
                Notification(
                    kind="summary",
                    title="Old news",
                    body="Yesterday's.",
                    url="/",
                    created_at=NOW - timedelta(days=2),
                    dedupe_key="old",
                ),
            ]
        )

    first = await service.deliver_pending()
    second = await service.deliver_pending()

    assert (first.sent, first.removed) == (1, 1)
    assert second.sent == 0  # already pushed
    body = delivered["https://push.test/phone"]
    assert len(body) == 1  # the two-day-old one is not pushed late
    message = json.loads(decrypt(body[0], phone, auth))
    assert message == {
        "title": "NVDA above 200",
        "body": "It crossed.",
        "url": "/holdings/NVDA_US_EQ",
        "tag": "a1",
    }
    assert await service.count() == 1  # the gone phone was removed

    with pytest.raises(ValueError):
        await service.subscribe(
            endpoint="http://insecure.test", p256dh=p256dh, auth=auth, label=None
        )
    assert await service.unsubscribe("https://push.test/phone") is True
    assert await service.count() == 0


def test_the_api_serves_the_key_and_manages_subscriptions_and_goals(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path))
    _, p256dh, auth = _phone()
    push = {"X-Helios-Local-Action": "push-subscribe"}
    goals = {"X-Helios-Local-Action": "goals-write"}
    with TestClient(app) as client:
        status = client.get("/api/v1/push").json()
        assert len(b64url_decode(status["publicKey"])) == 65 and status["subscriptions"] == 0
        # The key is created once and kept.
        assert client.get("/api/v1/push").json()["publicKey"] == status["publicKey"]
        subscribed = client.post(
            "/api/v1/push/subscriptions",
            json={"endpoint": "https://push.test/x", "keys": {"p256dh": p256dh, "auth": auth}},
            headers=push,
        )
        assert subscribed.json()["subscriptions"] == 1
        left = client.post(
            "/api/v1/push/unsubscribe", json={"endpoint": "https://push.test/x"}, headers=push
        )
        assert left.json()["subscriptions"] == 0

        created = client.post(
            "/api/v1/goals",
            json={
                "kind": "value",
                "name": "€10k",
                "targetAmount": "10000",
                "targetDate": "2028-12-31",
            },
            headers=goals,
        )
        assert created.status_code == 201
        goal = created.json()
        assert goal["targetAmount"] == "10000" and goal["kind"] == "value"
        bad = client.post(
            "/api/v1/goals",
            json={"kind": "car", "name": "x", "targetAmount": "1", "targetDate": "2028-01-01"},
            headers=goals,
        )
        assert bad.status_code == 422
        assert [row["name"] for row in client.get("/api/v1/goals").json()] == ["€10k"]
        assert client.delete(f"/api/v1/goals/{goal['id']}", headers=goals).status_code == 204
        assert client.get("/api/v1/goals").json() == []
        assert client.get("/api/v1/instruments/NVDA_US_EQ/facts").status_code == 404
        exposure = client.get("/api/v1/exposure").json()
        assert exposure["companies"] == [] and exposure["totalEur"] == 0
