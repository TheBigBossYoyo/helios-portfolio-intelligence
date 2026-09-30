"use client";

import { useState, useTransition } from "react";

import { ActionMessage } from "@/components/action-button";
import type { ActionResult } from "@/lib/actions";
import { BUTTON, FIELD, LABEL } from "@/lib/ui";

export function GoalForm({ action }: { action: (form: FormData) => Promise<ActionResult> }) {
  const [pending, startTransition] = useTransition();
  const [result, setResult] = useState<ActionResult | null>(null);
  const [kind, setKind] = useState<"value" | "income">("value");
  return (
    <form
      className="grid grid-cols-1 items-end gap-3 rounded-2xl bg-surface-2 p-4 sm:grid-cols-2 lg:grid-cols-[10rem_minmax(0,1fr)_9rem_10rem_auto]"
      onSubmit={(event) => {
        event.preventDefault();
        const form = event.currentTarget;
        const data = new FormData(form);
        setResult(null);
        startTransition(async () => {
          const outcome = await action(data);
          setResult(outcome);
          if (outcome.ok) form.reset();
        });
      }}
    >
      <label className={LABEL}>
        Goal
        <select
          className={FIELD}
          name="kind"
          onChange={(event) => setKind(event.target.value === "income" ? "income" : "value")}
          value={kind}
        >
          <option value="value">Portfolio value</option>
          <option value="income">Monthly dividends</option>
        </select>
      </label>
      <label className={LABEL}>
        Name
        <input className={FIELD} maxLength={120} name="name" placeholder={kind === "income" ? "€50 a month in dividends" : "€10,000 invested"} required />
      </label>
      <label className={LABEL}>
        {kind === "income" ? "€ a month" : "Target (€)"}
        <input className={`${FIELD} tabular-nums`} inputMode="decimal" name="targetAmount" placeholder={kind === "income" ? "50" : "10000"} required />
      </label>
      <label className={LABEL}>
        By
        <input className={FIELD} name="targetDate" required type="date" />
      </label>
      <div className="flex flex-col items-start gap-1">
        <button className={`${BUTTON.primary} h-10`} disabled={pending} type="submit">
          {pending ? "Adding…" : "Add goal"}
        </button>
        <ActionMessage pending={false} result={result} />
      </div>
    </form>
  );
}
