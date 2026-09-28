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

const health = {
  status: "ok",
  trading212Configured: true,
  databaseReady: true,
  trading212Environment: "demo",
};

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
  let netDeposits = 0;
  for (let index = 0; index < 120; index += 1) {
    const day = new Date(Date.UTC(2024, 0, 1 + index)).toISOString().slice(0, 10);
    nav = nav * (1 + Math.sin(index / 7) / 100);
    // A mid-series top-up: money moved, which must never read as investment gain.
    const flow = index === 0 ? 1000 : index === 60 ? 500 : 0;
    if (index === 60) nav += 500;
    // A small dividend, so the investment result has more than one part.
    const dividend = index === 90 ? 4.2 : 0;
    nav += dividend;
    netDeposits += flow;
    // One deliberate gap, so the "refuses to value" path is visible on screen.
    const valued = index !== 40;
    navSeries.push({
      asOfDate: day,
      navEur: valued ? nav.toFixed(2) : null,
      cashBalanceEur: "100.00",
      securitiesValueEur: valued ? (nav - 100).toFixed(2) : null,
      externalFlowEur: flow.toFixed(2),
      valuationStatus: valued ? "VALUED" : "PARTIAL",
      dividendEur: dividend.toFixed(2),
      interestEur: "0",
      feeEur: "0",
      netDepositsToDateEur: netDeposits.toFixed(2),
    });
    passive.push({ asOfDate: day, value: (1000 * (1 + index / 400)).toFixed(2) });
  }
  return { navSeries, passive };
}

const { navSeries, passive } = buildSeries();

/** Mirrors src/helios/periods.py: value change = net deposits + investment result. */
function summarise(key, label, startIndex, endIndex) {
  const window = navSeries.slice(startIndex === null ? 0 : startIndex + 1, endIndex + 1);
  const sum = (field, predicate = () => true) =>
    window.reduce((total, row) => (predicate(row) ? total + Number(row[field]) : total), 0);
  const deposits = sum("externalFlowEur", (row) => Number(row.externalFlowEur) > 0);
  const withdrawals = sum("externalFlowEur", (row) => Number(row.externalFlowEur) < 0);
  const dividends = sum("dividendEur");
  const startValue = startIndex === null ? 0 : Number(navSeries[startIndex].navEur);
  const endValue = Number(navSeries[endIndex].navEur);
  const change = endValue - startValue;
  const investment = change - (deposits + withdrawals);
  // Split the result by holding so the parts add up: AAPL rises, SHEL falls, and SHEL was
  // bought during any period that starts at inception.
  const aaplResult = investment * 0.8 + 30;
  const shelResult = investment - aaplResult;
  const holding = (ticker, name, result, bought, start, end) => ({
    ticker,
    name,
    status: "ok",
    startValueEur: start.toFixed(2),
    endValueEur: end.toFixed(2),
    startQuantity: start > 0 ? "5" : "0",
    endQuantity: "5",
    boughtEur: bought.toFixed(2),
    soldEur: "0.00",
    dividendsEur: ticker === "SHEL_EQ" ? dividends.toFixed(2) : "0.00",
    resultEur: result.toFixed(2),
    returnPct: result / Math.max(start + bought, 1),
    priceChangePct: start > 0 ? result / start : null,
    detail: null,
  });
  const inception = startIndex === null;
  return {
    key,
    label,
    status: "ok",
    startDate: startIndex === null ? null : navSeries[startIndex].asOfDate,
    endDate: navSeries[endIndex].asOfDate,
    startValueEur: startValue.toFixed(2),
    endValueEur: endValue.toFixed(2),
    valueChangeEur: change.toFixed(2),
    depositsEur: deposits.toFixed(2),
    withdrawalsEur: withdrawals.toFixed(2),
    netDepositsEur: (deposits + withdrawals).toFixed(2),
    investmentResultEur: investment.toFixed(2),
    marketEur: (investment - dividends).toFixed(2),
    dividendsEur: dividends.toFixed(2),
    interestEur: "0.00",
    feesEur: "0.00",
    // Every withdrawal in the fixture is a card payment; no cashback in the NAV fixture.
    cardSpendingEur: withdrawals.toFixed(2),
    cashbackEur: "0.00",
    twr: startValue > 0 ? investment / startValue : investment / 1000,
    unvaluedDays: window.filter((row) => row.navEur === null).length,
    detail: window.some((row) => row.navEur === null)
      ? "1 day(s) in this period could not be valued (missing prices)."
      : null,
    holdings: [
      holding("AAPL_US_EQ", "Apple Inc.", aaplResult, inception ? 1800 : 0, inception ? 0 : 1900, 2065.3),
      holding("SHEL_EQ", "Shell plc", shelResult, inception ? 1200 : 0, inception ? 0 : 1250, 1240.1),
    ].sort((left, right) => Math.abs(Number(right.resultEur)) - Math.abs(Number(left.resultEur))),
    unattributedEur: "0.00",
  };
}

