"use client";

import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatPercent } from "@/lib/format";
import { INK, MARK, SERIES } from "@/lib/viz";
import { AXIS_PROPS, ChartShell, GRID_PROPS, TOOLTIP_STYLES, toNumber } from "./chart-frame";

export interface DrawdownPoint {
  date: string;
  drawdown: number | null;
}

/**
 * Drawdown from the running peak. Single series, so no legend — the panel title names it.
 *
 * The hue is the *categorical* red slot, not the reserved `critical` status token: this is a
 * signed magnitude, not a state, and status colors must never impersonate a series.
 */
export function DrawdownChart({ data }: { data: DrawdownPoint[] }) {
  return (
    <ChartShell height={200}>
      <ResponsiveContainer height="100%" width="100%">
        <AreaChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
          <defs>
            <linearGradient id="drawdownFill" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor={SERIES.red} stopOpacity={0} />
              <stop offset="100%" stopColor={SERIES.red} stopOpacity={MARK.areaOpacity} />
            </linearGradient>
          </defs>
          <CartesianGrid {...GRID_PROPS} />
          <XAxis {...AXIS_PROPS} dataKey="date" minTickGap={48} />
          <YAxis
            {...AXIS_PROPS}
            tickFormatter={(value) => formatPercent(value, 0)}
            width={56}
          />
          <ReferenceLine stroke={INK.baseline} strokeWidth={1} y={0} />
          <Tooltip
            {...TOOLTIP_STYLES}
            formatter={(value) => [formatPercent(toNumber(value), 2), "Drawdown"]}
          />
          <Area
            activeDot={{ r: MARK.markerRadius, strokeWidth: MARK.ringWidth, stroke: INK.surface }}
            connectNulls={false}
            dataKey="drawdown"
            fill="url(#drawdownFill)"
            name="Drawdown"
            stroke={SERIES.red}
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={MARK.lineWidth}
            type="monotone"
          />
        </AreaChart>
      </ResponsiveContainer>
    </ChartShell>
  );
}
