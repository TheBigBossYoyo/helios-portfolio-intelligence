from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

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


class AccountCash(DTOModel):
    available_to_trade: Decimal | None = Field(default=None, alias="availableToTrade")
    reserved_for_orders: Decimal | None = Field(default=None, alias="reservedForOrders")
    in_pies: Decimal | None = Field(default=None, alias="inPies")


class AccountInvestments(DTOModel):
    current_value: Decimal | None = Field(default=None, alias="currentValue")
    total_cost: Decimal | None = Field(default=None, alias="totalCost")
    realized_profit_loss: Decimal | None = Field(default=None, alias="realizedProfitLoss")
    unrealized_profit_loss: Decimal | None = Field(default=None, alias="unrealizedProfitLoss")


class AccountSummary(DTOModel):
    """`GET /equity/account/summary`: the totals the Trading 212 app itself shows.

    The one source for every *current* figure the dashboard displays. Reconstructing "now" from
    positions plus a replayed cash balance put three different totals on three pages; this is
    the number the operator can check against their own app.
    """

    id: int | None = None
    currency: str | None = None
    total_value: Decimal | None = Field(default=None, alias="totalValue")
    cash: AccountCash | None = None
    investments: AccountInvestments | None = None


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
    # Absent for an order that never executed (CANCELLED, REJECTED...). Trading 212 lists those
    # in the same history with no `fill` object; requiring one made a single cancelled order
    # abort the entire sync of a real account.
    fill: HistoricalOrderFill | None = None
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
    trading212_environment: Literal["demo", "live"] = Field(alias="trading212Environment")


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


