"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatEur, formatEurCompact } from "@/lib/format";
import { MARK, SERIES } from "@/lib/viz";
import { AXIS_PROPS, BAR_CURSOR, ChartShell, GRID_PROPS, TOOLTIP_STYLES, toNumber } from "./chart-frame";

export interface IncomeBar {
  key: string;
  label: string;
  received: number;
  expected: number;
}

/**
 * Dividend income by month: what arrived (solid) and what is expected (the same hue, faded and
 * outlined) on one euro axis, with today's month marked. Expected is an estimate unless the
 * company declared it; the legend and the table say so, not colour alone.
 */
export function IncomeChart({ data, currentKey }: { data: IncomeBar[]; currentKey: string }) {
  const current = data.find((row) => row.key === currentKey)?.label;
  return (
    <ChartShell
      height={260}
      legend={[
        { label: "Received", color: SERIES.three, shape: "square" },
        { label: "Expected", color: "color-mix(in srgb, var(--series-3) 35%, transparent)", shape: "square" },
      ]}
    >
      <ResponsiveContainer height="100%" width="100%">
        <BarChart barCategoryGap="18%" data={data} margin={{ top: 8, right: 8, bottom: 4, left: 0 }} stackOffset="none">
          <CartesianGrid {...GRID_PROPS} />
          <XAxis {...AXIS_PROPS} dataKey="label" interval="preserveStartEnd" minTickGap={12} />
          <YAxis {...AXIS_PROPS} tickFormatter={(value) => formatEurCompact(value)} width={52} />
          <Tooltip
            {...TOOLTIP_STYLES}
            cursor={BAR_CURSOR}
            formatter={(value, name) => [formatEur(toNumber(value)), name]}
          />
          {current ? (
            <ReferenceLine
              label={{ value: "Now", position: "insideTopRight", fill: "var(--ink-3)", fontSize: 11 }}
              stroke="var(--chart-cursor)"
              strokeDasharray="3 3"
              x={current}
            />
          ) : null}
          <Bar
            dataKey="received"
            fill={SERIES.three}
            maxBarSize={MARK.maxBarThickness}
            name="Received"
            radius={MARK.barRadius}
            stackId="income"
          />
          <Bar
            dataKey="expected"
            fill={SERIES.three}
            fillOpacity={0.35}
            maxBarSize={MARK.maxBarThickness}
            name="Expected"
            radius={MARK.barRadius}
            stackId="income"
            stroke={SERIES.three}
            strokeDasharray="3 2"
            strokeWidth={1}
          />
        </BarChart>
      </ResponsiveContainer>
    </ChartShell>
  );
}
