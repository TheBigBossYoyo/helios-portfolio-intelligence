import { Unavailable } from "@/components/panel";
import { humanizeStatus } from "@/lib/format";
import type { AiAnalysis, AiObservation } from "@/lib/types";
import { STATUS } from "@/lib/viz";

const SEVERITY_COLOR: Record<string, string> = {
  info: STATUS.good,
  notable: STATUS.warning,
  elevated: STATUS.serious,
};

/**
 * Renders an AI analysis.
 *
 * Two things are non-negotiable here: the disclosure is always visible, and every observation
 * shows the figure it rests on. The evidence line is not decoration — it is how you check the
 * model against the data without leaving the page.
 */
export function AiPanel({ analysis }: { analysis: AiAnalysis }) {
  if (analysis.status !== "ok") {
    return (
      <Unavailable
        detail={analysis.detail ?? "Run `helios ai-analyse` to generate an analysis."}
        reason={humanizeStatus(analysis.status)}
      />
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <p className="border-l-2 border-amber-accent/40 pl-3 text-[11px] leading-relaxed text-neutral-500">
        {analysis.disclosure}
      </p>

      {analysis.summary ? (
        <p className="text-sm leading-relaxed text-neutral-200">{analysis.summary}</p>
      ) : null}

      <ol className="flex flex-col divide-y divide-neutral-900">
        {analysis.observations.map((item) => (
          <ObservationRow item={item} key={`${item.rank}-${item.headline}`} />
        ))}
      </ol>

      {analysis.unavailableMetrics.length > 0 ? (
        <p className="text-[11px] leading-relaxed text-neutral-600">
          Reported as unavailable in the source analytics:{" "}
          <span className="text-neutral-500">{analysis.unavailableMetrics.join(", ")}</span>
        </p>
      ) : null}

      <dl className="flex flex-wrap gap-x-6 gap-y-1 border-t border-neutral-900 pt-3 text-[10px] text-neutral-600">
        <div className="flex gap-1">
          <dt>Model</dt>
          <dd className="text-neutral-400">{analysis.servedByModel ?? analysis.model}</dd>
        </div>
        {analysis.effort ? (
          <div className="flex gap-1">
            <dt>Effort</dt>
            <dd className="text-neutral-400">{analysis.effort}</dd>
          </div>
        ) : null}
        {analysis.inputTokens !== null ? (
          <div className="flex gap-1">
            <dt>Tokens</dt>
            <dd className="tabular-nums text-neutral-400">
              {analysis.inputTokens} in / {analysis.outputTokens ?? 0} out
              {analysis.cacheReadTokens ? ` (${analysis.cacheReadTokens} cached)` : ""}
            </dd>
          </div>
        ) : null}
      </dl>
    </div>
  );
}

function ObservationRow({ item }: { item: AiObservation }) {
  return (
    <li className="py-3 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] uppercase tracking-wider text-neutral-600">
        <span
          className="inline-flex items-center gap-1.5"
          style={{ color: SEVERITY_COLOR[item.severity] ?? STATUS.good }}
        >
          <span aria-hidden="true">●</span>
          <span className="text-neutral-400">{item.severity}</span>
        </span>
        <span>{item.category.replace(/_/g, " ")}</span>
        {item.t212Ticker ? (
          <span className="border border-neutral-800 px-1.5 py-0.5 text-neutral-500">
            {item.t212Ticker}
          </span>
        ) : (
          <span className="text-neutral-700">portfolio-wide</span>
        )}
      </div>
      <h3 className="mt-1 text-sm leading-snug text-neutral-200">{item.headline}</h3>
      <p className="mt-1 text-[12px] leading-relaxed text-neutral-400">{item.detail}</p>
      {/* The citation is the audit trail: it names the figure the claim rests on. */}
      <p className="mt-1.5 font-mono text-[10px] text-neutral-600">
        <span className="text-neutral-700">evidence </span>
        {item.evidence}
      </p>
    </li>
  );
}
