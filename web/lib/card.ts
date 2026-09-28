/**
 * Card spending grouped the ways a person thinks about it: by day, week and month, by merchant
 * and by category, and for one merchant, by weekday and hour.
 *
 * Everything runs in the server's local time zone. For the desktop app that is the owner's own
 * machine, so a 00:30 payment counts on the day it was actually made, not on the UTC date
 * before it. Weeks start on Monday.
 */
import { decimalToNumber } from "./format";
import type { CardTransaction } from "./types";

export type Granularity = "day" | "week" | "month";

export const GRANULARITIES: { key: Granularity; label: string }[] = [
  { key: "day", label: "Day" },
  { key: "week", label: "Week" },
  { key: "month", label: "Month" },
];

/** How far back each view reaches on the chart, so bars stay readable. */
const WINDOW: Record<Granularity, number> = { day: 45, week: 26, month: 36 };

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export interface CashbackLike {
  ts: string;
  amount: string;
}

export interface Bucket {
  key: string;
  label: string;
  /** Spent in the bucket, positive (refunds netted off). */
  spent: number;
  cashback: number;
  count: number;
}

export interface Group {
  key: string;
  spent: number;
  count: number;
  /** ISO timestamp of the most recent payment in the group. */
  last: string;
}

export function parseGranularity(value: string | undefined): Granularity {
  return value === "day" || value === "week" || value === "month" ? value : "month";
}

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

