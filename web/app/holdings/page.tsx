import { ArrowDownRight, ArrowUpRight } from "lucide-react";
import { AllocationDonut } from "@/components/charts/allocation-donut";
import { BarList } from "@/components/charts/bar-list";
import { DataTable, type Column } from "@/components/data-table";
import { Note, PageHeader, Panel, Unavailable } from "@/components/panel";
import { Pager } from "@/components/pager";
import { getAccountSummary, getPositions } from "@/lib/api";
import {
  decimalToNumber,
  EMPTY,
  formatEur,
  formatPercent,
  formatQuantity,
  displayTicker,
  holdingHref,
} from "@/lib/format";
import { paginate, parsePageParam } from "@/lib/pagination";
import type { AccountSummary, Position } from "@/lib/types";
import { CARD } from "@/lib/ui";
import { SERIES } from "@/lib/viz";

export const dynamic = "force-dynamic";

// `/api/v1/t212/positions` returns every open position in one call with no offset param (see
// src/helios/api.py) — there is no page-of-positions concept on the backend at all. Slicing the
// already-fetched list here is fine for a single-user portfolio; see lib/pagination.ts.
const PAGE_SIZE = 10;

export default async function HoldingsPage({
  searchParams,
}: {
  searchParams: Promise<{ page?: string }>;
}) {
  const { page } = await searchParams;
  const [positions, account] = await Promise.all([getPositions(), getAccountSummary()]);

  return (
    <>
      <PageHeader
        description="Live snapshot from Trading 212. Per-share prices are in the instrument currency; account value, P/L and weight are in EUR."
        title="Holdings"
      />

      {positions.ok ? (
        <HoldingsView
          account={account.ok ? account.data : null}
          page={page}
          positions={positions.data}
        />
      ) : (
        <Panel title="Positions">
          <Unavailable
            detail={
              positions.status === 503
                ? "Trading 212 credentials are not configured on the backend."
                : positions.error
            }
            reason="Positions unavailable"
          />
        </Panel>
      )}
    </>
  );
}

function HoldingsView({
  positions,
  page,
  account,
}: {
  positions: Position[];
  page?: string;
  account: AccountSummary | null;
}) {
  if (positions.length === 0) {
    return (
      <Panel title="Positions">
        <Unavailable detail="Sync once your account holds a position." reason="No open positions" />
      </Panel>
    );
  }

  const rows = positions.map((position) => ({
    position,
    value: currentValue(position),
    cost: totalCost(position),
    pnl: unrealisedPnl(position),
  }));
  const totalValue = rows.reduce((sum, row) => sum + row.value, 0);
  const totalCostSum = rows.reduce((sum, row) => sum + row.cost, 0);
  const totalPnl = rows.reduce((sum, row) => sum + row.pnl, 0);
  // Headline totals are Trading 212's own account figures (the ones its app shows, and the
  // ones the Overview uses), so the two pages can never disagree. Per-row values and weights
  // still come from positions; the sum of rows can differ from the account total by the few
  // cents of price movement between the two reads.
  const headlineValue = decimalToNumber(account?.investments?.currentValue) ?? totalValue;
  const headlineCost = decimalToNumber(account?.investments?.totalCost) ?? totalCostSum;
  const headlinePnl = decimalToNumber(account?.investments?.unrealizedProfitLoss) ?? totalPnl;
  const largest = [...rows].sort((a, b) => b.value - a.value)[0];
  const largestWeight = totalValue > 0 && largest ? largest.value / totalValue : null;

  const currencyGroups = new Map<string, number>();
  for (const row of rows) {
    const key = row.position.instrument.currency ?? "Unknown";
    currencyGroups.set(key, (currencyGroups.get(key) ?? 0) + row.value);
  }
  const currencies = [...currencyGroups.entries()].sort((a, b) => b[1] - a[1]);

  return (
    <>
      <section
        aria-label="Portfolio summary"
        className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-5"
      >
        <Stat label="Holdings value" value={formatEur(headlineValue)} />
        <Stat label="Invested cost" value={formatEur(headlineCost)} />
        <Stat
          direction={headlinePnl > 0 ? "up" : headlinePnl < 0 ? "down" : undefined}
          label="Unrealised P/L"
          value={formatSignedEur(headlinePnl)}
        />
        <Stat label="Positions" value={String(rows.length)} />
        <Stat
          hint={largest ? displayTicker(largest.position.instrument.ticker) : undefined}
          label="Largest weight"
          value={largestWeight !== null ? formatPercent(largestWeight, 1) : EMPTY}
        />
      </section>

      <section className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Panel subtitle="Current value by holding." title="Allocation">
          <AllocationDonut
            slices={rows
              .filter((row) => row.value > 0)
              .map((row) => ({
                label: displayTicker(row.position.instrument.ticker),
                href: holdingHref(row.position.instrument.ticker),
                sublabel: row.position.instrument.name,
                value: row.value,
              }))}
            total={headlineValue}
          />
        </Panel>

        <Panel subtitle="Account value grouped by instrument currency." title="Currency exposure">
          {currencies.length > 1 ? (
            <BarList
              items={currencies.map(([currency, value]) => ({
                key: currency,
                label: currency,
                value,
                display: `${formatEur(value)} · ${formatPercent(totalValue > 0 ? value / totalValue : null, 1)}`,
              }))}
              color={SERIES.one}
            />
          ) : (
            <Note>
              Every position is held in {currencies[0]?.[0] ?? "one currency"} — there is nothing
              to break down yet.
            </Note>
          )}
        </Panel>
      </section>

      <Panel subtitle="Open positions, live from Trading 212." title="Positions">
        <HoldingsTable page={page} rows={rows} totalValue={totalValue} />
      </Panel>
    </>
  );
}

