import { decimalToNumber } from "./format";
import type { NavPoint } from "./types";

/**
 * Where the portfolio could be in a few years, as a range rather than one number.
 *
 * Monthly steps, lognormal returns: each month the portfolio grows by a random return with the
 * chosen expected annual return and volatility, then the month's contribution is added. Many
 * paths are simulated with a fixed seed (so the page shows the same fan every time for the same
 * inputs) and the 10th, 50th and 90th percentiles are read off each month.
 *
 * These are the consequences of assumptions the reader picks, not a forecast: past returns --
 * this portfolio's included -- say little about future ones.
 */

export interface ProjectionInput {
  startValue: number;
  monthlyContribution: number;
  /** Expected annual return, e.g. 0.06. */
  annualReturn: number;
  /** Annual volatility, e.g. 0.15. */
  annualVolatility: number;
  years: number;
  paths?: number;
  seed?: number;
}

export interface ProjectionPoint {
  month: number;
  low: number;
  median: number;
  high: number;
  /** What would have been put in by then: the start value plus every contribution. */
  contributed: number;
}

/** A small, fast, seedable PRNG (mulberry32), so the fan is reproducible. */
function mulberry32(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** A standard normal draw (Box-Muller). */
function normal(random: () => number): number {
  const u = Math.max(random(), 1e-12);
  const v = random();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

function percentile(sorted: number[], share: number): number {
  const index = (sorted.length - 1) * share;
  const lower = Math.floor(index);
  const upper = Math.ceil(index);
  return sorted[lower] + (sorted[upper] - sorted[lower]) * (index - lower);
}

export function project(input: ProjectionInput): ProjectionPoint[] {
  const months = Math.max(1, Math.round(input.years * 12));
  const paths = input.paths ?? 2000;
  const random = mulberry32(input.seed ?? 20260928);
  // Lognormal monthly step whose expectation matches the annual expected return.
  const sigma = input.annualVolatility / Math.sqrt(12);
  const mu = Math.log(1 + input.annualReturn) / 12 - (sigma * sigma) / 2;

  const values = new Array<number>(paths).fill(input.startValue);
  const points: ProjectionPoint[] = [
    {
      month: 0,
      low: input.startValue,
      median: input.startValue,
      high: input.startValue,
      contributed: input.startValue,
    },
  ];
  for (let month = 1; month <= months; month += 1) {
    for (let path = 0; path < paths; path += 1) {
      values[path] = values[path] * Math.exp(mu + sigma * normal(random)) + input.monthlyContribution;
    }
    const sorted = [...values].sort((a, b) => a - b);
    points.push({
      month,
      low: percentile(sorted, 0.1),
      median: percentile(sorted, 0.5),
      high: percentile(sorted, 0.9),
      contributed: input.startValue + input.monthlyContribution * month,
    });
  }
  return points;
}

/** The value with no randomness at all: every month earns exactly the expected return. */
export function deterministicValue(input: ProjectionInput, months: number): number {
  const monthly = (1 + input.annualReturn) ** (1 / 12) - 1;
  let value = input.startValue;
  for (let month = 0; month < months; month += 1) {
    value = value * (1 + monthly) + input.monthlyContribution;
  }
  return value;
}

export interface CashflowMonth {
  key: string;
  label: string;
  /** Money put in from outside (deposits), positive. */
  deposited: number;
  /** Spent with the 212 Card, positive. */
  spentByCard: number;
  /** Withdrawn to a bank, positive. */
  withdrawnToBank: number;
  /** Deposited minus everything that left: what stayed invested. */
  kept: number;
}

export function averageOf(months: CashflowMonth[], field: keyof Omit<CashflowMonth, "key" | "label">): number {
  if (months.length === 0) return 0;
  return months.reduce((sum, month) => sum + month[field], 0) / months.length;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/**
 * Money in and out per calendar month, from the day each flow actually happened. (The period
 * summaries measure between valued closes, so a flow on an unpriced day moves to the next
 * period there; for cash flow, the calendar is what matters.)
 */
export function cashflowByMonth(series: NavPoint[]): CashflowMonth[] {
  const months = new Map<string, CashflowMonth>();
  for (const point of series) {
    const key = point.asOfDate.slice(0, 7);
    const month =
      months.get(key) ??
      ({
        key,
        label: `${MONTHS[Number(key.slice(5, 7)) - 1]} ${key.slice(0, 4)}`,
        deposited: 0,
        spentByCard: 0,
        withdrawnToBank: 0,
        kept: 0,
      } satisfies CashflowMonth);
    const net = decimalToNumber(point.externalFlowEur) ?? 0;
    const hasGross = point.depositEur !== undefined && point.withdrawalEur !== undefined;
    const deposit = hasGross ? (decimalToNumber(point.depositEur ?? null) ?? 0) : Math.max(net, 0);
    const out = hasGross ? -(decimalToNumber(point.withdrawalEur ?? null) ?? 0) : Math.max(-net, 0);
    const card = -(decimalToNumber(point.cardSpendingEur ?? null) ?? 0);
    month.deposited += deposit;
    month.spentByCard += card;
    month.withdrawnToBank += Math.max(out - card, 0);
    month.kept += deposit - out;
    months.set(key, month);
  }
  return [...months.values()]
    .sort((a, b) => a.key.localeCompare(b.key))
    .map((month) => ({
      ...month,
      deposited: round2(month.deposited),
      spentByCard: round2(month.spentByCard),
      withdrawnToBank: round2(month.withdrawnToBank),
      kept: round2(month.kept),
    }));
}

function round2(value: number): number {
  return Math.round(value * 100) / 100;
}
