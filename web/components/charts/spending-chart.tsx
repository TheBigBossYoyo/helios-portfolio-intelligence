"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  LabelList,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatEur, formatEurCompact } from "@/lib/format";
import { MARK, SERIES } from "@/lib/viz";
import { AXIS_PROPS, BAR_CURSOR, ChartShell, GRID_PROPS, TOOLTIP_STYLES, toNumber } from "./chart-frame";

export interface SpendingBar {
  label: string;
  spent: number;
}

/**
 * Card spending per month. One series, in the "money moved" colour the Overview uses for
 * deposits and withdrawals: spending leaves the account, it is not a loss on an investment.
 * Each bar carries its value, so the chart reads without hovering.
 */
export function SpendingChart({ data }: { data: SpendingBar[] }) {
  return (
    <ChartShell height={260}>
      <ResponsiveContainer height="100%" width="100%">
        <BarChart data={data} margin={{ top: 22, right: 12, bottom: 4, left: 4 }}>
          <CartesianGrid {...GRID_PROPS} />
          <XAxis {...AXIS_PROPS} dataKey="label" />
          <YAxis {...AXIS_PROPS} tickFormatter={(value) => formatEurCompact(value)} width={60} />
          <Tooltip
            {...TOOLTIP_STYLES}
            cursor={BAR_CURSOR}
            formatter={(value) => [formatEur(toNumber(value)), "Spent"]}
          />
          <Bar
            dataKey="spent"
            fill={SERIES.three}
            maxBarSize={MARK.maxBarThickness}
            name="Spent"
            radius={MARK.barRadius}
          >
            <LabelList
              dataKey="spent"
              fill="var(--ink-3)"
              fontSize={11}
              formatter={(value: unknown) => formatEurCompact(toNumber(value))}
              position="top"
            />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartShell>
  );
}
