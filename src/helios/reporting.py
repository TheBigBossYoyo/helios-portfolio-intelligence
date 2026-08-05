from __future__ import annotations

from .config import Settings
from .models import Instrument, PositionReconciliation, SyncStatus
from .portfolio_repository import MetadataFreshness, PortfolioRepository
from .rate_limit import Clock, SystemClock
from .schemas import (
    EndpointAttemptReport,
    InstrumentMappingIssueReport,
    MetadataFreshnessReport,
    QualityReport,
    ReconciliationIssueReport,
)


class NoQualityReportDataError(ValueError):
    pass


class PortfolioQualityReportService:
    def __init__(
        self,
        repository: PortfolioRepository,
        settings: Settings,
        clock: Clock | None = None,
    ) -> None:
        self._repository = repository
        self._settings = settings
        self._clock = clock or SystemClock()

    async def get_report(self) -> QualityReport:
        endpoint_statuses = await self._repository.list_endpoint_statuses()
        as_of = await self._repository.latest_report_timestamp()
        if as_of is None:
            raise NoQualityReportDataError("No sync attempts recorded")
        checked_at = self._clock.utcnow()
        metadata_freshness = await self._repository.get_metadata_freshness(
            checked_at=checked_at,
            ttl_hours=self._settings.instrument_metadata_ttl_hours,
        )
        unresolved = await self._repository.list_instruments_with_status("unresolved")
        ambiguous = await self._repository.list_instruments_with_status("ambiguous")
        override_required = await self._repository.list_instruments_with_status("override_required")
        mismatches = await self._repository.list_reconciliation_by_status("MISMATCH")
        unsupported = await self._repository.list_reconciliation_by_status("UNSUPPORTED_ACTION")

        overall_status = "OK"
        if any(status.last_status == "failed" for status in endpoint_statuses):
            overall_status = "ERROR"
        elif (
            not metadata_freshness.fresh
            or unresolved
            or ambiguous
            or override_required
            or mismatches
            or unsupported
        ):
            overall_status = "WARNING"

        return QualityReport.model_validate(
            {
                "asOf": as_of,
                "overallStatus": overall_status,
                "endpointStatuses": [
                    _endpoint_status_report(status).model_dump(mode="json", by_alias=True)
                    for status in endpoint_statuses
                ],
                "metadataFreshness": _metadata_freshness_report(metadata_freshness).model_dump(
                    mode="json",
                    by_alias=True,
                ),
                "unresolvedInstruments": [
                    _instrument_issue(item).model_dump(mode="json", by_alias=True)
                    for item in unresolved
                ],
                "ambiguousInstruments": [
                    _instrument_issue(item).model_dump(mode="json", by_alias=True)
                    for item in ambiguous
                ],
                "overrideRequiredInstruments": [
                    _instrument_issue(item).model_dump(mode="json", by_alias=True)
                    for item in override_required
                ],
                "reconciliationMismatches": [
                    _reconciliation_issue(item).model_dump(mode="json", by_alias=True)
                    for item in mismatches
                ],
                "unsupportedActions": [
                    _reconciliation_issue(item).model_dump(mode="json", by_alias=True)
                    for item in unsupported
                ],
            }
        )


def _endpoint_status_report(item: SyncStatus) -> EndpointAttemptReport:
    return EndpointAttemptReport.model_validate(
        {
            "endpoint": item.endpoint,
            "lastAttemptAt": item.last_attempt_at,
            "lastSuccessAt": item.last_success_at,
            "lastStatus": item.last_status,
            "itemCount": item.item_count,
            "lastError": item.last_error,
        }
    )


def _metadata_freshness_report(item: MetadataFreshness) -> MetadataFreshnessReport:
    return MetadataFreshnessReport.model_validate(
        {
            "endpoint": item.endpoint,
            "fresh": item.fresh,
            "ttlHours": item.ttl_hours,
            "checkedAt": item.checked_at,
            "lastSuccessAt": item.last_success_at,
        }
    )


def _instrument_issue(item: Instrument) -> InstrumentMappingIssueReport:
    return InstrumentMappingIssueReport.model_validate(
        {
            "t212Ticker": item.t212_ticker,
            "isin": item.isin,
            "yahooTicker": item.yahoo_ticker,
            "mappingStatus": item.mapping_status,
            "mappingSource": item.mapping_source,
            "mappingDetails": item.mapping_details_json,
            "mappedAt": item.mapped_at,
        }
    )


def _reconciliation_issue(item: PositionReconciliation) -> ReconciliationIssueReport:
    return ReconciliationIssueReport.model_validate(
        {
            "t212Ticker": item.t212_ticker,
            "ts": item.ts,
            "replayedQuantity": item.replayed_quantity,
            "liveQuantity": item.live_quantity,
            "differenceQuantity": item.difference_quantity,
            "toleranceQuantity": item.tolerance_quantity,
            "status": item.status,
        }
    )
