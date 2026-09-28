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
  // Significant digits, not fraction digits: on a narrow axis (EUR 1,500-1,700) one fraction
  // digit printed "EUR 1.6K" on two different ticks.
  maximumSignificantDigits: 3,
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

// Spelled out rather than Intl: ICU versions disagree on en-GB's "Sep" vs "Sept", and the
// server and the browser must render the same text.
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "27 Sep 2026": for prose and labels. Tables keep ISO dates so columns sort and align. */
export function formatDay(value: string | null | undefined): string {
  if (!value) return EMPTY;
  const parsed = new Date(`${value.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) return value;
  return `${parsed.getUTCDate()} ${MONTHS[parsed.getUTCMonth()]} ${parsed.getUTCFullYear()}`;
}

/**
 * "VUAG" from Trading 212's "VUAGl_EQ": the part before the underscore, without the lowercase
 * exchange letter Trading 212 appends to non-US lines ("l" = London).
 */
export function displayTicker(ticker: string): string {
  const base = ticker.split("_")[0] || ticker;
  return base.replace(/[a-z]+$/, "") || base;
}

/** "SERVICE_PROVIDERS" -> "Service providers": Trading 212's category codes, read as words. */
export function categoryLabel(code: string | null): string {
  if (!code || code === "UNCATEGORISED") return "Uncategorised";
  const words = code.toLowerCase().replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

const ENTITIES: Record<string, string> = {
  amp: "&",
  lt: "<",
  gt: ">",
  quot: '"',
  apos: "'",
  nbsp: " ",
  "#39": "'",
};

/** Feed summaries sometimes carry HTML (Google News wraps them in links); show the text only. */
export function plainText(value: string | null | undefined): string {
  if (!value) return "";
  return value
    .replace(/<[^>]*>/g, " ")
    .replace(/&(#?\w+);/g, (match, name: string) => ENTITIES[name] ?? match)
    .replace(/\s+/g, " ")
    .trim();
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
