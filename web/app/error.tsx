"use client";

import { useEffect } from "react";
import { Note, Panel, Unavailable } from "@/components/panel";
import { BUTTON } from "@/lib/ui";

/**
 * Root error boundary.
 *
 * Next hands this component whatever value was thrown while rendering a page, and in production
 * that `Error`'s `message` (and its `stack`) can carry internal detail: a file path, a query
 * fragment, an upstream URL. This component never renders `error.message` or `error.stack` to
 * the page for that reason — the only identifier shown is `error.digest`, which Next generates
 * specifically to be safe to display and to grep out of server logs.
 *
 * Every other panel on this dashboard already explains a failure as "why", not a stack trace
 * (`Unavailable`), so this reuses that vocabulary rather than inventing a second one.
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // The one place this app logs a raw error — to the browser console, for whoever is looking
    // at devtools right now, not to the page itself.
    console.error(error);
  }, [error]);

  return (
    <>
      <Panel subtitle="This page hit an unhandled error while rendering." title="Something went wrong">
        <Unavailable
          detail={
            error.digest
              ? `Reference ${error.digest}. The API may be down, or this page hit a bug — Helios does not show the raw error here because it can contain internal detail. That reference is safe to search for in the backend logs.`
              : "The API may be down, or this page hit a bug. Helios does not show raw error detail here; check the backend logs."
          }
          reason="Page failed to render"
        />
        <div className="mt-4 flex flex-col items-start gap-3">
          <button className={BUTTON.primary} onClick={reset} type="button">
            Retry
          </button>
          <Note>
            Retry re-renders this page. If the underlying cause was the Trading 212 or
            market-data API being unreachable, it clears once that comes back; if it keeps
            happening, the reference above is what to look for in the backend logs.
          </Note>
        </div>
      </Panel>
    </>
  );
}
