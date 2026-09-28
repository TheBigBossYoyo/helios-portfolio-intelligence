from __future__ import annotations

import zipfile
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import cast

import httpx
import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from helios.config import Settings
from helios.db import migrate_database
from helios.models import (
    DailyHolding,
    DailyNav,
    Dividend,
    FxRateDaily,
    Instrument,
    MarketPriceDaily,
    OrderHistory,
    Transaction,
)
from helios.performance import (
    ANNUALIZATION_DAYS,
    FACTOR_NAMES,
    ONE,
    AlphaVantageMarketDataProvider,
    BenchmarkDefinition,
    CompositeMarketDataProvider,
    DailyPricePoint,
    DailyReturnPoint,
    EcbFxRateProvider,
    FactorObservation,
    FxRatePoint,
    KenFrenchFactorDataProvider,
    MarketDataProvider,
    MarketDataProviderError,
    PerformanceReplayService,
    PriceRequest,
    TwelveDataMarketDataProvider,
    UnknownCurrencyError,
    _alphavantage_symbol,
    _merge_fx_maps,
    _merge_market_price_maps,
    _missing_price_requests,
    _split_yahoo_symbol,
    _twelvedata_symbol_params,
    annualized_return_from_twr,
    brinson_fachler_attribution,
    compute_contributions,
    compute_daily_twr,
    compute_drawdown,
    compute_passive_counterfactual,
    compute_xirr,
    correlation_clusters,
    ff5_momentum_regression,
    historical_var_cvar,
    parse_ken_french_csv,
)
from helios.portfolio_repository import (
    PERFORMANCE_REPLAY_LEASE_ENDPOINT,
    PORTFOLIO_SYNC_LEASE_ENDPOINT,
    PortfolioRepository,
    SyncAlreadyRunningError,
)
from helios.rate_limit import Clock

USD_SETTINGS_OVERRIDES = {"benchmark_vwrp_currency": "USD"}


class FixedClock(Clock):
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> float:
        return self._now.timestamp()

    def utcnow(self) -> datetime:
        return self._now

    async def sleep(self, seconds: float) -> None:
        del seconds


class FakeMarketDataProvider:
    def __init__(self, prices: dict[str, list[DailyPricePoint]]) -> None:
        self.prices = prices
        self.seen_requests: list[PriceRequest] = []

    async def fetch_daily_closes(
        self,
        *,
        requests: Sequence[PriceRequest],
        start_date: date,
        end_date: date,
        skip_notes: dict[str, str] | None = None,
    ) -> dict[str, list[DailyPricePoint]]:
        del start_date, end_date, skip_notes
        self.seen_requests = list(requests)
        return {
            request.key: self.prices[request.key]
            for request in requests
            if request.key in self.prices
        }


class FakeFxRateProvider:
    def __init__(self, rates: dict[str, list[FxRatePoint]]) -> None:
        self.rates = rates
        self.seen_currencies: set[str] = set()

    async def fetch_eur_base_rates(
        self,
        *,
        currencies: set[str],
        start_date: date,
        end_date: date,
    ) -> dict[str, list[FxRatePoint]]:
        del start_date, end_date
        self.seen_currencies = set(currencies)
        return {
            currency: points for currency, points in self.rates.items() if currency in currencies
        }


class FakeFactorDataProvider:
    def __init__(self, rows: list[FactorObservation]) -> None:
        self.rows = rows

    async def fetch_factor_returns(
        self,
        *,
        start_date: date,
        end_date: date,
    ) -> list[FactorObservation]:
        del start_date, end_date
        return self.rows


# ---------------------------------------------------------------------------
# Fix 2 - TWR flow-timing contract
# ---------------------------------------------------------------------------


def _nav(
    day: date, nav: str | None, *, flow: str = "0", internal: str = "0", cash: str = "0"
) -> DailyNav:
    return DailyNav(
        as_of_date=day,
        cash_balance_eur=Decimal(cash),
        securities_value_eur=None if nav is None else Decimal(nav),
        nav_eur=None if nav is None else Decimal(nav),
        external_flow_eur=Decimal(flow),
        internal_cash_flow_eur=Decimal(internal),
        valuation_status="VALUED" if nav is not None else "PARTIAL",
        missing_price_count=0,
        missing_fx_count=0,
    )


@pytest.mark.parametrize("flow_timing", ["flow_at_close", "flow_at_open"])
def test_daily_twr_is_exactly_zero_on_a_deposit_only_day(flow_timing: str) -> None:
    """A day whose only event is an external flow returns 0 under both exact conventions."""
    nav_rows = [
        _nav(date(2024, 1, 1), "100"),
        _nav(date(2024, 1, 2), "210", flow="110"),
    ]

    points = compute_daily_twr(nav_rows, flow_timing=flow_timing)  # type: ignore[arg-type]

    assert [point.value for point in points] == [0.0]


def test_intraday_split_is_the_labelled_modified_dietz_approximation() -> None:
    """Modified Dietz half-weights the flow, so it is *not* flow-neutral - by construction."""
    nav_rows = [
        _nav(date(2024, 1, 1), "100"),
        _nav(date(2024, 1, 2), "210", flow="110"),
    ]

    points = compute_daily_twr(nav_rows, flow_timing="intraday_split")

    # (210 - 110) / (100 + 55) - 1
    assert points[0].value == pytest.approx(100 / 155 - 1)


def test_daily_twr_conventions_differ_on_a_mixed_flow_and_gain_day() -> None:
    """110 -> 220 with a 100 deposit: the flow convention decides the denominator."""
    nav_rows = [
        _nav(date(2024, 1, 1), "110"),
        _nav(date(2024, 1, 2), "220", flow="100"),
    ]

    at_close = compute_daily_twr(nav_rows, flow_timing="flow_at_close")
    at_open = compute_daily_twr(nav_rows, flow_timing="flow_at_open")
    split = compute_daily_twr(nav_rows, flow_timing="intraday_split")

    # (220 - 100) / 110 - 1
    assert round(at_close[0].value, 10) == round(120 / 110 - 1, 10)
    # 220 / (110 + 100) - 1
    assert round(at_open[0].value, 10) == round(220 / 210 - 1, 10)
    # (220 - 100) / (110 + 50) - 1
    assert round(split[0].value, 10) == round(120 / 160 - 1, 10)


def test_daily_twr_default_convention_is_flow_at_close() -> None:
    nav_rows = [
        _nav(date(2024, 1, 1), "110"),
        _nav(date(2024, 1, 2), "220", flow="100"),
    ]

    assert compute_daily_twr(nav_rows) == compute_daily_twr(nav_rows, flow_timing="flow_at_close")


def test_annualized_return_uses_a_calendar_day_basis() -> None:
    points = [DailyReturnPoint(date(2024, 1, day), 0.01) for day in range(1, 4)]

    metric = annualized_return_from_twr(points)

    assert metric.status == "ok"
    assert metric.value is not None
    assert metric.value == pytest.approx((1.01**3) ** (ANNUALIZATION_DAYS / 3) - 1.0)
    assert metric.detail is not None
    assert str(ANNUALIZATION_DAYS) in metric.detail


# ---------------------------------------------------------------------------
# Fix 1 - drawdown recovery is measured against the worst drawdown's peak
# ---------------------------------------------------------------------------


def test_drawdown_recovery_uses_the_worst_drawdown_peak() -> None:
    # peak 100 -> trough 50 -> partial recovery 80 -> lower peak 90 -> full recovery 100
    nav_rows = [
        _nav(date(2024, 1, 1), "100"),
        _nav(date(2024, 1, 2), "50"),
        _nav(date(2024, 1, 3), "80"),
        _nav(date(2024, 1, 4), "90"),
        _nav(date(2024, 1, 5), "100"),
    ]

    drawdown = compute_drawdown(nav_rows)

    assert drawdown is not None
    assert drawdown.peak_date == date(2024, 1, 1)
    assert drawdown.trough_date == date(2024, 1, 2)
    assert round(drawdown.drawdown, 10) == -0.5
    # 80 and 90 are above the trough but below the 100 peak: they are not a recovery.
    assert drawdown.recovery_date == date(2024, 1, 5)
    assert drawdown.recovery_days == 3
    assert drawdown.time_underwater_days == 4


def test_drawdown_without_recovery_reports_none() -> None:
    nav_rows = [
        _nav(date(2024, 1, 1), "100"),
        _nav(date(2024, 1, 2), "50"),
        _nav(date(2024, 1, 3), "60"),
    ]

    drawdown = compute_drawdown(nav_rows)

    assert drawdown is not None
    assert drawdown.recovery_date is None
    assert drawdown.recovery_days is None


# ---------------------------------------------------------------------------
# Fix 5 - cache coverage and merge semantics
# ---------------------------------------------------------------------------


def _price_row(day: date, key: str, close: str, symbol: str = "SYM") -> MarketPriceDaily:
    return MarketPriceDaily(
        price_date=day,
        t212_ticker=key,
        provider_symbol=symbol,
        currency_code="USD",
        close_price=Decimal(close),
        provider="fixture",
        source_date=day,
        provenance="EXACT",
    )


