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
import { formatPercent, formatRatio } from "@/lib/format";
import { DIVERGING, MARK } from "@/lib/viz";
import { AXIS_PROPS, ChartShell, GRID_PROPS, TOOLTIP_STYLES, toNumber } from "./chart-frame";

interface Bin {
  label: string;
  mid: number;
  gain: number | null;
  loss: number | null;
}

const BIN_COUNT = 18;

/** Bins daily returns into equal-width buckets, keeping the sign in the bucket's own field. */
function toBins(values: number[]): Bin[] {
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const width = span / BIN_COUNT;
  const counts = new Array(BIN_COUNT).fill(0);
  for (const value of values) {
    const index = Math.min(BIN_COUNT - 1, Math.max(0, Math.floor((value - min) / width)));
    counts[index] += 1;
  }
  return counts.map((count, index) => {
    const mid = min + width * (index + 0.5);
    return {
      label: formatPercent(mid, 1),
      mid,
      gain: mid >= 0 ? count : null,
      loss: mid < 0 ? count : null,
    };
  });
}

/** The bin label closest to a threshold value — where a `ReferenceLine` anchors on a category axis. */
function nearestLabel(bins: Bin[], value: number): string {
  let best = bins[0];
  let bestDistance = Math.abs(bins[0].mid - value);
  for (const bin of bins) {
    const distance = Math.abs(bin.mid - value);
    if (distance < bestDistance) {
      best = bin;
      bestDistance = distance;
    }
  }
  return best.label;
}

/**
 * Distribution of daily time-weighted returns, with the historical-simulation VaR thresholds
 * marked. The bars split by sign into the same diverging pair the contribution chart uses — the
 * x-axis already carries the sign, so this doubles the read rather than replacing it.
 */
export function ReturnHistogram({
  returns,
  var95,
  var99,
}: {
  returns: number[];
  var95: number | null;
  var99: number | null;
}) {
  const bins = toBins(returns);
  const markers = [
    var95 !== null ? { value: var95, label: "VaR 95%", color: "var(--warning)" } : null,
    var99 !== null ? { value: var99, label: "VaR 99%", color: "var(--negative)" } : null,
  ].filter((marker): marker is { value: number; label: string; color: string } => marker !== null);

  return (
    <ChartShell
      height={220}
      legend={[
        { label: "Gain days", color: DIVERGING.positive, shape: "square" },
        { label: "Loss days", color: DIVERGING.negative, shape: "square" },
      ]}
    >
      <ResponsiveContainer height="100%" width="100%">
        <BarChart data={bins} margin={{ top: 22, right: 12, bottom: 4, left: 4 }}>
          <CartesianGrid {...GRID_PROPS} />
          <XAxis
            {...AXIS_PROPS}
            dataKey="label"
            interval={Math.ceil(BIN_COUNT / 7) - 1}
            minTickGap={12}
          />
          <YAxis {...AXIS_PROPS} allowDecimals={false} tickFormatter={(v) => formatRatio(v, 0)} width={32} />
          <Tooltip
            {...TOOLTIP_STYLES}
            formatter={(value, name) => [`${toNumber(value) ?? 0} day(s)`, name]}
          />
          {markers.map((marker) => (
            <ReferenceLine
              key={marker.label}
              label={{ value: marker.label, position: "top", fill: marker.color, fontSize: 11 }}
              stroke={marker.color}
              strokeDasharray="4 4"
              strokeWidth={1.5}
              x={nearestLabel(bins, marker.value)}
            />
          ))}
          <Bar dataKey="gain" fill={DIVERGING.positive} name="Gain days" radius={MARK.barRadius} />
          <Bar dataKey="loss" fill={DIVERGING.negative} name="Loss days" radius={MARK.barRadius} />
        </BarChart>
      </ResponsiveContainer>
    </ChartShell>
  );
}
