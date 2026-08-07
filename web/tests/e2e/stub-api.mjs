/**
 * A stand-in Helios API for end-to-end runs.
 *
 * The E2E suite is about the dashboard, not the backend: it serves the same DTOs the FastAPI app
 * serves (decimal strings, camelCase aliases, explicit `unavailable` statuses) from fixed
 * fixtures, so a run is deterministic and needs no Trading 212 credentials, no database, and no
 * network. Backend behaviour has its own pytest suite.
 */
import { createServer } from "node:http";

const port = Number(process.env.STUB_API_PORT || 8099);

const health = { status: "ok", trading212Configured: true, databaseReady: true };

const positions = [
  {
    instrument: {
      ticker: "AAPL_US_EQ",
      isin: "US0378331005",
      name: "Apple Inc.",
      currency: "USD",
    },
    quantity: "12.3456789",
    averagePricePaid: "150.25",
    currentPrice: "182.40",
    walletImpact: {
      currency: "EUR",
      currentValue: "2065.30",
      fxImpact: "-12.40",
      totalCost: "1710.55",
      unrealizedProfitLoss: "354.75",
    },
  },
  {
    instrument: { ticker: "SHEL_EQ", isin: "GB00BP6MXD84", name: "Shell plc", currency: "GBX" },
    quantity: "40",
    averagePricePaid: "2800",
    currentPrice: "2650",
    walletImpact: {
      currency: "EUR",
      currentValue: "1240.10",
      fxImpact: "3.10",
      totalCost: "1310.00",
      unrealizedProfitLoss: "-69.90",
    },
  },
];

function metric(value, status = value === null ? "insufficient_data" : "ok", detail = null) {
  return { status, value, observations: value === null ? 0 : 120, detail };
}

function buildSeries() {
  const navSeries = [];
  const passive = [];
  let nav = 1000;
  for (let index = 0; index < 120; index += 1) {
    const day = new Date(Date.UTC(2024, 0, 1 + index)).toISOString().slice(0, 10);
    nav = nav * (1 + Math.sin(index / 7) / 100);
    // One deliberate gap, so the "refuses to value" path is visible on screen.
    const valued = index !== 40;
    navSeries.push({
      asOfDate: day,
      navEur: valued ? nav.toFixed(2) : null,
      cashBalanceEur: "100.00",
      securitiesValueEur: valued ? (nav - 100).toFixed(2) : null,
      externalFlowEur: index === 0 ? "1000.00" : "0",
      valuationStatus: valued ? "VALUED" : "PARTIAL",
    });
    passive.push({ asOfDate: day, value: (1000 * (1 + index / 400)).toFixed(2) });
  }
  return { navSeries, passive };
}

const { navSeries, passive } = buildSeries();

const rolling = (offset, length, base) =>
  Array.from({ length }, (_, index) => ({
    asOfDate: navSeries[index + offset].asOfDate,
    value: base + Math.sin(index / 9) / 50,
  }));

