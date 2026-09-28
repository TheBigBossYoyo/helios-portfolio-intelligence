import { MONEY_MOVED_COLOR } from "@/components/period-change";
import type { Group } from "@/lib/card";
import { EMPTY, formatEur, formatPercent } from "@/lib/format";

/**
 * Spending groups (merchants, categories) as a ranked list of bars on one shared scale, each
 * with its amount, share and count printed. Spending uses the "money moved" colour: it leaves
 * the account, it is not a loss.
 */
export function GroupBars({
  groups,
  total,
  label = (key) => key,
  hrefOf,
}: {
  groups: Group[];
  total: number;
  label?: (key: string) => string;
  hrefOf?: (key: string) => string;
}) {
  const largest = Math.max(...groups.map((group) => group.spent), 0.01);
  return (
    <ul className="flex flex-col gap-3">
      {groups.map((group) => (
        <li className="flex flex-col gap-1.5" key={group.key}>
          <div className="flex items-baseline justify-between gap-3 text-sm">
            <span className="min-w-0 truncate">
              {hrefOf ? (
                <a
                  className="font-medium text-ink hover:text-accent hover:underline"
                  href={hrefOf(group.key)}
                >
                  {label(group.key)}
                </a>
              ) : (
                <span className="font-medium text-ink">{label(group.key)}</span>
              )}
              <span className="ml-1.5 text-xs text-ink-3">{group.count}×</span>
            </span>
            <span className="tabular-nums shrink-0 text-ink">
              {formatEur(group.spent)}
              <span className="ml-2 text-xs text-ink-3">
                {total > 0 ? formatPercent(group.spent / total, 0) : EMPTY}
              </span>
            </span>
          </div>
          <div aria-hidden="true" className="h-1.5 w-full rounded-full bg-surface-3">
            <div
              className="h-full rounded-full"
              style={{
                width: `${Math.max((group.spent / largest) * 100, 1.5)}%`,
                background: MONEY_MOVED_COLOR,
              }}
            />
          </div>
        </li>
      ))}
    </ul>
  );
}