interface Row {
  position: Position;
  value: number;
  cost: number;
  pnl: number;
}

function HoldingsTable({
  rows,
  totalValue,
  page,
}: {
  rows: Row[];
  totalValue: number;
  page?: string;
}) {
  const columns: Column<Row>[] = [
    {
      key: "instrument",
      header: "Instrument",
      render: (row) => (
        <div className="flex flex-col gap-0.5 whitespace-normal">
          <a
            className="font-medium text-ink hover:text-accent hover:underline"
            href={holdingHref(row.position.instrument.ticker)}
          >
            {row.position.instrument.name ?? displayTicker(row.position.instrument.ticker)}
          </a>
          <span className="text-xs text-ink-3">
            {row.position.instrument.ticker}
            {row.position.instrument.isin ? ` · ${row.position.instrument.isin}` : ""}
          </span>
        </div>
      ),
    },
    {
      key: "quantity",
      header: "Quantity",
      numeric: true,
      render: (row) => formatQuantity(row.position.quantity),
    },
    {
      key: "avg",
      header: "Avg price",
      numeric: true,
      render: (row) => priceCell(row.position.averagePricePaid, row.position.instrument.currency),
    },
    {
      key: "price",
      header: "Current price",
      numeric: true,
      render: (row) => priceCell(row.position.currentPrice, row.position.instrument.currency),
    },
    {
      key: "value",
      header: "Value",
      numeric: true,
      render: (row) => <span className="font-medium text-ink">{formatEur(row.value)}</span>,
    },
    {
      key: "pnl",
      header: "Unrealised P/L",
      numeric: true,
      render: (row) => <PnlCell fx={row.position.walletImpact?.fxImpact} value={row.pnl} />,
    },
    {
      key: "weight",
      header: "Weight",
      numeric: true,
      render: (row) => (
        <WeightCell weight={totalValue > 0 ? row.value / totalValue : 0} />
      ),
    },
  ];

  const paged = paginate(rows, parsePageParam(page), PAGE_SIZE);

  return (
    <>
      <DataTable
        caption="Open positions"
        columns={columns}
        empty="No open positions"
        rowKey={(row) => row.position.instrument.ticker}
        rows={paged.items}
      />
      <Pager basePath="/holdings" page={paged.page} pageCount={paged.pageCount} />
      <div className="mt-4">
        <Note>
          This table is a live read, not the replayed history. Reconstructed daily valuation
          lives on the performance page, and the two can differ while a sync is pending.
        </Note>
      </div>
    </>
  );
}

