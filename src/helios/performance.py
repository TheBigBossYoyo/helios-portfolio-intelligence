"""Milestone 3 performance/risk analytics.

Design rules that the rest of this module obeys:

* Every persisted financial value stays an exact ``Decimal``; floats appear only inside the
  statistical layer, never in storage.
* The daily replay produces one NAV observation per **calendar** day, so every annualisation in
  this module uses :data:`ANNUALIZATION_DAYS` (365) rather than a 252 trading-day basis.
* Nothing is fabricated. When an input is missing, stale beyond the configured cutoff, or an
  unlicensed data source would be required, the affected metric carries an explicit
  ``insufficient_data``/``unavailable`` status instead of a number.
"""

from __future__ import annotations

import csv
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from io import StringIO
from itertools import pairwise
from typing import Protocol, cast

import httpx
import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.optimize import brentq
from scipy.spatial.distance import squareform

from .config import FlowTiming, Settings
from .models import (
    DailyHolding,
    DailyNav,
    FactorReturnDaily,
    FxRateDaily,
    Instrument,
    MarketPriceDaily,
)
from .portfolio_repository import PortfolioRepository, ReplayInputData
from .rate_limit import Clock, SystemClock

ZERO = Decimal("0")
ONE = Decimal("1")
BASE_CURRENCY = "EUR"

#: Daily observations are calendar days (the replay emits a row for every day), so annualised
#: statistics scale by 365 and not by a 252 trading-day count.
ANNUALIZATION_DAYS = 365
ROLLING_SHORT_WINDOW = 30
ROLLING_LONG_WINDOW = 90
MIN_VAR_OBSERVATIONS = 20
MIN_FACTOR_OBSERVATIONS = 12
VAR_QUANTILE_METHOD = "linear"
FACTOR_NAMES = ("mkt_rf", "smb", "hml", "rmw", "cma", "mom")

EXTERNAL_FLOW_TYPES = frozenset(
    {
        "DEPOSIT",
        "WITHDRAWAL",
        "WITHDRAW",
        "TRANSFER_IN",
        "TRANSFER_OUT",
        "CASH_TRANSFER",
    }
)

VALUATION_VALUED = "VALUED"
VALUATION_FORWARD_FILL = "FORWARD_FILL"
VALUATION_MISSING_PRICE = "MISSING_PRICE"
VALUATION_STALE_PRICE = "STALE_PRICE"
VALUATION_MISSING_FX = "MISSING_FX"
VALUATION_STALE_FX = "STALE_FX"
VALUATION_PARTIAL = "PARTIAL"

PROVENANCE_EXACT = "EXACT"
PROVENANCE_FORWARD_FILL = "FORWARD_FILL"

# Trading 212 quotes some London listings in GBX (pence), which is not a currency and has no ECB
# series -- asking for D.GBX.EUR.SP00.A returns 404. It is a minor unit: 1 GBX = 1/100 GBP. Map
# each minor unit to its major currency and the divisor, fetch the major series, and scale.
# Treating GBX as GBP without scaling would overstate a UK holding by 100x.
MINOR_UNIT_CURRENCIES: dict[str, tuple[str, Decimal]] = {
    "GBX": ("GBP", Decimal("100")),
    "GBP_MINOR": ("GBP", Decimal("100")),
    "ZAC": ("ZAR", Decimal("100")),
    "ILA": ("ILS", Decimal("100")),
}


def resolve_minor_unit(currency: str) -> tuple[str, Decimal]:
    """Return the (major currency, divisor) an FX quote must be fetched and scaled by."""
    return MINOR_UNIT_CURRENCIES.get(currency.upper(), (currency.upper(), ONE))


@dataclass(frozen=True)
class MetricValue:
    status: str
    value: float | None
    observations: int
    detail: str | None = None


@dataclass(frozen=True)
class DailyReturnPoint:
    as_of_date: date
    value: float


@dataclass(frozen=True)
class DailyValuePoint:
    as_of_date: date
    value: Decimal


@dataclass(frozen=True)
class NavPoint:
    """A replayed NAV observation, projected for presentation.

    ``nav_eur`` is ``None`` on days the replay refused to value (missing or stale inputs); the
    ``valuation_status`` says which, so a chart can show a gap instead of inventing a level.
    """

    as_of_date: date
    nav_eur: Decimal | None
    cash_balance_eur: Decimal
    securities_value_eur: Decimal | None
    external_flow_eur: Decimal
    valuation_status: str


@dataclass(frozen=True)
class DrawdownPoint:
    peak_date: date
    trough_date: date
    recovery_date: date | None
    drawdown: float
    time_underwater_days: int
    recovery_days: int | None


@dataclass(frozen=True)
class ReturnObservation:
    as_of_date: date
    portfolio_return: float
    benchmark_return: float


@dataclass(frozen=True)
class ContributionItem:
    key: str
    weight: float
    status: str
    return_value: float | None
    contribution: float | None
    detail: str | None = None


@dataclass(frozen=True)
class AttributionItem:
    key: str
    portfolio_weight: float
    benchmark_weight: float
    allocation_effect: float
    selection_effect: float
    interaction_effect: float
    total_effect: float


@dataclass(frozen=True)
class AttributionReport:
    status: str
    active_return: float | None
    items: list[AttributionItem]
    detail: str | None = None


@dataclass(frozen=True)
class ClusterAssignment:
    key: str
    cluster: int
    weight: float


@dataclass(frozen=True)
class CorrelationClusterReport:
    status: str
    observations: int
    distance_threshold: float
    cluster_count: int | None
    assignments: list[ClusterAssignment]
    detail: str | None = None


@dataclass(frozen=True)
class PassiveCounterfactualReport:
    status: str
    benchmark_key: str
    benchmark_label: str
    invested_eur: Decimal | None
    final_value_eur: Decimal | None
    actual_nav_eur: Decimal | None
    difference_eur: Decimal | None
    series: list[DailyValuePoint]
    excluded_flow_count: int
    detail: str | None = None


@dataclass(frozen=True)
class RegressionResult:
    status: str
    observations: int
    r_squared: float | None
    intercept: float | None
    coefficients: dict[str, float | None]
    detail: str | None = None


@dataclass(frozen=True)
class DailyPricePoint:
    as_of_date: date
    close_price: Decimal
    currency_code: str
    provider: str
    source_date: date
    provenance: str


@dataclass(frozen=True)
class FxRatePoint:
    as_of_date: date
    currency_code: str
    eur_per_unit: Decimal
    provider: str
    source_date: date
    provenance: str
    stale: bool


@dataclass(frozen=True)
class FactorObservation:
    as_of_date: date
    provider: str
    risk_free_rate: Decimal
    factors: dict[str, Decimal]


@dataclass(frozen=True)
class PriceRequest:
    """A single provider lookup.

    ``currency_code`` must come from trusted instrument/benchmark metadata. Providers refuse to
    guess: a request without a currency is skipped rather than defaulting to USD.
    """

    key: str
    provider_symbol: str
    currency_code: str | None


@dataclass(frozen=True)
class BenchmarkDefinition:
    key: str
    label: str
    provider_symbol: str
    currency_code: str | None
    description: str

    @property
    def cache_key(self) -> str:
        return benchmark_cache_key(self.key)


@dataclass(frozen=True)
class BenchmarkReport:
    benchmark: BenchmarkDefinition
    beta: MetricValue
    alpha: MetricValue
    r_squared: MetricValue
    correlation: MetricValue
    tracking_error: MetricValue
    information_ratio: MetricValue


@dataclass(frozen=True)
class PerformanceReplaySummary:
    as_of: date
    start_date: date | None
    end_date: date | None
    flow_timing: str
    holdings_written: int
    nav_written: int
    unsupported_quantity_events: list[str]
    missing_price_symbols: list[str]
    stale_price_symbols: list[str]
    missing_fx_currencies: list[str]
    stale_fx_currencies: list[str]
    excluded_flow_currencies: list[str]
    notes: list[str]


@dataclass(frozen=True)
class PerformanceReport:
    as_of: date | None
    start_date: date | None
    end_date: date | None
    flow_timing: str
    annualization_days: int
    cumulative_twr: MetricValue
    xirr: MetricValue
    annualized_return: MetricValue
    volatility: MetricValue
    downside_volatility: MetricValue
    sharpe: MetricValue
    sortino: MetricValue
    calmar: MetricValue
    max_drawdown: MetricValue
    time_underwater_days: MetricValue
    recovery_days: MetricValue
    beta_vs_benchmarks: list[BenchmarkReport]
    hhi: MetricValue
    effective_number_of_positions: MetricValue
    top5_weight: MetricValue
    var_95_1d: MetricValue
    cvar_95_1d: MetricValue
    var_99_1d: MetricValue
    cvar_99_1d: MetricValue
    var_95_10d: MetricValue
    cvar_95_10d: MetricValue
    var_99_10d: MetricValue
    cvar_99_10d: MetricValue
    ff5_momentum_regression: RegressionResult
    nav_series: list[NavPoint]
    daily_twr: list[DailyReturnPoint]
    rolling_volatility_30d: list[DailyReturnPoint]
    rolling_volatility_90d: list[DailyReturnPoint]
    rolling_beta_30d: list[DailyReturnPoint]
    rolling_beta_90d: list[DailyReturnPoint]
    contributions: list[ContributionItem]
    attribution: AttributionReport
    correlation_clusters: CorrelationClusterReport
    passive_counterfactual: PassiveCounterfactualReport
    notes: list[str]


class MarketDataProvider(Protocol):
    async def fetch_daily_closes(
        self,
        *,
        requests: Sequence[PriceRequest],
        start_date: date,
        end_date: date,
    ) -> dict[str, list[DailyPricePoint]]: ...


