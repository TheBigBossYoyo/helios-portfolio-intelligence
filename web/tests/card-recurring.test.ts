import { describe, expect, it } from "vitest";
import { budgetLines, detectRecurring, pendingAsTransactions } from "@/lib/card";
import type { CardTransaction } from "@/lib/types";

function pay(ts: string, amount: string, merchant: string, category = "UTILITIES"): CardTransaction {
  return {
    rowId: `${ts}-${merchant}-${amount}`,
    ts,
    action: "Card debit",
    amount,
    currency: "EUR",
    merchantName: merchant,
    merchantCategory: category,
  };
}

const NOW = new Date("2026-09-28T12:00:00Z");

describe("detectRecurring", () => {
  it("finds a monthly subscription and costs it at one charge a month", () => {
    const [netflix] = detectRecurring(
      [
        pay("2026-07-26T02:00:00Z", "-8.56", "Netflix"),
        pay("2026-08-26T02:00:00Z", "-8.56", "Netflix"),
        pay("2026-09-26T02:00:00Z", "-8.56", "Netflix"),
      ],
      NOW,
    );

    expect(netflix).toMatchObject({
      merchant: "Netflix",
      cadence: "Monthly",
      amount: 8.56,
      monthlyCost: 8.56,
      count: 3,
      status: "active",
      previousAmount: null,
    });
    expect(netflix.next.slice(0, 10)).toBe("2026-10-26");
  });

  it("flags a price rise and keeps the old price", () => {
    const [netflix] = detectRecurring(
      [
        pay("2026-08-26T02:00:00Z", "-8.56", "Netflix"),
        pay("2026-09-26T02:00:00Z", "-8.78", "Netflix"),
      ],
      NOW,
    );

    expect(netflix).toMatchObject({ status: "price-up", amount: 8.78, previousAmount: 8.56 });
  });

  it("separates two subscriptions at one merchant by their amounts", () => {
    const found = detectRecurring(
      [
        pay("2026-08-15T10:00:00Z", "-3.50", "Apple"),
        pay("2026-08-26T10:00:00Z", "-1.16", "Apple"),
        pay("2026-09-15T10:00:00Z", "-3.50", "Apple"),
        pay("2026-09-26T10:00:00Z", "-1.16", "Apple"),
        pay("2026-09-12T10:00:00Z", "-57.21", "Apple"), // a one-off purchase
      ],
      NOW,
    );

    expect(found.map((item) => item.amount).sort()).toEqual([1.16, 3.5]);
  });

  it("ignores irregular spending and marks an overdue charge as maybe cancelled", () => {
    const found = detectRecurring(
      [
        pay("2026-05-02T10:00:00Z", "-20", "Gym"),
        pay("2026-06-02T10:00:00Z", "-20", "Gym"),
        pay("2026-07-02T10:00:00Z", "-20", "Gym"),
        pay("2026-09-12T10:00:00Z", "-20.88", "iRacing"),
        pay("2026-09-12T15:00:00Z", "-57.21", "iRacing"),
        pay("2026-09-22T10:00:00Z", "-11.74", "iRacing"),
      ],
      NOW,
    );

    expect(found).toHaveLength(1);
    expect(found[0]).toMatchObject({ merchant: "Gym", status: "lapsed" });
  });
});

describe("budgetLines", () => {
  it("sets this month's spending against budgets and projects the month", () => {
    const lines = budgetLines(
      [
        pay("2026-09-10T10:00:00Z", "-30", "Netflix", "UTILITIES"),
        pay("2026-09-20T10:00:00Z", "-12", "Cafe", "MISCELLANEOUS"),
        pay("2026-08-20T10:00:00Z", "-99", "Cafe", "MISCELLANEOUS"), // last month
      ],
      [{ category: "UTILITIES", monthlyLimit: "40.00" }],
      NOW,
    );

    expect(lines.map((line) => line.category)).toEqual(["UTILITIES", "MISCELLANEOUS"]);
    // 30 spent over 28 days of a 30-day month.
    expect(lines[0]).toMatchObject({ limit: 40, spent: 30, projected: 32.14 });
    expect(lines[1]).toMatchObject({ limit: null, spent: 12 });
  });
});

describe("pendingAsTransactions", () => {
  it("turns unlabelled withdrawals into rows marked as not labelled", () => {
    const [row] = pendingAsTransactions([
      { reference: "r1", ts: "2026-09-28T09:00:00Z", amount: "-4.20", currency: "EUR" },
    ]);

    expect(row).toMatchObject({
      rowId: "pending-r1",
      action: "Pending",
      merchantName: "Not labelled yet",
      merchantCategory: "PENDING_EXPORT",
    });
  });
});
