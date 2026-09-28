"""Brinson-Fachler sector attribution, built only from what the operator declares.

Helios has no licensed index-constituent feed, so it cannot look up which sector a holding
belongs to, or what a benchmark's sector weights and sector returns were, on its own. Both sides
of the decomposition instead come from two files the operator fills in by hand:

* ``config/instrument_overrides.yaml`` -- an optional ``sector`` on an existing ISIN override.
  A holding whose ISIN has no declared sector is never guessed at from its name or exchange; it
  is grouped into the "Unclassified" bucket and its share of portfolio weight is reported, so a
  silently-wrong attribution never hides behind a made-up classification. If too much of the
  portfolio is Unclassified, the whole report degrades to ``insufficient_data`` naming the
  tickers, because a decomposition over `< 90%` of the portfolio is not a sector attribution.
* ``config/benchmark_sectors.yaml`` -- for the configured passive benchmark, the constituent
  sector weights (which must sum to 1) plus a sector-return proxy per sector: a ticker the
  market-data provider can price (e.g. a sector SPDR ETF), its quotation currency, the date the
  weights are as of, and a citation for where they came from. Nothing here is inferred from a
  provider; it is exactly what the operator typed in, with its provenance kept alongside it.

Both files ship with nothing declared (an empty override list; an empty ``benchmarks`` map), so a
fresh install reports attribution as ``unavailable`` naming both files rather than claiming a
number nobody supplied.

This module owns the config schema/validation and the group-by-sector aggregation of already
computed per-holding weights and returns. It does **not** do the Brinson-Fachler arithmetic
itself -- :func:`helios.performance.brinson_fachler_attribution` already does that, is already
tested, and needs no configuration at all, so :mod:`helios.performance` calls it directly with the
group vectors this module produces. Nor does it touch prices, FX, or the ORM: those stay in
:mod:`helios.performance`, which already has the machinery to turn a proxy symbol into an EUR
return over a date window, so this module can be tested with plain numbers.

Because a single-period Brinson decomposition is only exact when the window has no intermediate
weight changes, and Helios instead uses time-averaged weights over a possibly long report window,
the caller is expected to report the *residual* -- actual active return minus the summed
allocation/selection/interaction effects -- alongside the numbers, rather than pretend the
decomposition reconciles exactly.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import BENCHMARK_KEYS
from .resolver import load_instrument_overrides

#: A holding with no declared sector lands here rather than being guessed at.
UNCLASSIFIED_SECTOR = "Unclassified"

#: Above this share of portfolio weight, "we don't know most of the portfolio's sectors" makes
#: the decomposition unreliable enough to withhold rather than report.
UNCLASSIFIED_WEIGHT_THRESHOLD = Decimal("0.10")

#: Sector weights must sum to 1; this tolerance absorbs rounding in a hand-typed factsheet figure.
WEIGHT_SUM_TOLERANCE = Decimal("0.005")


class AttributionConfigError(ValueError):
    """A malformed ``config/benchmark_sectors.yaml`` or a bad ``sector`` override.

    Raised, never silently ignored -- but callers building a report catch it and turn it into an
    explicit ``insufficient_data`` status rather than letting a config typo fail the whole
    performance report.
    """


class SectorWeightEntry(BaseModel):
    """One benchmark sector: its declared weight and a proxy Helios can price."""

    model_config = ConfigDict(extra="forbid")

    sector: str
    weight: Decimal
    proxy_symbol: str
    proxy_currency: str

    @field_validator("sector", "proxy_symbol", "proxy_currency")
    @classmethod
    def _non_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("sector, proxy_symbol and proxy_currency must be non-blank")
        return stripped

    @field_validator("proxy_currency")
    @classmethod
    def _currency_upper(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("weight")
    @classmethod
    def _weight_nonnegative(cls, value: Decimal) -> Decimal:
        if value < Decimal("0"):
            raise ValueError("sector weight must be nonnegative")
        return value


class BenchmarkSectorEntry(BaseModel):
    """The declared sector composition of one benchmark key, as of a stated date."""

    model_config = ConfigDict(extra="forbid")

    as_of: date
    source: str
    sectors: list[SectorWeightEntry]

    @field_validator("source")
    @classmethod
    def _source_non_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError(
                "source must cite where the weights came from, e.g. a fund factsheet URL"
            )
        return stripped

    @model_validator(mode="after")
    def _validate_sectors(self) -> BenchmarkSectorEntry:
        if not self.sectors:
            raise ValueError("sectors must list at least one sector")
        names = [entry.sector for entry in self.sectors]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"duplicate sector names: {duplicates}")
        total = sum((entry.weight for entry in self.sectors), Decimal("0"))
        if abs(total - Decimal("1")) > WEIGHT_SUM_TOLERANCE:
            raise ValueError(
                f"sector weights must sum to 1 (+/- {WEIGHT_SUM_TOLERANCE}); got {total}"
            )
        return self


class BenchmarkSectorsFile(BaseModel):
    """The whole of ``config/benchmark_sectors.yaml``: zero or more benchmark keys declared."""

    model_config = ConfigDict(extra="forbid")

    benchmarks: dict[str, BenchmarkSectorEntry] = Field(default_factory=dict)

    @field_validator("benchmarks")
    @classmethod
    def _known_keys(
        cls, value: dict[str, BenchmarkSectorEntry]
    ) -> dict[str, BenchmarkSectorEntry]:
        unknown = sorted(set(value) - BENCHMARK_KEYS)
        if unknown:
            raise ValueError(
                f"unknown benchmark key(s) {unknown}; must be one of {sorted(BENCHMARK_KEYS)}"
            )
        return value


def load_benchmark_sector_config(path: Path) -> BenchmarkSectorsFile:
    """Load ``config/benchmark_sectors.yaml``. A missing or empty file declares nothing.

    Raises :class:`AttributionConfigError` for a present-but-malformed file (bad dates, unknown
    benchmark key, weights not summing to 1) with a message naming the problem, never a partially
    applied config.
    """
    if not path.is_file():
        return BenchmarkSectorsFile()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return BenchmarkSectorsFile()
    try:
        return BenchmarkSectorsFile.model_validate(raw)
    except ValueError as error:
        raise AttributionConfigError(f"{path}: {error}") from error


def sector_proxy_cache_key(proxy_symbol: str) -> str:
    """The market-price cache key a sector proxy is stored/looked up under.

    Mirrors :func:`helios.performance.benchmark_cache_key`'s convention of a namespaced key that
    cannot collide with a real T212 ticker, keyed by symbol so two sectors sharing a proxy (rare,
    but not forbidden) share one price fetch.
    """
    return f"__sector_proxy_{proxy_symbol.strip().upper()}__"


def load_sector_overrides(path: Path) -> dict[str, str]:
    """ISIN -> operator-declared sector, from the ``sector`` field on an instrument override.

    An override with no ``sector`` set contributes nothing here; that ISIN's holdings fall back to
    "Unclassified" rather than being guessed at from its name, exchange, or anything else.
    """
    try:
        overrides = load_instrument_overrides(path)
    except ValueError as error:
        raise AttributionConfigError(f"{path}: {error}") from error
    return {isin: entry.sector for isin, entry in overrides.items() if entry.sector is not None}


@dataclass(frozen=True)
class SectorGroupResult:
    """Either a reason attribution cannot run, or the group vectors ready for Brinson-Fachler.

    ``portfolio_groups``/``benchmark_groups`` are ``(sector, weight, return)`` triples, exactly
    the shape :func:`helios.performance.brinson_fachler_attribution` takes.
    """

    status: str
    detail: str | None
    portfolio_groups: list[tuple[str, float, float]] = field(default_factory=list)
    benchmark_groups: list[tuple[str, float, float]] = field(default_factory=list)
    benchmark_weighted_return: float | None = None
    unclassified_weight: float = 0.0
    unclassified_tickers: list[str] = field(default_factory=list)


def build_sector_groups(
    *,
    entry: BenchmarkSectorEntry,
    ticker_sector: Mapping[str, str | None],
    portfolio_weights: Mapping[str, float],
    portfolio_returns: Mapping[str, float],
    benchmark_sector_returns: Mapping[str, float | None],
    unclassified_threshold: Decimal = UNCLASSIFIED_WEIGHT_THRESHOLD,
) -> SectorGroupResult:
    """Group per-holding weights/returns into the sectors ``entry`` declares.

    ``ticker_sector`` maps a holding's ticker to its declared sector, or ``None`` when it has no
    declared sector (missing override, or an override whose sector isn't in ``entry``).
    ``benchmark_sector_returns`` maps each declared sector name to its proxy's EUR return over the
    report window, or ``None`` when that proxy has no usable price -- which makes the whole result
    ``insufficient_data`` naming the affected proxies, rather than a decomposition silently missing
    one term of the sum.
    """
    missing_symbols = sorted(
        {
            sector.proxy_symbol
            for sector in entry.sectors
            if benchmark_sector_returns.get(sector.sector) is None
        }
    )
    if missing_symbols:
        return SectorGroupResult(
            status="insufficient_data",
            detail=(
                "Benchmark sector-return proxy has no usable EUR price over the report window "
                f"for: {', '.join(missing_symbols)}. Check config/benchmark_sectors.yaml and "
                "whether the market-data provider covers these symbols."
            ),
        )

    declared_sectors = {sector.sector for sector in entry.sectors}

    # A held position with no measurable return over the window cannot be attributed. Counting
    # its weight in a sector while contributing no return would drag that sector's return
    # toward zero -- a silent 0% for something Helios never measured -- so refuse and name it.
    unmeasured = sorted(
        ticker
        for ticker, weight in portfolio_weights.items()
        if weight > 0.0 and portfolio_returns.get(ticker) is None
    )
    if unmeasured:
        return SectorGroupResult(
            status="insufficient_data",
            detail=(
                "These holdings have no measurable return over the report window (they need "
                f"two valued days): {', '.join(unmeasured)}. Attribution resumes once they do."
            ),
        )

    portfolio_sector_weight: defaultdict[str, float] = defaultdict(float)
    portfolio_sector_weighted_return: defaultdict[str, float] = defaultdict(float)
    unclassified_weight = 0.0
    unclassified_tickers: list[str] = []

    for ticker, weight in portfolio_weights.items():
        if weight <= 0.0:
            continue
        sector = ticker_sector.get(ticker)
        if sector is None or sector not in declared_sectors:
            unclassified_weight += weight
            unclassified_tickers.append(ticker)
            sector_key = UNCLASSIFIED_SECTOR
        else:
            sector_key = sector
        portfolio_sector_weight[sector_key] += weight
        holding_return = portfolio_returns.get(ticker)
        if holding_return is not None:
            portfolio_sector_weighted_return[sector_key] += weight * holding_return

    total_weight = sum(weight for weight in portfolio_weights.values() if weight > 0.0)
    unclassified_share = (unclassified_weight / total_weight) if total_weight > 0.0 else 0.0
    if unclassified_share > float(unclassified_threshold):
        return SectorGroupResult(
            status="insufficient_data",
            detail=(
                f"{unclassified_share:.1%} of portfolio weight has no declared sector "
                f"(threshold {float(unclassified_threshold):.0%}): "
                f"{', '.join(sorted(unclassified_tickers))}. Add a `sector` to these instruments' "
                "ISINs in config/instrument_overrides.yaml."
            ),
            unclassified_weight=unclassified_weight,
            unclassified_tickers=sorted(unclassified_tickers),
        )

    portfolio_groups = [
        (
            sector_key,
            weight,
            (portfolio_sector_weighted_return.get(sector_key, 0.0) / weight) if weight else 0.0,
        )
        for sector_key, weight in sorted(portfolio_sector_weight.items())
    ]
    resolved_returns = cast(dict[str, float], dict(benchmark_sector_returns))
    benchmark_groups = [
        (sector.sector, float(sector.weight), resolved_returns[sector.sector])
        for sector in entry.sectors
    ]
    benchmark_weighted_return = sum(weight * value for _, weight, value in benchmark_groups)

    return SectorGroupResult(
        status="ok",
        detail=None,
        portfolio_groups=portfolio_groups,
        benchmark_groups=benchmark_groups,
        benchmark_weighted_return=benchmark_weighted_return,
        unclassified_weight=unclassified_weight,
        unclassified_tickers=sorted(unclassified_tickers),
    )
