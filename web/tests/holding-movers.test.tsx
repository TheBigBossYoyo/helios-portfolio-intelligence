import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { HoldingMovers } from "@/components/holding-movers";
import { NewsFeed } from "@/components/news-feed";
import { displayTicker, plainText } from "@/lib/format";
import type { HoldingMovement, NewsItem, PeriodSummary } from "@/lib/types";

function holding(overrides: Partial<HoldingMovement>): HoldingMovement {
  return {
    ticker: "MU_US_EQ",
    name: "Micron Technology",
    status: "ok",
    startValueEur: "185.49",
    endValueEur: "219.18",
    startQuantity: "1",
    endQuantity: "1",
    boughtEur: "0",
    soldEur: "0",
    dividendsEur: "0",
    resultEur: "33.69",
    returnPct: 0.1816,
    priceChangePct: 0.1816,
    detail: null,
    ...overrides,
  };
}

function story(overrides: Partial<NewsItem>): NewsItem {
  return {
    dedupeKey: "s1",
    feedKey: "f",
    sourceLabel: "Example",
    t212Ticker: "MU_US_EQ",
    isin: null,
    headline: "Micron beats estimates",
    summary: null,
    url: "https://example.com/mu",
    publishedAt: "2026-09-20T09:00:00Z",
    fetchedAt: "2026-09-20T10:00:00Z",
    relevance: "headline",
    matchedTerm: "Micron",
    held: true,
    ...overrides,
  };
}

const PERIOD: PeriodSummary = {
  key: "1M",
  label: "1 month",
  status: "ok",
  startDate: "2026-08-27",
  endDate: "2026-09-26",
  startValueEur: "1385.28",
  endValueEur: "1355.51",
  valueChangeEur: "-29.77",
  depositsEur: "424.37",
  withdrawalsEur: "-461.67",
  netDepositsEur: "-37.30",
  investmentResultEur: "7.53",
  marketEur: "10.48",
  dividendsEur: "0.32",
  interestEur: "0.37",
  feesEur: "-3.64",
  twr: 0.0031,
  unvaluedDays: 0,
  detail: null,
  unattributedEur: "-3.27",
  holdings: [
    holding({}),
    holding({
      ticker: "ACHV_US_EQ",
      name: "Achieve Life Sciences",
      resultEur: "-17.43",
      returnPct: -0.0587,
      priceChangePct: -0.0587,
    }),
    holding({
      ticker: "VUAGl_EQ",
      name: "Vanguard S&P 500 (Acc)",
      startQuantity: "0.05",
      boughtEur: "108.56",
      resultEur: "-5.39",
      returnPct: null,
      priceChangePct: null,
    }),
    holding({ ticker: "OLD_US_EQ", name: null, status: "insufficient_data", resultEur: null }),
  ],
};

describe("HoldingMovers", () => {
  it("puts each holding under Rose or Fell, largest move first", () => {
    render(<HoldingMovers news={[]} period={PERIOD} />);

    const fell = screen.getByRole("region", { name: /^Fell/ });
    const rose = screen.getByRole("region", { name: /^Rose/ });
    expect(within(rose).getByText("MU")).toBeInTheDocument();
    const fellText = fell.textContent ?? "";
    expect(fellText.indexOf("ACHV")).toBeLessThan(fellText.indexOf("VUAG"));
    expect(within(fell).getByText("−€17.43")).toBeInTheDocument();
  });

  it("describes a purchase as a fact, not as part of the result", () => {
    render(<HoldingMovers news={[]} period={PERIOD} />);

    expect(screen.getByText(/added €108\.56/)).toBeInTheDocument();
  });

  it("reconciles the rows to the investment result", () => {
    render(<HoldingMovers news={[]} period={PERIOD} />);

    expect(screen.getByText("Interest, cashback, fees and other cash")).toBeInTheDocument();
    expect(screen.getByText("−€3.27")).toBeInTheDocument();
    expect(screen.getByText("+€7.53")).toBeInTheDocument();
    expect(screen.getByText(/Not split for lack of a price/)).toHaveTextContent("OLD");
  });

  it("shows the newest story that names the holding, only from inside the period", () => {
    render(
      <HoldingMovers
        news={[
          story({ dedupeKey: "late", headline: "Too late", publishedAt: "2026-10-15T09:00:00Z" }),
          story({ dedupeKey: "loose", headline: "Loose", relevance: "unconfirmed" }),
          story({}),
          story({ dedupeKey: "early", headline: "Too early", publishedAt: "2026-08-01T09:00:00Z" }),
        ]}
        period={PERIOD}
      />,
    );

    expect(screen.getByRole("link", { name: /Micron beats estimates/ })).toBeInTheDocument();
    expect(screen.queryByText("Too late")).not.toBeInTheDocument();
    expect(screen.queryByText("Loose")).not.toBeInTheDocument();
    expect(screen.queryByText("Too early")).not.toBeInTheDocument();
  });
});

describe("NewsFeed relevance", () => {
  it("says which words matched, or that none did", () => {
    render(
      <NewsFeed
        items={[
          story({}),
          story({ dedupeKey: "u", headline: "2 stocks to buy", relevance: "unconfirmed" }),
          story({ dedupeKey: "s", headline: "Chips week", relevance: "summary", held: false }),
        ]}
      />,
    );

    expect(screen.getByText("Names Micron")).toBeInTheDocument();
    expect(screen.getByText("Doesn't name the holding")).toBeInTheDocument();
    expect(screen.getByText("Summary names Micron · no longer held")).toBeInTheDocument();
    expect(screen.getAllByText("MU")).toHaveLength(3);
  });

  it("shows a feed summary as text, never as markup", () => {
    render(
      <NewsFeed
        items={[story({ summary: '<a href="https://x">Micron</a> &amp; peers rally' })]}
      />,
    );

    expect(screen.getByText("Micron & peers rally")).toBeInTheDocument();
  });
});

describe("NewsFeed summaries", () => {
  it("hides a summary that only repeats the headline", () => {
    render(
      <NewsFeed
        items={[
          story({
            headline: "Micron could be undervalued - simplywall.st",
            summary: '<a href="https://x">Micron could be undervalued</a>&nbsp;&nbsp;simplywall.st',
          }),
        ]}
      />,
    );

    expect(screen.queryByText(/simplywall\.st$/, { selector: "p" })).not.toBeInTheDocument();
  });
});

describe("displayTicker and plainText", () => {
  it("drops the Trading 212 suffixes", () => {
    expect(displayTicker("VUAGl_EQ")).toBe("VUAG");
    expect(displayTicker("MU_US_EQ")).toBe("MU");
    expect(displayTicker("plain")).toBe("plain");
  });

  it("strips tags and decodes common entities", () => {
    expect(plainText("<b>A</b>&nbsp;&amp;&#39;B&#39;")).toBe("A &'B'");
    expect(plainText(null)).toBe("");
  });
});
