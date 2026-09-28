"use client";

import {
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { INK, MARK, SERIES } from "@/lib/viz";
import { AXIS_PROPS, ChartShell, GRID_PROPS, TOOLTIP_STYLES, toNumber } from "./chart-frame";

export interface PriceRow {
  date: string;
  close: number | null;
  /** Price paid on a buy that day, else null. */
  bought: number | null;
  /** Price received on a sale that day, else null. */
  sold: number | null;
}

/** Round ticks (steps of 1, 2 or 5 x a power of ten) covering [low, high]. */
function niceTicks(low: number, high: number, count = 5): number[] {
  const raw = (high - low) / count || 1;
  const power = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((factor) => factor * power).find((value) => value >= raw) ?? raw;
  const ticks: number[] = [];
  for (let tick = Math.floor(low / step) * step; tick <= high + step / 2; tick += step) {
    ticks.push(Number(tick.toFixed(10)));
  }
  return ticks;
}

/** About `count` evenly spaced timestamps from the series, first and last included. */
function evenTicks(values: number[], count: number): number[] {
  if (values.length <= count) return values;
  const step = (values.length - 1) / (count - 1);
  return Array.from({ length: count }, (_, index) => values[Math.round(index * step)]);
}

function formatPrice(value: number | null, currency: string): string {
  if (value === null) return "—";
  const digits = Math.abs(value) >= 1000 ? 0 : 2;
  return `${value.toLocaleString("en-GB", { minimumFractionDigits: digits, maximumFractionDigits: digits })} ${currency}`;
}

/**
 * Daily closes in the instrument's own currency, with your trades marked on the line and your
 * average cost as a reference. Buys wear the "money in" colour used everywhere else and sales
 * the second categorical hue — a trade is a fact about you, not a gain or a loss.
 */
export function PriceChart({
  data,
  currency,
  averageCost,
  height = 320,
}: {
  data: PriceRow[];
  currency: string;
  averageCost?: number | null;
  height?: number;
}) {
  // Markers get their own point lists: given the full series, Recharts draws a marker for every
  // day with no trade too, pinned to the top of the axis.
  // Time on a numeric axis: the markers' own point lists then land on their dates instead of
  // being appended to a category axis.
  const rows = data.map((row) => ({ ...row, t: Date.parse(`${row.date}T00:00:00Z`) }));
  const buys = rows.filter((row) => row.bought !== null);
  const sells = rows.filter((row) => row.sold !== null);
  // Pad the price range so the average-cost line (and its label) never sits on the axis.
  const values = [
    ...rows.map((row) => row.close),
    ...buys.map((row) => row.bought),
    ...sells.map((row) => row.sold),
    averageCost ?? null,
  ].filter((value): value is number => value !== null && Number.isFinite(value));
  const low = Math.min(...values);
  const high = Math.max(...values);
  const pad = (high - low || Math.abs(high) || 1) * 0.08;
  const yTicks = niceTicks(low - pad, high + pad);
  const xTicks = evenTicks(rows.map((row) => row.t), 6);
  const hasBuys = buys.length > 0;
  const hasSells = sells.length > 0;
  const legend = [
    { label: `Price (${currency})`, color: SERIES.one },
    ...(hasBuys ? [{ label: "You bought", color: SERIES.three, shape: "square" as const }] : []),
    ...(hasSells ? [{ label: "You sold", color: SERIES.two, shape: "square" as const }] : []),
    ...(averageCost ? [{ label: "Your average cost", color: INK.muted }] : []),
  ];

  return (
    <ChartShell height={height} legend={legend.length > 1 ? legend : undefined}>
      <ResponsiveContainer height="100%" width="100%">
        <ComposedChart data={rows} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
          <CartesianGrid {...GRID_PROPS} />
          <XAxis
            {...AXIS_PROPS}
            dataKey="t"
            domain={["dataMin", "dataMax"]}
            scale="time"
            ticks={xTicks}
            tickFormatter={(value) => new Date(Number(value)).toISOString().slice(0, 10)}
            type="number"
          />
          <YAxis
            {...AXIS_PROPS}
            domain={[yTicks[0], yTicks[yTicks.length - 1]]}
            ticks={yTicks}
            tickFormatter={(value) => {
              // One number of decimals for the whole axis, set by its step.
              const step = yTicks.length > 1 ? yTicks[1] - yTicks[0] : 1;
              const digits = step >= 1 ? 0 : Math.min(4, Math.ceil(-Math.log10(step)));
              return (toNumber(value) ?? 0).toLocaleString("en-GB", {
                minimumFractionDigits: digits,
                maximumFractionDigits: digits,
              });
            }}
            width={64}
          />
          <Tooltip
            {...TOOLTIP_STYLES}
            formatter={(value, name) => [formatPrice(toNumber(value), currency), name]}
            labelFormatter={(value) => new Date(Number(value)).toISOString().slice(0, 10)}
          />
          {averageCost ? (
            <ReferenceLine
              ifOverflow="extendDomain"
              label={{
                value: `Your average ${formatPrice(averageCost, currency)}`,
                position: "insideTopRight",
                fill: INK.muted,
                fontSize: 11,
              }}
              stroke={INK.muted}
              y={averageCost}
            />
          ) : null}
          <Line
            activeDot={{ r: MARK.markerRadius, strokeWidth: MARK.ringWidth, stroke: INK.surface }}
            connectNulls
            dataKey="close"
            dot={false}
            isAnimationActive={false}
            name="Price"
            stroke={SERIES.one}
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={MARK.lineWidth}
            type="monotone"
          />
          {hasBuys ? (
            <Scatter
              data={buys}
              dataKey="bought"
              fill={SERIES.three}
              isAnimationActive={false}
              name="You bought"
              shape={(props: { cx?: number; cy?: number }) => (
                <circle cx={props.cx} cy={props.cy} fill={SERIES.three} r={6} stroke={INK.surface} strokeWidth={2} />
              )}
            />
          ) : null}
          {hasSells ? (
            <Scatter
              data={sells}
              dataKey="sold"
              fill={SERIES.two}
              isAnimationActive={false}
              name="You sold"
              shape={(props: { cx?: number; cy?: number }) => (
                <circle cx={props.cx} cy={props.cy} fill={SERIES.two} r={6} stroke={INK.surface} strokeWidth={2} />
              )}
            />
          ) : null}
        </ComposedChart>
      </ResponsiveContainer>
    </ChartShell>
  );
}
