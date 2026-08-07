import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { NewsFeed, NewsSourceNote } from "@/components/news-feed";
import { parseNewsItems } from "@/lib/api";
import { newsItems } from "./fixtures";

describe("parseNewsItems", () => {
  it("round-trips the fixture", () => {
    expect(parseNewsItems(newsItems())).toEqual(newsItems());
  });

  it("rejects an item with no source label", () => {
    const [item] = newsItems();
    expect(parseNewsItems([{ ...item, sourceLabel: null }])).toBeNull();
  });

  it("rejects an item with no link", () => {
    const [item] = newsItems();
    expect(parseNewsItems([{ ...item, url: undefined }])).toBeNull();
  });

  it("rejects a non-array payload", () => {
    expect(parseNewsItems({ items: [] })).toBeNull();
  });
});

describe("NewsFeed", () => {
  it("attributes every item and links out to the publisher", () => {
    render(<NewsFeed items={newsItems()} />);

    const link = screen.getByRole("link", { name: "Apple beats expectations" });
    expect(link).toHaveAttribute("href", "https://example.com/a");
    // Untrusted third-party links must not get access to the opener.
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
    expect(link).toHaveAttribute("target", "_blank");
    expect(screen.getAllByText("Example Markets")).toHaveLength(2);
  });

  it("labels an undated item rather than inventing a date", () => {
    render(<NewsFeed items={newsItems()} />);

    expect(screen.getByText("Undated")).toBeInTheDocument();
  });

  it("marks an unattributed item as market-wide instead of guessing a ticker", () => {
    render(<NewsFeed items={newsItems()} />);

    expect(screen.getByText("AAPL_US_EQ")).toBeInTheDocument();
    expect(screen.getByText("Market-wide")).toBeInTheDocument();
  });

  it("hides summaries in compact mode", () => {
    const { rerender } = render(<NewsFeed items={newsItems()} />);
    expect(screen.getByText("Quarterly results came in ahead.")).toBeInTheDocument();

    rerender(<NewsFeed compact items={newsItems()} />);
    expect(screen.queryByText("Quarterly results came in ahead.")).not.toBeInTheDocument();
  });

  it("explains how to add sources when nothing is stored", () => {
    render(<NewsFeed items={[]} />);

    expect(screen.getByText(/No stored articles/)).toBeInTheDocument();
    expect(screen.getByText("config/news_feeds.yaml")).toBeInTheDocument();
  });
});

describe("NewsSourceNote", () => {
  it("names the sources and states that article text is never stored", () => {
    render(<NewsSourceNote sources={["Example Markets", "Other Publisher"]} />);

    expect(screen.getByText(/Example Markets, Other Publisher/)).toBeInTheDocument();
    expect(screen.getByText(/does not fetch or store\s+article text/)).toBeInTheDocument();
  });
});
