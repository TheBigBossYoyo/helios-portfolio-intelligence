import { CalendarDays, ChevronLeft, ChevronRight, Download, LayoutList, RefreshCw } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";

import { ActionButton } from "@/components/action-button";
import {
  DayStrip,
  EventList,
  FilterLinks,
  KIND,
  KindLegend,
  MonthGrid,
  SectionTitle,
  TickerAvatar,
  WhoPaysWhen,
  eventDetail,
  eventTitle,
} from "@/components/calendar-view";
import { IncomeChart, type IncomeBar } from "@/components/charts/income-chart";
import { DataTable, type Column } from "@/components/data-table";
import { Note, PageHeader, Panel, Unavailable } from "@/components/panel";
import { refreshCalendarAction } from "@/lib/actions";
import { getCalendar } from "@/lib/api";
import {
  type CalendarFilter,
  MONTH_NAMES,
  agendaGroups,
  matchesFilter,
  monthKey,
  monthTitle,
  payMatrix,
  relativeDay,
  shiftMonth,
  shortDate,
  tickerSymbol,
} from "@/lib/calendar";
import { decimalToNumber, formatDateTime, formatEur, formatPercent, holdingHref } from "@/lib/format";
import type { HoldingIncome } from "@/lib/types";
import { BUTTON, CARD, SEGMENTED } from "@/lib/ui";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Calendar" };

const CADENCE: Record<number, string> = { 12: "Monthly", 4: "Quarterly", 2: "Twice a year", 1: "Yearly" };

type View = "month" | "list";

interface Params {
  view?: string;
  month?: string;
  day?: string;
  show?: string;
}

function monthLabel(key: string): string {
  const [year, month] = key.split("-").map(Number);
  return `${MONTH_NAMES[month - 1].slice(0, 3)} ${String(year).slice(2)}`;
}

function perShare(amount: string | null, currency: string | null): string {
  const value = decimalToNumber(amount);
  return value === null ? "—" : `${value.toLocaleString("en-GB", { maximumFractionDigits: 4 })} ${currency ?? ""}`.trim();
}

