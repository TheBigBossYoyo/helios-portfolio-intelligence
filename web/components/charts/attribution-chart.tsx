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
import type { AttributionItem } from "@/lib/types";
import { CATEGORICAL, INK, MARK } from "@/lib/viz";
import { AXIS_PROPS, ChartShell, GRID_PROPS, TOOLTIP_STYLES, toNumber } from "./chart-frame";

/**
 * Brinson-Fachler effects per sector: allocation, selection and interaction grouped side by side
 * around a zero baseline. Colour here carries the *identity* of the effect (three fixed
 * categorical slots), not its sign — length and side of the baseline already carry that.
 */
export function AttributionChart({ data }: { data: AttributionItem[] }) {
  const height = Math.max(180, data.length * 44 + 56);

  return (
    <ChartShell
      height={height}
      legend={[
        { label: "Allocation", color: CATEGORICAL[0], shape: "square" },
        { label: "Selection", color: CATEGORICAL[1], shape: "square" },
        { label: "Interaction", color: CATEGORICAL[2], shape: "square" },
      ]}
    >
      <ResponsiveContainer height="100%" width="100%">
        <BarChart data={data} layout="vertical" margin={{ top: 4, right: 24, bottom: 4, left: 4 }}>
          <CartesianGrid {...GRID_PROPS} horizontal={false} vertical />
          <XAxis
            {...AXIS_PROPS}
            tickFormatter={(value) => formatSignedPercent(toNumber(value), 1)}
            type="number"
          />
          <YAxis {...AXIS_PROPS} dataKey="key" type="category" width={100} />
          <ReferenceLine stroke={INK.baseline} strokeWidth={1} x={0} />
          <Tooltip
            {...TOOLTIP_STYLES}
            formatter={(value, name) => [formatSignedPercent(toNumber(value), 3), name]}
          />
          <Bar
            barSize={10}
            dataKey="allocationEffect"
            fill={CATEGORICAL[0]}
            name="Allocation"
            radius={MARK.barRadius}
          />
          <Bar
            barSize={10}
            dataKey="selectionEffect"
            fill={CATEGORICAL[1]}
            name="Selection"
            radius={MARK.barRadius}
          />
          <Bar
            barSize={10}
            dataKey="interactionEffect"
            fill={CATEGORICAL[2]}
            name="Interaction"
            radius={MARK.barRadius}
          />
        </BarChart>
      </ResponsiveContainer>
    </ChartShell>
  );
}
