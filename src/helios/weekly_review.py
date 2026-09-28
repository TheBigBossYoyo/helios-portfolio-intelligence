"""The weekly review: Claude's plain-language account of the past seven days.

Same contract as the on-demand analysis (``ai.py``): the model sees only figures Helios
computed and headlines from the configured feeds, every observation must cite a figure, the
output schema has nowhere to put a rating, target or buy/sell call, and the exact request and
response are stored before anything is parsed.

What it adds is the week's story: the result and what moved it stock by stock, stories that
named those holdings in the same week, card spending against the week before and this month's
budgets, and the alerts that fired. Headlines are shown as coverage in the same week -- the
model is told not to claim a story caused a move.

It runs when asked (a button, the API) or, only if the owner switches it on, once a week on a
schedule; each run costs a few cents on the Anthropic account.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

from .ai import (
    DISCLOSURE,
    PROVIDER,
    SEVERITIES,
    AiAnalysis,
    AiBrief,
    AiClient,
    AiUnavailableError,
    parse_observations,
)
from .card_history import card_label
from .config import Settings
from .models import AiObservation, AiRun, Notification
from .news import RankedNewsItem
from .performance import NoPerformanceDataError, PerformanceReport
from .periods import PeriodSummary
from .portfolio_repository import PortfolioRepository
from .rate_limit import Clock, SystemClock

KIND = "weekly"
CATEGORIES = ("result", "movers", "news", "spending", "alerts", "data_quality")
STORIES_PER_HOLDING = 3
TOP_MOVERS = 6

SYSTEM_PROMPT = """\
You are writing a short weekly review of a personal portfolio for its owner.

You will be given a JSON document: the past week's figures Helios computed from the owner's own
brokerage and card history, and headlines from sources the owner configured. Describe what
happened in the week.

Hard rules:
- Use ONLY the supplied figures. Never introduce a number, price or fact that is not in the
  input. Every observation must quote the figure it rests on in `evidence`.
- Keep money moved (deposits, withdrawals, card spending) apart from the investment result:
  never describe a deposit as a gain or card spending as a loss.
- Headlines are coverage, not causes. You may say a story naming a holding was published in the
  same week as its move; never claim the story caused the move or predict an effect.
- Do NOT give advice. No buy/sell/hold, no price targets, no forecasts, no suggestions to
  rebalance, trade, spend or save differently, and no opinion on whether a holding is good.
- Budgets are the owner's own limits: state where spending stands against them, factually.
- Metrics whose status is not "ok" are unknown: say so, never estimate them.

Order the observations as a reader would want the week told: the result, what moved it, the
news that week, spending, then alerts."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "Two or three sentences: the week in plain words.",
        },
        "observations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": list(CATEGORIES)},
                    "t212_ticker": {"type": ["string", "null"]},
                    "headline": {"type": "string"},
                    "detail": {"type": "string"},
                    "evidence": {"type": "string"},
                    "severity": {"type": "string", "enum": list(SEVERITIES)},
                },
                "required": [
                    "category",
                    "t212_ticker",
                    "headline",
                    "detail",
                    "evidence",
                    "severity",
                ],
                "additionalProperties": False,
            },
        },
        "unavailable_metrics": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "observations", "unavailable_metrics"],
    "additionalProperties": False,
}

BRIEF = AiBrief(
    system=SYSTEM_PROMPT,
    schema=SCHEMA,
    instruction="Write this week's review. Respond only with the JSON schema.",
)


class ReportSource(Protocol):
    async def get_report(self) -> PerformanceReport: ...


class NewsSource(Protocol):
    async def list_ranked_news(
        self,
        *,
        t212_ticker: str | None = None,
        isin: str | None = None,
        limit: int = 50,
        held_only: bool = False,
        mentions_only: bool = False,
    ) -> list[RankedNewsItem]: ...


def _money(value: Decimal | None) -> str | None:
    return None if value is None else f"{value:.2f}"


