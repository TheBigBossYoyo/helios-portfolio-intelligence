"use client";

import { useState, useTransition } from "react";

import { ActionMessage } from "@/components/action-button";
import { BUTTON, FIELD, LABEL } from "@/lib/ui";
import type { ActionResult } from "@/lib/actions";
import type { Thesis } from "@/lib/types";

/**
 * Write controls for theses and the journal.
 *
 * A thesis is worth recording at the moment you have the thought, which is a moment spent
 * looking at the dashboard rather than at a terminal. Until now every write here was CLI-only,
 * so the feature's whole point — capture the reasoning *before* the outcome — depended on the
 * user switching context at exactly the wrong time.
 *
 * These forms post to server actions, so the browser still never contacts the API directly.
 * Rules held throughout:
 *
 * - **A write reports what happened, in place.** Success and failure both render a sentence;
 *   the backend's `detail` reaches the screen verbatim, so an illegal transition says which
 *   one it refused.
 * - **Routine writes do not confirm.** Per the design note on the guard, confirmation is for
 *   expensive or irreversible actions. Creating a draft or adding a note is neither.
 * - **A transition does confirm**, because leaving draft freezes the reasoning and a terminal
 *   status cannot be walked back.
 */

/**
 * Shared submit plumbing: run the action, keep the result, and clear the form only when the
 * write actually landed. A failed submit must not discard what the user typed.
 */
function useFormAction(action: (form: FormData) => Promise<ActionResult>) {
  const [pending, startTransition] = useTransition();
  const [result, setResult] = useState<ActionResult | null>(null);

  const onSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setResult(null);
    startTransition(async () => {
      const outcome = await action(data);
      setResult(outcome);
      if (outcome.ok) {
        form.reset();
      }
    });
  };

  return { pending, result, onSubmit };
}

export function CreateThesisForm({
  action,
  tickers,
}: {
  action: (form: FormData) => Promise<ActionResult>;
  /** Held tickers, offered as a datalist. A thesis may also be portfolio-wide, so it is optional. */
  tickers: string[];
}) {
  const { pending, result, onSubmit } = useFormAction(action);

  return (
    <form className="flex flex-col gap-4" onSubmit={onSubmit}>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <label className={LABEL} htmlFor="thesis-title">
          Title
          <input
            className={FIELD}
            id="thesis-title"
            name="title"
            placeholder="Services compound"
            required
          />
        </label>
        <label className={LABEL} htmlFor="thesis-ticker">
          Ticker (optional)
          <input
            className={FIELD}
            id="thesis-ticker"
            list="held-tickers"
            name="t212Ticker"
            placeholder="AAPL_US_EQ — leave blank for portfolio-wide"
          />
          <datalist id="held-tickers">
            {tickers.map((ticker) => (
              <option key={ticker} value={ticker} />
            ))}
          </datalist>
        </label>
      </div>

      <label className={LABEL} htmlFor="thesis-body">
        Reasoning
        <textarea
          className={FIELD}
          id="thesis-body"
          name="body"
          placeholder="What you believe, and what would prove you wrong."
          required
          rows={4}
        />
      </label>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <label className={LABEL} htmlFor="thesis-conviction">
          Conviction
          <select className={FIELD} defaultValue="medium" id="thesis-conviction" name="conviction">
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
          </select>
        </label>
        <label className={LABEL} htmlFor="thesis-isin">
          ISIN (optional)
          <input className={FIELD} id="thesis-isin" name="isin" placeholder="US0378331005" />
        </label>
        <label className={LABEL} htmlFor="thesis-opened">
          Opened (optional)
          <input className={FIELD} id="thesis-opened" name="openedOn" type="date" />
        </label>
      </div>

      <div className="flex items-center justify-end gap-3">
        <ActionMessage pending={pending} result={result} />
        <button className={BUTTON.primary} disabled={pending} type="submit">
          {pending ? "Creating…" : "Create draft"}
        </button>
      </div>
    </form>
  );
}

