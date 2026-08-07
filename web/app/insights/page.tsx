import { AiPanel } from "@/components/ai-panel";
import { Note, Panel, Unavailable } from "@/components/panel";
import { getLatestAiAnalysis } from "@/lib/api";
import { formatDateTime } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function InsightsPage() {
  const analysis = await getLatestAiAnalysis();

  return (
    <>
      <Panel
        actions={
          analysis.ok ? (
            <span className="text-[10px] uppercase tracking-wider text-neutral-600">
              {formatDateTime(analysis.data.asOf)}
            </span>
          ) : null
        }
        subtitle="Claude describing the analytics Helios already computed. Every observation cites the figure it rests on."
        title="AI analysis"
      >
        {analysis.ok ? (
          <AiPanel analysis={analysis.data} />
        ) : (
          <Unavailable
            detail={
              analysis.status === 404
                ? "No analysis has been run yet. Run `helios ai-analyse` to generate one."
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
            Analysis runs only when you ask (<code>helios ai-analyse</code>, or the API with the
            local-action header). It is never on a schedule, because each run costs money.
          </Note>
        </div>
      </Panel>
    </>
  );
}