class ExportReport(BaseModel):
    """One entry of Trading 212's export list. The download link only exists once Finished."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    report_id: int = Field(alias="reportId")
    status: str
    download_link: str | None = Field(default=None, alias="downloadLink")


class InstrumentPricePointModel(DTOModel):
    as_of_date: date = Field(alias="asOfDate")
    close: Decimal
    currency: str
    close_eur: Decimal | None = Field(default=None, alias="closeEur")


class InstrumentPositionPointModel(DTOModel):
    as_of_date: date = Field(alias="asOfDate")
    quantity: Decimal
    value_eur: Decimal | None = Field(default=None, alias="valueEur")
    invested_eur: Decimal = Field(alias="investedEur")


class InstrumentTradeModel(DTOModel):
    ts: datetime
    side: str
    quantity: Decimal | None = None
    price: Decimal | None = None
    value_eur: Decimal | None = Field(default=None, alias="valueEur")
    realised_eur: Decimal | None = Field(default=None, alias="realisedEur")


class InstrumentDividendModel(DTOModel):
    paid_on: datetime = Field(alias="paidOn")
    amount_eur: Decimal | None = Field(default=None, alias="amountEur")
    quantity: Decimal | None = None
    per_share: Decimal | None = Field(default=None, alias="perShare")


class InstrumentPriceReturnModel(DTOModel):
    key: str
    label: str
    start_date: date | None = Field(default=None, alias="startDate")
    change_pct: float | None = Field(default=None, alias="changePct")


class InstrumentPeriodResultModel(DTOModel):
    key: str
    label: str
    result_eur: Decimal | None = Field(default=None, alias="resultEur")
    return_pct: float | None = Field(default=None, alias="returnPct")
    price_change_pct: float | None = Field(default=None, alias="priceChangePct")


class InstrumentDetailModel(DTOModel):
    """One instrument: its price history, your position in it, trades, dividends and results."""

    ticker: str
    name: str | None = None
    isin: str | None = None
    currency: str | None = None
    instrument_type: str | None = Field(default=None, alias="instrumentType")
    exchange: str | None = None
    market_symbol: str | None = Field(default=None, alias="marketSymbol")
    sector: str | None = None
    quantity: Decimal
    first_bought: datetime | None = Field(default=None, alias="firstBought")
    bought_eur: Decimal = Field(alias="boughtEur")
    sold_eur: Decimal = Field(alias="soldEur")
    dividends_eur: Decimal = Field(alias="dividendsEur")
    realised_eur: Decimal = Field(alias="realisedEur")
    value_eur: Decimal | None = Field(default=None, alias="valueEur")
    result_eur: Decimal | None = Field(default=None, alias="resultEur")
    high: InstrumentPricePointModel | None = None
    low: InstrumentPricePointModel | None = None
    prices: list[InstrumentPricePointModel]
    positions: list[InstrumentPositionPointModel]
    trades: list[InstrumentTradeModel]
    dividends: list[InstrumentDividendModel]
    price_returns: list[InstrumentPriceReturnModel] = Field(alias="priceReturns")
    periods: list[InstrumentPeriodResultModel]


class CardHistoryStatusModel(DTOModel):
    enabled: bool
    last_requested_at: datetime | None = Field(default=None, alias="lastRequestedAt")
    last_downloaded_at: datetime | None = Field(default=None, alias="lastDownloadedAt")
    pending: bool
    last_status: str | None = Field(default=None, alias="lastStatus")
    card_rows: int = Field(alias="cardRows")
    cash_rows: int = Field(alias="cashRows")


class CardRefreshModel(DTOModel):
    action: str
    detail: str
    rows_stored: int = Field(default=0, alias="rowsStored")


class CardTransactionModel(DTOModel):
    row_id: str = Field(alias="rowId")
    ts: datetime
    action: str
    amount: Decimal
    currency: str | None = None
    merchant_name: str | None = Field(default=None, alias="merchantName")
    merchant_category: str | None = Field(default=None, alias="merchantCategory")


class CashbackEntryModel(DTOModel):
    ts: datetime
    amount: Decimal


class SpendingGroupModel(DTOModel):
    key: str
    spent: Decimal
    count: int


class SpendingMonthModel(DTOModel):
    key: str
    label: str
    spent: Decimal
    cashback: Decimal
    count: int


class CardSummaryModel(DTOModel):
    currency: str | None = None
    first_date: date | None = Field(default=None, alias="firstDate")
    last_date: date | None = Field(default=None, alias="lastDate")
    spent: Decimal
    refunded: Decimal
    cashback: Decimal
    cashback_rate: float | None = Field(default=None, alias="cashbackRate")
    months: list[SpendingMonthModel]
    categories: list[SpendingGroupModel]
    merchants: list[SpendingGroupModel]
    transactions: list[CardTransactionModel]
    cashback_entries: list[CashbackEntryModel] = Field(
        default_factory=list, alias="cashbackEntries"
    )


class InstrumentMatchModel(DTOModel):
    ticker: str
    name: str | None = None
    isin: str | None = None
    currency: str | None = None
    instrument_type: str | None = Field(default=None, alias="instrumentType")
    watched: bool
    held: bool


class WatchEntryModel(DTOModel):
    ticker: str
    name: str | None = None
    currency: str | None = None
    instrument_type: str | None = Field(default=None, alias="instrumentType")
    note: str | None = None
    added_at: datetime = Field(alias="addedAt")
    held: bool
    priced: bool
    last_close: Decimal | None = Field(default=None, alias="lastClose")
    last_date: date | None = Field(default=None, alias="lastDate")
    day_change_pct: float | None = Field(default=None, alias="dayChangePct")
    month_change_pct: float | None = Field(default=None, alias="monthChangePct")
    active_alerts: int = Field(alias="activeAlerts")


class WatchRequest(DTOModel):
    ticker: str = Field(min_length=1, max_length=64)
    note: str | None = Field(default=None, max_length=200)


class BackupStatusModel(DTOModel):
    enabled: bool
    directory: str
    last_path: str | None = Field(default=None, alias="lastPath")
    last_at: datetime | None = Field(default=None, alias="lastAt")
    last_size_bytes: int | None = Field(default=None, alias="lastSizeBytes")
    count: int


class StorageStatusModel(DTOModel):
    database_bytes: int = Field(alias="databaseBytes")
    wal_bytes: int = Field(alias="walBytes")
    free_bytes: int = Field(alias="freeBytes")
    raw_news_rows: int = Field(alias="rawNewsRows")
    raw_snapshot_rows: int = Field(alias="rawSnapshotRows")
    raw_stored_bytes: int = Field(alias="rawStoredBytes")


class StorageCompactModel(DTOModel):
    vacuumed: bool
    bytes_before: int = Field(alias="bytesBefore")
    bytes_after: int = Field(alias="bytesAfter")
    detail: str
    status: StorageStatusModel


class PriceAlertModel(DTOModel):
    id: int
    ticker: str
    kind: str
    threshold: Decimal
    note: str | None = None
    created_at: datetime = Field(alias="createdAt")
    active: bool
    triggered_at: datetime | None = Field(default=None, alias="triggeredAt")
    triggered_price: Decimal | None = Field(default=None, alias="triggeredPrice")


class PriceAlertCreateRequest(DTOModel):
    ticker: str = Field(min_length=1, max_length=64)
    #: above / below: a price in the instrument's currency; gain_pct / loss_pct: percent on the
    #: average price paid.
    kind: Literal["above", "below", "gain_pct", "loss_pct"]
    threshold: Decimal = Field(gt=0)
    note: str | None = Field(default=None, max_length=200)


class NotificationModel(DTOModel):
    id: int
    kind: str
    title: str
    body: str
    url: str | None = None
    created_at: datetime = Field(alias="createdAt")
    delivered_at: datetime | None = Field(default=None, alias="deliveredAt")


class CardBudgetModel(DTOModel):
    category: str
    monthly_limit: Decimal = Field(alias="monthlyLimit")


class CardBudgetWriteRequest(DTOModel):
    category: str = Field(min_length=1, max_length=64)
    #: None (or omitted) removes the budget.
    monthly_limit: Decimal | None = Field(default=None, alias="monthlyLimit", ge=0)


class UnlabelledWithdrawalModel(DTOModel):
    reference: str
    ts: datetime
    amount: Decimal
    currency: str | None = None


class CardHistoryModel(DTOModel):
    """Card spending from Trading 212 exports, and where the export pipeline stands."""

    status: CardHistoryStatusModel
    summary: CardSummaryModel
    budgets: list[CardBudgetModel] = Field(default_factory=list)
    unlabelled: list[UnlabelledWithdrawalModel] = Field(default_factory=list)


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
    #: headline / summary: the text names the holding; unconfirmed: the feed is bound to it but
    #: the text does not name it; market: not bound to any holding.
    relevance: str | None = None
    matched_term: str | None = Field(default=None, alias="matchedTerm")
    held: bool | None = None


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


# --- Settings surface -----------------------------------------------------------------
#
# The write models below deliberately do NOT inherit DTOModel: it sets `extra="allow"`, which is
# right for permissive broker payloads and wrong for a request that changes credentials. An
# unrecognised field here means the caller and the server disagree about what is being written,
# and on this surface that is worth a 422 rather than a silent drop.


class SettingsWriteModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class CredentialStatusModel(DTOModel):
    """A credential's presence and impact. Carries no part of the value beyond a 4-char tail."""

    field: str
    label: str
    requirement: str
    present: bool
    hint: str | None = None
    source: str
    unlocks: str
    without: str
    signup: str


