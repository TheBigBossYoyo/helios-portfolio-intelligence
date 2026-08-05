from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class DTOModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class InstrumentInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    ticker: str
    isin: str | None = None
    name: str | None = None
    currency: str | None = None


class InstrumentMetadata(DTOModel):
    added_on: datetime | None = Field(default=None, alias="addedOn")
    currency_code: str | None = Field(default=None, alias="currencyCode")
    extended_hours: bool | None = Field(default=None, alias="extendedHours")
    isin: str | None = None
    max_open_quantity: Decimal | None = Field(default=None, alias="maxOpenQuantity")
    name: str | None = None
    short_name: str | None = Field(default=None, alias="shortName")
    ticker: str
    type: str | None = None
    working_schedule_id: int | None = Field(default=None, alias="workingScheduleId")


class WalletImpact(DTOModel):
    currency: str | None = None
    current_value: Decimal | None = Field(default=None, alias="currentValue")
    fx_impact: Decimal | None = Field(default=None, alias="fxImpact")
    total_cost: Decimal | None = Field(default=None, alias="totalCost")
    unrealized_profit_loss: Decimal | None = Field(default=None, alias="unrealizedProfitLoss")


class Position(DTOModel):
    average_price_paid: Decimal | None = Field(
        default=None,
        alias="averagePricePaid",
        description="Per-share average price in instrument currency.",
    )
    created_at: datetime | None = Field(default=None, alias="createdAt")
    current_price: Decimal | None = Field(
        default=None,
        alias="currentPrice",
        description="Per-share current price in instrument currency.",
    )
    instrument: InstrumentInfo
    quantity: Decimal
    quantity_available_for_trading: Decimal | None = Field(
        default=None,
        alias="quantityAvailableForTrading",
    )
    quantity_in_pies: Decimal | None = Field(default=None, alias="quantityInPies")
    wallet_impact: WalletImpact | None = Field(
        default=None,
        alias="walletImpact",
        description="Aggregated values in the account primary currency.",
    )


class HistoryTax(DTOModel):
    charged_at: datetime | None = Field(default=None, alias="chargedAt")
    currency: str | None = None
    name: str | None = None
    quantity: Decimal | None = None


class HistoryWalletImpact(DTOModel):
    currency: str | None = None
    fx_rate: Decimal | None = Field(default=None, alias="fxRate")
    net_value: Decimal | None = Field(default=None, alias="netValue")
    realised_profit_loss: Decimal | None = Field(default=None, alias="realisedProfitLoss")
    taxes: list[HistoryTax] | None = None


class HistoricalOrderFill(DTOModel):
    filled_at: datetime | None = Field(default=None, alias="filledAt")
    id: int | str | None = None
    price: Decimal | None = None
    quantity: Decimal | None = None
    trading_method: str | None = Field(default=None, alias="tradingMethod")
    type: str | None = None
    wallet_impact: HistoryWalletImpact | None = Field(default=None, alias="walletImpact")


class HistoricalOrder(DTOModel):
    created_at: datetime | None = Field(default=None, alias="createdAt")
    currency: str | None = None
    extended_hours: bool | None = Field(default=None, alias="extendedHours")
    filled_quantity: Decimal | None = Field(default=None, alias="filledQuantity")
    filled_value: Decimal | None = Field(default=None, alias="filledValue")
    id: int | str | None = None
    instrument: InstrumentInfo | None = None
    limit_price: Decimal | None = Field(default=None, alias="limitPrice")
    quantity: Decimal | None = None
    side: str | None = None
    status: str | None = None
    stop_price: Decimal | None = Field(default=None, alias="stopPrice")
    ticker: str | None = None
    type: str | None = None


class HistoricalOrderItem(DTOModel):
    fill: HistoricalOrderFill
    order: HistoricalOrder


