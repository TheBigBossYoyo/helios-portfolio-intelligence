"use client";

import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatPercent, formatRatio } from "@/lib/format";
import { INK, MARK, SERIES } from "@/lib/viz";
import { AXIS_PROPS, ChartShell, GRID_PROPS, TOOLTIP_STYLES, toNumber } from "./chart-frame";

export interface RollingPoint {
  date: string;
  short: number | null;
  long: number | null;
}

/**
 * Props crossing the server/client boundary must be serializable, so the caller names a format
 * rather than passing a formatter function.
 */
export type RollingValueFormat = "percent" | "ratio";

function formatValue(value: number | null, format: RollingValueFormat): string {
  return format === "percent" ? formatPercent(value, 1) : formatRatio(value, 2);
}

/**
 * A 30-day and a 90-day rolling window on one axis.
 *
 * Both series are the same measure in the same units, so sharing a scale is the honest choice —
 * this is explicitly not a dual-axis chart. Two series means a legend is mandatory.
 */
export function RollingChart({
  data,
  shortLabel,
  longLabel,
  valueFormat,
}: {
  data: RollingPoint[];
  shortLabel: string;
  longLabel: string;
  valueFormat: RollingValueFormat;
}) {
  return (
    <ChartShell
      height={220}
      legend={[
        { label: shortLabel, color: SERIES.one },
        { label: longLabel, color: SERIES.two },
      ]}
    >
      <ResponsiveContainer height="100%" width="100%">
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
          <CartesianGrid {...GRID_PROPS} />
          <XAxis {...AXIS_PROPS} dataKey="date" minTickGap={48} />
          <YAxis
            {...AXIS_PROPS}
            tickFormatter={(value) => formatValue(toNumber(value), valueFormat)}
            width={56}
          />
          <Tooltip
            {...TOOLTIP_STYLES}
            formatter={(value, name) => [formatValue(toNumber(value), valueFormat), name]}
          />
          <Line
            activeDot={{ r: MARK.markerRadius, strokeWidth: MARK.ringWidth, stroke: INK.surface }}
            connectNulls={false}
            dataKey="short"
            dot={false}
            name={shortLabel}
            stroke={SERIES.one}
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={MARK.lineWidth}
            type="monotone"
          />
          <Line
            activeDot={{ r: MARK.markerRadius, strokeWidth: MARK.ringWidth, stroke: INK.surface }}
            connectNulls={false}
            dataKey="long"
            dot={false}
            name={longLabel}
            stroke={SERIES.two}
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={MARK.lineWidth}
            type="monotone"
          />
        </LineChart>
      </ResponsiveContainer>
    </ChartShell>
  );
}
