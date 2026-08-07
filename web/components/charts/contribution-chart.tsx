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
import { formatSignedPercent } from "@/lib/format";
import { DIVERGING, INK, MARK } from "@/lib/viz";
import { AXIS_PROPS, ChartShell, GRID_PROPS, TOOLTIP_STYLES, toNumber } from "./chart-frame";

export interface ContributionBar {
  key: string;
  contribution: number;
}

/**
 * Weight x return per holding, as a diverging bar chart around a zero baseline.
 *
 * Color encodes *sign* (the diverging poles), not magnitude — bar length already carries
 * magnitude, and re-encoding it as hue would burn the only free channel. Sign is never carried by
 * color alone: the baseline shows which side a bar falls on, and the table view states it.
 *
 * The two signs are two `Bar` series sharing a stack, rather than per-cell fills, so each side
 * keeps a stable identity.
 */
export function ContributionChart({ data }: { data: ContributionBar[] }) {
  const height = Math.max(160, data.length * 32 + 48);
  const rows = data.map((row) => ({
    key: row.key,
    positive: row.contribution >= 0 ? row.contribution : null,
    negative: row.contribution < 0 ? row.contribution : null,
  }));

  return (
    <ChartShell
      height={height}
      legend={[
        { label: "Positive contribution", color: DIVERGING.positive },
        { label: "Negative contribution", color: DIVERGING.negative },
      ]}
    >
      <ResponsiveContainer height="100%" width="100%">
        <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 24, bottom: 4, left: 4 }}>
          <CartesianGrid {...GRID_PROPS} horizontal={false} vertical />
          <XAxis
            {...AXIS_PROPS}
            tickFormatter={(value) => formatSignedPercent(toNumber(value), 1)}
            type="number"
          />
          <YAxis {...AXIS_PROPS} dataKey="key" type="category" width={110} />
          <ReferenceLine stroke={INK.baseline} strokeWidth={1} x={0} />
          <Tooltip
            {...TOOLTIP_STYLES}
            cursor={{ fill: "rgba(255,255,255,0.04)" }}
            formatter={(value, name) => [formatSignedPercent(toNumber(value), 3), name]}
          />
          <Bar
            barSize={MARK.maxBarThickness}
            dataKey="positive"
            fill={DIVERGING.positive}
            name="Positive contribution"
            radius={MARK.barRadius}
            stackId="contribution"
          />
          <Bar
            barSize={MARK.maxBarThickness}
            dataKey="negative"
            fill={DIVERGING.negative}
            name="Negative contribution"
            radius={MARK.barRadius}
            stackId="contribution"
          />
        </BarChart>
      </ResponsiveContainer>
    </ChartShell>
  );
}