class FxRateProvider(Protocol):
    async def fetch_eur_base_rates(
        self,
        *,
        currencies: set[str],
        start_date: date,
        end_date: date,
    ) -> dict[str, list[FxRatePoint]]: ...


class FactorDataProvider(Protocol):
    async def fetch_factor_returns(
        self,
        *,
        start_date: date,
        end_date: date,
    ) -> list[FactorObservation]: ...


class NullMarketDataProvider:
    """Default provider: Helios ships without a paid market-data credential."""

    async def fetch_daily_closes(
        self,
        *,
        requests: Sequence[PriceRequest],
        start_date: date,
        end_date: date,
    ) -> dict[str, list[DailyPricePoint]]:
        del requests, start_date, end_date
        return {}


class NullFactorDataProvider:
    """Default provider: no licensed Fama-French/momentum feed is configured."""

    async def fetch_factor_returns(
        self,
        *,
        start_date: date,
        end_date: date,
    ) -> list[FactorObservation]:
        del start_date, end_date
        return []


class AlphaVantageMarketDataProvider:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(timeout=settings.market_data_timeout_seconds)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch_daily_closes(
        self,
        *,
        requests: Sequence[PriceRequest],
        start_date: date,
        end_date: date,
    ) -> dict[str, list[DailyPricePoint]]:
        api_key = self._settings.market_data_api_key
        if api_key is None:
            return {}
        results: dict[str, list[DailyPricePoint]] = {}
        for request in requests:
            if request.currency_code is None:
                # Alpha Vantage's daily series does not report a quotation currency. Without
                # trusted metadata the close would have to be assumed (historically: USD), which
                # would silently mis-value non-USD listings. Skip instead.
                continue
            response = await self._client.get(
                self._settings.market_data_base_url,
                params={
                    "function": "TIME_SERIES_DAILY_ADJUSTED",
                    "outputsize": "full",
                    "datatype": "json",
                    "symbol": request.provider_symbol,
                    "apikey": api_key.get_secret_value(),
                },
            )
            response.raise_for_status()
            payload = response.json()
            series = payload.get("Time Series (Daily)")
            if not isinstance(series, dict):
                continue
            points: list[DailyPricePoint] = []
            for key, raw in series.items():
                point_date = date.fromisoformat(key)
                if point_date < start_date or point_date > end_date:
                    continue
                close_value = raw.get("5. adjusted close") or raw.get("4. close")
                if not isinstance(close_value, str):
                    continue
                points.append(
                    DailyPricePoint(
                        as_of_date=point_date,
                        close_price=Decimal(close_value),
                        currency_code=request.currency_code,
                        provider="alphavantage",
                        source_date=point_date,
                        provenance=PROVENANCE_EXACT,
                    )
                )
            results[request.key] = sorted(points, key=lambda item: item.as_of_date)
        return results


class UnknownCurrencyError(ValueError):
    """A holding is quoted in a currency Helios cannot convert to EUR."""


class EcbFxRateProvider:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(timeout=settings.market_data_timeout_seconds)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch_eur_base_rates(
        self,
        *,
        currencies: set[str],
        start_date: date,
        end_date: date,
    ) -> dict[str, list[FxRatePoint]]:
        results: dict[str, list[FxRatePoint]] = {}
        # A minor unit (GBX) and its major currency (GBP) share one ECB series, so fetch each
        # series once and derive every requested currency that maps onto it.
        series_cache: dict[str, list[tuple[date, Decimal]]] = {}
        for currency in sorted(currencies):
            if currency == BASE_CURRENCY:
                continue
            major_currency, divisor = resolve_minor_unit(currency)
            if major_currency == BASE_CURRENCY:
                # A minor unit of the base currency (there is none today, but the table is open)
                # needs no FX lookup -- only the scale.
                results[currency] = []
                continue
            if major_currency not in series_cache:
                series_cache[major_currency] = await self._fetch_series(
                    currency=major_currency, start_date=start_date, end_date=end_date
                )
            points = [
                FxRatePoint(
                    as_of_date=point_date,
                    currency_code=currency,
                    # eur_per_unit is per unit of the *requested* currency: one GBX buys a
                    # hundredth of what one GBP buys.
                    eur_per_unit=(ONE / quoted_ccy_per_eur) / divisor,
                    provider="ecb",
                    source_date=point_date,
                    provenance=PROVENANCE_EXACT,
                    stale=False,
                )
                for point_date, quoted_ccy_per_eur in series_cache[major_currency]
            ]
            results[currency] = sorted(points, key=lambda item: item.as_of_date)
        return results

    async def _fetch_series(
        self, *, currency: str, start_date: date, end_date: date
    ) -> list[tuple[date, Decimal]]:
        url = (
            f"{self._settings.ecb_base_url}/EXR/D.{currency}.EUR.SP00.A"
            f"?format=csvdata&startPeriod={start_date.isoformat()}"
            f"&endPeriod={end_date.isoformat()}"
        )
        response = await self._client.get(url)
        if response.status_code == httpx.codes.NOT_FOUND:
            # ECB publishes no such series. Name the currency -- a bare 404 from a URL the caller
            # never built is not a diagnosable error.
            raise UnknownCurrencyError(
                f"ECB publishes no EUR reference rate for {currency!r}. If this is a minor unit "
                f"or a currency Helios should know about, add it to MINOR_UNIT_CURRENCIES."
            )
        response.raise_for_status()
        reader = csv.DictReader(StringIO(response.text))
        series: list[tuple[date, Decimal]] = []
        for row in reader:
            period = row.get("TIME_PERIOD")
            observed_value = row.get("OBS_VALUE")
            if period is None or observed_value is None:
                continue
            quoted_ccy_per_eur = Decimal(observed_value)
            if quoted_ccy_per_eur == ZERO:
                continue
            series.append((date.fromisoformat(period), quoted_ccy_per_eur))
        return series


class NoPerformanceDataError(ValueError):
    pass


