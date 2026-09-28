import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { notFound } from "next/navigation";

import { JournalTimeline, ThesisStepper } from "@/components/journal-timeline";
import { Note, Panel, Unavailable } from "@/components/panel";
import { StatusBadge } from "@/components/status-badge";
import {
  AddJournalEntryForm,
  EditThesisForm,
  TransitionThesisForm,
} from "@/components/thesis-forms";
import { addJournalEntryAction, editThesisAction, transitionThesisAction } from "@/lib/actions";
import { getThesis } from "@/lib/api";
import { EMPTY, formatDate, formatDateTime, formatPercent } from "@/lib/format";

export const dynamic = "force-dynamic";

/**
 * A single thesis, read in full: the frozen (or still-editable) reasoning, where it sits in the
 * draft → active → validated|invalidated → closed lifecycle, what Helios could link to it
 * automatically, and every dated journal entry attached to it.
 *
 * `/journal/[id]` rather than a separate `/theses` tree: the journal page already treats a
 * thesis as journal content (its open/settled tables live there), so this is a detail view one
 * level under the list that already links to it, not a new top-level section.
 */
export default async function ThesisDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  // A non-numeric id can never resolve to a thesis — same outcome as the backend's 404, so this
  // skips the round trip rather than asking the API to say so.
  if (!/^\d+$/.test(id)) {
    notFound();
  }
  const thesisId = Number(id);

  const result = await getThesis(thesisId);
  if (!result.ok) {
    if (result.status === 404) {
      notFound();
    }
    return (
      <Panel subtitle="Details for a single thesis." title={`Thesis #${thesisId}`}>
        <Unavailable detail={result.error} reason="Thesis unavailable" />
      </Panel>
    );
  }

  const { thesis, context, allowedTransitions, editable, journal } = result.data;
  const isSettled = thesis.status !== "draft" && thesis.status !== "active";
  const hasEnrichment =
    context.weight !== null ||
    context.contribution !== null ||
    context.newsCount > 0 ||
    context.latestNewsHeadline !== null;

  return (
    <>
      <div>
        <Link
          className="inline-flex items-center gap-1.5 text-sm font-medium text-ink-3 transition-colors hover:text-ink"
          href="/journal"
        >
          <ArrowLeft aria-hidden="true" size={15} />
          Back to journal
        </Link>
      </div>

      <Panel
        actions={<StatusBadge status={thesis.status} />}
        subtitle={thesis.t212Ticker ?? "Portfolio-wide"}
        title={thesis.title}
      >
        <ThesisStepper status={thesis.status} />

        <dl className="mt-5 grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <Field label="Conviction" value={thesis.conviction} />
          <Field label="Instrument" value={thesis.t212Ticker ?? "Portfolio-wide"} />
          <Field label="ISIN" value={thesis.isin ?? EMPTY} />
          <Field label="Opened" value={formatDate(thesis.openedOn)} />
        </dl>

        <div className="mt-4">
          <Note>
            Status lifecycle: draft → active → validated | invalidated → closed. From{" "}
            <strong>{thesis.status}</strong>, allowed next:{" "}
            {allowedTransitions.length > 0
              ? allowedTransitions.join(", ")
              : "nothing — it is closed"}
            .
          </Note>
        </div>

        <div className="mt-4 flex flex-col gap-2 border-t border-border pt-4">
          <h3 className="text-sm font-semibold text-ink">
            {editable ? "Reasoning (draft — still editable)" : "Original reasoning (frozen)"}
          </h3>
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-ink-2">{thesis.body}</p>
          {!editable ? (
            <Note>
              This thesis left draft, so the reasoning above is frozen exactly as it was
              written. Later thinking goes in the journal entries below, and the outcome goes in
              the note recorded when the thesis closed.
            </Note>
          ) : null}
        </div>

        {isSettled ? (
          <div className="mt-4 flex flex-col gap-2 border-t border-border pt-4">
            <h3 className="text-sm font-semibold text-ink">Outcome</h3>
            <p className="text-sm leading-relaxed text-ink-2">{thesis.outcomeNote ?? EMPTY}</p>
            {thesis.closedAt ? (
              <p className="text-xs text-ink-3">Closed {formatDateTime(thesis.closedAt)}</p>
            ) : null}
          </div>
        ) : null}
      </Panel>

      <Panel
        subtitle="What Helios could link to this instrument automatically — never written by the thesis itself."
        title="Enrichment"
      >
        {hasEnrichment ? (
          <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <Field
              label="Portfolio weight"
              value={context.weight === null ? "None yet" : formatPercent(context.weight)}
            />
            <Field
              label="Contribution"
              value={
                context.contribution === null ? "None yet" : formatPercent(context.contribution)
              }
            />
            <Field
              label="Recent news"
              value={context.newsCount > 0 ? `${context.newsCount} item(s)` : "None yet"}
            />
            <Field label="Latest headline" value={context.latestNewsHeadline ?? "None yet"} />
          </dl>
        ) : (
          <p className="px-1 py-4 text-center text-sm text-ink-3">
            None yet — no live weight, contribution or news is linked to this thesis.
          </p>
        )}
      </Panel>

      {editable ? (
        <Panel
          subtitle="Only offered while this thesis is a draft — once active, the reasoning above is frozen."
          title="Edit this draft"
        >
          <EditThesisForm action={editThesisAction} drafts={[thesis]} />
        </Panel>
      ) : null}

      {allowedTransitions.length > 0 ? (
        <Panel
          subtitle="Move this thesis along its lifecycle. Settling it requires the note explaining how it turned out."
          title="Change status"
        >
          <TransitionThesisForm action={transitionThesisAction} theses={[thesis]} />
        </Panel>
      ) : null}

      <Panel subtitle="Every dated note attached to this thesis." title={`Journal entries (${journal.length})`}>
        <JournalTimeline entries={journal} />
        <div className="mt-6 border-t border-border pt-4">
          <AddJournalEntryForm
            action={addJournalEntryAction}
            defaultThesisId={String(thesis.id)}
            theses={[thesis]}
          />
        </div>
      </Panel>
    </>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-1">
      <dt className="text-xs font-medium text-ink-3">{label}</dt>
      <dd className="text-sm font-medium text-ink">{value}</dd>
    </div>
  );
}
