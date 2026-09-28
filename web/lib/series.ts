import { decimalToNumber } from "./format";
import type { DailyReturnPoint, DailyValuePoint, NavPoint } from "./types";

/**
 * Pure shaping helpers that turn API DTOs into chart rows.
 *
 * Everything here preserves gaps. A day the backend refused to value stays `null` all the way to
 * the chart, which draws a break rather than a straight line through data that does not exist.
 */

export interface NavRow {
  date: string;
  nav: number | null;
  passive: number | null;
  /** Money put in, net of withdrawals, to date. The gap between this and `nav` is profit. */
  invested: number | null;
}

export function toNavRows(
  navSeries: NavPoint[],
  passiveSeries: DailyValuePoint[] = [],
): NavRow[] {
  const passiveByDate = new Map(
    passiveSeries.map((point) => [point.asOfDate, decimalToNumber(point.value)]),
  );
  return navSeries.map((point) => ({
    date: point.asOfDate,
    nav: point.navEur === null ? null : decimalToNumber(point.navEur),
    passive: passiveByDate.get(point.asOfDate) ?? null,
    invested:
      point.netDepositsToDateEur === undefined ? null : decimalToNumber(point.netDepositsToDateEur),
  }));
}

/**
 * The rows a period covers: from its starting close (inclusive, so the line starts where the
 * period does) to its end. A period starting at inception keeps everything.
 */
export function rowsInPeriod<T extends { date: string }>(
  rows: T[],
  startDate: string | null,
  endDate: string | null,
): T[] {
  return rows.filter(
    (row) => (startDate === null || row.date >= startDate) && (endDate === null || row.date <= endDate),
  );
}

export interface DrawdownRow {
  date: string;
  drawdown: number | null;
}

/**
 * Drawdown of the time-weighted growth index, from its running peak.
 *
 * Built by compounding the backend's daily time-weighted returns, not from raw NAV: on NAV every
 * withdrawal reads as a loss (a real account's 44 withdrawals once showed a -37.6% "drawdown").
 * This matches the backend's max-drawdown figure by construction. Unvalued days are `null`, and
 * across a gap the index carries its last level — no return is known there, so none is invented.
 */
export function toDrawdownRows(
  navSeries: NavPoint[],
  dailyReturns: { asOfDate: string; value: number }[],
): DrawdownRow[] {
  const returns = new Map(dailyReturns.map((point) => [point.asOfDate, point.value]));
  let level: number | null = null;
  let peak = 1;
  return navSeries.map((point) => {
    const nav = point.navEur === null ? null : decimalToNumber(point.navEur);
    if (nav === null || nav <= 0) {
      return { date: point.asOfDate, drawdown: null };
    }
    level = level === null ? 1 : level * (1 + (returns.get(point.asOfDate) ?? 0));
    peak = Math.max(peak, level);
    return { date: point.asOfDate, drawdown: level / peak - 1 };
  });
}

export interface RollingRow {
  date: string;
  short: number | null;
  long: number | null;
}

/** Outer-joins the 30d and 90d series on date so one axis carries both windows. */
export function toRollingRows(
  shortSeries: DailyReturnPoint[],
  longSeries: DailyReturnPoint[],
): RollingRow[] {
  const shortByDate = new Map(shortSeries.map((point) => [point.asOfDate, point.value]));
  const longByDate = new Map(longSeries.map((point) => [point.asOfDate, point.value]));
  const dates = [...new Set([...shortByDate.keys(), ...longByDate.keys()])].sort();
  return dates.map((date) => ({
    date,
    short: shortByDate.get(date) ?? null,
    long: longByDate.get(date) ?? null,
  }));
}

/** Latest valued NAV, or null when nothing in the series was valued. */
export function latestValuedNav(navSeries: NavPoint[]): NavPoint | null {
  for (let index = navSeries.length - 1; index >= 0; index -= 1) {
    if (navSeries[index].navEur !== null) return navSeries[index];
  }
  return null;
}

/**
 * Compound daily returns into a cumulative-return path (0 = start), for the TWR sparkline.
 * Uses the same geometric linking as the backend's cumulative TWR, so the line ends at the
 * figure the tile prints.
 */
export function compoundReturns(points: { value: number }[]): number[] {
  let growth = 1;
  return points.map((point) => {
    growth *= 1 + point.value;
    return growth - 1;
  });
}

/** Keep the last `count` items: sparklines show the recent shape, not the whole history. */
export function tail<T>(items: T[], count: number): T[] {
  return items.length > count ? items.slice(items.length - count) : items;
}

export interface GrowthRow {
  date: string;
  portfolio: number | null;
  benchmark: number | null;
}

/**
 * Indexes NAV and a benchmark's path to a common base of 100, each anchored to its own first
 * observed value. This is "growth of 100": the two lines become comparable in shape even though
 * their starting levels (EUR NAV vs. an index proxy price) are never the same units.
 *
 * A series with no observed value at all stays empty rather than dividing by a fabricated base.
 */
export function toGrowthRows(rows: NavRow[]): GrowthRow[] {
  const portfolioBase = rows.find((row) => row.nav !== null && row.nav > 0)?.nav ?? null;
  const benchmarkBase = rows.find((row) => row.passive !== null && row.passive > 0)?.passive ?? null;
  return rows.map((row) => ({
    date: row.date,
    portfolio: portfolioBase !== null && row.nav !== null ? (row.nav / portfolioBase) * 100 : null,
    benchmark:
      benchmarkBase !== null && row.passive !== null ? (row.passive / benchmarkBase) * 100 : null,
  }));
}