class PerformanceReplayService:
    def __init__(
        self,
        repository: PortfolioRepository,
        settings: Settings,
        market_data_provider: MarketDataProvider,
        fx_rate_provider: FxRateProvider,
        factor_data_provider: FactorDataProvider | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._repository = repository
        self._settings = settings
        self._market_data_provider = market_data_provider
        self._fx_rate_provider = fx_rate_provider
        self._factor_data_provider = factor_data_provider or NullFactorDataProvider()
        self._clock = clock or SystemClock()

    async def replay(self, *, as_of: date | None = None) -> PerformanceReplaySummary:
        replay_input = await self._repository.load_replay_inputs()
        start_date = _min_event_date(replay_input)
        end_date = as_of or self._clock.utcnow().date()
        if start_date is None:
            return PerformanceReplaySummary(
                as_of=end_date,
                start_date=None,
                end_date=None,
                flow_timing=self._settings.analytics_flow_timing,
                holdings_written=0,
                nav_written=0,
                unsupported_quantity_events=[],
                missing_price_symbols=[],
                stale_price_symbols=[],
                missing_fx_currencies=[],
                stale_fx_currencies=[],
                excluded_flow_currencies=[],
                notes=["No replayable portfolio events found."],
            )
        end_date = max(end_date, start_date)
        instruments_by_ticker = {item.t212_ticker: item for item in replay_input.instruments}
        benchmarks = default_benchmarks(self._settings)
        price_requests = _price_symbol_requests(instruments_by_ticker, benchmarks)

        cached_prices = await self._repository.list_market_prices(
            start_date=start_date, end_date=end_date
        )
        missing_requests = _missing_price_requests(
            price_requests,
            cached_prices,
            start_date=start_date,
            end_date=end_date,
            max_stale_days=self._settings.analytics_max_price_stale_days,
        )
        fetched_prices = await self._market_data_provider.fetch_daily_closes(
            requests=missing_requests,
            start_date=start_date,
            end_date=end_date,
        )
        await self._repository.upsert_market_prices(
            _flatten_price_points(price_requests, fetched_prices)
        )
        price_map = _merge_market_price_maps(cached_prices, fetched_prices, price_requests)

        currencies = _required_currencies(instruments_by_ticker, benchmarks, replay_input)
        cached_fx = await self._repository.list_fx_rates(start_date=start_date, end_date=end_date)
        missing_fx_currencies = {
            currency
            for currency in currencies
            if not _fx_cache_covers(
                cached_fx.get(currency, []),
                start_date=start_date,
                end_date=end_date,
                max_stale_days=self._settings.analytics_max_fx_stale_days,
            )
        }
        fetched_fx = await self._fx_rate_provider.fetch_eur_base_rates(
            currencies=missing_fx_currencies,
            start_date=start_date,
            end_date=end_date,
        )
        await self._repository.upsert_fx_rates(_flatten_fx_points(fetched_fx))
        fx_map = _merge_fx_maps(cached_fx, fetched_fx)

        factor_rows = await self._factor_data_provider.fetch_factor_returns(
            start_date=start_date, end_date=end_date
        )
        await self._repository.upsert_factor_returns(_flatten_factor_points(factor_rows))

        replay_result = _build_daily_replay(
            replay_input=replay_input,
            instruments_by_ticker=instruments_by_ticker,
            market_prices=price_map,
            fx_rates=fx_map,
            start_date=start_date,
            end_date=end_date,
            max_price_stale_days=self._settings.analytics_max_price_stale_days,
            max_fx_stale_days=self._settings.analytics_max_fx_stale_days,
        )
        await self._repository.replace_daily_replay(
            holdings=replay_result.holdings, nav_rows=replay_result.nav_rows
        )
        return PerformanceReplaySummary(
            as_of=end_date,
            start_date=start_date,
            end_date=end_date,
            flow_timing=self._settings.analytics_flow_timing,
            holdings_written=len(replay_result.holdings),
            nav_written=len(replay_result.nav_rows),
            unsupported_quantity_events=sorted(replay_result.unsupported_events),
            missing_price_symbols=sorted(
                _tickers_with_status(replay_result.holdings, VALUATION_MISSING_PRICE)
            ),
            stale_price_symbols=sorted(
                _tickers_with_status(replay_result.holdings, VALUATION_STALE_PRICE)
            ),
            missing_fx_currencies=sorted(
                _currencies_with_status(replay_result.holdings, VALUATION_MISSING_FX)
            ),
            stale_fx_currencies=sorted(
                _currencies_with_status(replay_result.holdings, VALUATION_STALE_FX)
            ),
            excluded_flow_currencies=sorted(replay_result.excluded_flow_currencies),
            notes=replay_result.notes,
        )

    async def get_report(self) -> PerformanceReport:
        nav_rows = await self._repository.list_daily_nav()
        if not nav_rows:
            raise NoPerformanceDataError("No replayed NAV available")
        start_date = nav_rows[0].as_of_date
        end_date = nav_rows[-1].as_of_date
        holding_rows = await self._repository.list_daily_holdings()
        benchmarks = default_benchmarks(self._settings)
        price_map = await self._repository.list_market_prices(
            start_date=start_date, end_date=end_date
        )
        fx_map = await self._repository.list_fx_rates(start_date=start_date, end_date=end_date)
        factor_rows = await self._repository.list_factor_returns(
            start_date=start_date, end_date=end_date
        )

        flow_timing = self._settings.analytics_flow_timing
        twr_points = compute_daily_twr(nav_rows, flow_timing=flow_timing)
        returns = [point.value for point in twr_points]
        risk_free_rate = float(self._settings.analytics_risk_free_rate)

        annualized_metric = annualized_return_from_twr(twr_points)
        drawdown = compute_drawdown(nav_rows)

        benchmark_returns = {
            benchmark.key: _benchmark_returns_eur(
                price_map.get(benchmark.cache_key, []),
                fx_map,
                benchmark.currency_code,
                self._settings.analytics_max_fx_stale_days,
            )
            for benchmark in benchmarks
        }
        benchmark_reports = [
            _build_benchmark_report(benchmark, twr_points, benchmark_returns[benchmark.key])
            for benchmark in benchmarks
        ]
        passive_benchmark = next(
            benchmark
            for benchmark in benchmarks
            if benchmark.key == self._settings.analytics_passive_benchmark_key
        )
        joined_passive = inner_join_returns(twr_points, benchmark_returns[passive_benchmark.key])

        weights = _latest_weights(holding_rows)
        holding_returns = _latest_holding_returns(holding_rows)
        holding_series = _holding_return_series(holding_rows)

        return PerformanceReport(
            as_of=end_date,
            start_date=start_date,
            end_date=end_date,
            flow_timing=flow_timing,
            annualization_days=ANNUALIZATION_DAYS,
            cumulative_twr=cumulative_return_metric(twr_points),
            xirr=compute_xirr(_xirr_cash_flows(nav_rows)),
            annualized_return=annualized_metric,
            volatility=compute_volatility(returns),
            downside_volatility=compute_downside_volatility(returns),
            sharpe=compute_sharpe_ratio(returns, risk_free_rate),
            sortino=compute_sortino_ratio(returns, risk_free_rate),
            calmar=compute_calmar_ratio(annualized_metric, drawdown),
            max_drawdown=MetricValue("ok", drawdown.drawdown, len(nav_rows))
            if drawdown is not None
            else _insufficient("Need at least two valued NAV observations."),
            time_underwater_days=MetricValue(
                "ok", float(drawdown.time_underwater_days), len(nav_rows)
            )
            if drawdown is not None
            else _insufficient("Need at least two valued NAV observations."),
            recovery_days=MetricValue("ok", float(drawdown.recovery_days), len(nav_rows))
            if drawdown is not None and drawdown.recovery_days is not None
            else _insufficient("The worst drawdown has not recovered to its peak."),
            beta_vs_benchmarks=benchmark_reports,
            **_concentration_fields(weights),
            **_var_fields(returns),
            ff5_momentum_regression=ff5_momentum_regression(
                twr_points, _factor_observations(factor_rows)
            ),
            nav_series=[_nav_point(row) for row in nav_rows],
            daily_twr=twr_points,
            rolling_volatility_30d=rolling_volatility(twr_points, ROLLING_SHORT_WINDOW),
            rolling_volatility_90d=rolling_volatility(twr_points, ROLLING_LONG_WINDOW),
            rolling_beta_30d=rolling_beta(joined_passive, ROLLING_SHORT_WINDOW),
            rolling_beta_90d=rolling_beta(joined_passive, ROLLING_LONG_WINDOW),
            contributions=compute_contributions(weights, holding_returns),
            attribution=_attribution_report(),
            correlation_clusters=correlation_clusters(
                holding_series,
                weights,
                distance_threshold=self._settings.analytics_cluster_distance_threshold,
                min_observations=self._settings.analytics_min_cluster_observations,
            ),
            passive_counterfactual=compute_passive_counterfactual(
                benchmark=passive_benchmark,
                nav_rows=nav_rows,
                price_rows=price_map.get(passive_benchmark.cache_key, []),
                fx_rates=fx_map,
                max_price_stale_days=self._settings.analytics_max_price_stale_days,
                max_fx_stale_days=self._settings.analytics_max_fx_stale_days,
            ),
            notes=[
                f"Cash-flow timing convention: {flow_timing}.",
                f"Annualisation basis: {ANNUALIZATION_DAYS} calendar days "
                "(the replay emits one NAV observation per calendar day).",
                "Benchmarks are configurable ETF proxies, not official S&P/MSCI/FTSE index levels.",
                "The passive comparison is a counterfactual built from ETF proxy prices, not an "
                "achievable or advised strategy.",
                "Standard deviations use ddof=1; VaR/CVaR use historical simulation with "
                f"'{VAR_QUANTILE_METHOD}' quantile interpolation and report losses as negative "
                "returns.",
                "Risk statistics run on the calendar-day NAV series, which includes non-trading "
                "days whose prices are carried forward. Those flat days dampen volatility and can "
                "pull a 95% VaR toward zero; read the per-metric detail for the flat-day share.",
            ],
        )


def benchmark_cache_key(key: str) -> str:
    return f"__benchmark_{key}__"


def default_benchmarks(settings: Settings) -> list[BenchmarkDefinition]:
    return [
        BenchmarkDefinition(
            "cspx",
            "S&P 500 ETF proxy",
            settings.benchmark_cspx_symbol,
            settings.benchmark_cspx_currency,
            "ETF proxy for the S&P 500; not the licensed index level.",
        ),
        BenchmarkDefinition(
            "swda",
            "MSCI World ETF proxy",
            settings.benchmark_swda_symbol,
            settings.benchmark_swda_currency,
            "ETF proxy for MSCI World; not the licensed index level.",
        ),
        BenchmarkDefinition(
            "vwrp",
            "FTSE All-World ETF proxy",
            settings.benchmark_vwrp_symbol,
            settings.benchmark_vwrp_currency,
            "ETF proxy for FTSE All-World; not the licensed index level.",
        ),
    ]


# ---------------------------------------------------------------------------
# Time-weighted return
# ---------------------------------------------------------------------------


def compute_daily_twr(
    nav_rows: Sequence[DailyNav],
    *,
    flow_timing: FlowTiming = "flow_at_close",
) -> list[DailyReturnPoint]:
    """Daily time-weighted returns under an explicitly labelled flow-timing convention.

    ``flow_at_close``   the flow lands after the market closes and earns nothing that day::

        r = (NAV_t - F_t) / NAV_(t-1) - 1

    ``flow_at_open``    the flow is invested at the start of the day and earns the full day::

        r = NAV_t / (NAV_(t-1) + F_t) - 1

    ``intraday_split``  Modified Dietz with a half-day weight on the flow::

        r = (NAV_t - F_t) / (NAV_(t-1) + F_t / 2) - 1

    ``flow_at_open`` and ``flow_at_close`` are exactly flow-neutral: a day whose only event is an
    external flow returns 0.0. ``intraday_split`` is deliberately *not* - it is the Modified Dietz
    approximation, which assumes the flow was at risk for half the day, so a same-day flow moves
    the reported return. Pick it only when flows genuinely arrive through the session.
    """
    valued_rows = [row for row in nav_rows if row.nav_eur is not None]
    points: list[DailyReturnPoint] = []
    for previous, current in pairwise(valued_rows):
        previous_nav = previous.nav_eur
        current_nav = current.nav_eur
        if previous_nav is None or current_nav is None:
            continue
        flow = current.external_flow_eur
        if flow_timing == "flow_at_open":
            denominator = previous_nav + flow
            numerator = current_nav
        elif flow_timing == "intraday_split":
            denominator = previous_nav + (flow / Decimal("2"))
            numerator = current_nav - flow
        else:
            denominator = previous_nav
            numerator = current_nav - flow
        if denominator == ZERO:
            continue
        points.append(DailyReturnPoint(current.as_of_date, float((numerator / denominator) - ONE)))
    return points


def cumulative_return_metric(points: Sequence[DailyReturnPoint]) -> MetricValue:
    if not points:
        return _insufficient("Need at least one TWR observation.")
    compounded = math.prod(1.0 + item.value for item in points) - 1.0
    return MetricValue("ok", compounded, len(points))


def annualized_return_from_twr(points: Sequence[DailyReturnPoint]) -> MetricValue:
    if len(points) < 2:
        return _insufficient("Need at least two TWR observations.")
    total = math.prod(1.0 + item.value for item in points)
    if total <= 0.0:
        return MetricValue(
            "undefined",
            None,
            len(points),
            "Compounded growth is non-positive; an annualised rate is undefined.",
        )
    annualized = total ** (ANNUALIZATION_DAYS / len(points)) - 1.0
    return MetricValue(
        "ok",
        annualized,
        len(points),
        f"Geometric annualisation over {ANNUALIZATION_DAYS} calendar days.",
    )


def compute_xirr(cash_flows: Sequence[tuple[date, Decimal]]) -> MetricValue:
    if len(cash_flows) < 2:
        return _insufficient("Need at least two cash flows.")
    amounts = [float(amount) for _, amount in cash_flows]
    if min(amounts) >= 0.0 or max(amounts) <= 0.0:
        return MetricValue("no_sign_change", None, len(cash_flows), "Cash flows never change sign.")
    base_date = cash_flows[0][0]

    def npv(rate: float) -> float:
        total = 0.0
        for flow_date, amount in cash_flows:
            years = (flow_date - base_date).days / 365.0
            total += float(amount) / ((1.0 + rate) ** years)
        return total

    low = -0.999999
    high = 1.0
    low_value = npv(low)
    high_value = npv(high)
    for _ in range(12):
        if low_value == 0.0 or high_value == 0.0 or low_value * high_value < 0.0:
            break
        high *= 2.0
        high_value = npv(high)
    if low_value * high_value > 0.0:
        return MetricValue("no_bracket", None, len(cash_flows), "Unable to bracket the XIRR root.")
    root = cast(float, brentq(npv, low, high, maxiter=200))
    return MetricValue("ok", root, len(cash_flows), "Annual effective rate, ACT/365 day count.")


# ---------------------------------------------------------------------------
# Risk statistics
# ---------------------------------------------------------------------------


def compute_volatility(returns: Sequence[float]) -> MetricValue:
    if len(returns) < 2:
        return _insufficient("Need at least two return observations.", len(returns))
    value = float(np.std(np.array(returns), ddof=1) * math.sqrt(ANNUALIZATION_DAYS))
    return MetricValue("ok", value, len(returns), "Annualised sample standard deviation (ddof=1).")


def compute_downside_volatility(returns: Sequence[float]) -> MetricValue:
    downside = [item for item in returns if item < 0.0]
    if len(downside) < 2:
        return _insufficient("Need at least two negative return observations.", len(downside))
    value = float(np.std(np.array(downside), ddof=1) * math.sqrt(ANNUALIZATION_DAYS))
    return MetricValue(
        "ok", value, len(downside), "Annualised standard deviation of negative returns (ddof=1)."
    )


def compute_sharpe_ratio(returns: Sequence[float], risk_free_rate: float) -> MetricValue:
    volatility = compute_volatility(returns)
    volatility_value = volatility.value
    if volatility_value is None or volatility_value == 0.0:
        return _insufficient("Need non-zero annualised volatility.", len(returns))
    excess_mean = (float(np.mean(np.array(returns))) * ANNUALIZATION_DAYS) - risk_free_rate
    return MetricValue("ok", excess_mean / volatility_value, len(returns))


def compute_sortino_ratio(returns: Sequence[float], risk_free_rate: float) -> MetricValue:
    downside = compute_downside_volatility(returns)
    downside_value = downside.value
    if downside_value is None or downside_value == 0.0:
        return _insufficient("Need non-zero annualised downside volatility.", len(returns))
    excess_mean = (float(np.mean(np.array(returns))) * ANNUALIZATION_DAYS) - risk_free_rate
    return MetricValue("ok", excess_mean / downside_value, len(returns))


def compute_drawdown(nav_rows: Sequence[DailyNav]) -> DrawdownPoint | None:
    """Worst peak-to-trough drawdown, with recovery measured against *that* peak."""
    valued_rows = [row for row in nav_rows if row.nav_eur is not None and row.nav_eur > ZERO]
    if len(valued_rows) < 2:
        return None
    running_peak_nav = cast(Decimal, valued_rows[0].nav_eur)
    running_peak_date = valued_rows[0].as_of_date
    worst_drawdown = 0.0
    worst_peak_nav = running_peak_nav
    worst_peak_date = running_peak_date
    worst_trough_date = running_peak_date
    for row in valued_rows[1:]:
        nav = cast(Decimal, row.nav_eur)
        if nav >= running_peak_nav:
            running_peak_nav = nav
            running_peak_date = row.as_of_date
            continue
        drawdown = float((nav / running_peak_nav) - ONE)
        if drawdown < worst_drawdown:
            worst_drawdown = drawdown
            worst_peak_nav = running_peak_nav
            worst_peak_date = running_peak_date
            worst_trough_date = row.as_of_date
    if worst_drawdown == 0.0:
        return DrawdownPoint(worst_peak_date, worst_peak_date, worst_peak_date, 0.0, 0, 0)
    recovery_date: date | None = None
    for row in valued_rows:
        if row.as_of_date <= worst_trough_date or row.nav_eur is None:
            continue
        if row.nav_eur >= worst_peak_nav:
            recovery_date = row.as_of_date
            break
    end_date = recovery_date or valued_rows[-1].as_of_date
    return DrawdownPoint(
        peak_date=worst_peak_date,
        trough_date=worst_trough_date,
        recovery_date=recovery_date,
        drawdown=worst_drawdown,
        time_underwater_days=(end_date - worst_peak_date).days,
        recovery_days=(recovery_date - worst_trough_date).days if recovery_date else None,
    )


def compute_calmar_ratio(
    annualized_return: MetricValue, drawdown: DrawdownPoint | None
) -> MetricValue:
    annualized_value = annualized_return.value
    if annualized_value is None or drawdown is None or drawdown.drawdown == 0.0:
        return _insufficient("Need an annualised return and a non-zero drawdown.")
    return MetricValue(
        "ok", annualized_value / abs(drawdown.drawdown), annualized_return.observations
    )


def historical_var_cvar(returns: Sequence[float]) -> dict[str, MetricValue]:
    """Historical-simulation VaR/CVaR.

    Losses are reported as negative returns (a VaR of ``-0.03`` means a 3% loss). Quantiles use
    numpy's ``linear`` interpolation. The 10-day horizon compounds overlapping 10-day windows.
    """
    results: dict[str, MetricValue] = {}
    for confidence in (0.95, 0.99):
        for horizon in (1, 10):
            suffix = f"{int(confidence * 100)}_{horizon}d"
            sample = _horizon_returns(returns, horizon)
            required = max(MIN_VAR_OBSERVATIONS, horizon)
            if len(sample) < required:
                detail = (
                    f"Need at least {required} overlapping {horizon}-day observations; "
                    f"have {len(sample)}."
                )
                results[f"var_{suffix}"] = _insufficient(detail, len(sample))
                results[f"cvar_{suffix}"] = _insufficient(detail, len(sample))
                continue
            array = np.array(sample, dtype=float)
            var_value = float(
                np.quantile(array, 1.0 - confidence, method=VAR_QUANTILE_METHOD)  # type: ignore[call-overload]
            )
            tail = array[array <= var_value]
            flat_share = float(np.mean(array == 0.0))
            detail = (
                f"Historical simulation at {confidence:.0%} over {horizon} day(s); "
                f"loss shown as a negative return; '{VAR_QUANTILE_METHOD}' quantile interpolation; "
                f"{len(sample)} observations, {int(tail.size)} in the tail. "
                f"{flat_share:.0%} of observations are flat (non-trading calendar days carry the "
                "previous close forward), which dampens the tail."
            )
            results[f"var_{suffix}"] = MetricValue("ok", var_value, len(sample), detail)
            results[f"cvar_{suffix}"] = MetricValue("ok", float(np.mean(tail)), len(sample), detail)
    return results


# ---------------------------------------------------------------------------
# Benchmark-relative statistics
# ---------------------------------------------------------------------------


def inner_join_returns(
    portfolio_returns: Sequence[DailyReturnPoint],
    benchmark_returns: Sequence[DailyReturnPoint],
) -> list[ReturnObservation]:
    benchmark_by_date = {item.as_of_date: item.value for item in benchmark_returns}
    return [
        ReturnObservation(item.as_of_date, item.value, benchmark_by_date[item.as_of_date])
        for item in portfolio_returns
        if item.as_of_date in benchmark_by_date
    ]


def compute_beta_alpha_r2_correlation(
    joined_returns: Sequence[ReturnObservation],
) -> dict[str, MetricValue]:
    keys = ("beta", "alpha", "r2", "correlation", "tracking_error", "information_ratio")
    if len(joined_returns) < 2:
        insufficient = _insufficient(
            "Need at least two aligned portfolio/benchmark observations.", len(joined_returns)
        )
        return dict.fromkeys(keys, insufficient)
    portfolio = np.array([item.portfolio_return for item in joined_returns])
    benchmark = np.array([item.benchmark_return for item in joined_returns])
    variance = float(np.var(benchmark, ddof=1))
    if variance == 0.0:
        return dict.fromkeys(
            keys, _insufficient("Benchmark variance is zero.", len(joined_returns))
        )
    covariance = float(np.cov(portfolio, benchmark, ddof=1)[0, 1])
    beta = covariance / variance
    alpha = float(np.mean(portfolio) - beta * np.mean(benchmark)) * ANNUALIZATION_DAYS
    correlation = float(np.corrcoef(portfolio, benchmark)[0, 1])
    residual = portfolio - benchmark
    tracking_error = float(np.std(residual, ddof=1) * math.sqrt(ANNUALIZATION_DAYS))
    fitted = beta * benchmark + float(np.mean(portfolio) - beta * np.mean(benchmark))
    ss_res = float(np.sum((portfolio - fitted) ** 2))
    ss_tot = float(np.sum((portfolio - np.mean(portfolio)) ** 2))
    r_squared = 1.0 - (ss_res / ss_tot) if ss_tot != 0.0 else 0.0
    information_ratio = (
        MetricValue(
            "ok",
            float(np.mean(residual) * ANNUALIZATION_DAYS) / tracking_error,
            len(joined_returns),
        )
        if tracking_error != 0.0
        else _insufficient("Need non-zero tracking error.", len(joined_returns))
    )
    return {
        "beta": MetricValue("ok", beta, len(joined_returns)),
        "alpha": MetricValue(
            "ok", alpha, len(joined_returns), "Annualised Jensen's alpha at the stated basis."
        ),
        "r2": MetricValue("ok", r_squared, len(joined_returns)),
        "correlation": MetricValue("ok", correlation, len(joined_returns)),
        "tracking_error": MetricValue(
            "ok", tracking_error, len(joined_returns), "Annualised stdev of active returns."
        ),
        "information_ratio": information_ratio,
    }


def rolling_volatility(points: Sequence[DailyReturnPoint], window: int) -> list[DailyReturnPoint]:
    if window < 2 or len(points) < window:
        return []
    values = np.array([item.value for item in points])
    return [
        DailyReturnPoint(
            points[index].as_of_date,
            float(
                np.std(values[index - window + 1 : index + 1], ddof=1)
                * math.sqrt(ANNUALIZATION_DAYS)
            ),
        )
        for index in range(window - 1, len(points))
    ]


def rolling_beta(
    joined_returns: Sequence[ReturnObservation], window: int
) -> list[DailyReturnPoint]:
    if window < 2 or len(joined_returns) < window:
        return []
    points: list[DailyReturnPoint] = []
    for index in range(window - 1, len(joined_returns)):
        sample = joined_returns[index - window + 1 : index + 1]
        beta = compute_beta_alpha_r2_correlation(sample)["beta"]
        if beta.value is not None:
            points.append(DailyReturnPoint(sample[-1].as_of_date, beta.value))
    return points


# ---------------------------------------------------------------------------
# Composition analytics
# ---------------------------------------------------------------------------


def compute_contributions(
    weights: Mapping[str, float], returns: Mapping[str, float]
) -> list[ContributionItem]:
    """Weight x period return per holding.

    Holdings without two valued observations are reported with an explicit
    ``insufficient_data`` status; they are never silently contributed as 0.
    """
    items: list[ContributionItem] = []
    for key, weight in sorted(weights.items()):
        if key not in returns:
            items.append(
                ContributionItem(
                    key,
                    weight,
                    "insufficient_data",
                    None,
                    None,
                    "Need two consecutive valued observations for this holding.",
                )
            )
            continue
        return_value = returns[key]
        items.append(ContributionItem(key, weight, "ok", return_value, weight * return_value))
    return items


def brinson_fachler_attribution(
    portfolio_groups: Sequence[tuple[str, float, float]],
    benchmark_groups: Sequence[tuple[str, float, float]],
) -> list[AttributionItem]:
    """Brinson-Fachler attribution over the union of portfolio and benchmark sectors.

    Benchmark-only sectors appear with a zero portfolio weight so that the allocation effect of
    *not* holding them is reported. Summed total effects reconcile to the active return
    ``Rp - Rb`` whenever both weight vectors sum to 1.
    """
    portfolio_map = {key: (weight, value) for key, weight, value in portfolio_groups}
    benchmark_map = {key: (weight, value) for key, weight, value in benchmark_groups}
    benchmark_total = sum(weight * value for _, weight, value in benchmark_groups)
    items: list[AttributionItem] = []
    for key in sorted(set(portfolio_map) | set(benchmark_map)):
        benchmark_weight, benchmark_return = benchmark_map.get(key, (0.0, 0.0))
        # A sector held only by the benchmark has no portfolio return; using the benchmark's
        # return zeroes selection/interaction and leaves a pure allocation effect.
        portfolio_weight, portfolio_return = portfolio_map.get(key, (0.0, benchmark_return))
        allocation = (portfolio_weight - benchmark_weight) * (benchmark_return - benchmark_total)
        selection = benchmark_weight * (portfolio_return - benchmark_return)
        interaction = (portfolio_weight - benchmark_weight) * (portfolio_return - benchmark_return)
        items.append(
            AttributionItem(
                key,
                portfolio_weight,
                benchmark_weight,
                allocation,
                selection,
                interaction,
                allocation + selection + interaction,
            )
        )
    return items


def concentration_metrics(weights: Mapping[str, float]) -> dict[str, MetricValue]:
    if not weights:
        return dict.fromkeys(
            ("hhi", "effective", "top5"), _insufficient("Need at least one valued holding.")
        )
    values = np.array(list(weights.values()), dtype=float)
    hhi = float(np.sum(values**2))
    top5 = float(np.sum(sorted(values, reverse=True)[:5]))
    return {
        "hhi": MetricValue("ok", hhi, len(weights), "Herfindahl-Hirschman index of NAV weights."),
        "effective": MetricValue("ok", 1.0 / hhi, len(weights))
        if hhi > 0.0
        else _insufficient("HHI is zero.", len(weights)),
        "top5": MetricValue("ok", top5, len(weights)),
    }


def correlation_clusters(
    return_series: Mapping[str, Sequence[tuple[date, float]]],
    weights: Mapping[str, float],
    *,
    distance_threshold: float,
    min_observations: int,
) -> CorrelationClusterReport:
    """Cluster holdings by correlation distance ``sqrt(2 * (1 - rho))`` via average linkage."""
    keys = sorted(key for key, series in return_series.items() if len(series) >= 2)
    if len(keys) < 2:
        return CorrelationClusterReport(
            "insufficient_data",
            0,
            distance_threshold,
            None,
            [],
            "Need at least two holdings with return history.",
        )
    by_key = {key: dict(return_series[key]) for key in keys}
    common_dates = sorted(set.intersection(*(set(by_key[key]) for key in keys)))
    if len(common_dates) < min_observations:
        return CorrelationClusterReport(
            "insufficient_data",
            len(common_dates),
            distance_threshold,
            None,
            [],
            f"Need at least {min_observations} aligned observations; have {len(common_dates)}.",
        )
    matrix = np.array([[by_key[key][day] for day in common_dates] for key in keys], dtype=float)
    if np.any(np.std(matrix, axis=1) == 0.0):
        return CorrelationClusterReport(
            "insufficient_data",
            len(common_dates),
            distance_threshold,
            None,
            [],
            "At least one holding has a constant return series; correlation is undefined.",
        )
    correlation = np.corrcoef(matrix)
    distance = np.sqrt(np.clip(2.0 * (1.0 - correlation), 0.0, None))
    np.fill_diagonal(distance, 0.0)
    condensed = squareform((distance + distance.T) / 2.0, checks=False)
    labels = fcluster(
        linkage(condensed, method="average"), t=distance_threshold, criterion="distance"
    )
    assignments = [
        ClusterAssignment(key, int(labels[index]), weights.get(key, 0.0))
        for index, key in enumerate(keys)
    ]
    return CorrelationClusterReport(
        "ok",
        len(common_dates),
        distance_threshold,
        len({item.cluster for item in assignments}),
        assignments,
        "Average-linkage clustering on sqrt(2*(1-corr)) distance of daily EUR unit-price returns.",
    )


def compute_passive_counterfactual(
    *,
    benchmark: BenchmarkDefinition,
    nav_rows: Sequence[DailyNav],
    price_rows: Sequence[MarketPriceDaily],
    fx_rates: Mapping[str, Sequence[FxRateDaily]],
    max_price_stale_days: int,
    max_fx_stale_days: int,
) -> PassiveCounterfactualReport:
    """The "you, but passive" comparison.

    Every *external* contribution is invested into the configured ETF proxy at its flow date (or
    at the next available observation if the proxy had not traded yet); withdrawals redeem units
    at the same price. This is a counterfactual built from proxy prices - not an index, not an
    achievable strategy, and not advice.
    """
    empty = PassiveCounterfactualReport(
        status="unavailable",
        benchmark_key=benchmark.key,
        benchmark_label=benchmark.label,
        invested_eur=None,
        final_value_eur=None,
        actual_nav_eur=None,
        difference_eur=None,
        series=[],
        excluded_flow_count=0,
    )
    if benchmark.currency_code is None:
        return _replace_detail(
            empty,
            f"No trusted quotation currency configured for {benchmark.provider_symbol}; "
            "Helios refuses to assume one.",
        )
    price_series = _eur_price_series(
        price_rows, fx_rates, benchmark.currency_code, max_fx_stale_days
    )
    if not price_series:
        return _replace_detail(
            empty,
            f"No EUR-converted proxy prices available for {benchmark.provider_symbol}.",
        )
    units = ZERO
    invested = ZERO
    excluded = 0
    series: list[DailyValuePoint] = []
    started = False
    for row in nav_rows:
        flow = row.external_flow_eur
        if flow != ZERO:
            price = _price_at_or_next(price_series, row.as_of_date, max_price_stale_days)
            if price is None or price == ZERO:
                excluded += 1
            else:
                units += flow / price
                invested += flow
                started = True
        if not started:
            continue
        valuation_price = _price_at_or_next(price_series, row.as_of_date, max_price_stale_days)
        if valuation_price is not None:
            series.append(DailyValuePoint(row.as_of_date, units * valuation_price))
    if not series:
        return _replace_detail(
            empty, "No external contributions could be priced against the proxy series."
        )
    actual_nav = next(
        (row.nav_eur for row in reversed(nav_rows) if row.nav_eur is not None),
        None,
    )
    final_value = series[-1].value
    detail = (
        f"Counterfactual: external contributions invested in {benchmark.provider_symbol} "
        f"({benchmark.label}) at each flow date."
    )
    if excluded:
        detail += f" {excluded} flow(s) had no priceable proxy observation and were excluded."
    return PassiveCounterfactualReport(
        status="ok",
        benchmark_key=benchmark.key,
        benchmark_label=benchmark.label,
        invested_eur=invested,
        final_value_eur=final_value,
        actual_nav_eur=actual_nav,
        difference_eur=(actual_nav - final_value) if actual_nav is not None else None,
        series=series,
        excluded_flow_count=excluded,
        detail=detail,
    )


def ff5_momentum_regression(
    portfolio_returns: Sequence[DailyReturnPoint],
    factor_rows: Sequence[FactorObservation],
) -> RegressionResult:
    """Regress *excess* portfolio returns on Fama-French 5 factors plus momentum."""
    empty_coefficients: dict[str, float | None] = dict.fromkeys(FACTOR_NAMES)
    if not factor_rows:
        return RegressionResult(
            "unavailable",
            0,
            None,
            None,
            empty_coefficients,
            "No factor return data is configured (HELIOS_FACTOR_DATA_PROVIDER=disabled); "
            "Helios does not fabricate factor series.",
        )
    factor_by_date = {row.as_of_date: row for row in factor_rows}
    aligned: list[tuple[float, list[float]]] = []
    for point in portfolio_returns:
        factor_row = factor_by_date.get(point.as_of_date)
        if factor_row is None or any(name not in factor_row.factors for name in FACTOR_NAMES):
            continue
        excess_return = point.value - float(factor_row.risk_free_rate)
        aligned.append((excess_return, [float(factor_row.factors[name]) for name in FACTOR_NAMES]))
    if len(aligned) < MIN_FACTOR_OBSERVATIONS:
        return RegressionResult(
            "insufficient_data",
            len(aligned),
            None,
            None,
            empty_coefficients,
            f"Need at least {MIN_FACTOR_OBSERVATIONS} aligned observations; have {len(aligned)}.",
        )
    y = np.array([item[0] for item in aligned])
    x = np.column_stack([np.ones(len(aligned)), np.array([item[1] for item in aligned])])
    params, _, _, _ = np.linalg.lstsq(x, y, rcond=None)
    fitted = x @ params
    ss_res = float(np.sum((y - fitted) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r_squared = 1.0 - (ss_res / ss_tot) if ss_tot != 0.0 else 0.0
    coefficients: dict[str, float | None] = {
        name: float(params[index + 1]) for index, name in enumerate(FACTOR_NAMES)
    }
    return RegressionResult(
        "ok",
        len(aligned),
        r_squared,
        float(params[0]),
        coefficients,
        "OLS of excess portfolio returns (return - risk-free) on FF5 + momentum.",
    )


# ---------------------------------------------------------------------------
# Daily replay
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _PriceLookup:
    row: MarketPriceDaily | None
    provenance: str | None
    status: str


@dataclass(frozen=True)
class _FxLookup:
    rate: Decimal | None
    provenance: str | None
    status: str


@dataclass(frozen=True)
class _ReplayResult:
    holdings: list[DailyHolding]
    nav_rows: list[DailyNav]
    unsupported_events: set[str]
    excluded_flow_currencies: set[str]
    notes: list[str]


def _build_daily_replay(
    *,
    replay_input: ReplayInputData,
    instruments_by_ticker: dict[str, Instrument],
    market_prices: Mapping[str, Sequence[MarketPriceDaily]],
    fx_rates: Mapping[str, Sequence[FxRateDaily]],
    start_date: date,
    end_date: date,
    max_price_stale_days: int,
    max_fx_stale_days: int,
) -> _ReplayResult:
    quantity_deltas: dict[date, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    trade_cash: dict[date, Decimal] = defaultdict(lambda: ZERO)
    dividend_cash: dict[date, Decimal] = defaultdict(lambda: ZERO)
    external_flows: dict[date, Decimal] = defaultdict(lambda: ZERO)
    unsupported_events: set[str] = set()
    excluded_flow_currencies: set[str] = set()
    notes: set[str] = set()

    def to_eur(amount: Decimal, currency: str | None, flow_date: date) -> Decimal | None:
        code = (currency or BASE_CURRENCY).upper()
        if code == BASE_CURRENCY:
            return amount
        lookup = _lookup_fx(fx_rates.get(code, []), flow_date, max_fx_stale_days)
        if lookup.rate is None:
            excluded_flow_currencies.add(code)
            return None
        return amount * lookup.rate

    for order in sorted(
        replay_input.orders,
        key=lambda item: ((item.fill_timestamp or datetime.min.replace(tzinfo=UTC)), item.fill_id),
    ):
        if order.t212_ticker is None or order.fill_timestamp is None:
            continue
        trade_date = order.fill_timestamp.date()
        if (
            order.fill_type != "TRADE"
            or order.side not in {"BUY", "SELL"}
            or order.filled_quantity is None
        ):
            unsupported_events.add(order.t212_ticker)
            continue
        sign = ONE if order.side == "BUY" else Decimal("-1")
        quantity_deltas[trade_date][order.t212_ticker] += order.filled_quantity * sign
        if order.wallet_net_value is None:
            continue
        # walletImpact.netValue is already expressed in the wallet (account) currency; the
        # instrument-level walletFxRate must NOT be applied to it.
        converted = to_eur(order.wallet_net_value, order.wallet_currency, trade_date)
        if converted is None:
            notes.add(
                "Excluded trade cash in "
                f"{(order.wallet_currency or '?').upper()}: no FX fix within the stale cutoff."
            )
            continue
        trade_cash[trade_date] += converted * (Decimal("-1") if order.side == "BUY" else ONE)

    for dividend in replay_input.dividends:
        if dividend.paid_on is None:
            continue
        paid_date = dividend.paid_on.date()
        if dividend.amount_in_euro is not None:
            dividend_cash[paid_date] += dividend.amount_in_euro
            continue
        if dividend.amount is None:
            notes.add("Skipped a dividend row without any amount.")
            continue
        converted = to_eur(dividend.amount, dividend.currency_code, paid_date)
        if converted is None:
            notes.add(
                "Excluded dividend in "
                f"{(dividend.currency_code or '?').upper()}: no FX fix within the stale cutoff."
            )
            continue
        dividend_cash[paid_date] += converted

    dividend_transactions = 0
    for transaction in replay_input.transactions:
        if transaction.ts is None or transaction.amount is None:
            continue
        transaction_type = (transaction.transaction_type or "").upper()
        flow_date = transaction.ts.date()
        if transaction_type in EXTERNAL_FLOW_TYPES:
            converted = to_eur(transaction.amount, transaction.currency_code, flow_date)
            if converted is None:
                notes.add(
                    "Excluded external flow in "
                    f"{(transaction.currency_code or '?').upper()}: "
                    "no FX fix within the stale cutoff."
                )
                continue
            external_flows[flow_date] += converted
        elif "DIVIDEND" in transaction_type:
            dividend_transactions += 1
        else:
            notes.add(
                f"Ignored non-external cash transaction type: {transaction_type or 'UNKNOWN'}"
            )
    if dividend_transactions:
        notes.add(
            f"Skipped {dividend_transactions} dividend cash transaction(s); the dividends ledger "
            "is authoritative for dividend cash and counting both would double-count it."
        )

    holdings: list[DailyHolding] = []
    nav_rows: list[DailyNav] = []
    running_quantities: dict[str, Decimal] = defaultdict(lambda: ZERO)
    running_cash = ZERO
    instrument_currencies = _instrument_currencies(instruments_by_ticker)
    for current_date in _date_range(start_date, end_date):
        for ticker, delta in quantity_deltas[current_date].items():
            running_quantities[ticker] += delta
        running_cash += (
            external_flows[current_date] + dividend_cash[current_date] + trade_cash[current_date]
        )
        securities_value = ZERO
        missing_price_count = 0
        missing_fx_count = 0
        forward_filled = False
        for ticker, quantity in sorted(running_quantities.items()):
            if quantity == ZERO:
                continue
            currency = instrument_currencies.get(ticker, BASE_CURRENCY)
            price = _lookup_price(market_prices.get(ticker, []), current_date, max_price_stale_days)
            fx = _lookup_fx(fx_rates.get(currency, []), current_date, max_fx_stale_days)
            status = price.status
            close_price: Decimal | None = None
            market_value_local: Decimal | None = None
            market_value_eur: Decimal | None = None
            fx_rate_to_eur: Decimal | None = None
            if price.row is None:
                missing_price_count += 1
            else:
                close_price = price.row.close_price
                market_value_local = quantity * close_price
                if fx.rate is None:
                    status = fx.status
                    missing_fx_count += 1
                else:
                    fx_rate_to_eur = fx.rate
                    market_value_eur = market_value_local * fx_rate_to_eur
                    securities_value += market_value_eur
                    if PROVENANCE_FORWARD_FILL in {price.provenance, fx.provenance}:
                        status = VALUATION_FORWARD_FILL
                        forward_filled = True
                    else:
                        status = VALUATION_VALUED
            holdings.append(
                DailyHolding(
                    as_of_date=current_date,
                    t212_ticker=ticker,
                    quantity=quantity,
                    price_currency=currency,
                    close_price=close_price,
                    price_provenance=price.provenance,
                    fx_rate_to_eur=fx_rate_to_eur,
                    fx_provenance=fx.provenance if market_value_eur is not None else None,
                    market_value_local=market_value_local,
                    market_value_eur=market_value_eur,
                    valuation_status=status,
                )
            )
        fully_valued = missing_price_count == 0 and missing_fx_count == 0
        nav_rows.append(
            DailyNav(
                as_of_date=current_date,
                cash_balance_eur=running_cash,
                securities_value_eur=securities_value if fully_valued else None,
                nav_eur=running_cash + securities_value if fully_valued else None,
                external_flow_eur=external_flows[current_date],
                internal_cash_flow_eur=dividend_cash[current_date] + trade_cash[current_date],
                valuation_status=_nav_status(fully_valued, forward_filled),
                missing_price_count=missing_price_count,
                missing_fx_count=missing_fx_count,
            )
        )
    return _ReplayResult(
        holdings=holdings,
        nav_rows=nav_rows,
        unsupported_events=unsupported_events,
        excluded_flow_currencies=excluded_flow_currencies,
        notes=sorted(notes),
    )


def _nav_status(fully_valued: bool, forward_filled: bool) -> str:
    if not fully_valued:
        return VALUATION_PARTIAL
    return VALUATION_FORWARD_FILL if forward_filled else VALUATION_VALUED


def _lookup_price(
    rows: Sequence[MarketPriceDaily], lookup_date: date, max_stale_days: int
) -> _PriceLookup:
    """Carry the last close forward across weekends/holidays, bounded by ``max_stale_days``."""
    candidates = [row for row in rows if row.price_date <= lookup_date]
    if not candidates:
        return _PriceLookup(None, None, VALUATION_MISSING_PRICE)
    best = max(candidates, key=lambda row: row.price_date)
    if best.price_date == lookup_date:
        return _PriceLookup(best, PROVENANCE_EXACT, VALUATION_VALUED)
    if (lookup_date - best.price_date).days > max_stale_days:
        return _PriceLookup(None, None, VALUATION_STALE_PRICE)
    carried = MarketPriceDaily(
        price_date=lookup_date,
        t212_ticker=best.t212_ticker,
        provider_symbol=best.provider_symbol,
        currency_code=best.currency_code,
        close_price=best.close_price,
        provider=best.provider,
        source_date=best.source_date,
        provenance=PROVENANCE_FORWARD_FILL,
    )
    return _PriceLookup(carried, PROVENANCE_FORWARD_FILL, VALUATION_FORWARD_FILL)


def _lookup_fx(rows: Sequence[FxRateDaily], lookup_date: date, max_stale_days: int) -> _FxLookup:
    """ECB publishes on business days only; forward-fill within the configured cutoff."""
    candidates = [row for row in rows if row.rate_date <= lookup_date]
    if not candidates:
        return _FxLookup(None, None, VALUATION_MISSING_FX)
    best = max(candidates, key=lambda row: row.rate_date)
    if best.rate_date == lookup_date:
        return _FxLookup(best.eur_per_unit, PROVENANCE_EXACT, VALUATION_VALUED)
    if (lookup_date - best.rate_date).days > max_stale_days:
        return _FxLookup(None, None, VALUATION_STALE_FX)
    return _FxLookup(best.eur_per_unit, PROVENANCE_FORWARD_FILL, VALUATION_FORWARD_FILL)


def _date_range(start_date: date, end_date: date) -> Iterable[date]:
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


# ---------------------------------------------------------------------------
# Provider request planning and cache merging
# ---------------------------------------------------------------------------


def _price_symbol_requests(
    instruments_by_ticker: Mapping[str, Instrument],
    benchmarks: Sequence[BenchmarkDefinition],
) -> list[PriceRequest]:
    requests = [
        PriceRequest(
            ticker, instrument.yahoo_ticker, (instrument.currency_code or "").upper() or None
        )
        for ticker, instrument in sorted(instruments_by_ticker.items())
        if instrument.yahoo_ticker is not None
    ]
    requests.extend(
        PriceRequest(benchmark.cache_key, benchmark.provider_symbol, benchmark.currency_code)
        for benchmark in benchmarks
    )
    return requests


def _missing_price_requests(
    requests: Sequence[PriceRequest],
    cached_prices: Mapping[str, Sequence[MarketPriceDaily]],
    *,
    start_date: date,
    end_date: date,
    max_stale_days: int,
) -> list[PriceRequest]:
    """Decide coverage from observation boundaries, not from a calendar-day row count.

    Markets do not trade every calendar day, so "rows < days in window" always looked missing.
    A symbol counts as covered when its cached observations reach within ``max_stale_days`` of
    both window edges - exactly the window in which forward-filling is allowed to value a day.
    """
    tolerance = timedelta(days=max_stale_days)
    missing: list[PriceRequest] = []
    for request in requests:
        rows = cached_prices.get(request.key, [])
        if not rows:
            missing.append(request)
            continue
        observed = [row.price_date for row in rows]
        if min(observed) > start_date + tolerance or max(observed) < end_date - tolerance:
            missing.append(request)
    return missing


def _fx_cache_covers(
    rows: Sequence[FxRateDaily],
    *,
    start_date: date,
    end_date: date,
    max_stale_days: int,
) -> bool:
    if not rows:
        return False
    tolerance = timedelta(days=max_stale_days)
    observed = [row.rate_date for row in rows]
    return min(observed) <= start_date + tolerance and max(observed) >= end_date - tolerance


def _required_currencies(
    instruments_by_ticker: Mapping[str, Instrument],
    benchmarks: Sequence[BenchmarkDefinition],
    replay_input: ReplayInputData,
) -> set[str]:
    currencies: set[str] = set()
    currencies.update(_instrument_currencies(instruments_by_ticker).values())
    currencies.update(
        benchmark.currency_code for benchmark in benchmarks if benchmark.currency_code
    )
    currencies.update(
        (order.wallet_currency or BASE_CURRENCY).upper() for order in replay_input.orders
    )
    currencies.update(
        (dividend.currency_code or BASE_CURRENCY).upper()
        for dividend in replay_input.dividends
        if dividend.amount_in_euro is None
    )
    currencies.update(
        (transaction.currency_code or BASE_CURRENCY).upper()
        for transaction in replay_input.transactions
    )
    return {currency for currency in currencies if currency and currency != BASE_CURRENCY}


def _flatten_price_points(
    requests: Sequence[PriceRequest],
    fetched_prices: Mapping[str, Sequence[DailyPricePoint]],
) -> list[MarketPriceDaily]:
    symbols = {request.key: request.provider_symbol for request in requests}
    return [
        MarketPriceDaily(
            price_date=point.as_of_date,
            t212_ticker=key,
            provider_symbol=symbols.get(key, key),
            currency_code=point.currency_code,
            close_price=point.close_price,
            provider=point.provider,
            source_date=point.source_date,
            provenance=point.provenance,
        )
        for key, points in fetched_prices.items()
        for point in points
    ]


def _flatten_fx_points(fetched_fx: Mapping[str, Sequence[FxRatePoint]]) -> list[FxRateDaily]:
    return [
        FxRateDaily(
            rate_date=point.as_of_date,
            currency_code=currency,
            eur_per_unit=point.eur_per_unit,
            provider=point.provider,
            source_date=point.source_date,
            provenance=point.provenance,
            stale=point.stale,
        )
        for currency, points in fetched_fx.items()
        for point in points
    ]


def _flatten_factor_points(rows: Sequence[FactorObservation]) -> list[FactorReturnDaily]:
    return [
        FactorReturnDaily(
            as_of_date=row.as_of_date,
            provider=row.provider,
            risk_free_rate=row.risk_free_rate,
            mkt_rf=row.factors["mkt_rf"],
            smb=row.factors["smb"],
            hml=row.factors["hml"],
            rmw=row.factors["rmw"],
            cma=row.factors["cma"],
            mom=row.factors["mom"],
        )
        for row in rows
        if all(name in row.factors for name in FACTOR_NAMES)
    ]


def _merge_market_price_maps(
    cached_prices: Mapping[str, Sequence[MarketPriceDaily]],
    fetched_prices: Mapping[str, Sequence[DailyPricePoint]],
    requests: Sequence[PriceRequest],
) -> dict[str, list[MarketPriceDaily]]:
    """Union cached and freshly fetched observations per (key, date), preferring the fetch.

    Cached points outside the fetched range are preserved, and the provider symbol comes from the
    request rather than being back-filled with the cache key.
    """
    symbols = {request.key: request.provider_symbol for request in requests}
    merged: dict[str, dict[date, MarketPriceDaily]] = {
        key: {row.price_date: row for row in rows} for key, rows in cached_prices.items()
    }
    for key, points in fetched_prices.items():
        target = merged.setdefault(key, {})
        for point in points:
            existing = target.get(point.as_of_date)
            target[point.as_of_date] = MarketPriceDaily(
                price_date=point.as_of_date,
                t212_ticker=key,
                provider_symbol=symbols.get(
                    key, existing.provider_symbol if existing is not None else key
                ),
                currency_code=point.currency_code,
                close_price=point.close_price,
                provider=point.provider,
                source_date=point.source_date,
                provenance=point.provenance,
            )
    return {key: [rows[day] for day in sorted(rows)] for key, rows in merged.items() if rows}


def _merge_fx_maps(
    cached_fx: Mapping[str, Sequence[FxRateDaily]],
    fetched_fx: Mapping[str, Sequence[FxRatePoint]],
) -> dict[str, list[FxRateDaily]]:
    merged: dict[str, dict[date, FxRateDaily]] = {
        currency: {row.rate_date: row for row in rows} for currency, rows in cached_fx.items()
    }
    for currency, points in fetched_fx.items():
        target = merged.setdefault(currency, {})
        for point in points:
            target[point.as_of_date] = FxRateDaily(
                rate_date=point.as_of_date,
                currency_code=currency,
                eur_per_unit=point.eur_per_unit,
                provider=point.provider,
                source_date=point.source_date,
                provenance=point.provenance,
                stale=point.stale,
            )
    return {
        currency: [rows[day] for day in sorted(rows)] for currency, rows in merged.items() if rows
    }


def _instrument_currencies(instruments_by_ticker: Mapping[str, Instrument]) -> dict[str, str]:
    return {
        ticker: (instrument.currency_code or BASE_CURRENCY).upper()
        for ticker, instrument in instruments_by_ticker.items()
    }


def _min_event_date(replay_input: ReplayInputData) -> date | None:
    candidates = [
        item.fill_timestamp.date()
        for item in replay_input.orders
        if item.fill_timestamp is not None
    ]
    candidates.extend(
        item.paid_on.date() for item in replay_input.dividends if item.paid_on is not None
    )
    candidates.extend(item.ts.date() for item in replay_input.transactions if item.ts is not None)
    return min(candidates) if candidates else None


# ---------------------------------------------------------------------------
# Report helpers
# ---------------------------------------------------------------------------


def _insufficient(detail: str, observations: int = 0) -> MetricValue:
    return MetricValue("insufficient_data", None, observations, detail)


def _horizon_returns(returns: Sequence[float], horizon: int) -> list[float]:
    if horizon == 1:
        return list(returns)
    return [
        math.prod(1.0 + value for value in returns[index - horizon + 1 : index + 1]) - 1.0
        for index in range(horizon - 1, len(returns))
    ]


def _concentration_fields(weights: Mapping[str, float]) -> dict[str, MetricValue]:
    metrics = concentration_metrics(weights)
    return {
        "hhi": metrics["hhi"],
        "effective_number_of_positions": metrics["effective"],
        "top5_weight": metrics["top5"],
    }


def _var_fields(returns: Sequence[float]) -> dict[str, MetricValue]:
    metrics = historical_var_cvar(returns)
    return {
        "var_95_1d": metrics["var_95_1d"],
        "cvar_95_1d": metrics["cvar_95_1d"],
        "var_99_1d": metrics["var_99_1d"],
        "cvar_99_1d": metrics["cvar_99_1d"],
        "var_95_10d": metrics["var_95_10d"],
        "cvar_95_10d": metrics["cvar_95_10d"],
        "var_99_10d": metrics["var_99_10d"],
        "cvar_99_10d": metrics["cvar_99_10d"],
    }


def _attribution_report() -> AttributionReport:
    """Brinson-Fachler needs benchmark sector weights and sector returns.

    Helios has no licensed index-constituent feed, so the report is explicitly unavailable rather
    than an empty list dressed up as a result. :func:`brinson_fachler_attribution` stays a tested
    pure function ready for a constituent source.
    """
    return AttributionReport(
        status="unavailable",
        active_return=None,
        items=[],
        detail=(
            "Sector attribution requires benchmark constituent weights and sector returns. "
            "No licensed index-constituent source is configured, so Helios reports no numbers."
        ),
    )


def _nav_point(row: DailyNav) -> NavPoint:
    return NavPoint(
        as_of_date=row.as_of_date,
        nav_eur=row.nav_eur,
        cash_balance_eur=row.cash_balance_eur,
        securities_value_eur=row.securities_value_eur,
        external_flow_eur=row.external_flow_eur,
        valuation_status=row.valuation_status,
    )


def _factor_observations(rows: Sequence[FactorReturnDaily]) -> list[FactorObservation]:
    return [
        FactorObservation(
            as_of_date=row.as_of_date,
            provider=row.provider,
            risk_free_rate=row.risk_free_rate,
            factors={
                "mkt_rf": row.mkt_rf,
                "smb": row.smb,
                "hml": row.hml,
                "rmw": row.rmw,
                "cma": row.cma,
                "mom": row.mom,
            },
        )
        for row in rows
    ]


def _build_benchmark_report(
    definition: BenchmarkDefinition,
    portfolio_returns: Sequence[DailyReturnPoint],
    benchmark_returns: Sequence[DailyReturnPoint],
) -> BenchmarkReport:
    metrics = compute_beta_alpha_r2_correlation(
        inner_join_returns(portfolio_returns, benchmark_returns)
    )
    return BenchmarkReport(
        definition,
        metrics["beta"],
        metrics["alpha"],
        metrics["r2"],
        metrics["correlation"],
        metrics["tracking_error"],
        metrics["information_ratio"],
    )


def _eur_price_series(
    rows: Sequence[MarketPriceDaily],
    fx_rates: Mapping[str, Sequence[FxRateDaily]],
    currency_code: str | None,
    max_fx_stale_days: int,
) -> list[tuple[date, Decimal]]:
    if currency_code is None:
        return []
    currency = currency_code.upper()
    series: list[tuple[date, Decimal]] = []
    for row in sorted(rows, key=lambda item: item.price_date):
        if currency == BASE_CURRENCY:
            series.append((row.price_date, row.close_price))
            continue
        fx = _lookup_fx(fx_rates.get(currency, []), row.price_date, max_fx_stale_days)
        if fx.rate is None:
            continue
        series.append((row.price_date, row.close_price * fx.rate))
    return series


def _benchmark_returns_eur(
    rows: Sequence[MarketPriceDaily],
    fx_rates: Mapping[str, Sequence[FxRateDaily]],
    currency_code: str | None,
    max_fx_stale_days: int,
) -> list[DailyReturnPoint]:
    """Benchmark returns as an unhedged EUR investor experiences them."""
    series = _eur_price_series(rows, fx_rates, currency_code, max_fx_stale_days)
    return [
        DailyReturnPoint(current_date, float((current_price / previous_price) - ONE))
        for (_, previous_price), (current_date, current_price) in pairwise(series)
        if previous_price != ZERO
    ]


def _price_at_or_next(
    series: Sequence[tuple[date, Decimal]], lookup_date: date, max_stale_days: int
) -> Decimal | None:
    at_or_before = [item for item in series if item[0] <= lookup_date]
    if at_or_before:
        observed_date, price = max(at_or_before, key=lambda item: item[0])
        if (lookup_date - observed_date).days <= max_stale_days:
            return price
    after = [item for item in series if item[0] > lookup_date]
    if after:
        return min(after, key=lambda item: item[0])[1]
    return None


def _replace_detail(
    report: PassiveCounterfactualReport, detail: str
) -> PassiveCounterfactualReport:
    return PassiveCounterfactualReport(
        status=report.status,
        benchmark_key=report.benchmark_key,
        benchmark_label=report.benchmark_label,
        invested_eur=report.invested_eur,
        final_value_eur=report.final_value_eur,
        actual_nav_eur=report.actual_nav_eur,
        difference_eur=report.difference_eur,
        series=report.series,
        excluded_flow_count=report.excluded_flow_count,
        detail=detail,
    )


def _xirr_cash_flows(nav_rows: Sequence[DailyNav]) -> list[tuple[date, Decimal]]:
    cash_flows = [
        (row.as_of_date, -row.external_flow_eur)
        for row in nav_rows
        if row.external_flow_eur != ZERO
    ]
    terminal = next((row for row in reversed(nav_rows) if row.nav_eur is not None), None)
    if terminal is not None and terminal.nav_eur is not None:
        cash_flows.append((terminal.as_of_date, terminal.nav_eur))
    return cash_flows


def _latest_weights(holdings: Sequence[DailyHolding]) -> dict[str, float]:
    if not holdings:
        return {}
    latest_date = max(item.as_of_date for item in holdings)
    latest = [
        item
        for item in holdings
        if item.as_of_date == latest_date and item.market_value_eur is not None
    ]
    total = sum(float(item.market_value_eur or ZERO) for item in latest)
    if total == 0.0:
        return {}
    return {item.t212_ticker: float(item.market_value_eur or ZERO) / total for item in latest}


def _eur_unit_prices(holdings: Sequence[DailyHolding]) -> dict[str, list[tuple[date, float]]]:
    """Per-holding EUR price per unit, immune to quantity changes from trades."""
    series: dict[str, list[tuple[date, float]]] = defaultdict(list)
    for item in sorted(holdings, key=lambda row: (row.t212_ticker, row.as_of_date)):
        if item.market_value_eur is None or item.quantity == ZERO:
            continue
        series[item.t212_ticker].append(
            (item.as_of_date, float(item.market_value_eur / item.quantity))
        )
    return dict(series)


def _latest_holding_returns(holdings: Sequence[DailyHolding]) -> dict[str, float]:
    """Most recent per-holding period return, from consecutive valued EUR unit prices."""
    returns: dict[str, float] = {}
    for ticker, series in _eur_unit_prices(holdings).items():
        if len(series) < 2:
            continue
        previous_price = series[-2][1]
        if previous_price == 0.0:
            continue
        returns[ticker] = (series[-1][1] / previous_price) - 1.0
    return returns


def _holding_return_series(holdings: Sequence[DailyHolding]) -> dict[str, list[tuple[date, float]]]:
    series: dict[str, list[tuple[date, float]]] = {}
    for ticker, prices in _eur_unit_prices(holdings).items():
        points = [
            (current_date, (current_price / previous_price) - 1.0)
            for (_, previous_price), (current_date, current_price) in pairwise(prices)
            if previous_price != 0.0
        ]
        if points:
            series[ticker] = points
    return series


def _tickers_with_status(holdings: Sequence[DailyHolding], status: str) -> set[str]:
    return {item.t212_ticker for item in holdings if item.valuation_status == status}


def _currencies_with_status(holdings: Sequence[DailyHolding], status: str) -> set[str]:
    return {
        item.price_currency or ""
        for item in holdings
        if item.valuation_status == status and item.price_currency
    }
