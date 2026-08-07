import { ContributionChart } from "@/components/charts/contribution-chart";
import { DrawdownChart } from "@/components/charts/drawdown-chart";
import { NavChart } from "@/components/charts/nav-chart";
import { RollingChart } from "@/components/charts/rolling-chart";
import { DataTable, type Column } from "@/components/data-table";
import { MetricTile } from "@/components/metric-tile";
import { Note, Panel, Unavailable } from "@/components/panel";
import { StatusBadge } from "@/components/status-badge";
import { getPerformanceReport } from "@/lib/api";
import {
  EMPTY,
  formatEur,
  formatPercent,
  formatRatio,
  formatSignedPercent,
  humanizeStatus,
  metricText,
} from "@/lib/format";
import { toDrawdownRows, toNavRows, toRollingRows } from "@/lib/series";
import type {
  BenchmarkReport,
  ClusterAssignment,
  ContributionItem,
  MetricValue,
  NavPoint,
} from "@/lib/types";

export const dynamic = "force-dynamic";

const BENCHMARK_COLUMNS: Column<BenchmarkReport>[] = [
  { key: "label", header: "Proxy", render: (row) => row.benchmark.label },
  {
    key: "symbol",
    header: "Symbol",
    render: (row) => <span className="text-neutral-600">{row.benchmark.providerSymbol}</span>,
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

export default async function PerformancePage() {
  const result = await getPerformanceReport();

  if (!result.ok) {
    return (
      <Panel subtitle="Reconstructed from your own ledger." title="Performance">
        <Unavailable
          detail={
            result.status === 404
              ? "No replayed NAV exists yet. Run `helios performance-replay` (or POST /api/v1/performance/replay) to build it."
              : result.error
          }
          reason="Performance report unavailable"
        />
      </Panel>
    );
  }

  const report = result.data;
  const navRows = toNavRows(report.navSeries, report.passiveCounterfactual.series);
  const drawdownRows = toDrawdownRows(report.navSeries);
  const volatilityRows = toRollingRows(report.rollingVolatility30d, report.rollingVolatility90d);
  const betaRows = toRollingRows(report.rollingBeta30d, report.rollingBeta90d);
  const passive = report.passiveCounterfactual;
  const passiveLabel = passive.status === "ok" ? passive.benchmarkLabel : null;
  const contributionBars = report.contributions
    .filter((item) => item.status === "ok" && item.contribution !== null)
    .map((item) => ({ key: item.key, contribution: item.contribution as number }));

  return (
    <>
      <section className="panel-raised flex flex-col gap-2 border border-border px-4 py-3 text-[11px] text-neutral-500 sm:flex-row sm:items-center sm:justify-between">
        <span>
          Window {report.startDate ?? EMPTY} → {report.endDate ?? EMPTY}
        </span>
        <span>
          Flow timing <span className="text-neutral-300">{report.flowTiming}</span> · annualised on{" "}
          <span className="text-neutral-300">{report.annualizationDays}</span> calendar days
        </span>
      </section>

      <section className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <MetricTile
          label="Cumulative TWR"
          metric={report.cumulativeTwr}
          render={(value) => formatSignedPercent(value)}
        />
        <MetricTile
          label="Annualized"
          metric={report.annualizedReturn}
          render={(value) => formatSignedPercent(value)}
        />
        <MetricTile
          label="XIRR"
          metric={report.xirr}
          render={(value) => formatSignedPercent(value)}
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
      </section>

      <Panel
        subtitle={
          passiveLabel
            ? `Daily NAV against the ${passiveLabel} counterfactual — your own external contributions invested in the proxy at each flow date.`
            : "Replayed daily NAV in EUR. Gaps are days Helios refused to value."
        }
        title="Net asset value"
      >
        {navRows.length > 0 ? (
          <NavChart data={navRows} passiveLabel={passiveLabel} />
        ) : (
          <Unavailable reason="No NAV history" />
        )}
        {passive.status === "ok" ? (
          <dl className="mt-4 grid grid-cols-2 gap-3 text-[11px] sm:grid-cols-4">
            <Figure label="Contributed" value={formatEur(passive.investedEur)} />
            <Figure label="Passive would be" value={formatEur(passive.finalValueEur)} />
            <Figure label="Actual NAV" value={formatEur(passive.actualNavEur)} />
            <Figure label="Difference" value={formatEur(passive.differenceEur)} />
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

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <Panel
          subtitle="Decline from the running peak of valued NAV."
          title="Drawdown"
        >
          {drawdownRows.some((row) => row.drawdown !== null) ? (
            <DrawdownChart data={drawdownRows} />
          ) : (
            <Unavailable reason="No valued NAV history" />
          )}
          <dl className="mt-4 grid grid-cols-3 gap-3 text-[11px]">
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

        <Panel subtitle="Historical simulation; losses are negative returns." title="Value at risk">
          <div className="grid grid-cols-2 gap-4">
            <MetricTile
              label="VaR 95% 1d"
              metric={report.var95_1d}
              render={(value) => formatPercent(value)}
            />
            <MetricTile
              label="CVaR 95% 1d"
              metric={report.cvar95_1d}
              render={(value) => formatPercent(value)}
            />
            <MetricTile
              label="VaR 99% 10d"
              metric={report.var99_10d}
              render={(value) => formatPercent(value)}
            />
            <MetricTile
              label="CVaR 99% 10d"
              metric={report.cvar99_10d}
              render={(value) => formatPercent(value)}
            />
          </div>
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

      <Panel
        subtitle="Labelled ETF proxies, not official index levels."
        title="Benchmark comparison"
      >
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
            <MetricTile
              label="HHI"
              metric={report.hhi}
              render={(value) => formatRatio(value, 3)}
            />
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
            <DataTable
              caption="Correlation cluster assignments"
              columns={CLUSTER_COLUMNS}
              rowKey={(row) => row.key}
              rows={report.correlationClusters.assignments}
            />
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
          <Unavailable
            detail={report.attribution.detail}
            reason={humanizeStatus(report.attribution.status)}
          />
        </Panel>

        <Panel
          subtitle="Excess returns regressed on Fama-French 5 factors plus momentum."
          title="Factor exposure"
        >
          {report.ff5MomentumRegression.status === "ok" ? (
            <dl className="grid grid-cols-2 gap-3 text-[11px] sm:grid-cols-4">
              <Figure
                label="R²"
                value={formatRatio(report.ff5MomentumRegression.rSquared, 3)}
              />
              {Object.entries(report.ff5MomentumRegression.coefficients).map(([name, value]) => (
                <Figure key={name} label={name} value={formatRatio(value, 3)} />
              ))}
            </dl>
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

function Figure({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-neutral-600">{label}</dt>
      <dd className="mt-0.5 tabular-nums text-neutral-300">{value}</dd>
    </div>
  );
}

function cell(metric: MetricValue, render: (value: number) => string) {
  return metric.status === "ok" ? metricText(metric, render) : statusCell(metric.status);
}

function statusCell(status: string) {
  return <span className="text-neutral-600">{humanizeStatus(status)}</span>;
}
