"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatPercent } from "@/lib/format";
import type { ClusterAssignment } from "@/lib/types";
import { CATEGORICAL, MARK, OTHER } from "@/lib/viz";
import { AXIS_PROPS, ChartShell, GRID_PROPS, TOOLTIP_STYLES, toNumber } from "./chart-frame";

/**
 * Holdings grouped by correlation cluster, coloured by cluster identity (fixed order of first
 * appearance, folding any cluster past the palette's eight slots into neutral "Other"). Weight
 * sets bar length, so a cluster's concentration and its members are both visible at once.
 */
export function ClusterChart({ assignments }: { assignments: ClusterAssignment[] }) {
  const clusterIds = [...new Set(assignments.map((item) => item.cluster))].sort((a, b) => a - b);
  const colorByCluster = new Map(
    clusterIds.map((id, index) => [id, index < CATEGORICAL.length ? CATEGORICAL[index] : OTHER]),
  );
  const rows = [...assignments]
    .sort((a, b) => a.cluster - b.cluster || b.weight - a.weight)
    .map((item) => ({
      ...item,
      fill: colorByCluster.get(item.cluster) ?? OTHER,
    }));
  const height = Math.max(160, rows.length * 32 + 48);

  return (
    <ChartShell
      height={height}
      legend={clusterIds.map((id) => ({
        label: `Cluster ${id}`,
        color: colorByCluster.get(id) ?? OTHER,
        shape: "square",
      }))}
    >
      <ResponsiveContainer height="100%" width="100%">
        <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 24, bottom: 4, left: 4 }}>
          <CartesianGrid {...GRID_PROPS} horizontal={false} vertical />
          <XAxis {...AXIS_PROPS} tickFormatter={(value) => formatPercent(toNumber(value), 0)} type="number" />
          <YAxis {...AXIS_PROPS} dataKey="key" type="category" width={110} />
          <Tooltip
            {...TOOLTIP_STYLES}
            formatter={(value) => [formatPercent(toNumber(value), 1), "Weight"]}
          />
          <Bar
            barSize={MARK.maxBarThickness}
            dataKey="weight"
            fill={colorByCluster.get(clusterIds[0]) ?? OTHER}
            name="Weight"
            radius={MARK.barRadius}
          />
        </BarChart>
      </ResponsiveContainer>
    </ChartShell>
  );
}