def test_missing_price_requests_uses_observation_coverage_not_calendar_days() -> None:
    """A symbol quoted only on business days must not look "missing" every weekend."""
    request = PriceRequest("ABC", "ABC.US", "USD")
    # Two weeks of window, but the market only produced 6 observations.
    cached = {
        "ABC": [
            _price_row(date(2024, 1, 1) + timedelta(days=offset), "ABC", "100")
            for offset in (0, 1, 2, 5, 8, 12)
        ]
    }

    missing = _missing_price_requests(
        [request],
        cached,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 14),
        max_stale_days=10,
    )

    assert missing == []


def test_missing_price_requests_refetches_when_cache_falls_behind_the_cutoff() -> None:
    request = PriceRequest("ABC", "ABC.US", "USD")
    cached = {"ABC": [_price_row(date(2024, 1, 1), "ABC", "100")]}

    missing = _missing_price_requests(
        [request],
        cached,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 2, 1),
        max_stale_days=10,
    )

    assert missing == [request]


def test_merge_market_prices_keeps_cached_points_and_the_true_provider_symbol() -> None:
    cached = {"ABC": [_price_row(date(2024, 1, 1), "ABC", "100", symbol="ABC.US")]}
    fetched = {
        "ABC": [
            DailyPricePoint(
                date(2024, 1, 2), Decimal("110"), "USD", "fixture", date(2024, 1, 2), "EXACT"
            )
        ]
    }

    merged = _merge_market_price_maps(cached, fetched, [PriceRequest("ABC", "ABC.US", "USD")])

    assert [row.price_date for row in merged["ABC"]] == [date(2024, 1, 1), date(2024, 1, 2)]
    assert {row.provider_symbol for row in merged["ABC"]} == {"ABC.US"}


def test_merge_fx_maps_keeps_cached_points() -> None:
    cached = {
        "USD": [
            FxRateDaily(
                rate_date=date(2024, 1, 1),
                currency_code="USD",
                eur_per_unit=Decimal("0.9"),
                provider="fixture",
                source_date=date(2024, 1, 1),
                provenance="EXACT",
                stale=False,
            )
        ]
    }
    fetched = {
        "USD": [
            FxRatePoint(
                date(2024, 1, 2), "USD", Decimal("0.91"), "ecb", date(2024, 1, 2), "EXACT", False
            )
        ]
    }

    merged = _merge_fx_maps(cached, fetched)

    assert [row.rate_date for row in merged["USD"]] == [date(2024, 1, 1), date(2024, 1, 2)]


# ---------------------------------------------------------------------------
# Fix 8 - Brinson-Fachler
# ---------------------------------------------------------------------------


def test_brinson_includes_benchmark_only_sectors_and_reconciles_to_active_return() -> None:
    portfolio = [("Tech", 0.7, 0.10), ("Health", 0.3, 0.04)]
    benchmark = [("Tech", 0.5, 0.08), ("Health", 0.3, 0.05), ("Energy", 0.2, -0.02)]

    items = brinson_fachler_attribution(portfolio, benchmark)

    assert [item.key for item in items] == ["Energy", "Health", "Tech"]
    energy = next(item for item in items if item.key == "Energy")
    assert energy.portfolio_weight == 0.0
    assert energy.selection_effect == 0.0
    assert energy.interaction_effect == 0.0
    portfolio_return = sum(weight * value for _, weight, value in portfolio)
    benchmark_return = sum(weight * value for _, weight, value in benchmark)
    assert round(sum(item.total_effect for item in items), 10) == round(
        portfolio_return - benchmark_return, 10
    )


# ---------------------------------------------------------------------------
# Fix 7 - contributions never fake a zero return
# ---------------------------------------------------------------------------


def test_contributions_report_insufficient_data_instead_of_a_zero_return() -> None:
    items = compute_contributions({"A": 0.6, "B": 0.4}, {"A": 0.1})

    by_key = {item.key: item for item in items}
    assert by_key["A"].status == "ok"
    assert by_key["A"].contribution == pytest.approx(0.06)
    assert by_key["B"].status == "insufficient_data"
    assert by_key["B"].return_value is None
    assert by_key["B"].contribution is None


# ---------------------------------------------------------------------------
# Fix 12 - VaR/CVaR conventions
# ---------------------------------------------------------------------------


def test_var_reports_the_actual_sample_size_when_insufficient() -> None:
    metrics = historical_var_cvar([0.01, -0.02, 0.005])

    assert metrics["var_95_1d"].status == "insufficient_data"
    assert metrics["var_95_1d"].observations == 3
    assert metrics["var_95_1d"].detail is not None
    assert "have 3" in metrics["var_95_1d"].detail


def test_var_uses_linear_quantiles_and_a_negative_loss_convention() -> None:
    # Returns from -0.20 to +0.19, so the 5% tail is unambiguously a loss.
    returns = [(index - 20) / 100.0 for index in range(40)]
    expected = float(np.quantile(np.array(returns), 0.05, method="linear"))

    metrics = historical_var_cvar(returns)

    var_metric = metrics["var_95_1d"]
    assert var_metric.status == "ok"
    assert var_metric.value == pytest.approx(expected)
    assert var_metric.value is not None
    assert var_metric.value < 0.0
    assert var_metric.observations == 40
    assert var_metric.detail is not None
    assert "linear" in var_metric.detail
    assert "negative return" in var_metric.detail
    assert "flat" in var_metric.detail
    assert metrics["cvar_95_1d"].value is not None
    assert metrics["cvar_95_1d"].value <= var_metric.value
    # Both metrics report the same sample size; the tail count lives in the detail.
    assert metrics["cvar_95_1d"].observations == 40


def test_var_detail_discloses_the_flat_day_share() -> None:
    # Half the observations are flat, exactly as a calendar-day series with weekends behaves.
    returns = [0.0 if index % 2 else (index - 21) / 100.0 for index in range(40)]

    detail = historical_var_cvar(returns)["var_95_1d"].detail

    assert detail is not None
    assert "50% of observations are flat" in detail


@given(
    st.lists(
        st.floats(min_value=-0.2, max_value=0.2, allow_nan=False, allow_infinity=False),
        min_size=10,
        max_size=60,
    )
)
# Generation speed depends on machine load, not on the property under test.
@settings(suppress_health_check=[HealthCheck.too_slow])
def test_historical_var_cvar_tail_is_not_above_var(returns: list[float]) -> None:
    metrics = historical_var_cvar(returns)

    for suffix in ("95_1d", "99_1d", "95_10d", "99_10d"):
        var_value = metrics[f"var_{suffix}"].value
        cvar_value = metrics[f"cvar_{suffix}"].value
        if var_value is None or cvar_value is None:
            continue
        assert cvar_value <= var_value


# ---------------------------------------------------------------------------
# Fix 13 - FF5 + momentum on excess returns
# ---------------------------------------------------------------------------


def _factor_rows(count: int, *, risk_free: str = "0.0001") -> list[FactorObservation]:
    rows: list[FactorObservation] = []
    for index in range(count):
        rows.append(
            FactorObservation(
                as_of_date=date(2024, 1, 1) + timedelta(days=index),
                provider="fixture",
                risk_free_rate=Decimal(risk_free),
                factors={
                    "mkt_rf": Decimal(str(0.001 * (index % 5 - 2))),
                    "smb": Decimal(str(0.0005 * (index % 3 - 1))),
                    "hml": Decimal(str(0.0004 * (index % 4 - 2))),
                    "rmw": Decimal(str(0.0003 * (index % 2))),
                    "cma": Decimal(str(0.0002 * (index % 7 - 3))),
                    "mom": Decimal(str(0.0006 * (index % 6 - 3))),
                },
            )
        )
    return rows


def test_ff5_regression_is_unavailable_without_a_factor_source() -> None:
    result = ff5_momentum_regression([DailyReturnPoint(date(2024, 1, 1), 0.01)], [])

    assert result.status == "unavailable"
    assert result.r_squared is None
    assert all(value is None for value in result.coefficients.values())
    assert result.detail is not None
    assert "fabricate" in result.detail


def test_ff5_regression_runs_on_excess_returns() -> None:
    factor_rows = _factor_rows(20, risk_free="0.0001")
    # Construct returns that are exactly rf + 2 * mkt_rf, so the mkt_rf beta must be 2.
    portfolio = [
        DailyReturnPoint(
            row.as_of_date,
            float(row.risk_free_rate) + 2.0 * float(row.factors["mkt_rf"]),
        )
        for row in factor_rows
    ]

    result = ff5_momentum_regression(portfolio, factor_rows)

    assert result.status == "ok"
    assert result.observations == 20
    assert result.coefficients["mkt_rf"] == pytest.approx(2.0, abs=1e-6)
    assert result.intercept == pytest.approx(0.0, abs=1e-9)


def test_ff5_regression_reports_insufficient_alignment() -> None:
    factor_rows = _factor_rows(20)
    portfolio = [DailyReturnPoint(factor_rows[0].as_of_date, 0.01)]

    result = ff5_momentum_regression(portfolio, factor_rows)

    assert result.status == "insufficient_data"
    assert result.observations == 1


