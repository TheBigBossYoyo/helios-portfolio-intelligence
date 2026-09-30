import { describe, expect, it } from "vitest";

import { evaluateGoal, futureValue, monthsBetween, requiredMonthly } from "@/lib/goals";
import type { Goal } from "@/lib/types";

const inputs = { value: 1000, monthly: 200, annualReturn: 0.06, income12m: 24, today: "2026-09-30" };

function goal(kind: Goal["kind"], targetAmount: string, targetDate: string): Goal {
  return { id: 1, kind, name: "Goal", targetAmount, targetDate };
}

describe("goal arithmetic", () => {
  it("counts whole months and compounds monthly", () => {
    expect(monthsBetween("2026-09-30", "2027-09-30")).toBe(12);
    expect(monthsBetween("2026-09-30", "2027-09-29")).toBe(11);
    expect(futureValue(1000, 0, 0.06, 12)).toBeCloseTo(1060, 6);
    expect(futureValue(0, 100, 0, 12)).toBe(1200);
    // Required saving reproduces the target exactly.
    const monthly = requiredMonthly(1000, 10_000, 0.06, 36);
    expect(futureValue(1000, monthly, 0.06, 36)).toBeCloseTo(10_000, 6);
    expect(requiredMonthly(10_000, 5_000, 0.06, 12)).toBe(0);
  });

  it("says whether a value goal is on course, and what it would take if not", () => {
    const easy = evaluateGoal(goal("value", "5000", "2028-09-30"), inputs);
    expect(easy.status).toBe("on-track");
    expect(easy.progress).toBeCloseTo(0.2);
    expect(easy.requiredMonthly).toBeNull();

    const hard = evaluateGoal(goal("value", "50000", "2028-09-30"), inputs);
    expect(hard.status).toBe("behind");
    expect(futureValue(1000, hard.requiredMonthly ?? 0, 0.06, 24)).toBeCloseTo(50_000, 4);

    expect(evaluateGoal(goal("value", "800", "2028-09-30"), inputs).status).toBe("reached");
    expect(evaluateGoal(goal("value", "5000", "2026-01-01"), inputs).status).toBe("missed");
  });

  it("reads an income goal at today's yield", () => {
    // €24 a year on €1,000 is a 2.4% yield: €2 a month now.
    const progress = evaluateGoal(goal("income", "10", "2030-09-30"), inputs);
    expect(progress.current).toBeCloseTo(2);
    expect(progress.progress).toBeCloseTo(0.2);
    expect(progress.projected).toBeCloseTo((futureValue(1000, 200, 0.06, 48) * 0.024) / 12);
    expect(progress.status).toBe("on-track");
  });
});
