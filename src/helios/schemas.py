from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class DTOModel(BaseModel):
    # from_attributes lets the analytics dataclasses (and their nested dataclasses) validate
    # straight into these DTOs without an intermediate dict round-trip.
    model_config = ConfigDict(extra="allow", populate_by_name=True, from_attributes=True)


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


class NewsItemModel(DTOModel):
    dedupe_key: str = Field(alias="dedupeKey")
    feed_key: str = Field(alias="feedKey")
    provider: str
    source_label: str = Field(alias="sourceLabel")
    t212_ticker: str | None = Field(default=None, alias="t212Ticker")
    isin: str | None = None
    headline: str
    summary: str | None = None
    url: str
    published_at: datetime | None = Field(default=None, alias="publishedAt")
    fetched_at: datetime = Field(alias="fetchedAt")


class JournalEntryModel(DTOModel):
    id: int
    thesis_id: int | None = Field(default=None, alias="thesisId")
    created_at: datetime = Field(alias="createdAt")
    note: str
    tags: str | None = None


class ThesisModel(DTOModel):
    id: int
    t212_ticker: str | None = Field(default=None, alias="t212Ticker")
    isin: str | None = None
    title: str
    body: str
    conviction: str
    status: str
    opened_on: date = Field(alias="openedOn")
    outcome_note: str | None = Field(default=None, alias="outcomeNote")
    closed_at: datetime | None = Field(default=None, alias="closedAt")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class ThesisContextModel(DTOModel):
    t212_ticker: str | None = Field(default=None, alias="t212Ticker")
    weight: float | None = None
    contribution: float | None = None
    news_count: int = Field(alias="newsCount")
    latest_news_headline: str | None = Field(default=None, alias="latestNewsHeadline")


class ThesisDetailModel(DTOModel):
    thesis: ThesisModel
    context: ThesisContextModel
    allowed_transitions: list[str] = Field(alias="allowedTransitions")
    editable: bool
    journal: list[JournalEntryModel]


class ThesisCreateRequest(DTOModel):
    title: str
    body: str
    t212_ticker: str | None = Field(default=None, alias="t212Ticker")
    isin: str | None = None
    conviction: str = "medium"
    opened_on: date | None = Field(default=None, alias="openedOn")


class ThesisEditRequest(DTOModel):
    title: str | None = None
    body: str | None = None
    conviction: str | None = None


class ThesisTransitionRequest(DTOModel):
    to_status: str = Field(alias="toStatus")
    outcome_note: str | None = Field(default=None, alias="outcomeNote")


class JournalCreateRequest(DTOModel):
    note: str
    thesis_id: int | None = Field(default=None, alias="thesisId")
    tags: str | None = None


class AiObservationModel(DTOModel):
    rank: int
    category: str
    t212_ticker: str | None = Field(default=None, alias="t212Ticker")
    headline: str
    detail: str
    evidence: str
    severity: str


class AiAnalysisModel(DTOModel):
    status: str
    as_of: datetime = Field(alias="asOf")
    model: str
    served_by_model: str | None = Field(default=None, alias="servedByModel")
    effort: str | None = None
    summary: str | None = None
    observations: list[AiObservationModel]
    unavailable_metrics: list[str] = Field(alias="unavailableMetrics")
    input_tokens: int | None = Field(default=None, alias="inputTokens")
    output_tokens: int | None = Field(default=None, alias="outputTokens")
    cache_read_tokens: int | None = Field(default=None, alias="cacheReadTokens")
    disclosure: str
    detail: str | None = None


class NewsSyncSummaryModel(DTOModel):
    as_of: datetime = Field(alias="asOf")
    feeds_configured: int = Field(alias="feedsConfigured")
    feeds_fetched: int = Field(alias="feedsFetched")
    raw_stored: int = Field(alias="rawStored")
    items_parsed: int = Field(alias="itemsParsed")
    items_written: int = Field(alias="itemsWritten")
    duplicates_skipped: int = Field(alias="duplicatesSkipped")
    cross_source_merges: int = Field(alias="crossSourceMerges")
    sources_used: list[str] = Field(alias="sourcesUsed")
    failures: list[str]
    notes: list[str]


class MetricValueModel(DTOModel):
    status: str
    value: float | None = None
    observations: int
    detail: str | None = None


class DailyReturnPointModel(DTOModel):
    as_of_date: date = Field(alias="asOfDate")
    value: float


class DailyValuePointModel(DTOModel):
    as_of_date: date = Field(alias="asOfDate")
    value: Decimal


class NavPointModel(DTOModel):
    as_of_date: date = Field(alias="asOfDate")
    nav_eur: Decimal | None = Field(default=None, alias="navEur")
    cash_balance_eur: Decimal = Field(alias="cashBalanceEur")
    securities_value_eur: Decimal | None = Field(default=None, alias="securitiesValueEur")
    external_flow_eur: Decimal = Field(alias="externalFlowEur")
    valuation_status: str = Field(alias="valuationStatus")


class ContributionItemModel(DTOModel):
    key: str
    weight: float
    status: str
    return_value: float | None = Field(default=None, alias="returnValue")
    contribution: float | None = None
    detail: str | None = None


class AttributionItemModel(DTOModel):
    key: str
    portfolio_weight: float = Field(alias="portfolioWeight")
    benchmark_weight: float = Field(alias="benchmarkWeight")
    allocation_effect: float = Field(alias="allocationEffect")
    selection_effect: float = Field(alias="selectionEffect")
    interaction_effect: float = Field(alias="interactionEffect")
    total_effect: float = Field(alias="totalEffect")


class AttributionReportModel(DTOModel):
    status: str
    active_return: float | None = Field(default=None, alias="activeReturn")
    items: list[AttributionItemModel]
    detail: str | None = None


