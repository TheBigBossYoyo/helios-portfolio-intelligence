"use client";

import { useRouter } from "next/navigation";
import {
  Bar,
  BarChart,
  type BarShapeProps,
  CartesianGrid,
  LabelList,
  Rectangle,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatEur, formatEurCompact } from "@/lib/format";
import { MARK, SERIES } from "@/lib/viz";
import { AXIS_PROPS, BAR_CURSOR, ChartShell, GRID_PROPS, TOOLTIP_STYLES, toNumber } from "./chart-frame";

export interface SpendingBar {
  key?: string;
  label: string;
  spent: number;
  count?: number;
}

/** Above this many bars, values move to the tooltip and the table twin instead of the bars. */
const LABELLED_BARS = 16;

/**
 * Card spending per day, week or month. One series, in the "money moved" colour the Overview
 * uses for deposits and withdrawals: spending leaves the account, it is not an investment loss.
 *
 * With `hrefPrefix`, each bar is a link: clicking it opens that day/week/month below the chart
 * (a plain URL, so the choice is bookmarkable). The selected bar stays full-strength and the
 * others step back, so the selection reads without a second colour.
 */
export function SpendingChart({
  data,
  selectedKey,
  hrefPrefix,
  height = 260,
}: {
  data: SpendingBar[];
  selectedKey?: string | null;
  hrefPrefix?: string;
  height?: number;
}) {
  const router = useRouter();
  const labelled = data.length <= LABELLED_BARS;
  const open = (index: number) => {
    const key = data[index]?.key;
    if (hrefPrefix && key) router.push(`${hrefPrefix}${encodeURIComponent(key)}`, { scroll: false });
  };

  return (
    <ChartShell height={height}>
      <ResponsiveContainer height="100%" width="100%">
        <BarChart
          data={data}
          margin={{ top: labelled ? 22 : 8, right: 12, bottom: 4, left: 4 }}
        >
          <CartesianGrid {...GRID_PROPS} />
          <XAxis {...AXIS_PROPS} dataKey="label" interval="preserveStartEnd" minTickGap={16} />
          <YAxis {...AXIS_PROPS} tickFormatter={(value) => formatEurCompact(value)} width={60} />
          <Tooltip
            {...TOOLTIP_STYLES}
            cursor={BAR_CURSOR}
            formatter={(value, _name, item) => {
              const count = (item?.payload as SpendingBar | undefined)?.count;
              return [
                `${formatEur(toNumber(value))}${count !== undefined ? ` · ${count} payment(s)` : ""}`,
                "Spent",
              ];
            }}
          />
          <Bar
            cursor={hrefPrefix ? "pointer" : undefined}
            dataKey="spent"
            fill={SERIES.three}
            maxBarSize={MARK.maxBarThickness}
            name="Spent"
            onClick={(_entry, index) => open(index)}
            radius={MARK.barRadius}
            // The selected bar stays full-strength; the others step back.
            shape={(props: BarShapeProps) => {
              const key = (props.payload as SpendingBar | undefined)?.key;
              return (
                <Rectangle
                  {...props}
                  fill={SERIES.three}
                  fillOpacity={selectedKey && key !== selectedKey ? 0.4 : 1}
                />
              );
            }}
          >
            {labelled ? (
              <LabelList
                dataKey="spent"
                fill="var(--ink-3)"
                fontSize={11}
                formatter={(value: unknown) => {
                  const number = toNumber(value);
                  return number ? formatEurCompact(number) : "";
                }}
                position="top"
              />
            ) : null}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartShell>
  );
}
