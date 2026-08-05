from __future__ import annotations

from dataclasses import dataclass

import pytest

from helios.cli import build_parser, main, render_positions
from helios.client import Trading212TransportError
from helios.portfolio_repository import SyncAlreadyRunningError
from helios.reporting import NoQualityReportDataError
from helios.schemas import PortfolioSyncSummary, Position, QualityReport


@dataclass
class FakeContainer:
    t212_service: object
    portfolio_sync_service: object
    portfolio_quality_report_service: object
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

    assert sync_args.command == "sync"
    assert sync_args.force_metadata is True
    assert quality_args.command == "quality"


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
    )
    monkeypatch.setattr("helios.cli.load_settings", lambda: object())
    monkeypatch.setattr("helios.cli.build_container", lambda _settings: container)
    monkeypatch.setattr("sys.argv", argv)

    exit_code = main()

    assert exit_code == 2
    assert expected_prefix in capsys.readouterr().err
