from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from helios.ai import (
    CATEGORIES,
    DISCLOSURE,
    OUTPUT_SCHEMA,
    SEVERITIES,
    AiAnalysisService,
    AiClientResult,
    AiUnavailableError,
    NullAiClient,
    build_payload,
)
from helios.config import Settings
from helios.db import migrate_database
from helios.models import AiObservation, AiRun, NewsItem
from helios.news import canonical_url, dedupe_key, title_key
from helios.performance import (
    AttributionReport,
    CorrelationClusterReport,
    MetricValue,
    NoPerformanceDataError,
    PassiveCounterfactualReport,
    PerformanceReport,
    RegressionResult,
)
from helios.portfolio_repository import PortfolioRepository
from helios.rate_limit import Clock

NOW = datetime(2024, 5, 1, 12, 0, tzinfo=UTC)


class FixedClock(Clock):
    def now(self) -> float:
        return NOW.timestamp()

    def utcnow(self) -> datetime:
        return NOW

    async def sleep(self, seconds: float) -> None:
        del seconds


class FakePerformanceService:
    def __init__(self, report: PerformanceReport | None) -> None:
        self._report = report

    async def get_report(self) -> PerformanceReport:
        if self._report is None:
            raise NoPerformanceDataError("no nav")
        return self._report


class FakeAiClient:
    def __init__(self, result: AiClientResult) -> None:
        self.result = result
        self.seen_payload: dict[str, Any] | None = None

    async def analyse(self, payload: dict[str, Any]) -> AiClientResult:
        self.seen_payload = payload
        return self.result


def _metric(value: float | None, status: str | None = None) -> MetricValue:
    resolved = status or ("ok" if value is not None else "insufficient_data")
    return MetricValue(resolved, value, 120 if value is not None else 0, "detail")


def _report() -> PerformanceReport:
    return PerformanceReport(
        as_of=date(2024, 5, 1),
        start_date=date(2024, 1, 1),
        end_date=date(2024, 5, 1),
        flow_timing="flow_at_close",
        annualization_days=365,
        cumulative_twr=_metric(0.0259),
        xirr=_metric(0.0499),
        annualized_return=_metric(0.081),
        volatility=_metric(0.2275),
        downside_volatility=_metric(None),
        sharpe=_metric(0.4595),
        sortino=_metric(None),
        calmar=_metric(1.21),
        max_drawdown=_metric(-0.0672),
        time_underwater_days=_metric(30),
        recovery_days=_metric(22),
        beta_vs_benchmarks=[],
        hhi=_metric(0.52),
        effective_number_of_positions=_metric(1.92),
        top5_weight=_metric(1.0),
        var_95_1d=_metric(0.0),
        cvar_95_1d=_metric(-0.007),
        var_99_1d=_metric(-0.02),
        cvar_99_1d=_metric(-0.03),
        var_95_10d=_metric(-0.02),
        cvar_95_10d=_metric(-0.03),
        var_99_10d=_metric(-0.0465),
        cvar_99_10d=_metric(-0.05),
        ff5_momentum_regression=RegressionResult(
            "unavailable", 0, None, None, {"mkt_rf": None}, "no factor source"
        ),
        nav_series=[],
        daily_twr=[],
        rolling_volatility_30d=[],
        rolling_volatility_90d=[],
        rolling_beta_30d=[],
        rolling_beta_90d=[],
        contributions=[],
        attribution=AttributionReport("unavailable", None, [], "no constituents"),
        correlation_clusters=CorrelationClusterReport(
            "insufficient_data", 0, 1.0, None, [], "not enough history"
        ),
        passive_counterfactual=PassiveCounterfactualReport(
            status="unavailable",
            benchmark_key="vwrp",
            benchmark_label="FTSE All-World ETF proxy",
            invested_eur=None,
            final_value_eur=None,
            actual_nav_eur=None,
            difference_eur=None,
            series=[],
            excluded_flow_count=0,
            detail="no proxy currency",
        ),
        notes=["Cash-flow timing convention: flow_at_close."],
    )


def _ok_result(parsed: dict[str, Any]) -> AiClientResult:
    return AiClientResult(
        status="ok",
        model="claude-opus-5",
        served_by_model=None,
        parsed=parsed,
        raw_response={"content": [{"type": "text", "text": __import__("json").dumps(parsed)}]},
        input_tokens=1200,
        output_tokens=400,
        cache_read_tokens=800,
    )


VALID_PARSED = {
    "summary": "The portfolio is concentrated and moderately volatile.",
    "observations": [
        {
            "category": "concentration",
            "t212_ticker": "AAPL_US_EQ",
            "headline": "One holding dominates",
            "detail": "The top-five weight is the entire portfolio.",
            "evidence": "top5_weight = 1.0",
            "severity": "notable",
        }
    ],
    "unavailable_metrics": ["sortino", "attribution"],
}