const HOLDING_COLUMNS: Column<HoldingIncome>[] = [
  {
    key: "holding",
    header: "Holding",
    render: (row) => (
      <Link className="flex items-center gap-2.5 font-medium text-ink hover:underline" href={holdingHref(row.ticker)}>
        <TickerAvatar size={28} ticker={row.ticker} />
        <span className="min-w-0 truncate">{row.name ?? tickerSymbol(row.ticker)}</span>
      </Link>
    ),
  },
  {
    key: "pays",
    header: "Pays",
    render: (row) => (row.paymentsPerYear ? CADENCE[row.paymentsPerYear] : "Irregular"),
  },
  { key: "per-share", header: "Per share", numeric: true, render: (row) => perShare(row.amountPerShare, row.currencyCode) },
  {
    key: "next",
    header: "Next payment",
    render: (row) =>
      row.nextPaymentDate ? (
        <span>
          {shortDate(row.nextPaymentDate)}{" "}
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

export default async function CalendarPage({ searchParams }: { searchParams: Promise<Params> }) {
  const params = await searchParams;
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
  const data = result.data;
  const today = data.asOf;
  const view: View = params.view === "list" ? "list" : "month";
  const filter: CalendarFilter =
    params.show === "earnings" || params.show === "dividends" ? params.show : "all";
  const firstMonth = monthKey(data.windowStart ?? today);
  const lastMonth = monthKey(data.windowEnd ?? today);
  const month =
    params.month && /^\d{4}-\d{2}$/.test(params.month) && params.month >= firstMonth && params.month <= lastMonth
      ? params.month
      : monthKey(today);

  const shown = data.events.filter((event) => matchesFilter(event, filter));
  const inMonth = shown.filter((event) => monthKey(event.day) === month);
  const defaultDay = month === monthKey(today) ? today : (inMonth[0]?.day ?? `${month}-01`);
  const selected = params.day && monthKey(params.day) === month ? params.day : defaultDay;
  const selectedEvents = shown.filter((event) => event.day === selected);

  const upcoming = data.events.filter((event) => !event.past && event.day >= today);
  const next = upcoming[0];
  const soon = upcoming.slice(1, 4);
  const reportsThisMonth = data.events.filter(
    (event) => event.kind === "earnings" && monthKey(event.day) === monthKey(today),
  ).length;

  const query = (changes: Partial<Record<keyof Params, string>>, hash = "calendar") => {
    const merged: Record<string, string> = {
      view,
      month,
      show: filter,
      ...(params.day ? { day: selected } : {}),
      ...changes,
    };
    const search = new URLSearchParams(Object.entries(merged).filter(([, value]) => value));
    return `/calendar?${search.toString()}#${hash}`;
  };

  const bars: IncomeBar[] = data.months.map((row) => ({
    key: row.month,
    label: monthLabel(row.month),
    received: decimalToNumber(row.receivedEur) ?? 0,
    expected: decimalToNumber(row.projectedEur) ?? 0,
  }));
  const projected = decimalToNumber(data.projected12mEur) ?? 0;
  const value = decimalToNumber(data.portfolioValueEur);
  const payers = data.holdings.filter((row) => row.source !== "none");
  const nonPayers = data.holdings.filter((row) => row.source === "none");
  const matrix = payMatrix(data.events, today);
  const groups = agendaGroups(shown, today);
  const recent = shown.filter((event) => event.past || event.day < today).reverse();

  return (
    <>
      <PageHeader
        actions={
          <>
            <a
              className={`${BUTTON.secondary} ${BUTTON.small}`}
              download
              href="/calendar/export.ics"
            >
              <Download aria-hidden="true" size={14} /> Add all to my calendar
            </a>
            {data.providerAvailable ? (
              <ActionButton
                action={refreshCalendarAction}
                icon={<RefreshCw aria-hidden="true" size={14} />}
                label="Refresh"
                pendingLabel="Asking Alpha Vantage…"
              />
            ) : null}
          </>
        }
        description="Results and dividends for what you hold and watch, and the income they bring."
        title="Calendar"
      />

      {data.notes.map((note) => (
        <Note key={note}>{note}</Note>
      ))}

      {next ? (
        <section
          aria-label="Next up"
          className={`${CARD} hero-wash grid grid-cols-1 gap-5 overflow-hidden p-5 sm:p-6 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)] lg:gap-8`}
        >
          <div className="flex flex-col gap-4">
            <span className="flex items-center gap-2 text-sm font-medium text-ink-3">
              <span className="h-2 w-2 rounded-full" style={{ background: KIND[next.kind].color }} />
              Next up · {KIND[next.kind].label}
            </span>
            <div className="flex items-center gap-4">
              <TickerAvatar size={60} ticker={next.ticker} />
              <div className="flex min-w-0 flex-col gap-1">
                <span className="text-3xl font-semibold leading-tight tracking-tight text-ink sm:text-4xl">
                  {relativeDay(next.day, today)}
                </span>
                <span className="text-sm text-ink-3">{shortDate(next.day)}</span>
              </div>
            </div>
            <div className="flex flex-col gap-1">
              <Link className="text-lg font-semibold text-ink hover:underline" href={holdingHref(next.ticker)}>
                {eventTitle(next)}
              </Link>
              <span className="text-sm text-ink-2">{eventDetail(next)}</span>
              {next.kind !== "earnings" && next.amountEur ? (
                <span className="mt-1 text-2xl font-semibold tabular-nums text-positive">
                  {formatEur(next.amountEur)}
                  <span className="ml-1.5 text-sm font-normal text-ink-3">{next.afterTax ? "after tax" : "before tax"}</span>
                </span>
              ) : null}
            </div>
            <div className="mt-auto flex flex-col gap-2 pt-2">
              <SectionTitle>Next two weeks</SectionTitle>
              <DayStrip
                events={data.events.filter((event) => !event.past)}
                hrefFor={(day) => query({ view: "month", month: monthKey(day), day })}
                today={today}
              />
            </div>
          </div>
          {soon.length > 0 ? (
            <div className="flex flex-col gap-2">
              <SectionTitle>After that</SectionTitle>
              <EventList events={soon} today={today} />
            </div>
          ) : null}
        </section>
      ) : null}

      <section aria-label="Income" className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
        <Stat label="Expected, next 12 months" tone="text-positive" value={formatEur(projected)} detail="Declared plus estimated" />
        <Stat label="Received, last 12 months" value={formatEur(data.received12mEur)} detail="Net, as Trading 212 booked it" />
        <Stat
          detail="Expected income over what you hold"
          label="Dividend yield"
          value={value && value > 0 ? formatPercent(projected / value, 2) : "—"}
        />
        <Stat
          detail={data.earningsFetchedAt ? `Dates checked ${formatDateTime(data.earningsFetchedAt)}` : "Not checked yet"}
          label="Results this month"
          value={String(reportsThisMonth)}
        />
      </section>

      <section className={`${CARD} theme-fade flex flex-col gap-4 p-4 sm:p-5`} id="calendar">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-1">
            {view === "month" ? (
              <>
                <Link
                  aria-disabled={month <= firstMonth}
                  aria-label="Previous month"
                  className={`${BUTTON.ghost} h-9 w-9 !px-0 ${month <= firstMonth ? "pointer-events-none opacity-40" : ""}`}
                  href={query({ month: shiftMonth(month, -1), day: "" })}
                  scroll={false}
                >
                  <ChevronLeft size={18} />
                </Link>
                <h2 className="min-w-40 text-center text-lg font-semibold tracking-tight text-ink">{monthTitle(month)}</h2>
                <Link
                  aria-disabled={month >= lastMonth}
                  aria-label="Next month"
                  className={`${BUTTON.ghost} h-9 w-9 !px-0 ${month >= lastMonth ? "pointer-events-none opacity-40" : ""}`}
                  href={query({ month: shiftMonth(month, 1), day: "" })}
                  scroll={false}
                >
                  <ChevronRight size={18} />
                </Link>
                {month !== monthKey(today) ? (
                  <Link className={`${BUTTON.secondary} ${BUTTON.small} ml-1`} href={query({ month: monthKey(today), day: "" })} scroll={false}>
                    Today
                  </Link>
                ) : null}
              </>
            ) : (
              <h2 className="text-lg font-semibold tracking-tight text-ink">Coming up</h2>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <FilterLinks current={filter} hrefFor={(value) => query({ show: value })} />
            <nav aria-label="View" className={SEGMENTED}>
              {(
                [
                  { value: "month", label: "Month", Icon: CalendarDays },
                  { value: "list", label: "List", Icon: LayoutList },
                ] as const
              ).map(({ value: option, label, Icon }) => (
                <Link
                  aria-current={view === option ? "true" : undefined}
                  className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
                    view === option ? "bg-surface text-ink shadow-card" : "text-ink-3 hover:text-ink"
                  }`}
                  href={query({ view: option })}
                  key={option}
                  scroll={false}
                >
                  <Icon aria-hidden="true" size={15} />
                  {label}
                </Link>
              ))}
            </nav>
          </div>
        </div>

        {view === "month" ? (
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_22rem]">
            <MonthGrid
              events={shown}
              hrefFor={(day) => query({ day, month: monthKey(day) })}
              month={month}
              selected={selected}
              today={today}
            />
            <div className="flex flex-col gap-2" id="day">
              <SectionTitle aside={<span className="text-xs text-ink-3">{relativeDay(selected, today)}</span>}>
                {shortDate(selected)}
              </SectionTitle>
              {selectedEvents.length > 0 ? (
                <EventList events={selectedEvents} showDate={false} today={today} />
              ) : (
                <p className="rounded-2xl border border-dashed border-border-strong px-4 py-6 text-center text-sm text-ink-3">
                  Nothing on this day.
                  {inMonth.some((event) => event.day > selected) ? " Pick a day with a coloured mark." : ""}
                </p>
              )}
            </div>
          </div>
        ) : groups.length > 0 || recent.length > 0 ? (
          <div className="flex flex-col gap-5">
            {groups.map((group) => (
              <div className="flex flex-col gap-2" key={group.key}>
                <SectionTitle aside={<span className="text-xs text-ink-3">{group.events.length}</span>}>{group.title}</SectionTitle>
                <EventList events={group.events} today={today} />
              </div>
            ))}
            {recent.length > 0 ? (
              <details className="group">
                <summary className="cursor-pointer text-sm font-medium text-ink-2">
                  Recently ({recent.length})
                </summary>
                <div className="mt-2">
                  <EventList events={recent} today={today} />
                </div>
              </details>
            ) : null}
          </div>
        ) : (
          <Unavailable
            detail={
              data.providerAvailable
                ? "Nothing is scheduled yet, or the first check has not run."
                : "Add an Alpha Vantage key in Settings to see results dates."
            }
            reason="Nothing scheduled"
          />
        )}
        <KindLegend />
      </section>

      {matrix.rows.length > 0 ? (
        <Panel
          subtitle="Each paying holding over the next twelve months. Bigger circles pay more; filled ones are declared, outlined ones are Helios's estimate."
          title="Who pays when"
        >
          <WhoPaysWhen matrix={matrix} />
        </Panel>
      ) : null}

      <Panel subtitle="Dividends received (net) and expected, month by month." title="Dividend income">
        <IncomeChart currentKey={monthKey(today)} data={bars} />
        <details className="mt-4">
          <summary className="cursor-pointer text-sm font-medium text-ink-2">Show as a table</summary>
          <div className="mt-3">
            <DataTable caption="Dividend income by month" columns={MONTH_COLUMNS} rowKey={(row) => row.key} rows={bars} />
          </div>
        </details>
      </Panel>

      <Panel subtitle="At today's shares and exchange rates." title="By holding">
        <DataTable
          caption="Dividends by holding"
          columns={HOLDING_COLUMNS}
          empty="No dividend payers yet."
          rowKey={(row) => row.ticker}
          rows={payers}
        />
        {nonPayers.length > 0 ? (
          <p className="mt-3 text-sm text-ink-3">
            No dividend on record for {nonPayers.map((row) => tickerSymbol(row.ticker)).join(", ")} (accumulating
            funds reinvest theirs).
          </p>
        ) : null}
      </Panel>
    </>
  );
}

function Stat({ label, value, detail, tone }: { label: string; value: string; detail: string; tone?: string }) {
  return (
    <div className={`${CARD} flex flex-col gap-1.5 p-4`}>
      <span className="text-sm font-medium text-ink-3">{label}</span>
      <span className={`text-2xl font-semibold tracking-tight ${tone ?? "text-ink"}`}>{value}</span>
      <span className="text-xs text-ink-3">{detail}</span>
    </div>
  );
}

