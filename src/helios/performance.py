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

import asyncio
import csv
import math
import re
import time
import zipfile
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from io import BytesIO, StringIO
from itertools import pairwise
from typing import Protocol, cast

import httpx
import numpy as np
from pydantic import SecretStr
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.optimize import brentq
from scipy.spatial.distance import squareform

from .attribution import (
    AttributionConfigError,
    build_sector_groups,
    load_benchmark_sector_config,
    load_sector_overrides,
    sector_proxy_cache_key,
)
from .card_history import card_label
from .config import FlowTiming, Settings
from .models import (
    DailyHolding,
    DailyHoldingFlow,
    DailyNav,
    FactorReturnDaily,
    FxRateDaily,
    Instrument,
    MarketPriceDaily,
    Transaction,
)
from .periods import (
    PeriodSummary,
    compute_monthly_summaries,
    compute_period_summaries,
    cumulative_net_deposits,
    with_holding_movements,
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

#: Cash movements that are part of the portfolio's own return, not money moved in or out:
#: fees reduce cash, interest on free cash adds to it. They change the balance but are never
#: external flows, so TWR sees them as the gain or cost they are. Ignoring them left the
#: replayed cash a few euros away from what Trading 212 reports.
INTERNAL_CASH_TYPES = frozenset({"FEE", "INTEREST_ON_FREE_CASH", "INTEREST"})

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


PERCENT = Decimal("100")
TWELVEDATA_MAX_OUTPUTSIZE = 5000
KEN_FRENCH_DATE_PATTERN = re.compile(r"\d{8}")
# The library encodes gaps as sentinels rather than blanks. Read literally, -99.99 becomes a
# -99.99% daily factor return and destroys the regression.
KEN_FRENCH_MISSING_SENTINELS = frozenset({Decimal("-99.99"), Decimal("-999")})


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
    dividend_eur: Decimal = ZERO
    interest_eur: Decimal = ZERO
    fee_eur: Decimal = ZERO
    #: Everything put in minus everything taken out, up to and including this day.
    net_deposits_to_date_eur: Decimal = ZERO
    #: The day's money in and out, gross, with the card-payment part of what went out.
    deposit_eur: Decimal = ZERO
    withdrawal_eur: Decimal = ZERO
    card_spending_eur: Decimal = ZERO
    cashback_eur: Decimal = ZERO


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
    #: Money moved vs investment result, per standard period and per calendar month.
    period_summaries: list[PeriodSummary] = field(default_factory=list)
    monthly_summaries: list[PeriodSummary] = field(default_factory=list)


class MarketDataProvider(Protocol):
    async def fetch_daily_closes(
        self,
        *,
        requests: Sequence[PriceRequest],
        start_date: date,
        end_date: date,
        skip_notes: dict[str, str] | None = None,
    ) -> dict[str, list[DailyPricePoint]]:
        """Fetch daily closes for ``requests``, keyed by :attr:`PriceRequest.key`.

        A request this provider could not price at all -- an unknown symbol, one its plan does
        not cover, no trusted currency to value it in -- is simply absent from the returned dict.
        That is not an error: it is reported through the replay's usual missing-price accounting
        like any other gap. When the *caller* also wants to know *why* a particular symbol was
        skipped (surfaced in the replay's notes, and consulted by
        :class:`CompositeMarketDataProvider` to decide what to hand its fallback), it passes a
        dict via ``skip_notes`` and implementations fill in ``skip_notes[request.key] = reason``
        for whichever symbols they skipped. A provider that never explains itself may leave the
        dict untouched; the caller still sees "no data" from the returned mapping.

        A failure that is not specific to one symbol -- a rejected API key, an exhausted daily
        quota, a rate limit -- raises :class:`MarketDataProviderError` instead, since every other
        request in the batch would fail identically and there is nothing useful left to try.
        """
        ...


class QuoteProvider(Protocol):
    async def latest_price(self, request: PriceRequest) -> Decimal | None:
        """The latest traded price (delayed on free plans), or None when not available."""
        ...


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
        skip_notes: dict[str, str] | None = None,
    ) -> dict[str, list[DailyPricePoint]]:
        del requests, start_date, end_date, skip_notes
        return {}

    async def latest_price(self, request: PriceRequest) -> Decimal | None:
        del request
        return None


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


#: Instrument.yahoo_ticker (see resolver.py) carries Yahoo Finance's own suffix convention: a US
#: listing has none, and resolver.py replaces any "." a US ticker legitimately contains (BRK.B)
#: with "-" before it ever reaches this module -- so any "." still present in a yahoo_ticker here
#: is a genuine Yahoo exchange suffix, never a US ticker artefact. Only ".L" (London Stock
#: Exchange) is mapped today because it is the only non-US listing type this account holds
#: (VUAGl_EQ, SSLNl_EQ); extending to another exchange means adding one entry here plus one in
#: each provider's own suffix table below -- never guessing a provider's format from Yahoo's.
YAHOO_EXCHANGE_SUFFIXES: dict[str, str] = {"L": "LSE"}


def _split_yahoo_symbol(yahoo_ticker: str) -> tuple[str, str | None]:
    """Split a Yahoo-style ticker into (base symbol, canonical exchange key or None for US)."""
    if "." in yahoo_ticker:
        base, _, suffix = yahoo_ticker.rpartition(".")
        exchange = YAHOO_EXCHANGE_SUFFIXES.get(suffix.upper())
        if exchange is not None and base:
            return base, exchange
    return yahoo_ticker, None


#: Twelve Data's `exchange` query parameter takes the plain exchange name (not a MIC code).
#: Verified 2026-09-27 against https://twelvedata.com/docs#time-series ("exchange: Exchange
#: where instrument is traded", example `exchange=NASDAQ`) and
#: https://twelvedata.com/exchanges/xlon, which lists London Stock Exchange tickers such as
#: "BT.A" resolved via `exchange=LSE`. Note the free Basic plan does not reach this exchange at
#: all -- see the class docstring below -- so this table only matters once a paid Twelve Data
#: plan, or Twelve Data used as the fallback provider, is in play.
TWELVEDATA_EXCHANGE_NAMES: dict[str, str] = {"LSE": "LSE"}

#: Alpha Vantage documents non-US tickers with a market suffix. Verified 2026-09-27 against
#: https://www.alphavantage.co/documentation/: "Sample ticker traded in UK - London Stock
#: Exchange: symbol=TSCO.LON".
ALPHAVANTAGE_EXCHANGE_SUFFIXES: dict[str, str] = {"LSE": "LON"}


def _twelvedata_symbol_params(yahoo_ticker: str) -> dict[str, str]:
    """Translate a Yahoo-style symbol into Twelve Data's `symbol` (+ `exchange`) parameters."""
    base, exchange = _split_yahoo_symbol(yahoo_ticker)
    params = {"symbol": base}
    exchange_name = TWELVEDATA_EXCHANGE_NAMES.get(exchange) if exchange else None
    if exchange_name is not None:
        params["exchange"] = exchange_name
    return params


def _alphavantage_symbol(yahoo_ticker: str) -> str:
    """Translate a Yahoo-style symbol into Alpha Vantage's suffixed form.

    E.g. ``VUAG.L`` -> ``VUAG.LON``.
    """
    base, exchange = _split_yahoo_symbol(yahoo_ticker)
    suffix = ALPHAVANTAGE_EXCHANGE_SUFFIXES.get(exchange) if exchange else None
    return f"{base}.{suffix}" if suffix else yahoo_ticker


#: Twelve Data error codes that mean the *account*, not the one symbol, cannot proceed: an
#: invalid/revoked key (401) or an exhausted daily quota / rate limit (429). Every other request
#: in the same batch would fail identically, so these raise rather than being skipped.
_TWELVEDATA_ACCOUNT_ERROR_CODES = frozenset({401, 429})

#: Codes that are specific to the one symbol: not found (404), a malformed request (400), or --
#: the case that actually matters for the free tier -- a symbol outside the current plan, e.g.
#: "available starting with Grow plan" for a non-US exchange (403). Skipped, not raised, so one
#: uncovered holding does not abort pricing for the rest of the portfolio.
_TWELVEDATA_SYMBOL_ERROR_CODES = frozenset({400, 403, 404})


