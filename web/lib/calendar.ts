/**
 * Calendar arithmetic and grouping, on plain "YYYY-MM-DD" days. Every date here is a calendar
 * day with no time or zone, so all maths runs in UTC to keep a day from sliding across midnight.
 */

import type { CalendarEvent } from "./types";

export type CalendarFilter = "all" | "earnings" | "dividends";

export const MONTH_NAMES = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];
export const WEEKDAYS_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export function parseDay(day: string): Date {
  const [year, month, date] = day.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, date));
}

export function dayKey(date: Date): string {
  return date.toISOString().slice(0, 10);
}

export function addDays(day: string, days: number): string {
  const date = parseDay(day);
  date.setUTCDate(date.getUTCDate() + days);
  return dayKey(date);
}

export function daysBetween(from: string, to: string): number {
  return Math.round((parseDay(to).getTime() - parseDay(from).getTime()) / 86_400_000);
}

export function monthKey(day: string): string {
  return day.slice(0, 7);
}

export function shiftMonth(month: string, by: number): string {
  const [year, value] = month.split("-").map(Number);
  const date = new Date(Date.UTC(year, value - 1 + by, 1));
  return dayKey(date).slice(0, 7);
}

export function monthTitle(month: string): string {
  const [year, value] = month.split("-").map(Number);
  return `${MONTH_NAMES[value - 1]} ${year}`;
}

/** "Mon 5 Oct" */
export function shortDate(day: string): string {
  const date = parseDay(day);
  const weekday = WEEKDAYS_SHORT[(date.getUTCDay() + 6) % 7];
  return `${weekday} ${date.getUTCDate()} ${MONTH_NAMES[date.getUTCMonth()].slice(0, 3)}`;
}

/** The month as whole weeks, Monday first: 5 or 6 rows of 7 days. */
export function monthGrid(month: string): string[][] {
  const first = parseDay(`${month}-01`);
  const offset = (first.getUTCDay() + 6) % 7;
  let cursor = addDays(`${month}-01`, -offset);
  const weeks: string[][] = [];
  do {
    const week: string[] = [];
    for (let index = 0; index < 7; index += 1) {
      week.push(cursor);
      cursor = addDays(cursor, 1);
    }
    weeks.push(week);
  } while (monthKey(cursor) === month);
  return weeks;
}

/** "Today", "Tomorrow", "In 5 days", "Yesterday", "3 days ago". */
export function relativeDay(day: string, today: string): string {
  const diff = daysBetween(today, day);
  if (diff === 0) return "Today";
  if (diff === 1) return "Tomorrow";
  if (diff === -1) return "Yesterday";
  if (diff > 1) return diff < 14 ? `In ${diff} days` : `In ${Math.round(diff / 7)} weeks`;
  return -diff < 14 ? `${-diff} days ago` : `${Math.round(-diff / 7)} weeks ago`;
}

export function matchesFilter(event: CalendarEvent, filter: CalendarFilter): boolean {
  if (filter === "earnings") return event.kind === "earnings";
  if (filter === "dividends") return event.kind !== "earnings";
  return true;
}

export function eventsByDay(events: CalendarEvent[]): Map<string, CalendarEvent[]> {
  const byDay = new Map<string, CalendarEvent[]>();
  for (const event of events) byDay.set(event.day, [...(byDay.get(event.day) ?? []), event]);
  return byDay;
}

export interface AgendaGroup {
  key: string;
  title: string;
  events: CalendarEvent[];
}

/** Upcoming events in the groups people plan by: today, this week, next week, then months. */
export function agendaGroups(events: CalendarEvent[], today: string): AgendaGroup[] {
  const weekday = (parseDay(today).getUTCDay() + 6) % 7;
  const endOfWeek = addDays(today, 6 - weekday);
  const endOfNextWeek = addDays(endOfWeek, 7);
  const groups: AgendaGroup[] = [];
  const push = (key: string, title: string, event: CalendarEvent) => {
    const last = groups.at(-1);
    if (last && last.key === key) last.events.push(event);
    else groups.push({ key, title, events: [event] });
  };
  for (const event of events) {
    if (event.day < today) continue;
    if (event.day === today) push("today", "Today", event);
    else if (event.day <= endOfWeek) push("week", "This week", event);
    else if (event.day <= endOfNextWeek) push("next", "Next week", event);
    else push(monthKey(event.day), monthTitle(monthKey(event.day)), event);
  }
  return groups;
}

/** A stable categorical colour per ticker, so a company looks the same everywhere. */
export function tickerHue(ticker: string): string {
  let hash = 0;
  for (const char of ticker) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return `var(--series-${(hash % 7) + 1})`;
}

/** "NVDA" from "NVDA_US_EQ", "VUAG" from "VUAGl_EQ". */
export function tickerSymbol(ticker: string): string {
  const base = ticker.split("_")[0];
  return base.replace(/[a-z]+$/, "") || base;
}

export interface PayMatrix {
  months: string[];
  rows: { ticker: string; name: string | null; cells: { month: string; amount: number; confirmed: boolean }[]; total: number }[];
  totals: number[];
  max: number;
}

/** Holdings by month for the next twelve months: who pays when, and how much. */
export function payMatrix(events: CalendarEvent[], today: string): PayMatrix {
  const months = Array.from({ length: 12 }, (_, index) => shiftMonth(monthKey(today), index));
  const rows = new Map<string, PayMatrix["rows"][number]>();
  for (const event of events) {
    if (event.kind !== "dividend" || event.past || event.day < today) continue;
    const column = months.indexOf(monthKey(event.day));
    if (column < 0) continue;
    const row =
      rows.get(event.ticker) ??
      {
        ticker: event.ticker,
        name: event.name,
        cells: months.map((month) => ({ month, amount: 0, confirmed: false })),
        total: 0,
      };
    const amount = Number(event.amountEur ?? 0);
    row.cells[column].amount += amount;
    row.cells[column].confirmed ||= event.confirmed;
    row.total += amount;
    rows.set(event.ticker, row);
  }
  const ordered = [...rows.values()].sort((a, b) => b.total - a.total);
  const totals = months.map((_, column) => ordered.reduce((sum, row) => sum + row.cells[column].amount, 0));
  const max = Math.max(0, ...ordered.flatMap((row) => row.cells.map((cell) => cell.amount)));
  return { months, rows: ordered, totals, max };
}
