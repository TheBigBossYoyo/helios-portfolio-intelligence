"use client";

import { Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { formatEur } from "@/lib/format";
import { CATEGORICAL, OTHER } from "@/lib/viz";
import { TOOLTIP_STYLES, toNumber } from "./chart-frame";

export interface AllocationSlice {
  label: string;
  sublabel?: string | null;
  value: number;
}

/** A donut is part-to-whole at a glance, so it never shows more than this many segments. */
const MAX_SEGMENTS = 5;

/**
 * Portfolio allocation by current value.
 *
 * The largest five holdings keep their own categorical colour (fixed order, by rank at render
 * time); the tail folds into a neutral "Other" so no sixth hue is ever generated. The legend
 * beside the ring doubles as the value list, so no figure is hover-only.
 */
export function AllocationDonut({ slices, total }: { slices: AllocationSlice[]; total: number }) {
  const sorted = [...slices].filter((slice) => slice.value > 0).sort((a, b) => b.value - a.value);
  const head = sorted.slice(0, MAX_SEGMENTS);
  const tail = sorted.slice(MAX_SEGMENTS);
  const tailValue = tail.reduce((sum, slice) => sum + slice.value, 0);
  const data = [
    ...head.map((slice, index) => ({ ...slice, color: CATEGORICAL[index], fill: CATEGORICAL[index] })),
    ...(tailValue > 0
      ? [
          {
            label: `Other (${tail.length})`,
            sublabel: null,
            value: tailValue,
            color: OTHER,
            fill: OTHER,
          },
        ]
      : []),
  ];
  const sum = data.reduce((acc, slice) => acc + slice.value, 0) || 1;

  return (
    <div className="flex flex-col items-center gap-6">
      <div className="relative h-52 w-52 shrink-0">
        <ResponsiveContainer height="100%" width="100%">
          <PieChart>
            <Pie
              cornerRadius={4}
              data={data}
              dataKey="value"
              innerRadius="68%"
              isAnimationActive={false}
              nameKey="label"
              outerRadius="100%"
              paddingAngle={data.length > 1 ? 2 : 0}
              stroke="var(--surface)"
              strokeWidth={2}
            />
            <Tooltip
              {...TOOLTIP_STYLES}
              formatter={(value, name) => {
                const amount = toNumber(value) ?? 0;
                return [`${formatEur(amount)} · ${((amount / sum) * 100).toFixed(1)}%`, name];
              }}
            />
          </PieChart>
        </ResponsiveContainer>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-xs font-medium text-ink-3">Holdings</span>
          <span className="text-lg font-semibold tracking-tight text-ink">{formatEur(total)}</span>
        </div>
      </div>

      <ul className="flex w-full min-w-0 flex-col gap-2.5">
        {data.map((slice) => (
          <li className="flex items-center gap-3 text-sm" key={slice.label}>
            <span
              aria-hidden="true"
              className="h-2.5 w-2.5 shrink-0 rounded-[3px]"
              style={{ background: slice.color }}
            />
            <span className="min-w-0 flex-1 truncate">
              <span className="font-medium text-ink">{slice.label}</span>
              {slice.sublabel ? (
                <span className="ml-1.5 text-ink-3">{slice.sublabel}</span>
              ) : null}
            </span>
            <span className="tabular text-ink-2">{((slice.value / sum) * 100).toFixed(1)}%</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
