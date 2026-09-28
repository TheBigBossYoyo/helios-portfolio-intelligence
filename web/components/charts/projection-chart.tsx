"use client";

import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatEur, formatEurCompact } from "@/lib/format";
import type { ProjectionPoint } from "@/lib/projection";
import { INK, MARK, SERIES } from "@/lib/viz";
import { AXIS_PROPS, ChartShell, GRID_PROPS, TOOLTIP_STYLES, toNumber } from "./chart-frame";

function yearLabel(month: number): string {
  if (month === 0) return "Now";
  return month % 12 === 0 ? `${month / 12}y` : `${(month / 12).toFixed(1)}y`;
}

/**
 * A fan: the shaded band holds 8 outcomes in 10 (10th to 90th percentile), the line through it
 * is the middle outcome, and the "money put in" line shows how much of any of it would be your
 * own contributions. One value axis, in EUR.
 */
export function ProjectionChart({ data }: { data: ProjectionPoint[] }) {
  const rows = data.map((point) => ({
    ...point,
    band: [point.low, point.high] as [number, number],
  }));
  const ticks = rows.filter((row) => row.month % 12 === 0).map((row) => row.month);

  return (
    <ChartShell
      height={320}
      legend={[
        { label: "Middle outcome", color: SERIES.one },
        { label: "8 in 10 outcomes", color: SERIES.one, shape: "square" },
        { label: "Money put in", color: SERIES.three },
      ]}
    >
      <ResponsiveContainer height="100%" width="100%">
        <ComposedChart data={rows} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
          <CartesianGrid {...GRID_PROPS} />
          <XAxis
            {...AXIS_PROPS}
            dataKey="month"
            domain={[0, "dataMax"]}
            tickFormatter={(value) => yearLabel(Number(value))}
            ticks={ticks}
            type="number"
          />
          <YAxis {...AXIS_PROPS} tickFormatter={(value) => formatEurCompact(value)} width={64} />
          <Tooltip
            {...TOOLTIP_STYLES}
            formatter={(value, name) => {
              if (Array.isArray(value)) {
                return [`${formatEur(toNumber(value[0]))} – ${formatEur(toNumber(value[1]))}`, name];
              }
              return [formatEur(toNumber(value)), name];
            }}
            labelFormatter={(value) => `In ${yearLabel(Number(value))}`}
          />
          <Area
            dataKey="band"
            fill={SERIES.one}
            fillOpacity={MARK.areaOpacity}
            isAnimationActive={false}
            name="8 in 10 outcomes"
            stroke="none"
            type="monotone"
          />
          <Line
            activeDot={{ r: MARK.markerRadius, strokeWidth: MARK.ringWidth, stroke: INK.surface }}
            dataKey="contributed"
            dot={false}
            isAnimationActive={false}
            name="Money put in"
            stroke={SERIES.three}
            strokeWidth={MARK.lineWidth}
            type="linear"
          />
          <Line
            activeDot={{ r: MARK.markerRadius, strokeWidth: MARK.ringWidth, stroke: INK.surface }}
            dataKey="median"
            dot={false}
            isAnimationActive={false}
            name="Middle outcome"
            stroke={SERIES.one}
            strokeWidth={MARK.lineWidth}
            type="monotone"
          />
        </ComposedChart>
      </ResponsiveContainer>
    </ChartShell>
  );
}