def _classify_twelvedata_error(payload: Mapping[str, object]) -> tuple[str, str]:
    """Classify a Twelve Data ``{"status": "error"}`` body as ("account"|"symbol", reason).

    Verified 2026-09-27 against https://twelvedata.com/docs#errors: the body always carries
    ``{"code": <int>, "message": <str>, "status": "error"}``. The code is authoritative when
    present; a missing or unrecognised code falls back to scanning the message for the same
    account-level language (a bad key or an exhausted quota), defaulting to "symbol" otherwise so
    an unfamiliar entitlement message degrades to a skip rather than aborting the whole replay.
    """
    code = payload.get("code")
    message = str(payload.get("message", "no message"))
    if isinstance(code, int):
        if code in _TWELVEDATA_ACCOUNT_ERROR_CODES:
            return "account", message
        if code in _TWELVEDATA_SYMBOL_ERROR_CODES:
            return "symbol", message
    lowered = message.lower()
    if any(term in lowered for term in ("api key", "apikey", "credit", "rate limit")):
        return "account", message
    return "symbol", message


class TwelveDataMarketDataProvider:
    """Free-tier daily closes from Twelve Data.

    Chosen over Alpha Vantage as the default primary provider: 800 credits/day against Alpha
    Vantage's 25, and -- the reason that actually matters here -- the response carries
    ``meta.currency``. Alpha Vantage reports no quotation currency, so Helios has to skip any
    symbol whose currency it cannot get from trusted metadata.

    Verified 2026-09-27 against https://twelvedata.com/pricing and
    https://twelvedata.com/exchanges/xlon: the free Basic plan's market coverage is "US equities,
    ETFs, forex and crypto" (3 markets); London Stock Exchange access starts at the paid Grow
    plan. A non-US listing on the free plan is therefore not silently mispriced -- Twelve Data
    answers with a 403 ("available starting with Grow plan" or similar), which is classified as a
    per-symbol error (see :func:`_classify_twelvedata_error`) and skipped like any other gap. This
    is exactly the case :class:`CompositeMarketDataProvider` exists for: pair this provider (US)
    with Alpha Vantage as the fallback (LSE) to cover both without paying for either.
    """

    def __init__(self, settings: Settings, *, api_key: SecretStr | None = None) -> None:
        self._settings = settings
        #: Defaults to the primary credential so existing single-provider construction is
        #: unchanged; the fallback wiring in dependencies.py passes the second credential
        #: explicitly so two provider instances never race to read the same settings field.
        self._api_key = api_key if api_key is not None else settings.market_data_api_key
        self._client = httpx.AsyncClient(timeout=settings.market_data_timeout_seconds)
        self._pacer = RequestPacer(settings.twelvedata_min_interval_seconds)
        self._covered_exchanges = settings.twelvedata_exchange_keys

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch_daily_closes(
        self,
        *,
        requests: Sequence[PriceRequest],
        start_date: date,
        end_date: date,
        skip_notes: dict[str, str] | None = None,
    ) -> dict[str, list[DailyPricePoint]]:
        api_key = self._api_key
        if api_key is None:
            return {}
        results: dict[str, list[DailyPricePoint]] = {}
        for request in requests:
            _, exchange = _split_yahoo_symbol(request.provider_symbol)
            if (exchange or "US") not in self._covered_exchanges:
                # Outside the plan: asking would spend a rate-limited request on a certain refusal.
                if skip_notes is not None:
                    skip_notes[request.key] = (
                        f"Twelve Data: {exchange} listings are outside the configured plan "
                        "(HELIOS_TWELVEDATA_EXCHANGES); left for the fallback provider"
                    )
                continue
            response = await _provider_get(
                self._client,
                self._settings.twelvedata_base_url,
                params={
                    **_twelvedata_symbol_params(request.provider_symbol),
                    "interval": "1day",
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "outputsize": str(TWELVEDATA_MAX_OUTPUTSIZE),
                    "format": "JSON",
                    "apikey": api_key.get_secret_value(),
                },
                provider="Twelve Data",
                pacer=self._pacer,
            )
            payload = response.json()
            if not isinstance(payload, dict):
                continue
            # Twelve Data signals errors with HTTP 200 and {"status": "error"}, so raise_for_status
            # alone would let a quota or entitlement failure through as "no data" -- indistinguish-
            # able from a genuinely empty series.
            if payload.get("status") == "error":
                kind, reason = _classify_twelvedata_error(payload)
                if kind == "account":
                    raise MarketDataProviderError(
                        f"Twelve Data rejected the request: {reason} (code {payload.get('code')})"
                    )
                if skip_notes is not None:
                    skip_notes[request.key] = f"Twelve Data: {reason} (code {payload.get('code')})"
                continue
            currency = self._resolve_currency(request, payload)
            if currency is None:
                if skip_notes is not None and request.currency_code is not None:
                    skip_notes[request.key] = (
                        "Twelve Data: reported currency did not match the trusted "
                        f"{request.currency_code!r}; refusing to mis-value"
                    )
                continue
            values = payload.get("values")
            if not isinstance(values, list):
                continue
            points: list[DailyPricePoint] = []
            for raw in values:
                if not isinstance(raw, dict):
                    continue
                stamp = raw.get("datetime")
                close_value = raw.get("close")
                if not isinstance(stamp, str) or not isinstance(close_value, str):
                    continue
                point_date = date.fromisoformat(stamp[:10])
                if point_date < start_date or point_date > end_date:
                    continue
                points.append(
                    DailyPricePoint(
                        as_of_date=point_date,
                        close_price=Decimal(close_value),
                        currency_code=currency,
                        provider="twelvedata",
                        source_date=point_date,
                        provenance=PROVENANCE_EXACT,
                    )
                )
            results[request.key] = sorted(points, key=lambda item: item.as_of_date)
        return results

    async def latest_price(self, request: PriceRequest) -> Decimal | None:
        _, exchange = _split_yahoo_symbol(request.provider_symbol)
        if (exchange or "US") not in self._covered_exchanges:
            return None
        return await _twelvedata_latest(self, request)

    @staticmethod
    def _resolve_currency(request: PriceRequest, payload: Mapping[str, object]) -> str | None:
        """Reconcile the configured currency against the one the provider reports.

        Two failure modes are worth separating. If Helios holds trusted metadata and the provider
        disagrees, that is a mapping error -- valuing a GBX series as GBP would overstate by 100x
        -- so the symbol is dropped rather than trusted. If Helios holds no currency, the
        provider's own ``meta.currency`` is used: that is reported data, not the assumption the
        no-guessing rule exists to prevent.
        """
        meta = payload.get("meta")
        reported = meta.get("currency") if isinstance(meta, Mapping) else None
        reported_code = reported.upper() if isinstance(reported, str) and reported else None
        if request.currency_code is None:
            return reported_code
        configured = request.currency_code.upper()
        if reported_code is not None and reported_code != configured:
            return None
        return configured


