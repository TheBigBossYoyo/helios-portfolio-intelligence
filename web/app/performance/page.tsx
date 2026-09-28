import { ActionButton } from "@/components/action-button";
import { AttributionChart } from "@/components/charts/attribution-chart";
import { ClusterChart } from "@/components/charts/cluster-chart";
import { ContributionChart } from "@/components/charts/contribution-chart";
import { DrawdownChart } from "@/components/charts/drawdown-chart";
import { GrowthChart } from "@/components/charts/growth-chart";
import { MonthlyChart } from "@/components/charts/monthly-chart";
import { NavChart } from "@/components/charts/nav-chart";
import { ReturnHistogram } from "@/components/charts/return-histogram";
import { RollingChart } from "@/components/charts/rolling-chart";
import { DataTable, type Column } from "@/components/data-table";
import { HoldingMovers } from "@/components/holding-movers";
import { MetricTile } from "@/components/metric-tile";
import { PeriodTable, signedEur } from "@/components/period-change";
import { Note, PageHeader, Panel, Unavailable } from "@/components/panel";
import { StatusBadge } from "@/components/status-badge";
import { replayPerformanceAction } from "@/lib/actions";
import { getAccountSummary, getNews, getPerformanceReport } from "@/lib/api";
import {
  formatDate,
  decimalToNumber,
  EMPTY,
  formatEur,
  formatPercent,
  formatRatio,
  formatSignedPercent,
  humanizeStatus,
  metricText,
} from "@/lib/format";
import {
  latestValuedNav,
  toDrawdownRows,
  toGrowthRows,
  toNavRows,
  toRollingRows,
} from "@/lib/series";
import type {
  AttributionItem,
  BenchmarkReport,
  ClusterAssignment,
  ContributionItem,
  MetricValue,
  NavPoint,
  PeriodSummary,
} from "@/lib/types";

export const dynamic = "force-dynamic";

/** The monthly chart's table twin: every bar's value, readable without hovering. */
const MONTH_COLUMNS: Column<PeriodSummary>[] = [
  { key: "month", header: "Month", render: (row) => row.label },
  {
    key: "result",
    header: "Investment result",
    numeric: true,
    render: (row) => (row.status === "ok" ? signedEur(decimalToNumber(row.investmentResultEur)) : EMPTY),
  },
  {
    key: "return",
    header: "Return",
    numeric: true,
    render: (row) => (row.twr !== null ? formatSignedPercent(row.twr) : EMPTY),
  },
  {
    key: "dividends",
    header: "Dividends",
    numeric: true,
    render: (row) => signedEur(decimalToNumber(row.dividendsEur)),
  },
  {
    key: "moved",
    header: "Money added",
    numeric: true,
    render: (row) => signedEur(decimalToNumber(row.netDepositsEur)),
  },
  {
    key: "end",
    header: "Value at month end",
    numeric: true,
    render: (row) => (row.status === "ok" ? formatEur(row.endValueEur) : EMPTY),
  },
];

const BENCHMARK_COLUMNS: Column<BenchmarkReport>[] = [
  { key: "label", header: "Proxy", render: (row) => row.benchmark.label },
  {
    key: "symbol",
    header: "Symbol",
    render: (row) => <span className="text-ink-3">{row.benchmark.providerSymbol}</span>,
  },
  { key: "beta", header: "Beta", numeric: true, render: (row) => cell(row.beta, formatRatio) },
  {
    key: "alpha",
    header: "Alpha",
    numeric: true,
    render: (row) => cell(row.alpha, (value) => formatSignedPercent(value)),
  },
  { key: "r2", header: "R²", numeric: true, render: (row) => cell(row.rSquared, formatRatio) },
  {
    key: "corr",
    header: "Correlation",
    numeric: true,
    render: (row) => cell(row.correlation, formatRatio),
  },
  {
    key: "te",
    header: "Tracking error",
    numeric: true,
    render: (row) => cell(row.trackingError, (value) => formatPercent(value)),
  },
  {
    key: "ir",
    header: "Info ratio",
    numeric: true,
    render: (row) => cell(row.informationRatio, formatRatio),
  },
];

