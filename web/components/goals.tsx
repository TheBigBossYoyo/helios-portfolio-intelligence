import { CheckCircle2, Coins, Flag, Target } from "lucide-react";

import { ActionButton } from "@/components/action-button";
import { GoalForm } from "@/components/goal-form";
import type { ActionResult } from "@/lib/actions";
import { formatEur, formatPercent } from "@/lib/format";
import { type GoalInputs, type GoalProgress, evaluateGoal } from "@/lib/goals";
import type { Goal } from "@/lib/types";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function monthYear(day: string): string {
  const [year, month] = day.split("-").map(Number);
  return `${MONTHS[month - 1]} ${year}`;
}

function timeLeft(months: number): string {
  if (months <= 0) return "due now";
  if (months < 12) return `${months} month${months === 1 ? "" : "s"} left`;
  const years = Math.floor(months / 12);
  const rest = months % 12;
  return `${years} year${years === 1 ? "" : "s"}${rest ? ` ${rest} mo` : ""} left`;
}

const STATUS = {
  reached: { label: "Reached", tone: "bg-positive-soft text-positive", ring: "var(--status-good)" },
  "on-track": { label: "On track", tone: "bg-positive-soft text-positive", ring: "var(--series-3)" },
  behind: { label: "Behind", tone: "bg-warning-soft text-warning", ring: "var(--series-4)" },
  missed: { label: "Date passed", tone: "bg-surface-3 text-ink-3", ring: "var(--ink-4)" },
} as const;

function Ring({ progress, color }: { progress: number; color: string }) {
  const radius = 26;
  const circumference = 2 * Math.PI * radius;
  return (
    <svg aria-hidden="true" className="shrink-0 -rotate-90" height={64} viewBox="0 0 64 64" width={64}>
      <circle cx={32} cy={32} fill="none" r={radius} stroke="var(--surface-3)" strokeWidth={7} />
      <circle
        cx={32}
        cy={32}
        fill="none"
        r={radius}
        stroke={color}
        strokeDasharray={`${circumference * Math.max(progress, 0.005)} ${circumference}`}
        strokeLinecap="round"
        strokeWidth={7}
      />
    </svg>
  );
}

function amount(progress: GoalProgress, value: number): string {
  return progress.goal.kind === "income" ? `${formatEur(value)}/mo` : formatEur(value);
}

export function GoalCard({
  progress,
  monthly,
  remove,
}: {
  progress: GoalProgress;
  monthly: number;
  remove?: (id: number) => Promise<ActionResult>;
}) {
  const status = STATUS[progress.status];
  const Icon = progress.goal.kind === "income" ? Coins : Target;
  return (
    <article className="flex flex-col gap-3 rounded-2xl border border-border bg-surface p-4" data-testid="goal">
      <div className="flex items-start gap-3.5">
        <div className="relative">
          <Ring color={status.ring} progress={progress.progress} />
          <span className="absolute inset-0 flex items-center justify-center text-[13px] font-semibold tabular-nums text-ink">
            {Math.round(progress.progress * 100)}%
          </span>
        </div>
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <div className="flex flex-wrap items-center gap-2">
            <Icon aria-hidden="true" className="text-ink-3" size={15} />
            <h3 className="font-semibold text-ink">{progress.goal.name}</h3>
            <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${status.tone}`}>{status.label}</span>
          </div>
          <span className="text-sm text-ink-2">
            <span className="font-semibold tabular-nums text-ink">{amount(progress, progress.current)}</span> of{" "}
            <span className="tabular-nums">{amount(progress, progress.target)}</span>
            <span className="text-ink-3"> · by {monthYear(progress.goal.targetDate)} · {timeLeft(progress.monthsLeft)}</span>
          </span>
        </div>
        {remove ? (
          <ActionButton
            action={remove.bind(null, progress.goal.id)}
            confirmLabel="Remove"
            label="Remove"
            pendingLabel="Removing…"
          />
        ) : null}
      </div>
      {progress.status === "reached" ? (
        <p className="flex items-center gap-1.5 text-sm text-positive">
          <CheckCircle2 aria-hidden="true" size={15} /> Done: you are there.
        </p>
      ) : progress.status === "missed" ? (
        <p className="text-sm text-ink-3">The date has passed. Remove it or set a new one.</p>
      ) : (
        <p className="text-sm leading-relaxed text-ink-3">
          On this course (about {formatEur(monthly)} a month kept invested):{" "}
          <span className="font-medium text-ink-2">{amount(progress, progress.projected)}</span> by{" "}
          {monthYear(progress.goal.targetDate)}.
          {progress.status === "behind" && progress.requiredMonthly !== null ? (
            <>
              {" "}
              To get there, keep about{" "}
              <span className="font-medium text-ink">{formatEur(progress.requiredMonthly)} a month</span> invested.
            </>
          ) : null}
        </p>
      )}
    </article>
  );
}

/** Every goal with its progress, and (when editable) the form to add one. */
export function GoalsBoard({
  goals,
  inputs,
  create,
  remove,
}: {
  goals: Goal[];
  inputs: GoalInputs;
  create?: (form: FormData) => Promise<ActionResult>;
  remove?: (id: number) => Promise<ActionResult>;
}) {
  const progress = goals.map((goal) => evaluateGoal(goal, inputs));
  return (
    <div className="flex flex-col gap-4">
      {progress.length > 0 ? (
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          {progress.map((item) => (
            <GoalCard key={item.goal.id} monthly={inputs.monthly} progress={item} remove={remove} />
          ))}
        </div>
      ) : (
        <p className="flex items-center gap-2 rounded-2xl border border-dashed border-border-strong px-4 py-5 text-sm text-ink-3">
          <Flag aria-hidden="true" size={16} /> No goal yet. Set one below: a portfolio value, or a
          monthly dividend income, by a date.
        </p>
      )}
      {create ? <GoalForm action={create} /> : null}
      <p className="text-xs text-ink-3">
        Progress assumes {formatPercent(inputs.annualReturn, 0)} a year and the amount you have kept
        invested each month lately. Not a forecast.
      </p>
    </div>
  );
}