const performanceReport = {
  asOf: "2024-04-29",
  startDate: "2024-01-01",
  endDate: "2024-04-29",
  flowTiming: "flow_at_close",
  annualizationDays: 365,
  cumulativeTwr: metric(0.0259),
  xirr: metric(0.0499),
  annualizedReturn: metric(0.0810),
  volatility: metric(0.2275),
  downsideVolatility: metric(0.3102),
  sharpe: metric(0.4595),
  sortino: metric(null),
  calmar: metric(1.2062),
  maxDrawdown: metric(-0.0672),
  timeUnderwaterDays: metric(30),
  recoveryDays: metric(22),
  betaVsBenchmarks: [
    {
      benchmark: {
        key: "vwrp",
        label: "FTSE All-World ETF proxy",
        providerSymbol: "VWRP.LON",
        currencyCode: "USD",
        description: "ETF proxy for FTSE All-World; not the licensed index level.",
      },
      beta: metric(0.2258),
      alpha: metric(0.0411),
      rSquared: metric(0.5102),
      correlation: metric(0.7143),
      trackingError: metric(0.1802),
      informationRatio: metric(0.2281),
    },
    {
      benchmark: {
        key: "cspx",
        label: "S&P 500 ETF proxy",
        providerSymbol: "CSPX.LON",
        currencyCode: null,
        description: "ETF proxy for the S&P 500; not the licensed index level.",
      },
      beta: metric(null),
      alpha: metric(null),
      rSquared: metric(null),
      correlation: metric(null),
      trackingError: metric(null),
      informationRatio: metric(null),
    },
  ],
  hhi: metric(0.52),
  effectiveNumberOfPositions: metric(1.92),
  top5Weight: metric(1),
  var95_1d: metric(
    0,
    "ok",
    "Historical simulation at 95% over 1 day(s); loss shown as a negative return; 'linear' quantile interpolation; 120 observations, 39 in the tail. 33% of observations are flat (non-trading calendar days carry the previous close forward), which dampens the tail.",
  ),
  cvar95_1d: metric(-0.007),
  var99_1d: metric(-0.0203),
  cvar99_1d: metric(-0.0288),
  var95_10d: metric(-0.0219),
  cvar95_10d: metric(-0.0304),
  var99_10d: metric(-0.0465),
  cvar99_10d: metric(-0.0502),
  ff5MomentumRegression: {
    status: "unavailable",
    observations: 0,
    rSquared: null,
    intercept: null,
    coefficients: { mkt_rf: null, smb: null, hml: null, rmw: null, cma: null, mom: null },
    detail:
      "No factor return data is configured (HELIOS_FACTOR_DATA_PROVIDER=disabled); Helios does not fabricate factor series.",
  },
  navSeries,
  dailyTwr: rolling(1, 119, 0.001),
  rollingVolatility30d: rolling(29, 91, 0.22),
  rollingVolatility90d: rolling(89, 31, 0.19),
  rollingBeta30d: rolling(29, 91, 0.45),
  rollingBeta90d: rolling(89, 31, 0.51),
  contributions: [
    {
      key: "AAPL_US_EQ",
      weight: 0.62,
      status: "ok",
      returnValue: 0.0413,
      contribution: 0.0256,
      detail: null,
    },
    {
      key: "SHEL_EQ",
      weight: 0.23,
      status: "ok",
      returnValue: -0.0182,
      contribution: -0.0042,
      detail: null,
    },
    {
      key: "NEW_EQ",
      weight: 0.15,
      status: "insufficient_data",
      returnValue: null,
      contribution: null,
      detail: "Need two consecutive valued observations for this holding.",
    },
  ],
  attribution: {
    status: "unavailable",
    activeReturn: null,
    items: [],
    detail:
      "Sector attribution requires benchmark constituent weights and sector returns. No licensed index-constituent source is configured, so Helios reports no numbers.",
  },
  correlationClusters: {
    status: "ok",
    observations: 118,
    distanceThreshold: 1,
    clusterCount: 2,
    assignments: [
      { key: "AAPL_US_EQ", cluster: 1, weight: 0.62 },
      { key: "SHEL_EQ", cluster: 2, weight: 0.23 },
    ],
    detail: null,
  },
  passiveCounterfactual: {
    status: "ok",
    benchmarkKey: "vwrp",
    benchmarkLabel: "FTSE All-World ETF proxy",
    investedEur: "1000.00",
    finalValueEur: "1297.50",
    actualNavEur: navSeries[navSeries.length - 1].navEur,
    differenceEur: "-210.30",
    series: passive,
    excludedFlowCount: 0,
    detail:
      "Counterfactual: external contributions invested in VWRP.LON (FTSE All-World ETF proxy) at each flow date.",
  },
  notes: [
    "Cash-flow timing convention: flow_at_close.",
    "Annualisation basis: 365 calendar days (the replay emits one NAV observation per calendar day).",
    "Benchmarks are configurable ETF proxies, not official S&P/MSCI/FTSE index levels.",
  ],
};

const qualityReport = {
  asOf: "2024-04-29T10:00:00Z",
  overallStatus: "degraded",
  endpointStatuses: [
    {
      endpoint: "/equity/positions",
      lastAttemptAt: "2024-04-29T10:00:00Z",
      lastSuccessAt: "2024-04-29T10:00:00Z",
      lastStatus: "success",
      itemCount: 2,
      lastError: null,
    },
    {
      endpoint: "/equity/history/orders",
      lastAttemptAt: "2024-04-29T10:00:00Z",
      lastSuccessAt: null,
      lastStatus: "failed",
      itemCount: null,
      lastError: "Trading212HTTPError",
    },
  ],
  metadataFreshness: {
    endpoint: "/equity/metadata/instruments",
    fresh: false,
    ttlHours: 24,
    checkedAt: "2024-04-29T10:00:00Z",
    lastSuccessAt: "2024-04-26T09:00:00Z",
  },
  unresolvedInstruments: [
    {
      t212Ticker: "XYZ_EQ",
      isin: "XX0000000001",
      yahooTicker: null,
      mappingStatus: "unresolved",
      mappingSource: null,
      mappedAt: null,
    },
  ],
  ambiguousInstruments: [],
  overrideRequiredInstruments: [],
  reconciliationMismatches: [
    {
      t212Ticker: "AAPL_US_EQ",
      ts: "2024-04-29T10:00:00Z",
      replayedQuantity: "12.3456789",
      liveQuantity: "12.0000000",
      differenceQuantity: "0.3456789",
      toleranceQuantity: "0.001",
      status: "mismatch",
    },
  ],
  unsupportedActions: [],
};

