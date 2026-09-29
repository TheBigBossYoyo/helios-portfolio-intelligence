import { ArrowDownRight, ArrowUpRight, Info } from "lucide-react";
import { EMPTY, decimalToNumber, formatDay, formatEur, formatSignedPercent } from "@/lib/format";
import type { PeriodSummary } from "@/lib/types";
import { DIVERGING, SERIES } from "@/lib/viz";
import { SEGMENTED } from "@/lib/ui";

/** Colour of money moved in or out: its own identity, never a gain or a loss colour. */
export const MONEY_MOVED_COLOR = SERIES.three;

const SHORT_LABEL: Record<string, string> = {
  "1D": "1D",
  "1W": "1W",
  "1M": "1M",
  "3M": "3M",
  YTD: "YTD",
  "1Y": "1Y",
  ALL: "All",
};

export function signedEur(value: number | null): string {
  if (value === null) return EMPTY;
  const text = formatEur(Math.abs(value));
  if (value > 0) return `+${text}`;
  if (value < 0) return `−${text}`;
  return text;
}

/**
 * Period tabs as plain links (`?period=1M`), so a period is bookmarkable, works without
 * JavaScript, and the page renders it server-side like everything else.
 */
export function PeriodTabs({
  periods,
  selected,
  basePath,
}: {
  periods: PeriodSummary[];
  selected: string;
  basePath: string;
}) {
  return (
    <nav aria-label="Period" className={`${SEGMENTED} w-full sm:w-auto`}>
      {periods.map((period) => {
        const active = period.key === selected;
        return (
          <a
            aria-current={active ? "true" : undefined}
            className={`flex-1 rounded-lg px-2 py-1.5 text-center text-sm font-medium transition-colors sm:flex-none sm:px-3 ${
              active ? "bg-surface text-ink shadow-card" : "text-ink-3 hover:text-ink"
            }`}
            href={`${basePath}?period=${period.key}`}
            key={period.key}
            title={period.label}
          >
            {SHORT_LABEL[period.key] ?? period.key}
          </a>
        );
      })}
    </nav>
  );
}

/** "27 Aug 2026 → 26 Sep 2026", or "since you started" for an inception period. */
export function periodRange(period: PeriodSummary): string {
  const end = period.endDate ? formatDay(period.endDate) : EMPTY;
  return period.startDate ? `${formatDay(period.startDate)} → ${end}` : `Since you started → ${end}`;
}

/**
 * The two numbers that answer "how did I do?" without letting deposits pose as profit:
 * what the investments made, and what the owner moved in or out, side by side.
 */
export function PeriodHeadline({ period }: { period: PeriodSummary }) {
  const result = decimalToNumber(period.investmentResultEur);
  const moved = decimalToNumber(period.netDepositsEur) ?? 0;
  const change = decimalToNumber(period.valueChangeEur);
  const cardSpent = decimalToNumber(period.cardSpendingEur ?? null) ?? 0;
  const up = result !== null && result > 0;
  const down = result !== null && result < 0;

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
      <div className="flex flex-col gap-1">
        <span className="text-sm font-medium text-ink-3">Investment result</span>
        <span
          className={`flex items-center gap-1.5 text-2xl font-semibold tracking-tight ${
            up ? "text-positive" : down ? "text-negative" : "text-ink"
          }`}
        >
          {up ? <ArrowUpRight aria-hidden="true" size={20} /> : null}
          {down ? <ArrowDownRight aria-hidden="true" size={20} /> : null}
          {signedEur(result)}
        </span>
        <span className="text-xs text-ink-3">
          {period.twr !== null ? `${formatSignedPercent(period.twr)} return` : "Return not available"}
        </span>
      </div>
      <div className="flex flex-col gap-1">
        <span className="text-sm font-medium text-ink-3">Money added</span>
        <span className="flex items-center gap-2 text-2xl font-semibold tracking-tight text-ink">
          <span
            aria-hidden="true"
            className="h-2.5 w-2.5 rounded-[3px]"
            style={{ background: MONEY_MOVED_COLOR }}
          />
          {signedEur(moved)}
        </span>
        <span className="text-xs text-ink-3">
          {signedEur(decimalToNumber(period.depositsEur))} in ·{" "}
          {signedEur(decimalToNumber(period.withdrawalsEur))} out
          {cardSpent === 0
            ? ""
            : cardSpent === decimalToNumber(period.withdrawalsEur)
              ? " (all by card)"
              : ` (${signedEur(cardSpent)} by card)`}{" "}
          · not performance
        </span>
      </div>
      <div className="flex flex-col gap-1">
        <span className="text-sm font-medium text-ink-3">Change in value</span>
        <span className="text-2xl font-semibold tracking-tight text-ink">{signedEur(change)}</span>
        <span className="text-xs text-ink-3">
          {formatEur(decimalToNumber(period.startValueEur))} →{" "}
          {formatEur(decimalToNumber(period.endValueEur))}
        </span>
      </div>
    </div>
  );
}

