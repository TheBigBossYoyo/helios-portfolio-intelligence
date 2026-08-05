from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from helios.rate_limit import RateLimitPolicy, TokenBucket, endpoint_policy_key


@dataclass
class FakeClock:
    current: float = 0.0
    slept: list[float] = field(default_factory=list)

    def now(self) -> float:
        return self.current

    def utcnow(self) -> datetime:
        return datetime(2024, 1, 1, tzinfo=UTC) + timedelta(seconds=self.current)

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.current += seconds


@pytest.mark.asyncio
async def test_token_bucket_waits_for_capacity() -> None:
    clock = FakeClock()
    bucket = TokenBucket(policy=RateLimitPolicy(capacity=1, period_seconds=1.0), clock=clock)

    await bucket.acquire()
    await bucket.acquire()

    assert clock.slept == [1.0]


def test_history_endpoints_have_independent_policy_keys() -> None:
    assert endpoint_policy_key("/equity/history/orders") == "history_orders"
    assert endpoint_policy_key("/equity/history/dividends") == "history_dividends"
    assert endpoint_policy_key("/equity/history/transactions") == "history_transactions"