class KenFrenchFactorDataProvider:
    """Daily FF5 + momentum factors from the Kenneth French Data Library.

    The official source, free, and no account -- which is why the factor regression no longer
    needs a licensed feed. Two zipped CSVs are joined on date: the 5-factor file supplies
    Mkt-RF/SMB/HML/RMW/CMA and the risk-free rate, the momentum file supplies Mom.

    Two properties of these files drive the parsing below. Values are quoted in **percent**, so
    they are divided by 100 -- skipping that would inflate every loading by 100x. And missing
    observations are sentinels (-99.99 / -999) rather than blanks, so they are dropped instead of
    being read as catastrophic single-day returns.

    Note the publication lag: the library is updated monthly and trails the present by roughly a
    month. The regression therefore covers only the overlap between your NAV history and the
    published factors, and reports ``insufficient_data`` when that overlap is too short -- which
    is the intended behaviour, not a fetch failure.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(
            timeout=settings.market_data_timeout_seconds, follow_redirects=True
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch_factor_returns(
        self,
        *,
        start_date: date,
        end_date: date,
    ) -> list[FactorObservation]:
        five = await self._fetch_table(self._settings.ken_french_five_factor_url)
        momentum = await self._fetch_table(self._settings.ken_french_momentum_url)
        observations: list[FactorObservation] = []
        for as_of, row in sorted(five.items()):
            if as_of < start_date or as_of > end_date:
                continue
            mom_row = momentum.get(as_of)
            if mom_row is None:
                # The momentum file is published separately and can lag the 5-factor file by a
                # day. An observation missing a factor is dropped, never zero-filled.
                continue
            try:
                factors = {
                    "mkt_rf": row["Mkt-RF"],
                    "smb": row["SMB"],
                    "hml": row["HML"],
                    "rmw": row["RMW"],
                    "cma": row["CMA"],
                    "mom": mom_row["Mom"],
                }
            except KeyError:
                continue
            risk_free = row.get("RF")
            if risk_free is None:
                continue
            observations.append(
                FactorObservation(
                    as_of_date=as_of,
                    provider="kenfrench",
                    risk_free_rate=risk_free,
                    factors=factors,
                )
            )
        return observations

    async def _fetch_table(self, url: str) -> dict[date, dict[str, Decimal]]:
        response = await self._client.get(url)
        response.raise_for_status()
        return parse_ken_french_csv(response.content)


def parse_ken_french_csv(payload: bytes) -> dict[date, dict[str, Decimal]]:
    """Parse a Kenneth French zipped daily CSV into {date: {column: decimal fraction}}.

    The files open with several lines of prose, then a header row whose first cell is empty, then
    ``YYYYMMDD,value,...`` rows, then a blank line and a copyright notice. Parsing therefore
    starts at the header row and stops at the first row whose first cell is not an 8-digit date,
    which also guards against the trailing annual block some files in this library carry.
    """
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        names = archive.namelist()
        if not names:
            raise FactorDataFormatError("Ken French archive is empty")
        text = archive.read(names[0]).decode("latin-1")

    header: list[str] | None = None
    table: dict[date, dict[str, Decimal]] = {}
    for line in text.splitlines():
        cells = [cell.strip() for cell in line.split(",")]
        if header is None:
            # The header is the first row that starts with an empty cell and names columns.
            if len(cells) > 1 and cells[0] == "" and any(cells[1:]):
                header = cells
            continue
        if not KEN_FRENCH_DATE_PATTERN.fullmatch(cells[0]):
            if table:
                break
            continue
        values: dict[str, Decimal] = {}
        for column, cell in zip(header[1:], cells[1:], strict=False):
            if not cell:
                continue
            value = Decimal(cell)
            if value in KEN_FRENCH_MISSING_SENTINELS:
                continue
            values[column] = value / PERCENT
        table[datetime.strptime(cells[0], "%Y%m%d").date()] = values
    if header is None:
        raise FactorDataFormatError("Ken French CSV had no recognisable header row")
    return table


#: Alpha Vantage signals a request-specific failure and an account-wide one with different body
#: keys, both under HTTP 200. Verified 2026-09-27 against
#: https://www.alphavantage.co/documentation/ and https://www.alphavantage.co/support/#api-key:
#:
#: * ``"Error Message"`` -- the request itself could not be fulfilled (unknown symbol, malformed
#:   parameter). Specific to the one symbol: skipped.
#: * ``"Note"`` / ``"Information"`` -- the free key's request-rate or daily-quota ceiling (5
#:   requests/minute, 25 requests/day at the time of writing) was hit, or the endpoint needs a
#:   paid plan. Every other request in this batch would fail the same way: raised.
def _classify_alphavantage_payload(payload: Mapping[str, object]) -> tuple[str, str] | None:
    """Return ("account"|"symbol", reason), or None when ``payload`` is not an error at all."""
    error_message = payload.get("Error Message")
    if isinstance(error_message, str) and error_message:
        return "symbol", error_message
    note = payload.get("Note")
    if isinstance(note, str) and note:
        return "account", note
    information = payload.get("Information")
    if isinstance(information, str) and information:
        return "account", information
    return None


class AlphaVantageMarketDataProvider:
    """Free-tier daily closes from Alpha Vantage.

    Used as the fallback provider for symbols the primary (normally Twelve Data's free plan)
    cannot price -- chiefly non-US listings such as this account's two LSE holdings.

    Verified 2026-09-27 against https://www.alphavantage.co/documentation/:

    * ``TIME_SERIES_DAILY_ADJUSTED`` is premium-only ("this is a premium API function. Subscribe
      to a premium membership plan to instantly unlock all premium APIs"). Only the unadjusted
      ``TIME_SERIES_DAILY`` is free, so that is what this provider calls; there is no split-
      adjusted close in its response, only ``"4. close"``.
    * ``outputsize=full`` (the entire history) is also premium. ``outputsize=compact`` -- "the
      latest 100 data points" -- is the free default and what this provider requests. In
      practice that is roughly the last 100 *trading* days per call. Anything older has to
      already be in Helios's own cache (``market_prices_daily``): the replay only ever asks a
      provider for the slice ``_missing_price_requests`` says is actually missing, so a backfill
      beyond ~100 trading days will show as ``insufficient_data`` until enough daily runs have
      accumulated cache coverage, or until a paid key is used once to seed it.
    * Non-US tickers carry a market suffix, e.g. "Sample ticker traded in UK - London Stock
      Exchange: symbol=TSCO.LON" -- see :func:`_alphavantage_symbol`.

    Quota math for daily use: the free key allows 25 requests/day. With the price cache covering
    everything already fetched (see ``_missing_price_requests``), a steady-state day only
    re-requests symbols whose cache fell stale -- for this account's two LSE holdings, at most 2
    of the 25 -- comfortably inside the ceiling even counting the 5-requests/minute burst limit,
    since the replay issues these sequentially with real network latency between them rather than
    back-to-back.
    """

    def __init__(self, settings: Settings, *, api_key: SecretStr | None = None) -> None:
        self._settings = settings
        self._api_key = api_key if api_key is not None else settings.market_data_api_key
        self._client = httpx.AsyncClient(timeout=settings.market_data_timeout_seconds)
        self._pacer = RequestPacer(settings.alphavantage_min_interval_seconds)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch_daily_closes(
        self,
        *,
        requests: Sequence[PriceRequest],
        start_date: date,
        end_date: date,
        skip_notes: dict[str, str] | None = None,
    ) -> dict[str, list[DailyPricePoint]]:
        api_key = self._api_key
        if api_key is None:
            return {}
        results: dict[str, list[DailyPricePoint]] = {}
        for request in requests:
            if request.currency_code is None:
                # Alpha Vantage's daily series does not report a quotation currency. Without
                # trusted metadata the close would have to be assumed (historically: USD), which
                # would silently mis-value non-USD listings. Skip instead.
                if skip_notes is not None:
                    skip_notes[request.key] = (
                        "Alpha Vantage: no trusted instrument currency configured for "
                        f"{request.provider_symbol!r}; refusing to guess"
                    )
                continue
            response = await _provider_get(
                self._client,
                self._settings.market_data_base_url,
                params={
                    "function": "TIME_SERIES_DAILY",
                    "outputsize": "compact",
                    "datatype": "json",
                    "symbol": _alphavantage_symbol(request.provider_symbol),
                    "apikey": api_key.get_secret_value(),
                },
                provider="Alpha Vantage",
                pacer=self._pacer,
            )
            payload = response.json()
            if not isinstance(payload, dict):
                continue
            classification = _classify_alphavantage_payload(payload)
            if classification is not None:
                kind, reason = classification
                if kind == "account":
                    raise MarketDataProviderError(f"Alpha Vantage rejected the request: {reason}")
                if skip_notes is not None:
                    skip_notes[request.key] = f"Alpha Vantage: {reason}"
                continue
            series = payload.get("Time Series (Daily)")
            if not isinstance(series, dict):
                if skip_notes is not None:
                    skip_notes[request.key] = "Alpha Vantage: response had no daily series"
                continue
            points: list[DailyPricePoint] = []
            for key, raw in series.items():
                point_date = date.fromisoformat(key)
                if point_date < start_date or point_date > end_date:
                    continue
                if not isinstance(raw, Mapping):
                    continue
                close_value = raw.get("4. close")
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

    async def latest_price(self, request: PriceRequest) -> Decimal | None:
        """Not offered: 25 free calls a day cannot be spent on intraday quotes."""
        del request
        return None


#: Yahoo exchange suffix -> EODHD exchange code. No suffix is a US listing.
EODHD_EXCHANGES: dict[str | None, str] = {
    None: "US",
    "L": "LSE",
    "IL": "IL",
    "DE": "XETRA",
    "F": "F",
    "PA": "PA",
    "AS": "AS",
    "BR": "BR",
    "LS": "LS",
    "MI": "MI",
    "MC": "MC",
    "SW": "SW",
    "VI": "VI",
    "ST": "ST",
    "CO": "CO",
    "OL": "OL",
    "HE": "HE",
    "IR": "IR",
    "TO": "TO",
    "V": "V",
    "HK": "HK",
    "AX": "AU",
    "T": "TSE",
}

#: Account-level refusals: every other symbol in the run would fail the same way, so these
#: stop the fetch. 401 is a bad token; 402 is EODHD's "daily limit reached / plan required".
_EODHD_ACCOUNT_STATUSES = frozenset({401, 402})
#: Symbol-level refusals: not in the database (404) or not in this plan (403). Skipped.
_EODHD_SYMBOL_STATUSES = frozenset({403, 404})


def _eodhd_symbol(yahoo_ticker: str) -> str | None:
    """``VUAG.L`` -> ``VUAG.LSE``, ``MU`` -> ``MU.US``; None for an exchange EODHD's map lacks.

    A Yahoo symbol's text after the last dot is its exchange (share classes use a hyphen there,
    ``BRK-B``), so any unmapped suffix is refused rather than sent as if it were a US ticker.
    """
    base, dot, suffix = yahoo_ticker.rpartition(".")
    exchange: str | None = suffix.upper() if dot and base else None
    code = EODHD_EXCHANGES.get(exchange)
    symbol = base if exchange is not None else yahoo_ticker
    return f"{symbol}.{code}" if code else None


class EodhdMarketDataProvider:
    """Daily closes from EODHD's end-of-day API.

    Verified 2026-09-28 against https://eodhd.com/financial-apis/api-for-historical-data-and-volumes:
    ``GET /api/eod/{SYMBOL}.{EXCHANGE}?api_token=...&fmt=json&period=d&from=...&to=...`` returns
    a list of ``{date, open, high, low, close, adjusted_close, volume}``. ``close`` is the raw
    printed close -- what this provider stores, like the others, because the replay values
    actual share counts and an adjusted close would double-count splits and dividends.

    One request per symbol covers the whole missing window, so a first backfill of a London ETF
    costs one call instead of the ~100-trading-day slices Alpha Vantage's free plan allows. The
    quote currency is not in the response; it comes from Trading 212's instrument metadata, and
    a symbol without it is skipped rather than guessed (LSE lines quote in GBP or in pence).
    """

    def __init__(self, settings: Settings, *, api_key: SecretStr | None = None) -> None:
        self._settings = settings
        self._api_key = api_key if api_key is not None else settings.market_data_api_key
        self._client = httpx.AsyncClient(timeout=settings.market_data_timeout_seconds)
        self._pacer = RequestPacer(settings.eodhd_min_interval_seconds)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch_daily_closes(
        self,
        *,
        requests: Sequence[PriceRequest],
        start_date: date,
        end_date: date,
        skip_notes: dict[str, str] | None = None,
    ) -> dict[str, list[DailyPricePoint]]:
        api_key = self._api_key
        if api_key is None:
            return {}
        results: dict[str, list[DailyPricePoint]] = {}
        for request in requests:
            symbol = _eodhd_symbol(request.provider_symbol)
            if symbol is None or request.currency_code is None:
                if skip_notes is not None:
                    skip_notes[request.key] = (
                        f"EODHD: no exchange mapping for {request.provider_symbol!r}"
                        if symbol is None
                        else "EODHD: no trusted instrument currency configured; refusing to guess"
                    )
                continue
            response = await _provider_get(
                self._client,
                f"{self._settings.eodhd_base_url.rstrip('/')}/eod/{symbol}",
                params={
                    "api_token": api_key.get_secret_value(),
                    "fmt": "json",
                    "period": "d",
                    "order": "a",
                    "from": start_date.isoformat(),
                    "to": end_date.isoformat(),
                },
                provider="EODHD",
                pacer=self._pacer,
                passthrough=_EODHD_ACCOUNT_STATUSES | _EODHD_SYMBOL_STATUSES,
            )
            if response.status_code in _EODHD_ACCOUNT_STATUSES:
                raise MarketDataProviderError(
                    "EODHD rejected the API key (HTTP 401)."
                    if response.status_code == 401
                    else "EODHD refused the request (HTTP 402): the plan's daily limit is used up "
                    "or the data needs a higher plan."
                )
            if response.status_code in _EODHD_SYMBOL_STATUSES:
                if skip_notes is not None:
                    skip_notes[request.key] = (
                        f"EODHD: {symbol} not found"
                        if response.status_code == 404
                        else f"EODHD: {symbol} is not included in this plan"
                    )
                continue
            payload = response.json()
            if not isinstance(payload, list):
                if skip_notes is not None:
                    skip_notes[request.key] = "EODHD: response was not a list of daily bars"
                continue
            points: list[DailyPricePoint] = []
            for raw in payload:
                if not isinstance(raw, Mapping):
                    continue
                raw_date, close = raw.get("date"), raw.get("close")
                if not isinstance(raw_date, str) or not isinstance(close, (int, float, str)):
                    continue
                point_date = date.fromisoformat(raw_date)
                if point_date < start_date or point_date > end_date:
                    continue
                points.append(
                    DailyPricePoint(
                        as_of_date=point_date,
                        close_price=Decimal(str(close)),
                        currency_code=request.currency_code,
                        provider="eodhd",
                        source_date=point_date,
                        provenance=PROVENANCE_EXACT,
                    )
                )
            results[request.key] = sorted(points, key=lambda item: item.as_of_date)
        return results

    async def latest_price(self, request: PriceRequest) -> Decimal | None:
        """EODHD's real-time endpoint (delayed ~15 minutes): ``{"close": ...}``."""

        symbol = _eodhd_symbol(request.provider_symbol)
        if self._api_key is None or symbol is None:
            return None
        response = await _provider_get(
            self._client,
            f"{self._settings.eodhd_base_url.rstrip('/')}/real-time/{symbol}",
            params={"api_token": self._api_key.get_secret_value(), "fmt": "json"},
            provider="EODHD",
            pacer=self._pacer,
            passthrough=_EODHD_ACCOUNT_STATUSES | _EODHD_SYMBOL_STATUSES,
        )
        if response.status_code >= 400:
            return None
        payload = response.json()
        close = payload.get("close") if isinstance(payload, dict) else None
        if not isinstance(close, (int, float, str)) or str(close) in {"NA", ""}:
            return None
        return Decimal(str(close))


async def _twelvedata_latest(
    provider: TwelveDataMarketDataProvider, request: PriceRequest
) -> Decimal | None:
    """Twelve Data's ``/price`` endpoint: one credit, the latest price in ``{"price": "..."}``."""

    api_key = provider._api_key
    if api_key is None:
        return None
    root = provider._settings.twelvedata_base_url.rsplit("/", 1)[0]
    response = await _provider_get(
        provider._client,
        f"{root}/price",
        params={
            **_twelvedata_symbol_params(request.provider_symbol),
            "apikey": api_key.get_secret_value(),
        },
        provider="Twelve Data",
        pacer=provider._pacer,
    )
    payload = response.json()
    price = payload.get("price") if isinstance(payload, dict) else None
    return Decimal(str(price)) if isinstance(price, (str, int, float)) else None


class CompositeMarketDataProvider:
    """Ask a primary provider for every symbol, then ask a fallback only for what it missed.

    This is the "Twelve Data for US + Alpha Vantage for London" setup: the primary (typically
    Twelve Data, 800 free credits/day, US-only on the free plan) covers the bulk of a portfolio
    cheaply, and the fallback (typically Alpha Vantage, 25 free credits/day) is spent only on the
    handful of symbols the primary's plan does not reach. Never the reverse -- the fallback is
    never asked for a symbol the primary already priced -- so a tiny daily quota lasts.

    "What the primary missed" is judged strictly from its return value: any request whose key is
    absent, or maps to an empty list, in the primary's result. That covers every way a provider
    can fail to price a symbol -- an explicit per-symbol skip (recorded in ``skip_notes`` if the
    primary chose to explain itself), a currency mismatch, or simply no rows in the response --
    without this class needing to know *why*.

    An account-level failure (bad key, exhausted quota) is not caught here: it propagates from
    whichever provider raised it. Silently swallowing that and handing everything to the other
    provider would burn through the fallback's much smaller quota for symbols the primary should
    have been able to serve, and would hide a configuration problem the operator needs to see.
    """

    def __init__(self, primary: MarketDataProvider, fallback: MarketDataProvider) -> None:
        self._primary = primary
        self._fallback = fallback

    async def aclose(self) -> None:
        for provider in (self._primary, self._fallback):
            close = getattr(provider, "aclose", None)
            if close is not None:
                await close()

    async def latest_price(self, request: PriceRequest) -> Decimal | None:
        for provider in (self._primary, self._fallback):
            quote = getattr(provider, "latest_price", None)
            if quote is None:
                continue
            try:
                price = await quote(request)
            except MarketDataProviderError:
                price = None
            if price is not None:
                return cast(Decimal, price)
        return None

    async def fetch_daily_closes(
        self,
        *,
        requests: Sequence[PriceRequest],
        start_date: date,
        end_date: date,
        skip_notes: dict[str, str] | None = None,
    ) -> dict[str, list[DailyPricePoint]]:
        primary_notes: dict[str, str] = {}
        primary_results = await self._primary.fetch_daily_closes(
            requests=requests,
            start_date=start_date,
            end_date=end_date,
            skip_notes=primary_notes,
        )
        if skip_notes is not None:
            skip_notes.update(primary_notes)

        unresolved = [request for request in requests if not primary_results.get(request.key)]
        if not unresolved:
            return primary_results

        fallback_notes: dict[str, str] = {}
        fallback_results = await self._fallback.fetch_daily_closes(
            requests=unresolved,
            start_date=start_date,
            end_date=end_date,
            skip_notes=fallback_notes,
        )

        merged = dict(primary_results)
        for request in unresolved:
            points = fallback_results.get(request.key)
            if points:
                merged[request.key] = points
                if skip_notes is not None:
                    skip_notes.pop(request.key, None)
            elif skip_notes is not None:
                reason = fallback_notes.get(request.key, "no data returned")
                primary_reason = primary_notes.get(request.key)
                skip_notes[request.key] = (
                    f"{primary_reason}; fallback also failed: {reason}"
                    if primary_reason
                    else reason
                )
        return merged


class UnknownCurrencyError(ValueError):
    """A holding is quoted in a currency Helios cannot convert to EUR."""


class MarketDataProviderError(RuntimeError):
    """A market-data provider refused a request (quota, entitlement, bad symbol).

    The message is shown to the operator and written to logs, so it must never contain a
    request URL: both providers take the API key as a query parameter.
    """


class RequestPacer:
    """Keeps at least ``interval`` seconds between one provider's requests.

    Free market-data plans are metered per minute as well as per day. Sending a portfolio's
    fifteen symbols back to back got Twelve Data's HTTP 429 on the ninth, and the whole replay
    failed; spacing them out costs a minute or two on a first replay and nothing afterwards,
    because later replays only request the days the price cache is missing.
    """

    def __init__(
        self,
        interval: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._interval = max(0.0, interval)
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None

    async def wait(self) -> None:
        if self._last is not None:
            remaining = self._interval - (self._clock() - self._last)
            if remaining > 0:
                await self._sleep(remaining)
        self._last = self._clock()


#: After an HTTP 429, wait this long before the single retry: the per-minute window resets.
RATE_LIMIT_RETRY_SECONDS = 61.0


async def _provider_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: Mapping[str, str],
    provider: str,
    pacer: RequestPacer,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    passthrough: frozenset[int] = frozenset(),
) -> httpx.Response:
    """One paced GET, with a single wait-and-retry on HTTP 429 and URL-free errors.

    ``raise_for_status`` puts the full request URL -- API key included -- into its message,
    and that message reached the dashboard and the API log verbatim. Every failure here is
    rewritten to name the provider and status only, with the original chain suppressed.
    """

    for attempt in range(2):
        await pacer.wait()
        try:
            response = await client.get(url, params=dict(params))
        except httpx.HTTPError as exc:
            raise MarketDataProviderError(
                f"{provider} could not be reached ({type(exc).__name__}). Check your connection "
                "and try again."
            ) from None
        if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
            if attempt == 0:
                await sleep(RATE_LIMIT_RETRY_SECONDS)
                continue
            raise MarketDataProviderError(
                f"{provider} is rate-limiting requests (HTTP 429): the free plan's per-minute or "
                "daily allowance is used up. Wait a minute and run the replay again."
            )
        if response.status_code >= 400 and response.status_code not in passthrough:
            raise MarketDataProviderError(f"{provider} returned HTTP {response.status_code}.")
        return response
    raise AssertionError("unreachable")


class FactorDataFormatError(ValueError):
    """A factor-data file did not have the structure Helios knows how to read."""


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
        """Rebuild the daily replay under an exclusive lease.

        Replay rewrites the whole ``daily_holdings``/``daily_nav`` pair, so two concurrent
        runs would duplicate every provider fetch -- burning a metered market-data quota --
        and race to be the last writer. The lease makes the second caller fail fast with
        :class:`SyncAlreadyRunningError` instead.
        """
        started_at = self._clock.utcnow()
        lease = await self._repository.acquire_performance_replay_lease(
            acquired_at=started_at,
            lease_minutes=self._settings.sync_lease_minutes,
        )
        try:
            summary = await self._replay_without_lease(as_of=as_of)
        except BaseException as exc:
            await self._repository.release_performance_replay_lease(
                lease=lease,
                completed_at=self._clock.utcnow(),
                succeeded=False,
                error_message=exc.__class__.__name__,
            )
            raise
        await self._repository.release_performance_replay_lease(
            lease=lease,
            completed_at=self._clock.utcnow(),
            succeeded=True,
            error_message=None,
        )
        return summary

    async def _replay_without_lease(
        self, *, as_of: date | None = None
    ) -> PerformanceReplaySummary:
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
        sector_proxy_requests = _sector_proxy_price_requests(self._settings)
        price_requests = [
            *_price_symbol_requests(instruments_by_ticker, benchmarks),
            *sector_proxy_requests,
        ]

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
        price_skip_notes: dict[str, str] = {}
        fetched_prices = await self._market_data_provider.fetch_daily_closes(
            requests=missing_requests,
            start_date=start_date,
            end_date=end_date,
            skip_notes=price_skip_notes,
        )
        await self._repository.upsert_market_prices(
            _flatten_price_points(price_requests, fetched_prices)
        )
        price_map = _merge_market_price_maps(cached_prices, fetched_prices, price_requests)

        currencies = _required_currencies(
            instruments_by_ticker, benchmarks, replay_input
        ) | {
            request.currency_code.upper()
            for request in sector_proxy_requests
            if request.currency_code and request.currency_code.upper() != BASE_CURRENCY
        }
        # FX is looked up by carrying the last fix forward, so the window must open *before* the
        # first event: a history that starts on a Saturday (or a bank holiday) otherwise has no
        # earlier fix to carry, and that day's foreign-currency deposit was silently excluded.
        # Opening by the stale cutoff is exactly as far back as a carried fix may come from.
        fx_start = start_date - timedelta(days=self._settings.analytics_max_fx_stale_days)
        cached_fx = await self._repository.list_fx_rates(start_date=fx_start, end_date=end_date)
        missing_fx_currencies = {
            currency
            for currency in currencies
            if not _fx_cache_covers(
                cached_fx.get(currency, []),
                start_date=fx_start,
                end_date=end_date,
                max_stale_days=self._settings.analytics_max_fx_stale_days,
            )
        }
        fetched_fx = await self._fx_rate_provider.fetch_eur_base_rates(
            currencies=missing_fx_currencies,
            start_date=fx_start,
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
            holdings=replay_result.holdings,
            nav_rows=replay_result.nav_rows,
            flows=replay_result.flows,
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
            notes=sorted(
                set(replay_result.notes)
                | {
                    f"Price fetch skipped {key}: {reason}"
                    for key, reason in price_skip_notes.items()
                }
            ),
        )

    async def get_report(self) -> PerformanceReport:
        nav_rows = await self._repository.list_daily_nav()
        if not nav_rows:
            raise NoPerformanceDataError("No replayed NAV available")
        start_date = nav_rows[0].as_of_date
        end_date = nav_rows[-1].as_of_date
        holding_rows = await self._repository.list_daily_holdings()
        holding_flows = await self._repository.list_daily_holding_flows()
        instruments_by_ticker = await self._repository.get_cached_instruments_by_tickers(
            {row.t212_ticker for row in holding_rows} | {row.t212_ticker for row in holding_flows}
        )
        holding_names = {
            ticker: instrument.name or instrument.short_name
            for ticker, instrument in instruments_by_ticker.items()
        }
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
        twr_by_date = {point.as_of_date: point.value for point in twr_points}
        running_deposits = cumulative_net_deposits(nav_rows)
        drawdown = compute_drawdown(nav_rows, flow_timing=flow_timing)

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
                twr_points,
                _factor_observations(factor_rows),
                factor_provider=self._settings.factor_data_provider,
            ),
            nav_series=[
                _nav_point(row, running_deposits.get(row.as_of_date, ZERO)) for row in nav_rows
            ],
            period_summaries=with_holding_movements(
                compute_period_summaries(nav_rows, twr_by_date),
                holding_rows,
                holding_flows,
                holding_names,
            ),
            monthly_summaries=with_holding_movements(
                compute_monthly_summaries(nav_rows, twr_by_date),
                holding_rows,
                holding_flows,
                holding_names,
            ),
            daily_twr=twr_points,
            rolling_volatility_30d=rolling_volatility(twr_points, ROLLING_SHORT_WINDOW),
            rolling_volatility_90d=rolling_volatility(twr_points, ROLLING_LONG_WINDOW),
            rolling_beta_30d=rolling_beta(joined_passive, ROLLING_SHORT_WINDOW),
            rolling_beta_90d=rolling_beta(joined_passive, ROLLING_LONG_WINDOW),
            contributions=compute_contributions(weights, holding_returns),
            attribution=_attribution_report(
                settings=self._settings,
                holding_rows=holding_rows,
                instruments_by_ticker=instruments_by_ticker,
                twr_points=twr_points,
                price_map=price_map,
                fx_map=fx_map,
                start_date=start_date,
                end_date=end_date,
            ),
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
    """Build the benchmark set from configuration.

    The keys are stable identifiers (they key stored rows), but the label and description are
    derived from the configured symbol so they never claim to be an instrument the deployment is
    not actually pricing.
    """
    return [
        BenchmarkDefinition(
            "cspx",
            f"S&P 500 ETF proxy ({settings.benchmark_cspx_symbol})",
            settings.benchmark_cspx_symbol,
            settings.benchmark_cspx_currency,
            f"ETF proxy for the S&P 500 using {settings.benchmark_cspx_symbol}; "
            "not the licensed index level.",
        ),
        BenchmarkDefinition(
            "swda",
            f"MSCI World ETF proxy ({settings.benchmark_swda_symbol})",
            settings.benchmark_swda_symbol,
            settings.benchmark_swda_currency,
            f"ETF proxy for MSCI World using {settings.benchmark_swda_symbol}; "
            "not the licensed index level.",
        ),
        BenchmarkDefinition(
            "vwrp",
            f"FTSE All-World ETF proxy ({settings.benchmark_vwrp_symbol})",
            settings.benchmark_vwrp_symbol,
            settings.benchmark_vwrp_currency,
            f"ETF proxy for FTSE All-World using {settings.benchmark_vwrp_symbol}; "
            "not the licensed index level.",
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
    points: list[DailyReturnPoint] = []
    # Pair each row with the row before it -- never with the last *valued* row. Bridging an
    # unvalued stretch took only the flow on the day after it, so every deposit made during the
    # gap was counted as investment gain: on a real account a month without London prices
    # produced a single +133% "day". A return across a gap is unknown, so it is left out, and
    # the cumulative figure links the valued segments either side of it.
    for previous, current in pairwise(sorted(nav_rows, key=lambda row: row.as_of_date)):
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


def time_weighted_index(
    nav_rows: Sequence[DailyNav], *, flow_timing: FlowTiming = "flow_at_close"
) -> list[tuple[date, float]]:
    """Growth of 1.0 invested at the first valued day, compounding daily TWR.

    Deposits and withdrawals do not move it -- only investment performance does -- which is
    what makes it the right series for drawdown. Across an unvalued gap there is no return, so
    the index carries its last level rather than inventing a move.
    """

    ordered = sorted(nav_rows, key=lambda row: row.as_of_date)
    first = next((row for row in ordered if row.nav_eur is not None and row.nav_eur > ZERO), None)
    if first is None:
        return []
    returns = {
        point.as_of_date: point.value
        for point in compute_daily_twr(ordered, flow_timing=flow_timing)
    }
    level = 1.0
    series: list[tuple[date, float]] = []
    for row in ordered:
        if row.as_of_date < first.as_of_date or row.nav_eur is None:
            continue
        level *= 1.0 + returns.get(row.as_of_date, 0.0)
        series.append((row.as_of_date, level))
    return series


def compute_drawdown(
    nav_rows: Sequence[DailyNav], *, flow_timing: FlowTiming = "flow_at_close"
) -> DrawdownPoint | None:
    """Worst peak-to-trough drawdown of the time-weighted index, recovery measured to that peak.

    Measured on the index, not on NAV. On raw NAV every withdrawal reads as a loss: a real
    account that took money out 44 times reported a -37.6% "drawdown" that was mostly its own
    withdrawals. Without flows the two coincide exactly.
    """

    series = time_weighted_index(nav_rows, flow_timing=flow_timing)
    if len(series) < 2:
        return None
    running_peak_level = series[0][1]
    running_peak_date = series[0][0]
    worst_drawdown = 0.0
    worst_peak_level = running_peak_level
    worst_peak_date = running_peak_date
    worst_trough_date = running_peak_date
    for as_of, level in series[1:]:
        if level >= running_peak_level:
            running_peak_level = level
            running_peak_date = as_of
            continue
        drawdown = level / running_peak_level - 1.0
        if drawdown < worst_drawdown:
            worst_drawdown = drawdown
            worst_peak_level = running_peak_level
            worst_peak_date = running_peak_date
            worst_trough_date = as_of
    if worst_drawdown == 0.0:
        return DrawdownPoint(worst_peak_date, worst_peak_date, worst_peak_date, 0.0, 0, 0)
    recovery_date: date | None = None
    for as_of, level in series:
        if as_of <= worst_trough_date:
            continue
        if level >= worst_peak_level:
            recovery_date = as_of
            break
    end_date = recovery_date or series[-1][0]
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
    *,
    factor_provider: str = "disabled",
) -> RegressionResult:
    """Regress *excess* portfolio returns on Fama-French 5 factors plus momentum.

    ``factor_provider`` is reported, not used, so an empty factor set can say which of two very
    different things happened: nothing was configured, or a configured source published nothing
    covering this window. Reporting the first when the second is true sends the reader to change
    a setting that is already correct.
    """
    empty_coefficients: dict[str, float | None] = dict.fromkeys(FACTOR_NAMES)
    if not factor_rows:
        if factor_provider == "disabled":
            detail = (
                "No factor return data is configured (HELIOS_FACTOR_DATA_PROVIDER=disabled); "
                "Helios does not fabricate factor series."
            )
        else:
            window = ""
            if portfolio_returns:
                window = (
                    f" for {portfolio_returns[0].as_of_date} to "
                    f"{portfolio_returns[-1].as_of_date}"
                )
            detail = (
                f"Factor provider '{factor_provider}' is configured and reachable but published "
                f"no observations{window}. The Kenneth French library is updated monthly and "
                "trails the present by roughly a month, so recent days have no factors yet."
            )
        return RegressionResult(
            "unavailable",
            0,
            None,
            None,
            empty_coefficients,
            detail,
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
    flows: list[DailyHoldingFlow] = field(default_factory=list)


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
    # Per holding and day: EUR spent buying, received selling, and paid in dividends.
    holding_flows: dict[tuple[date, str], list[Decimal]] = defaultdict(lambda: [ZERO, ZERO, ZERO])
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
        # Trading 212 reports a SELL fill's quantity as negative. The side alone decides the
        # direction, so take the magnitude: applying the side's sign to an already-negative
        # quantity counted every sale as a purchase (found on a real account, where a fully
        # sold holding replayed as 13.9 units held).
        quantity_deltas[trade_date][order.t212_ticker] += abs(order.filled_quantity) * sign
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
        holding_flows[(trade_date, order.t212_ticker)][0 if order.side == "BUY" else 1] += converted

    for dividend in replay_input.dividends:
        if dividend.paid_on is None:
            continue
        paid_date = dividend.paid_on.date()
        if dividend.amount_in_euro is not None:
            dividend_cash[paid_date] += dividend.amount_in_euro
            if dividend.t212_ticker:
                holding_flows[(paid_date, dividend.t212_ticker)][2] += dividend.amount_in_euro
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
        if dividend.t212_ticker:
            holding_flows[(paid_date, dividend.t212_ticker)][2] += converted

    dividend_transactions = 0
    internal_cash: dict[date, Decimal] = defaultdict(lambda: ZERO)
    interest_cash: dict[date, Decimal] = defaultdict(lambda: ZERO)
    fee_cash: dict[date, Decimal] = defaultdict(lambda: ZERO)
    card_cash: dict[date, Decimal] = defaultdict(lambda: ZERO)
    money_in: dict[date, Decimal] = defaultdict(lambda: ZERO)
    money_out: dict[date, Decimal] = defaultdict(lambda: ZERO)
    cashback_cash: dict[date, Decimal] = defaultdict(lambda: ZERO)
    conversion_legs = currency_conversion_legs(replay_input.transactions)
    if conversion_legs:
        notes.add(
            f"Treated {len(conversion_legs)} currency-conversion leg(s) as moves between your own "
            "cash balances, not deposits or withdrawals."
        )
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
            label = card_label(replay_input.export_actions.get(transaction.reference, ""))
            if transaction.reference in conversion_legs:
                # Converting GBP cash to EUR moves money between two of the owner's balances.
                # Both legs stay in cash; the small difference (spread) is a cost, not a flow.
                internal_cash[flow_date] += converted
                continue
            if label == "cashback":
                # Reported by the API as a DEPOSIT, but it is a reward, not money put in.
                internal_cash[flow_date] += converted
                cashback_cash[flow_date] += converted
                continue
            if label == "card":
                card_cash[flow_date] += converted
            external_flows[flow_date] += converted
            if converted >= 0:
                money_in[flow_date] += converted
            else:
                money_out[flow_date] += converted
        elif transaction_type in INTERNAL_CASH_TYPES:
            converted = to_eur(transaction.amount, transaction.currency_code, flow_date)
            if converted is None:
                notes.add(
                    f"Excluded {transaction_type.lower()} in "
                    f"{(transaction.currency_code or '?').upper()}: no FX fix within the stale "
                    "cutoff."
                )
                continue
            internal_cash[flow_date] += converted
            if transaction_type == "FEE":
                fee_cash[flow_date] += converted
            else:
                interest_cash[flow_date] += converted
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
            external_flows[current_date]
            + dividend_cash[current_date]
            + trade_cash[current_date]
            + internal_cash[current_date]
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
            # A base-currency holding converts at exactly 1. There is no EUR->EUR series to look
            # up -- the FX provider never fetches one -- so without this every Euronext or Xetra
            # position read as MISSING_FX and NAV went PARTIAL for good the day it was bought.
            fx = (
                _FxLookup(ONE, PROVENANCE_EXACT, VALUATION_VALUED)
                if currency == BASE_CURRENCY
                else _lookup_fx(fx_rates.get(currency, []), current_date, max_fx_stale_days)
            )
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
                internal_cash_flow_eur=(
                    dividend_cash[current_date]
                    + trade_cash[current_date]
                    + internal_cash[current_date]
                ),
                dividend_eur=dividend_cash[current_date],
                interest_eur=interest_cash[current_date],
                fee_eur=fee_cash[current_date],
                deposit_eur=money_in[current_date],
                withdrawal_eur=money_out[current_date],
                card_spending_eur=card_cash[current_date],
                cashback_eur=cashback_cash[current_date],
                valuation_status=_nav_status(fully_valued, forward_filled),
                missing_price_count=missing_price_count,
                missing_fx_count=missing_fx_count,
            )
        )
    return _ReplayResult(
        holdings=holdings,
        nav_rows=nav_rows,
        flows=[
            DailyHoldingFlow(
                as_of_date=flow_date,
                t212_ticker=ticker,
                bought_eur=bought,
                sold_eur=sold,
                dividend_eur=dividend,
            )
            for (flow_date, ticker), (bought, sold, dividend) in sorted(holding_flows.items())
            if start_date <= flow_date <= end_date
        ],
        unsupported_events=unsupported_events,
        excluded_flow_currencies=excluded_flow_currencies,
        notes=sorted(notes),
    )