def _period(summary: PeriodSummary | None) -> dict[str, Any] | None:
    if summary is None:
        return None
    return {
        "status": summary.status,
        "from": summary.start_date.isoformat() if summary.start_date else None,
        "to": summary.end_date.isoformat() if summary.end_date else None,
        "value_start_eur": _money(summary.start_value_eur),
        "value_end_eur": _money(summary.end_value_eur),
        "investment_result_eur": _money(summary.investment_result_eur),
        "time_weighted_return": summary.twr,
        "breakdown_eur": {
            "market_movement": _money(summary.market_eur),
            "dividends": _money(summary.dividends_eur),
            "interest": _money(summary.interest_eur),
            "card_cashback": _money(summary.cashback_eur),
            "fees": _money(summary.fees_eur),
        },
        "money_moved_eur": {
            "deposits": _money(summary.deposits_eur),
            "withdrawals_total": _money(summary.withdrawals_eur),
            "of_which_card_spending": _money(summary.card_spending_eur),
        },
    }


def build_weekly_payload(
    report: PerformanceReport,
    news: Sequence[RankedNewsItem],
    card_rows: Sequence[Any],
    budgets: Sequence[Any],
    fired_alerts: Sequence[Notification],
    *,
    now: datetime,
) -> dict[str, Any]:
    """Everything the model may say about the week, and nothing else."""

    week = next((item for item in report.period_summaries if item.key == "1W"), None)
    month = next((item for item in report.period_summaries if item.key == "1M"), None)
    movers = sorted(
        (item for item in (week.holdings if week else []) if item.result_eur is not None),
        key=lambda item: abs(item.result_eur or Decimal(0)),
        reverse=True,
    )[:TOP_MOVERS]
    week_start = now - timedelta(days=7)
    stories: dict[str, list[dict[str, str]]] = defaultdict(list)
    for entry in news:
        ticker = entry.item.t212_ticker
        published = entry.item.published_at
        if ticker is None or published is None or published < week_start:
            continue
        if len(stories[ticker]) < STORIES_PER_HOLDING:
            stories[ticker].append(
                {
                    "published": published.date().isoformat(),
                    "source": entry.item.source_label,
                    "headline": entry.item.headline,
                }
            )

    def spent_between(start: datetime, end: datetime) -> tuple[Decimal, dict[str, Decimal]]:
        total = Decimal(0)
        merchants: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
        for row in card_rows:
            if card_label(row.action) != "card" or row.total is None:
                continue
            if start <= row.ts < end:
                total -= row.total
                merchants[row.merchant_name or "Unknown"] -= row.total
        return total, merchants

    this_week, merchants = spent_between(week_start, now)
    last_week, _ = spent_between(week_start - timedelta(days=7), week_start)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    category_month: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    for row in card_rows:
        if card_label(row.action) == "card" and row.total is not None and row.ts >= month_start:
            category_month[row.merchant_category or "UNCATEGORISED"] -= row.total

    return {
        "as_of": now.date().isoformat(),
        "week": _period(week),
        "month_so_far_context": _period(month),
        "movers_this_week": [
            {
                "t212_ticker": item.ticker,
                "name": item.name,
                "result_eur": _money(item.result_eur),
                "return": item.return_pct,
                "price_change_eur_terms": item.price_change_pct,
                "bought_eur": _money(item.bought_eur),
                "sold_eur": _money(item.sold_eur),
                "stories_same_week": stories.get(item.ticker, []),
            }
            for item in movers
        ],
        "card_spending": {
            "last_7_days_eur": _money(this_week),
            "previous_7_days_eur": _money(last_week),
            "top_merchants_last_7_days": [
                {"merchant": name, "spent_eur": _money(value)}
                for name, value in sorted(merchants.items(), key=lambda pair: -pair[1])[:5]
            ],
            "budgets_this_month": [
                {
                    "category": budget.category,
                    "limit_eur": _money(budget.monthly_limit),
                    "spent_eur": _money(category_month.get(budget.category, Decimal(0))),
                }
                for budget in budgets
            ],
        },
        "alerts_fired_this_week": [
            {"title": item.title, "body": item.body, "when": item.created_at.isoformat()}
            for item in fired_alerts
            if item.created_at >= week_start
        ],
    }


