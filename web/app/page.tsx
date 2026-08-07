import { NavChart } from "@/components/charts/nav-chart";
import { DataTable, type Column } from "@/components/data-table";
import { HeroFigure, MetricTile } from "@/components/metric-tile";
import { Note, Panel, Unavailable } from "@/components/panel";
import { StatusBadge } from "@/components/status-badge";
import { AiPanel } from "@/components/ai-panel";
import { NewsFeed } from "@/components/news-feed";
import {
  getHealth,
  getLatestAiAnalysis,
  getNews,
  getPerformanceReport,
  getPositions,
} from "@/lib/api";
import {
  EMPTY,
  formatDateTime,
  formatEur,
  formatPercent,
  formatRatio,
  formatSignedPercent,
} from "@/lib/format";
import { latestValuedNav, toNavRows } from "@/lib/series";
import type { Position } from "@/lib/types";

export const dynamic = "force-dynamic";

interface TopHolding {
  ticker: string;
  name: string | null;
  value: string | null;
}

const TOP_HOLDING_COLUMNS: Column<TopHolding>[] = [
  { key: "ticker", header: "Ticker", render: (row) => row.ticker },
  {
    key: "name",
    header: "Instrument",
    render: (row) => <span className="text-neutral-500">{row.name ?? EMPTY}</span>,
  },
  {
    key: "value",
    header: "Account value",
    numeric: true,
    render: (row) => formatEur(row.value),
  },
];

