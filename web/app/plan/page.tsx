import { CashflowChart } from "@/components/charts/cashflow-chart";
import { ProjectionChart } from "@/components/charts/projection-chart";
import { DataTable, type Column } from "@/components/data-table";
import { Note, PageHeader, Panel, Unavailable } from "@/components/panel";
import { getAccountSummary, getPerformanceReport } from "@/lib/api";
import { decimalToNumber, formatEur, formatPercent } from "@/lib/format";
import { type CashflowMonth, type ProjectionPoint, averageOf, project } from "@/lib/projection";
import { latestValuedNav } from "@/lib/series";
import { BUTTON, CARD, FIELD, LABEL } from "@/lib/ui";

export const dynamic = "force-dynamic";

const RECENT_MONTHS = 6;
const SHOWN_MONTHS = 12;
const DEFAULT_RETURN = 0.06;
const DEFAULT_VOLATILITY = 0.15;
const DEFAULT_YEARS = 10;

function number(value: string | undefined, fallback: number, min: number, max: number): number {
  const parsed = value === undefined || value.trim() === "" ? Number.NaN : Number(value.replace(",", "."));
  return Number.isFinite(parsed) ? Math.min(Math.max(parsed, min), max) : fallback;
}

const CASHFLOW_COLUMNS: Column<CashflowMonth>[] = [
  { key: "month", header: "Month", render: (row) => row.label },
  { key: "in", header: "Deposited", numeric: true, render: (row) => formatEur(row.deposited) },
  { key: "card", header: "Spent by card", numeric: true, render: (row) => formatEur(row.spentByCard) },
  { key: "bank", header: "To your bank", numeric: true, render: (row) => formatEur(row.withdrawnToBank) },
  {
    key: "kept",
    header: "Kept invested",
    numeric: true,
    render: (row) => (
      <span className={row.kept < 0 ? "text-negative" : "font-medium text-ink"}>{formatEur(row.kept)}</span>
    ),
  },
];

interface Milestone {
  years: number;
  point: ProjectionPoint;
}

const MILESTONE_COLUMNS: Column<Milestone>[] = [
  { key: "when", header: "In", render: (row) => `${row.years} year${row.years === 1 ? "" : "s"}` },
  { key: "low", header: "1 in 10 below", numeric: true, render: (row) => formatEur(row.point.low) },
  {
    key: "median",
    header: "Middle outcome",
    numeric: true,
    render: (row) => <span className="font-medium text-ink">{formatEur(row.point.median)}</span>,
  },
  { key: "high", header: "1 in 10 above", numeric: true, render: (row) => formatEur(row.point.high) },
  { key: "in", header: "Money put in", numeric: true, render: (row) => formatEur(row.point.contributed) },
];

