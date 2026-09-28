import { describe, expect, it } from "vitest";
import {
  bucketKey,
  bucketLabel,
  buildBuckets,
  groupBy,
  merchantOf,
  merchantProfile,
  normaliseBucketKey,
  shiftBucket,
} from "@/lib/card";
import type { CardTransaction } from "@/lib/types";

function pay(ts: string, amount: string, merchant: string | null = "Grocer"): CardTransaction {
  return {
    rowId: `${ts}-${merchant}`,
    ts,
    action: "Card debit",
    amount,
    currency: "EUR",
    merchantName: merchant,
    merchantCategory: "MISCELLANEOUS",
  };
}

const PAYMENTS = [
  pay("2026-09-26T10:34:00Z", "-10.00"), // Saturday
  pay("2026-09-26T18:00:00Z", "-5.00", "Cafe"),
  pay("2026-09-22T09:00:00Z", "-20.00"), // Tuesday, same week
  pay("2026-09-15T09:00:00Z", "-40.00"), // Tuesday, the week before
  pay("2026-08-31T23:30:00Z", "-7.50", null), // August, Monday
];

describe("buckets", () => {
  it("keys days, Monday-start weeks and months", () => {
    expect(bucketKey("2026-09-26T10:34:00Z", "day")).toBe("2026-09-26");
    expect(bucketKey("2026-09-26T10:34:00Z", "week")).toBe("2026-09-21");
    expect(bucketKey("2026-09-21T00:00:00Z", "week")).toBe("2026-09-21");
    expect(bucketKey("2026-09-26T10:34:00Z", "month")).toBe("2026-09");
  });

  it("labels each kind readably", () => {
    expect(bucketLabel("2026-09-26", "day")).toBe("Sat 26 Sep");
    expect(bucketLabel("2026-09-21", "week")).toBe("21–27 Sep");
    expect(bucketLabel("2026-09-28", "week")).toBe("28 Sep – 4 Oct");
    expect(bucketLabel("2026-09", "month")).toBe("Sep 2026");
  });

  it("steps across month and year boundaries", () => {
    expect(shiftBucket("2026-01", "month", -1)).toBe("2025-12");
    expect(shiftBucket("2026-09-28", "week", 1)).toBe("2026-10-05");
    expect(shiftBucket("2026-03-01", "day", -1)).toBe("2026-02-28");
  });

  it("snaps a URL key to the current view and ignores junk", () => {
    expect(normaliseBucketKey("2026-09-26", "month")).toBe("2026-09");
    expect(normaliseBucketKey("2026-09-26", "week")).toBe("2026-09-21");
    expect(normaliseBucketKey("2026-09", "day")).toBe("2026-09-01");
    expect(normaliseBucketKey("../etc", "day")).toBeNull();
    expect(normaliseBucketKey(undefined, "day")).toBeNull();
  });

  it("fills quiet periods with zeros, oldest first, up to now", () => {
    const now = new Date("2026-09-28T12:00:00Z");
    const weeks = buildBuckets(PAYMENTS, [{ ts: "2026-09-23T01:00:00Z", amount: "0.40" }], "week", now);

    expect(weeks.map((bucket) => bucket.key)).toEqual([
      "2026-08-31",
      "2026-09-07",
      "2026-09-14",
      "2026-09-21",
      "2026-09-28",
    ]);
    expect(weeks[1]).toMatchObject({ spent: 0, count: 0 });
    expect(weeks[3]).toMatchObject({ spent: 35, count: 3, cashback: 0.4 });
    expect(weeks[4]).toMatchObject({ spent: 0, count: 0 });
  });

  it("limits how far back the chart reaches", () => {
    const now = new Date("2026-09-28T12:00:00Z");
    expect(buildBuckets(PAYMENTS, [], "day", now, 3).map((bucket) => bucket.key)).toEqual([
      "2026-09-26",
      "2026-09-27",
      "2026-09-28",
    ]);
  });
});

describe("groups and merchants", () => {
  it("ranks merchants by spending and names unknown ones", () => {
    const groups = groupBy(PAYMENTS, merchantOf);

    expect(groups.map((group) => [group.key, group.spent, group.count])).toEqual([
      ["Grocer", 70, 3],
      ["Unknown merchant", 7.5, 1],
      ["Cafe", 5, 1],
    ]);
    expect(groups[0].last).toBe("2026-09-26T10:34:00Z");
  });

  it("profiles one merchant by weekday and hour", () => {
    const profile = merchantProfile(PAYMENTS, "Grocer");

    expect(profile).toMatchObject({ count: 3, spent: 70, average: 23.33 });
    expect(profile.largest?.amount).toBe("-40.00");
    expect(profile.first).toBe("2026-09-15T09:00:00Z");
    expect(profile.last).toBe("2026-09-26T10:34:00Z");
    // Monday first: the two Tuesdays (22 and 15 Sep) and one Saturday (26 Sep).
    expect(profile.byWeekday).toEqual([0, 60, 0, 0, 0, 10, 0]);
    expect(profile.byHour[9]).toBe(60);
    expect(profile.byHour[10]).toBe(10);
  });
});
