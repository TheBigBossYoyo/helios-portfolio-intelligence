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

// ---------------------------------------------------------------------------
// Payments not labelled yet
// ---------------------------------------------------------------------------

export const PENDING_CATEGORY = "PENDING_EXPORT";
export const PENDING_MERCHANT = "Not labelled yet";

/**
 * Withdrawals made after the last export, as card rows. On this account almost every
 * withdrawal is a card payment, so they count toward today's and this week's spending, marked
 * as not labelled until the next daily export names the merchant.
 */
export function pendingAsTransactions(
  unlabelled: { reference: string; ts: string; amount: string; currency: string | null }[],
): CardTransaction[] {
  return unlabelled.map((item) => ({
    rowId: `pending-${item.reference}`,
    ts: item.ts,
    action: "Pending",
    amount: item.amount,
    currency: item.currency,
    merchantName: PENDING_MERCHANT,
    merchantCategory: PENDING_CATEGORY,
  }));
}

export function isPending(item: CardTransaction): boolean {
  return item.action === "Pending";
}

// ---------------------------------------------------------------------------
// Recurring payments
// ---------------------------------------------------------------------------

const DAY_MS = 86_400_000;
const DAYS_PER_MONTH = 30.44;

export type RecurringStatus = "active" | "new" | "price-up" | "lapsed";

export interface Recurring {
  merchant: string;
  category: string | null;
  cadence: string;
  intervalDays: number;
  /** The usual charge, positive. */
  amount: number;
  /** The charge before the latest one, when it differs. */
  previousAmount: number | null;
  /** What it costs per month at its cadence. */
  monthlyCost: number;
  count: number;
  first: string;
  last: string;
  next: string;
  status: RecurringStatus;
}

function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

function cadenceLabel(days: number): string {
  if (days >= 6 && days <= 8) return "Weekly";
  if (days >= 13 && days <= 16) return "Every 2 weeks";
  if (days >= 26 && days <= 35) return "Monthly";
  if (days >= 80 && days <= 100) return "Quarterly";
  if (days >= 340 && days <= 390) return "Yearly";
  return `Every ~${Math.round(days)} days`;
}

/**
 * How many charges fall in a month. A named cadence uses its calendar meaning -- a monthly
 * charge is exactly one a month even though February makes the measured gap ~28.6 days --
 * and anything irregular uses the measured interval.
 */
function chargesPerMonth(days: number): number {
  const label = cadenceLabel(days);
  if (label === "Weekly") return 52 / 12;
  if (label === "Every 2 weeks") return 26 / 12;
  if (label === "Monthly") return 1;
  if (label === "Quarterly") return 1 / 3;
  if (label === "Yearly") return 1 / 12;
  return DAYS_PER_MONTH / days;
}

/** The next charge: same day next month (or year) for calendar cadences, else by interval. */
function nextCharge(last: string, days: number): Date {
  const next = new Date(last);
  const label = cadenceLabel(days);
  if (label === "Monthly") next.setUTCMonth(next.getUTCMonth() + 1);
  else if (label === "Yearly") next.setUTCFullYear(next.getUTCFullYear() + 1);
  else next.setTime(next.getTime() + days * DAY_MS);
  return next;
}

/** Charges of about the same amount (within 15%) are one subscription; others are separate. */
function amountClusters(items: CardTransaction[]): CardTransaction[][] {
  const clusters: CardTransaction[][] = [];
  for (const item of [...items].sort((a, b) => a.ts.localeCompare(b.ts))) {
    const amount = -amountOf(item);
    const home = clusters.find((cluster) => {
      const typical = median(cluster.map((entry) => -amountOf(entry)));
      return Math.abs(amount - typical) <= typical * 0.15;
    });
    if (home) home.push(item);
    else clusters.push([item]);
  }
  return clusters;
}

/**
 * Subscriptions and other regular charges: the same merchant charging about the same amount at
 * a steady interval. Two charges count when they are a month (or a year) apart; three or more
 * when most gaps are within a quarter of the usual one. A charge that is overdue by half its
 * interval again is flagged as possibly cancelled.
 */
