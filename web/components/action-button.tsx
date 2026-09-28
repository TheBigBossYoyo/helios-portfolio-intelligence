"use client";

import { useState, useTransition } from "react";
import { Loader2 } from "lucide-react";
import type { ReactNode } from "react";
import { BUTTON } from "@/lib/ui";

import type { ActionResult } from "@/lib/actions";

/**
 * Runs one server action and reports what happened, in place.
 *
 * Three rules this holds to, all of them inherited from how the rest of the dashboard treats
 * uncertainty:
 *
 * - **The button disables while the action is in flight.** A replay rewrites the whole NAV
 *   table and refetches every price against a metered quota; a double-click is a real cost,
 *   not a cosmetic one. The backend refuses the second call too, but the UI should not invite
 *   it.
 * - **Expensive or irreversible actions confirm first.** Spending money or rewriting history
 *   deserves a deliberate second click. Routine writes do not, so `confirmLabel` is opt-in.
 * - **A failure says why.** The backend's `detail` is rendered verbatim, so "Portfolio sync
 *   already running" reaches the screen instead of a spinner that quietly stops.
 */
export function ActionButton({
  action,
  label,
  pendingLabel,
  confirmLabel,
  variant = "secondary",
  icon,
}: {
  action: () => Promise<ActionResult>;
  label: string;
  pendingLabel: string;
  /** When set, the first click asks for confirmation and only the second runs the action. */
  confirmLabel?: string;
  variant?: "primary" | "secondary";
  icon?: ReactNode;
}) {
  const [pending, startTransition] = useTransition();
  const [result, setResult] = useState<ActionResult | null>(null);
  const [confirming, setConfirming] = useState(false);

  const run = () => {
    setConfirming(false);
    setResult(null);
    startTransition(async () => {
      setResult(await action());
    });
  };

  return (
    <div className="flex flex-col items-end gap-1">
      <div className="flex items-center gap-2">
        {confirming ? (
          <button
            type="button"
            onClick={() => setConfirming(false)}
            className={`${BUTTON.ghost} ${BUTTON.small}`}
          >
            Cancel
          </button>
        ) : null}
        <button
          type="button"
          disabled={pending}
          onClick={() => {
            if (confirmLabel && !confirming) {
              setConfirming(true);
              return;
            }
            run();
          }}
          className={`${confirming ? BUTTON.primary : BUTTON[variant]} ${BUTTON.small}`}
        >
          {pending ? (
            <Loader2 aria-hidden="true" className="animate-spin" size={14} />
          ) : confirming ? null : (
            icon
          )}
          {pending ? pendingLabel : confirming ? confirmLabel : label}
        </button>
      </div>
      <ActionMessage pending={pending} result={result} />
    </div>
  );
}

/**
 * Status line under a control. Never colour alone: each state carries a glyph and a word, so
 * the outcome survives both a monochrome screen and a colourblind reader.
 */
export function ActionMessage({
  pending,
  result,
}: {
  pending: boolean;
  result: ActionResult | null;
}) {
  if (pending) {
    return (
      <span role="status" className="text-xs text-ink-3">
        Working…
      </span>
    );
  }
  if (result === null) {
    return null;
  }
  return result.ok ? (
    <span role="status" className="max-w-md text-right text-xs font-medium text-positive">
      ✓ {result.message}
    </span>
  ) : (
    <span role="alert" className="max-w-md text-right text-xs font-medium text-negative">
      ✕ {result.error}
    </span>
  );
}