function Stat({
  label,
  value,
  hint,
  direction,
}: {
  label: string;
  value: string;
  hint?: string;
  direction?: "up" | "down";
}) {
  return (
    <div className={`${CARD} theme-fade flex min-w-0 flex-col p-4`}>
      <span className="text-sm font-medium text-ink-3">{label}</span>
      <div className="mt-2 flex items-center gap-1.5">
        {direction ? (
          <span
            aria-hidden="true"
            className={`flex h-6 w-6 items-center justify-center rounded-full ${
              direction === "up" ? "bg-positive-soft text-positive" : "bg-negative-soft text-negative"
            }`}
          >
            {direction === "up" ? <ArrowUpRight size={15} /> : <ArrowDownRight size={15} />}
          </span>
        ) : null}
        <span
          className={`text-2xl font-semibold leading-none tracking-tight ${
            direction === "up" ? "text-positive" : direction === "down" ? "text-negative" : "text-ink"
          }`}
        >
          {value}
        </span>
      </div>
      {hint ? <div className="mt-auto pt-2 text-xs text-ink-3">{hint}</div> : null}
    </div>
  );
}

function PnlCell({ value, fx }: { value: number; fx: string | null | undefined }) {
  const fxValue = decimalToNumber(fx);
  if (value === 0) {
    return <span className="text-ink">{formatEur(0)}</span>;
  }
  const up = value > 0;
  return (
    <div className="flex flex-col items-end gap-0.5">
      <span
        className={`inline-flex items-center gap-1 font-medium ${up ? "text-positive" : "text-negative"}`}
      >
        {up ? (
          <ArrowUpRight aria-hidden="true" size={13} />
        ) : (
          <ArrowDownRight aria-hidden="true" size={13} />
        )}
        {formatSignedEur(value)}
      </span>
      {fxValue !== null && fxValue !== 0 ? (
        <span className="text-xs text-ink-3">FX {formatSignedEur(fxValue)}</span>
      ) : null}
    </div>
  );
}

function WeightCell({ weight }: { weight: number }) {
  const pct = Math.max(0, Math.min(1, weight));
  return (
    <div className="flex items-center justify-end gap-2">
      <span className="tabular text-ink">{formatPercent(weight, 1)}</span>
      <span aria-hidden="true" className="h-1.5 w-12 shrink-0 rounded-full bg-surface-3">
        <span
          className="block h-1.5 rounded-full"
          style={{ width: `${pct * 100}%`, background: SERIES.one }}
        />
      </span>
    </div>
  );
}

function priceCell(value: string | null | undefined, currency: string | null) {
  if (!value) return EMPTY;
  return (
    <span>
      {value}
      {/* A real space, not just a CSS margin: otherwise the accessible name reads "182.40USD". */}
      {currency ? <> <span className="text-ink-3">{currency}</span></> : null}
    </span>
  );
}

function currentValue(position: Position): number {
  return decimalToNumber(position.walletImpact?.currentValue) ?? 0;
}

function totalCost(position: Position): number {
  return decimalToNumber(position.walletImpact?.totalCost) ?? 0;
}

function unrealisedPnl(position: Position): number {
  return decimalToNumber(position.walletImpact?.unrealizedProfitLoss) ?? 0;
}

/** `AAPL_US_EQ` → `AAPL`: the Trading 212 suffix is noise in a chart label. */

function formatSignedEur(value: number): string {
  const text = formatEur(Math.abs(value));
  if (value > 0) return `+${text}`;
  if (value < 0) return `−${text}`;
  return text;
}