class WeeklyReviewService:
    def __init__(
        self,
        repository: PortfolioRepository,
        settings: Settings,
        report_source: ReportSource,
        news: NewsSource,
        client: AiClient,
        clock: Clock | None = None,
    ) -> None:
        self._repository = repository
        self._settings = settings
        self._reports = report_source
        self._news = news
        self._client = client
        self._clock = clock or SystemClock()

    async def generate(self) -> AiAnalysis:
        now = self._clock.utcnow()
        try:
            report = await self._reports.get_report()
        except NoPerformanceDataError as error:
            raise AiUnavailableError(
                "No replayed history to review. Run a performance replay first."
            ) from error
        payload = build_weekly_payload(
            report,
            await self._news.list_ranked_news(limit=200, held_only=True, mentions_only=True),
            await self._repository.list_export_rows(),
            await self._repository.list_card_budgets(),
            [
                item
                for item in await self._repository.list_notifications(limit=50)
                if item.kind == "alert"
            ],
            now=now,
        )
        result = await self._client.analyse(payload, brief=BRIEF)
        run_id = await self._repository.insert_ai_run(
            AiRun(
                ts=now,
                kind=KIND,
                provider=PROVIDER,
                model=result.model,
                effort=self._settings.anthropic_effort,
                status=result.status,
                detail=result.detail,
                prompt_json=payload,
                response_json=result.raw_response or None,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                cache_read_tokens=result.cache_read_tokens,
                served_by_model=result.served_by_model,
            )
        )
        observations = (
            parse_observations(result.parsed, categories=CATEGORIES)
            if result.status == "ok" and result.parsed is not None
            else []
        )
        await self._repository.insert_ai_observations(
            [
                AiObservation(
                    run_id=run_id,
                    rank=view.rank,
                    category=view.category,
                    t212_ticker=view.t212_ticker,
                    headline=view.headline,
                    detail=view.detail,
                    evidence=view.evidence,
                    severity=view.severity,
                )
                for view in observations
            ]
        )
        parsed = result.parsed or {}
        summary = parsed.get("summary")
        unavailable = parsed.get("unavailable_metrics")
        return AiAnalysis(
            status=result.status,
            as_of=now,
            model=result.model,
            served_by_model=result.served_by_model,
            effort=self._settings.anthropic_effort,
            summary=summary if isinstance(summary, str) else None,
            observations=observations,
            unavailable_metrics=[m for m in unavailable or [] if isinstance(m, str)],
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cache_read_tokens=result.cache_read_tokens,
            disclosure=DISCLOSURE,
            detail=result.detail,
        )

    async def maybe_run_scheduled(self, *, now_local: datetime | None = None) -> AiAnalysis | None:
        """Once a week, on the chosen day after the chosen time -- only if switched on."""

        if not self._settings.weekly_review_enabled or self._settings.anthropic_api_key is None:
            return None
        local = now_local or self._clock.utcnow().astimezone()
        if local.weekday() != self._settings.weekly_review_weekday:
            return None
        if local.time() < self._settings.weekly_review_at:
            return None
        key = f"weekly:{local.date().isoformat()}"
        if await self._repository.has_notification(key):
            return None
        review = await self.generate()
        if review.status == "ok":
            await self._repository.add_notification(
                Notification(
                    kind="weekly_review",
                    title="Your weekly review is ready",
                    body=review.summary or "Open Insights to read it.",
                    url="/insights#weekly",
                    created_at=self._clock.utcnow(),
                    dedupe_key=key,
                )
            )
        return review