const lastIndex = navSeries.length - 1; // 2024-04-29, a Monday
const periodSummaries = [
  summarise("1D", "Last trading day", lastIndex - 3, lastIndex), // Friday -> Monday
  summarise("1W", "1 week", lastIndex - 7, lastIndex),
  summarise("1M", "1 month", lastIndex - 29, lastIndex),
  summarise("3M", "3 months", lastIndex - 89, lastIndex),
  summarise("YTD", "Year to date", null, lastIndex),
  summarise("1Y", "1 year", null, lastIndex),
  summarise("ALL", "Since you started", null, lastIndex),
];
const monthlySummaries = [
  summarise("2024-01", "Jan 2024", null, 30),
  summarise("2024-02", "Feb 2024", 30, 59),
  summarise("2024-03", "Mar 2024", 59, 90),
  summarise("2024-04", "Apr 2024", 90, lastIndex),
];

const rolling = (offset, length, base) =>
  Array.from({ length }, (_, index) => ({
    asOfDate: navSeries[index + offset].asOfDate,
    value: base + Math.sin(index / 9) / 50,
  }));

const performanceReport = {
  periodSummaries,
  monthlySummaries,
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
    relevance: "headline",
    matchedTerm: "Apple",
    held: true,
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
    relevance: "market",
    matchedTerm: null,
    held: false,
    summary: null,
    url: "https://example.com/market-note",
    publishedAt: null,
    fetchedAt: "2024-04-29T12:00:00Z",
  },
  // The rest exist only to give the news list more than one page (page size 5, see
  // web/app/news/page.tsx) so the pagination E2E has something to click through.
  {
    dedupeKey: "c3",
    feedKey: "example-markets",
    sourceLabel: "Example Markets",
    t212Ticker: "SHEL_EQ",
    isin: "GB00BP6MXD84",
    headline: "Shell announces buyback update",
    relevance: "headline",
    matchedTerm: "Shell",
    held: true,
    summary: "Programme pace confirmed for the quarter.",
    url: "https://example.com/shell-buyback",
    publishedAt: "2024-04-28T09:00:00Z",
    fetchedAt: "2024-04-28T12:00:00Z",
  },
  {
    dedupeKey: "d4",
    feedKey: "example-markets",
    sourceLabel: "Example Markets",
    t212Ticker: "AAPL_US_EQ",
    isin: "US0378331005",
    headline: "Apple supply chain note",
    relevance: "headline",
    matchedTerm: "Apple",
    held: true,
    summary: "Suppliers report steady order volumes.",
    url: "https://example.com/apple-supply-chain",
    publishedAt: "2024-04-27T09:00:00Z",
    fetchedAt: "2024-04-27T12:00:00Z",
  },
  {
    dedupeKey: "e5",
    feedKey: "example-markets",
    sourceLabel: "Example Markets",
    // Not a held ticker, so it never matches a filter tab and stays under "All" — deliberately
    // distinct from "b2" so page 1 has exactly one "Market-wide" (unattributed) item, matching
    // what the pre-existing "labels undated and unattributed items honestly" test expects.
    t212Ticker: "MSFT_US_EQ",
    isin: "US5949181045",
    headline: "Broad market weekly recap",
    relevance: "unconfirmed",
    matchedTerm: null,
    held: false,
    summary: "Indices closed mixed for the week.",
    url: "https://example.com/weekly-recap",
    publishedAt: "2024-04-26T09:00:00Z",
    fetchedAt: "2024-04-26T12:00:00Z",
  },
  {
    dedupeKey: "f6",
    feedKey: "example-markets",
    sourceLabel: "Example Markets",
    t212Ticker: "SHEL_EQ",
    isin: "GB00BP6MXD84",
    headline: "Oil prices dip on demand concerns",
    relevance: "unconfirmed",
    matchedTerm: null,
    held: true,
    summary: "Crude fell on softer demand forecasts.",
    url: "https://example.com/oil-prices-dip",
    publishedAt: "2024-04-25T09:00:00Z",
    fetchedAt: "2024-04-25T12:00:00Z",
  },
  {
    dedupeKey: "g7",
    feedKey: "example-markets",
    sourceLabel: "Example Markets",
    t212Ticker: "AAPL_US_EQ",
    isin: "US0378331005",
    headline: "iPhone shipment estimates raised",
    relevance: "unconfirmed",
    matchedTerm: null,
    held: true,
    summary: "Analysts lifted unit estimates for the quarter.",
    url: "https://example.com/iphone-estimates",
    publishedAt: "2024-04-24T09:00:00Z",
    fetchedAt: "2024-04-24T12:00:00Z",
  },
];