class ClusterAssignmentModel(DTOModel):
    key: str
    cluster: int
    weight: float


class CorrelationClusterReportModel(DTOModel):
    status: str
    observations: int
    distance_threshold: float = Field(alias="distanceThreshold")
    cluster_count: int | None = Field(default=None, alias="clusterCount")
    assignments: list[ClusterAssignmentModel]
    detail: str | None = None


class PassiveCounterfactualReportModel(DTOModel):
    status: str
    benchmark_key: str = Field(alias="benchmarkKey")
    benchmark_label: str = Field(alias="benchmarkLabel")
    invested_eur: Decimal | None = Field(default=None, alias="investedEur")
    final_value_eur: Decimal | None = Field(default=None, alias="finalValueEur")
    actual_nav_eur: Decimal | None = Field(default=None, alias="actualNavEur")
    difference_eur: Decimal | None = Field(default=None, alias="differenceEur")
    series: list[DailyValuePointModel]
    excluded_flow_count: int = Field(alias="excludedFlowCount")
    detail: str | None = None


class RegressionResultModel(DTOModel):
    status: str
    observations: int
    r_squared: float | None = Field(default=None, alias="rSquared")
    intercept: float | None = None
    coefficients: dict[str, float | None]
    detail: str | None = None


class BenchmarkDefinitionModel(DTOModel):
    key: str
    label: str
    provider_symbol: str = Field(alias="providerSymbol")
    currency_code: str | None = Field(default=None, alias="currencyCode")
    description: str


class BenchmarkReportModel(DTOModel):
    benchmark: BenchmarkDefinitionModel
    beta: MetricValueModel
    alpha: MetricValueModel
    r_squared: MetricValueModel = Field(alias="rSquared")
    correlation: MetricValueModel
    tracking_error: MetricValueModel = Field(alias="trackingError")
    information_ratio: MetricValueModel = Field(alias="informationRatio")


class PerformanceReplaySummaryModel(DTOModel):
    as_of: date = Field(alias="asOf")
    start_date: date | None = Field(default=None, alias="startDate")
    end_date: date | None = Field(default=None, alias="endDate")
    flow_timing: str = Field(alias="flowTiming")
    holdings_written: int = Field(alias="holdingsWritten")
    nav_written: int = Field(alias="navWritten")
    unsupported_quantity_events: list[str] = Field(alias="unsupportedQuantityEvents")
    missing_price_symbols: list[str] = Field(alias="missingPriceSymbols")
    stale_price_symbols: list[str] = Field(alias="stalePriceSymbols")
    missing_fx_currencies: list[str] = Field(alias="missingFxCurrencies")
    stale_fx_currencies: list[str] = Field(alias="staleFxCurrencies")
    excluded_flow_currencies: list[str] = Field(alias="excludedFlowCurrencies")
    notes: list[str]


class PerformanceReportModel(DTOModel):
    as_of: date | None = Field(default=None, alias="asOf")
    start_date: date | None = Field(default=None, alias="startDate")
    end_date: date | None = Field(default=None, alias="endDate")
    flow_timing: str = Field(alias="flowTiming")
    annualization_days: int = Field(alias="annualizationDays")
    cumulative_twr: MetricValueModel = Field(alias="cumulativeTwr")
    xirr: MetricValueModel
    annualized_return: MetricValueModel = Field(alias="annualizedReturn")
    volatility: MetricValueModel
    downside_volatility: MetricValueModel = Field(alias="downsideVolatility")
    sharpe: MetricValueModel
    sortino: MetricValueModel
    calmar: MetricValueModel
    max_drawdown: MetricValueModel = Field(alias="maxDrawdown")
    time_underwater_days: MetricValueModel = Field(alias="timeUnderwaterDays")
    recovery_days: MetricValueModel = Field(alias="recoveryDays")
    beta_vs_benchmarks: list[BenchmarkReportModel] = Field(alias="betaVsBenchmarks")
    hhi: MetricValueModel
    effective_number_of_positions: MetricValueModel = Field(alias="effectiveNumberOfPositions")
    top5_weight: MetricValueModel = Field(alias="top5Weight")
    var_95_1d: MetricValueModel = Field(alias="var95_1d")
    cvar_95_1d: MetricValueModel = Field(alias="cvar95_1d")
    var_99_1d: MetricValueModel = Field(alias="var99_1d")
    cvar_99_1d: MetricValueModel = Field(alias="cvar99_1d")
    var_95_10d: MetricValueModel = Field(alias="var95_10d")
    cvar_95_10d: MetricValueModel = Field(alias="cvar95_10d")
    var_99_10d: MetricValueModel = Field(alias="var99_10d")
    cvar_99_10d: MetricValueModel = Field(alias="cvar99_10d")
    ff5_momentum_regression: RegressionResultModel = Field(alias="ff5MomentumRegression")
    nav_series: list[NavPointModel] = Field(alias="navSeries")
    daily_twr: list[DailyReturnPointModel] = Field(alias="dailyTwr")
    rolling_volatility_30d: list[DailyReturnPointModel] = Field(alias="rollingVolatility30d")
    rolling_volatility_90d: list[DailyReturnPointModel] = Field(alias="rollingVolatility90d")
    rolling_beta_30d: list[DailyReturnPointModel] = Field(alias="rollingBeta30d")
    rolling_beta_90d: list[DailyReturnPointModel] = Field(alias="rollingBeta90d")
    contributions: list[ContributionItemModel]
    attribution: AttributionReportModel
    correlation_clusters: CorrelationClusterReportModel = Field(alias="correlationClusters")
    passive_counterfactual: PassiveCounterfactualReportModel = Field(alias="passiveCounterfactual")
    notes: list[str]
