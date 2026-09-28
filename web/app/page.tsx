import Link from "next/link";
import {
  ArrowUpRight,
  BrainCircuit,
  Database,
  History,
  Newspaper,
  RefreshCw,
  Server,
} from "lucide-react";
import type { ReactNode } from "react";
import { ActionButton } from "@/components/action-button";
import { AiPanel } from "@/components/ai-panel";
import { AllocationDonut } from "@/components/charts/allocation-donut";
import { BarList } from "@/components/charts/bar-list";
import { NavChart } from "@/components/charts/nav-chart";
import { HoldingMovers } from "@/components/holding-movers";
import {
  ChangeBreakdown,
  PeriodHeadline,
  PeriodTable,
  PeriodTabs,
  periodRange,
} from "@/components/period-change";
import { MetricTile } from "@/components/metric-tile";
import { NewsFeed } from "@/components/news-feed";
import { Note, PageHeader, Panel, Unavailable } from "@/components/panel";
import { SetupChecklist, type SetupStep } from "@/components/setup-checklist";
import { StatusBadge } from "@/components/status-badge";
import {
  analyseWithAiAction,
  replayPerformanceAction,
  syncNewsAction,
  syncPortfolioAction,
} from "@/lib/actions";
import {
  getAccountSummary,
  getHealth,
  getLatestAiAnalysis,
  getNews,
  getPerformanceReport,
  getPositions,
  getQualityReport,
} from "@/lib/api";
import {
  EMPTY,
  decimalToNumber,
  formatDate,
  formatDateTime,
  formatDay,
  formatEur,
  formatPercent,
  formatRatio,
  formatSignedPercent,
  displayTicker,
  holdingHref,
} from "@/lib/format";
import {
  compoundReturns,
  latestValuedNav,
  rowsInPeriod,
  tail,
  toDrawdownRows,
  toNavRows,
} from "@/lib/series";
import type { NewsItem, Position } from "@/lib/types";
import { CARD, LINK } from "@/lib/ui";

export const dynamic = "force-dynamic";

/** Sparklines show the recent shape: roughly the last quarter of daily observations. */
const TREND_POINTS = 90;

/** The period the Overview opens on: long enough to be meaningful, short enough to be current. */
const DEFAULT_PERIOD = "1M";

