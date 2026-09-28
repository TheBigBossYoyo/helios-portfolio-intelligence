"use client";

import { useState, useTransition } from "react";
import { ActionMessage } from "@/components/action-button";
import type { ActionResult } from "@/lib/actions";
import { BUTTON, FIELD } from "@/lib/ui";

/**
 * One category's monthly budget: an amount and Save. Leaving the amount empty and saving
 * removes the budget, so there is no separate delete control to find.
 */
export function BudgetForm({
  category,
  label,
  current,
  action,
}: {
  category: string;
  label: string;
  current: number | null;
  action: (form: FormData) => Promise<ActionResult>;
}) {
  const [pending, startTransition] = useTransition();
  const [result, setResult] = useState<ActionResult | null>(null);
  const inputId = `budget-${category}`;

  return (
    <form
      className="flex flex-col items-end gap-1"
      onSubmit={(event) => {
        event.preventDefault();
        const data = new FormData(event.currentTarget);
        setResult(null);
        startTransition(async () => setResult(await action(data)));
      }}
    >
      <input name="category" type="hidden" value={category} />
      <div className="flex items-center gap-2">
        <label className="sr-only" htmlFor={inputId}>
          Monthly budget for {label} (EUR)
        </label>
        <input
          className={`${FIELD} w-28 text-right tabular-nums`}
          defaultValue={current !== null ? current.toFixed(2) : ""}
          id={inputId}
          inputMode="decimal"
          name="limit"
          placeholder="No budget"
        />
        <button className={`${BUTTON.secondary} ${BUTTON.small}`} disabled={pending} type="submit">
          {pending ? "Saving…" : "Save"}
        </button>
      </div>
      <ActionMessage pending={pending} result={result} />
    </form>
  );
}