export function EditThesisForm({
  action,
  drafts,
}: {
  action: (form: FormData) => Promise<ActionResult>;
  /** Only drafts: the backend refuses edits once a thesis leaves draft, so offering more would lie. */
  drafts: Thesis[];
}) {
  const { pending, result, onSubmit } = useFormAction(action);
  const [selected, setSelected] = useState<string>(
    drafts.length > 0 ? String(drafts[0].id) : "",
  );
  const draft = drafts.find((row) => String(row.id) === selected) ?? null;

  if (drafts.length === 0) {
    return (
      <p className="px-1 py-4 text-center text-sm text-ink-3">
        No drafts — a thesis is editable only before it is activated.
      </p>
    );
  }

  return (
    <form className="flex flex-col gap-4" onSubmit={onSubmit}>
      {drafts.length > 1 ? (
        <label className={LABEL} htmlFor="edit-thesis-id">
          Draft
          <select
            className={FIELD}
            id="edit-thesis-id"
            name="thesisId"
            onChange={(event) => setSelected(event.target.value)}
            value={selected}
          >
            {drafts.map((row) => (
              <option key={row.id} value={row.id}>
                #{row.id} — {row.title}
              </option>
            ))}
          </select>
        </label>
      ) : (
        <input name="thesisId" type="hidden" value={selected} />
      )}

      {/* Prefilled from the selected draft, and keyed by id so switching drafts reloads the
          defaults rather than leaving the previous one's text in place. */}
      <label className={LABEL} htmlFor="edit-thesis-title">
        Title
        <input
          className={FIELD}
          defaultValue={draft?.title ?? ""}
          id="edit-thesis-title"
          key={`title-${selected}`}
          name="title"
        />
      </label>

      <label className={LABEL} htmlFor="edit-thesis-body">
        Reasoning
        <textarea
          className={FIELD}
          defaultValue={draft?.body ?? ""}
          id="edit-thesis-body"
          key={`body-${selected}`}
          name="body"
          rows={4}
        />
      </label>

      <label className={LABEL} htmlFor="edit-thesis-conviction">
        Conviction
        <select
          className={FIELD}
          defaultValue={draft?.conviction ?? "medium"}
          id="edit-thesis-conviction"
          key={`conviction-${selected}`}
          name="conviction"
        >
          <option value="low">Low</option>
          <option value="medium">Medium</option>
          <option value="high">High</option>
        </select>
      </label>

      <div className="flex items-center justify-end gap-3">
        <ActionMessage pending={pending} result={result} />
        <button className={BUTTON.primary} disabled={pending} type="submit">
          {pending ? "Saving…" : "Save draft"}
        </button>
      </div>
    </form>
  );
}

/** Status flow, mirrored from `helios.thesis.TRANSITIONS` so the UI never offers an illegal move. */
const TRANSITIONS: Record<string, string[]> = {
  draft: ["active", "closed"],
  active: ["validated", "invalidated", "closed"],
  validated: ["closed"],
  invalidated: ["closed"],
  closed: [],
};

/** Statuses the backend requires an outcome note for. */
const TERMINAL = new Set(["validated", "invalidated", "closed"]);

