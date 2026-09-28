import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { GroupBars } from "@/components/card-bits";
import { SpendingChart } from "@/components/charts/spending-chart";
import { DataTable, type Column } from "@/components/data-table";
import { PageHeader, Panel, Unavailable } from "@/components/panel";
import { getCardHistory } from "@/lib/api";
import {
  GRANULARITIES,
  WEEKDAYS,
  amountOf,
  buildBuckets,
  formatLocalDateTime,
  merchantProfile,
  parseGranularity,
} from "@/lib/card";
import { EMPTY, categoryLabel, formatEur, formatPercent } from "@/lib/format";
import type { CardTransaction } from "@/lib/types";
import { CARD, LINK } from "@/lib/ui";

export const dynamic = "force-dynamic";

const DAY_MS = 86_400_000;

export default async function MerchantPage({
  params,
  searchParams,
}: {
  params: Promise<{ merchant: string }>;
  searchParams: Promise<{ view?: string }>;
}) {
  const [{ merchant }, { view }] = await Promise.all([params, searchParams]);
  const name = decodeURIComponent(merchant);
  const granularity = parseGranularity(view ?? "month");
  const result = await getCardHistory();

  const back = (
    <Link className={`${LINK} inline-flex items-center gap-1 text-sm`} href="/card">
      <ArrowLeft aria-hidden="true" size={15} /> All card spending
    </Link>
  );

  if (!result.ok) {
    return (
      <>
        <PageHeader actions={back} title={name} />
        <Panel title="Card history">
          <Unavailable detail={result.error} reason="Card history unavailable" />
        </Panel>
      </>
    );
  }

  const all = result.data.summary.transactions;
  const profile = merchantProfile(all, name);
  if (profile.count === 0) {
    return (
      <>
        <PageHeader actions={back} title={name} />
        <Panel title="Payments">
          <Unavailable detail="No card payment to this merchant is stored." reason="Unknown merchant" />
        </Panel>
      </>
    );
  }

  const totalSpent = all.reduce((sum, item) => sum - amountOf(item), 0);
  const days =
    profile.first && profile.last
      ? (new Date(profile.last).getTime() - new Date(profile.first).getTime()) / DAY_MS
      : 0;
  const cadence = profile.count > 1 && days > 0 ? days / (profile.count - 1) : null;
  const buckets = buildBuckets(profile.transactions, [], granularity);
  const weekdayCounts = WEEKDAYS.map(
    (_, index) =>
      profile.transactions.filter((item) => (new Date(item.ts).getDay() + 6) % 7 === index).length,
  );
  const columns: Column<CardTransaction>[] = [
    {
      key: "when",
      header: "When",
      render: (row) => <span className="whitespace-nowrap text-ink-2">{formatLocalDateTime(row.ts)}</span>,
    },
    {
      key: "category",
      header: "Category",
      render: (row) => <span className="text-ink-3">{categoryLabel(row.merchantCategory)}</span>,
    },
    {
      key: "gap",
      header: "Since the previous one",
      numeric: true,
      render: (row) => {
        const index = profile.transactions.indexOf(row);
        const previous = profile.transactions[index + 1];
        if (!previous) return <span className="text-ink-4">First payment</span>;
        const gap = (new Date(row.ts).getTime() - new Date(previous.ts).getTime()) / DAY_MS;
        return <span className="text-ink-3">{gap < 1 ? "Same day" : `${Math.round(gap)} days`}</span>;
      },
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

  return (
    <>
      <PageHeader
        actions={back}
        description={
          <>
            {profile.categories.map(categoryLabel).join(", ") || "Uncategorised"} · first paid{" "}
            {profile.first ? formatLocalDateTime(profile.first) : EMPTY}, last{" "}
            {profile.last ? formatLocalDateTime(profile.last) : EMPTY}
          </>
        }
        title={name}
      />

      <section aria-label="Merchant totals" className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat
          detail={`${totalSpent > 0 ? formatPercent(profile.spent / totalSpent, 1) : EMPTY} of all card spending`}
          label="Spent in total"
          value={formatEur(profile.spent)}
        />
        <Stat
          detail={cadence !== null ? `About every ${Math.max(1, Math.round(cadence))} day(s)` : "Once"}
          label="Payments"
          value={String(profile.count)}
        />
        <Stat detail="Per payment" label="Average" value={formatEur(profile.average)} />
        <Stat
          detail={profile.largest ? formatLocalDateTime(profile.largest.ts) : EMPTY}
          label="Largest payment"
          value={profile.largest ? formatEur(Math.abs(amountOf(profile.largest))) : EMPTY}
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
                href={`?view=${option.key}`}
                key={option.key}
              >
                {option.label}
              </a>
            ))}
          </nav>
        }
        subtitle="Click a bar to see everything you paid by card in that period."
        title="Over time"
      >
        <SpendingChart
          data={buckets.map((bucket) => ({
            key: bucket.key,
            label: bucket.label,
            spent: bucket.spent,
            count: bucket.count,
          }))}
          hrefPrefix={`/card?view=${granularity}&at=`}
        />
      </Panel>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Panel subtitle="Which days of the week the money goes out, in your local time." title="By weekday">
          <GroupBars
            groups={WEEKDAYS.map((day, index) => ({
              key: day,
              spent: profile.byWeekday[index],
              count: weekdayCounts[index],
              last: "",
            }))}
            total={profile.spent}
          />
        </Panel>
        <Panel subtitle="Which hour of the day, in your local time." title="By time of day">
          <SpendingChart
            data={profile.byHour.map((spent, hour) => ({
              label: `${String(hour).padStart(2, "0")}h`,
              spent,
            }))}
            height={220}
          />
        </Panel>
      </div>

      <Panel subtitle="Newest first, in your local time." title="Every payment">
        <DataTable
          caption={`Card payments to ${name}`}
          columns={columns}
          maxHeight={560}
          rowKey={(row) => row.rowId}
          rows={profile.transactions}
        />
      </Panel>
    </>
  );
}

function Stat({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className={`${CARD} flex flex-col gap-1.5 p-4`}>
      <span className="text-sm font-medium text-ink-3">{label}</span>
      <span className="text-2xl font-semibold tracking-tight text-ink">{value}</span>
      <span className="text-xs text-ink-3">{detail}</span>
    </div>
  );
}