# ---------------------------------------------------------------------------
# Fix 11 - correlation clustering
# ---------------------------------------------------------------------------


def _series(base: list[float], start: date = date(2024, 1, 1)) -> list[tuple[date, float]]:
    return [(start + timedelta(days=index), value) for index, value in enumerate(base)]


def test_correlation_clusters_group_co_moving_holdings() -> None:
    up = [0.01 * ((index % 7) - 3) for index in range(25)]
    down = [-value for value in up]
    almost_up = [value + 0.0001 for value in up]

    report = correlation_clusters(
        {"A": _series(up), "B": _series(almost_up), "C": _series(down)},
        {"A": 0.4, "B": 0.4, "C": 0.2},
        distance_threshold=1.0,
        min_observations=20,
    )

    assert report.status == "ok"
    assert report.observations == 25
    clusters = {item.key: item.cluster for item in report.assignments}
    assert clusters["A"] == clusters["B"]
    assert clusters["C"] != clusters["A"]
    assert report.cluster_count == 2


def test_correlation_clusters_report_insufficient_data() -> None:
    report = correlation_clusters(
        {"A": _series([0.01, 0.02]), "B": _series([0.03, -0.01])},
        {"A": 0.5, "B": 0.5},
        distance_threshold=1.0,
        min_observations=20,
    )

    assert report.status == "insufficient_data"
    assert report.observations == 2
    assert report.assignments == []


# ---------------------------------------------------------------------------
# Fix 10 - you-but-passive counterfactual
# ---------------------------------------------------------------------------


def test_passive_counterfactual_invests_each_contribution_at_its_flow_date() -> None:
    benchmark = BenchmarkDefinition("vwrp", "FTSE All-World ETF proxy", "VWRP.LON", "EUR", "proxy")
    nav_rows = [
        _nav(date(2024, 1, 1), "100", flow="100"),
        _nav(date(2024, 1, 2), "100"),
        _nav(date(2024, 1, 3), "260", flow="100"),
    ]
    price_rows = [
        _price_row(date(2024, 1, 1), "__benchmark_vwrp__", "10"),
        _price_row(date(2024, 1, 2), "__benchmark_vwrp__", "20"),
        _price_row(date(2024, 1, 3), "__benchmark_vwrp__", "25"),
    ]

    report = compute_passive_counterfactual(
        benchmark=benchmark,
        nav_rows=nav_rows,
        price_rows=price_rows,
        fx_rates={},
        max_price_stale_days=10,
        max_fx_stale_days=10,
    )

    assert report.status == "ok"
    # 100 EUR at 10 = 10 units; on day 3 the units are worth 250, plus 100/25 = 4 more units.
    assert report.invested_eur == Decimal("200")
    assert report.final_value_eur == Decimal("350")
    assert report.actual_nav_eur == Decimal("260")
    assert report.difference_eur == Decimal("-90")
    assert [point.as_of_date for point in report.series] == [
        date(2024, 1, 1),
        date(2024, 1, 2),
        date(2024, 1, 3),
    ]
    assert report.detail is not None
    assert "Counterfactual" in report.detail


def test_passive_counterfactual_is_unavailable_without_a_trusted_proxy_currency() -> None:
    benchmark = BenchmarkDefinition("vwrp", "FTSE All-World ETF proxy", "VWRP.LON", None, "proxy")

    report = compute_passive_counterfactual(
        benchmark=benchmark,
        nav_rows=[_nav(date(2024, 1, 1), "100", flow="100")],
        price_rows=[_price_row(date(2024, 1, 1), "__benchmark_vwrp__", "10")],
        fx_rates={},
        max_price_stale_days=10,
        max_fx_stale_days=10,
    )

    assert report.status == "unavailable"
    assert report.final_value_eur is None
    assert report.detail is not None
    assert "refuses to assume" in report.detail


# ---------------------------------------------------------------------------
# XIRR
# ---------------------------------------------------------------------------


def test_xirr_reports_no_sign_change() -> None:
    metric = compute_xirr([(date(2024, 1, 1), Decimal("100")), (date(2024, 1, 2), Decimal("50"))])

    assert metric.status == "no_sign_change"
    assert metric.value is None


def test_xirr_solves_a_simple_doubling_over_one_year() -> None:
    metric = compute_xirr(
        [(date(2024, 1, 1), Decimal("-100")), (date(2024, 12, 31), Decimal("200"))]
    )

    assert metric.status == "ok"
    assert metric.value is not None
    assert metric.value == pytest.approx(1.0, abs=0.02)


# ---------------------------------------------------------------------------
# Replay integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replay_reconstructs_daily_nav_with_forward_filled_fx(tmp_path: Path) -> None:
    repository, session_factory = await _repository_and_session_factory(
        tmp_path, "performance.sqlite3"
    )
    await _seed_single_buy(session_factory)

    market_data = FakeMarketDataProvider(
        {
            "ABC_US_EQ": [
                DailyPricePoint(
                    date(2024, 1, 5), Decimal("100"), "USD", "fixture", date(2024, 1, 5), "EXACT"
                ),
                DailyPricePoint(
                    date(2024, 1, 6), Decimal("110"), "USD", "fixture", date(2024, 1, 6), "EXACT"
                ),
            ]
        }
    )
    service = PerformanceReplayService(
        repository,
        Settings(data_dir=tmp_path),
        market_data,
        FakeFxRateProvider(
            {
                "USD": [
                    FxRatePoint(
                        date(2024, 1, 5),
                        "USD",
                        Decimal("0.9"),
                        "fixture",
                        date(2024, 1, 5),
                        "EXACT",
                        False,
                    )
                ]
            }
        ),
        clock=FixedClock(datetime(2024, 1, 6, tzinfo=UTC)),
    )

    summary = await service.replay(as_of=date(2024, 1, 6))
    nav_rows = await repository.list_daily_nav()
    holdings = await repository.list_daily_holdings()

    assert summary.nav_written == 2
    assert summary.flow_timing == "flow_at_close"
    assert nav_rows[-1].nav_eur == Decimal("218")
    # The FX fix was carried forward one day, so the day is valued but flagged FORWARD_FILL.
    assert nav_rows[-1].valuation_status == "FORWARD_FILL"
    carried = next(item for item in holdings if item.as_of_date == date(2024, 1, 6))
    assert carried.valuation_status == "FORWARD_FILL"
    assert carried.fx_provenance == "FORWARD_FILL"
    assert carried.price_provenance == "EXACT"
    # The instrument currency is what drives the request, never a hardcoded USD default.
    assert {
        request.currency_code for request in market_data.seen_requests if request.key == "ABC_US_EQ"
    } == {"USD"}


@pytest.mark.asyncio
async def test_replay_carries_prices_over_a_weekend_gap(tmp_path: Path) -> None:
    repository, session_factory = await _repository_and_session_factory(tmp_path, "weekend.sqlite3")
    # 2024-01-05 is a Friday; 6th/7th are the weekend.
    await _seed_single_buy(session_factory)

    service = PerformanceReplayService(
        repository,
        Settings(data_dir=tmp_path, analytics_max_price_stale_days=3),
        FakeMarketDataProvider(
            {
                "ABC_US_EQ": [
                    DailyPricePoint(
                        date(2024, 1, 5),
                        Decimal("100"),
                        "USD",
                        "fixture",
                        date(2024, 1, 5),
                        "EXACT",
                    )
                ]
            }
        ),
        FakeFxRateProvider(
            {
                "USD": [
                    FxRatePoint(
                        date(2024, 1, 5) + timedelta(days=offset),
                        "USD",
                        Decimal("0.9"),
                        "fixture",
                        date(2024, 1, 5) + timedelta(days=offset),
                        "EXACT",
                        False,
                    )
                    for offset in range(4)
                ]
            }
        ),
        clock=FixedClock(datetime(2024, 1, 8, tzinfo=UTC)),
    )

    summary = await service.replay(as_of=date(2024, 1, 8))
    holdings = {item.as_of_date: item for item in await repository.list_daily_holdings()}

    assert summary.stale_price_symbols == []
    for weekend_day in (date(2024, 1, 6), date(2024, 1, 7)):
        assert holdings[weekend_day].valuation_status == "FORWARD_FILL"
        assert holdings[weekend_day].price_provenance == "FORWARD_FILL"
        assert holdings[weekend_day].close_price == Decimal("100")