// Nine more stories that name Apple, so the default ("about my holdings") news view spans two
// pages of ten. Oldest last: "Apple analyst note 9" is the one item on page 2 besides note 8.
for (let n = 1; n <= 9; n += 1) {
  newsItems.push({
    dedupeKey: `apple-note-${n}`,
    feedKey: "example-markets",
    sourceLabel: "Example Markets",
    t212Ticker: "AAPL_US_EQ",
    isin: "US0378331005",
    headline: `Apple analyst note ${n}`,
    relevance: "headline",
    matchedTerm: "Apple",
    held: true,
    summary: "An analyst revisited the thesis.",
    url: `https://example.com/apple-note-${n}`,
    publishedAt: `2024-04-0${10 - n}T09:00:00Z`,
    fetchedAt: "2024-04-10T12:00:00Z",
  });
}

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
  // ids 12-24 exist only to give the journal entries list more than one page (page size 10, see
  // web/app/journal/page.tsx) so the pagination E2E has a second page to click into.
  { id: 12, thesisId: 1, createdAt: "2024-04-17T08:00:00Z", note: "Trimmed a bit into strength.", tags: "position" },
  { id: 13, thesisId: 1, createdAt: "2024-04-16T08:00:00Z", note: "Re-read the original thesis; still holds.", tags: null },
  // ids 14 and 18 stay attributed (not `null`) so page 1 has exactly one "general"
  // (unattributed) entry — id 11 — matching what the pre-existing "lists journal entries with
  // their scope" test expects; ids 20-21 below are the unattributed ones that live on page 2.
  { id: 14, thesisId: 2, createdAt: "2024-04-15T08:00:00Z", note: "New product cycle chatter picking up.", tags: "watch" },
  { id: 15, thesisId: 1, createdAt: "2024-04-14T08:00:00Z", note: "Watching margins next quarter.", tags: null },
  { id: 16, thesisId: 2, createdAt: "2024-04-13T08:00:00Z", note: "No change after earnings.", tags: null },
  { id: 17, thesisId: 1, createdAt: "2024-04-12T08:00:00Z", note: "Position sized per plan.", tags: "position" },
  { id: 18, thesisId: 1, createdAt: "2024-04-11T08:00:00Z", note: "Macro backdrop stable this week.", tags: "macro" },
  { id: 19, thesisId: 1, createdAt: "2024-04-10T08:00:00Z", note: "Reviewed the correlation cluster note.", tags: null },
  { id: 20, thesisId: null, createdAt: "2024-04-09T08:00:00Z", note: "Nothing new to add this week.", tags: null },
  { id: 21, thesisId: null, createdAt: "2024-04-08T08:00:00Z", note: "Checked data quality report — one mismatch outstanding.", tags: "ops" },
  { id: 22, thesisId: 1, createdAt: "2024-04-07T08:00:00Z", note: "Added a tag for tax-loss tracking.", tags: "tax" },
  { id: 23, thesisId: 2, createdAt: "2024-04-06T08:00:00Z", note: "Re-confirmed conviction after the call.", tags: null },
  { id: 24, thesisId: 2, createdAt: "2024-04-05T08:00:00Z", note: "Closed the loop on the buyback question.", tags: null },
];

