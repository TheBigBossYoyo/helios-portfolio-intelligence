"use client";

import { useState, useTransition } from "react";
import { ActionMessage } from "@/components/action-button";
import type { ActionResult } from "@/lib/actions";
import { BUTTON, FIELD, LABEL } from "@/lib/ui";

const KINDS = [
  { value: "above", label: "Price rises to", unit: "price" },
  { value: "below", label: "Price falls to", unit: "price" },
  { value: "gain_pct", label: "Gain on my average reaches", unit: "%" },
  { value: "loss_pct", label: "Loss on my average reaches", unit: "%" },
] as const;

/**
 * "Tell me when...": a price level in the instrument's own currency, or a gain or loss on the
 * average price paid. Helios checks it every few minutes and sends a Windows notification once.
 */
export function AlertForm({
  ticker,
  currency,
  currentPrice,
  held,
  action,
}: {
  ticker: string;
  currency: string | null;
  currentPrice: number | null;
  held: boolean;
  action: (form: FormData) => Promise<ActionResult>;
}) {
  const [pending, startTransition] = useTransition();
  const [result, setResult] = useState<ActionResult | null>(null);
  const [kind, setKind] = useState<(typeof KINDS)[number]["value"]>("above");
  const unit = KINDS.find((item) => item.value === kind)?.unit === "%" ? "%" : (currency ?? "");
  const kinds = held ? KINDS : KINDS.filter((item) => item.unit === "price");

  return (
    <form
      className="flex flex-col gap-3"
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
      <input name="ticker" type="hidden" value={ticker} />
      <label className={LABEL} htmlFor={`alert-kind-${ticker}`}>
        When
        <select
          className={FIELD}
          id={`alert-kind-${ticker}`}
          name="kind"
          onChange={(event) => setKind(event.target.value as typeof kind)}
          value={kind}
        >
          {kinds.map((item) => (
            <option key={item.value} value={item.value}>
              {item.label}
            </option>
          ))}
        </select>
      </label>
      <label className={LABEL} htmlFor={`alert-threshold-${ticker}`}>
        {unit === "%" ? "Percent" : `Price${unit ? ` (${unit})` : ""}`}
        <input
          className={`${FIELD} tabular-nums`}
          id={`alert-threshold-${ticker}`}
          inputMode="decimal"
          name="threshold"
          placeholder={
            unit === "%" ? "e.g. 20" : currentPrice !== null ? `now ${currentPrice}` : "e.g. 100"
          }
          required
        />
      </label>
      <label className={LABEL} htmlFor={`alert-note-${ticker}`}>
        Note (optional)
        <input
          className={FIELD}
          id={`alert-note-${ticker}`}
          maxLength={200}
          name="note"
          placeholder="e.g. take some profit"
        />
      </label>
      <div className="flex items-center justify-between gap-3">
        <ActionMessage pending={pending} result={result} />
        <button className={`${BUTTON.primary} ${BUTTON.small}`} disabled={pending} type="submit">
          {pending ? "Saving…" : "Set alert"}
        </button>
      </div>
    </form>
  );
}