@pytest.mark.asyncio
async def test_replay_marks_stale_price_past_the_cutoff(tmp_path: Path) -> None:
    repository, session_factory = await _repository_and_session_factory(tmp_path, "stale.sqlite3")
    await _seed_single_buy(session_factory)

    service = PerformanceReplayService(
        repository,
        Settings(data_dir=tmp_path, analytics_max_price_stale_days=2),
        FakeMarketDataProvider(
            {
                "ABC_US_EQ": [
                    DailyPricePoint(
                        date(2024, 1, 5),
                        Decimal("100"),
                        "USD",
                        "fixture",
                        date(2024, 1, 5),
                        "EXACT",
                    )
                ]
            }
        ),
        FakeFxRateProvider(
            {
                "USD": [
                    FxRatePoint(
                        date(2024, 1, 5) + timedelta(days=offset),
                        "USD",
                        Decimal("0.9"),
                        "fixture",
                        date(2024, 1, 5) + timedelta(days=offset),
                        "EXACT",
                        False,
                    )
                    for offset in range(4)
                ]
            }
        ),
        clock=FixedClock(datetime(2024, 1, 8, tzinfo=UTC)),
    )

    await service.replay(as_of=date(2024, 1, 8))
    holdings = {item.as_of_date: item for item in await repository.list_daily_holdings()}
    nav_rows = {item.as_of_date: item for item in await repository.list_daily_nav()}

    # Day 7 is exactly at the cutoff, day 8 is one day past it.
    assert holdings[date(2024, 1, 7)].valuation_status == "FORWARD_FILL"
    assert holdings[date(2024, 1, 8)].valuation_status == "STALE_PRICE"
    assert holdings[date(2024, 1, 8)].market_value_eur is None
    assert nav_rows[date(2024, 1, 8)].nav_eur is None
    assert nav_rows[date(2024, 1, 8)].valuation_status == "PARTIAL"


@pytest.mark.asyncio
async def test_replay_marks_partial_nav_when_price_missing(tmp_path: Path) -> None:
    repository, session_factory = await _repository_and_session_factory(
        tmp_path, "missing_price.sqlite3"
    )
    async with session_factory() as session:
        async with session.begin():
            session.add(
                Instrument(
                    t212_ticker="ABC_US_EQ",
                    yahoo_ticker="ABC",
                    currency_code="USD",
                    mapping_status="resolved",
                )
            )
            session.add(
                OrderHistory(
                    fill_id="buy-1",
                    fill_timestamp=datetime(2024, 1, 5, tzinfo=UTC),
                    t212_ticker="ABC_US_EQ",
                    side="BUY",
                    fill_type="TRADE",
                    filled_quantity=Decimal("1"),
                    wallet_currency="EUR",
                    wallet_net_value=Decimal("90"),
                )
            )
            session.add(
                Transaction(
                    reference="dep-1",
                    ts=datetime(2024, 1, 5, tzinfo=UTC),
                    transaction_type="DEPOSIT",
                    currency_code="EUR",
                    amount=Decimal("100"),
                )
            )

    service = PerformanceReplayService(
        repository,
        Settings(data_dir=tmp_path),
        FakeMarketDataProvider({}),
        FakeFxRateProvider(
            {
                "USD": [
                    FxRatePoint(
                        date(2024, 1, 5),
                        "USD",
                        Decimal("0.9"),
                        "fixture",
                        date(2024, 1, 5),
                        "EXACT",
                        False,
                    )
                ]
            }
        ),
        clock=FixedClock(datetime(2024, 1, 5, tzinfo=UTC)),
    )

    summary = await service.replay(as_of=date(2024, 1, 5))
    nav_rows = await repository.list_daily_nav()

    assert summary.missing_price_symbols == ["ABC_US_EQ"]
    assert nav_rows[0].valuation_status == "PARTIAL"
    assert nav_rows[0].nav_eur is None


@pytest.mark.asyncio
async def test_replay_surfaces_a_provider_skip_reason_in_its_notes(tmp_path: Path) -> None:
    """A symbol a provider could not price still needs its *reason* somewhere the operator sees.

    ``missing_price_symbols`` already says *which* holding stayed unpriced; this checks the
    provider's own explanation (bad plan, unknown symbol, ...) reaches the replay's notes rather
    than being discarded once the fetch returns.
    """
    repository, session_factory = await _repository_and_session_factory(
        tmp_path, "skip_reason.sqlite3"
    )
    async with session_factory() as session:
        async with session.begin():
            session.add(
                Instrument(
                    t212_ticker="VUAGl_EQ",
                    yahoo_ticker="VUAG.L",
                    currency_code="GBP",
                    mapping_status="resolved",
                )
            )
            session.add(
                OrderHistory(
                    fill_id="buy-1",
                    fill_timestamp=datetime(2024, 1, 5, tzinfo=UTC),
                    t212_ticker="VUAGl_EQ",
                    side="BUY",
                    fill_type="TRADE",
                    filled_quantity=Decimal("1"),
                    wallet_currency="EUR",
                    wallet_net_value=Decimal("90"),
                )
            )
            session.add(
                Transaction(
                    reference="dep-1",
                    ts=datetime(2024, 1, 5, tzinfo=UTC),
                    transaction_type="DEPOSIT",
                    currency_code="EUR",
                    amount=Decimal("100"),
                )
            )

    class SkippingProvider:
        async def fetch_daily_closes(
            self,
            *,
            requests: Sequence[PriceRequest],
            start_date: date,
            end_date: date,
            skip_notes: dict[str, str] | None = None,
        ) -> dict[str, list[DailyPricePoint]]:
            del start_date, end_date
            if skip_notes is not None:
                for request in requests:
                    if request.key == "VUAGl_EQ":
                        skip_notes[request.key] = (
                            "Twelve Data: symbol VUAG is available starting with the Grow plan "
                            "(code 403)"
                        )
            return {}

    service = PerformanceReplayService(
        repository,
        Settings(data_dir=tmp_path),
        cast(MarketDataProvider, SkippingProvider()),
        FakeFxRateProvider(
            {
                "GBP": [
                    FxRatePoint(
                        date(2024, 1, 5),
                        "GBP",
                        Decimal("1.1"),
                        "fixture",
                        date(2024, 1, 5),
                        "EXACT",
                        False,
                    )
                ]
            }
        ),
        clock=FixedClock(datetime(2024, 1, 5, tzinfo=UTC)),
    )

    summary = await service.replay(as_of=date(2024, 1, 5))

    assert summary.missing_price_symbols == ["VUAGl_EQ"]
    assert any(
        "VUAGl_EQ" in note and "Grow plan" in note for note in summary.notes
    ), summary.notes


@pytest.mark.asyncio
async def test_replay_converts_non_eur_cash_flows_at_the_flow_date(tmp_path: Path) -> None:
    repository, session_factory = await _repository_and_session_factory(
        tmp_path, "fx_flows.sqlite3"
    )
    async with session_factory() as session:
        async with session.begin():
            session.add(
                Transaction(
                    reference="dep-usd",
                    ts=datetime(2024, 1, 5, tzinfo=UTC),
                    transaction_type="DEPOSIT",
                    currency_code="USD",
                    amount=Decimal("100"),
                )
            )

    service = PerformanceReplayService(
        repository,
        Settings(data_dir=tmp_path),
        FakeMarketDataProvider({}),
        FakeFxRateProvider(
            {
                "USD": [
                    FxRatePoint(
                        date(2024, 1, 5),
                        "USD",
                        Decimal("0.9"),
                        "fixture",
                        date(2024, 1, 5),
                        "EXACT",
                        False,
                    )
                ]
            }
        ),
        clock=FixedClock(datetime(2024, 1, 5, tzinfo=UTC)),
    )

    summary = await service.replay(as_of=date(2024, 1, 5))
    nav_rows = await repository.list_daily_nav()

    assert summary.excluded_flow_currencies == []
    assert nav_rows[0].external_flow_eur == Decimal("90.0")
    assert nav_rows[0].cash_balance_eur == Decimal("90.0")


@pytest.mark.asyncio
async def test_replay_excludes_a_flow_it_cannot_convert(tmp_path: Path) -> None:
    repository, session_factory = await _repository_and_session_factory(
        tmp_path, "fx_missing.sqlite3"
    )
    async with session_factory() as session:
        async with session.begin():
            session.add(
                Transaction(
                    reference="dep-gbp",
                    ts=datetime(2024, 1, 5, tzinfo=UTC),
                    transaction_type="DEPOSIT",
                    currency_code="GBP",
                    amount=Decimal("100"),
                )
            )

    service = PerformanceReplayService(
        repository,
        Settings(data_dir=tmp_path),
        FakeMarketDataProvider({}),
        FakeFxRateProvider({}),
        clock=FixedClock(datetime(2024, 1, 5, tzinfo=UTC)),
    )

    summary = await service.replay(as_of=date(2024, 1, 5))
    nav_rows = await repository.list_daily_nav()

    assert summary.excluded_flow_currencies == ["GBP"]
    # Never added as if it were EUR.
    assert nav_rows[0].external_flow_eur == Decimal("0")
    assert any("Excluded external flow in GBP" in note for note in summary.notes)