/**
 * Allowed moves per status, mirroring `helios.thesis.TRANSITIONS` (see src/helios/thesis.py)
 * closely enough for the E2E: it only needs to prove the dashboard renders and offers what the
 * detail payload says, not re-derive the whole state machine.
 */
const THESIS_TRANSITIONS = {
  draft: ["active", "closed"],
  active: ["validated", "invalidated", "closed"],
  validated: ["closed"],
  invalidated: ["closed"],
  closed: [],
};

/**
 * A draft thesis that exists only for `GET /api/v1/theses/{id}`, not for the list endpoint —
 * adding it to `theses` above would change "Open theses (1)" counts that other E2E specs already
 * assert on. It exercises the two states the plain list fixtures do not: a still-editable draft,
 * and a thesis with no linked enrichment ("none yet") and no journal entries of its own.
 */
const draftThesisOnly = {
  id: 3,
  t212Ticker: null,
  isin: null,
  title: "Diversify into short-duration fixed income",
  body: "Rates look attractive relative to the duration risk in the equity book right now.",
  conviction: "low",
  status: "draft",
  openedOn: "2024-04-25",
  outcomeNote: null,
  closedAt: null,
  createdAt: "2024-04-25T08:00:00Z",
  updatedAt: "2024-04-25T08:00:00Z",
};

/**
 * Enrichment for the two theses that already have live positions, matching the weight and
 * contribution the performance-report fixture reports for the same tickers above, and the
 * headline that leads their news fixtures — so a reader who checks both pages sees one story,
 * not two disconnected numbers.
 */
const THESIS_ENRICHMENT = {
  1: { weight: 0.62, contribution: 0.0256, newsCount: 2, latestNewsHeadline: "Apple beats expectations" },
  2: { weight: 0.23, contribution: -0.0042, newsCount: 2, latestNewsHeadline: "Shell announces buyback update" },
};

/** Builds a `ThesisDetailModel`-shaped payload for `GET /api/v1/theses/{id}`, or `null` for 404. */
function thesisDetail(id) {
  const thesis = theses.find((row) => row.id === id) ?? (id === draftThesisOnly.id ? draftThesisOnly : null);
  if (!thesis) return null;
  const enrichment = THESIS_ENRICHMENT[id] ?? {
    weight: null,
    contribution: null,
    newsCount: 0,
    latestNewsHeadline: null,
  };
  return {
    thesis,
    context: { t212Ticker: thesis.t212Ticker, ...enrichment },
    allowedTransitions: THESIS_TRANSITIONS[thesis.status] ?? [],
    editable: thesis.status === "draft",
    journal: journal.filter((entry) => entry.thesisId === id),
  };
}

/**
 * The settings snapshot as the real API builds it: presence and a four-character tail, never a
 * value. The E2E asserts the page never renders anything longer, so the shape here matters —
 * a stub that leaked a full key would make that assertion vacuous.
 */