class DividendItem(DTOModel):
    amount: Decimal | None = None
    amount_in_euro: Decimal | None = Field(default=None, alias="amountInEuro")
    currency: str | None = None
    gross_amount_per_share: Decimal | None = Field(default=None, alias="grossAmountPerShare")
    instrument: InstrumentInfo | None = None
    paid_on: datetime | None = Field(default=None, alias="paidOn")
    quantity: Decimal | None = None
    reference: str
    ticker: str | None = None
    ticker_currency: str | None = Field(default=None, alias="tickerCurrency")
    type: str | None = None


class TransactionItem(DTOModel):
    amount: Decimal | None = None
    currency: str | None = None
    date_time: datetime | None = Field(default=None, alias="dateTime")
    reference: str
    type: str | None = None


class HistoryPage[TItem](DTOModel):
    items: list[TItem]
    next_page_path: str | None = Field(default=None, alias="nextPagePath")


class HealthResponse(DTOModel):
    status: str
    trading212_configured: bool = Field(alias="trading212Configured")
    database_ready: bool = Field(alias="databaseReady")


class SyncEndpointSummary(DTOModel):
    endpoint: str
    fetched: bool
    item_count: int | None = Field(default=None, alias="itemCount")


class PortfolioSyncSummary(DTOModel):
    as_of: datetime = Field(alias="asOf")
    metadata_fetched: bool = Field(alias="metadataFetched")
    endpoints: list[SyncEndpointSummary]


class EndpointAttemptReport(DTOModel):
    endpoint: str
    last_attempt_at: datetime | None = Field(default=None, alias="lastAttemptAt")
    last_success_at: datetime | None = Field(default=None, alias="lastSuccessAt")
    last_status: str | None = Field(default=None, alias="lastStatus")
    item_count: int | None = Field(default=None, alias="itemCount")
    last_error: str | None = Field(default=None, alias="lastError")


class MetadataFreshnessReport(DTOModel):
    endpoint: str
    fresh: bool
    ttl_hours: int = Field(alias="ttlHours")
    checked_at: datetime = Field(alias="checkedAt")
    last_success_at: datetime | None = Field(default=None, alias="lastSuccessAt")


class InstrumentMappingIssueReport(DTOModel):
    t212_ticker: str = Field(alias="t212Ticker")
    isin: str | None = None
    yahoo_ticker: str | None = Field(default=None, alias="yahooTicker")
    mapping_status: str = Field(alias="mappingStatus")
    mapping_source: str | None = Field(default=None, alias="mappingSource")
    mapping_details: dict[str, object] | None = Field(default=None, alias="mappingDetails")
    mapped_at: datetime | None = Field(default=None, alias="mappedAt")


class ReconciliationIssueReport(DTOModel):
    t212_ticker: str = Field(alias="t212Ticker")
    ts: datetime
    replayed_quantity: Decimal = Field(alias="replayedQuantity")
    live_quantity: Decimal = Field(alias="liveQuantity")
    difference_quantity: Decimal = Field(alias="differenceQuantity")
    tolerance_quantity: Decimal = Field(alias="toleranceQuantity")
    status: str


class QualityReport(DTOModel):
    as_of: datetime = Field(alias="asOf")
    overall_status: str = Field(alias="overallStatus")
    endpoint_statuses: list[EndpointAttemptReport] = Field(alias="endpointStatuses")
    metadata_freshness: MetadataFreshnessReport = Field(alias="metadataFreshness")
    unresolved_instruments: list[InstrumentMappingIssueReport] = Field(
        alias="unresolvedInstruments"
    )
    ambiguous_instruments: list[InstrumentMappingIssueReport] = Field(alias="ambiguousInstruments")
    override_required_instruments: list[InstrumentMappingIssueReport] = Field(
        alias="overrideRequiredInstruments"
    )
    reconciliation_mismatches: list[ReconciliationIssueReport] = Field(
        alias="reconciliationMismatches"
    )
    unsupported_actions: list[ReconciliationIssueReport] = Field(alias="unsupportedActions")


PositionsResponse = list[Position]
