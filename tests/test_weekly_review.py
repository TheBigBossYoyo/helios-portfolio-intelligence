"""The weekly AI review: what the model is given, how runs are stored, and when it runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from helios.ai import AiBrief, AiClientResult
from helios.config import Settings
from helios.db import migrate_database
from helios.models import CardBudget, NewsItem, Notification, T212ExportRow
from helios.news import RankedNewsItem
from helios.periods import HoldingMovement, PeriodSummary
from helios.portfolio_repository import PortfolioRepository
from helios.rate_limit import Clock
from helios.weekly_review import BRIEF, WeeklyReviewService, build_weekly_payload

D = Decimal
NOW = datetime(2026, 9, 27, 18, 0, tzinfo=UTC)  # a Sunday


class FixedClock(Clock):
    def now(self) -> float:
        return NOW.timestamp()

    def utcnow(self) -> datetime:
        return NOW

    async def sleep(self, seconds: float) -> None:
        return None


def _movement(ticker: str, result: str) -> HoldingMovement:
    return HoldingMovement(
        ticker=ticker,
        status="ok",
        start_value_eur=D("100"),
        end_value_eur=D("100") + D(result),
        start_quantity=D("1"),
        end_quantity=D("1"),
        bought_eur=D("0"),
        sold_eur=D("0"),
        dividends_eur=D("0"),
        result_eur=D(result),
        return_pct=float(D(result) / 100),
        price_change_pct=float(D(result) / 100),
        name=ticker.split("_")[0],
    )


def _week() -> PeriodSummary:
    zero = D("0")
    return PeriodSummary(
        key="1W",
        label="1 week",
        status="ok",
        start_date=date(2026, 9, 20),
        end_date=date(2026, 9, 27),
        start_value_eur=D("1376"),
        end_value_eur=D("1355"),
        value_change_eur=D("-21"),
        deposits_eur=D("100"),
        withdrawals_eur=D("-121.38"),
        net_deposits_eur=D("-21.38"),
        investment_result_eur=D("0.38"),
        market_eur=D("0.38"),
        dividends_eur=zero,
        interest_eur=zero,
        fees_eur=zero,
        card_spending_eur=D("-121.38"),
        cashback_eur=zero,
        twr=0.0002,
        unvalued_days=0,
        detail=None,
        holdings=[_movement("ACHV_US_EQ", "-14.84"), _movement("MU_US_EQ", "14.49")],
    )


@dataclass
class FakeReport:
    period_summaries: list[PeriodSummary]


@dataclass
class FakeReports:
    async def get_report(self) -> FakeReport:
        return FakeReport([_week()])


def _story(ticker: str, headline: str, published: datetime) -> RankedNewsItem:
    item = NewsItem(
        dedupe_key=headline,
        feed_key="f",
        provider="rss",
        source_label="Example",
        t212_ticker=ticker,
        headline=headline,
        url="https://example.com",
        canonical_url="https://example.com",
        title_key=headline.lower(),
        published_at=published,
        fetched_at=published,
    )
    return RankedNewsItem(item=item, relevance="headline", matched_term="x", held=True)


class FakeNews:
    async def list_ranked_news(self, **_: object) -> list[RankedNewsItem]:
        return [
            _story("MU_US_EQ", "Micron beats estimates", datetime(2026, 9, 25, tzinfo=UTC)),
            _story("MU_US_EQ", "Old Micron story", datetime(2026, 9, 1, tzinfo=UTC)),
        ]


def _card(row_id: str, ts: datetime, total: str, merchant: str, category: str) -> T212ExportRow:
    return T212ExportRow(
        row_id=row_id,
        action="Card debit",
        ts=ts,
        total=D(total),
        currency="EUR",
        merchant_name=merchant,
        merchant_category=category,
        export_id=1,
    )


def test_payload_tells_the_week_and_nothing_else() -> None:
    payload = build_weekly_payload(
        FakeReport([_week()]),  # type: ignore[arg-type]
        [
            _story("MU_US_EQ", "Micron beats estimates", datetime(2026, 9, 25, tzinfo=UTC)),
            _story("MU_US_EQ", "Old Micron story", datetime(2026, 9, 1, tzinfo=UTC)),
        ],
        [
            _card("a", datetime(2026, 9, 26, tzinfo=UTC), "-60", "Gym", "MEMBERSHIPS"),
            _card("b", datetime(2026, 9, 16, tzinfo=UTC), "-20", "Cafe", "MISCELLANEOUS"),
        ],
        [CardBudget(category="MEMBERSHIPS", monthly_limit=D("50"), updated_at=NOW)],
        [
            Notification(
                kind="alert", title="MU above 1,000", body="b", created_at=NOW, dedupe_key="k"
            )
        ],
        now=NOW,
    )

    assert payload["week"]["investment_result_eur"] == "0.38"
    assert payload["week"]["money_moved_eur"]["of_which_card_spending"] == "-121.38"
    movers = payload["movers_this_week"]
    assert [mover["t212_ticker"] for mover in movers] == ["ACHV_US_EQ", "MU_US_EQ"]
    # Only stories published in the week travel with the mover.
    assert movers[1]["stories_same_week"] == [
        {"published": "2026-09-25", "source": "Example", "headline": "Micron beats estimates"}
    ]
    card = payload["card_spending"]
    assert (card["last_7_days_eur"], card["previous_7_days_eur"]) == ("60.00", "20.00")
    assert card["budgets_this_month"] == [
        {"category": "MEMBERSHIPS", "limit_eur": "50.00", "spent_eur": "60.00"}
    ]
    assert payload["alerts_fired_this_week"][0]["title"] == "MU above 1,000"


def test_the_brief_forbids_advice_and_causal_claims() -> None:
    assert "Do NOT give advice" in BRIEF.system
    assert "never claim the story caused the move" in BRIEF.system
    item_fields = BRIEF.schema["properties"]["observations"]["items"]["properties"]
    assert set(item_fields) == {
        "category",
        "t212_ticker",
        "headline",
        "detail",
        "evidence",
        "severity",
    }


@dataclass
class FakeClient:
    briefs: list[AiBrief | None] = field(default_factory=list)

    async def analyse(
        self, payload: dict[str, Any], *, brief: AiBrief | None = None
    ) -> AiClientResult:
        self.briefs.append(brief)
        return AiClientResult(
            status="ok",
            model="claude-test",
            served_by_model=None,
            parsed={
                "summary": "A flat week.",
                "observations": [
                    {
                        "category": "movers",
                        "t212_ticker": "ACHV_US_EQ",
                        "headline": "ACHV fell",
                        "detail": "It fell.",
                        "evidence": "result_eur -14.84",
                        "severity": "notable",
                    },
                    {
                        "category": "movers",
                        "t212_ticker": None,
                        "headline": "No evidence",
                        "detail": "Dropped.",
                        "evidence": "",
                        "severity": "info",
                    },
                ],
                "unavailable_metrics": [],
            },
            raw_response={"content": []},
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=0,
        )


async def _service(tmp_path: Path, **settings: Any) -> tuple[WeeklyReviewService, FakeClient]:
    config = Settings(data_dir=tmp_path, sqlite_filename="weekly.sqlite3", **settings)
    await migrate_database(config)
    engine = create_async_engine(config.sqlite_url, poolclass=NullPool)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)
    client = FakeClient()
    service = WeeklyReviewService(
        PortfolioRepository(factory),
        config,
        FakeReports(),  # type: ignore[arg-type]
        FakeNews(),
        client,
        FixedClock(),
    )
    return service, client


@pytest.mark.asyncio
async def test_generate_uses_the_weekly_brief_and_keeps_only_cited_observations(
    tmp_path: Path,
) -> None:
    service, client = await _service(tmp_path)

    review = await service.generate()

    assert client.briefs == [BRIEF]
    assert review.summary == "A flat week."
    assert [item.headline for item in review.observations] == ["ACHV fell"]


@pytest.mark.asyncio
async def test_the_schedule_runs_once_on_its_day_and_only_when_switched_on(tmp_path: Path) -> None:
    off, off_client = await _service(tmp_path / "off")
    on, on_client = await _service(
        tmp_path / "on", weekly_review_enabled=True, anthropic_api_key="sk-test"
    )
    sunday_evening = datetime(2026, 9, 27, 19, 30).astimezone()
    sunday_morning = datetime(2026, 9, 27, 9, 0).astimezone()
    monday_evening = datetime(2026, 9, 28, 19, 30).astimezone()

    assert await off.maybe_run_scheduled(now_local=sunday_evening) is None
    assert await on.maybe_run_scheduled(now_local=sunday_morning) is None
    assert await on.maybe_run_scheduled(now_local=monday_evening) is None
    first = await on.maybe_run_scheduled(now_local=sunday_evening)
    again = await on.maybe_run_scheduled(now_local=sunday_evening)

    assert off_client.briefs == []
    assert first is not None and again is None
    assert len(on_client.briefs) == 1
