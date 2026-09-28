import { ChevronLeft, ChevronRight, RefreshCw } from "lucide-react";
import { ActionButton } from "@/components/action-button";
import { GroupBars } from "@/components/card-bits";
import { SpendingChart } from "@/components/charts/spending-chart";
import { DataTable, type Column } from "@/components/data-table";
import { Note, PageHeader, Panel, Unavailable } from "@/components/panel";
import { Pager } from "@/components/pager";
import { refreshCardHistoryAction } from "@/lib/actions";
import { getCardHistory } from "@/lib/api";
import {
  type Bucket,
  GRANULARITIES,
  type Granularity,
  amountOf,
  bucketKey,
  bucketLabel,
  buildBuckets,
  formatLocalDateTime,
  groupBy,
  inBucket,
  merchantHref,
  merchantOf,
  normaliseBucketKey,
  parseGranularity,
  shiftBucket,
} from "@/lib/card";
import {
  EMPTY,
  categoryLabel,
  formatDateTime,
  formatDay,
  formatEur,
  formatPercent,
  formatSignedPercent,
} from "@/lib/format";
import { paginate, parsePageParam } from "@/lib/pagination";
import type { CardHistoryStatus, CardTransaction } from "@/lib/types";
import { CARD } from "@/lib/ui";

export const dynamic = "force-dynamic";

const PAGE_SIZE = 20;
const ALL = "all";

const TRANSACTION_COLUMNS: Column<CardTransaction>[] = [
  {
    key: "date",
    header: "When",
    render: (row) => (
      <span className="whitespace-nowrap text-ink-2">{formatLocalDateTime(row.ts)}</span>
    ),
  },
  {
    key: "merchant",
    header: "Merchant",
    render: (row) => (
      <a
        className="font-medium text-ink hover:text-accent hover:underline"
        href={merchantHref(merchantOf(row))}
      >
        {merchantOf(row)}
      </a>
    ),
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
      const amount = amountOf(row);
      return (
        <span className={amount > 0 ? "text-positive" : "text-ink"}>
          {amount > 0 ? `+${formatEur(amount)} refund` : `−${formatEur(Math.abs(amount))}`}
        </span>
      );
    },
  },
];

const BUCKET_COLUMNS: Column<Bucket>[] = [
  { key: "period", header: "Period", render: (row) => row.label },
  { key: "spent", header: "Spent", numeric: true, render: (row) => formatEur(row.spent) },
  { key: "count", header: "Payments", numeric: true, render: (row) => String(row.count) },
  { key: "cashback", header: "Cashback", numeric: true, render: (row) => formatEur(row.cashback) },
];

const NAV_LINK =
  "inline-flex items-center gap-1 rounded-lg border border-border px-2.5 py-1 text-xs font-medium text-ink-2 transition-colors hover:border-border-strong hover:bg-surface-2";

