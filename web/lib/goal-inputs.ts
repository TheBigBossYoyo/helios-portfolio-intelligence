import type { GoalInputs } from "./goals";
import { averageOf, cashflowByMonth } from "./projection";
import { latestValuedNav } from "./series";
import type { MarketCalendar, NavPoint } from "./types";
import { decimalToNumber } from "./format";

/** The assumed yearly return for goals, the same default the Plan projection starts from. */
export const GOAL_RETURN = 0.06;

/**
 * What a goal is measured against: today's value (live from Trading 212 when available), the
 * amount kept invested a month over the last six full months, and the dividend income the
 * calendar expects over the next twelve months.
 */
export function goalInputs({
  navSeries,
  liveTotal,
  calendar,
  today,
}: {
  navSeries: NavPoint[];
  liveTotal: number | null;
  calendar: MarketCalendar | null;
  today: string;
}): GoalInputs {
  const months = cashflowByMonth(navSeries);
  const full = months.slice(0, -1).slice(-6);
  const value = liveTotal ?? decimalToNumber(latestValuedNav(navSeries)?.navEur ?? null) ?? 0;
  return {
    value,
    monthly: Math.max(0, averageOf(full, "kept")),
    annualReturn: GOAL_RETURN,
    income12m: calendar ? (decimalToNumber(calendar.projected12mEur) ?? 0) : 0,
    today,
  };
}
