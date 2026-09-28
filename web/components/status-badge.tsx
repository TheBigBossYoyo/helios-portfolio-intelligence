import { humanizeStatus } from "@/lib/format";
import { statusColor, statusTone } from "@/lib/viz";

/**
 * Status is never carried by color alone: every badge ships a glyph plus a text label, so it
 * survives CVD, grayscale print, and forced-colors.
 */
function glyph(status: string): string {
  switch (statusTone(status)) {
    case "good":
      return "✓";
    case "warning":
      return "!";
    case "serious":
      return "▲";
    case "critical":
      return "✕";
    default:
      return "·";
  }
}

const TONE_CLASSES = {
  good: "bg-positive-soft text-positive",
  warning: "bg-warning-soft text-warning",
  serious: "bg-warning-soft text-warning",
  critical: "bg-negative-soft text-negative",
  neutral: "bg-surface-3 text-ink-2",
} as const;

export function StatusBadge({ status, label }: { status: string; label?: string }) {
  const tone = statusTone(status);
  return (
    <span
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ${TONE_CLASSES[tone]}`}
    >
      <span
        aria-hidden="true"
        className="text-[11px] leading-none"
        style={{ color: tone === "neutral" ? undefined : statusColor(status) }}
      >
        {glyph(status)}
      </span>
      {label ?? humanizeStatus(status)}
    </span>
  );
}