@pytest.mark.asyncio
async def test_replay_does_not_double_count_dividends(tmp_path: Path) -> None:
    repository, session_factory = await _repository_and_session_factory(
        tmp_path, "dividends.sqlite3"
    )
    async with session_factory() as session:
        async with session.begin():
            session.add(
                Dividend(
                    reference="div-1",
                    paid_on=datetime(2024, 1, 5, tzinfo=UTC),
                    t212_ticker="ABC_US_EQ",
                    currency_code="USD",
                    amount=Decimal("10"),
                    amount_in_euro=Decimal("9"),
                )
            )
            session.add(
                Transaction(
                    reference="div-1",
                    ts=datetime(2024, 1, 5, tzinfo=UTC),
                    transaction_type="DIVIDEND",
                    currency_code="EUR",
                    amount=Decimal("9"),
                )
            )

    service = PerformanceReplayService(
        repository,
        Settings(data_dir=tmp_path),
        FakeMarketDataProvider({}),
        FakeFxRateProvider({}),
        clock=FixedClock(datetime(2024, 1, 5, tzinfo=UTC)),
    )

    summary = await service.replay(as_of=date(2024, 1, 5))
    nav_rows = await repository.list_daily_nav()

    assert nav_rows[0].internal_cash_flow_eur == Decimal("9")
    assert nav_rows[0].cash_balance_eur == Decimal("9")
    assert any("double-count" in note for note in summary.notes)


@pytest.mark.asyncio
async def test_get_report_exposes_rolling_windows_and_explicit_unavailable_metrics(
    tmp_path: Path,
) -> None:
    repository, _session_factory = await _repository_and_session_factory(tmp_path, "report.sqlite3")
    holdings: list[DailyHolding] = []
    nav_rows: list[DailyNav] = []
    for index in range(120):
        day = date(2024, 1, 1) + timedelta(days=index)
        price = Decimal("100") + Decimal(index)
        holdings.append(
            DailyHolding(
                as_of_date=day,
                t212_ticker="ABC_US_EQ",
                quantity=Decimal("2"),
                price_currency="EUR",
                close_price=price,
                price_provenance="EXACT",
                fx_rate_to_eur=Decimal("1"),
                fx_provenance="EXACT",
                market_value_local=price * 2,
                market_value_eur=price * 2,
                valuation_status="VALUED",
            )
        )
        nav_rows.append(_nav(day, str(price * 2), flow="200" if index == 0 else "0"))
    await repository.replace_daily_replay(holdings=holdings, nav_rows=nav_rows)

    service = PerformanceReplayService(
        repository,
        Settings(data_dir=tmp_path),
        FakeMarketDataProvider({}),
        FakeFxRateProvider({}),
        clock=FixedClock(datetime(2024, 4, 30, tzinfo=UTC)),
    )

    report = await service.get_report()

    assert report.flow_timing == "flow_at_close"
    assert report.annualization_days == ANNUALIZATION_DAYS
    assert len(report.rolling_volatility_30d) == 119 - 30 + 1
    assert len(report.rolling_volatility_90d) == 119 - 90 + 1
    # No benchmark prices are cached, so beta series and benchmark stats stay empty/explicit.
    assert report.rolling_beta_30d == []
    assert report.rolling_beta_90d == []
    assert all(item.beta.status == "insufficient_data" for item in report.beta_vs_benchmarks)
    assert report.attribution.status == "unavailable"
    assert report.attribution.items == []
    assert report.ff5_momentum_regression.status == "unavailable"
    assert report.passive_counterfactual.status == "unavailable"
    # The NAV series is a straight projection of the replayed rows, gaps and all.
    assert len(report.nav_series) == 120
    assert report.nav_series[0].as_of_date == date(2024, 1, 1)
    assert report.nav_series[0].external_flow_eur == Decimal("200")
    assert report.nav_series[-1].nav_eur == Decimal("438")
    assert {point.valuation_status for point in report.nav_series} == {"VALUED"}
    assert report.contributions[0].status == "ok"
    assert report.contributions[0].return_value == pytest.approx(
        float(Decimal("219") / Decimal("218") - 1)
    )
    assert any("counterfactual" in note for note in report.notes)


@pytest.mark.asyncio
async def test_replay_persists_factor_rows_and_report_regresses_excess_returns(
    tmp_path: Path,
) -> None:
    repository, session_factory = await _repository_and_session_factory(tmp_path, "factors.sqlite3")
    factor_rows = _factor_rows(30)
    async with session_factory() as session:
        async with session.begin():
            session.add(
                Transaction(
                    reference="dep-1",
                    ts=datetime(2024, 1, 1, tzinfo=UTC),
                    transaction_type="DEPOSIT",
                    currency_code="EUR",
                    amount=Decimal("100"),
                )
            )

    service = PerformanceReplayService(
        repository,
        Settings(data_dir=tmp_path),
        FakeMarketDataProvider({}),
        FakeFxRateProvider({}),
        FakeFactorDataProvider(factor_rows),
        clock=FixedClock(datetime(2024, 2, 1, tzinfo=UTC)),
    )
    # replay() is what persists factor observations for the report to read back.
    await service.replay(as_of=date(2024, 1, 30))

    assert len(await repository.list_factor_returns(**_window(factor_rows))) == 30

    # Overwrite the replayed NAV with a series whose excess return is exactly 2 * mkt_rf.
    nav_value = Decimal("100")
    nav_rows = [_nav(factor_rows[0].as_of_date, str(nav_value), flow="100")]
    for row in factor_rows[1:]:
        growth = ONE + row.risk_free_rate + (Decimal("2") * row.factors["mkt_rf"])
        nav_value = nav_value * growth
        nav_rows.append(_nav(row.as_of_date, str(nav_value)))
    await repository.replace_daily_replay(holdings=[], nav_rows=nav_rows)

    report = await service.get_report()

    assert report.ff5_momentum_regression.status == "ok"
    assert report.ff5_momentum_regression.observations == 29
    assert report.ff5_momentum_regression.coefficients["mkt_rf"] == pytest.approx(2.0, abs=1e-4)
    assert report.ff5_momentum_regression.detail is not None
    assert "excess" in report.ff5_momentum_regression.detail


def _window(rows: list[FactorObservation]) -> dict[str, date]:
    return {"start_date": rows[0].as_of_date, "end_date": rows[-1].as_of_date}


async def _seed_single_buy(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        async with session.begin():
            session.add(
                Instrument(
                    t212_ticker="ABC_US_EQ",
                    yahoo_ticker="ABC",
                    currency_code="USD",
                    mapping_status="resolved",
                )
            )
            session.add(
                OrderHistory(
                    fill_id="buy-1",
                    fill_timestamp=datetime(2024, 1, 5, tzinfo=UTC),
                    t212_ticker="ABC_US_EQ",
                    side="BUY",
                    fill_type="TRADE",
                    filled_quantity=Decimal("2"),
                    wallet_currency="EUR",
                    wallet_net_value=Decimal("180"),
                )
            )
            session.add(
                Transaction(
                    reference="dep-1",
                    ts=datetime(2024, 1, 5, tzinfo=UTC),
                    transaction_type="DEPOSIT",
                    currency_code="EUR",
                    amount=Decimal("200"),
                )
            )


async def _repository_and_session_factory(
    tmp_path: Path,
    sqlite_filename: str,
) -> tuple[PortfolioRepository, async_sessionmaker[AsyncSession]]:
    settings = Settings(data_dir=tmp_path, sqlite_filename=sqlite_filename)
    await migrate_database(settings)
    engine = create_async_engine(settings.sqlite_url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return PortfolioRepository(session_factory), session_factory


class _StubEcbTransport(httpx.AsyncBaseTransport):
    """Serves the ECB CSV shape and 404s any series ECB does not publish."""

    def __init__(self) -> None:
        self.requested_urls: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requested_urls.append(str(request.url))
        if "D.GBP.EUR.SP00.A" not in str(request.url):
            return httpx.Response(404, text="No such series")
        body = "TIME_PERIOD,OBS_VALUE\n2026-08-05,0.86\n2026-08-06,0.88\n"
        return httpx.Response(200, text=body)


def _ecb_provider(tmp_path: Path) -> tuple[EcbFxRateProvider, _StubEcbTransport]:
    provider = EcbFxRateProvider(Settings(data_dir=tmp_path))
    transport = _StubEcbTransport()
    provider._client = httpx.AsyncClient(transport=transport)
    return provider, transport


async def test_gbx_is_converted_through_gbp_and_scaled_by_one_hundred(tmp_path: Path) -> None:
    """Trading 212 quotes London listings in pence. ECB has no GBX series.

    A GBX holding must be valued through the GBP fix divided by 100. Skipping the divisor would
    overstate a UK position by 100x, which is the worst kind of wrong: silent and enormous.
    """
    provider, transport = _ecb_provider(tmp_path)

    result = await provider.fetch_eur_base_rates(
        currencies={"GBX"}, start_date=date(2026, 8, 5), end_date=date(2026, 8, 6)
    )

    assert all("D.GBX." not in url for url in transport.requested_urls)
    points = result["GBX"]
    assert [point.currency_code for point in points] == ["GBX", "GBX"]
    # 0.86 GBP per EUR -> 1/0.86 EUR per GBP -> that / 100 EUR per GBX.
    assert points[0].eur_per_unit == (ONE / Decimal("0.86")) / Decimal("100")
    assert points[1].eur_per_unit == (ONE / Decimal("0.88")) / Decimal("100")


async def test_gbx_and_gbp_share_a_single_ecb_request(tmp_path: Path) -> None:
    provider, transport = _ecb_provider(tmp_path)

    result = await provider.fetch_eur_base_rates(
        currencies={"GBX", "GBP"}, start_date=date(2026, 8, 5), end_date=date(2026, 8, 6)
    )

    assert len(transport.requested_urls) == 1
    # The same fix, expressed per unit of each quote convention.
    assert result["GBP"][0].eur_per_unit == result["GBX"][0].eur_per_unit * Decimal("100")


async def test_unpublished_currency_names_itself_instead_of_raising_a_bare_404(
    tmp_path: Path,
) -> None:
    provider, _ = _ecb_provider(tmp_path)

    with pytest.raises(UnknownCurrencyError, match="ZZZ"):
        await provider.fetch_eur_base_rates(
            currencies={"ZZZ"}, start_date=date(2026, 8, 5), end_date=date(2026, 8, 6)
        )


def _twelvedata_payload(currency: str = "USD") -> dict[str, object]:
    return {
        "meta": {"symbol": "IVV", "currency": currency, "exchange": "NYSE", "type": "ETF"},
        "values": [
            {"datetime": "2026-08-06", "close": "772.13000"},
            {"datetime": "2026-08-05", "close": "773.35999"},
        ],
        "status": "ok",
    }


class _StubJsonTransport(httpx.AsyncBaseTransport):
    def __init__(self, payload: object, status_code: int = 200) -> None:
        self._payload = payload
        self._status_code = status_code
        self.calls = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        return httpx.Response(self._status_code, json=self._payload)


def _twelvedata_provider(
    tmp_path: Path, payload: object
) -> tuple[TwelveDataMarketDataProvider, _StubJsonTransport]:
    settings = Settings(
        data_dir=tmp_path, market_data_provider="twelvedata", market_data_api_key="test-key"
    )
    provider = TwelveDataMarketDataProvider(settings)
    transport = _StubJsonTransport(payload)
    provider._client = httpx.AsyncClient(transport=transport)
    return provider, transport


async def test_twelvedata_adopts_the_provider_reported_currency_when_none_is_configured(
    tmp_path: Path,
) -> None:
    """A currency the provider states is data, not a guess.

    The no-guessing rule exists to stop Helios assuming USD. Twelve Data reports meta.currency, so
    using it is what lets benchmarks work without hand-configuring a listing currency.
    """
    provider, _ = _twelvedata_provider(tmp_path, _twelvedata_payload("USD"))

    result = await provider.fetch_daily_closes(
        requests=[PriceRequest("bench:cspx", "IVV", None)],
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 7),
    )

    points = result["bench:cspx"]
    assert [point.currency_code for point in points] == ["USD", "USD"]
    assert [point.as_of_date for point in points] == [date(2026, 8, 5), date(2026, 8, 6)]
    assert points[0].close_price == Decimal("773.35999")


async def test_twelvedata_drops_a_symbol_whose_currency_contradicts_trusted_metadata(
    tmp_path: Path,
) -> None:
    """Disagreement is a mapping error, and valuing a GBX series as GBP overstates by 100x."""
    provider, _ = _twelvedata_provider(tmp_path, _twelvedata_payload("GBX"))

    result = await provider.fetch_daily_closes(
        requests=[PriceRequest("holding:X", "X", "GBP")],
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 7),
    )

    assert result == {}


