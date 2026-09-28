import { RefreshCw } from "lucide-react";
import { ActionButton } from "@/components/action-button";
import { SpendingChart } from "@/components/charts/spending-chart";
import { DataTable, type Column } from "@/components/data-table";
import { Note, PageHeader, Panel, Unavailable } from "@/components/panel";
import { Pager } from "@/components/pager";
import { MONEY_MOVED_COLOR } from "@/components/period-change";
import { refreshCardHistoryAction } from "@/lib/actions";
import { getCardHistory } from "@/lib/api";
import {
  EMPTY,
  categoryLabel,
  decimalToNumber,
  formatDateTime,
  formatDay,
  formatEur,
  formatPercent,
} from "@/lib/format";
import { paginate, parsePageParam } from "@/lib/pagination";
import type { CardHistoryStatus, CardTransaction, SpendingGroup, SpendingMonth } from "@/lib/types";
import { CARD } from "@/lib/ui";

export const dynamic = "force-dynamic";

const PAGE_SIZE = 20;
const TOP_MERCHANTS = 10;

const TRANSACTION_COLUMNS: Column<CardTransaction>[] = [
  {
    key: "date",
    header: "Date",
    render: (row) => <span className="whitespace-nowrap text-ink-2">{formatDateTime(row.ts)}</span>,
  },
  {
    key: "merchant",
    header: "Merchant",
    render: (row) => <span className="font-medium text-ink">{row.merchantName ?? "Unknown"}</span>,
  },
  {
    key: "category",
    header: "Category",
    render: (row) => <span className="text-ink-3">{categoryLabel(row.merchantCategory)}</span>,
  },
  {
    key: "amount",
    header: "Amount",
    numeric: true,
    render: (row) => {
      const amount = decimalToNumber(row.amount) ?? 0;
      return (
        <span className={amount > 0 ? "text-positive" : "text-ink"}>
          {amount > 0 ? `+${formatEur(amount)} refund` : `−${formatEur(Math.abs(amount))}`}
        </span>
      );
    },
  },
];

const MONTH_COLUMNS: Column<SpendingMonth>[] = [
  { key: "month", header: "Month", render: (row) => row.label },
  { key: "spent", header: "Spent", numeric: true, render: (row) => formatEur(row.spent) },
  { key: "cashback", header: "Cashback", numeric: true, render: (row) => formatEur(row.cashback) },
  { key: "count", header: "Payments", numeric: true, render: (row) => String(row.count) },
];

export default async function CardPage({
  searchParams,
}: {
  searchParams: Promise<{ month?: string; page?: string }>;
}) {
  const { month, page } = await searchParams;
  const result = await getCardHistory();

  const header = (
    <PageHeader
      actions={<RefreshButton />}
      description="Your 212 Card spending, from Trading 212's own CSV export. Card payments leave the account, so they count as money taken out — never as an investment loss."
      title="Card"
    />
  );

  if (!result.ok) {
    return (
      <>
        {header}
        <Panel title="Card history">
          <Unavailable detail={result.error} reason="Card history unavailable" />
        </Panel>
      </>
    );
  }

  const { status, summary } = result.data;
  if (status.cardRows === 0) {
    return (
      <>
        {header}
        <Panel title="Card history">
          <Unavailable detail={emptyDetail(status)} reason="No card payments yet" />
        </Panel>
      </>
    );
  }

  const months = summary.months;
  const thisMonthKey = new Date().toISOString().slice(0, 7);
  const current = months.find((item) => item.key === thisMonthKey) ?? null;
  const previousKey = (() => {
    const today = new Date();
    const previous = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth() - 1, 1));
    return previous.toISOString().slice(0, 7);
  })();
  const previous = months.find((item) => item.key === previousKey) ?? null;
  const spent = decimalToNumber(summary.spent) ?? 0;
  const selectedMonth = months.find((item) => item.key === month) ?? null;
  const transactions = selectedMonth
    ? summary.transactions.filter((item) => item.ts.slice(0, 7) === selectedMonth.key)
    : summary.transactions;
  const paged = paginate(transactions, parsePageParam(page), PAGE_SIZE);

  return (
    <>
      {header}
      <StatusNote status={status} />

      <section aria-label="Card totals" className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat
          detail={current ? `${current.count} payment(s)` : "No payments yet"}
          label="Spent this month"
          value={formatEur(current?.spent ?? 0)}
        />
        <Stat
          detail={previous ? `${previous.count} payment(s)` : "No payments"}
          label="Last month"
          value={formatEur(previous?.spent ?? 0)}
        />
        <Stat
          detail={summary.firstDate ? `Since ${formatDay(summary.firstDate)}` : EMPTY}
          label="Spent in total"
          value={formatEur(spent)}
        />
        <Stat
          detail={
            summary.cashbackRate !== null
              ? `${formatPercent(summary.cashbackRate)} of spending back · counted as income`
              : "Counted as income, not money added"
          }
          label="Cashback earned"
          tone="up"
          value={formatEur(summary.cashback)}
        />
      </section>

      <Panel subtitle="What left the account by card each month." title="Spending by month">
        <SpendingChart
          data={months.map((item) => ({ label: item.label, spent: decimalToNumber(item.spent) ?? 0 }))}
        />
        <details className="mt-4">
          <summary className="cursor-pointer text-sm font-medium text-ink-2">Show as a table</summary>
          <div className="mt-3">
            <DataTable
              caption="Card spending and cashback by month"
              columns={MONTH_COLUMNS}
              rowKey={(row) => row.key}
              rows={[...months].reverse()}
            />
          </div>
        </details>
      </Panel>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Panel subtitle="Trading 212's merchant categories." title="By category">
          <GroupBars groups={summary.categories} label={categoryLabel} total={spent} />
        </Panel>
        <Panel subtitle={`Where most of it went. Top ${TOP_MERCHANTS}.`} title="Top merchants">
          <GroupBars
            groups={summary.merchants.slice(0, TOP_MERCHANTS)}
            label={(key) => key}
            total={spent}
          />
        </Panel>
      </div>

      <Panel
        subtitle={
          selectedMonth
            ? `${selectedMonth.label}: ${selectedMonth.count} payment(s), ${formatEur(selectedMonth.spent)}.`
            : "Every card payment, newest first."
        }
        title="Card payments"
      >
        <nav aria-label="Filter payments by month" className="mb-4 flex flex-wrap gap-2">
          <MonthPill active={!selectedMonth} href="/card" label="All" />
          {[...months].reverse().map((item) => (
            <MonthPill
              active={selectedMonth?.key === item.key}
              href={`/card?month=${item.key}`}
              key={item.key}
              label={item.label}
            />
          ))}
        </nav>
        <DataTable
          caption="Card payments"
          columns={TRANSACTION_COLUMNS}
          empty="No card payments in this month."
          rowKey={(row) => row.rowId}
          rows={paged.items}
        />
        <Pager
          basePath="/card"
          extraParams={{ month: selectedMonth?.key }}
          page={paged.page}
          pageCount={paged.pageCount}
        />
      </Panel>
    </>
  );
}

