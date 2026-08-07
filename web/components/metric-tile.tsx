import type { MetricValue } from "@/lib/types";
import { EMPTY, humanizeStatus, metricText } from "@/lib/format";

interface MetricTileProps {
  label: string;
  metric: MetricValue | null | undefined;
  render: (value: number) => string;
  hint?: string;
}

/**
 * Stat tile per the dataviz contract: label, value, and — when the backend could not compute the
 * metric — its status instead of a fabricated number.
 *
 * The value uses proportional figures on purpose. `tabular-nums` is reserved for columns that
 * align vertically (see the table component); on a standalone display number it reads loose.
 */
export function MetricTile({ label, metric, render, hint }: MetricTileProps) {
  const available = Boolean(metric && metric.status === "ok" && metric.value !== null);
  const value = metricText(metric, render);
  const status = metric?.status ?? "unavailable";

  return (
    <div className="panel-raised group flex flex-col justify-between border border-border p-3 transition-colors duration-200 hover:border-neutral-700">
      <div className="flex items-start justify-between gap-2">
        <span className="text-[10px] uppercase tracking-wider text-neutral-500">{label}</span>
        <span
          aria-hidden="true"
          className={`mt-1 h-1.5 w-1.5 shrink-0 rounded-full transition-shadow duration-300 ${
            available
              ? "bg-amber-accent shadow-[0_0_6px_rgba(255,176,0,0.55)]"
              : "bg-neutral-700"
          }`}
        />
      </div>
      <div
        className={`mt-3 font-sans text-2xl leading-none ${
          available ? "text-neutral-100" : "text-neutral-600"
        }`}
      >
        {value}
      </div>
      <div className="mt-2 text-[10px] leading-tight text-neutral-600">
        {available
          ? (hint ?? `${metric?.observations ?? 0} obs`)
          : humanizeStatus(status)}
      </div>
      {!available && metric?.detail ? (
        <p className="mt-1 text-[10px] leading-tight text-neutral-700">{metric.detail}</p>
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
    <div className="flex flex-col gap-1">
      <span className="text-[10px] uppercase tracking-widest text-neutral-500">{label}</span>
      <span className="bg-gradient-to-b from-white to-neutral-400 bg-clip-text font-sans text-5xl leading-none text-transparent">
        {value || EMPTY}
      </span>
      {caption ? <span className="text-[11px] text-neutral-500">{caption}</span> : null}
    </div>
  );
}