export default async function OverviewPage() {
  const [health, report, positions, news, insight] = await Promise.all([
    getHealth(),
    getPerformanceReport(),
    getPositions(),
    getNews({ limit: 6 }),
    getLatestAiAnalysis(),
  ]);

  const navSeries = report.ok ? report.data.navSeries : [];
  const latestNav = latestValuedNav(navSeries);
  const navRows = report.ok ? toNavRows(navSeries, report.data.passiveCounterfactual.series) : [];
  const passiveLabel =
    report.ok && report.data.passiveCounterfactual.status === "ok"
      ? report.data.passiveCounterfactual.benchmarkLabel
      : null;

  const topHoldings: TopHolding[] = positions.ok
    ? [...positions.data]
        .sort((left, right) => walletValue(right) - walletValue(left))
        .slice(0, 5)
        .map((position) => ({
          ticker: position.instrument.ticker,
          name: position.instrument.name,
          value: position.walletImpact?.currentValue ?? null,
        }))
    : [];

  return (
    <>
      <section className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <div className="sheen panel-raised flex flex-col justify-between border border-border p-4 md:col-span-1">
          <HeroFigure
            caption={
              latestNav
                ? `Replayed NAV as of ${latestNav.asOfDate}`
                : "No valued NAV in the replay yet"
            }
            label="Portfolio NAV"
            value={latestNav ? formatEur(latestNav.navEur) : EMPTY}
          />
          <dl className="mt-6 grid grid-cols-2 gap-3 text-[11px]">
            <div>
              <dt className="text-neutral-600">Cash</dt>
              <dd className="mt-0.5 tabular-nums text-neutral-300">
                {latestNav ? formatEur(latestNav.cashBalanceEur) : EMPTY}
              </dd>
            </div>
            <div>
              <dt className="text-neutral-600">Securities</dt>
              <dd className="mt-0.5 tabular-nums text-neutral-300">
                {latestNav ? formatEur(latestNav.securitiesValueEur) : EMPTY}
              </dd>
            </div>
          </dl>
        </div>

        <div className="grid grid-cols-2 gap-4 md:col-span-2 lg:grid-cols-3">
          <MetricTile
            label="Cumulative TWR"
            metric={report.ok ? report.data.cumulativeTwr : null}
            render={(value) => formatSignedPercent(value)}
          />
          <MetricTile
            label="Annualized"
            metric={report.ok ? report.data.annualizedReturn : null}
            render={(value) => formatSignedPercent(value)}
          />
          <MetricTile
            label="XIRR"
            metric={report.ok ? report.data.xirr : null}
            render={(value) => formatSignedPercent(value)}
          />
          <MetricTile
            label="Volatility"
            metric={report.ok ? report.data.volatility : null}
            render={(value) => formatPercent(value)}
          />
          <MetricTile
            label="Sharpe"
            metric={report.ok ? report.data.sharpe : null}
            render={(value) => formatRatio(value)}
          />
          <MetricTile
            label="Max drawdown"
            metric={report.ok ? report.data.maxDrawdown : null}
            render={(value) => formatPercent(value)}
          />
        </div>
      </section>

      <Panel
        subtitle={
          passiveLabel
            ? `Replayed daily NAV against the ${passiveLabel} counterfactual. Gaps are days Helios refused to value.`
            : "Replayed daily NAV in EUR. Gaps are days Helios refused to value."
        }
        title="Net asset value"
      >
        {report.ok && navRows.length > 0 ? (
          <NavChart data={navRows} passiveLabel={passiveLabel} />
        ) : (
          <Unavailable
            detail={
              report.ok
                ? "Run a performance replay to reconstruct the daily NAV series."
                : report.error
            }
            reason={report.ok ? "No NAV history" : "Performance report unavailable"}
          />
        )}
      </Panel>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Panel subtitle="Live from Trading 212, by account value." title="Largest holdings">
          {positions.ok ? (
            <DataTable
              caption="Five largest holdings by account value"
              columns={TOP_HOLDING_COLUMNS}
              empty="No open positions"
              rowKey={(row) => row.ticker}
              rows={topHoldings}
            />
          ) : (
            <Unavailable detail={positions.error} reason="Positions unavailable" />
          )}
        </Panel>

        <Panel
          actions={
            <a
              className="text-[10px] uppercase tracking-wider text-neutral-500 hover:text-amber-accent"
              href="/news"
            >
              All news →
            </a>
          }
          subtitle="From the feeds you configured. Headlines link to the publisher."
          title="Latest news"
        >
          {news.ok ? (
            <NewsFeed compact items={news.data} />
          ) : (
            <Unavailable detail={news.error} reason="News unavailable" />
          )}
        </Panel>
      </div>

      {insight.ok && insight.data.status === "ok" ? (
        <Panel
          actions={
            <a
              className="text-[10px] uppercase tracking-wider text-neutral-500 hover:text-amber-accent"
              href="/insights"
            >
              Full analysis →
            </a>
          }
          subtitle="Claude describing your own analytics. Not advice."
          title="Latest insight"
        >
          <AiPanel
            analysis={{ ...insight.data, observations: insight.data.observations.slice(0, 3) }}
          />
        </Panel>
      ) : null}

      <Panel subtitle="Local stack reachability." title="System">
        <dl className="flex flex-col gap-2 text-xs">
          <div className="flex items-center justify-between gap-2">
            <dt className="text-neutral-500">API</dt>
            <dd>
              <StatusBadge
                label={health.ok ? health.data.status.toUpperCase() : "OFFLINE"}
                status={health.ok && health.data.status === "ok" ? "ok" : "failed"}
              />
            </dd>
          </div>
          <div className="flex items-center justify-between gap-2">
            <dt className="text-neutral-500">Database</dt>
            <dd>
              <StatusBadge
                label={health.ok && health.data.databaseReady ? "READY" : "UNAVAILABLE"}
                status={health.ok && health.data.databaseReady ? "ok" : "failed"}
              />
            </dd>
          </div>
          <div className="flex items-center justify-between gap-2">
            <dt className="text-neutral-500">Trading 212 uplink</dt>
            <dd>
              <StatusBadge
                label={health.ok && health.data.trading212Configured ? "CONFIGURED" : "PENDING"}
                status={health.ok && health.data.trading212Configured ? "ok" : "warning"}
              />
            </dd>
          </div>
          <div className="mt-2 flex items-center justify-between gap-2 border-t border-neutral-900 pt-2 text-[10px] text-neutral-600">
            <dt>Checked</dt>
            <dd className="tabular-nums">{formatDateTime(health.timestamp)}</dd>
          </div>
        </dl>
      {!health.ok ? <Note>{health.error}</Note> : null}
    </Panel>
    </>
  );
}

function walletValue(position: Position): number {
  const raw = position.walletImpact?.currentValue;
  if (!raw) return 0;
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? parsed : 0;
}