function dateKey(date: Date): string {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** Monday of the local week containing `date`. */
function weekStart(date: Date): Date {
  const start = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  start.setDate(start.getDate() - ((start.getDay() + 6) % 7));
  return start;
}

/** The bucket a moment falls in: "2026-09-26", the week's Monday "2026-09-22", or "2026-09". */
export function bucketKey(value: string | Date, granularity: Granularity): string {
  const date = typeof value === "string" ? new Date(value) : value;
  if (granularity === "day") return dateKey(date);
  if (granularity === "week") return dateKey(weekStart(date));
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}`;
}

function keyToDate(key: string): Date {
  const [year, month, day] = key.split("-").map(Number);
  return new Date(year, (month ?? 1) - 1, day ?? 1);
}

/** "Fri 26 Sep", "22–28 Sep" (or "29 Sep – 5 Oct"), "Sep 2026". */
export function bucketLabel(key: string, granularity: Granularity): string {
  const date = keyToDate(key);
  if (granularity === "month") return `${MONTHS[date.getMonth()]} ${date.getFullYear()}`;
  if (granularity === "day") {
    return `${WEEKDAYS[(date.getDay() + 6) % 7]} ${date.getDate()} ${MONTHS[date.getMonth()]}`;
  }
  const end = new Date(date);
  end.setDate(end.getDate() + 6);
  return date.getMonth() === end.getMonth()
    ? `${date.getDate()}–${end.getDate()} ${MONTHS[date.getMonth()]}`
    : `${date.getDate()} ${MONTHS[date.getMonth()]} – ${end.getDate()} ${MONTHS[end.getMonth()]}`;
}

function step(date: Date, granularity: Granularity, by: number): Date {
  const next = new Date(date);
  if (granularity === "day") next.setDate(next.getDate() + by);
  else if (granularity === "week") next.setDate(next.getDate() + 7 * by);
  else next.setMonth(next.getMonth() + by, 1);
  return next;
}

/** The bucket before or after `key`. */
export function shiftBucket(key: string, granularity: Granularity, by: number): string {
  return bucketKey(step(keyToDate(key), granularity, by), granularity);
}

export function amountOf(item: { amount: string }): number {
  return decimalToNumber(item.amount) ?? 0;
}

/**
 * One bucket per day/week/month, oldest first, with no gaps: a quiet week shows as a zero,
 * not as a missing bar. Reaches back at most `WINDOW[granularity]` buckets from `now`.
 */
export function buildBuckets(
  transactions: CardTransaction[],
  cashback: CashbackLike[],
  granularity: Granularity,
  now: Date = new Date(),
  window: number = WINDOW[granularity],
): Bucket[] {
  const spent = new Map<string, number>();
  const counts = new Map<string, number>();
  const back = new Map<string, number>();
  for (const item of transactions) {
    const key = bucketKey(item.ts, granularity);
    spent.set(key, (spent.get(key) ?? 0) - amountOf(item));
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  for (const item of cashback) {
    const key = bucketKey(item.ts, granularity);
    back.set(key, (back.get(key) ?? 0) + amountOf(item));
  }
  if (transactions.length === 0) return [];

  const oldest = transactions.reduce(
    (earliest, item) => (item.ts < earliest ? item.ts : earliest),
    transactions[0].ts,
  );
  const lastKey = bucketKey(now, granularity);
  let key = bucketKey(oldest, granularity);
  const earliestAllowed = shiftBucket(lastKey, granularity, -(window - 1));
  if (key < earliestAllowed) key = earliestAllowed;

  const buckets: Bucket[] = [];
  while (key <= lastKey && buckets.length < 2000) {
    buckets.push({
      key,
      label: bucketLabel(key, granularity),
      spent: round(spent.get(key) ?? 0),
      cashback: round(back.get(key) ?? 0),
      count: counts.get(key) ?? 0,
    });
    key = shiftBucket(key, granularity, 1);
  }
  return buckets;
}

export function inBucket(ts: string, key: string, granularity: Granularity): boolean {
  return bucketKey(ts, granularity) === key;
}

/** Group payments by merchant or category, most spent first. */
export function groupBy(
  transactions: CardTransaction[],
  keyOf: (item: CardTransaction) => string,
): Group[] {
  const groups = new Map<string, Group>();
  for (const item of transactions) {
    const key = keyOf(item);
    const group = groups.get(key) ?? { key, spent: 0, count: 0, last: item.ts };
    group.spent = round(group.spent - amountOf(item));
    group.count += 1;
    if (item.ts > group.last) group.last = item.ts;
    groups.set(key, group);
  }
  return [...groups.values()].sort((a, b) => b.spent - a.spent || a.key.localeCompare(b.key));
}

export function merchantOf(item: CardTransaction): string {
  return item.merchantName ?? "Unknown merchant";
}

export interface MerchantProfile {
  name: string;
  categories: string[];
  spent: number;
  count: number;
  average: number;
  largest: CardTransaction | null;
  first: string | null;
  last: string | null;
  /** Spent per weekday, Monday first. */
  byWeekday: number[];
  /** Spent per local hour, 0-23. */
  byHour: number[];
  transactions: CardTransaction[];
}

/** Everything about one merchant: how much, how often, and on which days and hours. */
export function merchantProfile(transactions: CardTransaction[], name: string): MerchantProfile {
  const own = transactions
    .filter((item) => merchantOf(item) === name)
    .sort((a, b) => b.ts.localeCompare(a.ts));
  const byWeekday = Array.from({ length: 7 }, () => 0);
  const byHour = Array.from({ length: 24 }, () => 0);
  let spent = 0;
  let largest: CardTransaction | null = null;
  for (const item of own) {
    const amount = -amountOf(item);
    spent += amount;
    const date = new Date(item.ts);
    byWeekday[(date.getDay() + 6) % 7] += amount;
    byHour[date.getHours()] += amount;
    if (largest === null || amountOf(item) < amountOf(largest)) largest = item;
  }
  return {
    name,
    categories: [...new Set(own.map((item) => item.merchantCategory).filter(Boolean))] as string[],
    spent: round(spent),
    count: own.length,
    average: own.length > 0 ? round(spent / own.length) : 0,
    largest,
    first: own.at(-1)?.ts ?? null,
    last: own[0]?.ts ?? null,
    byWeekday: byWeekday.map(round),
    byHour: byHour.map(round),
    transactions: own,
  };
}

/** "Fri 26 Sep 2026, 10:34" in local time. */
export function formatLocalDateTime(ts: string): string {
  const date = new Date(ts);
  return `${WEEKDAYS[(date.getDay() + 6) % 7]} ${date.getDate()} ${MONTHS[date.getMonth()]} ${date.getFullYear()}, ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function round(value: number): number {
  return Math.round(value * 100) / 100;
}

export function merchantHref(name: string): string {
  return `/card/merchant/${encodeURIComponent(name)}`;
}

/**
 * A bucket key from the URL, snapped to the current view: "2026-09-26" opened in the month view
 * becomes "2026-09", and in the week view that week's Monday. Anything else is ignored.
 */
export function normaliseBucketKey(value: string | undefined, granularity: Granularity): string | null {
  if (!value || !/^\d{4}-\d{2}(-\d{2})?$/.test(value)) return null;
  return bucketKey(keyToDate(value.length === 7 ? `${value}-01` : value), granularity);
}
