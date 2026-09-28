import { ArrowDownRight, ArrowUpRight } from "lucide-react";
import type { MetricValue } from "@/lib/types";
import { EMPTY, humanizeStatus, metricText } from "@/lib/format";
import { CARD } from "@/lib/ui";
import { Sparkline } from "./charts/sparkline";

interface MetricTileProps {
  label: string;
  metric: MetricValue | null | undefined;
  render: (value: number) => string;
  hint?: string;
  /** Returns and deltas: colour the value by sign and add an arrow (never colour alone). */
  signed?: boolean;
  /** Optional trailing series drawn as a sparkline under the value. */
  trend?: (number | null)[];
}

/**
 * Stat tile per the dataviz contract: label, value, and — when the backend could not compute the
 * metric — its status instead of a fabricated number.
 *
 * The value uses proportional figures on purpose. `tabular-nums` is reserved for columns that
 * align vertically (see the table component); on a standalone display number it reads loose.
 */
export function MetricTile({ label, metric, render, hint, signed, trend }: MetricTileProps) {
  const available = Boolean(metric && metric.status === "ok" && metric.value !== null);
  const value = metricText(metric, render);
  const status = metric?.status ?? "unavailable";
  const numeric = available && metric?.value !== null && metric?.value !== undefined ? metric.value : null;
  const direction = signed && numeric !== null ? Math.sign(numeric) : 0;

  return (
    <div className={`${CARD} theme-fade flex min-w-0 flex-col p-4`}>
      <span className="text-sm font-medium text-ink-3">{label}</span>
      <div className="mt-2 flex items-center gap-1.5">
        {direction !== 0 ? (
          <span
            aria-hidden="true"
            className={`flex h-6 w-6 items-center justify-center rounded-full ${
              direction > 0 ? "bg-positive-soft text-positive" : "bg-negative-soft text-negative"
            }`}
          >
            {direction > 0 ? <ArrowUpRight size={15} /> : <ArrowDownRight size={15} />}
          </span>
        ) : null}
        <span
          className={`text-2xl font-semibold leading-none tracking-tight ${
            !available
              ? "text-ink-4"
              : direction > 0
                ? "text-positive"
                : direction < 0
                  ? "text-negative"
                  : "text-ink"
          }`}
        >
          {value}
        </span>
      </div>
      {available && trend && trend.filter((point) => point !== null).length > 1 ? (
        <div className="mt-3">
          <Sparkline values={trend} />
        </div>
      ) : null}
      <div className="mt-auto pt-2 text-xs leading-snug text-ink-3">
        {available ? (
          (hint ?? `${metric?.observations ?? 0} observations`)
        ) : (
          <span className="inline-flex items-center gap-1.5">
            <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-ink-4" />
            {humanizeStatus(status)}
          </span>
        )}
      </div>
      {!available && metric?.detail ? (
        <p className="mt-1 text-xs leading-snug text-ink-4">{metric.detail}</p>
      ) : null}
    </div>
  );
}

/** The one hero figure a view is allowed to lead with. */
export function HeroFigure({
  label,
  value,
  caption,
}: {
  label: string;
  value: string;
  caption?: string;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-sm font-medium text-ink-3">{label}</span>
      <span className="text-4xl font-semibold leading-none tracking-tight text-ink sm:text-5xl">
        {value || EMPTY}
      </span>
      {caption ? <span className="text-sm text-ink-3">{caption}</span> : null}
    </div>
  );
}
