"use client";

import type { ReactNode } from "react";
import { INK } from "@/lib/viz";

/** Shared axis/grid/tooltip styling so every chart in the app reads as one system. */
export const AXIS_PROPS = {
  stroke: INK.baseline,
  tick: { fill: INK.muted, fontSize: 10 },
  tickLine: false,
} as const;

/** Hairline, solid, one step off surface. Never dashed — dashing reads as "threshold". */
export const GRID_PROPS = {
  stroke: INK.gridline,
  strokeWidth: 1,
  vertical: false,
} as const;

export const TOOLTIP_STYLES = {
  contentStyle: {
    background: INK.panel,
    border: `1px solid ${INK.gridline}`,
    borderRadius: 0,
    fontSize: 11,
    fontFamily: "var(--font-jetbrains-mono)",
    padding: "6px 8px",
  },
  labelStyle: { color: INK.secondary, marginBottom: 4 },
  itemStyle: { color: INK.primary, padding: 0 },
  cursor: { stroke: INK.baseline, strokeWidth: 1 },
} as const;

/**
 * Recharts hands tooltip formatters a widened `ValueType`. Narrow it back to a finite number (or
 * null for a gap) in one place rather than at every call site.
 */
export function toNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

/**
 * A legend is always present for two or more series; a single-series chart gets none, because
 * its title already says what is plotted.
 */
export function ChartLegend({ items }: { items: { label: string; color: string }[] }) {
  if (items.length < 2) return null;
  return (
    <ul className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1">
      {items.map((item) => (
        <li className="flex items-center gap-1.5 text-[10px] text-neutral-400" key={item.label}>
          <span
            aria-hidden="true"
            className="inline-block h-0.5 w-4"
            style={{ background: item.color }}
          />
          {item.label}
        </li>
      ))}
    </ul>
  );
}

export function ChartShell({
  children,
  height = 260,
  legend,
}: {
  children: ReactNode;
  height?: number;
  legend?: { label: string; color: string }[];
}) {
  return (
    <div>
      {legend ? <ChartLegend items={legend} /> : null}
      {/* Height includes the x-axis band, so the axis labels never get their own scrollbar. */}
      <div style={{ height }}>{children}</div>
    </div>
  );
}