class SettingsSnapshotModel(DTOModel):
    credentials: list[CredentialStatusModel]
    editable: dict[str, str]
    keyring_backend: str = Field(alias="keyringBackend")
    keyring_available: bool = Field(alias="keyringAvailable")
    keyring_detail: str = Field(alias="keyringDetail")
    env_path: str = Field(alias="envPath")
    allowed_t212_base_urls: list[str] = Field(alias="allowedT212BaseUrls")
    active_database: str = Field(alias="activeDatabase")
    restart_required: bool = Field(alias="restartRequired")
    writable: bool
    read_only_reason: str | None = Field(alias="readOnlyReason")
    t212_environment: str = Field(alias="t212Environment")
    t212_pending_environment: str = Field(alias="t212PendingEnvironment")


class CredentialWriteRequest(SettingsWriteModel):
    """One credential. A blank value clears it rather than storing an empty string."""

    field: str
    value: str


class Trading212ConnectRequest(SettingsWriteModel):
    """Environment, key and secret together: they only mean anything as a set."""

    environment: Literal["demo", "live"]
    api_key: str = Field(alias="apiKey")
    api_secret: str = Field(alias="apiSecret")


class Trading212ConnectResponse(DTOModel):
    environment: str
    verified: str
    restart_required: bool = Field(alias="restartRequired")
    detail: str


class CredentialWriteResponse(DTOModel):
    field: str
    stored_in: str = Field(alias="storedIn")
    verified: str | None = None
    restart_required: bool = Field(alias="restartRequired")
    detail: str


class EditableSettingRequest(SettingsWriteModel):
    field: str
    value: str


class EditableSettingResponse(DTOModel):
    field: str
    env_name: str = Field(alias="envName")
    value: str
    restart_required: bool = Field(alias="restartRequired")
    detail: str