def currency_conversion_legs(transactions: Sequence[Transaction]) -> set[str]:
    """References of transactions that are the two legs of a currency conversion.

    Trading 212 reports converting cash between currencies as a WITHDRAW in one currency and a
    DEPOSIT in another, stamped with the same instant (plus a FEE). Counted as they come, that
    is money "withdrawn" and money "deposited" -- both untrue. A leg is recognised only when a
    withdrawal and a deposit in *different* currencies share the exact timestamp.
    """

    by_instant: dict[datetime, list[Transaction]] = defaultdict(list)
    for transaction in transactions:
        if transaction.ts is not None and transaction.amount is not None:
            by_instant[transaction.ts].append(transaction)
    legs: set[str] = set()
    for group in by_instant.values():
        outs = [item for item in group if (item.transaction_type or "").upper() in _OUT_TYPES]
        ins = [item for item in group if (item.transaction_type or "").upper() in _IN_TYPES]
        out_currencies = {(item.currency_code or "").upper() for item in outs}
        in_currencies = {(item.currency_code or "").upper() for item in ins}
        if outs and ins and out_currencies.isdisjoint(in_currencies):
            legs.update(item.reference for item in (*outs, *ins))
    return legs


_OUT_TYPES = frozenset({"WITHDRAW", "WITHDRAWAL"})
_IN_TYPES = frozenset({"DEPOSIT"})


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


