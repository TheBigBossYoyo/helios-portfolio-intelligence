import { SERIES } from "@/lib/viz";

export interface BarListItem {
  key: string;
  label: string;
  sublabel?: string | null;
  /** The magnitude that sets the bar length. */
  value: number;
  /** What is printed at the end of the row (already formatted). */
  display: string;
  href?: string;
}

/**
 * Ranked horizontal bars: one series, so one colour for every bar (never a value ramp on
 * nominal categories). Every value is printed, so nothing is hover-only; bars are relative to
 * the largest row and anchored at zero, because length is the encoding.
 */
export function BarList({ items, color = SERIES.one }: { items: BarListItem[]; color?: string }) {
  const max = Math.max(...items.map((item) => item.value), 0) || 1;
  return (
    <ul className="flex flex-col gap-3">
      {items.map((item) => {
        const width = Math.max(2, (item.value / max) * 100);
        const label = (
          <span className="min-w-0 truncate">
            <span className="font-medium text-ink">{item.label}</span>
            {item.sublabel ? <span className="ml-1.5 text-ink-3">{item.sublabel}</span> : null}
          </span>
        );
        return (
          <li className="flex flex-col gap-1.5" key={item.key}>
            <div className="flex items-baseline justify-between gap-3 text-sm">
              {item.href ? (
                <a className="min-w-0 truncate hover:underline" href={item.href}>
                  {label}
                </a>
              ) : (
                label
              )}
              <span className="tabular shrink-0 text-ink-2">{item.display}</span>
            </div>
            <div aria-hidden="true" className="h-1.5 w-full rounded-full bg-surface-3">
              <div
                className="h-1.5 rounded-full"
                style={{ width: `${width}%`, background: color }}
              />
            </div>
          </li>
        );
      })}
    </ul>
  );
}
