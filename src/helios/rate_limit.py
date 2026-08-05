from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> float: ...

    def utcnow(self) -> datetime: ...

    async def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def now(self) -> float:
        return asyncio.get_running_loop().time()

    def utcnow(self) -> datetime:
        return datetime.now(UTC)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


@dataclass(frozen=True)
class RateLimitPolicy:
    capacity: int
    period_seconds: float


@dataclass
class TokenBucket:
    policy: RateLimitPolicy
    clock: Clock
    tokens: float = field(init=False)
    updated_at: float = field(init=False)
    blocked_until: float = 0.0

    def __post_init__(self) -> None:
        self.tokens = float(self.policy.capacity)
        self.updated_at = self.clock.now()

    async def acquire(self) -> None:
        while True:
            self._refill()
            now = self.clock.now()
            if now < self.blocked_until:
                await self.clock.sleep(self.blocked_until - now)
                continue
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                self.updated_at = self.clock.now()
                return
            await self.clock.sleep(self._seconds_until_next_token())

    def apply_headers(self, headers: dict[str, str], status_code: int) -> None:
        remaining_value = headers.get("x-ratelimit-remaining")
        reset_value = headers.get("x-ratelimit-reset")
        if remaining_value is not None:
            try:
                self.tokens = min(float(remaining_value), float(self.policy.capacity))
            except ValueError:
                pass
        if reset_value is not None and (status_code == 429 or remaining_value == "0"):
            try:
                reset_at = float(reset_value)
            except ValueError:
                return
            delay = max(reset_at - self.clock.utcnow().timestamp(), 0.0)
            self.blocked_until = max(self.blocked_until, self.clock.now() + delay)

    def _refill(self) -> None:
        now = self.clock.now()
        elapsed = now - self.updated_at
        if elapsed <= 0:
            return
        refill_rate = self.policy.capacity / self.policy.period_seconds
        self.tokens = min(float(self.policy.capacity), self.tokens + (elapsed * refill_rate))
        self.updated_at = now

    def _seconds_until_next_token(self) -> float:
        refill_rate = self.policy.capacity / self.policy.period_seconds
        return max((1.0 - self.tokens) / refill_rate, 0.0)


class EndpointLimiter:
    def __init__(self, clock: Clock, policies: dict[str, RateLimitPolicy]) -> None:
        self._clock = clock
        self._buckets = {
            key: TokenBucket(policy=policy, clock=clock) for key, policy in policies.items()
        }

    async def acquire(self, endpoint_key: str) -> None:
        await self._bucket(endpoint_key).acquire()

    def observe(self, endpoint_key: str, headers: dict[str, str], status_code: int) -> None:
        self._bucket(endpoint_key).apply_headers(headers, status_code)

    def refund(self, endpoint_key: str) -> None:
        bucket = self._bucket(endpoint_key)
        bucket.tokens = min(bucket.tokens + 1.0, float(bucket.policy.capacity))

    def _bucket(self, endpoint_key: str) -> TokenBucket:
        return self._buckets[endpoint_key]


def retry_delay_from_headers(headers: dict[str, str], clock: Clock) -> float | None:
    retry_after = headers.get("retry-after")
    if retry_after is not None:
        try:
            return max(float(retry_after), 0.0)
        except ValueError:
            try:
                retry_datetime = parsedate_to_datetime(retry_after)
            except (TypeError, ValueError):
                retry_datetime = None
            if retry_datetime is not None:
                return max((retry_datetime - clock.utcnow()).total_seconds(), 0.0)
    reset_value = headers.get("x-ratelimit-reset")
    if reset_value is None:
        return None
    try:
        reset_at = float(reset_value)
    except ValueError:
        return None
    return max(reset_at - clock.utcnow().timestamp(), 0.0)


def default_rate_limit_policies() -> dict[str, RateLimitPolicy]:
    return {
        "summary": RateLimitPolicy(capacity=1, period_seconds=5.0),
        "positions": RateLimitPolicy(capacity=1, period_seconds=1.0),
        "instruments": RateLimitPolicy(capacity=1, period_seconds=50.0),
        "exchanges": RateLimitPolicy(capacity=1, period_seconds=30.0),
        "history_orders": RateLimitPolicy(capacity=6, period_seconds=60.0),
        "history_dividends": RateLimitPolicy(capacity=6, period_seconds=60.0),
        "history_transactions": RateLimitPolicy(capacity=6, period_seconds=60.0),
        "exports": RateLimitPolicy(capacity=1, period_seconds=60.0),
        "default": RateLimitPolicy(capacity=1, period_seconds=1.0),
    }


def endpoint_policy_key(path: str) -> str:
    if path == "/equity/account/summary":
        return "summary"
    if path == "/equity/positions":
        return "positions"
    if path.startswith("/equity/metadata/instruments"):
        return "instruments"
    if path.startswith("/equity/metadata/exchanges"):
        return "exchanges"
    if path.startswith("/equity/history/exports"):
        return "exports"
    if path.startswith("/equity/history/orders"):
        return "history_orders"
    if path.startswith("/equity/history/dividends"):
        return "history_dividends"
    if path.startswith("/equity/history/transactions"):
        return "history_transactions"
    return "default"
