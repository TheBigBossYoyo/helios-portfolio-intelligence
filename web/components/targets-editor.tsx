"use client";

import { useState, useTransition } from "react";

import { ActionMessage } from "@/components/action-button";
import type { ActionResult } from "@/lib/actions";
import { BUTTON, FIELD, HELP } from "@/lib/ui";

export interface TargetRow {
  ticker: string;
  label: string;
  name: string | null;
  /** Share of the portfolio today, 0..1. */
  current: number;
  /** Saved target in percent, or null. */
  target: number | null;
}

function percentText(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

/**
 * One percentage per holding. The running total says how much is planned; anything left blank
 * stays outside the plan. Saving sends the whole plan at once.
 */
export function TargetsEditor({
  rows,
  action,
}: {
  rows: TargetRow[];
  action: (targets: { ticker: string; weight: string }[]) => Promise<ActionResult>;
}) {
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(rows.map((row) => [row.ticker, row.target === null ? "" : percentText(row.target)])),
  );
  const [pending, startTransition] = useTransition();
  const [result, setResult] = useState<ActionResult | null>(null);

  const parsed = Object.fromEntries(
    Object.entries(values).map(([ticker, text]) => [ticker, Number(text.replace(",", ".")) || 0]),
  );
  const total = Object.values(parsed).reduce((sum, value) => sum + Math.max(value, 0), 0);
  const over = total > 100.01;

  const set = (ticker: string, text: string) => setValues((current) => ({ ...current, [ticker]: text }));
  const matchToday = () =>
    setValues(Object.fromEntries(rows.map((row) => [row.ticker, row.current > 0 ? percentText(Math.round(row.current * 1000) / 10) : ""])));
  const evenly = () => {
    const held = rows.filter((row) => row.current > 0);
    const share = held.length ? Math.floor((1000 / held.length)) / 10 : 0;
    setValues(Object.fromEntries(rows.map((row) => [row.ticker, row.current > 0 ? percentText(share) : ""])));
  };

  const save = () => {
    setResult(null);
    startTransition(async () => {
      setResult(
        await action(
          Object.entries(parsed)
            .filter(([, value]) => value > 0)
            .map(([ticker, value]) => ({ ticker, weight: (value / 100).toFixed(4) })),
        ),
      );
    });
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap gap-2">
        <button className={`${BUTTON.secondary} ${BUTTON.small}`} onClick={matchToday} type="button">
          Start from today&apos;s mix
        </button>
        <button className={`${BUTTON.secondary} ${BUTTON.small}`} onClick={evenly} type="button">
          Split evenly
        </button>
        <button
          className={`${BUTTON.ghost} ${BUTTON.small}`}
          onClick={() => setValues(Object.fromEntries(rows.map((row) => [row.ticker, ""])))}
          type="button"
        >
          Clear
        </button>
      </div>
      <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {rows.map((row) => (
          <li className="flex items-center justify-between gap-3 rounded-xl border border-border px-3.5 py-2.5" key={row.ticker}>
            <label className="flex min-w-0 flex-1 flex-col" htmlFor={`target-${row.ticker}`}>
              <span className="truncate text-sm font-medium text-ink">
                {row.label}
                {row.name ? <span className="ml-1.5 font-normal text-ink-3">{row.name}</span> : null}
              </span>
              <span className="text-xs text-ink-3">
                {row.current > 0 ? `${(row.current * 100).toFixed(1)}% today` : "Not held"}
              </span>
            </label>
            <span className="flex shrink-0 items-center gap-1.5">
              <input
                aria-label={`Target for ${row.label}, percent`}
                className={`${FIELD.replace("w-full", "")} w-20 text-right tabular-nums`}
                id={`target-${row.ticker}`}
                inputMode="decimal"
                onChange={(event) => set(row.ticker, event.target.value)}
                placeholder="—"
                value={values[row.ticker] ?? ""}
              />
              <span className="text-sm text-ink-3">%</span>
            </span>
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className={`${HELP} ${over ? "font-medium text-negative" : ""}`} role={over ? "alert" : undefined}>
          {over
            ? `Targets add up to ${total.toFixed(1)}%: bring them down to 100% or less.`
            : total === 0
              ? "No targets yet. Leave a holding blank to keep it outside the plan."
              : `Planned: ${total.toFixed(1)}%. ${
                  total < 99.95
                    ? "The rest stays outside the plan; the targets you set are scaled to fill it."
                    : "Every euro has a target."
                }`}
        </p>
        <div className="flex items-center gap-3">
          <ActionMessage pending={pending} result={result} />
          <button className={`${BUTTON.primary} ${BUTTON.small}`} disabled={pending || over} onClick={save} type="button">
            {pending ? "Saving…" : "Save targets"}
          </button>
        </div>
      </div>
    </div>
  );
}
