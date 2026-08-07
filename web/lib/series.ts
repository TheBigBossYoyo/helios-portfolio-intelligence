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
  }));
}

export interface DrawdownRow {
  date: string;
  drawdown: number | null;
}

/**
 * Drawdown from the running peak of valued NAV.
 *
 * Unvalued days produce a `null` and, importantly, do not advance the peak — a gap must not be
 * read as a new high.
 */
export function toDrawdownRows(navSeries: NavPoint[]): DrawdownRow[] {
  let peak: number | null = null;
  return navSeries.map((point) => {
    const nav = point.navEur === null ? null : decimalToNumber(point.navEur);
    if (nav === null || nav <= 0) {
      return { date: point.asOfDate, drawdown: null };
    }
    peak = peak === null ? nav : Math.max(peak, nav);
    return { date: point.asOfDate, drawdown: peak > 0 ? nav / peak - 1 : null };
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
