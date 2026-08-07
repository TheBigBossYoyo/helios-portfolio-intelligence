import type { ReactNode } from "react";

interface PanelProps {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  children: ReactNode;
}

export function Panel({ title, subtitle, actions, children }: PanelProps) {
  return (
    <section className="panel-surface flex flex-col border border-border transition-colors duration-200 hover:border-neutral-700">
      <header className="relative flex flex-wrap items-baseline justify-between gap-2 px-4 py-3 after:absolute after:inset-x-0 after:bottom-0 after:h-px after:bg-border">
        <div>
          <h2 className="text-xs uppercase tracking-widest text-neutral-300">{title}</h2>
          {subtitle ? (
            <p className="mt-1 max-w-3xl text-[11px] leading-relaxed text-neutral-500">
              {subtitle}
            </p>
          ) : null}
        </div>
        {actions}
      </header>
      <div className="p-4">{children}</div>
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
    <div className="flex flex-col gap-1 rounded-sm border border-dashed border-neutral-800 bg-neutral-950/40 px-4 py-8 text-center">
      <span className="font-mono text-xs uppercase tracking-wider text-neutral-400">{reason}</span>
      {detail ? (
        <span className="mx-auto max-w-2xl text-[11px] leading-relaxed text-neutral-600">
          {detail}
        </span>
      ) : null}
    </div>
  );
}

export function Note({ children }: { children: ReactNode }) {
  return (
    <p className="border-l-2 border-neutral-800 pl-3 text-[11px] leading-relaxed text-neutral-500">
      {children}
    </p>
  );
}
