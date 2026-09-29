import type { Metadata } from "next";
import Link from "next/link";

import { DataTable, type Column } from "@/components/data-table";
import { Note, PageHeader, Panel, Unavailable } from "@/components/panel";
import { TargetsEditor, type TargetRow } from "@/components/targets-editor";
import { saveTargetsAction } from "@/lib/actions";
import { getAllocationTargets, getPositions, getWatchlist } from "@/lib/api";
import { decimalToNumber, displayTicker, formatEur, formatPercent, holdingHref } from "@/lib/format";
import { type AllocationRow, planAllocation, roundToCents } from "@/lib/rebalance";
import { BUTTON, CARD, FIELD, LABEL } from "@/lib/ui";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Targets" };

const DEFAULT_DEPOSIT = 100;
/** Drift bars are drawn against this many percentage points either side. */
const DRIFT_SCALE = 0.15;

function points(value: number): string {
  const pp = value * 100;
  const sign = pp > 0.05 ? "+" : pp < -0.05 ? "−" : "";
  return `${sign}${Math.abs(pp).toFixed(1)} pp`;
}

function DriftBar({ drift }: { drift: number }) {
  const width = Math.min(Math.abs(drift) / DRIFT_SCALE, 1) * 50;
  const over = drift > 0;
  return (
    <div aria-hidden="true" className="relative h-2 w-full rounded-full bg-surface-3">
      <span className="absolute inset-y-[-3px] left-1/2 w-px bg-border-strong" />
      <span
        className="absolute inset-y-0 rounded-full"
        style={{
          left: over ? "50%" : `${50 - width}%`,
          width: `${width}%`,
          background: over ? "var(--series-2)" : "var(--series-1)",
        }}
      />
    </div>
  );
}

