"use client";

import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatRatio } from "@/lib/format";
import { INK, MARK, SERIES } from "@/lib/viz";
import { AXIS_PROPS, ChartShell, GRID_PROPS, TOOLTIP_STYLES, toNumber } from "./chart-frame";
import type { GrowthRow } from "@/lib/series";

/**
 * "Growth of 100": the portfolio and a benchmark, each indexed to its own first observed value.
 * One axis, one unit (index points) — the honest way to compare a EUR NAV against an ETF proxy
 * price without a second, misleading scale.
 */
export function GrowthChart({
  data,
  benchmarkLabel,
}: {
  data: GrowthRow[];
  benchmarkLabel?: string | null;
}) {
  const hasBenchmark = Boolean(benchmarkLabel) && data.some((point) => point.benchmark !== null);
  const legend = hasBenchmark
    ? [
        { label: "Portfolio", color: SERIES.one },
        { label: benchmarkLabel ?? "Benchmark", color: SERIES.two },
      ]
    : undefined;

  return (
    <ChartShell height={240} legend={legend}>
      <ResponsiveContainer height="100%" width="100%">
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
          <CartesianGrid {...GRID_PROPS} />
          <XAxis {...AXIS_PROPS} dataKey="date" minTickGap={48} />
          <YAxis
            {...AXIS_PROPS}
            domain={["auto", "auto"]}
            tickFormatter={(value) => formatRatio(toNumber(value), 0)}
            width={48}
          />
          {/* The common starting point of both paths — a real threshold, so it earns the dash.
              The panel subtitle already says "indexed to 100"; no inline label here to collide
              with the x-axis ticks that sit right where a start-of-series baseline usually is. */}
          <ReferenceLine stroke={INK.baseline} strokeDasharray="4 4" strokeWidth={1} y={100} />
          <Tooltip
            {...TOOLTIP_STYLES}
            formatter={(value, name) => [formatRatio(toNumber(value), 1), name]}
          />
          <Line
            activeDot={{ r: MARK.markerRadius, strokeWidth: MARK.ringWidth, stroke: INK.surface }}
            connectNulls={false}
            dataKey="portfolio"
            dot={false}
            name="Portfolio"
            stroke={SERIES.one}
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={MARK.lineWidth}
            type="monotone"
          />
          {hasBenchmark ? (
            <Line
              activeDot={{ r: MARK.markerRadius, strokeWidth: MARK.ringWidth, stroke: INK.surface }}
              connectNulls={false}
              dataKey="benchmark"
              dot={false}
              name={benchmarkLabel ?? "Benchmark"}
              stroke={SERIES.two}
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={MARK.lineWidth}
              type="monotone"
            />
          ) : null}
        </LineChart>
      </ResponsiveContainer>
    </ChartShell>
  );
}
