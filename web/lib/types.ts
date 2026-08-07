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
  externalFlowEur: string;
  valuationStatus: string;
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
}

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

/** Every fetch resolves to this: data, or a reason it is unavailable. Never a thrown page. */
export type ApiResult<T> =
  | { ok: true; data: T; timestamp: string }
  | { ok: false; error: string; status: number | null; timestamp: string };
