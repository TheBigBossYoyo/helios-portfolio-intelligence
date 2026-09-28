/**
 * Wire types for the Helios API.
 *
 * These mirror the backend Pydantic DTOs (camelCase aliases). Money and quantities arrive as
 * decimal *strings* — the backend stores exact `Decimal` and serialises without going through a
 * float, so parsing them into JS numbers would throw that exactness away. Keep them as strings
 * and only convert at the point of rendering or charting.
 */

export interface Health {
  status: string;
  trading212Configured: boolean;
  databaseReady: boolean;
  /** Which Trading 212 account the credentials belong to; null from an older API. */
  trading212Environment: "demo" | "live" | null;
}

export interface PositionInstrument {
  ticker: string;
  isin: string | null;
  name: string | null;
  currency: string | null;
}

export interface WalletImpact {
  currency: string | null;
  currentValue: string | null;
  fxImpact: string | null;
  totalCost: string | null;
  unrealizedProfitLoss: string | null;
}

/**
 * Trading 212's own current totals (`/equity/account/summary`), as decimal strings.
 *
 * The single source for every "now" figure on every page, so the Overview, Holdings and the
 * allocation ring can never show three different totals — and each matches the Trading 212 app.
 */
export interface AccountSummary {
  currency: string | null;
  totalValue: string | null;
  cash: {
    availableToTrade: string | null;
    reservedForOrders: string | null;
    inPies: string | null;
  } | null;
  investments: {
    currentValue: string | null;
    totalCost: string | null;
    realizedProfitLoss: string | null;
    unrealizedProfitLoss: string | null;
  } | null;
}

export interface Position {
  instrument: PositionInstrument;
  quantity: string;
  averagePricePaid: string | null;
  currentPrice: string | null;
  walletImpact: WalletImpact | null;
}

export interface MetricValue {
  status: string;
  value: number | null;
  observations: number;
  detail: string | null;
}

export interface DailyReturnPoint {
  asOfDate: string;
  value: number;
}

export interface NavPoint {
  asOfDate: string;
  navEur: string | null;
  cashBalanceEur: string;
  securitiesValueEur: string | null;
  /** Deposits (positive) and withdrawals (negative) that day: money moved, not performance. */
  externalFlowEur: string;
  valuationStatus: string;
  dividendEur?: string;
  interestEur?: string;
  feeEur?: string;
  /** Everything put in minus everything taken out, up to this day: the "money in" line. */
  netDepositsToDateEur?: string;
}

/**
 * A period's change in value, split into money moved and investment result.
 *
 * Identities the backend guarantees: `valueChange = netDeposits + investmentResult` and
 * `investmentResult = market + dividends + interest + fees`. Amounts are decimal strings.
 */
/** What one holding did over a period: its share of the investment result. */
export interface HoldingMovement {
  ticker: string;
  name?: string | null;
  status: string;
  /** Zero when the holding was not owned at that end of the period. */
  startValueEur: string | null;
  endValueEur: string | null;
  startQuantity: string;
  endQuantity: string;
  boughtEur: string;
  soldEur: string;
  dividendsEur: string;
  /** end - start - bought + sold + dividends. Buying and selling are not gains or losses. */
  resultEur: string | null;
  /** Result relative to the money at work (start value + bought). */
  returnPct: number | null;
  /** The EUR price move between the two ends, when held at both. */
  priceChangePct: number | null;
  detail: string | null;
}

export interface PeriodSummary {
  key: string;
  label: string;
  status: string;
  startDate: string | null;
  endDate: string | null;
  startValueEur: string | null;
  endValueEur: string | null;
  valueChangeEur: string | null;
  depositsEur: string;
  withdrawalsEur: string;
  netDepositsEur: string;
  investmentResultEur: string | null;
  marketEur: string | null;
  dividendsEur: string;
  interestEur: string;
  feesEur: string;
  /** The card-payment part of withdrawalsEur (negative). Absent before a card export. */
  cardSpendingEur?: string;
  /** Card cashback: income, part of the investment result. */
  cashbackEur?: string;
  twr: number | null;
  unvaluedDays: number;
  detail: string | null;
  /** The investment result split by holding, largest move first. */
  holdings?: HoldingMovement[];
  /** Investment result no holding explains: interest, fees, dividends without a ticker. */
  unattributedEur?: string | null;
}

export interface DailyValuePoint {
  asOfDate: string;
  value: string;
}

export interface ContributionItem {
  key: string;
  weight: number;
  status: string;
  returnValue: number | null;
  contribution: number | null;
  detail: string | null;
}

export interface AttributionItem {
  key: string;
  portfolioWeight: number;
  benchmarkWeight: number;
  allocationEffect: number;
  selectionEffect: number;
  interactionEffect: number;
  totalEffect: number;
}

export interface AttributionReport {
  status: string;
  activeReturn: number | null;
  items: AttributionItem[];
  detail: string | null;
}

export interface ClusterAssignment {
  key: string;
  cluster: number;
  weight: number;
}

export interface CorrelationClusterReport {
  status: string;
  observations: number;
  distanceThreshold: number;
  clusterCount: number | null;
  assignments: ClusterAssignment[];
  detail: string | null;
}

