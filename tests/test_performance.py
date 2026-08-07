from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import numpy as np
import pytest
from hypothesis import given
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
    ONE,
    BenchmarkDefinition,
    DailyPricePoint,
    DailyReturnPoint,
    EcbFxRateProvider,
    FactorObservation,
    FxRatePoint,
    PerformanceReplayService,
    PriceRequest,
    UnknownCurrencyError,
    _merge_fx_maps,
    _merge_market_price_maps,
    _missing_price_requests,
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
)
from helios.portfolio_repository import PortfolioRepository
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
    ) -> dict[str, list[DailyPricePoint]]:
        del start_date, end_date
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
