import { describe, expect, it } from "vitest";
import { latestValuedNav, toDrawdownRows, toNavRows, toRollingRows } from "@/lib/series";
import type { NavPoint } from "@/lib/types";
import { performanceReport } from "./fixtures";

const report = performanceReport();

describe("toNavRows", () => {
  it("preserves unvalued days as gaps instead of inventing a level", () => {
    const rows = toNavRows(report.navSeries, report.passiveCounterfactual.series);

    expect(rows.map((row) => row.nav)).toEqual([1000, 1100, null, 1050]);
  });

  it("joins the passive series on date and leaves missing dates null", () => {
    const rows = toNavRows(report.navSeries, report.passiveCounterfactual.series);

    // The counterfactual has no 2024-01-03 point; it must not be back-filled.
    expect(rows.map((row) => row.passive)).toEqual([1000, 1010, null, 1020]);
  });

  it("returns null passives when no counterfactual series is supplied", () => {
    const rows = toNavRows(report.navSeries);

    expect(rows.every((row) => row.passive === null)).toBe(true);
  });
});

describe("toDrawdownRows", () => {
  it("measures decline of the time-weighted index from its running peak", () => {
    const series: NavPoint[] = [
      navPoint("2024-01-01", "1000"),
      navPoint("2024-01-02", "1100"),
      navPoint("2024-01-03", "1050"),
    ];
    const returns = [
      { asOfDate: "2024-01-02", value: 0.1 },
      { asOfDate: "2024-01-03", value: 1050 / 1100 - 1 },
    ];

    const rows = toDrawdownRows(series, returns);

    expect(rows[0].drawdown).toBe(0);
    expect(rows[1].drawdown).toBe(0);
    // 1050 against a 1100 peak.
    expect(rows[2].drawdown).toBeCloseTo(1050 / 1100 - 1, 12);
  });

  it("does not read a withdrawal as a loss", () => {
    // NAV halves because money left, not because anything fell: the day's TWR is 0.
    const series: NavPoint[] = [navPoint("2024-01-01", "1000"), navPoint("2024-01-02", "500")];

    const rows = toDrawdownRows(series, [{ asOfDate: "2024-01-02", value: 0 }]);

    expect(rows[1].drawdown).toBe(0);
  });

  it("carries the index across an unvalued gap instead of inventing a return", () => {
    const series: NavPoint[] = [
      navPoint("2024-01-01", "100"),
      navPoint("2024-01-02", null),
      navPoint("2024-01-03", "80"),
    ];

    // The backend emits no return across a gap, so none is passed for 2024-01-03.
    const rows = toDrawdownRows(series, []);

    expect(rows[1].drawdown).toBeNull();
    expect(rows[2].drawdown).toBe(0);
  });
});

describe("toRollingRows", () => {
  it("outer-joins the two windows onto one date axis", () => {
    const rows = toRollingRows(report.rollingVolatility30d, report.rollingVolatility90d);

    expect(rows).toEqual([
      { date: "2024-02-01", short: 0.2, long: null },
      { date: "2024-04-01", short: null, long: 0.18 },
    ]);
  });

  it("returns an empty axis when neither window has data", () => {
    expect(toRollingRows([], [])).toEqual([]);
  });
});

describe("latestValuedNav", () => {
  it("skips trailing unvalued days", () => {
    const series = [...report.navSeries, navPoint("2024-01-05", null)];

    expect(latestValuedNav(series)?.asOfDate).toBe("2024-01-04");
  });

  it("returns null when nothing was ever valued", () => {
    expect(latestValuedNav([navPoint("2024-01-01", null)])).toBeNull();
  });
});

function navPoint(asOfDate: string, navEur: string | null): NavPoint {
  return {
    asOfDate,
    navEur,
    cashBalanceEur: "0",
    securitiesValueEur: navEur,
    externalFlowEur: "0",
    valuationStatus: navEur === null ? "PARTIAL" : "VALUED",
  };
}