export interface PassiveCounterfactualReport {
  status: string;
  benchmarkKey: string;
  benchmarkLabel: string;
  investedEur: string | null;
  finalValueEur: string | null;
  actualNavEur: string | null;
  differenceEur: string | null;
  series: DailyValuePoint[];
  excludedFlowCount: number;
  detail: string | null;
}

export interface RegressionResult {
  status: string;
  observations: number;
  rSquared: number | null;
  intercept: number | null;
  coefficients: Record<string, number | null>;
  detail: string | null;
}

export interface BenchmarkDefinition {
  key: string;
  label: string;
  providerSymbol: string;
  currencyCode: string | null;
  description: string;
}

export interface BenchmarkReport {
  benchmark: BenchmarkDefinition;
  beta: MetricValue;
  alpha: MetricValue;
  rSquared: MetricValue;
  correlation: MetricValue;
  trackingError: MetricValue;
  informationRatio: MetricValue;
}

export interface PerformanceReport {
  asOf: string | null;
  startDate: string | null;
  endDate: string | null;
  flowTiming: string;
  annualizationDays: number;
  cumulativeTwr: MetricValue;
  xirr: MetricValue;
  annualizedReturn: MetricValue;
  volatility: MetricValue;
  downsideVolatility: MetricValue;
  sharpe: MetricValue;
  sortino: MetricValue;
  calmar: MetricValue;
  maxDrawdown: MetricValue;
  timeUnderwaterDays: MetricValue;
  recoveryDays: MetricValue;
  betaVsBenchmarks: BenchmarkReport[];
  hhi: MetricValue;
  effectiveNumberOfPositions: MetricValue;
  top5Weight: MetricValue;
  var95_1d: MetricValue;
  cvar95_1d: MetricValue;
  var99_1d: MetricValue;
  cvar99_1d: MetricValue;
  var95_10d: MetricValue;
  cvar95_10d: MetricValue;
  var99_10d: MetricValue;
  cvar99_10d: MetricValue;
  ff5MomentumRegression: RegressionResult;
  navSeries: NavPoint[];
  dailyTwr: DailyReturnPoint[];
  rollingVolatility30d: DailyReturnPoint[];
  rollingVolatility90d: DailyReturnPoint[];
  rollingBeta30d: DailyReturnPoint[];
  rollingBeta90d: DailyReturnPoint[];
  contributions: ContributionItem[];
  attribution: AttributionReport;
  correlationClusters: CorrelationClusterReport;
  passiveCounterfactual: PassiveCounterfactualReport;
  notes: string[];
  periodSummaries?: PeriodSummary[];
  monthlySummaries?: PeriodSummary[];
}

export interface NewsItem {
  dedupeKey: string;
  feedKey: string;
  sourceLabel: string;
  t212Ticker: string | null;
  isin: string | null;
  headline: string;
  summary: string | null;
  url: string;
  publishedAt: string | null;
  fetchedAt: string;
  /**
   * headline / summary: the text names the holding the feed is bound to; unconfirmed: bound to
   * it but the text does not name it; market: not bound to any holding.
   */
  relevance?: NewsRelevance | null;
  matchedTerm?: string | null;
  /** Whether the bound holding is still in the portfolio. */
  held?: boolean | null;
}

export interface InstrumentPricePoint {
  asOfDate: string;
  close: string;
  currency: string;
  closeEur: string | null;
}

export interface InstrumentDetail {
  ticker: string;
  name: string | null;
  isin: string | null;
  currency: string | null;
  instrumentType: string | null;
  exchange: string | null;
  marketSymbol: string | null;
  sector: string | null;
  quantity: string;
  firstBought: string | null;
  boughtEur: string;
  soldEur: string;
  dividendsEur: string;
  realisedEur: string;
  valueEur: string | null;
  /** value - bought + sold + dividends: everything this holding has made or lost. */
  resultEur: string | null;
  high: InstrumentPricePoint | null;
  low: InstrumentPricePoint | null;
  prices: InstrumentPricePoint[];
  positions: { asOfDate: string; quantity: string; valueEur: string | null; investedEur: string }[];
  trades: {
    ts: string;
    side: string;
    quantity: string | null;
    price: string | null;
    valueEur: string | null;
    realisedEur: string | null;
  }[];
  dividends: { paidOn: string; amountEur: string | null; quantity: string | null; perShare: string | null }[];
  priceReturns: { key: string; label: string; startDate: string | null; changePct: number | null }[];
  periods: {
    key: string;
    label: string;
    resultEur: string | null;
    returnPct: number | null;
    priceChangePct: number | null;
  }[];
}

export interface CardTransaction {
  rowId: string;
  ts: string;
  action: string;
  /** Negative for a payment, positive for a refund. */
  amount: string;
  currency: string | null;
  merchantName: string | null;
  merchantCategory: string | null;
}

export interface SpendingGroup {
  key: string;
  spent: string;
  count: number;
}

export interface SpendingMonth {
  key: string;
  label: string;
  spent: string;
  cashback: string;
  count: number;
}