def _sector_proxy_price_requests(settings: Settings) -> list[PriceRequest]:
    """Extra price requests for the active benchmark's declared sector-return proxies.

    A malformed ``config/benchmark_sectors.yaml`` does not abort the replay -- it just means no
    extra symbols get fetched here; :func:`_attribution_report` is what surfaces the config
    problem as an explicit status when the report is actually built.
    """
    try:
        config = load_benchmark_sector_config(settings.benchmark_sectors_path)
    except AttributionConfigError:
        return []
    entry = config.benchmarks.get(settings.analytics_passive_benchmark_key)
    if entry is None:
        return []
    return [
        PriceRequest(
            sector_proxy_cache_key(sector.proxy_symbol), sector.proxy_symbol, sector.proxy_currency
        )
        for sector in entry.sectors
    ]


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


def _average_weights(holdings: Sequence[DailyHolding]) -> dict[str, float]:
    """Time-averaged EUR market-value weight per ticker across every replayed day.

    Unlike :func:`_latest_weights` (a single day's snapshot), Brinson-Fachler over a multi-day
    report window needs a portfolio weight that represents the whole window, so this averages each
    day's weight share instead of taking only the most recent one.
    """
    by_date: defaultdict[date, list[DailyHolding]] = defaultdict(list)
    for item in holdings:
        if item.market_value_eur is not None:
            by_date[item.as_of_date].append(item)
    weight_sums: defaultdict[str, float] = defaultdict(float)
    valid_days = 0
    for day_items in by_date.values():
        total = sum(float(item.market_value_eur or ZERO) for item in day_items)
        if total == 0.0:
            continue
        valid_days += 1
        for item in day_items:
            weight_sums[item.t212_ticker] += float(item.market_value_eur or ZERO) / total
    if valid_days == 0:
        return {}
    return {ticker: total / valid_days for ticker, total in weight_sums.items()}


