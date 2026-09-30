/**
 * Goal arithmetic: where steady saving takes you by the target date, and what it would take.
 *
 * Deterministic on purpose: one expected return, compounded monthly, with the monthly amount
 * added at the end of each month. The Plan page's projection shows the uncertainty around that
 * middle path; here the question is simply "on this course, do I get there?".
 */

import type { Goal } from "./types";

export type GoalStatus = "reached" | "on-track" | "behind" | "missed";

export interface GoalInputs {
  /** Portfolio value today (EUR). */
  value: number;
  /** What you keep investing a month (EUR). */
  monthly: number;
  /** Expected yearly return, e.g. 0.06. */
  annualReturn: number;
  /** Dividend income expected over the next twelve months (EUR). */
  income12m: number;
  /** "YYYY-MM-DD" */
  today: string;
}

export interface GoalProgress {
  goal: Goal;
  target: number;
  /** Where the goal stands today: value, or monthly dividend income. */
  current: number;
  progress: number;
  monthsLeft: number;
  /** Where the current course lands by the target date (same unit as the target). */
  projected: number;
  status: GoalStatus;
  /** Monthly saving that would reach the target by the date (EUR a month), when behind. */
  requiredMonthly: number | null;
}

export function monthsBetween(from: string, to: string): number {
  const [fy, fm, fd] = from.split("-").map(Number);
  const [ty, tm, td] = to.split("-").map(Number);
  return (ty - fy) * 12 + (tm - fm) - (td < fd ? 1 : 0);
}

/** Future value after `months` of monthly compounding with a contribution each month. */
export function futureValue(value: number, monthly: number, annualReturn: number, months: number): number {
  if (months <= 0) return value;
  const rate = (1 + annualReturn) ** (1 / 12) - 1;
  if (Math.abs(rate) < 1e-12) return value + monthly * months;
  const growth = (1 + rate) ** months;
  return value * growth + monthly * ((growth - 1) / rate);
}

/** Monthly contribution that turns `value` into `target` in `months`. */
export function requiredMonthly(value: number, target: number, annualReturn: number, months: number): number {
  if (months <= 0) return Number.POSITIVE_INFINITY;
  const rate = (1 + annualReturn) ** (1 / 12) - 1;
  const growth = (1 + rate) ** months;
  const gap = target - value * growth;
  if (gap <= 0) return 0;
  return Math.abs(rate) < 1e-12 ? gap / months : (gap * rate) / (growth - 1);
}

export function evaluateGoal(goal: Goal, inputs: GoalInputs): GoalProgress {
  const target = Number(goal.targetAmount);
  const monthsLeft = Math.max(0, monthsBetween(inputs.today, goal.targetDate));
  const endValue = futureValue(inputs.value, inputs.monthly, inputs.annualReturn, monthsLeft);

  if (goal.kind === "income") {
    // Income is read at today's yield on what you hold: more money in, same mix.
    const yieldRate = inputs.value > 0 ? inputs.income12m / inputs.value : 0;
    const current = inputs.income12m / 12;
    const projected = (endValue * yieldRate) / 12;
    const neededValue = yieldRate > 0 ? (target * 12) / yieldRate : Number.POSITIVE_INFINITY;
    const status: GoalStatus =
      current >= target ? "reached" : monthsLeft <= 0 ? "missed" : projected >= target ? "on-track" : "behind";
    return {
      goal,
      target,
      current,
      progress: target > 0 ? Math.min(current / target, 1) : 0,
      monthsLeft,
      projected,
      status,
      requiredMonthly:
        status === "behind" && Number.isFinite(neededValue)
          ? requiredMonthly(inputs.value, neededValue, inputs.annualReturn, monthsLeft)
          : null,
    };
  }

  const status: GoalStatus =
    inputs.value >= target ? "reached" : monthsLeft <= 0 ? "missed" : endValue >= target ? "on-track" : "behind";
  return {
    goal,
    target,
    current: inputs.value,
    progress: target > 0 ? Math.min(inputs.value / target, 1) : 0,
    monthsLeft,
    projected: endValue,
    status,
    requiredMonthly: status === "behind" ? requiredMonthly(inputs.value, target, inputs.annualReturn, monthsLeft) : null,
  };
}
