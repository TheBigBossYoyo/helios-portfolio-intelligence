import { humanizeStatus } from "@/lib/format";
import { statusColor } from "@/lib/viz";

/**
 * Status is never carried by color alone: every badge ships a glyph plus a text label, so it
 * survives CVD, grayscale print, and forced-colors.
 */
function glyph(status: string): string {
  switch (status.toLowerCase()) {
    case "ok":
    case "success":
    case "resolved":
    case "healthy":
      return "✓";
    case "degraded":
    case "stale":
    case "ambiguous":
    case "warning":
      return "!";
    case "override_required":
    case "unsupported_action":
    case "serious":
      return "▲";
    case "failed":
    case "error":
    case "mismatch":
    case "unresolved":
    case "critical":
      return "✕";
    default:
      return "·";
  }
}

export function StatusBadge({ status, label }: { status: string; label?: string }) {
  const color = statusColor(status);
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap border border-neutral-800 bg-neutral-950 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-neutral-300">
      <span aria-hidden="true" style={{ color }}>
        {glyph(status)}
      </span>
      {label ?? humanizeStatus(status)}
    </span>
  );
}