const MOVED_KEYS = new Set(["deposits", "card", "withdrawals"]);

interface BreakdownRow {
  key: string;
  label: string;
  hint: string;
  value: number;
  color: string;
}

function breakdownRows(period: PeriodSummary): BreakdownRow[] {
  const number = (value: string | null) => decimalToNumber(value) ?? 0;
  const investment: BreakdownRow[] = [
    {
      key: "market",
      label: "Market movement",
      hint: "Price and currency changes on what you hold",
      value: number(period.marketEur),
      color: "",
    },
    { key: "dividends", label: "Dividends", hint: "Paid to you", value: number(period.dividendsEur), color: "" },
    { key: "interest", label: "Interest", hint: "On uninvested cash", value: number(period.interestEur), color: "" },
    {
      key: "cashback",
      label: "Card cashback",
      hint: "Earned on card spending",
      value: number(period.cashbackEur ?? null),
      color: "",
    },
    { key: "fees", label: "Fees", hint: "Charged by Trading 212", value: number(period.feesEur), color: "" },
  ].map((row) => ({ ...row, color: row.value < 0 ? DIVERGING.negative : DIVERGING.positive }));
  // Card payments are withdrawals too; once an export has labelled them they get their own
  // row, and "Withdrawals" is what went to a bank.
  const card = number(period.cardSpendingEur ?? null);
  const moved: BreakdownRow[] = [
    {
      key: "deposits",
      label: "Deposits",
      hint: "Money you put in",
      value: number(period.depositsEur),
      color: MONEY_MOVED_COLOR,
    },
    {
      key: "card",
      label: "Card spending",
      hint: "Paid with your 212 Card",
      value: card,
      color: MONEY_MOVED_COLOR,
    },
    {
      key: "withdrawals",
      label: card !== 0 ? "Withdrawals to bank" : "Withdrawals",
      hint: "Money you took out",
      value: number(period.withdrawalsEur) - card,
      color: MONEY_MOVED_COLOR,
    },
  ];
  // Market movement is always shown (it is the headline of the investment side); the rest only
  // when they happened, so a quiet week is not a list of zeros.
  return [
    ...investment.filter((row) => row.key === "market" || row.value !== 0),
    ...moved.filter((row) => row.value !== 0),
  ];
}

/**
 * Where a period's change in value came from, one row per cause, on a shared scale centred on
 * zero. Money moved in or out sits apart, in its own colour, below a rule: it changes the
 * balance but is not something the investments did. Every value is printed, so no figure is
 * colour- or hover-only.
 */