export default async function OverviewPage({
  searchParams,
}: {
  searchParams: Promise<{ period?: string }>;
}) {
  const { period: requestedPeriod } = await searchParams;
  const [health, report, positions, news, insight, quality, account] = await Promise.all([
    getHealth(),
    getPerformanceReport(),
    getPositions(),
    // Stories that name a holding you still own: the Overview's news and the "why" line under
    // each mover. Read deep enough that quieter holdings still have their latest story.
    getNews({ limit: 200, heldOnly: true, mentionsOnly: true }),
    getLatestAiAnalysis(),
    getQualityReport(),
    getAccountSummary(),
  ]);

  const navSeries = report.ok ? report.data.navSeries : [];
  const latestNav = latestValuedNav(navSeries);
  const navRows = report.ok ? toNavRows(navSeries, report.data.passiveCounterfactual.series) : [];

  const periods = report.ok ? (report.data.periodSummaries ?? []) : [];
  const selectedPeriod =
    periods.find((item) => item.key === (requestedPeriod ?? "").toUpperCase()) ??
    periods.find((item) => item.key === DEFAULT_PERIOD) ??
    periods[0] ??
    null;
  const periodRows = selectedPeriod
    ? rowsInPeriod(navRows, selectedPeriod.startDate, selectedPeriod.endDate)
    : navRows;

  const held = positions.ok ? positions.data.filter((position) => walletValue(position) > 0) : [];
  const invested = held.reduce((sum, position) => sum + walletValue(position), 0);
  const unrealised = held.reduce(
    (sum, position) => sum + (decimalToNumber(position.walletImpact?.unrealizedProfitLoss) ?? 0),
    0,
  );
  const largest = [...held].sort((left, right) => walletValue(right) - walletValue(left)).slice(0, 6);

  // Every "now" figure comes from Trading 212's own account summary, so this page, Holdings and
  // the allocation ring show one set of numbers -- the ones in the Trading 212 app. Positions
  // are used only per holding (weights, names); the replay only for history.
  const summary = account.ok ? account.data : null;
  const liveTotal = decimalToNumber(summary?.totalValue);
  const liveHoldings = decimalToNumber(summary?.investments?.currentValue) ?? (positions.ok ? invested : null);
  const liveCash = liveTotal !== null && liveHoldings !== null ? liveTotal - liveHoldings : null;
  const liveUnrealised =
    decimalToNumber(summary?.investments?.unrealizedProfitLoss) ?? (positions.ok ? unrealised : null);

  const twrTrend = report.ok ? tail(compoundReturns(report.data.dailyTwr), TREND_POINTS) : [];
  const volTrend = report.ok
    ? tail(report.data.rollingVolatility30d.map((point) => point.value), TREND_POINTS)
    : [];
  const hasValuedNav = navRows.some((row) => row.nav !== null);
  const syncFailed = quality.ok && quality.data.overallStatus === "ERROR";
  const connected = health.ok && health.data.trading212Configured;

  const setup: SetupStep[] = [
    {
      key: "connect",
      title: "Connect Trading 212",
      state: connected ? "done" : "todo",
      detail: "Pick your account and paste its API key and secret. Helios verifies them first.",
      href: "/settings",
      cta: "Open Settings",
    },
    {
      key: "sync",
      title: "Sync your account",
      state: !connected ? "todo" : syncFailed ? "failed" : quality.ok ? "done" : "todo",
      detail: syncFailed
        ? "The last sync failed, so your history is incomplete. Data quality shows which step and why."
        : "Download your positions and full order history from Trading 212.",
      href: syncFailed ? "/data-quality" : "/",
      cta: syncFailed ? "See what failed" : "Use Sync at the top",
    },
    {
      key: "prices",
      title: "Add a price source",
      state: hasValuedNav ? "done" : "todo",
      detail:
        "Returns, risk and the NAV chart need daily prices. Add a free Twelve Data key in Settings; without one Helios shows no numbers rather than guessed ones.",
      href: "/settings",
      cta: "Add a prices key",
    },
    {
      key: "replay",
      title: "Replay your history",
      state: hasValuedNav ? "done" : "todo",
      detail: "Rebuild every day since your first trade and value it. Run it after syncing.",
      href: "/",
      cta: "Use Replay at the top",
    },
  ];

  const drawdownTrend = tail(
    toDrawdownRows(navSeries, report.ok ? report.data.dailyTwr : []).map(
      (row) => row.drawdown,
    ),
    TREND_POINTS,
  );

  return (
    <>
      <PageHeader
        actions={
          <>
            <ActionButton
              action={replayPerformanceAction}
              confirmLabel="Confirm replay"
              icon={<History aria-hidden="true" size={15} />}
              label="Replay"
              pendingLabel="Replaying…"
            />
            <ActionButton
              action={syncPortfolioAction}
              confirmLabel="Confirm sync"
              icon={<RefreshCw aria-hidden="true" size={15} />}
              label="Sync"
              pendingLabel="Syncing…"
              variant="primary"
            />
          </>
        }
        description={
          latestNav
            ? `Reconstructed from your Trading 212 history. Valued as of ${formatDate(latestNav.asOfDate)}.`
            : "Your Trading 212 portfolio at a glance. Sync, then replay, to build the history."
        }
        title="Overview"
      />

      <SetupChecklist steps={setup} />

      <section className="grid grid-cols-1 gap-6 xl:grid-cols-3">
        <div className={`${CARD} hero-wash theme-fade flex min-w-0 flex-col gap-6 p-5 sm:p-6 xl:col-span-2`}>
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="flex flex-col gap-2">
              <span className="text-sm font-medium text-ink-3">Portfolio value</span>
              <span className="text-4xl font-semibold leading-none tracking-tight text-ink sm:text-5xl">
                {liveTotal !== null
                  ? formatEur(liveTotal)
                  : latestNav
                    ? formatEur(latestNav.navEur)
                    : EMPTY}
              </span>
              <span className="text-sm text-ink-3">
                {liveTotal !== null
                  ? "Live from Trading 212 — the same total its app shows."
                  : latestNav
                    ? `Replayed value on ${formatDay(latestNav.asOfDate)}.`
                    : "No live total or replayed history yet."}
              </span>
            </div>
            {periods.length > 0 && selectedPeriod ? (
              <PeriodTabs basePath="/" periods={periods} selected={selectedPeriod.key} />
            ) : null}
          </div>

          {selectedPeriod && selectedPeriod.status === "ok" ? (
            <div className="flex flex-col gap-3">
              <p className="text-sm text-ink-3">
                <span className="font-medium text-ink-2">{selectedPeriod.label}</span> ·{" "}
                {periodRange(selectedPeriod)}
              </p>
              <PeriodHeadline period={selectedPeriod} />
            </div>
          ) : null}

          {report.ok && hasValuedNav ? (
            <NavChart
              data={periodRows.length > 1 ? periodRows : navRows}
              passiveLabel={null}
              showInvested
            />
          ) : (
            <Unavailable
              detail={
                !report.ok
                  ? report.error
                  : navRows.length > 0
                    ? "Your history was replayed, but no day could be valued: every holding needs a daily price, and no price source is set up yet. Add one in Settings, then Replay."
                    : "Run a replay to reconstruct your daily portfolio value."
              }
              reason={
                !report.ok
                  ? "Performance report unavailable"
                  : navRows.length > 0
                    ? "No prices yet"
                    : "No history yet"
              }
            />
          )}

          <dl className="grid grid-cols-2 gap-x-8 gap-y-3 border-t border-border pt-4 sm:grid-cols-4">
            <MiniStat label="Cash" value={liveCash !== null ? formatEur(liveCash) : EMPTY} />
            <MiniStat
              label="Holdings value"
              value={liveHoldings !== null ? formatEur(liveHoldings) : EMPTY}
            />
            <MiniStat
              label="Unrealised P/L"
              tone={
                liveUnrealised !== null && liveUnrealised > 0
                  ? "up"
                  : liveUnrealised !== null && liveUnrealised < 0
                    ? "down"
                    : undefined
              }
              value={liveUnrealised !== null ? formatSignedEur(liveUnrealised) : EMPTY}
            />
            <MiniStat label="Positions" value={positions.ok ? String(held.length) : EMPTY} />
          </dl>
        </div>

        <Panel
          subtitle={
            selectedPeriod && selectedPeriod.status === "ok"
              ? `${selectedPeriod.label}: where the change in value came from.`
              : "Where each period's change in value came from."
          }
          title="What changed"
        >
          {selectedPeriod && selectedPeriod.status === "ok" ? (
            <div className="flex flex-col gap-6">
              <ChangeBreakdown period={selectedPeriod} />
              <div className="flex flex-col gap-2">
                <h3 className="text-sm font-semibold text-ink">All periods at a glance</h3>
                <PeriodTable basePath="/" compact periods={periods} selected={selectedPeriod.key} />
              </div>
            </div>
          ) : (
            <Unavailable
              detail={selectedPeriod?.detail ?? "Replay your history to see what changed."}
              reason="Not available yet"
            />
          )}
        </Panel>
      </section>

      {selectedPeriod && selectedPeriod.status === "ok" && (selectedPeriod.holdings ?? []).length > 0 ? (
        <Panel
          subtitle={`${selectedPeriod.label} · ${periodRange(selectedPeriod)}: what each holding made or lost. Buying and selling are shown beside a holding, never counted as a gain or a loss.`}
          title="Stock by stock"
        >
          <HoldingMovers news={news.ok ? news.data : []} period={selectedPeriod} />
        </Panel>
      ) : null}

      <section
        aria-label="Headline metrics"
        className="grid grid-cols-2 gap-4 md:grid-cols-3 2xl:grid-cols-6"
      >
        <MetricTile
          label="Total return (TWR)"
          metric={report.ok ? report.data.cumulativeTwr : null}
          render={(value) => formatSignedPercent(value)}
          signed
          trend={twrTrend}
        />
        <MetricTile
          label="Annualised"
          metric={report.ok ? report.data.annualizedReturn : null}
          render={(value) => formatSignedPercent(value)}
          signed
        />
        <MetricTile
          label="Money-weighted (XIRR)"
          metric={report.ok ? report.data.xirr : null}
          render={(value) => formatSignedPercent(value)}
          signed
        />
        <MetricTile
          label="Volatility"
          metric={report.ok ? report.data.volatility : null}
          render={(value) => formatPercent(value)}
          trend={volTrend}
        />
        <MetricTile
          label="Sharpe ratio"
          metric={report.ok ? report.data.sharpe : null}
          render={(value) => formatRatio(value)}
        />
        <MetricTile
          label="Max drawdown"
          metric={report.ok ? report.data.maxDrawdown : null}
          render={(value) => formatPercent(value)}
          trend={drawdownTrend}
        />
      </section>

      <section className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Panel subtitle="Current value by holding, live from Trading 212." title="Allocation">
          {positions.ok ? (
            held.length > 0 ? (
              <AllocationDonut
                slices={held.map((position) => ({
                  label: displayTicker(position.instrument.ticker),
                  sublabel: position.instrument.name,
                  value: walletValue(position),
                }))}
                total={liveHoldings ?? invested}
              />
            ) : (
              <Unavailable detail="Sync once your account holds a position." reason="No open positions" />
            )
          ) : (
            <Unavailable detail={positions.error} reason="Positions unavailable" />
          )}
        </Panel>

        <Panel
          actions={
            <Link className={`${LINK} inline-flex items-center gap-1 text-sm`} href="/holdings">
              All holdings <ArrowUpRight aria-hidden="true" size={15} />
            </Link>
          }
          subtitle="By current value, as a share of what is invested."
          title="Largest holdings"
        >
          {positions.ok ? (
            largest.length > 0 ? (
              <BarList
                items={largest.map((position) => ({
                  key: position.instrument.ticker,
                  href: holdingHref(position.instrument.ticker),
                  label: displayTicker(position.instrument.ticker),
                  sublabel: position.instrument.name,
                  value: walletValue(position),
                  display: `${formatEur(walletValue(position))} · ${formatPercent(
                    invested > 0 ? walletValue(position) / invested : null,
                    1,
                  )}`,
                }))}
              />
            ) : (
              <Unavailable reason="No open positions" />
            )
          ) : (
            <Unavailable detail={positions.error} reason="Positions unavailable" />
          )}
        </Panel>
      </section>

      <section
        className={`grid grid-cols-1 gap-6 ${
          insight.ok && insight.data.status === "ok" ? "lg:grid-cols-2" : ""
        }`}
      >
        <Panel
          actions={
            <a className={`${LINK} inline-flex items-center gap-1 text-sm`} href="/news">
              All news <ArrowUpRight aria-hidden="true" size={15} />
            </a>
          }
          subtitle="Only stories that name a holding you own. Headlines link to the publisher."
          title="Latest news about your holdings"
        >
          {news.ok ? (
            <NewsFeed compact items={news.data.slice(0, 6)} />
          ) : (
            <Unavailable detail={news.error} reason="News unavailable" />
          )}
        </Panel>

      {insight.ok && insight.data.status === "ok" ? (
        <Panel
          actions={
            <a className={`${LINK} inline-flex items-center gap-1 text-sm`} href="/insights">
              Full analysis <ArrowUpRight aria-hidden="true" size={15} />
            </a>
          }
          subtitle="Claude describing your own analytics. Not advice."
          title="Latest insight"
        >
          <AiPanel
            analysis={{ ...insight.data, observations: insight.data.observations.slice(0, 2) }}
          />
        </Panel>
      ) : null}
      </section>

      <section className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <Panel
          className="lg:col-span-2"
          subtitle="Every figure is only as current as the run that produced it. Sync and Replay are at the top of the page."
          title="Data pipelines"
        >
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Pipeline
              asOf={quality.ok ? quality.data.asOf : null}
              failed={syncFailed}
              icon={<RefreshCw size={16} />}
              label="Trading 212 sync"
              never="Never synced"
            />
            <Pipeline
              asOf={report.ok ? report.data.asOf : null}
              icon={<History size={16} />}
              label="NAV replay"
              never="Never replayed"
            />
            <Pipeline
              asOf={news.ok ? latestFetchedAt(news.data) : null}
              control={
                <ActionButton action={syncNewsAction} label="Sync news" pendingLabel="Syncing…" />
              }
              icon={<Newspaper size={16} />}
              label="News"
              never="No articles yet"
            />
            <Pipeline
              asOf={insight.ok ? insight.data.asOf : null}
              control={
                <ActionButton
                  action={analyseWithAiAction}
                  confirmLabel="Confirm — this costs money"
                  label="Analyse"
                  pendingLabel="Analysing…"
                />
              }
              icon={<BrainCircuit size={16} />}
              label="AI analysis"
              never="Never run"
            />
          </div>
        </Panel>

        <Panel subtitle="Local stack reachability." title="System">
          <dl className="flex flex-col divide-y divide-border text-sm">
            <SystemRow
              icon={<Server size={16} />}
              label="API"
              status={
                <StatusBadge
                  label={health.ok ? capitalise(health.data.status) : "Offline"}
                  status={health.ok && health.data.status === "ok" ? "ok" : "failed"}
                />
              }
            />
            <SystemRow
              icon={<Database size={16} />}
              label="Database"
              status={
                <StatusBadge
                  label={health.ok && health.data.databaseReady ? "Ready" : "Unavailable"}
                  status={health.ok && health.data.databaseReady ? "ok" : "failed"}
                />
              }
            />
            <SystemRow
              icon={<RefreshCw size={16} />}
              label="Trading 212"
              status={
                <StatusBadge
                  label={health.ok && health.data.trading212Configured ? "Connected" : "Not connected"}
                  status={health.ok && health.data.trading212Configured ? "ok" : "warning"}
                />
              }
            />
          </dl>
          <p className="mt-3 text-xs text-ink-3">Checked {formatDateTime(health.timestamp)}</p>
          {!health.ok ? (
            <div className="mt-3">
              <Note>{health.error}</Note>
            </div>
          ) : null}
        </Panel>
      </section>
    </>
  );
}