const settingsSnapshot = {
  credentials: [
    {
      field: "t212_api_key",
      label: "Trading 212 API key",
      requirement: "required",
      present: true,
      hint: "…7f2a",
      source: "keyring",
      unlocks: "Positions, order history, dividends, the entire ledger",
      without: "Nothing works — Helios has no data source without this",
      signup: "https://www.trading212.com",
    },
    {
      field: "t212_api_secret",
      label: "Trading 212 API secret",
      requirement: "required",
      present: true,
      hint: "…9c14",
      source: "keyring",
      unlocks: "Paired with the key to authenticate every request",
      without: "Nothing works — the API rejects a key without its secret",
      signup: "https://www.trading212.com",
    },
    {
      field: "market_data_api_key",
      label: "Twelve Data API key",
      requirement: "recommended",
      present: false,
      hint: null,
      source: "unset",
      unlocks: "Daily prices, and through them TWR, Sharpe, VaR, drawdown, beta, NAV chart",
      without: "Every price-dependent metric reports unavailable",
      signup: "https://twelvedata.com/pricing",
    },
    {
      field: "anthropic_api_key",
      label: "Anthropic API key",
      requirement: "optional",
      present: false,
      hint: null,
      source: "unset",
      unlocks: "The Insights page",
      without: "Insights reports unavailable; nothing else changes",
      signup: "https://console.anthropic.com",
    },
  ],
  editable: {
    t212_base_url: "https://demo.trading212.com/api/v0",
    news_sec_user_agent: "",
    market_data_provider: "twelvedata",
    anthropic_model: "claude-opus-5",
    analytics_passive_benchmark_key: "vwrp",
  },
  keyringBackend: "WinVaultKeyring",
  keyringAvailable: true,
  keyringDetail: "verified by round-trip",
  envPath: "/app/.env",
  allowedT212BaseUrls: [
    "https://demo.trading212.com/api/v0",
    "https://live.trading212.com/api/v0",
  ],
  activeDatabase: "data/helios.sqlite3",
  restartRequired: false,
  writable: true,
  readOnlyReason: null,
  t212Environment: "demo",
  t212PendingEnvironment: "demo",
};

/** What the real API says when Compose runs it with HELIOS_SETTINGS_WRITABLE=false. */
const READ_ONLY_REASON =
  "Settings are read-only in this deployment. Helios is running with configuration injected " +
  "by its supervisor (under Docker Compose, the .env file next to compose.yaml on the host), " +
  "and a change saved from here would never be read. Edit that file, then recreate the " +
  "containers with `docker compose up -d`.";

const databaseList = {
  dataDir: "data",
  active: "helios.sqlite3",
  databases: [
    {
      filename: "helios.sqlite3",
      sizeBytes: 42_332_160,
      modifiedAt: "2024-04-29T10:05:00Z",
      active: true,
      schemaVersion: "0009_index_instrument_mapping_status",
    },
    {
      filename: "helios-live.sqlite3",
      sizeBytes: 290_816,
      modifiedAt: "2024-04-29T09:00:00Z",
      active: false,
      schemaVersion: "0009_index_instrument_mapping_status",
    },
  ],
};

// Card history as the API reports it after one export: three payments dated relative to today
// (yesterday, nine days ago, forty days ago) so the day, week and month views all have data.
// Noon UTC keeps each on the same calendar day in any time zone the test machine uses.
function daysAgo(days) {
  const date = new Date();
  date.setUTCDate(date.getUTCDate() - days);
  return `${date.toISOString().slice(0, 10)}T12:00:00Z`;
}

const cardHistory = {
  status: {
    enabled: true,
    lastRequestedAt: daysAgo(0),
    lastDownloadedAt: daysAgo(0),
    pending: false,
    lastStatus: "Downloaded",
    cardRows: 3,
    cashRows: 5,
  },
  summary: {
    currency: "EUR",
    firstDate: daysAgo(40).slice(0, 10),
    lastDate: daysAgo(1).slice(0, 10),
    spent: "150.00",
    refunded: "0",
    cashback: "1.20",
    cashbackRate: 0.008,
    months: [],
    categories: [
      { key: "MISCELLANEOUS", spent: "90.00", count: 2 },
      { key: "MEMBERSHIPS", spent: "60.00", count: 1 },
    ],
    merchants: [
      { key: "Grocer", spent: "90.00", count: 2 },
      { key: "Gym Club", spent: "60.00", count: 1 },
    ],
    transactions: [
      {
        rowId: "c3",
        ts: daysAgo(1),
        action: "Card debit",
        amount: "-60.00",
        currency: "EUR",
        merchantName: "Gym Club",
        merchantCategory: "MEMBERSHIPS",
      },
      {
        rowId: "c2",
        ts: daysAgo(9),
        action: "Card debit",
        amount: "-50.00",
        currency: "EUR",
        merchantName: "Grocer",
        merchantCategory: "MISCELLANEOUS",
      },
      {
        rowId: "c1",
        ts: daysAgo(40),
        action: "Card debit",
        amount: "-40.00",
        currency: "EUR",
        merchantName: "Grocer",
        merchantCategory: "MISCELLANEOUS",
      },
    ],
    cashbackEntries: [
      { ts: daysAgo(0), amount: "0.90" },
      { ts: daysAgo(30), amount: "0.30" },
    ],
  },
  budgets: [{ category: "MEMBERSHIPS", monthlyLimit: "50.00" }],
  // A withdrawal after the export: shown as card spending, "not labelled yet".
  unlabelled: [{ reference: "w-new", ts: daysAgo(0), amount: "-4.20", currency: "EUR" }],
};

