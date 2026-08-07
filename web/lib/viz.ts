/**
 * Chart tokens for Helios.
 *
 * These are the dataviz reference palette's **dark** steps, re-validated against this app's own
 * chart surface (#09090b) rather than the reference #1a1a19:
 *
 *   node scripts/validate_palette.js "#3987e5,#d95926,#199e70" --mode dark \
 *     --surface "#09090b" --pairs all   ->  ALL CHECKS PASS
 *     (worst all-pairs CVD dE 9.4 deutan; worst normal-vision dE 20.9; all >= 3:1)
 *
 * The brand amber (#ffb000) is deliberately NOT a series color: it measures OKLCH L 0.812, well
 * outside the 0.48-0.67 band a data mark needs on this surface, and the validator FAILs it.
 * Amber stays what it already is here — UI chrome and accent. Series identity comes from the
 * slots below.
 *
 * Categorical slots are assigned in fixed order and never cycled. Helios never plots more than
 * three series on one axis, which is also the all-pairs-validated cap.
 */

export const SERIES = {
  /** slot 1 - blue. Portfolio / primary measure. */
  one: "#3987e5",
  /** slot 2 - orange. Comparison series (passive counterfactual, 90d window). */
  two: "#d95926",
  /** slot 3 - aqua. Third series where one is genuinely needed. */
  three: "#199e70",
  /** slot 8 - categorical red. Single-series loss magnitude (drawdown), not a status token. */
  red: "#e66767",
} as const;

/** Diverging poles for signed magnitudes (contribution). Warm/cool, neutral gray midpoint. */
export const DIVERGING = {
  positive: SERIES.one,
  negative: SERIES.red,
  midpoint: "#383835",
} as const;

/** Reserved status tokens. Never reused as a series color. */
export const STATUS = {
  good: "#0ca30c",
  warning: "#fab219",
  serious: "#ec835a",
  critical: "#d03b3b",
} as const;

/**
 * Signed-delta text (unrealized P/L and the like). These are the status tokens, not the brand
 * accent: acid green is chrome, and reusing it for "gain" would conflate decoration with meaning.
 * Direction is always also carried by the number's own sign, never by hue alone.
 */
export const DELTA = {
  up: STATUS.good,
  down: STATUS.critical,
} as const;

/** Chart chrome. Text always wears an ink token, never a series color. */
export const INK = {
  surface: "#09090b",
  panel: "#121214",
  primary: "#ffffff",
  secondary: "#c3c2b7",
  muted: "#898781",
  gridline: "#2c2c2a",
  baseline: "#383835",
} as const;

/** Mark specs, fixed across every chart in the app. */
export const MARK = {
  lineWidth: 2,
  markerRadius: 4,
  /** Dots and end-markers carry a surface-colored ring so they stay legible on crossings. */
  ringWidth: 2,
  areaOpacity: 0.1,
  maxBarThickness: 24,
  barRadius: 4,
} as const;

export function statusColor(status: string): string {
  switch (status.toLowerCase()) {
    case "ok":
    case "success":
    case "resolved":
    case "healthy":
      return STATUS.good;
    case "degraded":
    case "stale":
    case "ambiguous":
    case "warning":
      return STATUS.warning;
    case "override_required":
    case "unsupported_action":
    case "serious":
      return STATUS.serious;
    case "failed":
    case "error":
    case "mismatch":
    case "unresolved":
    case "critical":
      return STATUS.critical;
    default:
      return INK.muted;
  }
}
