"""Tests for Brinson-Fachler sector attribution.

Covers ``helios.attribution`` and ``helios.performance._attribution_report``: config validation
(bad weights/keys/dates), the "Unclassified" threshold, a missing sector-return proxy degrading to
insufficient_data, a hand-verified two-sector happy path through the full pipeline, and the
no-config default naming both files an operator must fill in.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from helios.attribution import (
    AttributionConfigError,
    BenchmarkSectorEntry,
    SectorWeightEntry,
    build_sector_groups,
    load_benchmark_sector_config,
    load_sector_overrides,
    sector_proxy_cache_key,
)
from helios.config import Settings
from helios.models import DailyHolding, Instrument, MarketPriceDaily
from helios.performance import DailyReturnPoint, _attribution_report

# ---------------------------------------------------------------------------
# config/benchmark_sectors.yaml validation
# ---------------------------------------------------------------------------


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_missing_benchmark_sectors_file_declares_nothing(tmp_path: Path) -> None:
    config = load_benchmark_sector_config(tmp_path / "does-not-exist.yaml")

    assert config.benchmarks == {}


def test_empty_benchmark_sectors_file_declares_nothing(tmp_path: Path) -> None:
    path = _write(tmp_path / "benchmark_sectors.yaml", "benchmarks: {}\n")

    assert load_benchmark_sector_config(path).benchmarks == {}


def test_sector_weights_must_sum_to_one(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "benchmark_sectors.yaml",
        """
        benchmarks:
          vwrp:
            as_of: 2024-01-01
            source: test factsheet
            sectors:
              - sector: Technology
                weight: 0.5
                proxy_symbol: XLK
                proxy_currency: USD
              - sector: Financials
                weight: 0.2
                proxy_symbol: XLF
                proxy_currency: USD
        """,
    )

    with pytest.raises(AttributionConfigError, match="sum to 1"):
        load_benchmark_sector_config(path)


def test_unknown_benchmark_key_is_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "benchmark_sectors.yaml",
        """
        benchmarks:
          not_a_real_key:
            as_of: 2024-01-01
            source: test factsheet
            sectors:
              - sector: Technology
                weight: 1.0
                proxy_symbol: XLK
                proxy_currency: USD
        """,
    )

    with pytest.raises(AttributionConfigError, match="unknown benchmark key"):
        load_benchmark_sector_config(path)


def test_bad_as_of_date_is_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "benchmark_sectors.yaml",
        """
        benchmarks:
          vwrp:
            as_of: not-a-date
            source: test factsheet
            sectors:
              - sector: Technology
                weight: 1.0
                proxy_symbol: XLK
                proxy_currency: USD
        """,
    )

    with pytest.raises(AttributionConfigError):
        load_benchmark_sector_config(path)


def test_blank_source_is_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "benchmark_sectors.yaml",
        """
        benchmarks:
          vwrp:
            as_of: 2024-01-01
            source: "   "
            sectors:
              - sector: Technology
                weight: 1.0
                proxy_symbol: XLK
                proxy_currency: USD
        """,
    )

    with pytest.raises(AttributionConfigError, match="source must cite"):
        load_benchmark_sector_config(path)


# ---------------------------------------------------------------------------
# config/instrument_overrides.yaml sector field
# ---------------------------------------------------------------------------


def test_load_sector_overrides_skips_entries_without_a_sector(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "instrument_overrides.yaml",
        """
        overrides:
          - isin: US0000000001
            yahoo_ticker: AAA
            preferred_exchange: NASDAQ
            quote_currency: USD
            reason: test
            sector: Technology
          - isin: US0000000002
            yahoo_ticker: BBB
            preferred_exchange: NASDAQ
            quote_currency: USD
            reason: test
        """,
    )

    overrides = load_sector_overrides(path)

    assert overrides == {"US0000000001": "Technology"}


def test_load_sector_overrides_wraps_a_bad_file_as_attribution_config_error(
    tmp_path: Path,
) -> None:
    path = _write(
        tmp_path / "instrument_overrides.yaml",
        """
        overrides:
          - isin: US0000000001
            yahoo_ticker: AAA
            preferred_exchange: NASDAQ
            quote_currency: USD
            reason: test
          - isin: US0000000001
            yahoo_ticker: AAA
            preferred_exchange: NASDAQ
            quote_currency: USD
            reason: duplicate
        """,
    )

    with pytest.raises(AttributionConfigError):
        load_sector_overrides(path)


# ---------------------------------------------------------------------------
# build_sector_groups: the "Unclassified" threshold and missing proxies
# ---------------------------------------------------------------------------


def _entry(*sectors: tuple[str, str, str, str]) -> BenchmarkSectorEntry:
    """``sectors`` as (name, weight, proxy_symbol, proxy_currency) string tuples."""
    return BenchmarkSectorEntry(
        as_of=date(2024, 1, 1),
        source="test",
        sectors=[
            SectorWeightEntry(
                sector=name, weight=Decimal(weight), proxy_symbol=symbol, proxy_currency=currency
            )
            for name, weight, symbol, currency in sectors
        ],
    )


def test_unclassified_weight_above_threshold_is_insufficient_data() -> None:
    entry = _entry(("Technology", "1.0", "XLK", "USD"))

    result = build_sector_groups(
        entry=entry,
        ticker_sector={"KNOWN": "Technology", "MYSTERY_A": None, "MYSTERY_B": None},
        portfolio_weights={"KNOWN": 0.5, "MYSTERY_A": 0.3, "MYSTERY_B": 0.2},
        portfolio_returns={"KNOWN": 0.05, "MYSTERY_A": 0.01, "MYSTERY_B": 0.02},
        benchmark_sector_returns={"Technology": 0.03},
    )

    assert result.status == "insufficient_data"
    assert result.detail is not None
    assert "MYSTERY_A" in result.detail and "MYSTERY_B" in result.detail
    assert result.unclassified_weight == pytest.approx(0.5)


def test_unclassified_weight_below_threshold_is_grouped_and_ok() -> None:
    entry = _entry(("Technology", "1.0", "XLK", "USD"))

    result = build_sector_groups(
        entry=entry,
        ticker_sector={"KNOWN": "Technology", "TINY": None},
        portfolio_weights={"KNOWN": 0.95, "TINY": 0.05},
        portfolio_returns={"KNOWN": 0.05, "TINY": 0.01},
        benchmark_sector_returns={"Technology": 0.03},
    )

    assert result.status == "ok"
    sectors = {key: (weight, ret) for key, weight, ret in result.portfolio_groups}
    tech_weight, tech_return = sectors["Technology"]
    assert tech_weight == pytest.approx(0.95)
    assert tech_return == pytest.approx(0.05)
    unclassified_weight, unclassified_return = sectors["Unclassified"]
    assert unclassified_weight == pytest.approx(0.05)
    assert unclassified_return == pytest.approx(0.01)


def test_missing_benchmark_proxy_return_is_insufficient_data() -> None:
    entry = _entry(("Technology", "0.6", "XLK", "USD"), ("Financials", "0.4", "XLF", "USD"))

    result = build_sector_groups(
        entry=entry,
        ticker_sector={"A": "Technology", "B": "Financials"},
        portfolio_weights={"A": 0.6, "B": 0.4},
        portfolio_returns={"A": 0.05, "B": 0.02},
        benchmark_sector_returns={"Technology": 0.03, "Financials": None},
    )

    assert result.status == "insufficient_data"
    assert result.detail is not None
    assert "XLF" in result.detail


def test_a_holding_without_a_measurable_return_is_never_counted_as_zero() -> None:
    """Weight without a return would dilute its sector toward 0% -- a number never measured."""
    entry = _entry(("Technology", "1.0", "XLK", "USD"))

    result = build_sector_groups(
        entry=entry,
        ticker_sector={"OLD": "Technology", "JUST_BOUGHT": "Technology"},
        portfolio_weights={"OLD": 0.9, "JUST_BOUGHT": 0.1},
        portfolio_returns={"OLD": 0.10},
        benchmark_sector_returns={"Technology": 0.03},
    )

    assert result.status == "insufficient_data"
    assert result.detail is not None
    assert "JUST_BOUGHT" in result.detail
    assert result.portfolio_groups == []


# ---------------------------------------------------------------------------
# performance._attribution_report: default, missing proxy, hand-verified happy path
# ---------------------------------------------------------------------------


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        instrument_overrides_path=tmp_path / "instrument_overrides.yaml",
        benchmark_sectors_path=tmp_path / "benchmark_sectors.yaml",
    )


def test_default_no_config_reports_unavailable_naming_both_files(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    report = _attribution_report(
        settings=settings,
        holding_rows=[],
        instruments_by_ticker={},
        twr_points=[],
        price_map={},
        fx_map={},
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 2),
    )

    assert report.status == "unavailable"
    assert report.detail is not None
    assert "instrument_overrides.yaml" in report.detail
    assert "benchmark_sectors.yaml" in report.detail


def _holding(day: date, ticker: str, market_value_eur: str, quantity: str = "10") -> DailyHolding:
    return DailyHolding(
        as_of_date=day,
        t212_ticker=ticker,
        quantity=Decimal(quantity),
        price_currency="EUR",
        close_price=Decimal(market_value_eur) / Decimal(quantity),
        price_provenance="EXACT",
        fx_rate_to_eur=Decimal("1"),
        fx_provenance="EXACT",
        market_value_local=Decimal(market_value_eur),
        market_value_eur=Decimal(market_value_eur),
        valuation_status="VALUED",
    )


def _proxy_price(day: date, symbol: str, close: str) -> MarketPriceDaily:
    return MarketPriceDaily(
        price_date=day,
        t212_ticker=sector_proxy_cache_key(symbol),
        provider_symbol=symbol,
        currency_code="EUR",
        close_price=Decimal(close),
        provider="fixture",
        source_date=day,
        provenance="EXACT",
    )


_START = date(2024, 1, 1)
_END = date(2024, 1, 2)


def _happy_path_config(tmp_path: Path) -> Settings:
    _write(
        tmp_path / "instrument_overrides.yaml",
        """
        overrides:
          - isin: ISIN_A
            yahoo_ticker: TICKA
            preferred_exchange: NASDAQ
            quote_currency: EUR
            reason: test
            sector: Technology
          - isin: ISIN_B
            yahoo_ticker: TICKB
            preferred_exchange: NASDAQ
            quote_currency: EUR
            reason: test
            sector: Financials
        """,
    )
    _write(
        tmp_path / "benchmark_sectors.yaml",
        """
        benchmarks:
          vwrp:
            as_of: 2024-01-01
            source: test factsheet
            sectors:
              - sector: Technology
                weight: 0.6
                proxy_symbol: XLK
                proxy_currency: EUR
              - sector: Financials
                weight: 0.4
                proxy_symbol: XLF
                proxy_currency: EUR
        """,
    )
    return _settings(tmp_path)


def _happy_path_holdings() -> list[DailyHolding]:
    return [
        _holding(_START, "TICK_A", "700"),
        _holding(_START, "TICK_B", "300"),
        _holding(_END, "TICK_A", "770"),
        _holding(_END, "TICK_B", "270"),
    ]


def _happy_path_instruments() -> dict[str, Instrument]:
    return {
        "TICK_A": Instrument(t212_ticker="TICK_A", isin="ISIN_A", currency_code="EUR"),
        "TICK_B": Instrument(t212_ticker="TICK_B", isin="ISIN_B", currency_code="EUR"),
    }


def test_missing_sector_proxy_price_is_insufficient_data(tmp_path: Path) -> None:
    settings = _happy_path_config(tmp_path)

    report = _attribution_report(
        settings=settings,
        holding_rows=_happy_path_holdings(),
        instruments_by_ticker=_happy_path_instruments(),
        twr_points=[DailyReturnPoint(_END, 0.05)],
        price_map={
            sector_proxy_cache_key("XLK"): [
                _proxy_price(_START, "XLK", "100"),
                _proxy_price(_END, "XLK", "108"),
            ]
            # Financials proxy (XLF) has no price rows at all.
        },
        fx_map={},
        start_date=_START,
        end_date=_END,
    )

    assert report.status == "insufficient_data"
    assert report.detail is not None
    assert "XLF" in report.detail


def test_hand_verified_two_sector_happy_path(tmp_path: Path) -> None:
    """A fully worked example: two holdings, two sectors, hand-computed Brinson-Fachler effects.

    Day 1 -> day 2: TICK_A (Technology) 700 -> 770 EUR (+10%); TICK_B (Financials) 300 -> 270 EUR
    (-10%). Benchmark declares Technology 60% / Financials 40%, with sector proxies XLK (+8%) and
    XLF (-2%) over the same window. The portfolio's actual TWR over the window is supplied
    independently as +5% (it need not equal the naive holdings-only ratio; that gap plus the
    single-period-vs-multi-period approximation is exactly what the residual captures).
    """
    settings = _happy_path_config(tmp_path)

    # Time-averaged weights: day 1 shares are 0.7/0.3; day 2 shares are 770/1040 and 270/1040.
    day1_weight_a, day1_weight_b = 0.7, 0.3
    day2_weight_a, day2_weight_b = 770.0 / 1040.0, 270.0 / 1040.0
    avg_weight_a = (day1_weight_a + day2_weight_a) / 2.0
    avg_weight_b = (day1_weight_b + day2_weight_b) / 2.0
    assert avg_weight_a == pytest.approx(0.7201923076923077)
    assert avg_weight_b == pytest.approx(0.2798076923076923)

    portfolio_return_a = 770.0 / 700.0 - 1.0  # +10%
    portfolio_return_b = 270.0 / 300.0 - 1.0  # -10%
    assert portfolio_return_a == pytest.approx(0.10)
    assert portfolio_return_b == pytest.approx(-0.10)

    benchmark_weight_tech, benchmark_weight_fin = 0.6, 0.4
    benchmark_return_tech = 108.0 / 100.0 - 1.0  # +8%
    benchmark_return_fin = 49.0 / 50.0 - 1.0  # -2%
    rb_weighted = (
        benchmark_weight_tech * benchmark_return_tech + benchmark_weight_fin * benchmark_return_fin
    )
    assert rb_weighted == pytest.approx(0.04)

    def brinson(pw: float, bw: float, pr: float, br: float) -> tuple[float, float, float]:
        allocation = (pw - bw) * (br - rb_weighted)
        selection = bw * (pr - br)
        interaction = (pw - bw) * (pr - br)
        return allocation, selection, interaction

    tech_allocation, tech_selection, tech_interaction = brinson(
        avg_weight_a, benchmark_weight_tech, portfolio_return_a, benchmark_return_tech
    )
    fin_allocation, fin_selection, fin_interaction = brinson(
        avg_weight_b, benchmark_weight_fin, portfolio_return_b, benchmark_return_fin
    )
    expected_total_effects = (
        tech_allocation
        + tech_selection
        + tech_interaction
        + fin_allocation
        + fin_selection
        + fin_interaction
    )

    reported_twr = 0.05
    expected_active_return = reported_twr - rb_weighted
    expected_residual = expected_active_return - expected_total_effects

    report = _attribution_report(
        settings=settings,
        holding_rows=_happy_path_holdings(),
        instruments_by_ticker=_happy_path_instruments(),
        twr_points=[DailyReturnPoint(_END, reported_twr)],
        price_map={
            sector_proxy_cache_key("XLK"): [
                _proxy_price(_START, "XLK", "100"),
                _proxy_price(_END, "XLK", "108"),
            ],
            sector_proxy_cache_key("XLF"): [
                _proxy_price(_START, "XLF", "50"),
                _proxy_price(_END, "XLF", "49"),
            ],
        },
        fx_map={},
        start_date=_START,
        end_date=_END,
    )

    assert report.status == "ok"
    assert report.active_return is not None
    assert report.active_return == pytest.approx(expected_active_return)

    by_key = {item.key: item for item in report.items}
    assert set(by_key) == {"Technology", "Financials"}

    tech = by_key["Technology"]
    assert tech.portfolio_weight == pytest.approx(avg_weight_a)
    assert tech.benchmark_weight == pytest.approx(benchmark_weight_tech)
    assert tech.allocation_effect == pytest.approx(tech_allocation)
    assert tech.selection_effect == pytest.approx(tech_selection)
    assert tech.interaction_effect == pytest.approx(tech_interaction)

    fin = by_key["Financials"]
    assert fin.portfolio_weight == pytest.approx(avg_weight_b)
    assert fin.benchmark_weight == pytest.approx(benchmark_weight_fin)
    assert fin.allocation_effect == pytest.approx(fin_allocation)
    assert fin.selection_effect == pytest.approx(fin_selection)
    assert fin.interaction_effect == pytest.approx(fin_interaction)

    summed_effects = sum(item.total_effect for item in report.items)
    assert summed_effects == pytest.approx(expected_total_effects)
    # The defining property of the residual: active return minus the summed effects.
    implied_residual = report.active_return - summed_effects
    assert implied_residual == pytest.approx(expected_residual)
    assert report.detail is not None
    assert "Residual" in report.detail
    assert "test factsheet" in report.detail
