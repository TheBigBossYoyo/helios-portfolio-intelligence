"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatEur, formatEurCompact } from "@/lib/format";
import { DIVERGING, MARK, SERIES } from "@/lib/viz";
import { AXIS_PROPS, BAR_CURSOR, ChartShell, GRID_PROPS, TOOLTIP_STYLES, toNumber } from "./chart-frame";

export interface MonthlyBar {
  label: string;
  /** What the investments made or lost that month. */
  result: number | null;
  /** Deposits minus withdrawals that month: money moved, not performance. */
  moved: number;
}

/**
 * Month by month, the two things a change in value is made of, side by side on one euro axis:
 * the investment result (blue up, red down — a signed magnitude, so diverging) and money moved
 * (its own colour, the same one the "money put in" line uses). A month with a big deposit and a
 * small loss can no longer look like a good month.
 */
export function MonthlyChart({ data }: { data: MonthlyBar[] }) {
  const rows = data.map((row) => ({
    ...row,
    // Not `fill`: Recharts applies a row's `fill` to every bar in it, money moved included.
    tone: row.result !== null && row.result < 0 ? DIVERGING.negative : DIVERGING.positive,
  }));

  return (
    <ChartShell
      height={300}
      legend={[
        { label: "Investment result", color: SERIES.one, shape: "square" },
        { label: "Money added", color: SERIES.three, shape: "square" },
      ]}
    >
      <ResponsiveContainer height="100%" width="100%">
        <BarChart barCategoryGap="24%" barGap={2} data={rows} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
          <CartesianGrid {...GRID_PROPS} />
          <XAxis {...AXIS_PROPS} dataKey="label" />
          <YAxis {...AXIS_PROPS} tickFormatter={(value) => formatEurCompact(value)} width={64} />
          {/* Length encodes magnitude, so the baseline is zero and drawn. */}
          <ReferenceLine stroke="var(--chart-axis)" y={0} />
          <Tooltip
            {...TOOLTIP_STYLES}
            cursor={BAR_CURSOR}
            formatter={(value, name) => [formatEur(toNumber(value)), name]}
          />
          <Bar
            dataKey="result"
            maxBarSize={MARK.maxBarThickness}
            name="Investment result"
            radius={MARK.barRadius}
          >
            {rows.map((row) => (
              <Cell fill={row.tone} key={row.label} />
            ))}
          </Bar>
          <Bar
            dataKey="moved"
            fill={SERIES.three}
            maxBarSize={MARK.maxBarThickness}
            name="Money added"
            radius={MARK.barRadius}
          />
        </BarChart>
      </ResponsiveContainer>
    </ChartShell>
  );
}