export function detectRecurring(transactions: CardTransaction[], now: Date = new Date()): Recurring[] {
  const byMerchant = new Map<string, CardTransaction[]>();
  for (const item of transactions) {
    if (isPending(item) || amountOf(item) >= 0) continue;
    const key = merchantOf(item);
    byMerchant.set(key, [...(byMerchant.get(key) ?? []), item]);
  }

  const found: Recurring[] = [];
  for (const [merchant, items] of byMerchant) {
    for (const cluster of amountClusters(items)) {
      if (cluster.length < 2) continue;
      const times = cluster.map((item) => new Date(item.ts).getTime());
      const gaps = times.slice(1).map((time, index) => (time - times[index]) / DAY_MS);
      if (gaps.some((gap) => gap < 3)) continue; // same-week repeats are not a schedule
      const interval = median(gaps);
      const regular =
        cluster.length === 2
          ? (interval >= 26 && interval <= 35) || (interval >= 340 && interval <= 390)
          : interval >= 6 &&
            interval <= 400 &&
            gaps.filter((gap) => Math.abs(gap - interval) <= interval * 0.25).length >=
              Math.ceil(gaps.length * 0.7);
      if (!regular) continue;

      const amounts = cluster.map((item) => -amountOf(item));
      const latest = amounts.at(-1) ?? 0;
      const before = amounts.at(-2) ?? latest;
      const last = cluster.at(-1)?.ts ?? cluster[0].ts;
      const lastTime = new Date(last).getTime();
      const overdue = (now.getTime() - lastTime) / DAY_MS > interval * 1.5;
      // New: only its first couple of charges so far, and those recent.
      const young = cluster.length <= 2 && (now.getTime() - times[0]) / DAY_MS < interval * 2.5;
      const status: RecurringStatus = overdue
        ? "lapsed"
        : latest > before * 1.01
          ? "price-up"
          : young
            ? "new"
            : "active";
      found.push({
        merchant,
        category: cluster.at(-1)?.merchantCategory ?? null,
        cadence: cadenceLabel(interval),
        intervalDays: interval,
        amount: round(latest),
        previousAmount: Math.abs(latest - before) >= 0.01 ? round(before) : null,
        monthlyCost: round(latest * chargesPerMonth(interval)),
        count: cluster.length,
        first: cluster[0].ts,
        last,
        next: nextCharge(last, interval).toISOString(),
        status,
      });
    }
  }
  // Still-running charges first, most expensive per month first.
  return found.sort(
    (a, b) =>
      Number(a.status === "lapsed") - Number(b.status === "lapsed") || b.monthlyCost - a.monthlyCost,
  );
}

// ---------------------------------------------------------------------------
// Budgets
// ---------------------------------------------------------------------------

export interface BudgetLine {
  category: string;
  limit: number | null;
  spent: number;
  /** Spent so far, extrapolated to the whole month at the same daily rate. */
  projected: number;
}

/** This month's spending per category against its budget, budgeted categories first. */
export function budgetLines(
  transactions: CardTransaction[],
  budgets: { category: string; monthlyLimit: string }[],
  now: Date = new Date(),
): BudgetLine[] {
  const month = bucketKey(now, "month");
  const daysInMonth = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate();
  const elapsed = Math.max(now.getDate(), 1);
  const spent = new Map<string, number>();
  for (const item of transactions) {
    if (isPending(item) || bucketKey(item.ts, "month") !== month) continue;
    const key = item.merchantCategory ?? "UNCATEGORISED";
    spent.set(key, (spent.get(key) ?? 0) - amountOf(item));
  }
  const categories = new Set([
    ...budgets.map((budget) => budget.category),
    ...transactions
      .filter((item) => !isPending(item))
      .map((item) => item.merchantCategory ?? "UNCATEGORISED"),
  ]);
  const limits = new Map(budgets.map((budget) => [budget.category, Number(budget.monthlyLimit)]));
  return [...categories]
    .map((category) => {
      const value = round(spent.get(category) ?? 0);
      return {
        category,
        limit: limits.get(category) ?? null,
        spent: value,
        projected: round((value / elapsed) * daysInMonth),
      };
    })
    .sort(
      (a, b) =>
        Number(b.limit !== null) - Number(a.limit !== null) ||
        b.spent - a.spent ||
        a.category.localeCompare(b.category),
    );
}