const CONTRIBUTION_COLUMNS: Column<ContributionItem>[] = [
  { key: "key", header: "Holding", render: (row) => row.key },
  {
    key: "weight",
    header: "Weight",
    numeric: true,
    render: (row) => formatPercent(row.weight),
  },
  {
    key: "return",
    header: "Period return",
    numeric: true,
    render: (row) =>
      row.status === "ok" ? formatSignedPercent(row.returnValue, 3) : statusCell(row.status),
  },
  {
    key: "contribution",
    header: "Contribution",
    numeric: true,
    render: (row) =>
      row.status === "ok" ? formatSignedPercent(row.contribution, 3) : statusCell(row.status),
  },
];

const NAV_TABLE_COLUMNS: Column<NavPoint>[] = [
  { key: "date", header: "Date", render: (row) => row.asOfDate },
  { key: "nav", header: "NAV", numeric: true, render: (row) => formatEur(row.navEur) },
  {
    key: "cash",
    header: "Cash",
    numeric: true,
    render: (row) => formatEur(row.cashBalanceEur),
  },
  {
    key: "securities",
    header: "Securities",
    numeric: true,
    render: (row) => formatEur(row.securitiesValueEur),
  },
  {
    key: "flow",
    header: "External flow",
    numeric: true,
    render: (row) => formatEur(row.externalFlowEur),
  },
  {
    key: "status",
    header: "Status",
    render: (row) => <StatusBadge status={row.valuationStatus} />,
  },
];

const ATTRIBUTION_COLUMNS: Column<AttributionItem>[] = [
  { key: "key", header: "Sector", render: (row) => row.key },
  {
    key: "portfolioWeight",
    header: "Portfolio weight",
    numeric: true,
    render: (row) => formatPercent(row.portfolioWeight),
  },
  {
    key: "benchmarkWeight",
    header: "Benchmark weight",
    numeric: true,
    render: (row) => formatPercent(row.benchmarkWeight),
  },
  {
    key: "allocation",
    header: "Allocation",
    numeric: true,
    render: (row) => formatSignedPercent(row.allocationEffect),
  },
  {
    key: "selection",
    header: "Selection",
    numeric: true,
    render: (row) => formatSignedPercent(row.selectionEffect),
  },
  {
    key: "interaction",
    header: "Interaction",
    numeric: true,
    render: (row) => formatSignedPercent(row.interactionEffect),
  },
  {
    key: "total",
    header: "Total",
    numeric: true,
    render: (row) => formatSignedPercent(row.totalEffect),
  },
];

const CLUSTER_COLUMNS: Column<ClusterAssignment>[] = [
  { key: "key", header: "Holding", render: (row) => row.key },
  { key: "cluster", header: "Cluster", numeric: true, render: (row) => String(row.cluster) },
  {
    key: "weight",
    header: "Weight",
    numeric: true,
    render: (row) => formatPercent(row.weight),
  },
];

interface FactorRow {
  name: string;
  value: number | null;
}

const FACTOR_COLUMNS: Column<FactorRow>[] = [
  { key: "name", header: "Factor", render: (row) => row.name },
  {
    key: "value",
    header: "Coefficient",
    numeric: true,
    render: (row) => formatRatio(row.value, 3),
  },
];