// A monthly subscription, 30 days apart, so the recurring-payments view has one to find.
for (const [index, days] of [3, 33, 63].entries()) {
  cardHistory.summary.transactions.push({
    rowId: `s${index}`,
    ts: daysAgo(days),
    action: "Card debit",
    amount: "-9.99",
    currency: "EUR",
    merchantName: "Streamflix",
    merchantCategory: "UTILITIES",
  });
}

// One instrument's detail page: a price path over the NAV fixture's dates, one buy on day one,
// and the AAPL line of every period's stock-by-stock split.
const appleDetail = {
  ticker: "AAPL_US_EQ",
  name: "Apple Inc.",
  isin: "US0378331005",
  currency: "USD",
  instrumentType: "STOCK",
  exchange: "NASDAQ",
  marketSymbol: "AAPL",
  sector: "Technology",
  quantity: "10",
  firstBought: "2024-01-01T14:30:00Z",
  boughtEur: "1800.00",
  soldEur: "0",
  dividendsEur: "0",
  realisedEur: "0",
  valueEur: "2065.30",
  resultEur: "265.30",
  high: { asOfDate: "2024-04-26", close: "196.40", currency: "USD", closeEur: "183.10" },
  low: { asOfDate: "2024-01-01", close: "150.00", currency: "USD", closeEur: "138.00" },
  prices: navSeries.map((point, index) => ({
    asOfDate: point.asOfDate,
    close: (150 + index * 0.35 + Math.sin(index / 6) * 3).toFixed(2),
    currency: "USD",
    closeEur: ((150 + index * 0.35) * 0.92).toFixed(2),
  })),
  positions: navSeries.map((point, index) => ({
    asOfDate: point.asOfDate,
    quantity: "10",
    valueEur: ((150 + index * 0.35) * 9.2 + 400).toFixed(2),
    investedEur: "1800.00",
  })),
  trades: [
    {
      ts: "2024-01-01T14:30:00Z",
      side: "BUY",
      quantity: "10",
      price: "150.00",
      valueEur: "1800.00",
      realisedEur: null,
    },
  ],
  dividends: [],
  priceReturns: [
    { key: "1W", label: "1 week", startDate: "2024-04-22", changePct: 0.012 },
    { key: "1M", label: "1 month", startDate: "2024-03-29", changePct: 0.051 },
    { key: "3M", label: "3 months", startDate: "2024-01-29", changePct: 0.18 },
    { key: "6M", label: "6 months", startDate: null, changePct: null },
    { key: "YTD", label: "Year to date", startDate: "2024-01-01", changePct: 0.276 },
    { key: "1Y", label: "1 year", startDate: null, changePct: null },
    { key: "ALL", label: "All history", startDate: "2024-01-01", changePct: 0.276 },
  ],
  periods: periodSummaries.map((period) => {
    const apple = period.holdings.find((item) => item.ticker === "AAPL_US_EQ");
    return {
      key: period.key,
      label: period.label,
      resultEur: apple.resultEur,
      returnPct: apple.returnPct,
      priceChangePct: apple.priceChangePct,
    };
  }),
};