async def test_twelvedata_error_payload_raises_instead_of_looking_like_an_empty_series(
    tmp_path: Path,
) -> None:
    """Twelve Data returns HTTP 200 with status=error for quota and entitlement failures.

    Swallowing that would report 'no price data' for what is really 'you are out of credits'.
    """
    provider, _ = _twelvedata_provider(
        tmp_path, {"code": 429, "message": "You have run out of API credits", "status": "error"}
    )

    with pytest.raises(MarketDataProviderError, match="credits"):
        await provider.fetch_daily_closes(
            requests=[PriceRequest("holding:X", "X", "USD")],
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 7),
        )


# ---------------------------------------------------------------------------
# Symbol translation
# ---------------------------------------------------------------------------


def test_split_yahoo_symbol_recognises_only_the_lse_suffix() -> None:
    assert _split_yahoo_symbol("VUAG.L") == ("VUAG", "LSE")
    assert _split_yahoo_symbol("SSLN.L") == ("SSLN", "LSE")
    # A bare US ticker (resolver.py never leaves a "." in one -- BRK.B becomes BRK-B) passes
    # through untouched, with no exchange.
    assert _split_yahoo_symbol("NVDA") == ("NVDA", None)
    assert _split_yahoo_symbol("BRK-B") == ("BRK-B", None)


def test_twelvedata_symbol_params_add_exchange_only_for_lse() -> None:
    assert _twelvedata_symbol_params("NVDA") == {"symbol": "NVDA"}
    assert _twelvedata_symbol_params("VUAG.L") == {"symbol": "VUAG", "exchange": "LSE"}


def test_alphavantage_symbol_translates_the_lse_suffix() -> None:
    assert _alphavantage_symbol("NVDA") == "NVDA"
    assert _alphavantage_symbol("VUAG.L") == "VUAG.LON"


async def test_twelvedata_sends_the_translated_symbol_and_exchange_on_the_wire(
    tmp_path: Path,
) -> None:
    """The request Twelve Data actually receives must carry the translated symbol, not Yahoo's."""
    captured: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_twelvedata_payload("GBP"))

    # A paid plan that reaches London: only then is an LSE symbol sent to Twelve Data at all.
    settings = Settings(
        data_dir=tmp_path,
        market_data_provider="twelvedata",
        market_data_api_key="test-key",
        twelvedata_exchanges="US,LSE",
    )
    provider = TwelveDataMarketDataProvider(settings)
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    await provider.fetch_daily_closes(
        requests=[PriceRequest("holding:VUAG", "VUAG.L", "GBP")],
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 7),
    )

    assert len(captured) == 1
    params = captured[0].url.params
    assert params["symbol"] == "VUAG"
    assert params["exchange"] == "LSE"


async def test_alphavantage_sends_the_translated_symbol_on_the_wire(tmp_path: Path) -> None:
    captured: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"Time Series (Daily)": {}})

    provider, _ = _alphavantage_provider(tmp_path, handler)

    await provider.fetch_daily_closes(
        requests=[PriceRequest("holding:VUAG", "VUAG.L", "GBX")],
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 7),
    )

    assert len(captured) == 1
    assert captured[0].url.params["symbol"] == "VUAG.LON"


# ---------------------------------------------------------------------------
# Per-symbol vs account-level failures
# ---------------------------------------------------------------------------


async def test_twelvedata_symbol_level_error_is_skipped_while_other_symbols_still_price(
    tmp_path: Path,
) -> None:
    """A symbol the free plan doesn't cover must not abort pricing for the rest of the batch."""

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("symbol") == "VUAG":
            return httpx.Response(
                200,
                json={
                    "code": 403,
                    "message": "symbol VUAG is available starting with the Grow plan",
                    "status": "error",
                },
            )
        return httpx.Response(200, json=_twelvedata_payload("USD"))

    # A paid plan that reaches London: only then is an LSE symbol sent to Twelve Data at all.
    settings = Settings(
        data_dir=tmp_path,
        market_data_provider="twelvedata",
        market_data_api_key="test-key",
        twelvedata_exchanges="US,LSE",
    )
    provider = TwelveDataMarketDataProvider(settings)
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    skip_notes: dict[str, str] = {}
    result = await provider.fetch_daily_closes(
        requests=[
            PriceRequest("holding:VUAG", "VUAG.L", "GBP"),
            PriceRequest("bench:cspx", "IVV", None),
        ],
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 7),
        skip_notes=skip_notes,
    )

    assert "holding:VUAG" not in result
    assert len(result["bench:cspx"]) == 2
    assert "Grow plan" in skip_notes["holding:VUAG"]


@pytest.mark.parametrize(
    ("code", "message"),
    [
        (401, "Invalid API key"),
        (429, "You have run out of API credits"),
    ],
)
async def test_twelvedata_account_level_error_raises_instead_of_being_skipped(
    tmp_path: Path, code: int, message: str
) -> None:
    provider, _ = _twelvedata_provider(
        tmp_path, {"code": code, "message": message, "status": "error"}
    )

    with pytest.raises(MarketDataProviderError):
        await provider.fetch_daily_closes(
            requests=[PriceRequest("holding:X", "X", "USD")],
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 7),
        )


def _alphavantage_provider(
    tmp_path: Path, handler: object
) -> tuple[AlphaVantageMarketDataProvider, httpx.AsyncClient]:
    settings = Settings(
        data_dir=tmp_path, market_data_provider="alphavantage", market_data_api_key="test-key"
    )
    provider = AlphaVantageMarketDataProvider(settings)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
    provider._client = client
    return provider, client


def _alphavantage_daily_payload() -> dict[str, object]:
    return {
        "Meta Data": {"1. Information": "Daily Prices", "2. Symbol": "TSCO.LON"},
        "Time Series (Daily)": {
            "2026-08-06": {
                "1. open": "270.00",
                "2. high": "271.00",
                "3. low": "269.00",
                "4. close": "270.50",
                "5. volume": "1000",
            },
            "2026-08-05": {
                "1. open": "268.00",
                "2. high": "269.00",
                "3. low": "267.00",
                "4. close": "268.75",
                "5. volume": "900",
            },
        },
    }