export function ChangeBreakdown({ period }: { period: PeriodSummary }) {
  const rows = breakdownRows(period);
  const scale = Math.max(...rows.map((row) => Math.abs(row.value)), 0.01);
  const firstMoved = rows.findIndex((row) => MOVED_KEYS.has(row.key));

  return (
    <div className="flex flex-col gap-4">
      <ul className="flex flex-col gap-3">
        {rows.map((row, index) => {
          const width = (Math.abs(row.value) / scale) * 50;
          return (
            <li
              className={`flex flex-col gap-1.5 ${
                index === firstMoved ? "mt-2 border-t border-border pt-4" : ""
              }`}
              key={row.key}
            >
              {index === firstMoved ? (
                <span className="-mt-1 mb-1 text-xs font-medium text-ink-3">
                  Money moved — changes your balance, not your performance
                </span>
              ) : null}
              <div className="flex items-baseline justify-between gap-3 text-sm">
                <span className="min-w-0">
                  <span className="font-medium text-ink">{row.label}</span>
                  <span className="ml-1.5 text-ink-3">{row.hint}</span>
                </span>
                <span className="tabular shrink-0 font-medium text-ink">{signedEur(row.value)}</span>
              </div>
              <div aria-hidden="true" className="relative h-2 w-full rounded-full bg-surface-3">
                <span className="absolute inset-y-0 left-1/2 w-px bg-border-strong" />
                {row.value !== 0 ? (
                  <span
                    className="absolute inset-y-0 rounded-full"
                    style={{
                      background: row.color,
                      width: `${Math.max(width, 1)}%`,
                      left: row.value >= 0 ? "50%" : `${50 - Math.max(width, 1)}%`,
                    }}
                  />
                ) : null}
              </div>
            </li>
          );
        })}
      </ul>
      {period.detail ? (
        <p className="flex gap-2 rounded-xl bg-surface-2 px-3 py-2.5 text-xs leading-relaxed text-ink-2">
          <Info aria-hidden="true" className="mt-0.5 shrink-0 text-ink-4" size={14} />
          {period.detail}
        </p>
      ) : null}
    </div>
  );
}


/**
 * Every period at a glance: what the investments made and what was moved, one row each, each a
 * link to that period. Answers "was it a good week but a bad month?" without clicking through.
 */
export function PeriodTable({
  periods,
  selected,
  basePath,
  compact = false,
}: {
  periods: PeriodSummary[];
  selected: string;
  basePath: string;
  /** Narrow column: short period names and no money-added column (it is in the headline). */
  compact?: boolean;
}) {
  return (
    <div className="overflow-x-auto rounded-xl border border-border">
      <table className={`w-full border-collapse whitespace-nowrap text-sm ${compact ? "" : "stack-sm"}`}>
        <caption className="sr-only">Investment result and money moved for every period</caption>
        <thead className="bg-surface-2">
          <tr className="border-b border-border text-xs text-ink-3">
            <th className="px-3 py-2 text-left font-medium" scope="col">
              Period
            </th>
            <th className="px-3 py-2 text-right font-medium" scope="col">
              Investment result
            </th>
            <th className="px-3 py-2 text-right font-medium" scope="col">
              Return
            </th>
            {compact ? null : (
              <>
                <th className="px-3 py-2 text-right font-medium" scope="col">
                  Money added
                </th>
                <th className="px-3 py-2 text-right font-medium" scope="col">
                  Change in value
                </th>
              </>
            )}
          </tr>
        </thead>
        <tbody>
          {periods.map((period) => {
            const result = decimalToNumber(period.investmentResultEur);
            const active = period.key === selected;
            const tone =
              result === null || result === 0
                ? "text-ink-2"
                : result > 0
                  ? "text-positive"
                  : "text-negative";
            return (
              <tr
                className={`border-b border-border last:border-b-0 ${
                  active ? "bg-accent-soft" : "hover:bg-surface-2"
                }`}
                key={period.key}
              >
                <td className="px-3 py-2" data-label="Period">
                  <a
                    aria-current={active ? "true" : undefined}
                    className="font-medium text-ink hover:underline"
                    href={`${basePath}?period=${period.key}`}
                  >
                    {compact ? (SHORT_LABEL[period.key] ?? period.key) : period.label}
                  </a>
                </td>
                <td className={`tabular-nums px-3 py-2 text-right font-medium ${tone}`} data-label="Investment result">
                  {period.status === "ok" ? signedEur(result) : EMPTY}
                </td>
                <td className={`tabular-nums px-3 py-2 text-right ${tone}`} data-label="Return">
                  {period.twr !== null ? formatSignedPercent(period.twr) : EMPTY}
                </td>
                {compact ? null : (
                  <>
                    <td className="tabular-nums px-3 py-2 text-right text-ink-2" data-label="Money added">
                      {signedEur(decimalToNumber(period.netDepositsEur))}
                    </td>
                    <td className="tabular-nums px-3 py-2 text-right text-ink-2" data-label="Change in value">
                      {period.status === "ok"
                        ? signedEur(decimalToNumber(period.valueChangeEur))
                        : EMPTY}
                    </td>
                  </>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