class DatabaseInfoModel(DTOModel):
    filename: str
    size_bytes: int = Field(alias="sizeBytes")
    modified_at: datetime | None = Field(default=None, alias="modifiedAt")
    active: bool
    schema_version: str | None = Field(default=None, alias="schemaVersion")


class DatabaseListModel(DTOModel):
    data_dir: str = Field(alias="dataDir")
    active: str
    databases: list[DatabaseInfoModel]


class DatabaseCreateRequest(SettingsWriteModel):
    filename: str


class DatabaseSwitchRequest(SettingsWriteModel):
    filename: str


class DatabaseActionResponse(DTOModel):
    filename: str
    created: bool
    active: bool
    restart_required: bool = Field(alias="restartRequired")
    detail: str


class RestartResponse(DTOModel):
    scheduled: bool
    detail: str


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
    dividend_eur: Decimal = Field(default=Decimal("0"), alias="dividendEur")
    interest_eur: Decimal = Field(default=Decimal("0"), alias="interestEur")
    fee_eur: Decimal = Field(default=Decimal("0"), alias="feeEur")
    net_deposits_to_date_eur: Decimal = Field(default=Decimal("0"), alias="netDepositsToDateEur")
    deposit_eur: Decimal = Field(default=Decimal("0"), alias="depositEur")
    withdrawal_eur: Decimal = Field(default=Decimal("0"), alias="withdrawalEur")
    card_spending_eur: Decimal = Field(default=Decimal("0"), alias="cardSpendingEur")
    cashback_eur: Decimal = Field(default=Decimal("0"), alias="cashbackEur")


class HoldingMovementModel(DTOModel):
    """What one holding did over a period: its share of the investment result."""

    ticker: str
    status: str
    start_value_eur: Decimal | None = Field(default=None, alias="startValueEur")
    end_value_eur: Decimal | None = Field(default=None, alias="endValueEur")
    start_quantity: Decimal = Field(alias="startQuantity")
    end_quantity: Decimal = Field(alias="endQuantity")
    bought_eur: Decimal = Field(alias="boughtEur")
    sold_eur: Decimal = Field(alias="soldEur")
    dividends_eur: Decimal = Field(alias="dividendsEur")
    result_eur: Decimal | None = Field(default=None, alias="resultEur")
    return_pct: float | None = Field(default=None, alias="returnPct")
    price_change_pct: float | None = Field(default=None, alias="priceChangePct")
    detail: str | None = None
    name: str | None = None


class PeriodSummaryModel(DTOModel):
    """A period's change in value, split into money moved and investment result."""

    key: str
    label: str
    status: str
    start_date: date | None = Field(default=None, alias="startDate")
    end_date: date | None = Field(default=None, alias="endDate")
    start_value_eur: Decimal | None = Field(default=None, alias="startValueEur")
    end_value_eur: Decimal | None = Field(default=None, alias="endValueEur")
    value_change_eur: Decimal | None = Field(default=None, alias="valueChangeEur")
    deposits_eur: Decimal = Field(alias="depositsEur")
    withdrawals_eur: Decimal = Field(alias="withdrawalsEur")
    net_deposits_eur: Decimal = Field(alias="netDepositsEur")
    investment_result_eur: Decimal | None = Field(default=None, alias="investmentResultEur")
    market_eur: Decimal | None = Field(default=None, alias="marketEur")
    dividends_eur: Decimal = Field(alias="dividendsEur")
    interest_eur: Decimal = Field(alias="interestEur")
    fees_eur: Decimal = Field(alias="feesEur")
    card_spending_eur: Decimal = Field(default=Decimal("0"), alias="cardSpendingEur")
    cashback_eur: Decimal = Field(default=Decimal("0"), alias="cashbackEur")
    twr: float | None = None
    unvalued_days: int = Field(alias="unvaluedDays")
    detail: str | None = None
    holdings: list[HoldingMovementModel] = Field(default_factory=list)
    unattributed_eur: Decimal | None = Field(default=None, alias="unattributedEur")


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
    period_summaries: list[PeriodSummaryModel] = Field(
        default_factory=list, alias="periodSummaries"
    )
    monthly_summaries: list[PeriodSummaryModel] = Field(
        default_factory=list, alias="monthlySummaries"
    )
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
