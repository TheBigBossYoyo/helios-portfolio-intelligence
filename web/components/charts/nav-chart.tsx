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
import { INK, MARK, SERIES } from "@/lib/viz";
import { AXIS_PROPS, ChartShell, GRID_PROPS, TOOLTIP_STYLES, toNumber } from "./chart-frame";

export interface NavChartPoint {
  date: string;
  nav: number | null;
  passive: number | null;
  invested?: number | null;
}

/**
 * Portfolio NAV over time, optionally against the passive counterfactual.
 *
 * Days the replay refused to value arrive as `null` and stay null: recharts leaves a gap rather
 * than interpolating a level that was never observed. One y-axis only — the counterfactual is in
 * the same units (EUR), so it shares the scale honestly.
 */
export function NavChart({
  data,
  passiveLabel,
  showInvested = false,
  height,
}: {
  data: NavChartPoint[];
  passiveLabel?: string | null;
  /**
   * Draw "money put in" (net deposits to date) under the value line. The gap between the two
   * is what the investments made or lost -- deposits raise both lines, so they never look like
   * profit.
   */
  showInvested?: boolean;
  height?: number;
}) {
  const hasPassive = Boolean(passiveLabel) && data.some((point) => point.passive !== null);
  const hasInvested =
    showInvested && data.some((point) => point.invested !== null && point.invested !== undefined);
  const legendItems = [
    { label: "Portfolio value", color: SERIES.one },
    ...(hasInvested ? [{ label: "Money put in", color: SERIES.three }] : []),
    ...(hasPassive ? [{ label: passiveLabel ?? "Passive proxy", color: SERIES.two }] : []),
  ];
  const legend = legendItems.length > 1 ? legendItems : undefined;

  return (
    <ChartShell height={height} legend={legend}>
      <ResponsiveContainer height="100%" width="100%">
        <ComposedChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
          <defs>
            <linearGradient id="navFill" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor={SERIES.one} stopOpacity={MARK.areaOpacity} />
              <stop offset="100%" stopColor={SERIES.one} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid {...GRID_PROPS} />
          <XAxis {...AXIS_PROPS} dataKey="date" minTickGap={48} />
          {/*
            Fitted domain, not a zero baseline. Position encodes value on a line chart, so a
            zero-anchored axis on a four-figure NAV squeezes the whole series into a flat band
            and hides exactly what the chart is for. (A *bar* chart would be the opposite case:
            length encodes magnitude there, so its baseline must stay at zero.) The axis ticks
            are labelled in euro, so the range is never ambiguous.
          */}
          <YAxis
            {...AXIS_PROPS}
            domain={["auto", "auto"]}
            tickFormatter={(value) => formatEurCompact(value)}
            width={64}
          />
          <Tooltip
            {...TOOLTIP_STYLES}
            formatter={(value, name) => [formatEur(toNumber(value)), name]}
          />
          <Area
            baseValue="dataMin"
            connectNulls={false}
            dataKey="nav"
            fill="url(#navFill)"
            name="Portfolio value"
            stroke="none"
            type="monotone"
          />
          <Line
            activeDot={{ r: MARK.markerRadius, strokeWidth: MARK.ringWidth, stroke: INK.surface }}
            connectNulls={false}
            dataKey="nav"
            dot={false}
            name="Portfolio value"
            stroke={SERIES.one}
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={MARK.lineWidth}
            type="monotone"
          />
          {hasInvested ? (
            <Line
              activeDot={{ r: MARK.markerRadius, strokeWidth: MARK.ringWidth, stroke: INK.surface }}
              connectNulls={false}
              dataKey="invested"
              dot={false}
              name="Money put in"
              stroke={SERIES.three}
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={MARK.lineWidth}
              // A step, not a slope: money arrives on a day, it does not drift in.
              type="stepAfter"
            />
          ) : null}
          {hasPassive ? (
            <Line
              activeDot={{ r: MARK.markerRadius, strokeWidth: MARK.ringWidth, stroke: INK.surface }}
              connectNulls={false}
              dataKey="passive"
              dot={false}
              name={passiveLabel ?? "Passive proxy"}
              stroke={SERIES.two}
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={MARK.lineWidth}
              type="monotone"
            />
          ) : null}
        </ComposedChart>
      </ResponsiveContainer>
    </ChartShell>
  );
}