export function TransitionThesisForm({
  action,
  theses,
}: {
  action: (form: FormData) => Promise<ActionResult>;
  theses: Thesis[];
}) {
  const { pending, result, onSubmit } = useFormAction(action);
  const movable = theses.filter((row) => (TRANSITIONS[row.status] ?? []).length > 0);
  const [selected, setSelected] = useState<string>(
    movable.length > 0 ? String(movable[0].id) : "",
  );
  const [confirming, setConfirming] = useState(false);

  const thesis = movable.find((row) => String(row.id) === selected) ?? null;
  const allowed = thesis ? (TRANSITIONS[thesis.status] ?? []) : [];
  const [toStatus, setToStatus] = useState<string>(allowed[0] ?? "");
  const target = allowed.includes(toStatus) ? toStatus : (allowed[0] ?? "");
  const needsNote = TERMINAL.has(target);

  if (movable.length === 0) {
    return (
      <p className="px-1 py-4 text-center text-sm text-ink-3">
        Nothing to move — every thesis is closed.
      </p>
    );
  }

  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(event) => {
        // A transition freezes the reasoning or settles the outcome, so it is deliberate.
        if (!confirming) {
          event.preventDefault();
          setConfirming(true);
          return;
        }
        setConfirming(false);
        onSubmit(event);
      }}
    >
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {movable.length > 1 ? (
          <label className={LABEL} htmlFor="transition-thesis-id">
            Thesis
            <select
              className={FIELD}
              id="transition-thesis-id"
              name="thesisId"
              onChange={(event) => {
                setSelected(event.target.value);
                setConfirming(false);
                const next = movable.find((row) => String(row.id) === event.target.value);
                setToStatus(next ? (TRANSITIONS[next.status] ?? [])[0] ?? "" : "");
              }}
              value={selected}
            >
              {movable.map((row) => (
                <option key={row.id} value={row.id}>
                  #{row.id} — {row.title} ({row.status})
                </option>
              ))}
            </select>
          </label>
        ) : (
          <input name="thesisId" type="hidden" value={selected} />
        )}

        <label className={LABEL} htmlFor="transition-to-status">
          Move to
          <select
            className={FIELD}
            id="transition-to-status"
            name="toStatus"
            onChange={(event) => {
              setToStatus(event.target.value);
              setConfirming(false);
            }}
            value={target}
          >
            {allowed.map((status) => (
              <option key={status} value={status}>
                {capitalise(status)}
              </option>
            ))}
          </select>
        </label>
      </div>

      <label className={LABEL} htmlFor="transition-outcome-note">
        Outcome note{needsNote ? "" : " (optional)"}
        <textarea
          className={FIELD}
          id="transition-outcome-note"
          name="outcomeNote"
          placeholder={
            needsNote
              ? "What actually happened. Required to settle a thesis."
              : "Optional context for this move."
          }
          required={needsNote}
          rows={3}
        />
      </label>

      <div className="flex items-center justify-end gap-3">
        <ActionMessage pending={pending} result={result} />
        {confirming ? (
          <button className={BUTTON.ghost} onClick={() => setConfirming(false)} type="button">
            Cancel
          </button>
        ) : null}
        <button className={confirming ? BUTTON.primary : BUTTON.secondary} disabled={pending} type="submit">
          {pending
            ? "Moving…"
            : confirming
              ? `Confirm — move to ${target}`
              : `Move to ${target}`}
        </button>
      </div>
    </form>
  );
}

export function AddJournalEntryForm({
  action,
  theses,
  defaultThesisId = "",
}: {
  action: (form: FormData) => Promise<ActionResult>;
  theses: Thesis[];
  /**
   * Preselects a thesis in the dropdown — used on a thesis's own detail page, so a note added
   * there is pre-attached rather than defaulting to "general". Left as "" (general) everywhere
   * else, unchanged from before this prop existed.
   */
  defaultThesisId?: string;
}) {
  const { pending, result, onSubmit } = useFormAction(action);

  return (
    <form className="flex flex-col gap-4" onSubmit={onSubmit}>
      <label className={LABEL} htmlFor="journal-note">
        Note
        <textarea
          className={FIELD}
          id="journal-note"
          name="note"
          placeholder="Added on the pullback."
          required
          rows={3}
        />
      </label>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <label className={LABEL} htmlFor="journal-thesis-id">
          Thesis (optional)
          <select
            className={FIELD}
            defaultValue={defaultThesisId}
            id="journal-thesis-id"
            name="thesisId"
          >
            <option value="">General — not tied to a thesis</option>
            {theses.map((row) => (
              <option key={row.id} value={row.id}>
                #{row.id} — {row.title}
              </option>
            ))}
          </select>
        </label>
        <label className={LABEL} htmlFor="journal-tags">
          Tags (optional)
          <input
            className={FIELD}
            id="journal-tags"
            name="tags"
            placeholder="valuation, earnings"
          />
        </label>
      </div>

      <div className="flex items-center justify-end gap-3">
        <ActionMessage pending={pending} result={result} />
        <button className={BUTTON.primary} disabled={pending} type="submit">
          {pending ? "Adding…" : "Add note"}
        </button>
      </div>
    </form>
  );
}

function capitalise(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}
