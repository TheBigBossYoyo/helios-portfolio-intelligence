import { describe, expect, it } from "vitest";

import { planAllocation, roundToCents } from "@/lib/rebalance";

const holdings = [
  { ticker: "VWRP", name: "All-World", value: 600, target: 0.7 },
  { ticker: "AAPL", name: "Apple", value: 300, target: 0.2 },
  { ticker: "NVDA", name: "Nvidia", value: 100, target: 0.1 },
];

describe("planAllocation", () => {
  it("measures drift against the targets", () => {
    const plan = planAllocation(holdings, 0);
    const byTicker = Object.fromEntries(plan.rows.map((row) => [row.ticker, row]));
    expect(byTicker.VWRP.weight).toBeCloseTo(0.6);
    expect(byTicker.VWRP.drift).toBeCloseTo(-0.1);
    expect(byTicker.AAPL.drift).toBeCloseTo(0.1);
    // Exactly on target without new money: sell 100 of Apple, buy 100 of the fund.
    expect(byTicker.VWRP.fullTrade).toBeCloseTo(100);
    expect(byTicker.AAPL.fullTrade).toBeCloseTo(-100);
    expect(byTicker.NVDA.fullTrade).toBeCloseTo(0);
  });

  it("splits a deposit only into what is below target, and never sells", () => {
    const plan = planAllocation(holdings, 200);
    const byTicker = Object.fromEntries(plan.rows.map((row) => [row.ticker, row]));
    // After 200 in, the fund's target is 0.7 x 1200 = 840: 240 short; Nvidia 120: 20 short.
    expect(byTicker.VWRP.buy).toBeCloseTo((200 * 240) / 260);
    expect(byTicker.NVDA.buy).toBeCloseTo((200 * 20) / 260);
    expect(byTicker.AAPL.buy).toBe(0);
    expect(plan.rows.reduce((sum, row) => sum + row.buy, 0)).toBeCloseTo(200);
    expect(plan.maxDriftAfter).toBeLessThan(0.1);
  });

  it("scales a partial plan and leaves holdings without a target outside it", () => {
    const plan = planAllocation(
      [
        { ticker: "A", name: null, value: 100, target: 0.3 },
        { ticker: "B", name: null, value: 100, target: 0.3 },
        { ticker: "C", name: null, value: 500, target: null },
      ],
      0,
    );
    expect(plan.targetSum).toBeCloseTo(0.6);
    expect(plan.rows.map((row) => row.target)).toEqual([0.5, 0.5]);
    expect(plan.outside.map((row) => row.ticker)).toEqual(["C"]);
    expect(plan.plannedValue).toBe(200);
  });

  it("copes with an empty portfolio", () => {
    const plan = planAllocation([{ ticker: "A", name: null, value: 0, target: 1 }], 50);
    expect(plan.rows[0].buy).toBeCloseTo(50);
    expect(plan.rows[0].weightAfter).toBeCloseTo(1);
  });
});

describe("roundToCents", () => {
  it("adds up exactly to the total", () => {
    const parts = roundToCents([33.333, 33.333, 33.334], 100);
    expect(parts.reduce((sum, value) => sum + value, 0)).toBeCloseTo(100, 10);
    expect(parts).toEqual([33.33, 33.33, 33.34]);
  });
});