export default async function PerformancePage({
  searchParams,
}: {
  searchParams: Promise<{ month?: string }>;
}) {
  const { month: requestedMonth } = await searchParams;
  const [result, account, news] = await Promise.all([
    getPerformanceReport(),
    getAccountSummary(),
    getNews({ limit: 200, heldOnly: true, mentionsOnly: true }),
  ]);

  if (!result.ok) {
    return (
      <>
        <PageHeader description="Reconstructed from your own ledger." title="Performance" />
        <Panel actions={<ReplayButton />} title="Performance report">
          <Unavailable
            detail={
              result.status === 404
                ? "No replayed NAV exists yet. Run a replay to build it."
                : result.error
            }
            reason="Performance report unavailable"
          />
        </Panel>
      </>
    );
  }

  const report = result.data;
  const navRows = toNavRows(report.navSeries, report.passiveCounterfactual.series);
  const growthRows = toGrowthRows(navRows);
  const drawdownRows = toDrawdownRows(report.navSeries, report.dailyTwr);
  const volatilityRows = toRollingRows(report.rollingVolatility30d, report.rollingVolatility90d);
  const betaRows = toRollingRows(report.rollingBeta30d, report.rollingBeta90d);
  const passive = report.passiveCounterfactual;
  // The replay values each day at that day's closing prices and ECB rates; Trading 212's total
  // is live. They should sit close together, and saying how close is what makes the chart
  // trustworthy rather than "a different number from the Overview".
  const lastValued = latestValuedNav(report.navSeries);
  const liveTotal = account.ok ? decimalToNumber(account.data.totalValue) : null;
  const replayedTotal = lastValued ? decimalToNumber(lastValued.navEur) : null;
  const passiveLabel = passive.status === "ok" ? passive.benchmarkLabel : null;
  const contributionBars = report.contributions
    .filter((item) => item.status === "ok" && item.contribution !== null)
    .map((item) => ({ key: item.key, contribution: item.contribution as number }));
  const dailyReturns = report.dailyTwr.map((point) => point.value);
  const hasGrowth = growthRows.some((row) => row.portfolio !== null);
  const periods = report.periodSummaries ?? [];
  const months = report.monthlySummaries ?? [];
  const valuedMonths = months.filter((month) => month.status === "ok");
  const selectedMonth =
    valuedMonths.find((month) => month.key === requestedMonth) ?? valuedMonths.at(-1) ?? null;
  const monthlyBars = months.map((month) => ({
    label: month.label,
    result: month.status === "ok" ? decimalToNumber(month.investmentResultEur) : null,
    moved: decimalToNumber(month.netDepositsEur) ?? 0,
  }));
  const factorRows: FactorRow[] = Object.entries(report.ff5MomentumRegression.coefficients).map(
    ([name, value]) => ({ name, value }),
  );

  return (
    <>
      <PageHeader
        actions={<ReplayButton />}
        description={
          <>
            Window {report.startDate ?? EMPTY} → {report.endDate ?? EMPTY} · flow timing{" "}
            <span className="text-ink-2">{report.flowTiming}</span> · annualised on{" "}
            <span className="text-ink-2">{report.annualizationDays}</span> calendar days
          </>
        }
        title="Performance"
      />

      <section
        aria-label="Headline metrics"
        className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5"
      >
        <MetricTile
          label="Cumulative TWR"
          metric={report.cumulativeTwr}
          render={(value) => formatSignedPercent(value)}
          signed
        />
        <MetricTile
          label="Annualized"
          metric={report.annualizedReturn}
          render={(value) => formatSignedPercent(value)}
          signed
        />
        <MetricTile
          label="Money-weighted (XIRR)"
          metric={report.xirr}
          render={(value) => formatSignedPercent(value)}
          signed
        />
        <MetricTile
          label="Volatility"
          metric={report.volatility}
          render={(value) => formatPercent(value)}
        />
        <MetricTile
          label="Downside vol"
          metric={report.downsideVolatility}
          render={(value) => formatPercent(value)}
        />
        <MetricTile label="Sharpe" metric={report.sharpe} render={(value) => formatRatio(value)} />
        <MetricTile
          label="Sortino"
          metric={report.sortino}
          render={(value) => formatRatio(value)}
        />
        <MetricTile label="Calmar" metric={report.calmar} render={(value) => formatRatio(value)} />
        <MetricTile
          label="Max drawdown"
          metric={report.maxDrawdown}
          render={(value) => formatPercent(value)}
        />
        <MetricTile
          label="Time underwater"
          metric={report.timeUnderwaterDays}
          render={(value) => `${value.toFixed(0)} days`}
        />
      </section>

      {periods.length > 0 ? (
        <Panel
          subtitle="What your investments made in each period, kept apart from the money you moved in or out. Open a period on the Overview for its full breakdown."
          title="Returns by period"
        >
          <PeriodTable basePath="/" periods={periods} selected="" />
        </Panel>
      ) : null}

      {months.length > 0 ? (
        <Panel
          subtitle="Each month: what the investments made or lost (blue up, red down) beside the money you added. A big deposit is not a good month."
          title="Month by month"
        >
          <MonthlyChart data={monthlyBars} />
          {selectedMonth ? (
            <div className="mt-6 flex flex-col gap-4 border-t border-border pt-5" id="month-detail">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h3 className="text-sm font-semibold text-ink">
                  {selectedMonth.label}, stock by stock
                </h3>
                <nav aria-label="Month" className="flex flex-wrap gap-1 rounded-xl bg-surface-3 p-1">
                  {valuedMonths.map((month) => (
                    <a
                      aria-current={month.key === selectedMonth.key ? "true" : undefined}
                      className={`rounded-lg px-2.5 py-1 text-xs font-medium transition-colors ${
                        month.key === selectedMonth.key
                          ? "bg-surface text-ink shadow-card"
                          : "text-ink-3 hover:text-ink"
                      }`}
                      href={`/performance?month=${month.key}#month-detail`}
                      key={month.key}
                    >
                      {month.label}
                    </a>
                  ))}
                </nav>
              </div>
              <HoldingMovers news={news.ok ? news.data : []} period={selectedMonth} />
            </div>
          ) : null}
          <details className="mt-4">
            <summary className="cursor-pointer text-sm font-medium text-ink-2">Show as a table</summary>
            <div className="mt-3">
              <DataTable
                caption="Investment result and money added by month"
                columns={MONTH_COLUMNS}
                maxHeight={360}
                rowKey={(row) => row.key}
                rows={[...months].reverse()}
              />
            </div>
          </details>
        </Panel>
      ) : null}

      <Panel
        subtitle={
          passiveLabel
            ? `Daily NAV against the ${passiveLabel} counterfactual — your own external contributions invested in the proxy at each flow date.`
            : "Replayed daily NAV in EUR. Gaps are days Helios refused to value."
        }
        title="Net asset value"
      >
        {navRows.length > 0 ? (
          <NavChart data={navRows} passiveLabel={passiveLabel} showInvested />
        ) : (
          <Unavailable reason="No NAV history" />
        )}
        {liveTotal !== null && replayedTotal !== null && lastValued ? (
          <div className="mt-4">
            <Note>
              Reconstructed value on {formatDate(lastValued.asOfDate)}:{" "}
              <span className="font-medium text-ink">{formatEur(replayedTotal)}</span>, at that
              day&apos;s closing prices and ECB rates. Trading 212 reports{" "}
              <span className="font-medium text-ink">{formatEur(liveTotal)}</span> right now —{" "}
              {formatEur(Math.abs(liveTotal - replayedTotal))} apart, from price movement since the
              close and Trading 212&apos;s own exchange rates.
            </Note>
          </div>
        ) : null}
        {passive.status === "ok" ? (
          <dl className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Figure label="Contributed" value={formatEur(passive.investedEur)} />
            <Figure label="Passive would be" value={formatEur(passive.finalValueEur)} />
            <Figure label="Actual NAV" value={formatEur(passive.actualNavEur)} />
            <Figure
              label="Difference"
              tone={
                passive.differenceEur === null
                  ? undefined
                  : passive.differenceEur.trim().startsWith("-")
                    ? "down"
                    : "up"
              }
              value={formatEur(passive.differenceEur)}
            />
          </dl>
        ) : (
          <div className="mt-4">
            <Note>
              Passive counterfactual {humanizeStatus(passive.status).toLowerCase()}
              {passive.detail ? ` — ${passive.detail}` : "."}
            </Note>
          </div>
        )}
      </Panel>

      <Panel
        subtitle={
          passiveLabel
            ? `Portfolio vs. ${passiveLabel}, each indexed to its own first observed value = 100 — the honest way to compare a EUR NAV against an ETF proxy price on one scale.`
            : "Portfolio NAV indexed to its first valued day = 100. No configured benchmark proxy to compare against."
        }
        title="Growth of €100"
      >
        {hasGrowth ? (
          <GrowthChart benchmarkLabel={passiveLabel} data={growthRows} />
        ) : (
          <Unavailable reason="No NAV history yet" />
        )}
      </Panel>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <Panel subtitle="Decline from the running peak of valued NAV." title="Drawdown">
          {drawdownRows.some((row) => row.drawdown !== null) ? (
            <DrawdownChart data={drawdownRows} />
          ) : (
            <Unavailable reason="No valued NAV history" />
          )}
          <dl className="mt-4 grid grid-cols-3 gap-3">
            <Figure
              label="Max drawdown"
              value={metricText(report.maxDrawdown, (value) => formatPercent(value))}
            />
            <Figure
              label="Underwater days"
              value={metricText(report.timeUnderwaterDays, (value) => formatRatio(value, 0))}
            />
            <Figure
              label="Recovery days"
              value={metricText(report.recoveryDays, (value) => formatRatio(value, 0))}
            />
          </dl>
        </Panel>

        <Panel
          subtitle="Historical simulation on daily time-weighted returns; losses are negative."
          title="Daily return distribution"
        >
          {dailyReturns.length >= 8 ? (
            <ReturnHistogram
              returns={dailyReturns}
              var95={report.var95_1d.status === "ok" ? report.var95_1d.value : null}
              var99={report.var99_1d.status === "ok" ? report.var99_1d.value : null}
            />
          ) : (
            <Unavailable detail="Needs at least 8 daily return observations." reason="Not enough history" />
          )}
          <dl className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Figure label="VaR 95% 1d" value={metricText(report.var95_1d, (value) => formatPercent(value))} />
            <Figure label="CVaR 95% 1d" value={metricText(report.cvar95_1d, (value) => formatPercent(value))} />
            <Figure label="VaR 99% 10d" value={metricText(report.var99_10d, (value) => formatPercent(value))} />
            <Figure label="CVaR 99% 10d" value={metricText(report.cvar99_10d, (value) => formatPercent(value))} />
          </dl>
          {report.var95_1d.detail ? (
            <div className="mt-4">
              <Note>{report.var95_1d.detail}</Note>
            </div>
          ) : null}
        </Panel>
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <Panel subtitle="Annualised, 30- and 90-day windows." title="Rolling volatility">
          {volatilityRows.length > 0 ? (
            <RollingChart
              data={volatilityRows}
              longLabel="90-day"
              shortLabel="30-day"
              valueFormat="percent"
            />
          ) : (
            <Unavailable
              detail="Needs at least 30 aligned return observations."
              reason="Not enough history"
            />
          )}
        </Panel>

        <Panel
          subtitle="Against the configured passive proxy, 30- and 90-day windows."
          title="Rolling beta"
        >
          {betaRows.length > 0 ? (
            <RollingChart
              data={betaRows}
              longLabel="90-day"
              shortLabel="30-day"
              valueFormat="ratio"
            />
          ) : (
            <Unavailable
              detail="Needs aligned benchmark returns, which require a configured proxy currency and market-data provider."
              reason="Not enough aligned history"
            />
          )}
        </Panel>
      </div>

      <Panel
        subtitle="Weight × period return per holding, over the most recent valued day."
        title="Contribution"
      >
        {contributionBars.length > 0 ? (
          <ContributionChart data={contributionBars} />
        ) : (
          <Unavailable reason="No holding has two consecutive valued observations" />
        )}
        <div className="mt-4">
          <DataTable
            caption="Contribution by holding"
            columns={CONTRIBUTION_COLUMNS}
            empty="No holdings"
            rowKey={(row) => row.key}
            rows={report.contributions}
          />
        </div>
      </Panel>

      <Panel subtitle="Labelled ETF proxies, not official index levels." title="Benchmark comparison">
        <DataTable
          caption="Benchmark statistics"
          columns={BENCHMARK_COLUMNS}
          empty="No benchmarks configured"
          rowKey={(row) => row.benchmark.key}
          rows={report.betaVsBenchmarks}
        />
      </Panel>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <Panel subtitle="Concentration of the latest valued weights." title="Concentration">
          <div className="grid grid-cols-3 gap-4">
            <MetricTile label="HHI" metric={report.hhi} render={(value) => formatRatio(value, 3)} />
            <MetricTile
              label="Effective positions"
              metric={report.effectiveNumberOfPositions}
              render={(value) => formatRatio(value, 1)}
            />
            <MetricTile
              label="Top 5 weight"
              metric={report.top5Weight}
              render={(value) => formatPercent(value)}
            />
          </div>
        </Panel>

        <Panel
          subtitle="Average-linkage clustering on correlation distance of daily EUR unit-price returns."
          title="Correlation clusters"
        >
          {report.correlationClusters.status === "ok" ? (
            <div className="flex flex-col gap-4">
              {report.correlationClusters.assignments.length > 0 ? (
                <ClusterChart assignments={report.correlationClusters.assignments} />
              ) : null}
              <DataTable
                caption="Correlation cluster assignments"
                columns={CLUSTER_COLUMNS}
                empty="No clustered holdings"
                rowKey={(row) => row.key}
                rows={report.correlationClusters.assignments}
              />
              {report.correlationClusters.detail ? <Note>{report.correlationClusters.detail}</Note> : null}
            </div>
          ) : (
            <Unavailable
              detail={report.correlationClusters.detail}
              reason={humanizeStatus(report.correlationClusters.status)}
            />
          )}
        </Panel>
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <Panel subtitle="Brinson-Fachler sector attribution." title="Attribution">
          {report.attribution.status === "ok" ? (
            <div className="flex flex-col gap-4">
              <Figure
                label="Active return"
                value={formatSignedPercent(report.attribution.activeReturn)}
              />
              {report.attribution.items.length > 0 ? (
                <AttributionChart data={report.attribution.items} />
              ) : null}
              <DataTable
                caption="Sector attribution effects"
                columns={ATTRIBUTION_COLUMNS}
                empty="No sectors"
                rowKey={(row) => row.key}
                rows={report.attribution.items}
              />
              {report.attribution.detail ? <Note>{report.attribution.detail}</Note> : null}
            </div>
          ) : (
            <Unavailable
              detail={report.attribution.detail}
              reason={humanizeStatus(report.attribution.status)}
            />
          )}
        </Panel>

        <Panel
          subtitle="Excess returns regressed on Fama-French 5 factors plus momentum."
          title="Factor exposure"
        >
          {report.ff5MomentumRegression.status === "ok" ? (
            <div className="flex flex-col gap-4">
              <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                <Figure label="R²" value={formatRatio(report.ff5MomentumRegression.rSquared, 3)} />
                <Figure
                  label="Observations"
                  value={formatRatio(report.ff5MomentumRegression.observations, 0)}
                />
                <Figure
                  label="Intercept"
                  value={formatRatio(report.ff5MomentumRegression.intercept, 3)}
                />
              </dl>
              <DataTable
                caption="Factor regression coefficients"
                columns={FACTOR_COLUMNS}
                empty="No factors"
                rowKey={(row) => row.name}
                rows={factorRows}
              />
            </div>
          ) : (
            <Unavailable
              detail={report.ff5MomentumRegression.detail}
              reason={humanizeStatus(report.ff5MomentumRegression.status)}
            />
          )}
        </Panel>
      </div>

      <Panel subtitle="How these numbers were produced." title="Method notes">
        <ul className="flex flex-col gap-2">
          {report.notes.map((note) => (
            <li key={note}>
              <Note>{note}</Note>
            </li>
          ))}
        </ul>
      </Panel>

      <Panel
        subtitle={`Every charted value, in full — ${report.navSeries.length} days. Scrolls inside the panel.`}
        title="Daily NAV table"
      >
        <DataTable
          caption="Replayed daily NAV"
          columns={NAV_TABLE_COLUMNS}
          empty="No NAV history"
          maxHeight={420}
          rowKey={(row) => row.asOfDate}
          rows={report.navSeries}
        />
      </Panel>
    </>
  );
}

/**
 * A replay rewrites the whole daily NAV table and refetches every price against a metered
 * quota, so it asks before spending that. The backend holds a lease and refuses a concurrent
 * run, but the UI should not invite the second click in the first place.
 */
function ReplayButton() {
  return (
    <ActionButton
      action={replayPerformanceAction}
      confirmLabel="Confirm replay"
      label="Replay"
      pendingLabel="Replaying…"
    />
  );
}

function Figure({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "up" | "down";
}) {
  return (
    <div>
      <dt className="text-xs font-medium text-ink-3">{label}</dt>
      <dd
        className={`tabular mt-0.5 text-sm font-semibold ${
          tone === "up" ? "text-positive" : tone === "down" ? "text-negative" : "text-ink"
        }`}
      >
        {value}
      </dd>
    </div>
  );
}

function cell(metric: MetricValue, render: (value: number) => string) {
  return metric.status === "ok" ? metricText(metric, render) : statusCell(metric.status);
}

function statusCell(status: string) {
  return <span className="text-ink-3">{humanizeStatus(status)}</span>;
}