# ---------------------------------------------------------------------------
# The output contract is the guardrail
# ---------------------------------------------------------------------------


def test_schema_has_no_field_that_could_hold_advice() -> None:
    """No rating, target or action field exists, so the model cannot emit one."""
    item = OUTPUT_SCHEMA["properties"]["observations"]["items"]
    fields = set(item["properties"])

    assert fields == {"category", "t212_ticker", "headline", "detail", "evidence", "severity"}
    assert not fields & {"action", "rating", "recommendation", "price_target", "forecast"}
    # additionalProperties: False means the model cannot invent one either.
    assert item["additionalProperties"] is False


def test_schema_requires_evidence_on_every_observation() -> None:
    item = OUTPUT_SCHEMA["properties"]["observations"]["items"]

    assert "evidence" in item["required"]


def test_severity_vocabulary_is_descriptive_not_directive() -> None:
    """'elevated' describes a reading; 'sell' or 'buy' would be an instruction."""
    assert set(SEVERITIES) == {"info", "notable", "elevated"}


# ---------------------------------------------------------------------------
# Payload — the only facts the model sees
# ---------------------------------------------------------------------------


def test_payload_carries_metric_status_so_unknown_stays_unknown() -> None:
    payload = build_payload(_report(), [], max_news=10)

    assert payload["risk"]["sortino"] == {
        "status": "insufficient_data",
        "value": None,
        "note": "detail",
    }
    assert payload["risk"]["sharpe"]["status"] == "ok"
    assert payload["risk"]["sharpe"]["value"] == 0.4595


def test_payload_passes_unavailable_sections_through_with_their_reason() -> None:
    payload = build_payload(_report(), [], max_news=10)

    assert payload["attribution"]["status"] == "unavailable"
    assert "constituent" in payload["attribution"]["detail"]
    assert payload["factor_exposure"]["status"] == "unavailable"
    assert payload["passive_counterfactual"]["status"] == "unavailable"


def test_payload_states_the_conventions_behind_the_numbers() -> None:
    payload = build_payload(_report(), [], max_news=10)

    assert payload["conventions"] == {
        "flow_timing": "flow_at_close",
        "annualization_days": 365,
        "base_currency": "EUR",
    }


def test_payload_caps_news_and_carries_no_article_text() -> None:
    news = [_news(f"Headline {index}") for index in range(50)]

    payload = build_payload(_report(), news, max_news=5)

    assert len(payload["recent_news"]) == 5
    assert set(payload["recent_news"][0]) == {"ticker", "source", "headline", "published_at"}


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyse_refuses_without_replayed_analytics(tmp_path: Path) -> None:
    repository, _ = await _repository(tmp_path, "ai_none.sqlite3")
    service = AiAnalysisService(
        repository, Settings(data_dir=tmp_path), FakePerformanceService(None), NullAiClient()
    )

    with pytest.raises(AiUnavailableError, match="performance replay"):
        await service.analyse()


@pytest.mark.asyncio
async def test_analyse_without_a_key_reports_unavailable_rather_than_guessing(
    tmp_path: Path,
) -> None:
    repository, _ = await _repository(tmp_path, "ai_nokey.sqlite3")
    service = AiAnalysisService(
        repository,
        Settings(data_dir=tmp_path),
        FakePerformanceService(_report()),
        NullAiClient(),
        clock=FixedClock(),
    )

    analysis = await service.analyse()

    assert analysis.status == "unavailable"
    assert analysis.observations == []
    assert analysis.detail is not None
    assert "HELIOS_ANTHROPIC_API_KEY" in analysis.detail
    assert analysis.disclosure == DISCLOSURE


@pytest.mark.asyncio
async def test_analyse_stores_the_run_raw_before_parsing(tmp_path: Path) -> None:
    repository, session_factory = await _repository(tmp_path, "ai_raw.sqlite3")
    client = FakeAiClient(_ok_result(VALID_PARSED))
    service = AiAnalysisService(
        repository,
        Settings(data_dir=tmp_path, anthropic_api_key=SecretStr("k")),
        FakePerformanceService(_report()),
        client,
        clock=FixedClock(),
    )

    analysis = await service.analyse()

    async with session_factory() as session:
        runs = list(await session.scalars(select(AiRun)))
        observations = list(await session.scalars(select(AiObservation)))

    assert analysis.status == "ok"
    assert len(runs) == 1
    # The exact prompt is kept, so any published insight traces back to its inputs.
    assert runs[0].prompt_json == client.seen_payload
    assert runs[0].response_json is not None
    assert runs[0].input_tokens == 1200
    assert runs[0].cache_read_tokens == 800
    assert len(observations) == 1
    assert observations[0].evidence == "top5_weight = 1.0"