def _window_holding_returns(holdings: Sequence[DailyHolding]) -> dict[str, float]:
    """Per-holding compounded return across the whole report window.

    Reuses the same per-unit EUR price series as :func:`_holding_return_series`/
    :func:`_latest_holding_returns` (immune to quantity changes from trades), but compounds across
    every valued day rather than only the latest one -- Brinson-Fachler needs one return per
    holding for the whole window, not just its most recent day's. A holding valued for only part
    of the window returns its return since first valued rather than nothing, matching "you cannot
    have a return before you held it".
    """
    returns: dict[str, float] = {}
    for ticker, series in _eur_unit_prices(holdings).items():
        if len(series) < 2:
            continue
        first_price = series[0][1]
        last_price = series[-1][1]
        if first_price == 0.0:
            continue
        returns[ticker] = (last_price / first_price) - 1.0
    return returns


def _sector_proxy_return(
    price_rows: Sequence[MarketPriceDaily],
    fx_rates: Mapping[str, Sequence[FxRateDaily]],
    currency_code: str,
    start_date: date,
    end_date: date,
    max_price_stale_days: int,
    max_fx_stale_days: int,
) -> float | None:
    """A sector-return proxy's EUR buy-and-hold return across the report window, or ``None``.

    ``None`` covers every way Helios refuses to guess here: no price/FX at all, or the nearest
    observation to either edge of the window is further away than the configured staleness
    cutoff -- the same rule :func:`compute_passive_counterfactual` uses for the benchmark proxy
    itself.
    """
    series = _eur_price_series(price_rows, fx_rates, currency_code, max_fx_stale_days)
    if not series:
        return None
    start_price = _price_at_or_next(series, start_date, max_price_stale_days)
    end_price = _price_at_or_next(series, end_date, max_price_stale_days)
    if start_price is None or end_price is None or start_price == ZERO:
        return None
    return float(end_price / start_price - ONE)