const newsItems = [
  {
    dedupeKey: "a1",
    feedKey: "example-markets",
    sourceLabel: "Example Markets",
    t212Ticker: "AAPL_US_EQ",
    isin: "US0378331005",
    headline: "Apple beats expectations",
    summary: "Quarterly results came in ahead of consensus.",
    url: "https://example.com/apple-results",
    publishedAt: "2024-04-29T09:30:00Z",
    fetchedAt: "2024-04-29T12:00:00Z",
  },
  {
    dedupeKey: "b2",
    feedKey: "example-markets",
    sourceLabel: "Example Markets",
    t212Ticker: null,
    isin: null,
    headline: "Undated market note",
    summary: null,
    url: "https://example.com/market-note",
    publishedAt: null,
    fetchedAt: "2024-04-29T12:00:00Z",
  },
];

const newsSyncSummary = {
  asOf: "2024-04-29T12:00:00Z",
  feedsConfigured: 1,
  feedsFetched: 1,
  rawStored: 1,
  itemsParsed: 2,
  itemsWritten: 2,
  duplicatesSkipped: 0,
  failures: [],
  notes: [],
};

const aiAnalysis = {
  status: "ok",
  asOf: "2024-04-29T12:30:00Z",
  model: "claude-opus-5",
  servedByModel: null,
  effort: "high",
  summary:
    "The portfolio is highly concentrated in a single holding and carries moderate volatility.",
  observations: [
    {
      rank: 1,
      category: "concentration",
      t212Ticker: "AAPL_US_EQ",
      headline: "One holding dominates the portfolio",
      detail: "The top-five weight accounts for the entire portfolio value.",
      evidence: "top5_weight = 1.0, hhi = 0.52",
      severity: "notable",
    },
    {
      rank: 2,
      category: "risk",
      t212Ticker: null,
      headline: "Annualised volatility is 22.75%",
      detail: "Measured on the calendar-day NAV series with a 365-day basis.",
      evidence: "volatility = 0.2275 over 120 observations",
      severity: "info",
    },
  ],
  unavailableMetrics: ["sortino", "attribution", "ff5MomentumRegression"],
  inputTokens: 4210,
  outputTokens: 890,
  cacheReadTokens: 3200,
  disclosure:
    "Descriptive analysis of your own portfolio data, generated by an AI model. It is not investment advice, not a recommendation to buy or sell, and not a forecast. Every statement is derived from figures Helios reconstructed from your Trading 212 history.",
  detail: null,
};

const theses = [
  {
    id: 1,
    t212Ticker: "AAPL_US_EQ",
    isin: "US0378331005",
    title: "Services revenue compounds faster than hardware",
    body: "Recurring services margin should keep expanding.",
    conviction: "high",
    status: "active",
    openedOn: "2024-02-01",
    outcomeNote: null,
    closedAt: null,
    createdAt: "2024-02-01T09:00:00Z",
    updatedAt: "2024-02-01T09:00:00Z",
  },
  {
    id: 2,
    t212Ticker: "SHEL_EQ",
    isin: "GB00BP6MXD84",
    title: "Buyback pace is sustainable",
    body: "Free cash flow covers the announced programme.",
    conviction: "medium",
    status: "invalidated",
    openedOn: "2023-11-15",
    outcomeNote: "Programme slowed in Q1; the premise did not hold.",
    closedAt: "2024-04-02T10:00:00Z",
    createdAt: "2023-11-15T09:00:00Z",
    updatedAt: "2024-04-02T10:00:00Z",
  },
];

const journal = [
  {
    id: 10,
    thesisId: 1,
    createdAt: "2024-04-20T08:00:00Z",
    note: "Added on the pullback; thesis unchanged.",
    tags: "position",
  },
  {
    id: 11,
    thesisId: null,
    createdAt: "2024-04-18T08:00:00Z",
    note: "Rates backdrop feels like the dominant driver this quarter.",
    tags: "macro",
  },
];

const ROUTES = {
  "/health": health,
  "/api/v1/t212/positions": positions,
  "/api/v1/performance/report": performanceReport,
  "/api/v1/portfolio/data-quality": qualityReport,
  "/api/v1/news": newsItems,
  "/api/v1/news/sync": newsSyncSummary,
  "/api/v1/ai/latest": aiAnalysis,
  "/api/v1/theses": theses,
  "/api/v1/journal": journal,
};

createServer((request, response) => {
  const [path, query = ""] = (request.url || "").split("?");
  let body = ROUTES[path];
  if (path === "/api/v1/news") {
    const ticker = new URLSearchParams(query).get("ticker");
    body = ticker ? newsItems.filter((item) => item.t212Ticker === ticker) : newsItems;
  }
  if (body === undefined) {
    response.writeHead(404, { "content-type": "application/json" });
    response.end(JSON.stringify({ detail: "Not found" }));
    return;
  }
  response.writeHead(200, { "content-type": "application/json" });
  response.end(JSON.stringify(body));
}).listen(port, "127.0.0.1", () => {
  process.stdout.write(`stub-api listening on http://127.0.0.1:${port}\n`);
});
