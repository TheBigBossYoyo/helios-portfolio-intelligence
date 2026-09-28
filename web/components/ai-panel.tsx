import { Sparkles } from "lucide-react";
import { Note, Unavailable } from "@/components/panel";
import { StatusBadge } from "@/components/status-badge";
import { humanizeStatus } from "@/lib/format";
import type { AiAnalysis, AiObservation } from "@/lib/types";

/** Severity is the model's own vocabulary; map it onto the shared status tones so a badge reads
 * consistently with the rest of the app instead of inventing a second colour system. */
const SEVERITY_STATUS: Record<string, string> = {
  info: "ok",
  notable: "warning",
  elevated: "serious",
};

function severityStatus(severity: string): string {
  return SEVERITY_STATUS[severity] ?? severity;
}

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
      <Note>{analysis.disclosure}</Note>

      {analysis.summary ? (
        <div className="flex gap-3 rounded-xl bg-accent-soft px-4 py-3.5">
          <Sparkles aria-hidden="true" className="mt-0.5 shrink-0 text-accent" size={17} />
          <p className="text-sm leading-relaxed text-ink">{analysis.summary}</p>
        </div>
      ) : null}

      <ol className="flex flex-col gap-3">
        {analysis.observations.map((item) => (
          <ObservationCard item={item} key={`${item.rank}-${item.headline}`} />
        ))}
      </ol>

      {analysis.unavailableMetrics.length > 0 ? (
        <p className="text-xs leading-relaxed text-ink-3">
          Reported as unavailable in the source analytics:{" "}
          <span className="text-ink-2">{analysis.unavailableMetrics.join(", ")}</span>
        </p>
      ) : null}

      <dl className="flex flex-wrap gap-x-6 gap-y-1.5 border-t border-border pt-3.5 text-xs text-ink-3">
        <div className="flex gap-1.5">
          <dt>Model</dt>
          <dd className="font-medium text-ink-2">{analysis.servedByModel ?? analysis.model}</dd>
        </div>
        {analysis.effort ? (
          <div className="flex gap-1.5">
            <dt>Effort</dt>
            <dd className="font-medium text-ink-2">{analysis.effort}</dd>
          </div>
        ) : null}
        {analysis.inputTokens !== null ? (
          <div className="flex gap-1.5">
            <dt>Tokens</dt>
            <dd className="tabular font-medium text-ink-2">
              {analysis.inputTokens} in / {analysis.outputTokens ?? 0} out
              {analysis.cacheReadTokens ? ` (${analysis.cacheReadTokens} cached)` : ""}
            </dd>
          </div>
        ) : null}
      </dl>
    </div>
  );
}

function ObservationCard({ item }: { item: AiObservation }) {
  return (
    <li className="rounded-xl border border-border bg-surface p-4 shadow-card">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5">
        <StatusBadge label={capitalise(item.severity)} status={severityStatus(item.severity)} />
        <span className="rounded-full bg-surface-3 px-2 py-0.5 text-xs font-medium text-ink-2">
          {item.category.replace(/_/g, " ")}
        </span>
        {item.t212Ticker ? (
          <span className="rounded-full bg-accent-soft px-2 py-0.5 text-xs font-medium text-accent-ink">
            {item.t212Ticker}
          </span>
        ) : (
          <span className="text-xs text-ink-4">Portfolio-wide</span>
        )}
      </div>
      <h3 className="mt-2 text-sm font-semibold leading-snug text-ink">{item.headline}</h3>
      <p className="mt-1 text-sm leading-relaxed text-ink-2">{item.detail}</p>
      {/* The citation is the audit trail: it names the figure the claim rests on. */}
      <p className="mt-2 rounded-lg bg-surface-2 px-2.5 py-1.5 font-mono text-xs text-ink-3">
        <span className="text-ink-4">evidence </span>
        {item.evidence}
      </p>
    </li>
  );
}

function capitalise(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}