def _attribution_report(
    *,
    settings: Settings,
    holding_rows: Sequence[DailyHolding],
    instruments_by_ticker: Mapping[str, Instrument],
    twr_points: Sequence[DailyReturnPoint],
    price_map: Mapping[str, Sequence[MarketPriceDaily]],
    fx_map: Mapping[str, Sequence[FxRateDaily]],
    start_date: date,
    end_date: date,
) -> AttributionReport:
    """Brinson-Fachler sector attribution from two operator-declared config files.

    Helios has no licensed index-constituent feed, so neither side of the decomposition is ever
    inferred: holding sectors come from ``sector`` fields in ``instrument_overrides_path``, and
    benchmark sector weights/return proxies come from ``benchmark_sectors_path`` (see
    :mod:`helios.attribution` for the full rules, including the "Unclassified" bucket and the
    residual this single-period approximation reports alongside the numbers).
    """
    benchmark_key = settings.analytics_passive_benchmark_key
    try:
        sector_config = load_benchmark_sector_config(settings.benchmark_sectors_path)
    except AttributionConfigError as error:
        return AttributionReport(
            status="insufficient_data", active_return=None, items=[], detail=str(error)
        )

    entry = sector_config.benchmarks.get(benchmark_key)
    if entry is None:
        return AttributionReport(
            status="unavailable",
            active_return=None,
            items=[],
            detail=(
                "Sector attribution needs two operator-filled files: a `sector` per ISIN in "
                f"{settings.instrument_overrides_path}, and a '{benchmark_key}' entry (benchmark "
                f"sector weights and priceable proxies) in {settings.benchmark_sectors_path}. "
                "Neither ships with real numbers, so Helios reports no numbers."
            ),
        )

    try:
        sector_overrides = load_sector_overrides(settings.instrument_overrides_path)
    except AttributionConfigError as error:
        return AttributionReport(
            status="insufficient_data", active_return=None, items=[], detail=str(error)
        )

    ticker_sector = {
        ticker: (sector_overrides.get(instrument.isin) if instrument.isin else None)
        for ticker, instrument in instruments_by_ticker.items()
    }
    portfolio_weights = _average_weights(holding_rows)
    portfolio_returns = _window_holding_returns(holding_rows)

    benchmark_sector_returns: dict[str, float | None] = {
        sector.sector: _sector_proxy_return(
            price_map.get(sector_proxy_cache_key(sector.proxy_symbol), []),
            fx_map,
            sector.proxy_currency,
            start_date,
            end_date,
            settings.analytics_max_price_stale_days,
            settings.analytics_max_fx_stale_days,
        )
        for sector in entry.sectors
    }

    grouping = build_sector_groups(
        entry=entry,
        ticker_sector=ticker_sector,
        portfolio_weights=portfolio_weights,
        portfolio_returns=portfolio_returns,
        benchmark_sector_returns=benchmark_sector_returns,
    )
    if grouping.status != "ok":
        return AttributionReport(
            status=grouping.status, active_return=None, items=[], detail=grouping.detail
        )

    portfolio_return_metric = cumulative_return_metric(twr_points)
    if portfolio_return_metric.status != "ok" or portfolio_return_metric.value is None:
        return AttributionReport(
            status="insufficient_data",
            active_return=None,
            items=[],
            detail="Need at least one time-weighted return observation for the report window.",
        )

    items = brinson_fachler_attribution(grouping.portfolio_groups, grouping.benchmark_groups)
    active_return = portfolio_return_metric.value - (grouping.benchmark_weighted_return or 0.0)
    residual = active_return - sum(item.total_effect for item in items)

    unclassified_note = (
        f" {grouping.unclassified_weight:.1%} of portfolio weight is Unclassified "
        f"({', '.join(grouping.unclassified_tickers)})."
        if grouping.unclassified_weight > 0.0
        else ""
    )
    detail = (
        f"Single-period Brinson-Fachler over {start_date.isoformat()}..{end_date.isoformat()}: "
        "portfolio sectors use time-averaged EUR market-value weights against a static benchmark "
        f"declared as of {entry.as_of.isoformat()} (source: {entry.source}). Residual (active "
        f"return minus summed effects, from applying single-period weights to a multi-period "
        f"window) is {residual:+.4%}." + unclassified_note
    )
    return AttributionReport(status="ok", active_return=active_return, items=items, detail=detail)


def _nav_point(row: DailyNav, net_deposits_to_date: Decimal = ZERO) -> NavPoint:
    return NavPoint(
        as_of_date=row.as_of_date,
        nav_eur=row.nav_eur,
        cash_balance_eur=row.cash_balance_eur,
        securities_value_eur=row.securities_value_eur,
        external_flow_eur=row.external_flow_eur,
        valuation_status=row.valuation_status,
        dividend_eur=row.dividend_eur or ZERO,
        interest_eur=row.interest_eur or ZERO,
        fee_eur=row.fee_eur or ZERO,
        net_deposits_to_date_eur=net_deposits_to_date,
        deposit_eur=row.deposit_eur or ZERO,
        withdrawal_eur=row.withdrawal_eur or ZERO,
        card_spending_eur=row.card_spending_eur or ZERO,
        cashback_eur=row.cashback_eur or ZERO,
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