export interface CardSummary {
  currency: string | null;
  firstDate: string | null;
  lastDate: string | null;
  spent: string;
  refunded: string;
  cashback: string;
  cashbackRate: number | null;
  months: SpendingMonth[];
  categories: SpendingGroup[];
  merchants: SpendingGroup[];
  transactions: CardTransaction[];
  /** Every cashback payment, newest first, so it can be grouped like spending. */
  cashbackEntries?: { ts: string; amount: string }[];
}

export interface CardHistoryStatus {
  enabled: boolean;
  lastRequestedAt: string | null;
  lastDownloadedAt: string | null;
  pending: boolean;
  lastStatus: string | null;
  cardRows: number;
  cashRows: number;
}

export interface CardHistory {
  status: CardHistoryStatus;
  summary: CardSummary;
}

export type NewsRelevance = "headline" | "summary" | "unconfirmed" | "market";

export interface AiObservation {
  rank: number;
  category: string;
  t212Ticker: string | null;
  headline: string;
  detail: string;
  evidence: string;
  severity: string;
}

export interface AiAnalysis {
  status: string;
  asOf: string;
  model: string;
  servedByModel: string | null;
  effort: string | null;
  summary: string | null;
  observations: AiObservation[];
  unavailableMetrics: string[];
  inputTokens: number | null;
  outputTokens: number | null;
  cacheReadTokens: number | null;
  disclosure: string;
  detail: string | null;
}

export interface Thesis {
  id: number;
  t212Ticker: string | null;
  isin: string | null;
  title: string;
  body: string;
  conviction: string;
  status: string;
  openedOn: string;
  outcomeNote: string | null;
  closedAt: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface JournalEntry {
  id: number;
  thesisId: number | null;
  createdAt: string;
  note: string;
  tags: string | null;
}

export interface ThesisContext {
  t212Ticker: string | null;
  weight: number | null;
  contribution: number | null;
  newsCount: number;
  latestNewsHeadline: string | null;
}

export interface ThesisDetail {
  thesis: Thesis;
  context: ThesisContext;
  allowedTransitions: string[];
  editable: boolean;
  journal: JournalEntry[];
}

export interface EndpointAttemptReport {
  endpoint: string;
  lastAttemptAt: string | null;
  lastSuccessAt: string | null;
  lastStatus: string | null;
  itemCount: number | null;
  lastError: string | null;
}

export interface MetadataFreshnessReport {
  endpoint: string;
  fresh: boolean;
  ttlHours: number;
  checkedAt: string;
  lastSuccessAt: string | null;
}

export interface InstrumentMappingIssueReport {
  t212Ticker: string;
  isin: string | null;
  yahooTicker: string | null;
  mappingStatus: string;
  mappingSource: string | null;
  mappedAt: string | null;
}

export interface ReconciliationIssueReport {
  t212Ticker: string;
  ts: string;
  replayedQuantity: string;
  liveQuantity: string;
  differenceQuantity: string;
  toleranceQuantity: string;
  status: string;
}

export interface QualityReport {
  asOf: string;
  overallStatus: string;
  endpointStatuses: EndpointAttemptReport[];
  metadataFreshness: MetadataFreshnessReport;
  unresolvedInstruments: InstrumentMappingIssueReport[];
  ambiguousInstruments: InstrumentMappingIssueReport[];
  overrideRequiredInstruments: InstrumentMappingIssueReport[];
  reconciliationMismatches: ReconciliationIssueReport[];
  unsupportedActions: ReconciliationIssueReport[];
}

/**
 * A credential's presence, never its value.
 *
 * `hint` is the last four characters and nothing more — enough to recognise a key you pasted,
 * useless to anyone who did not already have it. The backend constructs this; there is no shape
 * of this type that can carry a secret.
 */
export interface CredentialStatus {
  field: string;
  label: string;
  requirement: string;
  present: boolean;
  hint: string | null;
  source: string;
  unlocks: string;
  without: string;
  signup: string;
}

export interface SettingsSnapshot {
  credentials: CredentialStatus[];
  editable: Record<string, string>;
  keyringBackend: string;
  keyringAvailable: boolean;
  keyringDetail: string;
  envPath: string;
  allowedT212BaseUrls: string[];
  activeDatabase: string;
  restartRequired: boolean;
  /** False under Docker Compose, where a saved change could never be read. */
  writable: boolean;
  /** Why, and where to change configuration instead. Null whenever `writable` is true. */
  readOnlyReason: string | null;
  /** The Trading 212 environment the running API syncs from. */
  t212Environment: "demo" | "live";
  /** The environment it will sync from after the next restart (differs while one is pending). */
  t212PendingEnvironment: "demo" | "live";
}

export interface DatabaseInfo {
  filename: string;
  sizeBytes: number;
  modifiedAt: string | null;
  active: boolean;
  schemaVersion: string | null;
}

export interface DatabaseList {
  dataDir: string;
  active: string;
  databases: DatabaseInfo[];
}

/** Every fetch resolves to this: data, or a reason it is unavailable. Never a thrown page. */
export type ApiResult<T> =
  | { ok: true; data: T; timestamp: string }
  | { ok: false; error: string; status: number | null; timestamp: string };
