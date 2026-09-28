import Link from "next/link";

import { formatDateTime } from "@/lib/format";
import { LINK } from "@/lib/ui";
import type { JournalEntry } from "@/lib/types";

/**
 * Shared read-only pieces for the journal feature (`/journal` and `/journal/[id]`): the thesis
 * lifecycle stepper and the journal-entry timeline. Split out of the page files themselves so
 * neither page has to import the other's module, and kept server-renderable (no "use client")
 * since nothing here holds state.
 */

/** The thesis lifecycle, collapsed to four visual slots. */
const STEP_INDEX: Record<string, number> = {
  draft: 0,
  active: 1,
  validated: 2,
  invalidated: 2,
  closed: 3,
};

/**
 * A small, four-slot stepper for draft → active → validated|invalidated → closed.
 *
 * `validated`/`invalidated` share a slot because they are alternative outcomes of the same step,
 * not two separate steps — the label and tone of that slot switch to whichever one the thesis
 * actually reached. A thesis that closed directly from `active` (a legal transition) still shows
 * that slot as passed; the exact allowed-transitions note beside it is the source of truth, this
 * is the at-a-glance version.
 */
export function ThesisStepper({ status }: { status: string }) {
  const current = STEP_INDEX[status] ?? 0;
  const branchLabel =
    status === "validated" ? "Validated" : status === "invalidated" ? "Invalidated" : "Validated/Invalidated";
  const isInvalidated = status === "invalidated";
  const labels = ["Draft", "Active", branchLabel, "Closed"];

  return (
    <ol aria-label="Thesis lifecycle" className="flex flex-wrap items-center gap-x-1 gap-y-1.5">
      {labels.map((label, index) => {
        const isCurrent = index === current;
        const isDone = index < current;
        const tone = isCurrent
          ? isInvalidated && index === 2
            ? "bg-negative-soft text-negative"
            : "bg-accent-soft text-accent-ink"
          : isDone
            ? "bg-positive-soft text-positive"
            : "bg-surface-3 text-ink-4";
        return (
          <li className="flex items-center gap-1" key={label}>
            <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${tone}`}>{label}</span>
            {index < labels.length - 1 ? (
              <span aria-hidden="true" className="h-px w-2.5 bg-border-strong" />
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}

/**
 * Dated journal entries, newest first, drawn as a timeline: a connecting rail with one dot per
 * entry rather than a table row. The scope (a thesis link, or "general") and any tags ride
 * alongside the timestamp, above the note itself.
 */
export function JournalTimeline({ entries }: { entries: JournalEntry[] }) {
  if (entries.length === 0) {
    return (
      <p className="px-1 py-8 text-center text-sm text-ink-3">
        No journal entries yet — add the first one below.
      </p>
    );
  }

  return (
    <ol className="flex max-h-[480px] flex-col overflow-y-auto pr-1">
      {entries.map((entry, index) => (
        <li className="relative flex gap-3.5 pb-5 last:pb-0" key={entry.id}>
          {index < entries.length - 1 ? (
            <span aria-hidden="true" className="absolute left-[5px] top-4 bottom-0 w-px bg-border" />
          ) : null}
          <span
            aria-hidden="true"
            className="mt-1.5 h-[11px] w-[11px] shrink-0 rounded-full border-2 border-accent bg-surface"
          />
          <div className="flex min-w-0 flex-1 flex-col gap-1">
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="tabular font-medium text-ink-2">{formatDateTime(entry.createdAt)}</span>
              {entry.thesisId ? (
                <Link className={LINK} href={`/journal/${entry.thesisId}`}>
                  #{entry.thesisId}
                </Link>
              ) : (
                <span className="rounded-full bg-surface-3 px-2 py-0.5 text-[11px] font-medium text-ink-3">
                  general
                </span>
              )}
              {entry.tags ? <span className="text-ink-4">{entry.tags}</span> : null}
            </div>
            <p className="text-sm leading-relaxed text-ink">{entry.note}</p>
          </div>
        </li>
      ))}
    </ol>
  );
}
