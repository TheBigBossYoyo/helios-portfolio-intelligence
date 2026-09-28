/**
 * Chart tokens for Helios.
 *
 * Every value here is a CSS custom property, defined per theme in app/globals.css. SVG presentation
 * attributes accept `var(...)`, so one chart renders correctly in light and dark mode with no
 * re-render and no hydration mismatch when the theme changes.
 *
 * The series steps are the dataviz reference palette, validated against Helios's own surfaces:
 *
 *   validate_palette.js "<8 light slots>" --mode light --surface "#ffffff"
 *     -> ALL CHECKS PASS (worst adjacent CVD dE 9.1, normal-vision dE 19.6); slots 3/4/5 are
 *        under 3:1 on white, so every chart also ships a table view and a legend.
 *   validate_palette.js "<8 dark slots>" --mode dark --surface "#141820"
 *     -> ALL CHECKS PASS (worst adjacent CVD dE 8.4, normal-vision dE 19.3, all >= 3:1)
 *   first three slots, --pairs all, light -> PASS (CVD dE 9.2, normal-vision dE 24.0)
 *
 * Categorical slots are assigned in fixed order and never cycled. A chart with more categories
 * than slots folds the tail into "Other"; scatter-like forms stop at three.
 *
 * The UI accent (indigo) and the brand sun (amber) are chrome, never data colours.
 */

export const SERIES = {
  one: "var(--series-1)",
  two: "var(--series-2)",
  three: "var(--series-3)",
  four: "var(--series-4)",
  five: "var(--series-5)",
  six: "var(--series-6)",
  seven: "var(--series-7)",
  red: "var(--series-8)",
} as const;

/** The categorical order, for charts that colour many entities (allocation). */
export const CATEGORICAL = [
  "var(--series-1)",
  "var(--series-2)",
  "var(--series-3)",
  "var(--series-4)",
  "var(--series-5)",
  "var(--series-6)",
  "var(--series-7)",
  "var(--series-8)",
] as const;

/** Folded-tail colour ("Other"): neutral, so it never reads as a ninth category. */
export const OTHER = "var(--ink-4)";

/** Diverging poles for signed magnitudes (contribution, attribution). Neutral midpoint. */
export const DIVERGING = {
  positive: SERIES.one,
  negative: SERIES.red,
  midpoint: "var(--chart-axis)",
} as const;

/** Reserved status tokens. Never reused as a series colour. */
export const STATUS = {
  good: "var(--status-good)",
  warning: "var(--status-warning)",
  serious: "var(--status-serious)",
  critical: "var(--status-critical)",
} as const;

/**
 * Signed-delta text. Text-safe steps (the status marks are too light to be read as text on a
 * white surface). Direction is always also carried by the sign and an arrow, never hue alone.
 */
export const DELTA = {
  up: "var(--positive)",
  down: "var(--negative)",
} as const;

/** Chart chrome. Text always wears an ink token, never a series colour. */
export const INK = {
  surface: "var(--surface)",
  panel: "var(--surface)",
  primary: "var(--ink)",
  secondary: "var(--ink-2)",
  muted: "var(--chart-tick)",
  gridline: "var(--chart-grid)",
  baseline: "var(--chart-axis)",
  cursor: "var(--chart-cursor)",
} as const;

/** Mark specs, fixed across every chart in the app. */
export const MARK = {
  lineWidth: 2,
  markerRadius: 4,
  /** Dots and end-markers carry a surface-coloured ring so they stay legible on crossings. */
  ringWidth: 2,
  areaOpacity: 0.16,
  maxBarThickness: 24,
  barRadius: 4,
} as const;

export function statusColor(status: string): string {
  switch (statusTone(status)) {
    case "good":
      return STATUS.good;
    case "warning":
      return STATUS.warning;
    case "serious":
      return STATUS.serious;
    case "critical":
      return STATUS.critical;
    default:
      return "var(--ink-4)";
  }
}

export type StatusTone = "good" | "warning" | "serious" | "critical" | "neutral";

export function statusTone(status: string): StatusTone {
  switch (status.toLowerCase()) {
    case "ok":
    case "success":
    case "resolved":
    case "healthy":
    case "active":
    case "validated":
      return "good";
    case "degraded":
    case "stale":
    case "ambiguous":
    case "warning":
    case "insufficient_data":
    case "draft":
      return "warning";
    case "override_required":
    case "unsupported_action":
    case "serious":
      return "serious";
    case "failed":
    case "error":
    case "mismatch":
    case "unresolved":
    case "critical":
    case "invalidated":
      return "critical";
    default:
      return "neutral";
  }
}
