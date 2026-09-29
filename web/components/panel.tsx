import { CircleSlash, Info } from "lucide-react";
import type { ReactNode } from "react";
import { CARD } from "@/lib/ui";

interface PanelProps {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  children: ReactNode;
  /** Drop the body padding, for content (tables, charts) that manages its own edge. */
  flush?: boolean;
  className?: string;
}

/** A card: the one container every section of every page sits in. */
export function Panel({ title, subtitle, actions, children, flush, className }: PanelProps) {
  return (
    <section className={`${CARD} theme-fade flex min-w-0 flex-col ${className ?? ""}`}>
      <header className="flex flex-wrap items-start justify-between gap-3 px-4 pb-1 pt-4 sm:px-5">
        <div className="min-w-0">
          <h2 className="text-[15px] font-semibold tracking-tight text-ink">{title}</h2>
          {subtitle ? (
            <p className="mt-0.5 max-w-3xl text-sm leading-relaxed text-ink-3">{subtitle}</p>
          ) : null}
        </div>
        {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
      </header>
      <div className={flush ? "pb-2 pt-3" : "px-4 pb-4 pt-3 sm:px-5 sm:pb-5"}>{children}</div>
    </section>
  );
}

/**
 * Renders the reason a section has no data. Used for both transport failures and the backend's
 * own explicit `unavailable` / `insufficient_data` statuses — the distinction the user needs is
 * "why", and both cases have one.
 */
export function Unavailable({ reason, detail }: { reason: string; detail?: string | null }) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-border-strong bg-surface-2 px-6 py-10 text-center">
      <span
        aria-hidden="true"
        className="flex h-9 w-9 items-center justify-center rounded-full bg-surface-3 text-ink-3"
      >
        <CircleSlash size={18} strokeWidth={1.75} />
      </span>
      <span className="text-sm font-semibold text-ink">{reason}</span>
      {detail ? (
        <span className="mx-auto max-w-xl text-sm leading-relaxed text-ink-3">{detail}</span>
      ) : null}
    </div>
  );
}

/** An explanatory aside. Quiet by design: context, not an alert. */
export function Note({ children }: { children: ReactNode }) {
  return (
    <p className="flex gap-2.5 rounded-xl bg-surface-2 px-3.5 py-3 text-sm leading-relaxed text-ink-2">
      <Info aria-hidden="true" className="mt-0.5 shrink-0 text-ink-4" size={16} strokeWidth={2} />
      <span>{children}</span>
    </p>
  );
}

/** The title block every page opens with. */
export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        <h1 className="text-2xl font-semibold tracking-tight text-ink sm:text-[28px]">{title}</h1>
        {description ? (
          <p className="mt-1 max-w-2xl text-sm leading-relaxed text-ink-3">{description}</p>
        ) : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}