const ROUTES = {
  "/health": health,
  "/api/v1/card": cardHistory,
  "/api/v1/instruments/AAPL_US_EQ": appleDetail,
  "/api/v1/t212/positions": positions,
  "/api/v1/t212/account": {
    id: 1,
    currency: "EUR",
    totalValue: 3405.4,
    cash: { availableToTrade: 100.0, reservedForOrders: 0, inPies: 0 },
    investments: {
      currentValue: 3305.4,
      totalCost: 3020.55,
      realizedProfitLoss: 0,
      unrealizedProfitLoss: 284.85,
    },
  },
  "/api/v1/performance/report": performanceReport,
  "/api/v1/portfolio/data-quality": qualityReport,
  "/api/v1/news": newsItems,
  "/api/v1/ai/latest": aiAnalysis,
  "/api/v1/theses": theses,
  "/api/v1/journal": journal,
  "/api/v1/settings": settingsSnapshot,
  "/api/v1/settings/databases": databaseList,
};

const portfolioSyncSummary = {
  asOf: "2024-04-29T10:05:00Z",
  metadataFetched: true,
  endpoints: [{ endpoint: "/equity/positions", fetched: true, itemCount: 2 }],
};

const replaySummary = {
  asOf: "2024-04-29",
  startDate: "2024-01-01",
  endDate: "2024-04-29",
  flowTiming: "flow_at_close",
  holdingsWritten: 240,
  navWritten: 120,
  unsupportedQuantityEvents: [],
  missingPriceSymbols: [],
  stalePriceSymbols: [],
  missingFxCurrencies: [],
  staleFxCurrencies: [],
  excludedFlowCurrencies: [],
  notes: [],
};

/**
 * Mutating routes and the action each requires, mirroring the FastAPI guard.
 *
 * The stub enforces the header rather than accepting any POST: the point of the E2E is that a
 * button in the browser reaches the API as Helios' own server-side caller, and a stub that
 * answered 200 to a header-less POST would pass whether or not that were true.
 */
const MUTATIONS = {
  "POST /api/v1/portfolio/sync": { action: "sync", body: portfolioSyncSummary },
  "POST /api/v1/performance/replay": { action: "replay", body: replaySummary },
  "POST /api/v1/news/sync": { action: "news-sync", body: newsSyncSummary },
  "PUT /api/v1/card/budgets": {
    action: "card-budget",
    body: [{ category: "MEMBERSHIPS", monthlyLimit: "80.00" }],
  },
  "POST /api/v1/card/refresh": {
    action: "card-refresh",
    body: { action: "downloaded", detail: "Stored 5 cash rows from the export.", rowsStored: 5 },
  },
  "POST /api/v1/ai/analyse": { action: "ai-analyse", body: aiAnalysis },
  "POST /api/v1/theses": { action: "thesis-write", body: theses[0] },
  "POST /api/v1/journal": { action: "journal-write", body: journal[0] },
  // The settings writes. Keyed by method as well as path, so `GET /api/v1/settings/databases`
  // still falls through to the read table below while the POST is treated as a mutation.
  "PUT /api/v1/settings/credential": {
    action: "settings-write",
    body: {
      field: "market_data_api_key",
      storedIn: "keyring",
      verified: null,
      restartRequired: true,
      detail: "Stored in the OS keyring. Restart to apply.",
    },
  },
  "PUT /api/v1/settings/trading212": {
    action: "settings-write",
    body: {
      environment: "live",
      verified: "live account 4242 (EUR)",
      restartRequired: true,
      detail:
        "Verified against your Live account (live account 4242 (EUR)) and saved. Restart Helios to start syncing it.",
    },
  },
  "PUT /api/v1/settings/value": {
    action: "settings-write",
    body: {
      field: "news_sec_user_agent",
      envName: "HELIOS_NEWS_SEC_USER_AGENT",
      value: "Test Person test@example.com",
      restartRequired: true,
      detail: "HELIOS_NEWS_SEC_USER_AGENT set. Restart to apply.",
    },
  },
  "POST /api/v1/settings/databases": {
    action: "settings-write",
    body: {
      filename: "helios-2026.sqlite3",
      created: true,
      active: false,
      restartRequired: false,
      detail: "helios-2026.sqlite3 created at the current schema.",
    },
  },
  "PUT /api/v1/settings/databases/active": {
    action: "settings-write",
    body: {
      filename: "helios-live.sqlite3",
      created: false,
      active: true,
      restartRequired: true,
      detail: "Helios will use helios-live.sqlite3 after a restart. The previous database is kept.",
    },
  },
  "POST /api/v1/settings/restart": {
    action: "restart",
    body: { scheduled: true, detail: "Restarting now." },
  },
};