export default async function CardPage({
  searchParams,
}: {
  searchParams: Promise<{ view?: string; at?: string; page?: string }>;
}) {
  const { view, at, page } = await searchParams;
  const granularity = parseGranularity(view);
  const result = await getCardHistory();

  const header = (
    <PageHeader
      actions={<RefreshButton />}
      description="Your 212 Card spending, from Trading 212's own CSV export, in your local time. Card payments leave the account, so they count as money taken out — never as an investment loss."
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
  if (status.cardRows === 0 || summary.transactions.length === 0) {
    return (
      <>
        {header}
        <Panel title="Card history">
          <Unavailable detail={emptyDetail(status)} reason="No card payments yet" />
        </Panel>
      </>
    );
  }

  const transactions = summary.transactions;
  const now = new Date();
  const within = (key: string, g: Granularity) =>
    transactions.filter((item) => inBucket(item.ts, key, g));
  const spentOf = (items: CardTransaction[]) => items.reduce((sum, item) => sum - amountOf(item), 0);

  const buckets = buildBuckets(transactions, summary.cashbackEntries ?? [], granularity, now);
  const allTime = at === ALL;
  const latestWithSpend = [...buckets].reverse().find((bucket) => bucket.count > 0);
  // The chosen day/week/month: from the URL, else the most recent one with any spending.
  const selectedKey = allTime
    ? null
    : (normaliseBucketKey(at, granularity) ?? latestWithSpend?.key ?? buckets.at(-1)?.key ?? null);
  const scoped = selectedKey ? within(selectedKey, granularity) : transactions;
  const scopedSpent = spentOf(scoped);
  const previousKey = selectedKey ? shiftBucket(selectedKey, granularity, -1) : null;
  const nextKey = selectedKey ? shiftBucket(selectedKey, granularity, 1) : null;
  const previousSpent = previousKey ? spentOf(within(previousKey, granularity)) : 0;
  const change = selectedKey && previousSpent > 0 ? scopedSpent / previousSpent - 1 : null;
  const hasNext = nextKey !== null && nextKey <= bucketKey(now, granularity);
  const merchants = groupBy(scoped, merchantOf);
  const categories = groupBy(scoped, (item) => item.merchantCategory ?? "UNCATEGORISED");
  const paged = paginate(scoped, parsePageParam(page), PAGE_SIZE);
  const base = `/card?view=${granularity}`;
  const scopeTitle = selectedKey ? bucketLabel(selectedKey, granularity) : "All time";

  const today = within(bucketKey(now, "day"), "day");
  const thisWeek = within(bucketKey(now, "week"), "week");
  const thisMonth = within(bucketKey(now, "month"), "month");

  return (
    <>
      {header}
      <StatusNote status={status} />

      <section aria-label="Card totals" className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat detail={`${today.length} payment(s)`} label="Today" value={formatEur(spentOf(today))} />
        <Stat
          detail={`${thisWeek.length} payment(s) since Monday`}
          label="This week"
          value={formatEur(spentOf(thisWeek))}
        />
        <Stat
          detail={`${thisMonth.length} payment(s) · ${formatEur(summary.spent)} since ${
            summary.firstDate ? formatDay(summary.firstDate) : EMPTY
          }`}
          label="This month"
          value={formatEur(spentOf(thisMonth))}
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

      <Panel
        actions={
          <nav aria-label="Group spending by" className="flex gap-1 rounded-xl bg-surface-3 p-1">
            {GRANULARITIES.map((option) => (
              <a
                aria-current={option.key === granularity ? "true" : undefined}
                className={`rounded-lg px-3 py-1 text-xs font-medium transition-colors ${
                  option.key === granularity
                    ? "bg-surface text-ink shadow-card"
                    : "text-ink-3 hover:text-ink"
                }`}
                href={`/card?view=${option.key}`}
                key={option.key}
              >
                {option.label}
              </a>
            ))}
          </nav>
        }
        subtitle="Click a bar to see which merchants that money went to."
        title="Spending over time"
      >
        <SpendingChart
          data={buckets.map((bucket) => ({
            key: bucket.key,
            label: bucket.label,
            spent: bucket.spent,
            count: bucket.count,
          }))}
          hrefPrefix={`${base}&at=`}
          selectedKey={selectedKey}
        />
        <details className="mt-4">
          <summary className="cursor-pointer text-sm font-medium text-ink-2">
            Show as a table
          </summary>
          <div className="mt-3">
            <DataTable
              caption="Card spending per period"
              columns={BUCKET_COLUMNS}
              maxHeight={360}
              rowKey={(row) => row.key}
              rows={[...buckets].reverse()}
            />
          </div>
        </details>
      </Panel>

      <section
        aria-label={`Spending: ${scopeTitle}`}
        className={`${CARD} flex flex-col gap-5 p-5 sm:p-6`}
      >
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex flex-col gap-1">
            <h2 className="text-lg font-semibold text-ink">{scopeTitle}</h2>
            <p className="text-sm text-ink-3">
              <span className="font-medium text-ink">{formatEur(scopedSpent)}</span> in{" "}
              {scoped.length} payment(s)
              {scoped.length > 0 ? ` · ${formatEur(scopedSpent / scoped.length)} on average` : ""}
              {change !== null
                ? ` · ${formatSignedPercent(change, 0)} vs the ${granularity} before`
                : ""}
            </p>
          </div>
          <nav aria-label="Choose period" className="flex flex-wrap items-center gap-1">
            {previousKey ? (
              <a className={NAV_LINK} href={`${base}&at=${previousKey}`}>
                <ChevronLeft aria-hidden="true" size={15} />
                {bucketLabel(previousKey, granularity)}
              </a>
            ) : null}
            <a
              aria-current={allTime ? "true" : undefined}
              className={`${NAV_LINK} ${allTime ? "bg-accent-soft text-accent-ink" : ""}`}
              href={`${base}&at=${ALL}`}
            >
              All time
            </a>
            {nextKey && hasNext ? (
              <a className={NAV_LINK} href={`${base}&at=${nextKey}`}>
                {bucketLabel(nextKey, granularity)}
                <ChevronRight aria-hidden="true" size={15} />
              </a>
            ) : null}
          </nav>
        </div>

        {scoped.length === 0 ? (
          <p className="rounded-xl bg-surface-2 px-4 py-6 text-center text-sm text-ink-3">
            No card payments in this {granularity}.
          </p>
        ) : (
          <>
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
              <div className="flex flex-col gap-3">
                <h3 className="text-sm font-semibold text-ink">Merchants</h3>
                <GroupBars groups={merchants} hrefOf={merchantHref} total={scopedSpent} />
              </div>
              <div className="flex flex-col gap-3">
                <h3 className="text-sm font-semibold text-ink">Categories</h3>
                <GroupBars groups={categories} label={categoryLabel} total={scopedSpent} />
              </div>
            </div>
            <div className="flex flex-col gap-3">
              <h3 className="text-sm font-semibold text-ink">Payments</h3>
              <DataTable
                caption={`Card payments: ${scopeTitle}`}
                columns={TRANSACTION_COLUMNS}
                rowKey={(row) => row.rowId}
                rows={paged.items}
              />
              <Pager
                basePath="/card"
                extraParams={{ view: granularity, at: allTime ? ALL : (selectedKey ?? undefined) }}
                page={paged.page}
                pageCount={paged.pageCount}
              />
            </div>
          </>
        )}
      </section>
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
  if (status.lastDownloadedAt) {
    parts.push(`Last export collected ${formatDateTime(status.lastDownloadedAt)}.`);
  }
  if (status.pending) {
    parts.push("A newer export is being prepared and will be collected automatically.");
  } else if (status.enabled) {
    parts.push("A fresh export is requested once a day.");
  }
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
