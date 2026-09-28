import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  ChangeBreakdown,
  PeriodHeadline,
  PeriodTable,
  PeriodTabs,
  periodRange,
  signedEur,
} from "@/components/period-change";
import { rowsInPeriod } from "@/lib/series";
import type { PeriodSummary } from "@/lib/types";

function period(overrides: Partial<PeriodSummary> = {}): PeriodSummary {
  return {
    key: "1M",
    label: "1 month",
    status: "ok",
    startDate: "2026-08-26",
    endDate: "2026-09-26",
    startValueEur: "1000",
    endValueEur: "1560",
    valueChangeEur: "560",
    depositsEur: "500",
    withdrawalsEur: "0",
    netDepositsEur: "500",
    investmentResultEur: "60",
    marketEur: "55",
    dividendsEur: "5",
    interestEur: "0",
    feesEur: "0",
    twr: 0.05,
    unvaluedDays: 0,
    detail: null,
    ...overrides,
  };
}

describe("signedEur", () => {
  it("signs gains and losses and leaves zero unsigned", () => {
    expect(signedEur(12.5)).toMatch(/^\+€12\.50$/);
    expect(signedEur(-3)).toMatch(/^−€3\.00$/);
    expect(signedEur(0)).toBe("€0.00");
  });
});

describe("periodRange", () => {
  it("names both ends, or inception", () => {
    expect(periodRange(period())).toBe("26 Aug 2026 → 26 Sep 2026");
    expect(periodRange(period({ startDate: null }))).toBe("Since you started → 26 Sep 2026");
  });
});

describe("PeriodHeadline", () => {
  it("keeps the deposit out of the investment result", () => {
    render(<PeriodHeadline period={period()} />);

    expect(screen.getByText("+€60.00")).toBeInTheDocument();
    expect(screen.getByText("+5.00% return")).toBeInTheDocument();
    expect(screen.getByText("+€500.00")).toBeInTheDocument();
    expect(screen.getByText("+€560.00")).toBeInTheDocument();
  });
});

describe("ChangeBreakdown", () => {
  it("lists the causes, hides zero ones, and sets money moved apart", () => {
    render(<ChangeBreakdown period={period()} />);

    expect(screen.getByText("Market movement")).toBeInTheDocument();
    expect(screen.getByText("Dividends")).toBeInTheDocument();
    expect(screen.queryByText("Interest")).not.toBeInTheDocument();
    expect(screen.queryByText("Withdrawals")).not.toBeInTheDocument();
    expect(screen.getByText(/Money moved/)).toBeInTheDocument();
    expect(screen.getByText("Deposits")).toBeInTheDocument();
  });

  it("always shows market movement, even on a quiet period", () => {
    render(
      <ChangeBreakdown
        period={period({ depositsEur: "0", netDepositsEur: "0", marketEur: "0", dividendsEur: "0" })}
      />,
    );

    expect(screen.getByText("Market movement")).toBeInTheDocument();
    expect(screen.queryByText(/Money moved/)).not.toBeInTheDocument();
  });
});

describe("ChangeBreakdown with card history", () => {
  it("splits card spending from bank withdrawals and counts cashback as income", () => {
    render(
      <ChangeBreakdown
        period={period({
          depositsEur: "0",
          withdrawalsEur: "-100",
          netDepositsEur: "-100",
          cardSpendingEur: "-70",
          cashbackEur: "0.60",
        })}
      />,
    );

    expect(screen.getByText("Card cashback")).toBeInTheDocument();
    expect(screen.getByText("+€0.60")).toBeInTheDocument();
    expect(screen.getByText("Card spending")).toBeInTheDocument();
    expect(screen.getByText("−€70.00")).toBeInTheDocument();
    expect(screen.getByText("Withdrawals to bank")).toBeInTheDocument();
    expect(screen.getByText("−€30.00")).toBeInTheDocument();
  });
});

describe("PeriodTabs and PeriodTable", () => {
  const periods = [period({ key: "1W", label: "1 week" }), period()];

  it("links each period and marks the selected one", () => {
    render(<PeriodTabs basePath="/" periods={periods} selected="1M" />);

    const tabs = screen.getByRole("navigation", { name: "Period" });
    expect(within(tabs).getByRole("link", { name: "1M" })).toHaveAttribute("aria-current", "true");
    expect(within(tabs).getByRole("link", { name: "1W" })).toHaveAttribute("href", "/?period=1W");
  });

  it("drops the money columns when compact", () => {
    const { rerender } = render(<PeriodTable basePath="/" periods={periods} selected="1M" />);
    expect(screen.getByRole("columnheader", { name: "Money added" })).toBeInTheDocument();

    rerender(<PeriodTable basePath="/" compact periods={periods} selected="1M" />);
    expect(screen.queryByRole("columnheader", { name: "Money added" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "1W" })).toBeInTheDocument();
  });

  it("prints a dash for a period that could not be valued", () => {
    render(
      <PeriodTable
        basePath="/"
        periods={[period({ status: "insufficient_data", twr: null, investmentResultEur: null })]}
        selected=""
      />,
    );

    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(2);
  });
});

describe("rowsInPeriod", () => {
  const rows = ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"].map((date) => ({ date }));

  it("keeps the starting close and the end, inclusive", () => {
    expect(rowsInPeriod(rows, "2026-09-02", "2026-09-03").map((row) => row.date)).toEqual([
      "2026-09-02",
      "2026-09-03",
    ]);
  });

  it("keeps everything from inception", () => {
    expect(rowsInPeriod(rows, null, null)).toHaveLength(4);
  });
});