export default async function TargetsPage({
  searchParams,
}: {
  searchParams: Promise<{ deposit?: string }>;
}) {
  const params = await searchParams;
  const [positions, targets, watchlist] = await Promise.all([
    getPositions(),
    getAllocationTargets(),
    getWatchlist(),
  ]);
  if (!positions.ok) {
    return (
      <>
        <PageHeader title="Targets" />
        <Panel title="Target allocation">
          <Unavailable detail={positions.error} reason="Holdings unavailable" />
        </Panel>
      </>
    );
  }

  const saved = new Map(
    (targets.ok ? targets.data : []).map((row) => [row.ticker, decimalToNumber(row.weight) ?? 0]),
  );
  const held = positions.data.map((position) => ({
    ticker: position.instrument.ticker,
    name: position.instrument.name,
    value: decimalToNumber(position.walletImpact?.currentValue) ?? 0,
  }));
  const heldTickers = new Set(held.map((row) => row.ticker));
  // Watched names can carry a target too: a plan to start a position.
  const extra = [
    ...(watchlist.ok ? watchlist.data : []).filter((row) => !heldTickers.has(row.ticker)),
  ].map((row) => ({ ticker: row.ticker, name: row.name, value: 0 }));
  const universe = [...held, ...extra];
  const total = held.reduce((sum, row) => sum + row.value, 0);

  const parsedDeposit = Number((params.deposit ?? "").replace(",", "."));
  const deposit = Number.isFinite(parsedDeposit) && parsedDeposit > 0 ? Math.min(parsedDeposit, 1_000_000) : DEFAULT_DEPOSIT;
  const plan = planAllocation(
    universe.map((row) => ({ ...row, target: saved.has(row.ticker) ? (saved.get(row.ticker) ?? 0) : null })),
    deposit,
  );
  const buys = roundToCents(plan.rows.map((row) => row.buy), plan.deposit);
  const rows = plan.rows.map((row, index) => ({ ...row, buy: buys[index] }));
  const worst = [...rows].sort((a, b) => Math.abs(b.drift) - Math.abs(a.drift))[0];
  const outsideHeld = plan.outside.filter((row) => row.value > 0).length;

  const editorRows: TargetRow[] = [...universe]
    .sort((a, b) => b.value - a.value)
    .map((row) => ({
      ticker: row.ticker,
      label: displayTicker(row.ticker),
      name: row.name,
      current: total > 0 ? row.value / total : 0,
      target: saved.has(row.ticker) ? Math.round((saved.get(row.ticker) ?? 0) * 1000) / 10 : null,
    }));

  const depositColumns: Column<AllocationRow>[] = [
    {
      key: "holding",
      header: "Holding",
      render: (row) => (
        <Link className="font-medium text-ink hover:text-accent hover:underline" href={holdingHref(row.ticker)}>
          {displayTicker(row.ticker)}
          {row.name ? <span className="ml-1.5 font-normal text-ink-3">{row.name}</span> : null}
        </Link>
      ),
    },
    {
      key: "buy",
      header: "Put in",
      numeric: true,
      render: (row) => (row.buy > 0 ? <span className="font-medium text-ink">{formatEur(row.buy)}</span> : "—"),
    },
    { key: "now", header: "Now", numeric: true, render: (row) => formatPercent(row.weight, 1) },
    { key: "after", header: "After", numeric: true, render: (row) => formatPercent(row.weightAfter, 1) },
    { key: "target", header: "Target", numeric: true, render: (row) => formatPercent(row.target, 1) },
  ];
  const tradeColumns: Column<AllocationRow>[] = [
    depositColumns[0],
    {
      key: "trade",
      header: "Buy or sell",
      numeric: true,
      render: (row) =>
        Math.abs(row.fullTrade) < 0.5 ? (
          "On target"
        ) : (
          <span className={row.fullTrade > 0 ? "text-positive" : "text-negative"}>
            {row.fullTrade > 0 ? "Buy " : "Sell "}
            {formatEur(Math.abs(row.fullTrade))}
          </span>
        ),
    },
    { key: "now", header: "Now", numeric: true, render: (row) => formatPercent(row.weight, 1) },
    { key: "target", header: "Target", numeric: true, render: (row) => formatPercent(row.target, 1) },
  ];

  return (
    <>
      <PageHeader
        description="Set the share you want each holding to be. Helios shows how far you have drifted and how to split your next deposit to get back on track. It never places a trade: this is a plan for you to act on in Trading 212."
        title="Targets"
      />

      {plan.rows.length === 0 ? (
        <Note>
          No targets yet. Set a percentage for each holding below (or start from today&apos;s mix and
          adjust), and this page will show your drift and how to split each deposit.
        </Note>
      ) : (
        <>
          <section aria-label="Plan summary" className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
            <Stat
              detail={
                outsideHeld === 0
                  ? "Every holding has a target"
                  : `${outsideHeld} holding${outsideHeld === 1 ? "" : "s"} outside the plan`
              }
              label="In the plan"
              value={formatEur(plan.plannedValue)}
            />
            <Stat detail="The share of the plan you have set" label="Targets set" value={formatPercent(plan.targetSum, 0)} />
            <Stat
              detail={worst ? `${displayTicker(worst.ticker)}: ${worst.drift > 0 ? "over" : "under"} target` : ""}
              label="Largest drift"
              value={worst ? points(worst.drift) : "—"}
            />
            <Stat
              detail={`After putting in ${formatEur(plan.deposit)} as below`}
              label="Largest drift after"
              value={`${(plan.maxDriftAfter * 100).toFixed(1)} pp`}
            />
          </section>

          <Panel subtitle="Each holding's share of the plan now against its target. Orange is over target, blue is under." title="Drift">
            <ul className="flex flex-col gap-3.5">
              {[...rows].sort((a, b) => b.drift - a.drift).map((row) => (
                <li className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1.5 sm:grid-cols-[12rem_minmax(0,1fr)_7rem]" key={row.ticker}>
                  <Link className="min-w-0 truncate text-sm font-medium text-ink hover:underline" href={holdingHref(row.ticker)}>
                    {displayTicker(row.ticker)}
                    {row.name ? <span className="ml-1.5 font-normal text-ink-3">{row.name}</span> : null}
                  </Link>
                  <span
                    className={`text-right text-sm font-medium tabular-nums sm:order-3 ${
                      Math.abs(row.drift) < 0.005 ? "text-ink-2" : row.drift > 0 ? "text-[var(--series-2)]" : "text-[var(--series-1)]"
                    }`}
                  >
                    {points(row.drift)}
                  </span>
                  <div className="col-span-2 flex flex-col gap-1 sm:order-2 sm:col-span-1">
                    <DriftBar drift={row.drift} />
                    <span className="text-xs text-ink-3">
                      {formatPercent(row.weight, 1)} now · target {formatPercent(row.target, 1)}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          </Panel>

          <Panel
            subtitle="Split so that only what is below target gets bought, the furthest below first. Nothing is sold."
            title="Your next deposit"
          >
            <form className="mb-4 flex flex-wrap items-end gap-3" method="get">
              <label className={LABEL}>
                Amount (€)
                <input
                  className={`${FIELD.replace("w-full", "")} w-36 tabular-nums`}
                  defaultValue={plan.deposit}
                  inputMode="decimal"
                  name="deposit"
                />
              </label>
              <button className={`${BUTTON.primary} ${BUTTON.small} h-10`} type="submit">
                Split it
              </button>
            </form>
            <DataTable caption="How to split the deposit" columns={depositColumns} rowKey={(row) => row.ticker} rows={rows} />
          </Panel>

          <Panel subtitle="What it would take to be exactly on target today with no new money. Selling can have tax consequences." title="Rebalance fully">
            <DataTable caption="Trades to be exactly on target" columns={tradeColumns} rowKey={(row) => row.ticker} rows={rows} />
          </Panel>
        </>
      )}

      <Panel subtitle="A percentage per holding. Leave one blank to keep it outside the plan; watched stocks can have a target too." title="Set targets">
        <TargetsEditor action={saveTargetsAction} rows={editorRows} />
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
