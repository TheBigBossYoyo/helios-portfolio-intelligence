import { ArrowUpRight, CircleCheck, CircleDashed, CircleX } from "lucide-react";
import { CARD, LINK } from "@/lib/ui";

export interface SetupStep {
  key: string;
  title: string;
  state: "done" | "todo" | "failed";
  /** Why it is not done yet, in words; shown only while it isn't. */
  detail: string;
  href: string;
  cta: string;
}

/**
 * What stands between the operator and a fully valued dashboard, in order.
 *
 * Helios refuses to fabricate: with no price source every return and risk figure reads
 * "insufficient data", which is honest but — without this — looks like a broken app. The
 * checklist turns each gap into a named step with a link to where it is fixed, and disappears
 * once every step is done.
 */
export function SetupChecklist({ steps }: { steps: SetupStep[] }) {
  if (steps.every((step) => step.state === "done")) return null;
  const done = steps.filter((step) => step.state === "done").length;

  return (
    <section aria-label="Setup" className={`${CARD} theme-fade p-5`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-[15px] font-semibold tracking-tight text-ink">
          Finish setting up Helios
        </h2>
        <span className="text-sm text-ink-3">
          {done} of {steps.length} done
        </span>
      </div>
      <div aria-hidden="true" className="mt-3 h-1.5 w-full rounded-full bg-surface-3">
        <div
          className="h-1.5 rounded-full bg-accent transition-[width]"
          style={{ width: `${(done / steps.length) * 100}%` }}
        />
      </div>
      <ol className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
        {steps.map((step, index) => (
          <li
            className={`flex flex-col gap-2 rounded-xl border p-3.5 ${
              step.state === "failed"
                ? "border-negative/40 bg-negative-soft"
                : step.state === "done"
                  ? "border-border bg-surface-2"
                  : "border-border bg-surface"
            }`}
            key={step.key}
          >
            <div className="flex items-center gap-2">
              {step.state === "done" ? (
                <CircleCheck aria-hidden="true" className="text-positive" size={18} />
              ) : step.state === "failed" ? (
                <CircleX aria-hidden="true" className="text-negative" size={18} />
              ) : (
                <CircleDashed aria-hidden="true" className="text-ink-4" size={18} />
              )}
              <span
                className={`text-sm font-medium ${step.state === "done" ? "text-ink-3" : "text-ink"}`}
              >
                {index + 1}. {step.title}
              </span>
              <span className="sr-only">
                {step.state === "done" ? "(done)" : step.state === "failed" ? "(failed)" : "(to do)"}
              </span>
            </div>
            {step.state !== "done" ? (
              <>
                <p className="text-xs leading-relaxed text-ink-2">{step.detail}</p>
                <a className={`${LINK} mt-auto inline-flex items-center gap-1 text-xs`} href={step.href}>
                  {step.cta} <ArrowUpRight aria-hidden="true" size={13} />
                </a>
              </>
            ) : null}
          </li>
        ))}
      </ol>
    </section>
  );
}
