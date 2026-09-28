"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatEur, formatEurCompact } from "@/lib/format";
import type { CashflowMonth } from "@/lib/projection";
import { MARK, SERIES } from "@/lib/viz";
import { AXIS_PROPS, BAR_CURSOR, ChartShell, GRID_PROPS, TOOLTIP_STYLES, toNumber } from "./chart-frame";

/**
 * Each month: what came in, what left by card, and what stayed invested, side by side on one
 * euro axis. Categorical colours in their fixed order; the legend names every series.
 */
export function CashflowChart({ data }: { data: CashflowMonth[] }) {
  return (
    <ChartShell
      height={280}
      legend={[
        { label: "Deposited", color: SERIES.three, shape: "square" },
        { label: "Spent by card", color: SERIES.two, shape: "square" },
        { label: "Kept invested", color: SERIES.one, shape: "square" },
      ]}
    >
      <ResponsiveContainer height="100%" width="100%">
        <BarChart barCategoryGap="22%" barGap={2} data={data} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
          <CartesianGrid {...GRID_PROPS} />
          <XAxis {...AXIS_PROPS} dataKey="label" />
          <YAxis {...AXIS_PROPS} tickFormatter={(value) => formatEurCompact(value)} width={64} />
          <Tooltip
            {...TOOLTIP_STYLES}
            cursor={BAR_CURSOR}
            formatter={(value, name) => [formatEur(toNumber(value)), name]}
          />
          <Bar dataKey="deposited" fill={SERIES.three} maxBarSize={MARK.maxBarThickness} name="Deposited" radius={MARK.barRadius} />
          <Bar dataKey="spentByCard" fill={SERIES.two} maxBarSize={MARK.maxBarThickness} name="Spent by card" radius={MARK.barRadius} />
          <Bar dataKey="kept" fill={SERIES.one} maxBarSize={MARK.maxBarThickness} name="Kept invested" radius={MARK.barRadius} />
        </BarChart>
      </ResponsiveContainer>
    </ChartShell>
  );
}
