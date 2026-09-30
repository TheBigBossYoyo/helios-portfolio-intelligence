import { CalendarClock, CalendarPlus, Coins, Megaphone, Moon, Sun } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import {
  addDays,
  type CalendarFilter,
  eventsByDay,
  monthGrid,
  monthKey,
  MONTH_NAMES,
  type PayMatrix,
  parseDay,
  relativeDay,
  shortDate,
  tickerHue,
  tickerSymbol,
  WEEKDAYS_SHORT,
} from "@/lib/calendar";
import { formatEur, holdingHref } from "@/lib/format";
import type { CalendarEvent } from "@/lib/types";
import { SERIES } from "@/lib/viz";

/** Each kind of date: its colour (a categorical series, never a status), glyph and name. */
export const KIND = {
  earnings: { label: "Results", color: SERIES.one, Icon: Megaphone },
  "ex-dividend": { label: "Ex-dividend", color: SERIES.four, Icon: CalendarClock },
  dividend: { label: "Dividend", color: SERIES.three, Icon: Coins },
} as const;

export function companyName(event: CalendarEvent): string {
  return event.name ?? tickerSymbol(event.ticker);
}

export function eventTitle(event: CalendarEvent): string {
  const name = companyName(event);
  if (event.kind === "earnings") return event.past ? `${name} reported results` : `${name} reports results`;
  if (event.kind === "ex-dividend") return `${name} goes ex-dividend`;
  return event.received ? `Dividend from ${name} arrived` : `Dividend from ${name}`;
}

function money(amount: string | null, currency: string | null): string | null {
  if (amount === null) return null;
  const value = Number(amount);
  if (!Number.isFinite(value)) return null;
  return `${value.toLocaleString("en-GB", { maximumFractionDigits: 4 })}${currency ? ` ${currency}` : ""}`;
}

function quarterOf(fiscalDateEnding: string | null): string | null {
  if (!fiscalDateEnding) return null;
  const date = parseDay(fiscalDateEnding);
  return `quarter to ${MONTH_NAMES[date.getUTCMonth()].slice(0, 3)} ${date.getUTCFullYear()}`;
}

/** The one-line explanation under a title: when, how much, what to do. */
export function eventDetail(event: CalendarEvent): string {
  if (event.kind === "earnings") {
    const parts = [
      event.timeOfDay === "pre-market"
        ? "Before the market opens"
        : event.timeOfDay === "post-market"
          ? "After the close"
          : "Time not announced",
    ];
    const quarter = quarterOf(event.fiscalDateEnding);
    if (quarter) parts.push(quarter);
    const eps = money(event.estimateEps, event.epsCurrency);
    if (eps && !event.past) parts.push(`analysts expect ${eps} a share`);
    return parts.join(" · ");
  }
  const parts: string[] = [];
  const perShare = money(event.amountPerShare, event.currencyCode);
  if (event.kind === "ex-dividend") {
    parts.push("Own the shares before this day to be paid");
    if (perShare) parts.push(`${perShare} a share`);
    return parts.join(" · ");
  }
  if (perShare) parts.push(`${perShare} a share`);
  if (event.exDate && !event.past) parts.push(`ex-dividend ${parseDay(event.exDate).getUTCDate()} ${MONTH_NAMES[parseDay(event.exDate).getUTCMonth()].slice(0, 3)}`);
  if (!event.confirmed) parts.push("estimated from the usual rhythm");
  return parts.join(" · ");
}

export function TickerAvatar({ ticker, size = 40 }: { ticker: string; size?: number }) {
  const symbol = tickerSymbol(ticker);
  const hue = tickerHue(ticker);
  return (
    <span
      aria-hidden="true"
      className="flex shrink-0 items-center justify-center rounded-full font-semibold tracking-tight text-ink"
      style={{
        width: size,
        height: size,
        fontSize: symbol.length > 3 ? size * 0.28 : size * 0.34,
        background: `color-mix(in srgb, ${hue} 16%, var(--surface))`,
        boxShadow: `inset 0 0 0 1.5px color-mix(in srgb, ${hue} 45%, transparent)`,
      }}
    >
      {symbol.slice(0, 4)}
    </span>
  );
}

