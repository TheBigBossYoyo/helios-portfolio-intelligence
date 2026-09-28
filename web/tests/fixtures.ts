import type {
  JournalEntry,
  NewsItem,
  PerformanceReport,
  Position,
  QualityReport,
  ThesisDetail,
} from "@/lib/types";

/**
 * Deterministic fixtures shaped exactly like the backend's serialised DTOs, including the
 * decimal-as-string convention and the explicit `unavailable` / `insufficient_data` statuses
 * that M3 emits when it will not fabricate a number.
 */

export function metric(value: number | null, status = value === null ? "insufficient_data" : "ok") {
  return {
    status,
    value,
    observations: value === null ? 0 : 42,
    detail: value === null ? "Not enough history." : null,
  };
}

export function performanceReport(
  overrides: Partial<PerformanceReport> = {},
): PerformanceReport {
  return {
    asOf: "2024-05-01",
    startDate: "2024-01-01",
    endDate: "2024-05-01",
    flowTiming: "flow_at_close",
    annualizationDays: 365,
    cumulativeTwr: metric(0.0259),
    xirr: metric(0.0499),
    annualizedReturn: metric(0.081),
    volatility: metric(0.2275),
    downsideVolatility: metric(0.31),
    sharpe: metric(0.4595),
    sortino: metric(0.33),
    calmar: metric(1.21),
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
        alpha: metric(0.04),
        rSquared: metric(0.51),
        correlation: metric(0.71),
        trackingError: metric(0.18),
        informationRatio: metric(0.22),
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
    hhi: metric(1),
    effectiveNumberOfPositions: metric(1),
    top5Weight: metric(1),
    var95_1d: metric(0),
    cvar95_1d: metric(-0.007),
    var99_1d: metric(-0.02),
    cvar99_1d: metric(-0.03),
    var95_10d: metric(-0.02),
    cvar95_10d: metric(-0.03),
    var99_10d: metric(-0.0465),
    cvar99_10d: metric(-0.05),
    ff5MomentumRegression: {
      status: "unavailable",
      observations: 0,
      rSquared: null,
      intercept: null,
      coefficients: { mkt_rf: null, smb: null, hml: null, rmw: null, cma: null, mom: null },
      detail:
        "No factor return data is configured (HELIOS_FACTOR_DATA_PROVIDER=disabled); Helios does not fabricate factor series.",
    },
    navSeries: [
      {
        asOfDate: "2024-01-01",
        navEur: "1000.00",
        cashBalanceEur: "1000.00",
        securitiesValueEur: "0",
        externalFlowEur: "1000.00",
        valuationStatus: "VALUED",
      },
      {
        asOfDate: "2024-01-02",
        navEur: "1100.00",
        cashBalanceEur: "100.00",
        securitiesValueEur: "1000.00",
        externalFlowEur: "0",
        valuationStatus: "VALUED",
      },
      {
        asOfDate: "2024-01-03",
        navEur: null,
        cashBalanceEur: "100.00",
        securitiesValueEur: null,
        externalFlowEur: "0",
        valuationStatus: "PARTIAL",
      },
      {
        asOfDate: "2024-01-04",
        navEur: "1050.00",
        cashBalanceEur: "100.00",
        securitiesValueEur: "950.00",
        externalFlowEur: "0",
        valuationStatus: "FORWARD_FILL",
      },
    ],
    dailyTwr: [{ asOfDate: "2024-01-02", value: 0.1 }],
    rollingVolatility30d: [{ asOfDate: "2024-02-01", value: 0.2 }],
    rollingVolatility90d: [{ asOfDate: "2024-04-01", value: 0.18 }],
    rollingBeta30d: [{ asOfDate: "2024-02-01", value: 0.4 }],
    rollingBeta90d: [],
    contributions: [
      { key: "AAPL_US_EQ", weight: 0.6, status: "ok", returnValue: 0.05, contribution: 0.03, detail: null },
      { key: "SHEL_EQ", weight: 0.25, status: "ok", returnValue: -0.04, contribution: -0.01, detail: null },
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
      observations: 25,
      distanceThreshold: 1,
      clusterCount: 2,
      assignments: [
        { key: "AAPL_US_EQ", cluster: 1, weight: 0.6 },
        { key: "SHEL_EQ", cluster: 2, weight: 0.25 },
      ],
      detail: null,
    },
    passiveCounterfactual: {
      status: "ok",
      benchmarkKey: "vwrp",
      benchmarkLabel: "FTSE All-World ETF proxy",
      investedEur: "1000.00",
      finalValueEur: "1020.00",
      actualNavEur: "1050.00",
      differenceEur: "30.00",
      series: [
        { asOfDate: "2024-01-01", value: "1000.00" },
        { asOfDate: "2024-01-02", value: "1010.00" },
        { asOfDate: "2024-01-04", value: "1020.00" },
      ],
      excludedFlowCount: 0,
      detail: "Counterfactual: external contributions invested in VWRP.LON at each flow date.",
    },
    notes: ["Cash-flow timing convention: flow_at_close."],
    ...overrides,
  };
}

export function positions(): Position[] {
  return [
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
}

export function qualityReport(overrides: Partial<QualityReport> = {}): QualityReport {
  return {
    asOf: "2024-05-01T10:00:00Z",
    overallStatus: "degraded",
    endpointStatuses: [
      {
        endpoint: "/equity/positions",
        lastAttemptAt: "2024-05-01T10:00:00Z",
        lastSuccessAt: "2024-05-01T10:00:00Z",
        lastStatus: "success",
        itemCount: 2,
        lastError: null,
      },
      {
        endpoint: "/equity/history/orders",
        lastAttemptAt: "2024-05-01T10:00:00Z",
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
      checkedAt: "2024-05-01T10:00:00Z",
      lastSuccessAt: "2024-04-28T09:00:00Z",
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
        ts: "2024-05-01T10:00:00Z",
        replayedQuantity: "12.3456789",
        liveQuantity: "12.0000000",
        differenceQuantity: "0.3456789",
        toleranceQuantity: "0.001",
        status: "mismatch",
      },
    ],
    unsupportedActions: [],
    ...overrides,
  };
}

export function thesisDetail(overrides: Partial<ThesisDetail> = {}): ThesisDetail {
  const journal: JournalEntry[] = [
    { id: 10, thesisId: 1, createdAt: "2024-04-20T08:00:00Z", note: "Added on the pullback.", tags: "position" },
  ];
  return {
    thesis: {
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
    context: {
      t212Ticker: "AAPL_US_EQ",
      weight: 0.62,
      contribution: 0.0256,
      newsCount: 2,
      latestNewsHeadline: "Apple beats expectations",
    },
    allowedTransitions: ["validated", "invalidated", "closed"],
    editable: false,
    journal,
    ...overrides,
  };
}

export function newsItems(): NewsItem[] {
  return [
    {
      dedupeKey: "a1",
      feedKey: "example-markets",
      sourceLabel: "Example Markets",
      t212Ticker: "AAPL_US_EQ",
      isin: "US0378331005",
      headline: "Apple beats expectations",
      summary: "Quarterly results came in ahead.",
      url: "https://example.com/a",
      publishedAt: "2024-05-01T09:30:00Z",
      fetchedAt: "2024-05-01T12:00:00Z",
    },
    {
      dedupeKey: "b2",
      feedKey: "example-markets",
      sourceLabel: "Example Markets",
      t212Ticker: null,
      isin: null,
      headline: "Undated market note",
      summary: null,
      url: "https://example.com/b",
      publishedAt: null,
      fetchedAt: "2024-05-01T12:00:00Z",
    },
  ];
}