async def test_alphavantage_uses_the_free_daily_endpoint_with_compact_outputsize(
    tmp_path: Path,
) -> None:
    """TIME_SERIES_DAILY_ADJUSTED and outputsize=full are premium-only; the free key needs the
    unadjusted daily series with the compact (~100 point) window."""
    captured: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_alphavantage_daily_payload())

    provider, _ = _alphavantage_provider(tmp_path, handler)

    result = await provider.fetch_daily_closes(
        requests=[PriceRequest("holding:TSCO", "TSCO.L", "GBX")],
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 7),
    )

    assert len(captured) == 1
    params = captured[0].url.params
    assert params["function"] == "TIME_SERIES_DAILY"
    assert params["outputsize"] == "compact"
    assert "adjusted" not in str(params)

    points = result["holding:TSCO"]
    assert [point.close_price for point in points] == [Decimal("268.75"), Decimal("270.50")]
    # GBX (pence) is passed through untouched -- Alpha Vantage never reports a currency, and the
    # only source of truth here is the trusted instrument currency on the request.
    assert {point.currency_code for point in points} == {"GBX"}


async def test_alphavantage_error_message_is_skipped_not_raised(tmp_path: Path) -> None:
    """'Error Message' means this one symbol -- unknown ticker, bad parameter -- not the account."""

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200, json={"Error Message": "Invalid API call. Please retry or visit the documentation"}
        )

    provider, _ = _alphavantage_provider(tmp_path, handler)

    skip_notes: dict[str, str] = {}
    result = await provider.fetch_daily_closes(
        requests=[PriceRequest("holding:X", "X", "USD")],
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 7),
        skip_notes=skip_notes,
    )

    assert result == {}
    assert "Invalid API call" in skip_notes["holding:X"]


@pytest.mark.parametrize("key", ["Note", "Information"])
async def test_alphavantage_rate_limit_keys_raise_instead_of_being_skipped(
    tmp_path: Path, key: str
) -> None:
    """'Note'/'Information' mean the free plan's rate or daily ceiling was hit -- every other
    request in the batch would fail the same way, so this must not look like an empty series."""

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                key: (
                    "Thank you for using Alpha Vantage! Our standard API rate limit is "
                    "25 requests per day"
                )
            },
        )

    provider, _ = _alphavantage_provider(tmp_path, handler)

    with pytest.raises(MarketDataProviderError, match="25 requests per day"):
        await provider.fetch_daily_closes(
            requests=[PriceRequest("holding:X", "X", "USD")],
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 7),
        )


async def test_alphavantage_skips_a_request_with_no_trusted_currency(tmp_path: Path) -> None:
    """Alpha Vantage never reports a quotation currency; without trusted metadata it must not
    guess (historically USD), which would silently mis-value a non-USD listing."""

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json=_alphavantage_daily_payload())

    provider, _ = _alphavantage_provider(tmp_path, handler)

    skip_notes: dict[str, str] = {}
    result = await provider.fetch_daily_closes(
        requests=[PriceRequest("holding:TSCO", "TSCO.L", None)],
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 7),
        skip_notes=skip_notes,
    )

    assert result == {}
    assert "holding:TSCO" in skip_notes


# ---------------------------------------------------------------------------
# Composite (primary + fallback) provider
# ---------------------------------------------------------------------------


async def test_composite_provider_only_asks_the_fallback_for_the_primarys_gaps() -> None:
    """The fallback's tiny quota must be spent only on what the primary genuinely couldn't price."""
    primary = FakeMarketDataProvider(
        {
            "holding:NVDA": [
                DailyPricePoint(
                    date(2026, 8, 5), Decimal("100"), "USD", "twelvedata", date(2026, 8, 5), "EXACT"
                )
            ]
        }
    )
    fallback = FakeMarketDataProvider(
        {
            "holding:VUAG": [
                DailyPricePoint(
                    date(2026, 8, 5),
                    Decimal("90"),
                    "GBP",
                    "alphavantage",
                    date(2026, 8, 5),
                    "EXACT",
                )
            ]
        }
    )
    composite = CompositeMarketDataProvider(
        cast(MarketDataProvider, primary), cast(MarketDataProvider, fallback)
    )

    result = await composite.fetch_daily_closes(
        requests=[
            PriceRequest("holding:NVDA", "NVDA", "USD"),
            PriceRequest("holding:VUAG", "VUAG.L", "GBP"),
        ],
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 7),
    )

    assert set(result) == {"holding:NVDA", "holding:VUAG"}
    assert [request.key for request in fallback.seen_requests] == ["holding:VUAG"]


async def test_composite_provider_never_asks_the_fallback_when_the_primary_covers_everything() -> (
    None
):
    primary = FakeMarketDataProvider(
        {
            "holding:NVDA": [
                DailyPricePoint(
                    date(2026, 8, 5), Decimal("100"), "USD", "twelvedata", date(2026, 8, 5), "EXACT"
                )
            ]
        }
    )
    fallback = FakeMarketDataProvider({})
    composite = CompositeMarketDataProvider(
        cast(MarketDataProvider, primary), cast(MarketDataProvider, fallback)
    )

    await composite.fetch_daily_closes(
        requests=[PriceRequest("holding:NVDA", "NVDA", "USD")],
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 7),
    )

    assert fallback.seen_requests == []


async def test_composite_provider_clears_a_skip_note_once_the_fallback_fills_the_gap() -> None:
    class SkippingPrimary:
        async def fetch_daily_closes(
            self,
            *,
            requests: Sequence[PriceRequest],
            start_date: date,
            end_date: date,
            skip_notes: dict[str, str] | None = None,
        ) -> dict[str, list[DailyPricePoint]]:
            del start_date, end_date
            if skip_notes is not None:
                for request in requests:
                    skip_notes[request.key] = "Twelve Data: not covered by the free plan"
            return {}

    fallback = FakeMarketDataProvider(
        {
            "holding:VUAG": [
                DailyPricePoint(
                    date(2026, 8, 5),
                    Decimal("90"),
                    "GBP",
                    "alphavantage",
                    date(2026, 8, 5),
                    "EXACT",
                )
            ]
        }
    )
    composite = CompositeMarketDataProvider(
        cast(MarketDataProvider, SkippingPrimary()), cast(MarketDataProvider, fallback)
    )

    skip_notes: dict[str, str] = {}
    result = await composite.fetch_daily_closes(
        requests=[PriceRequest("holding:VUAG", "VUAG.L", "GBP")],
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 7),
        skip_notes=skip_notes,
    )

    assert "holding:VUAG" in result
    assert "holding:VUAG" not in skip_notes


async def test_composite_provider_reports_both_reasons_when_the_fallback_also_fails() -> None:
    class SkippingPrimary:
        async def fetch_daily_closes(
            self,
            *,
            requests: Sequence[PriceRequest],
            start_date: date,
            end_date: date,
            skip_notes: dict[str, str] | None = None,
        ) -> dict[str, list[DailyPricePoint]]:
            del start_date, end_date
            if skip_notes is not None:
                for request in requests:
                    skip_notes[request.key] = "Twelve Data: not covered by the free plan"
            return {}

    class SkippingFallback:
        async def fetch_daily_closes(
            self,
            *,
            requests: Sequence[PriceRequest],
            start_date: date,
            end_date: date,
            skip_notes: dict[str, str] | None = None,
        ) -> dict[str, list[DailyPricePoint]]:
            del start_date, end_date
            if skip_notes is not None:
                for request in requests:
                    skip_notes[request.key] = "Alpha Vantage: unknown symbol"
            return {}

    composite = CompositeMarketDataProvider(
        cast(MarketDataProvider, SkippingPrimary()), cast(MarketDataProvider, SkippingFallback())
    )

    skip_notes: dict[str, str] = {}
    result = await composite.fetch_daily_closes(
        requests=[PriceRequest("holding:GHOST", "GHOST.L", "GBP")],
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 7),
        skip_notes=skip_notes,
    )

    assert result == {}
    assert "Twelve Data" in skip_notes["holding:GHOST"]
    assert "Alpha Vantage" in skip_notes["holding:GHOST"]


def _ken_french_zip(name: str, body: str) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, body)
    return buffer.getvalue()


FF5_BODY = """This file was created by using the 202606 CRSP database.
Some more prose about the T-bill.

,Mkt-RF,SMB,HML,RMW,CMA,RF
20260803,   -0.67,    0.00,   -0.34,   -0.01,    0.16,    0.01
20260804,    0.79,   -0.26,    0.26,   -0.07,   -0.20,    0.01
20260805,  -99.99,  -99.99,  -99.99,  -99.99,  -99.99,  -99.99

Copyright 2026 Eugene F. Fama and Kenneth R. French
"""