@pytest.mark.asyncio
async def test_observations_without_evidence_are_dropped(tmp_path: Path) -> None:
    """An uncited claim has no standing — the citation is the whole mechanism."""
    repository, _ = await _repository(tmp_path, "ai_evidence.sqlite3")
    parsed = {
        "summary": "s",
        "observations": [
            {
                "category": "risk",
                "t212_ticker": None,
                "headline": "Vague worry",
                "detail": "Something feels off.",
                "evidence": "   ",
                "severity": "elevated",
            },
            VALID_PARSED["observations"][0],
        ],
        "unavailable_metrics": [],
    }
    service = AiAnalysisService(
        repository,
        Settings(data_dir=tmp_path, anthropic_api_key=SecretStr("k")),
        FakePerformanceService(_report()),
        FakeAiClient(_ok_result(parsed)),
        clock=FixedClock(),
    )

    analysis = await service.analyse()

    assert [item.headline for item in analysis.observations] == ["One holding dominates"]


@pytest.mark.asyncio
async def test_unknown_category_or_severity_falls_back_rather_than_propagating(
    tmp_path: Path,
) -> None:
    repository, _ = await _repository(tmp_path, "ai_enum.sqlite3")
    parsed = {
        "summary": "s",
        "observations": [
            {
                "category": "made_up",
                "t212_ticker": None,
                "headline": "H",
                "detail": "D",
                "evidence": "hhi = 0.52",
                "severity": "catastrophic",
            }
        ],
        "unavailable_metrics": [],
    }
    service = AiAnalysisService(
        repository,
        Settings(data_dir=tmp_path, anthropic_api_key=SecretStr("k")),
        FakePerformanceService(_report()),
        FakeAiClient(_ok_result(parsed)),
        clock=FixedClock(),
    )

    analysis = await service.analyse()

    assert analysis.observations[0].category in CATEGORIES
    assert analysis.observations[0].severity in SEVERITIES


@pytest.mark.asyncio
async def test_a_refusal_is_reported_not_swallowed(tmp_path: Path) -> None:
    repository, session_factory = await _repository(tmp_path, "ai_refusal.sqlite3")
    result = AiClientResult(
        status="refused",
        model="claude-opus-5",
        served_by_model=None,
        parsed=None,
        raw_response={"stop_reason": "refusal"},
        input_tokens=None,
        output_tokens=None,
        cache_read_tokens=None,
        detail="The model declined this request.",
    )
    service = AiAnalysisService(
        repository,
        Settings(data_dir=tmp_path, anthropic_api_key=SecretStr("k")),
        FakePerformanceService(_report()),
        FakeAiClient(result),
        clock=FixedClock(),
    )

    analysis = await service.analyse()

    async with session_factory() as session:
        runs = list(await session.scalars(select(AiRun)))

    assert analysis.status == "refused"
    assert analysis.observations == []
    # The failed attempt is still recorded, so cost and behaviour stay auditable.
    assert runs[0].status == "refused"


@pytest.mark.asyncio
async def test_latest_replays_a_stored_run_without_calling_the_model(tmp_path: Path) -> None:
    repository, _ = await _repository(tmp_path, "ai_latest.sqlite3")
    client = FakeAiClient(_ok_result(VALID_PARSED))
    service = AiAnalysisService(
        repository,
        Settings(data_dir=tmp_path, anthropic_api_key=SecretStr("k")),
        FakePerformanceService(_report()),
        client,
        clock=FixedClock(),
    )
    await service.analyse()
    client.seen_payload = None

    latest = await service.latest()

    assert latest is not None
    assert latest.status == "ok"
    assert latest.summary == VALID_PARSED["summary"]
    assert [item.headline for item in latest.observations] == ["One holding dominates"]
    assert latest.unavailable_metrics == ["sortino", "attribution"]
    # Opening the dashboard must not spend money.
    assert client.seen_payload is None


@pytest.mark.asyncio
async def test_latest_is_none_before_any_run(tmp_path: Path) -> None:
    repository, _ = await _repository(tmp_path, "ai_empty.sqlite3")
    service = AiAnalysisService(
        repository, Settings(data_dir=tmp_path), FakePerformanceService(_report()), NullAiClient()
    )

    assert await service.latest() is None


def _news(headline: str) -> NewsItem:
    url = f"https://example.com/{abs(hash(headline)) % 10**8}"
    return NewsItem(
        dedupe_key=dedupe_key(url, NOW),
        feed_key="f",
        provider="rss",
        source_label="Example",
        t212_ticker="AAPL_US_EQ",
        isin=None,
        headline=headline,
        summary="body text that must never be sent",
        url=url,
        canonical_url=canonical_url(url),
        title_key=title_key(headline),
        published_at=NOW,
        fetched_at=NOW,
        raw_news_id=None,
    )


async def _repository(
    tmp_path: Path, filename: str
) -> tuple[PortfolioRepository, async_sessionmaker[AsyncSession]]:
    settings = Settings(data_dir=tmp_path, sqlite_filename=filename)
    await migrate_database(settings)
    engine = create_async_engine(settings.sqlite_url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return PortfolioRepository(session_factory), session_factory
