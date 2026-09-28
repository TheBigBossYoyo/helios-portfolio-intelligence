"use client";

import type { ReactNode } from "react";
import { INK } from "@/lib/viz";

/** Shared axis/grid/tooltip styling so every chart in the app reads as one system. */
export const AXIS_PROPS = {
  stroke: INK.baseline,
  tick: { fill: INK.muted, fontSize: 11 },
  tickLine: false,
  axisLine: false,
} as const;

/** Hairline, solid, one step off surface. Never dashed — dashing reads as "threshold". */
export const GRID_PROPS = {
  stroke: INK.gridline,
  strokeWidth: 1,
  vertical: false,
} as const;

export const TOOLTIP_STYLES = {
  contentStyle: {
    background: "var(--surface)",
    border: "1px solid var(--border)",
    borderRadius: 12,
    boxShadow: "var(--shadow-pop)",
    fontSize: 12,
    fontFamily: "var(--font-inter)",
    padding: "8px 12px",
  },
  labelStyle: { color: "var(--ink-3)", marginBottom: 4, fontWeight: 500 },
  itemStyle: { color: "var(--ink)", padding: 0, fontVariantNumeric: "tabular-nums" },
  cursor: { stroke: INK.cursor, strokeWidth: 1 },
} as const;

/** Bar-chart hover band: a soft wash rather than a line. */
export const BAR_CURSOR = { fill: "var(--surface-3)", opacity: 0.6 } as const;

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
export function ChartLegend({
  items,
}: {
  items: { label: string; color: string; shape?: "line" | "square" }[];
}) {
  if (items.length < 2) return null;
  return (
    <ul className="mb-3 flex flex-wrap items-center gap-x-5 gap-y-1.5">
      {items.map((item) => (
        <li className="flex items-center gap-2 text-xs font-medium text-ink-2" key={item.label}>
          <span
            aria-hidden="true"
            className={item.shape === "square" ? "h-2.5 w-2.5 rounded-[3px]" : "h-[3px] w-4 rounded-full"}
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
  height = 280,
  legend,
}: {
  children: ReactNode;
  height?: number;
  legend?: { label: string; color: string; shape?: "line" | "square" }[];
}) {
  return (
    <div>
      {legend ? <ChartLegend items={legend} /> : null}
      {/* Height includes the x-axis band, so the axis labels never get their own scrollbar. */}
      <div style={{ height }}>{children}</div>
    </div>
  );
}