function MiniStat({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "up" | "down";
}) {
  return (
    <div className="flex flex-col gap-1">
      <dt className="text-xs font-medium text-ink-3">{label}</dt>
      <dd
        className={`text-base font-semibold tracking-tight ${
          tone === "up" ? "text-positive" : tone === "down" ? "text-negative" : "text-ink"
        }`}
      >
        {value}
      </dd>
    </div>
  );
}

function SystemRow({
  icon,
  label,
  status,
}: {
  icon: ReactNode;
  label: string;
  status: ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-3 py-2.5 first:pt-0">
      <dt className="flex items-center gap-2.5 text-ink-2">
        <span aria-hidden="true" className="text-ink-4">
          {icon}
        </span>
        {label}
      </dt>
      <dd>{status}</dd>
    </div>
  );
}

/**
 * One pipeline's freshness and its control.
 *
 * The timestamp is the point: now that the dashboard can trigger a run, a page full of
 * numbers with no "as of" invites reading stale output as current. A pipeline that has
 * never run says so in words rather than rendering an empty slot.
 */
function Pipeline({
  label,
  asOf,
  never,
  control,
  icon,
  failed = false,
}: {
  label: string;
  asOf: string | null;
  /** Shown instead of a date when the pipeline has never produced anything. */
  never: string;
  /** Omitted for pipelines whose control already sits in the page header. */
  control?: ReactNode;
  icon: ReactNode;
  /** The last attempt failed: its time is when it broke, not when data was last good. */
  failed?: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-xl border border-border bg-surface-2 p-3.5">
      <div className="flex min-w-0 items-center gap-3">
        <span
          aria-hidden="true"
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-surface text-ink-2 shadow-card"
        >
          {icon}
        </span>
        <div className="min-w-0">
          <p className="text-sm font-medium text-ink">{label}</p>
          <p className={`tabular truncate text-xs ${failed ? "font-medium text-negative" : "text-ink-3"}`}>
            {failed
              ? `Failed ${asOf ? formatDateTime(asOf) : ""}`.trim()
              : asOf
                ? formatDateTime(asOf)
                : never}
          </p>
        </div>
      </div>
      {control}
    </div>
  );
}

/** Newest `fetchedAt` across stored articles — when the news pipeline last brought something in. */
function latestFetchedAt(items: NewsItem[]): string | null {
  let newest: string | null = null;
  for (const item of items) {
    if (item.fetchedAt && (newest === null || item.fetchedAt > newest)) {
      newest = item.fetchedAt;
    }
  }
  return newest;
}

function walletValue(position: Position): number {
  return decimalToNumber(position.walletImpact?.currentValue) ?? 0;
}

/** `AAPL_US_EQ` → `AAPL`: the Trading 212 suffix is noise in a chart label. */

function formatSignedEur(value: number): string {
  const text = formatEur(Math.abs(value));
  if (value > 0) return `+${text}`;
  if (value < 0) return `−${text}`;
  return text;
}

function capitalise(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}
