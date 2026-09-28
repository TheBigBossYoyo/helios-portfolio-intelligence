import Link from "next/link";

import { JournalTimeline, ThesisStepper } from "@/components/journal-timeline";
import { Note, PageHeader, Panel, Unavailable } from "@/components/panel";
import { Pager } from "@/components/pager";
import { StatusBadge } from "@/components/status-badge";
import {
  AddJournalEntryForm,
  CreateThesisForm,
  EditThesisForm,
  TransitionThesisForm,
} from "@/components/thesis-forms";
import {
  addJournalEntryAction,
  createThesisAction,
  editThesisAction,
  transitionThesisAction,
} from "@/lib/actions";
import { getJournal, getPositions, getTheses } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { paginate, parsePageParam } from "@/lib/pagination";
import type { Thesis } from "@/lib/types";

export const dynamic = "force-dynamic";

// `/api/v1/journal` only supports capping the result with `limit` (see src/helios/api.py) —
// there is no offset param. Fetch past what one page shows and slice the fetched list here; the
// thesis lists above stay unpaginated, since a single-user portfolio realistically holds a
// handful of theses at once.
const JOURNAL_FETCH_LIMIT = 100;
const JOURNAL_PAGE_SIZE = 10;

export default async function JournalPage({
  searchParams,
}: {
  searchParams: Promise<{ page?: string }>;
}) {
  const { page } = await searchParams;
  const [theses, journal, positions] = await Promise.all([
    getTheses(),
    getJournal(JOURNAL_FETCH_LIMIT),
    getPositions(),
  ]);

  const all = theses.ok ? theses.data : [];
  const open = all.filter((row) => row.status === "draft" || row.status === "active");
  const settled = all.filter((row) => row.status !== "draft" && row.status !== "active");
  const drafts = all.filter((row) => row.status === "draft");
  const heldTickers = positions.ok
    ? positions.data.map((position) => position.instrument.ticker)
    : [];
  const journalPage = journal.ok ? paginate(journal.data, parsePageParam(page), JOURNAL_PAGE_SIZE) : null;

  return (
    <>
      <PageHeader
        description="Why you hold what you hold, written before the outcome is known."
        title="Journal"
      />

      <Panel subtitle="Editable until activated; frozen from then on." title={`Open theses (${open.length})`}>
        {theses.ok ? (
          open.length > 0 ? (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
              {open.map((thesis) => (
                <ThesisCard key={thesis.id} thesis={thesis} />
              ))}
            </div>
          ) : (
            <Unavailable detail="Write your first one below." reason="No open theses yet" />
          )
        ) : (
          <Unavailable detail={theses.error} reason="Theses unavailable" />
        )}
        <div className="mt-4">
          <Note>
            A thesis is editable only while it is a draft. Once you activate it, the original
            reasoning is frozen — later thinking goes in the journal, and the outcome goes in the
            note you write when you close it. That is what makes reviewing them honest.
          </Note>
        </div>
      </Panel>

      {settled.length > 0 ? (
        <Panel
          subtitle="Closed positions on your own reasoning — the part worth re-reading."
          title={`Settled theses (${settled.length})`}
        >
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {settled.map((thesis) => (
              <ThesisCard key={thesis.id} thesis={thesis} />
            ))}
          </div>
        </Panel>
      ) : null}

      <Panel
        subtitle="Written now, read later. A thesis starts as a draft so you can still change your mind about the wording, not about what happened."
        title="Write a thesis"
      >
        <CreateThesisForm action={createThesisAction} tickers={heldTickers} />
      </Panel>

      {drafts.length > 0 ? (
        <Panel
          subtitle="Only drafts appear here. Once a thesis is active its reasoning is frozen, so there is nothing to edit."
          title="Edit a draft"
        >
          <EditThesisForm action={editThesisAction} drafts={drafts} />
        </Panel>
      ) : null}

      {all.length > 0 ? (
        <Panel
          subtitle="Move a thesis along its lifecycle. Settling one requires the note explaining how it turned out."
          title="Change a thesis status"
        >
          <TransitionThesisForm action={transitionThesisAction} theses={all} />
        </Panel>
      ) : null}

      <Panel subtitle="Dated notes, attached to a thesis or standalone." title="Journal">
        {journalPage ? (
          <>
            <JournalTimeline entries={journalPage.items} />
            <Pager basePath="/journal" page={journalPage.page} pageCount={journalPage.pageCount} />
          </>
        ) : (
          <Unavailable detail={!journal.ok ? journal.error : undefined} reason="Journal unavailable" />
        )}
        <div className="mt-6 border-t border-border pt-4">
          <AddJournalEntryForm action={addJournalEntryAction} theses={all} />
        </div>
      </Panel>
    </>
  );
}

/** One thesis: status, scope, conviction, opened date and its place in the lifecycle. */
function ThesisCard({ thesis }: { thesis: Thesis }) {
  return (
    <div className="flex flex-col gap-3 rounded-xl border border-border bg-surface-2 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <StatusBadge status={thesis.status} />
        <span className="truncate text-xs font-medium text-ink-3">
          {thesis.t212Ticker ?? "Portfolio-wide"}
        </span>
      </div>

      <Link
        className="text-sm font-semibold text-ink hover:text-accent hover:underline underline-offset-4"
        href={`/journal/${thesis.id}`}
      >
        {thesis.title}
      </Link>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-3">
        <span className="capitalize">{thesis.conviction} conviction</span>
        <span aria-hidden="true">·</span>
        <span>Opened {formatDate(thesis.openedOn)}</span>
      </div>

      {thesis.outcomeNote ? (
        <p className="line-clamp-2 text-xs leading-relaxed text-ink-3">{thesis.outcomeNote}</p>
      ) : null}

      <ThesisStepper status={thesis.status} />
    </div>
  );
}
