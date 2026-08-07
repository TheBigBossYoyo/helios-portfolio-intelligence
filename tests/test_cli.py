from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

import pytest

from helios.cli import build_parser, main, render_positions
from helios.client import Trading212TransportError
from helios.performance import (
    ANNUALIZATION_DAYS,
    AttributionReport,
    CorrelationClusterReport,
    MetricValue,
    NoPerformanceDataError,
    PassiveCounterfactualReport,
    PerformanceReport,
    RegressionResult,
)
from helios.portfolio_repository import SyncAlreadyRunningError
from helios.reporting import NoQualityReportDataError
from helios.schemas import PerformanceReportModel, PortfolioSyncSummary, Position, QualityReport


@dataclass
class FakeContainer:
    t212_service: object
    portfolio_sync_service: object
    portfolio_quality_report_service: object
    performance_replay_service: object | None = None
    startup_calls: int = 0
    shutdown_calls: int = 0

    async def startup(self) -> None:
        self.startup_calls += 1

    async def shutdown(self) -> None:
        self.shutdown_calls += 1


class FakePositionsService:
    def __init__(
        self,
        positions: list[Position] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._positions = positions or []
        self._error = error

    async def get_positions(self) -> list[Position]:
        if self._error is not None:
            raise self._error
        return self._positions


class FakeSyncService:
    def __init__(
        self,
        result: PortfolioSyncSummary | None = None,
        error: Exception | None = None,
    ) -> None:
        self._result = result
        self._error = error
        self.calls: list[bool] = []

    async def sync(self, *, force_metadata: bool = False) -> PortfolioSyncSummary:
        self.calls.append(force_metadata)
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


class FakeQualityService:
    def __init__(self, result: QualityReport | None = None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error

    async def get_report(self) -> QualityReport:
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


class FakePerformanceService:
    def __init__(self, result: object | None = None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error

    async def replay(self, *, as_of: object | None = None) -> object:
        del as_of
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result

    async def get_report(self) -> object:
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


def test_render_positions_uses_native_and_account_currency_fields() -> None:
    position = Position.model_validate(
        {
            "instrument": {"ticker": "AAPL_US_EQ", "currency": "USD"},
            "quantity": "1.5",
            "currentPrice": "225.10",
            "averagePricePaid": "180.00",
            "walletImpact": {"currency": "EUR", "currentValue": "310.25"},
        }
    )

    output = render_positions([position])

    assert "AAPL_US_EQ" in output
    assert "225.10" in output
    assert "180.00" in output
    assert "310.25" in output


def test_build_parser_supports_sync_and_quality_flags() -> None:
    parser = build_parser()

    sync_args = parser.parse_args(["sync", "--force-metadata"])
    quality_args = parser.parse_args(["quality"])
    replay_args = parser.parse_args(["performance-replay"])

    assert sync_args.command == "sync"
    assert sync_args.force_metadata is True
    assert quality_args.command == "quality"
    assert replay_args.command == "performance-replay"


def test_main_sync_outputs_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sync_service = FakeSyncService(
        result=PortfolioSyncSummary.model_validate(
            {
                "asOf": "2024-01-01T00:00:00Z",
                "metadataFetched": True,
                "endpoints": [{"endpoint": "/equity/positions", "fetched": True, "itemCount": 1}],
            }
        )
    )
    container = FakeContainer(
        t212_service=FakePositionsService(),
        portfolio_sync_service=sync_service,
        portfolio_quality_report_service=FakeQualityService(),
        performance_replay_service=FakePerformanceService(result=sync_service._result),
    )
    monkeypatch.setattr("helios.cli.load_settings", lambda: object())
    monkeypatch.setattr("helios.cli.build_container", lambda _settings: container)
    monkeypatch.setattr("sys.argv", ["helios", "sync", "--force-metadata"])

    exit_code = main()

    assert exit_code == 0
    assert sync_service.calls == [True]
    assert '"metadataFetched": true' in capsys.readouterr().out
    assert container.startup_calls == 1
    assert container.shutdown_calls == 1


def test_main_quality_outputs_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = QualityReport.model_validate(
        {
            "asOf": "2024-01-01T00:00:00Z",
            "overallStatus": "OK",
            "endpointStatuses": [],
            "metadataFreshness": {
                "endpoint": "/equity/metadata/instruments",
                "fresh": True,
                "ttlHours": 24,
                "checkedAt": "2024-01-01T00:00:00Z",
                "lastSuccessAt": "2024-01-01T00:00:00Z",
            },
            "unresolvedInstruments": [],
            "ambiguousInstruments": [],
            "overrideRequiredInstruments": [],
            "reconciliationMismatches": [],
            "unsupportedActions": [],
        }
    )
    container = FakeContainer(
        t212_service=FakePositionsService(),
        portfolio_sync_service=FakeSyncService(),
        portfolio_quality_report_service=FakeQualityService(result=report),
        performance_replay_service=FakePerformanceService(result=report),
    )
    monkeypatch.setattr("helios.cli.load_settings", lambda: object())
    monkeypatch.setattr("helios.cli.build_container", lambda _settings: container)
    monkeypatch.setattr("sys.argv", ["helios", "quality"])

    exit_code = main()

    assert exit_code == 0
    assert '"overallStatus": "OK"' in capsys.readouterr().out


@pytest.mark.parametrize(
    ("argv", "error", "expected_prefix"),
    [
        (["helios", "positions"], Trading212TransportError("boom"), "helios.positions.error"),
        (["helios", "sync"], SyncAlreadyRunningError("busy"), "helios.sync.error"),
        (["helios", "quality"], NoQualityReportDataError("missing"), "helios.quality.error"),
    ],
)
def test_main_commands_emit_safe_errors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    error: Exception,
    expected_prefix: str,
) -> None:
    container = FakeContainer(
        t212_service=FakePositionsService(error=error),
        portfolio_sync_service=FakeSyncService(error=error),
        portfolio_quality_report_service=FakeQualityService(error=error),
        performance_replay_service=FakePerformanceService(error=error),
    )
    monkeypatch.setattr("helios.cli.load_settings", lambda: object())
    monkeypatch.setattr("helios.cli.build_container", lambda _settings: container)
    monkeypatch.setattr("sys.argv", argv)

    exit_code = main()

    assert exit_code == 2
    assert expected_prefix in capsys.readouterr().err


def test_main_performance_report_outputs_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = PerformanceReportModel.model_validate(_performance_report(), from_attributes=True)
    container = FakeContainer(
        t212_service=FakePositionsService(),
        portfolio_sync_service=FakeSyncService(),
        portfolio_quality_report_service=FakeQualityService(),
        performance_replay_service=FakePerformanceService(result=report),
    )
    monkeypatch.setattr("helios.cli.load_settings", lambda: object())
    monkeypatch.setattr("helios.cli.build_container", lambda _settings: container)
    monkeypatch.setattr("sys.argv", ["helios", "performance-report"])

    exit_code = main()

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["cumulativeTwr"]["value"] == 0.1
    assert payload["flowTiming"] == "flow_at_close"
    assert payload["annualizationDays"] == ANNUALIZATION_DAYS
    assert payload["attribution"]["status"] == "unavailable"
    assert payload["passiveCounterfactual"]["status"] == "unavailable"
    assert payload["correlationClusters"]["status"] == "insufficient_data"
    assert payload["rollingVolatility30d"] == []
    assert payload["rollingBeta90d"] == []


def test_main_performance_report_emits_safe_errors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    container = FakeContainer(
        t212_service=FakePositionsService(),
        portfolio_sync_service=FakeSyncService(),
        portfolio_quality_report_service=FakeQualityService(),
        performance_replay_service=FakePerformanceService(error=NoPerformanceDataError("missing")),
    )
    monkeypatch.setattr("helios.cli.load_settings", lambda: object())
    monkeypatch.setattr("helios.cli.build_container", lambda _settings: container)
    monkeypatch.setattr("sys.argv", ["helios", "performance-report"])

    exit_code = main()

    assert exit_code == 2
    assert "helios.performance_report.error" in capsys.readouterr().err


def _metric(value: float | None, status: str = "ok", observations: int = 2) -> MetricValue:
    return MetricValue(status, value, observations, None if status == "ok" else "no data")


def _performance_report() -> PerformanceReport:
    """A complete report dataclass, so the DTO mapping is exercised field by field."""
    insufficient = _metric(None, status="insufficient_data", observations=0)
    return PerformanceReport(
        as_of=date(2024, 1, 2),
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 2),
        flow_timing="flow_at_close",
        annualization_days=ANNUALIZATION_DAYS,
        cumulative_twr=_metric(0.1),
        xirr=_metric(0.1),
        annualized_return=_metric(0.1),
        volatility=_metric(0.1),
        downside_volatility=insufficient,
        sharpe=_metric(1.0),
        sortino=insufficient,
        calmar=_metric(1.0),
        max_drawdown=_metric(-0.1),
        time_underwater_days=_metric(3.0),
        recovery_days=insufficient,
        beta_vs_benchmarks=[],
        hhi=_metric(0.5),
        effective_number_of_positions=_metric(2.0),
        top5_weight=_metric(1.0),
        var_95_1d=_metric(-0.1),
        cvar_95_1d=_metric(-0.1),
        var_99_1d=_metric(-0.1),
        cvar_99_1d=_metric(-0.1),
        var_95_10d=insufficient,
        cvar_95_10d=insufficient,
        var_99_10d=insufficient,
        cvar_99_10d=insufficient,
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
        notes=[],
    )