function StatusBadge({ event }: { event: CalendarEvent }) {
  const [text, tone] = event.received
    ? ["Received", "bg-positive-soft text-positive"]
    : event.past
      ? ["Done", "bg-surface-3 text-ink-3"]
      : event.kind === "earnings"
        ? ["Scheduled", "bg-surface-3 text-ink-2"]
        : event.confirmed
          ? ["Declared", "bg-surface-3 text-ink-2"]
          : ["Estimate", "bg-warning-soft text-warning"];
  return <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${tone}`}>{text}</span>;
}

/** A full event row: avatar, what happens, when, how much, and "add to my calendar". */
export function EventCard({ event, today, showDate = true }: { event: CalendarEvent; today: string; showDate?: boolean }) {
  const kind = KIND[event.kind];
  const amount = event.kind !== "earnings" && event.amountEur !== null ? formatEur(event.amountEur) : null;
  const TimeIcon = event.timeOfDay === "pre-market" ? Sun : event.timeOfDay === "post-market" ? Moon : null;
  return (
    <div className={`flex items-start gap-3 px-3.5 py-3 sm:px-4 ${event.past ? "opacity-70" : ""}`}>
      <Link className="relative mt-0.5 shrink-0" href={holdingHref(event.ticker)} tabIndex={-1}>
        <TickerAvatar ticker={event.ticker} />
        <span
          className="absolute -bottom-1 -right-1 flex h-5 w-5 items-center justify-center rounded-full border-2 border-surface text-white"
          style={{ background: kind.color }}
        >
          <kind.Icon aria-hidden="true" size={11} strokeWidth={2.5} />
        </span>
      </Link>
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex items-baseline justify-between gap-3">
          <Link className="min-w-0 font-medium leading-snug text-ink hover:underline" href={holdingHref(event.ticker)}>
            {eventTitle(event)}
          </Link>
          {amount ? (
            <span className="shrink-0 text-sm font-semibold tabular-nums text-ink">
              {amount}
              <span className="sr-only">{event.received ? " arrived" : event.afterTax ? " after tax" : " before tax"}</span>
            </span>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
          <StatusBadge event={event} />
          {event.held ? null : (
            <span className="rounded-full bg-accent-soft px-2 py-0.5 text-[11px] font-medium text-accent-ink">Watching</span>
          )}
          {showDate ? <span className="font-medium text-ink-2">{relativeDay(event.day, today)}</span> : null}
          {TimeIcon ? <TimeIcon aria-hidden="true" className="text-ink-3" size={13} /> : null}
          {amount ? (
            <span aria-hidden="true" className="text-ink-3">
              {event.received ? "arrived" : event.afterTax ? "after tax" : "before tax"}
            </span>
          ) : null}
        </div>
        <span className="text-sm leading-snug text-ink-3">{eventDetail(event)}</span>
      </div>
      {event.past ? null : (
        <a
          aria-label={`Add “${eventTitle(event)}” to your calendar`}
          className="-mr-1.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-ink-3 transition-colors hover:bg-surface-3 hover:text-ink"
          download
          href={`/calendar/export.ics?id=${encodeURIComponent(event.id)}`}
          title="Add to calendar"
        >
          <CalendarPlus aria-hidden="true" size={17} />
        </a>
      )}
    </div>
  );
}

/** The next two weeks at a glance: each day with a mark per event; a tap opens that day. */
export function DayStrip({
  events,
  today,
  hrefFor,
}: {
  events: CalendarEvent[];
  today: string;
  hrefFor: (day: string) => string;
}) {
  const byDay = eventsByDay(events);
  const days = Array.from({ length: 14 }, (_, index) => addDays(today, index));
  return (
    <nav aria-label="Next two weeks" className="no-scrollbar -mx-1 flex gap-1 overflow-x-auto px-1">
      {days.map((day) => {
        const date = parseDay(day);
        const dayEvents = byDay.get(day) ?? [];
        const isToday = day === today;
        return (
          <Link
            aria-label={`${shortDate(day)}${dayEvents.length ? `, ${dayEvents.length} event${dayEvents.length > 1 ? "s" : ""}` : ""}`}
            className={`flex min-w-11 flex-1 flex-col items-center gap-1 rounded-xl py-2 transition-colors ${
              isToday ? "bg-accent text-accent-contrast" : "hover:bg-surface-3/70"
            }`}
            href={hrefFor(day)}
            key={day}
            scroll={false}
          >
            <span className={`text-[10px] font-semibold uppercase ${isToday ? "opacity-80" : "text-ink-4"}`}>
              {WEEKDAYS_SHORT[(date.getUTCDay() + 6) % 7].slice(0, 2)}
            </span>
            <span className={`text-sm font-semibold tabular-nums ${isToday ? "" : "text-ink"}`}>{date.getUTCDate()}</span>
            <span className="flex h-1.5 gap-0.5">
              {dayEvents.slice(0, 3).map((event) => (
                <span
                  className="h-1.5 w-1.5 rounded-full"
                  key={event.id}
                  style={{
                    background: KIND[event.kind].color,
                    boxShadow: isToday ? "0 0 0 1.5px var(--accent-contrast)" : undefined,
                  }}
                />
              ))}
            </span>
          </Link>
        );
      })}
    </nav>
  );
}

export function EventList({ events, today, showDate = true }: { events: CalendarEvent[]; today: string; showDate?: boolean }) {
  return (
    <ul className="divide-y divide-border overflow-hidden rounded-2xl border border-border bg-surface">
      {events.map((event) => (
        <li key={event.id}>
          <EventCard event={event} showDate={showDate} today={today} />
        </li>
      ))}
    </ul>
  );
}

function EventChip({ event }: { event: CalendarEvent }) {
  const kind = KIND[event.kind];
  return (
    <span
      className={`flex min-w-0 items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium leading-4 text-ink ${
        event.past ? "opacity-60" : ""
      }`}
      style={{
        background: `color-mix(in srgb, ${kind.color} ${event.confirmed ? 16 : 7}%, var(--surface))`,
        boxShadow: event.confirmed ? undefined : `inset 0 0 0 1px color-mix(in srgb, ${kind.color} 55%, transparent)`,
      }}
      title={`${eventTitle(event)}${event.confirmed ? "" : " (estimate)"}`}
    >
      <kind.Icon aria-hidden="true" className="shrink-0" size={11} style={{ color: kind.color }} />
      <span className="truncate">{tickerSymbol(event.ticker)}</span>
    </span>
  );
}

/**
 * The month at a glance. Each day links to itself (the list beside or below shows that day);
 * on a phone the chips shrink to coloured dots.
 */
export function MonthGrid({
  month,
  events,
  today,
  selected,
  hrefFor,
}: {
  month: string;
  events: CalendarEvent[];
  today: string;
  selected: string;
  hrefFor: (day: string) => string;
}) {
  const byDay = eventsByDay(events);
  const weeks = monthGrid(month);
  return (
    <div className="overflow-hidden rounded-2xl border border-border">
      <div className="grid grid-cols-7 border-b border-border bg-surface-2">
        {WEEKDAYS_SHORT.map((weekday) => (
          <span className="py-2 text-center text-[11px] font-semibold uppercase tracking-wide text-ink-3" key={weekday}>
            <span className="sm:hidden">{weekday.slice(0, 1)}</span>
            <span className="hidden sm:inline">{weekday}</span>
          </span>
        ))}
      </div>
      <div className="grid grid-cols-7">
        {weeks.flat().map((day, index) => {
          const inMonth = monthKey(day) === month;
          const dayEvents = byDay.get(day) ?? [];
          const isToday = day === today;
          const isSelected = day === selected;
          const weekend = index % 7 >= 5;
          return (
            <Link
              aria-current={isSelected ? "date" : undefined}
              aria-label={`${parseDay(day).getUTCDate()} ${MONTH_NAMES[parseDay(day).getUTCMonth()]}${
                dayEvents.length ? `, ${dayEvents.length} event${dayEvents.length > 1 ? "s" : ""}` : ""
              }`}
              className={`relative flex min-h-14 flex-col gap-1 border-b border-r border-border p-1 transition-colors sm:min-h-24 sm:p-1.5 [&:nth-child(7n)]:border-r-0 ${
                inMonth ? (weekend ? "bg-surface-2/60" : "bg-surface") : "bg-surface-2 text-ink-4"
              } ${isSelected ? "z-10 ring-2 ring-inset ring-accent" : "hover:bg-surface-3/60"}`}
              href={hrefFor(day)}
              key={day}
              scroll={false}
            >
              <span
                className={`flex h-6 w-6 items-center justify-center self-center rounded-full text-xs font-semibold tabular-nums sm:self-start ${
                  isToday ? "bg-accent text-accent-contrast" : inMonth ? "text-ink" : "text-ink-4"
                }`}
              >
                {parseDay(day).getUTCDate()}
              </span>
              {dayEvents.length > 0 ? (
                <>
                  <span className="flex flex-wrap justify-center gap-0.5 sm:hidden">
                    {dayEvents.slice(0, 4).map((event) => (
                      <span
                        className="h-1.5 w-1.5 rounded-full"
                        key={event.id}
                        style={{ background: KIND[event.kind].color, opacity: event.past ? 0.5 : 1 }}
                      />
                    ))}
                  </span>
                  <span className="hidden min-w-0 flex-col gap-0.5 sm:flex">
                    {dayEvents.slice(0, 3).map((event) => (
                      <EventChip event={event} key={event.id} />
                    ))}
                    {dayEvents.length > 3 ? (
                      <span className="px-1 text-[11px] font-medium text-ink-3">+{dayEvents.length - 3} more</span>
                    ) : null}
                  </span>
                </>
              ) : null}
            </Link>
          );
        })}
      </div>
    </div>
  );
}

export function KindLegend() {
  return (
    <ul className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-ink-2">
      {Object.values(KIND).map((kind) => (
        <li className="flex items-center gap-1.5" key={kind.label}>
          <span className="flex h-5 w-5 items-center justify-center rounded-full text-white" style={{ background: kind.color }}>
            <kind.Icon aria-hidden="true" size={11} strokeWidth={2.5} />
          </span>
          {kind.label}
        </li>
      ))}
      <li className="flex items-center gap-1.5">
        <span className="h-4 w-7 rounded-md" style={{ boxShadow: `inset 0 0 0 1px ${SERIES.three}` }} />
        Outlined: estimate
      </li>
    </ul>
  );
}

export function FilterLinks({ current, hrefFor }: { current: CalendarFilter; hrefFor: (filter: CalendarFilter) => string }) {
  const options: { value: CalendarFilter; label: string }[] = [
    { value: "all", label: "All" },
    { value: "earnings", label: "Results" },
    { value: "dividends", label: "Dividends" },
  ];
  return (
    <nav aria-label="Show" className="flex gap-1.5">
      {options.map((option) => (
        <Link
          aria-current={option.value === current ? "true" : undefined}
          className={`rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
            option.value === current
              ? "border-transparent bg-ink text-bg"
              : "border-border text-ink-2 hover:border-border-strong hover:text-ink"
          }`}
          href={hrefFor(option.value)}
          key={option.value}
          scroll={false}
        >
          {option.label}
        </Link>
      ))}
    </nav>
  );
}

/**
 * Who pays when: each paying holding across the next twelve months, one circle per payment,
 * its area proportional to the euros. Filled when declared, outlined when estimated.
 */
export function WhoPaysWhen({ matrix }: { matrix: PayMatrix }) {
  if (matrix.rows.length === 0) return null;
  const radius = (amount: number) => (matrix.max > 0 ? 4 + Math.sqrt(amount / matrix.max) * 10 : 0);
  return (
    <div className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">
      <table className="w-full min-w-[640px] border-collapse text-sm">
        <caption className="sr-only">Dividends expected per holding and month</caption>
        <thead>
          <tr>
            <th className="w-40 py-2 pr-3 text-left text-xs font-medium text-ink-3" scope="col">
              Holding
            </th>
            {matrix.months.map((month) => (
              <th className="py-2 text-center text-[11px] font-medium text-ink-3" key={month} scope="col">
                {MONTH_NAMES[Number(month.slice(5)) - 1].slice(0, 3)}
                {month.endsWith("-01") ? <span className="block text-[10px] text-ink-4">{month.slice(0, 4)}</span> : null}
              </th>
            ))}
            <th className="py-2 pl-3 text-right text-xs font-medium text-ink-3" scope="col">
              Year
            </th>
          </tr>
        </thead>
        <tbody>
          {matrix.rows.map((row) => (
            <tr className="border-t border-border" key={row.ticker}>
              <th className="py-2 pr-3 text-left font-normal" scope="row">
                <Link className="flex items-center gap-2 hover:underline" href={holdingHref(row.ticker)}>
                  <TickerAvatar size={26} ticker={row.ticker} />
                  <span className="truncate text-ink">{row.name ?? tickerSymbol(row.ticker)}</span>
                </Link>
              </th>
              {row.cells.map((cell) => (
                <td className="h-11 text-center" key={cell.month} title={cell.amount > 0 ? formatEur(cell.amount) : undefined}>
                  {cell.amount > 0 ? (
                    <svg aria-label={`${formatEur(cell.amount)}${cell.confirmed ? "" : ", estimate"}`} className="mx-auto" height={30} role="img" width={30}>
                      <circle
                        cx={15}
                        cy={15}
                        fill={cell.confirmed ? SERIES.three : `color-mix(in srgb, ${SERIES.three} 18%, transparent)`}
                        r={radius(cell.amount)}
                        stroke={SERIES.three}
                        strokeDasharray={cell.confirmed ? undefined : "2.5 2"}
                        strokeWidth={cell.confirmed ? 0 : 1.5}
                      />
                    </svg>
                  ) : (
                    <span aria-hidden="true" className="mx-auto block h-1 w-1 rounded-full bg-border-strong" />
                  )}
                </td>
              ))}
              <td className="py-2 pl-3 text-right font-medium tabular-nums text-ink">{formatEur(row.total)}</td>
            </tr>
          ))}
          <tr className="border-t-2 border-border">
            <th className="py-2 pr-3 text-left text-xs font-medium text-ink-3" scope="row">
              All holdings
            </th>
            {matrix.totals.map((total, index) => (
              <td className="py-2 text-center text-[11px] tabular-nums text-ink-2" key={matrix.months[index]}>
                {total > 0 ? `€${total < 10 ? total.toFixed(2) : Math.round(total)}` : ""}
              </td>
            ))}
            <td className="py-2 pl-3 text-right font-semibold tabular-nums text-ink">
              {formatEur(matrix.totals.reduce((sum, value) => sum + value, 0))}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

export function SectionTitle({ children, aside }: { children: ReactNode; aside?: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-3">{children}</h3>
      {aside}
    </div>
  );
}