function emptyDetail(status: CardHistoryStatus): string {
  if (!status.enabled) {
    return "Card history needs your Trading 212 key (with history permission), and HELIOS_CARD_HISTORY_ENABLED left on.";
  }
  if (status.pending) {
    return "Trading 212 is preparing the export. Helios collects it automatically within about 15 minutes.";
  }
  return "Press Refresh to ask Trading 212 for its CSV export. Your phone gets a notification from Trading 212 when it is ready.";
}

function StatusNote({ status }: { status: CardHistoryStatus }) {
  const parts: string[] = [];
  if (status.lastDownloadedAt) parts.push(`Last export collected ${formatDateTime(status.lastDownloadedAt)}.`);
  if (status.pending) parts.push("A newer export is being prepared and will be collected automatically.");
  else if (status.enabled) parts.push("A fresh export is requested once a day.");
  parts.push(
    "Card spending is money leaving your account: the Overview lists it apart from bank withdrawals, and it never lowers your returns. Cashback counts as income.",
  );
  return <Note>{parts.join(" ")}</Note>;
}

function Stat({
  label,
  value,
  detail,
  tone,
}: {
  label: string;
  value: string;
  detail: string;
  tone?: "up";
}) {
  return (
    <div className={`${CARD} flex flex-col gap-1.5 p-4`}>
      <span className="text-sm font-medium text-ink-3">{label}</span>
      <span
        className={`text-2xl font-semibold tracking-tight ${tone === "up" ? "text-positive" : "text-ink"}`}
      >
        {value}
      </span>
      <span className="text-xs text-ink-3">{detail}</span>
    </div>
  );
}

/** A bar per group on one shared scale, with the value and share printed beside it. */
function GroupBars({
  groups,
  total,
  label,
}: {
  groups: SpendingGroup[];
  total: number;
  label: (key: string) => string;
}) {
  const largest = Math.max(...groups.map((group) => decimalToNumber(group.spent) ?? 0), 0.01);
  return (
    <ul className="flex flex-col gap-3">
      {groups.map((group) => {
        const value = decimalToNumber(group.spent) ?? 0;
        return (
          <li className="flex flex-col gap-1.5" key={group.key}>
            <div className="flex items-baseline justify-between gap-3 text-sm">
              <span className="min-w-0 truncate">
                <span className="font-medium text-ink">{label(group.key)}</span>
                <span className="ml-1.5 text-xs text-ink-3">{group.count}×</span>
              </span>
              <span className="tabular-nums shrink-0 text-ink">
                {formatEur(value)}
                <span className="ml-2 text-xs text-ink-3">
                  {total > 0 ? formatPercent(value / total, 0) : EMPTY}
                </span>
              </span>
            </div>
            <div aria-hidden="true" className="h-1.5 w-full rounded-full bg-surface-3">
              <div
                className="h-full rounded-full"
                style={{
                  width: `${Math.max((value / largest) * 100, 1.5)}%`,
                  background: MONEY_MOVED_COLOR,
                }}
              />
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function MonthPill({ href, label, active }: { href: string; label: string; active: boolean }) {
  return (
    <a
      aria-current={active ? "true" : undefined}
      className={`inline-flex items-center whitespace-nowrap rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
        active
          ? "border-transparent bg-accent-soft text-accent-ink"
          : "border-border text-ink-2 hover:border-border-strong hover:bg-surface-2"
      }`}
      href={href}
    >
      {label}
    </a>
  );
}

/**
 * Requesting an export makes Trading 212 notify the phone app, so the button asks once before
 * doing it. If an export is already being prepared, the same press just collects it.
 */
function RefreshButton() {
  return (
    <ActionButton
      action={refreshCardHistoryAction}
      confirmLabel="Confirm — notifies your phone"
      icon={<RefreshCw aria-hidden="true" size={15} />}
      label="Refresh"
      pendingLabel="Refreshing…"
      variant="secondary"
    />
  );
}
