import type { MetricValue } from "./types";

/**
 * Rendering helpers.
 *
 * The rule throughout: an absent value renders as an em dash, never as 0. A dashboard that shows
 * "0.00%" for a metric the backend declared `insufficient_data` is lying, and M3 went to some
 * trouble to make those statuses explicit.
 */

export const EMPTY = "—";

const EUR = new Intl.NumberFormat("en-IE", {
  style: "currency",
  currency: "EUR",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const COMPACT_EUR = new Intl.NumberFormat("en-IE", {
  style: "currency",
  currency: "EUR",
  notation: "compact",
  maximumFractionDigits: 1,
});

/** Decimal strings arrive exact; parse only here, at the render boundary. */
export function decimalToNumber(value: string | null | undefined): number | null {
  if (value === null || value === undefined || value.trim() === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function formatEur(value: string | number | null | undefined): string {
  const numeric = typeof value === "number" ? value : decimalToNumber(value ?? null);
  return numeric === null ? EMPTY : EUR.format(numeric);
}

export function formatEurCompact(value: string | number | null | undefined): string {
  const numeric = typeof value === "number" ? value : decimalToNumber(value ?? null);
  return numeric === null ? EMPTY : COMPACT_EUR.format(numeric);
}

export function formatPercent(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EMPTY;
  return `${(value * 100).toFixed(digits)}%`;
}

export function formatSignedPercent(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EMPTY;
  const sign = value > 0 ? "+" : "";
  return `${sign}${(value * 100).toFixed(digits)}%`;
}

export function formatRatio(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EMPTY;
  return value.toFixed(digits);
}

/** Quantities keep their full precision — they are fractional-share exact. */
export function formatQuantity(value: string | null | undefined): string {
  if (value === null || value === undefined || value.trim() === "") return EMPTY;
  return value;
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return EMPTY;
  return value.slice(0, 10);
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return EMPTY;
  return value.replace("T", " ").replace(/\.\d+/, "").replace(/(Z|\+00:00)$/, " UTC");
}

/** A metric renders its value only when the backend says `ok`; otherwise the status shows. */
export function metricText(
  metric: MetricValue | null | undefined,
  render: (value: number) => string,
): string {
  if (!metric) return EMPTY;
  if (metric.status !== "ok" || metric.value === null) return EMPTY;
  return render(metric.value);
}

export function metricIsAvailable(metric: MetricValue | null | undefined): boolean {
  return Boolean(metric && metric.status === "ok" && metric.value !== null);
}

/** Turns `insufficient_data` into `Insufficient data` for display. */
export function humanizeStatus(status: string): string {
  const spaced = status.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}
