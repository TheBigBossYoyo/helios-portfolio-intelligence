import { Coins, Megaphone, RefreshCw } from "lucide-react";
import Link from "next/link";
import type { Metadata } from "next";

import { ActionButton } from "@/components/action-button";
import { IncomeChart, type IncomeBar } from "@/components/charts/income-chart";
import { DataTable, type Column } from "@/components/data-table";
import { Note, PageHeader, Panel, Unavailable } from "@/components/panel";
import { refreshCalendarAction } from "@/lib/actions";
import { getCalendar } from "@/lib/api";
import {
  decimalToNumber,
  displayTicker,
  formatDateTime,
  formatEur,
  formatPercent,
  holdingHref,
} from "@/lib/format";
import type { CalendarEvent, HoldingIncome, MarketCalendar } from "@/lib/types";
import { CARD } from "@/lib/ui";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Calendar" };

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const CADENCE: Record<number, string> = { 12: "Monthly", 4: "Quarterly", 2: "Twice a year", 1: "Yearly" };

function parseDay(day: string): Date {
  const [year, month, date] = day.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, date));
}

function shortDay(day: string | null): string {
  if (!day) return "—";
  const value = parseDay(day);
  return `${value.getUTCDate()} ${MONTHS[value.getUTCMonth()]}`;
}

function monthLabel(key: string): string {
  const [year, month] = key.split("-").map(Number);
  return `${MONTHS[month - 1]} ${String(year).slice(2)}`;
}

function money(amount: string | null, currency: string | null): string {
  const value = decimalToNumber(amount);
  if (value === null) return "—";
  return `${value.toLocaleString("en-GB", { maximumFractionDigits: 4 })} ${currency ?? ""}`.trim();
}

function eventTitle(event: CalendarEvent): string {
  const name = event.name ?? displayTicker(event.ticker);
  return event.kind === "earnings" ? `${name} reports results` : `Dividend from ${name}`;
}

function eventDetail(event: CalendarEvent): string {
  if (event.kind === "earnings") {
    const when =
      event.timeOfDay === "pre-market"
        ? "Before the market opens"
        : event.timeOfDay === "post-market"
          ? "After the close"
          : "Time not announced";
    const estimate =
      event.estimateEps !== null ? ` · analysts expect ${money(event.estimateEps, event.epsCurrency)} a share` : "";
    return `${when}${estimate}`;
  }
  const parts: string[] = [];
  if (event.amountEur !== null) {
    parts.push(`${formatEur(event.amountEur)} ${event.afterTax ? "after tax" : "before tax"}`);
  }
  parts.push(`${money(event.amountPerShare, event.currencyCode)} a share`);
  if (event.exDate) parts.push(`own it before ${shortDay(event.exDate)}`);
  return parts.join(" · ");
}

function groupByMonth(events: CalendarEvent[]): { key: string; label: string; events: CalendarEvent[] }[] {
  const groups = new Map<string, CalendarEvent[]>();
  for (const event of events) {
    const key = event.day.slice(0, 7);
    groups.set(key, [...(groups.get(key) ?? []), event]);
  }
  return [...groups.entries()].map(([key, list]) => {
    const [year, month] = key.split("-").map(Number);
    return { key, label: `${["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"][month - 1]} ${year}`, events: list };
  });
}

const HOLDING_COLUMNS: Column<HoldingIncome>[] = [
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
    key: "pays",
    header: "Pays",
    render: (row) =>
      row.source === "none"
        ? "No dividend"
        : row.paymentsPerYear
          ? CADENCE[row.paymentsPerYear]
          : "Irregular",
  },
  { key: "per-share", header: "Per share", numeric: true, render: (row) => money(row.amountPerShare, row.currencyCode) },
  {
    key: "next",
    header: "Next payment",
    render: (row) =>
      row.nextPaymentDate ? (
        <span>
          {shortDay(row.nextPaymentDate)}{" "}
          <span className="text-xs text-ink-3">{row.nextConfirmed ? "declared" : "estimate"}</span>
        </span>
      ) : (
        "—"
      ),
  },
  {
    key: "annual",
    header: "Next 12 months",
    numeric: true,
    render: (row) => (row.annualEur === null ? "Not enough history" : formatEur(row.annualEur)),
  },
  { key: "yield", header: "Yield", numeric: true, render: (row) => (row.yieldPct === null ? "—" : formatPercent(row.yieldPct, 2)) },
  { key: "received", header: "Received, 12 months", numeric: true, render: (row) => formatEur(row.received12mEur) },
];

const MONTH_COLUMNS: Column<IncomeBar>[] = [
  { key: "month", header: "Month", render: (row) => row.label },
  { key: "received", header: "Received", numeric: true, render: (row) => formatEur(row.received) },
  { key: "expected", header: "Expected", numeric: true, render: (row) => formatEur(row.expected) },
];

