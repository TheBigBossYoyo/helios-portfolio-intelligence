import { Sparkles } from "lucide-react";
import { ActionButton } from "@/components/action-button";
import { AiPanel } from "@/components/ai-panel";
import { Note, PageHeader, Panel, Unavailable } from "@/components/panel";
import { analyseWithAiAction } from "@/lib/actions";
import { getLatestAiAnalysis } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { EYEBROW } from "@/lib/ui";

export const dynamic = "force-dynamic";

export default async function InsightsPage() {
  const analysis = await getLatestAiAnalysis();

  return (
    <>
      <PageHeader
        actions={
          <div className="flex flex-col items-end gap-1.5">
            {analysis.ok ? (
              <span className={EYEBROW}>Last run {formatDateTime(analysis.data.asOf)}</span>
            ) : null}
            <AnalyseButton />
          </div>
        }
        description="Claude describing the analytics Helios already computed. Every observation cites the figure it rests on."
        title="AI analysis"
      />

      <Panel title="Latest analysis">
        {analysis.ok ? (
          <AiPanel analysis={analysis.data} />
        ) : (
          <Unavailable
            detail={
              analysis.status === 404
                ? "No analysis has been run yet. Run one with the button above."
                : analysis.error
            }
            reason="No analysis available"
          />
        )}
      </Panel>

      <Panel subtitle="What this feature will and will not do." title="How it works">
        <div className="flex flex-col gap-3">
          <Note>
            The model only ever sees figures Helios computed from your own Trading 212 history,
            plus headlines from the sources you configured. It cannot look anything up.
          </Note>
          <Note>
            The output schema has no field for a rating, a price target, or a buy/sell action —
            so there is nowhere for advice to go, even if it were asked for.
          </Note>
          <Note>
            Metrics your analytics reported as unavailable are passed through with that status
            attached, so the model states they are unknown rather than estimating them.
          </Note>
          <Note>
            Every run is stored in full — prompt and response — so any statement here can be
            traced back to the numbers behind it.
          </Note>
          <Note>
            Analysis runs only when you ask — the button above, <code>helios ai-analyse</code>, or
            the API with the local-action header. It is never on a schedule, because each run
            costs money.
          </Note>
        </div>
      </Panel>
    </>
  );
}

/**
 * Each run bills the Anthropic account, so it confirms first. This is the clearest case for
 * the deliberate second click: the cost is real, immediate, and invisible until the bill.
 */
function AnalyseButton() {
  return (
    <ActionButton
      action={analyseWithAiAction}
      confirmLabel="Confirm — this costs money"
      icon={<Sparkles aria-hidden="true" size={15} />}
      label="Run analysis"
      pendingLabel="Analysing…"
      variant="primary"
    />
  );
}
