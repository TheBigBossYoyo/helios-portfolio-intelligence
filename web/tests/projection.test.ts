import { describe, expect, it } from "vitest";
import { averageOf, cashflowByMonth, deterministicValue, project } from "@/lib/projection";

describe("project", () => {
  const input = {
    startValue: 1000,
    monthlyContribution: 100,
    annualReturn: 0.06,
    annualVolatility: 0.15,
    years: 10,
    paths: 1500,
  };

  it("starts at today's value and counts every contribution", () => {
    const points = project(input);

    expect(points).toHaveLength(121);
    expect(points[0]).toMatchObject({ month: 0, low: 1000, median: 1000, high: 1000 });
    expect(points[120].contributed).toBe(1000 + 100 * 120);
  });

  it("fans out: low below median below high, widening with time", () => {
    const points = project(input);
    const year1 = points[12];
    const year10 = points[120];

    expect(year10.low).toBeLessThan(year10.median);
    expect(year10.median).toBeLessThan(year10.high);
    expect(year10.high - year10.low).toBeGreaterThan(year1.high - year1.low);
  });

  it("centres near the no-randomness path and is reproducible", () => {
    const expected = deterministicValue(input, 120);
    const median = project(input)[120].median;

    // With volatility the median sits a little below the expected path, never far from it.
    expect(median / expected).toBeGreaterThan(0.85);
    expect(median / expected).toBeLessThan(1.02);
    expect(project(input)[120].median).toBe(median);
  });

  it("with no volatility collapses to the deterministic path", () => {
    const flat = project({ ...input, annualVolatility: 0, paths: 10 });

    expect(flat[120].low).toBeCloseTo(deterministicValue(input, 120), 6);
    expect(flat[120].high).toBeCloseTo(flat[120].low, 6);
  });
});

describe("averageOf", () => {
  it("averages a field and handles no months", () => {
    const months = [
      { key: "a", label: "a", deposited: 100, spentByCard: 40, withdrawnToBank: 0, kept: 60 },
      { key: "b", label: "b", deposited: 300, spentByCard: 20, withdrawnToBank: 80, kept: 200 },
    ];

    expect(averageOf(months, "kept")).toBe(130);
    expect(averageOf([], "kept")).toBe(0);
  });
});

describe("cashflowByMonth", () => {
  it("buckets each day's flows into its own calendar month", () => {
    const point = (asOfDate: string, flows: Record<string, string>) => ({
      asOfDate,
      navEur: null,
      cashBalanceEur: "0",
      securitiesValueEur: null,
      externalFlowEur: flows.net ?? "0",
      valuationStatus: "PARTIAL",
      ...flows,
    });
    const months = cashflowByMonth([
      // An unvalued April day still counts in April.
      point("2026-04-08", { net: "551", depositEur: "551", withdrawalEur: "0", cardSpendingEur: "0" }),
      point("2026-04-20", { net: "-40", depositEur: "0", withdrawalEur: "-40", cardSpendingEur: "-30" }),
      point("2026-05-02", { net: "100", depositEur: "100", withdrawalEur: "0", cardSpendingEur: "0" }),
    ]);

    expect(months).toEqual([
      { key: "2026-04", label: "Apr 2026", deposited: 551, spentByCard: 30, withdrawnToBank: 10, kept: 511 },
      { key: "2026-05", label: "May 2026", deposited: 100, spentByCard: 0, withdrawnToBank: 0, kept: 100 },
    ]);
  });
});
