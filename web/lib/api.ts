import "server-only";

import type {
  AiAnalysis,
  ApiResult,
  Health,
  JournalEntry,
  NewsItem,
  PerformanceReport,
  Position,
  QualityReport,
  Thesis,
} from "./types";

/**
 * Server-only Helios API client.
 *
 * The browser never talks to the API. Every read happens in a React Server Component, so the
 * API base URL and anything the backend holds (Trading 212 keys, OpenFIGI keys) stay on the
 * server. `import "server-only"` makes an accidental client import a build error rather than a
 * silent credential path.
 *
 * Nothing here throws: a failure becomes an `ApiResult` the page renders as an explicit
 * unavailable panel. A dashboard that blanks out tells you less than one that says why.
 */

const DEFAULT_TIMEOUT_MS = 5_000;

export function apiBaseUrl(): string {
  return process.env.HELIOS_API_URL || "http://127.0.0.1:8000";
}

async function getJson<T>(
  path: string,
  parse: (value: unknown) => T | null,
  timeoutMs = DEFAULT_TIMEOUT_MS,
): Promise<ApiResult<T>> {
  const timestamp = () => new Date().toISOString();
  try {
    const response = await fetch(`${apiBaseUrl()}${path}`, {
      cache: "no-store",
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (!response.ok) {
      return {
        ok: false,
        error: response.status === 404 ? "Not available yet" : `HTTP ${response.status}`,
        status: response.status,
        timestamp: timestamp(),
      };
    }
    const parsed = parse(await response.json());
    if (parsed === null) {
      return {
        ok: false,
        error: "Malformed response",
        status: response.status,
        timestamp: timestamp(),
      };
    }
    return { ok: true, data: parsed, timestamp: timestamp() };
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : "Network error";
    return { ok: false, error: message, status: null, timestamp: timestamp() };
  }
}

// --- narrowing helpers -----------------------------------------------------

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function str(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

/** Decimal-as-string, kept exact. Numbers are accepted and stringified for tolerance. */
function decimal(value: unknown): string | null {
  if (typeof value === "string") return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return null;
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function bool(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function list(value: unknown): unknown[] | null {
  return Array.isArray(value) ? value : null;
}

/**
 * The report is wide and every field is already validated server-side by Pydantic. Rather than
 * re-deriving 40 narrow parsers, check the shape is an object with the handful of fields the UI
 * cannot render without, then trust the typed contract. A missing optional renders as "—".
 */
function parseShape<T>(value: unknown, required: string[]): T | null {
  const object = record(value);
  if (!object) return null;
  for (const key of required) {
    if (!(key in object)) return null;
  }
  return object as T;
}

// --- parsers ---------------------------------------------------------------

export function parseHealth(value: unknown): Health | null {
  const object = record(value);
  if (!object) return null;
  const status = str(object.status);
  const configured = bool(object.trading212Configured);
  const ready = bool(object.databaseReady);
  if (status === null || configured === null || ready === null) return null;
  return { status, trading212Configured: configured, databaseReady: ready };
}

export function parsePositions(value: unknown): Position[] | null {
  const rows = list(value);
  if (!rows) return null;
  const positions: Position[] = [];
  for (const row of rows) {
    const object = record(row);
    if (!object) return null;
    const instrument = record(object.instrument);
    const ticker = instrument ? str(instrument.ticker) : null;
    const quantity = decimal(object.quantity);
    if (ticker === null || quantity === null) return null;
    const wallet = record(object.walletImpact);
    positions.push({
      instrument: {
        ticker,
        isin: instrument ? str(instrument.isin) : null,
        name: instrument ? str(instrument.name) : null,
        currency: instrument ? str(instrument.currency) : null,
      },
      quantity,
      averagePricePaid: decimal(object.averagePricePaid),
      currentPrice: decimal(object.currentPrice),
      walletImpact: wallet
        ? {
            currency: str(wallet.currency),
            currentValue: decimal(wallet.currentValue),
            fxImpact: decimal(wallet.fxImpact),
            totalCost: decimal(wallet.totalCost),
            unrealizedProfitLoss: decimal(wallet.unrealizedProfitLoss),
          }
        : null,
    });
  }
  return positions;
}

export function parsePerformanceReport(value: unknown): PerformanceReport | null {
  const report = parseShape<PerformanceReport>(value, [
    "flowTiming",
    "annualizationDays",
    "cumulativeTwr",
    "navSeries",
    "attribution",
    "passiveCounterfactual",
    "correlationClusters",
  ]);
  if (!report) return null;
  // The chart layer indexes these directly; a non-array would crash the render.
  const series: (keyof PerformanceReport)[] = [
    "navSeries",
    "dailyTwr",
    "rollingVolatility30d",
    "rollingVolatility90d",
    "rollingBeta30d",
    "rollingBeta90d",
    "contributions",
    "betaVsBenchmarks",
    "notes",
  ];
  for (const key of series) {
    if (!Array.isArray(report[key])) return null;
  }
  return report;
}

export function parseQualityReport(value: unknown): QualityReport | null {
  const report = parseShape<QualityReport>(value, [
    "asOf",
    "overallStatus",
    "endpointStatuses",
    "metadataFreshness",
  ]);
  if (!report) return null;
  const series: (keyof QualityReport)[] = [
    "endpointStatuses",
    "unresolvedInstruments",
    "ambiguousInstruments",
    "overrideRequiredInstruments",
    "reconciliationMismatches",
    "unsupportedActions",
  ];
  for (const key of series) {
    if (!Array.isArray(report[key])) return null;
  }
  return report;
}

export function parseNewsItems(value: unknown): NewsItem[] | null {
  const rows = list(value);
  if (!rows) return null;
  const items: NewsItem[] = [];
  for (const row of rows) {
    const object = record(row);
    if (!object) return null;
    const headline = str(object.headline);
    const url = str(object.url);
    const sourceLabel = str(object.sourceLabel);
    const dedupeKey = str(object.dedupeKey);
    if (headline === null || url === null || sourceLabel === null || dedupeKey === null) {
      return null;
    }
    // Attribution is not optional: an item with no source label or link is not renderable
    // without misrepresenting where it came from.
    items.push({
      dedupeKey,
      feedKey: str(object.feedKey) ?? "",
      sourceLabel,
      t212Ticker: str(object.t212Ticker),
      isin: str(object.isin),
      headline,
      summary: str(object.summary),
      url,
      publishedAt: str(object.publishedAt),
      fetchedAt: str(object.fetchedAt) ?? "",
    });
  }
  return items;
}

export function parseAiAnalysis(value: unknown): AiAnalysis | null {
  const report = parseShape<AiAnalysis>(value, ["status", "asOf", "model", "disclosure"]);
  if (!report) return null;
  if (!Array.isArray(report.observations) || !Array.isArray(report.unavailableMetrics)) {
    return null;
  }
  return report;
}

export function parseTheses(value: unknown): Thesis[] | null {
  const rows = list(value);
  if (!rows) return null;
  const out: Thesis[] = [];
  for (const row of rows) {
    const parsed = parseShape<Thesis>(row, ["id", "title", "body", "status", "conviction"]);
    if (!parsed) return null;
    out.push(parsed);
  }
  return out;
}

export function parseJournal(value: unknown): JournalEntry[] | null {
  const rows = list(value);
  if (!rows) return null;
  const out: JournalEntry[] = [];
  for (const row of rows) {
    const parsed = parseShape<JournalEntry>(row, ["id", "note", "createdAt"]);
    if (!parsed) return null;
    out.push(parsed);
  }
  return out;
}

// --- fetchers --------------------------------------------------------------

export function getHealth(): Promise<ApiResult<Health>> {
  return getJson("/health", parseHealth, 3_000);
}

export function getPositions(): Promise<ApiResult<Position[]>> {
  return getJson("/api/v1/t212/positions", parsePositions);
}

export function getPerformanceReport(): Promise<ApiResult<PerformanceReport>> {
  return getJson("/api/v1/performance/report", parsePerformanceReport, 15_000);
}

export function getQualityReport(): Promise<ApiResult<QualityReport>> {
  return getJson("/api/v1/portfolio/data-quality", parseQualityReport);
}

export function getLatestAiAnalysis(): Promise<ApiResult<AiAnalysis>> {
  return getJson("/api/v1/ai/latest", parseAiAnalysis);
}

export function getTheses(): Promise<ApiResult<Thesis[]>> {
  return getJson("/api/v1/theses", parseTheses);
}

export function getJournal(limit = 50): Promise<ApiResult<JournalEntry[]>> {
  return getJson(`/api/v1/journal?limit=${limit}`, parseJournal);
}

export function getNews(
  options: { ticker?: string; isin?: string; limit?: number } = {},
): Promise<ApiResult<NewsItem[]>> {
  const query = new URLSearchParams();
  if (options.ticker) query.set("ticker", options.ticker);
  if (options.isin) query.set("isin", options.isin);
  query.set("limit", String(options.limit ?? 50));
  return getJson(`/api/v1/news?${query.toString()}`, parseNewsItems);
}

export { num as parseNumber, decimal as parseDecimal };