export default async function PlanPage({
  searchParams,
}: {
  searchParams: Promise<{ monthly?: string; years?: string; ret?: string; vol?: string }>;
}) {
  const params = await searchParams;
  const [report, account] = await Promise.all([getPerformanceReport(), getAccountSummary()]);

  if (!report.ok) {
    return (
      <>
        <PageHeader title="Plan" />
        <Panel title="Savings and projections">
          <Unavailable detail={report.error} reason="Replay your history first" />
        </Panel>
      </>
    );
  }

  const months: CashflowMonth[] = (report.data.monthlySummaries ?? [])
    .map((month) => {
      const withdrawals = -(decimalToNumber(month.withdrawalsEur) ?? 0);
      const card = -(decimalToNumber(month.cardSpendingEur ?? null) ?? 0);
      return {
        key: month.key,
        label: month.label,
        deposited: decimalToNumber(month.depositsEur) ?? 0,
        spentByCard: card,
        withdrawnToBank: Math.max(withdrawals - card, 0),
        kept: decimalToNumber(month.netDepositsEur) ?? 0,
      };
    })
    .slice(-SHOWN_MONTHS);
  // The month in progress is partial; the averages use the last full months.
  const full = months.slice(0, -1).slice(-RECENT_MONTHS);
  const avgIn = averageOf(full, "deposited");
  const avgCard = averageOf(full, "spentByCard");
  const avgKept = averageOf(full, "kept");

  const liveTotal = account.ok ? decimalToNumber(account.data.totalValue) : null;
  const startValue = liveTotal ?? decimalToNumber(latestValuedNav(report.data.navSeries)?.navEur ?? null) ?? 0;
  const realisedVolatility =
    report.data.volatility.status === "ok" ? report.data.volatility.value : null;

  const monthly = number(params.monthly, Math.max(0, Math.round(avgKept)), 0, 100_000);
  const years = Math.round(number(params.years, DEFAULT_YEARS, 1, 40));
  const annualReturn = number(params.ret, DEFAULT_RETURN * 100, -10, 20) / 100;
  const annualVolatility =
    number(
      params.vol,
      Math.round(Math.min(Math.max(realisedVolatility ?? DEFAULT_VOLATILITY, 0.05), 0.4) * 100),
      0,
      80,
    ) / 100;
  const points = project({ startValue, monthlyContribution: monthly, annualReturn, annualVolatility, years });
  const end = points.at(-1);
  const milestones: Milestone[] = [1, 3, 5, 10, 20, 30]
    .filter((year) => year <= years)
    .map((year) => ({ years: year, point: points[year * 12] }));
  if (!milestones.some((item) => item.years === years) && end) milestones.push({ years, point: end });

  return (
    <>
      <PageHeader
        description="How much you put in, how much the card takes back out, and where steady saving could take the portfolio. Projections follow from the assumptions you set below; they are not forecasts or advice."
        title="Plan"
      />

      <section aria-label="Monthly averages" className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat detail={`Average of the last ${full.length} full month(s)`} label="Deposited a month" value={formatEur(avgIn)} />
        <Stat detail="Card payments from the same account" label="Spent by card a month" value={formatEur(avgCard)} />
        <Stat detail="Deposited minus everything that left" label="Kept invested a month" value={formatEur(avgKept)} />
        <Stat
          detail="of every €100 deposited went back out by card"
          label="Card share"
          value={avgIn > 0 ? formatPercent(avgCard / avgIn, 0) : "—"}
        />
      </section>

      <Panel subtitle="Month by month: money in, card spending out, and what stayed invested." title="Money in and out">
        {months.length > 0 ? <CashflowChart data={months} /> : <Unavailable reason="No months yet" />}
        <details className="mt-4">
          <summary className="cursor-pointer text-sm font-medium text-ink-2">Show as a table</summary>
          <div className="mt-3">
            <DataTable caption="Money in and out by month" columns={CASHFLOW_COLUMNS} rowKey={(row) => row.key} rows={[...months].reverse()} />
          </div>
        </details>
      </Panel>

      <Panel
        subtitle={`Starting from ${formatEur(startValue)} today, adding ${formatEur(monthly)} a month for ${years} years, at ${formatPercent(annualReturn, 1)} a year expected and ${formatPercent(annualVolatility, 0)} volatility.`}
        title="Where it could go"
      >
        <form className="mb-5 grid grid-cols-2 items-end gap-3 sm:grid-cols-5" method="get">
          <label className={LABEL}>
            Add a month (€)
            <input className={`${FIELD} tabular-nums`} defaultValue={monthly} inputMode="decimal" name="monthly" />
          </label>
          <label className={LABEL}>
            Years
            <input className={`${FIELD} tabular-nums`} defaultValue={years} inputMode="numeric" name="years" />
          </label>
          <label className={LABEL}>
            Expected return (% a year)
            <input className={`${FIELD} tabular-nums`} defaultValue={(annualReturn * 100).toFixed(1)} inputMode="decimal" name="ret" />
          </label>
          <label className={LABEL}>
            Volatility (% a year)
            <input className={`${FIELD} tabular-nums`} defaultValue={Math.round(annualVolatility * 100)} inputMode="decimal" name="vol" />
          </label>
          <button className={`${BUTTON.primary} ${BUTTON.small} h-10`} type="submit">
            Update
          </button>
        </form>
        <ProjectionChart data={points} />
        <div className="mt-5">
          <DataTable caption="Projected value at milestones" columns={MILESTONE_COLUMNS} rowKey={(row) => String(row.years)} rows={milestones} />
        </div>
        <div className="mt-4">
          <Note>
            The monthly amount starts at what you kept invested on average; the volatility at your
            portfolio&apos;s own ({realisedVolatility !== null ? formatPercent(realisedVolatility, 0) : "not measured yet"}).
            The expected return is yours to choose: 6% is a common long-run assumption for a
            stock-heavy portfolio. Your own return so far is not a guide to the next ten years.
            Each run simulates 2,000 paths; the band holds 8 in 10 of them.
          </Note>
        </div>
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