MOM_BODY = """This file was created by using the 202606 CRSP database.
Missing data are indicated by -99.99 or -999.

,Mom
20260803,   0.35
20260804,  -0.61
20260805,   1.15

Copyright 2026 Eugene F. Fama and Kenneth R. French
"""


def test_ken_french_values_are_converted_from_percent_to_decimal_fractions() -> None:
    """The library quotes percent. Reading -0.67 as a fraction inflates every loading by 100x."""
    table = parse_ken_french_csv(_ken_french_zip("ff5.csv", FF5_BODY))

    assert table[date(2026, 8, 3)]["Mkt-RF"] == Decimal("-0.67") / Decimal("100")
    assert table[date(2026, 8, 3)]["RF"] == Decimal("0.01") / Decimal("100")


def test_ken_french_missing_sentinels_are_dropped_not_read_as_returns() -> None:
    """-99.99 is 'no observation', not a -99.99% day."""
    table = parse_ken_french_csv(_ken_french_zip("ff5.csv", FF5_BODY))

    assert table[date(2026, 8, 5)] == {}
    assert "Mkt-RF" in table[date(2026, 8, 4)]


def test_ken_french_ignores_prose_header_and_trailing_copyright() -> None:
    table = parse_ken_french_csv(_ken_french_zip("ff5.csv", FF5_BODY))

    assert sorted(table) == [date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5)]


async def test_ken_french_provider_joins_five_factor_and_momentum_files(tmp_path: Path) -> None:
    provider = KenFrenchFactorDataProvider(Settings(data_dir=tmp_path))

    async def handler(request: httpx.Request) -> httpx.Response:
        if "Momentum" in str(request.url):
            return httpx.Response(200, content=_ken_french_zip("mom.csv", MOM_BODY))
        return httpx.Response(200, content=_ken_french_zip("ff5.csv", FF5_BODY))

    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    observations = await provider.fetch_factor_returns(
        start_date=date(2026, 8, 1), end_date=date(2026, 8, 31)
    )

    # 2026-08-05 is dropped: its 5-factor row is all sentinels, so the factors are absent.
    assert [row.as_of_date for row in observations] == [date(2026, 8, 3), date(2026, 8, 4)]
    first = observations[0]
    assert set(first.factors) == set(FACTOR_NAMES)
    assert first.factors["mom"] == Decimal("0.35") / Decimal("100")
    assert first.risk_free_rate == Decimal("0.01") / Decimal("100")


def test_factor_regression_distinguishes_unconfigured_from_no_published_overlap() -> None:
    """Two empty-factor cases need different explanations.

    'Set the provider' is the wrong instruction when the provider is set and working; the real
    cause is that the library trails the present by about a month.
    """
    returns = [DailyReturnPoint(date(2026, 8, 5), 0.01), DailyReturnPoint(date(2026, 8, 6), 0.02)]

    disabled = ff5_momentum_regression(returns, [], factor_provider="disabled")
    configured = ff5_momentum_regression(returns, [], factor_provider="kenfrench")

    assert disabled.status == "unavailable"
    assert "HELIOS_FACTOR_DATA_PROVIDER=disabled" in str(disabled.detail)

    assert configured.status == "unavailable"
    assert "kenfrench" in str(configured.detail)
    assert "trails the present" in str(configured.detail)
    assert "2026-08-05" in str(configured.detail)
    assert "disabled" not in str(configured.detail)


# ---------------------------------------------------------------------------
# Replay concurrency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replay_refuses_to_run_concurrently(tmp_path: Path) -> None:
    """A second replay must be refused while the first holds the lease.

    Replay rewrites the whole daily_holdings/daily_nav pair and re-fetches every price, so two
    concurrent runs duplicate provider calls against a metered quota and race to be the last
    writer. Once the dashboard has a Replay button, a double-click is exactly this case.
    """
    repository, session_factory = await _repository_and_session_factory(
        tmp_path, "replay-lease.sqlite3"
    )
    await _seed_single_buy(session_factory)
    service = _replay_service(repository, tmp_path)

    # Stand in for a replay already in flight by holding its lease.
    held = await repository.acquire_performance_replay_lease(
        acquired_at=datetime(2024, 1, 6, tzinfo=UTC), lease_minutes=15
    )

    with pytest.raises(SyncAlreadyRunningError):
        await service.replay(as_of=date(2024, 1, 6))

    # The refusal must leave the in-flight run's lease untouched, not steal or clear it.
    await repository.release_performance_replay_lease(
        lease=held,
        completed_at=datetime(2024, 1, 6, tzinfo=UTC),
        succeeded=True,
        error_message=None,
    )
    summary = await service.replay(as_of=date(2024, 1, 6))
    assert summary.nav_written > 0


@pytest.mark.asyncio
async def test_replay_releases_its_lease_after_a_failure(tmp_path: Path) -> None:
    """A crashed replay must not lock the key until the stale cutoff expires."""
    repository, session_factory = await _repository_and_session_factory(
        tmp_path, "replay-lease-failure.sqlite3"
    )
    await _seed_single_buy(session_factory)

    class ExplodingMarketData:
        async def fetch_daily_closes(self, **_: object) -> dict[str, list[DailyPricePoint]]:
            raise RuntimeError("provider down")

    failing = PerformanceReplayService(
        repository,
        Settings(data_dir=tmp_path),
        cast(MarketDataProvider, ExplodingMarketData()),
        FakeFxRateProvider({}),
        clock=FixedClock(datetime(2024, 1, 6, tzinfo=UTC)),
    )

    with pytest.raises(RuntimeError):
        await failing.replay(as_of=date(2024, 1, 6))

    # The lease is free again, so a healthy replay runs immediately rather than waiting it out.
    summary = await _replay_service(repository, tmp_path).replay(as_of=date(2024, 1, 6))
    assert summary.nav_written > 0


@pytest.mark.asyncio
async def test_replay_lease_row_never_appears_as_an_endpoint(tmp_path: Path) -> None:
    """Lease keys are internal bookkeeping, not upstream endpoints the quality report lists."""
    repository, session_factory = await _repository_and_session_factory(
        tmp_path, "replay-lease-hidden.sqlite3"
    )
    await _seed_single_buy(session_factory)
    await _replay_service(repository, tmp_path).replay(as_of=date(2024, 1, 6))

    endpoints = {row.endpoint for row in await repository.list_endpoint_statuses()}

    assert PERFORMANCE_REPLAY_LEASE_ENDPOINT not in endpoints
    assert PORTFOLIO_SYNC_LEASE_ENDPOINT not in endpoints


def _replay_service(repository: PortfolioRepository, tmp_path: Path) -> PerformanceReplayService:
    return PerformanceReplayService(
        repository,
        Settings(data_dir=tmp_path),
        FakeMarketDataProvider(
            {
                "ABC_US_EQ": [
                    DailyPricePoint(
                        date(2024, 1, 5),
                        Decimal("100"),
                        "USD",
                        "fixture",
                        date(2024, 1, 5),
                        "EXACT",
                    )
                ]
            }
        ),
        FakeFxRateProvider(
            {
                "USD": [
                    FxRatePoint(
                        date(2024, 1, 5),
                        "USD",
                        Decimal("0.9"),
                        "fixture",
                        date(2024, 1, 5),
                        "EXACT",
                        False,
                    )
                ]
            }
        ),
        clock=FixedClock(datetime(2024, 1, 6, tzinfo=UTC)),
    )


@pytest.mark.parametrize("flow_timing", ["flow_at_close", "flow_at_open", "intraday_split"])
def test_daily_twr_never_bridges_an_unvalued_gap(flow_timing: str) -> None:
    """A deposit made on a day that could not be valued must not become investment gain.

    Found on a real account: a month without London prices, EUR ~930 deposited inside it, and
    the first valued day afterwards reported +133% because the return was measured against the
    last valued day before the gap using only that day's (zero) flow. Across a gap the return
    is unknown, so it is left out; only day-to-day returns between valued neighbours count.
    """
    nav_rows = [
        _nav(date(2024, 1, 1), "500"),
        _nav(date(2024, 1, 2), None, flow="900"),  # deposit on an unpriced day
        _nav(date(2024, 1, 3), None),
        _nav(date(2024, 1, 4), "1400"),  # first valued day after the gap
        _nav(date(2024, 1, 5), "1414"),  # a genuine +1% day
    ]

    points = compute_daily_twr(nav_rows, flow_timing=flow_timing)  # type: ignore[arg-type]

    assert [point.as_of_date for point in points] == [date(2024, 1, 5)]
    assert points[0].value == pytest.approx(0.01)


def test_a_withdrawal_is_not_a_drawdown() -> None:
    """Taking money out halves NAV but loses nothing: the time-weighted index must not move.

    Found on a real account whose 44 withdrawals produced a -37.6% "max drawdown".
    """
    nav_rows = [
        _nav(date(2024, 1, 1), "1000"),
        _nav(date(2024, 1, 2), "500", flow="-500"),  # withdrawal only
        _nav(date(2024, 1, 3), "505"),  # +1% genuine gain
    ]

    drawdown = compute_drawdown(nav_rows)

    assert drawdown is not None
    assert drawdown.drawdown == 0.0