/** Records what the dashboard actually sent, so a test can assert on it via /__mutations. */
const received = [];

function mutationKey(method, path) {
  // Thesis edit/transition carry an id in the path, so match them by shape.
  if (/^\/api\/v1\/theses\/[^/]+$/.test(path) && method === "PATCH") {
    return "PATCH /api/v1/theses/{id}";
  }
  if (/^\/api\/v1\/theses\/[^/]+\/transition$/.test(path) && method === "POST") {
    return "POST /api/v1/theses/{id}/transition";
  }
  return `${method} ${path}`;
}

MUTATIONS["PATCH /api/v1/theses/{id}"] = { action: "thesis-write", body: theses[0] };
MUTATIONS["POST /api/v1/theses/{id}/transition"] = { action: "thesis-write", body: theses[0] };

createServer((request, response) => {
  const [path, query = ""] = (request.url || "").split("?");
  const method = request.method || "GET";

  if (path === "/__mutations") {
    response.writeHead(200, { "content-type": "application/json" });
    response.end(JSON.stringify(received));
    return;
  }

  // Flips the settings snapshot between a bare-metal install and a Compose deployment, so the
  // read-only rendering can be exercised without a second stub process.
  if (path === "/__settings-mode") {
    const writable = new URLSearchParams(query).get("writable") !== "false";
    settingsSnapshot.writable = writable;
    settingsSnapshot.readOnlyReason = writable ? null : READ_ONLY_REASON;
    response.writeHead(200, { "content-type": "application/json" });
    response.end(JSON.stringify({ writable }));
    return;
  }

  const mutation = MUTATIONS[mutationKey(method, path)];
  if (mutation) {
    const action = request.headers["x-helios-local-action"];
    let raw = "";
    request.on("data", (chunk) => {
      raw += chunk;
    });
    request.on("end", () => {
      received.push({ method, path, action: action ?? null, body: raw || null });
      if (action !== mutation.action) {
        response.writeHead(403, { "content-type": "application/json" });
        response.end(JSON.stringify({ detail: "Missing required local action confirmation" }));
        return;
      }
      response.writeHead(200, { "content-type": "application/json" });
      response.end(JSON.stringify(mutation.body));
    });
    return;
  }

  let body = ROUTES[path];
  if (path === "/api/v1/news") {
    const params = new URLSearchParams(query);
    const ticker = params.get("ticker");
    // Same contract as the real endpoint: newest first, undated last, then the two filters.
    body = [...newsItems]
      .sort((left, right) =>
        left.publishedAt === right.publishedAt
          ? 0
          : left.publishedAt === null
            ? 1
            : right.publishedAt === null
              ? -1
              : right.publishedAt.localeCompare(left.publishedAt),
      )
      .filter((item) => !ticker || item.t212Ticker === ticker)
      .filter((item) => params.get("heldOnly") !== "true" || item.t212Ticker === null || item.held)
      .filter(
        (item) =>
          params.get("mentionsOnly") !== "true" ||
          item.relevance === "headline" ||
          item.relevance === "summary",
      );
  }
  // A thesis id carries no fixed path in ROUTES, so match it by shape like the mutations above.
  const detailMatch = method === "GET" && /^\/api\/v1\/theses\/([^/]+)$/.exec(path);
  if (detailMatch) {
    const rawId = detailMatch[1];
    const id = Number(rawId);
    body = Number.isInteger(id) ? thesisDetail(id) ?? undefined : undefined;
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