export default async function CalendarPage() {
  const result = await getCalendar();
  if (!result.ok) {
    return (
      <>
        <PageHeader title="Calendar" />
        <Panel title="Earnings and dividends">
          <Unavailable detail={result.error} reason="Calendar unavailable" />
        </Panel>
      </>
    );
  }
  const data: MarketCalendar = result.data;
  const currentKey = data.asOf.slice(0, 7);
  const bars: IncomeBar[] = data.months.map((month) => ({
    key: month.month,
    label: monthLabel(month.month),
    received: decimalToNumber(month.receivedEur) ?? 0,
    expected: decimalToNumber(month.projectedEur) ?? 0,
  }));
  const projected = decimalToNumber(data.projected12mEur) ?? 0;
  const value = decimalToNumber(data.portfolioValueEur);
  const nextPayment = data.events.find((event) => event.kind === "dividend");
  const nextReport = data.events.find((event) => event.kind === "earnings");
  const groups = groupByMonth(data.events);
  const payers = data.holdings.filter((row) => row.source !== "none");
  const nonPayers = data.holdings.filter((row) => row.source === "none");

  return (
    <>
      <PageHeader
        actions={
          data.providerAvailable ? (
            <ActionButton
              action={refreshCalendarAction}
              icon={<RefreshCw aria-hidden="true" size={14} />}
              label="Refresh"
              pendingLabel="Asking Alpha Vantage…"
            />
          ) : undefined
        }
        description="Earnings reports and dividends for what you hold and watch, and the income those dividends bring. Declared means the company announced it; anything else is Helios's estimate from the company's own rhythm."
        title="Calendar"
      />

      <section aria-label="Dividend income" className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
        <Stat detail="Net, as Trading 212 booked it" label="Received, last 12 months" value={formatEur(data.received12mEur)} />
        <Stat detail="Declared plus estimated, after tax where known" label="Expected, next 12 months" value={formatEur(projected)} />
        <Stat
          detail="Expected income over what you hold today"
          label="Yield on today's value"
          value={value && value > 0 ? formatPercent(projected / value, 2) : "—"}
        />
        <Stat
          detail={nextReport ? `Next report: ${nextReport.name ?? displayTicker(nextReport.ticker)}, ${shortDay(nextReport.day)}` : "No report scheduled"}
          label="Next dividend"
          value={nextPayment ? shortDay(nextPayment.day) : "—"}
        />
      </section>

      {data.notes.map((note) => (
        <Note key={note}>{note}</Note>
      ))}

      <Panel
        subtitle={
          data.earningsFetchedAt
            ? `The next 120 days. Earnings dates checked ${formatDateTime(data.earningsFetchedAt)}.`
            : "The next 120 days."
        }
        title="Coming up"
      >
        {groups.length === 0 ? (
          <Unavailable
            detail={
              data.providerAvailable
                ? "No report or payment is scheduled in the next 120 days, or the first check has not run yet."
                : "Add an Alpha Vantage key in Settings to see earnings dates."
            }
            reason="Nothing scheduled"
          />
        ) : (
          <div className="flex flex-col gap-6">
            {groups.map((group) => (
              <section aria-label={group.label} className="flex flex-col gap-2" key={group.key}>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-3">{group.label}</h3>
                <ol className="flex flex-col divide-y divide-border rounded-xl border border-border">
                  {group.events.map((event) => {
                    const day = parseDay(event.day);
                    const Icon = event.kind === "earnings" ? Megaphone : Coins;
                    return (
                      <li key={`${event.kind}-${event.ticker}-${event.day}`}>
                        <Link
                          className="flex items-center gap-3.5 px-3.5 py-3 transition-colors hover:bg-surface-2 sm:px-4"
                          href={holdingHref(event.ticker)}
                        >
                          <span className="flex w-11 shrink-0 flex-col items-center rounded-xl bg-surface-2 py-1.5">
                            <span className="text-[11px] font-medium uppercase text-ink-3">{WEEKDAYS[day.getUTCDay()]}</span>
                            <span className="text-lg font-semibold leading-tight tabular-nums text-ink">{day.getUTCDate()}</span>
                          </span>
                          <span
                            aria-hidden="true"
                            className={`hidden h-9 w-9 shrink-0 items-center justify-center rounded-full sm:flex ${
                              event.kind === "earnings" ? "bg-accent-soft text-accent-ink" : "bg-positive-soft text-positive"
                            }`}
                          >
                            <Icon size={17} strokeWidth={2} />
                          </span>
                          <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                            <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                              <span className="font-medium text-ink">{eventTitle(event)}</span>
                              <span
                                className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${
                                  event.confirmed ? "bg-surface-3 text-ink-2" : "bg-warning-soft text-warning"
                                }`}
                              >
                                {event.kind === "earnings" ? "Scheduled" : event.confirmed ? "Declared" : "Estimate"}
                              </span>
                              {event.held ? null : (
                                <span className="rounded-full bg-accent-soft px-2 py-0.5 text-[11px] font-medium text-accent-ink">
                                  Watching
                                </span>
                              )}
                            </span>
                            <span className="text-sm text-ink-3">{eventDetail(event)}</span>
                          </span>
                        </Link>
                      </li>
                    );
                  })}
                </ol>
              </section>
            ))}
          </div>
        )}
      </Panel>

      <Panel
        subtitle="Month by month: dividends received (net) and expected. Expected bars are estimates unless the company has declared the payment."
        title="Dividend income"
      >
        <IncomeChart currentKey={currentKey} data={bars} />
        <details className="mt-4">
          <summary className="cursor-pointer text-sm font-medium text-ink-2">Show as a table</summary>
          <div className="mt-3">
            <DataTable caption="Dividend income by month" columns={MONTH_COLUMNS} rowKey={(row) => row.key} rows={bars} />
          </div>
        </details>
      </Panel>

      <Panel
        subtitle="What each holding pays, when it pays next, and what that adds up to over a year at today's shares and exchange rates."
        title="By holding"
      >
        <DataTable
          caption="Dividends by holding"
          columns={HOLDING_COLUMNS}
          empty="No holdings yet."
          rowKey={(row) => row.ticker}
          rows={payers}
        />
        {nonPayers.length > 0 ? (
          <p className="mt-3 text-sm text-ink-3">
            No dividend on record for {nonPayers.map((row) => displayTicker(row.ticker)).join(", ")}
            {" "}(accumulating funds reinvest theirs).
          </p>
        ) : null}
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
