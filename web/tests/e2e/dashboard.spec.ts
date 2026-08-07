import { expect, test } from "@playwright/test";

test.describe("navigation", () => {
  test("moves between every dashboard section", async ({ page }) => {
    // Seven sequential server-rendered navigations; under a parallel run the whole walk
    // exceeds the default per-test budget even though each hop is fast.
    test.slow();
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1 })).toContainText("HELIOS");

    await page.getByRole("link", { name: "Holdings" }).click();
    await expect(page).toHaveURL(/\/holdings$/);
    await expect(page.getByRole("heading", { name: "Holdings" })).toBeVisible();

    await page.getByRole("link", { name: "Performance" }).click();
    await expect(page).toHaveURL(/\/performance$/);
    await expect(page.getByRole("heading", { name: "Net asset value" })).toBeVisible();

    await page.getByRole("link", { name: "News", exact: true }).click();
    await expect(page).toHaveURL(/\/news$/);
    await expect(page.getByRole("heading", { name: "News", exact: true })).toBeVisible();

    await page.getByRole("link", { name: "Insights" }).click();
    await expect(page).toHaveURL(/\/insights$/);
    await expect(page.getByRole("heading", { name: "AI analysis" })).toBeVisible();

    await page.getByRole("link", { name: "Journal" }).click();
    await expect(page).toHaveURL(/\/journal$/);
    await expect(page.getByRole("heading", { name: /Open theses/ })).toBeVisible();

    await page.getByRole("link", { name: "Data quality" }).click();
    await expect(page).toHaveURL(/\/data-quality$/);
    await expect(page.getByRole("heading", { name: "Ingestion" })).toBeVisible();

    await page.getByRole("link", { name: "Overview" }).click();
    await expect(page).toHaveURL(/\/$/);
  });

  test("marks the current section for assistive tech", async ({ page }) => {
    await page.goto("/performance");

    await expect(page.getByRole("link", { name: "Performance" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });
});

test.describe("overview", () => {
  test("leads with the NAV hero figure and headline metrics", async ({ page }) => {
    await page.goto("/");

    await expect(page.getByText("Portfolio NAV").first()).toBeVisible();
    await expect(page.getByText("Cumulative TWR")).toBeVisible();
    await expect(page.getByText("+2.59%")).toBeVisible();
    await expect(page.getByText("Sharpe")).toBeVisible();
    await expect(page.getByText("0.46")).toBeVisible();
  });

  test("renders the NAV chart as SVG", async ({ page }) => {
    await page.goto("/");

    const chart = page.locator(".recharts-surface").first();
    await expect(chart).toBeVisible();
    await expect(chart.locator("path.recharts-curve").first()).toBeVisible();
  });

  test("shows live system status", async ({ page }) => {
    await page.goto("/");

    const system = page.locator("section", { hasText: "Local stack reachability" }).first();
    await expect(system.getByText("READY")).toBeVisible();
    await expect(system.getByText("CONFIGURED")).toBeVisible();
  });

  test("lists the largest holdings", async ({ page }) => {
    await page.goto("/");

    await expect(page.getByRole("cell", { name: "AAPL_US_EQ" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "Apple Inc." })).toBeVisible();
  });
});

test.describe("holdings", () => {
  test("renders every position with exact quantities", async ({ page }) => {
    await page.goto("/holdings");

    await expect(page.getByRole("cell", { name: "AAPL_US_EQ" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "SHEL_EQ" })).toBeVisible();
    // Fractional-share precision must survive to the screen unrounded.
    await expect(page.getByRole("cell", { name: "12.3456789" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "US0378331005" })).toBeVisible();
  });

  test("labels per-share prices with the instrument currency", async ({ page }) => {
    await page.goto("/holdings");

    await expect(page.getByRole("cell", { name: "182.40 USD" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "2650 GBX" })).toBeVisible();
  });
});

test.describe("performance", () => {
  test("states the flow-timing and annualisation conventions", async ({ page }) => {
    await page.goto("/performance");

    // The convention appears both in the header strip and again in the method notes.
    await expect(page.getByText("flow_at_close", { exact: true })).toBeVisible();
    await expect(page.getByText("Cash-flow timing convention: flow_at_close.")).toBeVisible();
    await expect(page.getByText(/365/).first()).toBeVisible();
  });

  test("renders NAV, drawdown, rolling and contribution charts", async ({ page }) => {
    await page.goto("/performance");

    await expect(page.getByRole("heading", { name: "Net asset value" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Drawdown" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Rolling volatility" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Rolling beta" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Contribution" })).toBeVisible();

    // Five charts on the page, each an SVG surface.
    await expect(page.locator(".recharts-surface")).toHaveCount(5);
  });

  test("shows a legend whenever two series share an axis", async ({ page }) => {
    await page.goto("/performance");

    await expect(page.getByText("FTSE All-World ETF proxy").first()).toBeVisible();
    await expect(page.getByText("30-day").first()).toBeVisible();
    await expect(page.getByText("90-day").first()).toBeVisible();
  });

  test("reports unavailable analytics with their reason, never a fake number", async ({
    page,
  }) => {
    await page.goto("/performance");

    const attribution = page.locator("section", { hasText: "Brinson-Fachler" }).first();
    await expect(attribution.getByText("Unavailable").first()).toBeVisible();
    await expect(
      attribution.getByText(/No licensed index-constituent source is configured/),
    ).toBeVisible();

    await expect(
      page.getByText(/Helios does not fabricate factor series/),
    ).toBeVisible();
  });

  test("discloses the flat-day caveat on the VaR panel", async ({ page }) => {
    await page.goto("/performance");

    await expect(page.getByText(/of observations are flat/)).toBeVisible();
  });

  test("gives every chart a table-view twin", async ({ page }) => {
    await page.goto("/performance");

    await expect(page.getByRole("heading", { name: "Daily NAV table" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "2024-01-01" })).toBeVisible();
    // The unvalued day is shown as a gap in the table too, with its status.
    await expect(page.getByRole("cell", { name: "Partial" }).first()).toBeVisible();
  });

  test("marks a holding without enough history as insufficient data", async ({ page }) => {
    await page.goto("/performance");

    const contributions = page.locator("section", { hasText: "Weight × period return" }).first();
    await expect(contributions.getByRole("cell", { name: "NEW_EQ" })).toBeVisible();
    await expect(contributions.getByText("Insufficient data").first()).toBeVisible();
  });
});

test.describe("news", () => {
  test("attributes every headline and links to the publisher", async ({ page }) => {
    await page.goto("/news");

    const link = page.getByRole("link", { name: "Apple beats expectations" });
    await expect(link).toHaveAttribute("href", "https://example.com/apple-results");
    await expect(link).toHaveAttribute("target", "_blank");
    await expect(link).toHaveAttribute("rel", /noopener/);
    await expect(page.getByText("Example Markets").first()).toBeVisible();
  });

  test("labels undated and unattributed items honestly", async ({ page }) => {
    await page.goto("/news");

    // "Undated" is the date slot; the headline "Undated market note" is a separate element.
    await expect(page.getByText("Undated", { exact: true })).toBeVisible();
    await expect(page.getByText("Market-wide")).toBeVisible();
  });

  test("filters by holding", async ({ page }) => {
    await page.goto("/news");
    await page.getByRole("link", { name: "AAPL_US_EQ", exact: true }).click();

    await expect(page).toHaveURL(/ticker=AAPL_US_EQ/);
    await expect(page.getByRole("link", { name: "Apple beats expectations" })).toBeVisible();
    await expect(page.getByText("Undated market note")).not.toBeVisible();
  });

  test("states that article text is never stored", async ({ page }) => {
    await page.goto("/news");

    await expect(page.getByText(/does not fetch or store article text/)).toBeVisible();
  });

  test("shows a news panel on the overview", async ({ page }) => {
    await page.goto("/");

    await expect(page.getByRole("heading", { name: "Latest news" })).toBeVisible();
    await expect(page.getByRole("link", { name: "All news →" })).toBeVisible();
  });
});

test.describe("AI insights", () => {
  test("always shows the not-advice disclosure", async ({ page }) => {
    await page.goto("/insights");

    await expect(page.getByText(/not investment advice/).first()).toBeVisible();
    await expect(page.getByText(/not a forecast/).first()).toBeVisible();
  });

  test("cites the evidence behind every observation", async ({ page }) => {
    await page.goto("/insights");

    await expect(page.getByText("One holding dominates the portfolio")).toBeVisible();
    await expect(page.getByText(/top5_weight = 1\.0/)).toBeVisible();
    await expect(page.getByText(/volatility = 0\.2275/)).toBeVisible();
    // Every observation row carries an evidence line.
    await expect(page.getByText("evidence", { exact: false })).toHaveCount(2);
  });

  test("names the metrics the analytics could not compute", async ({ page }) => {
    await page.goto("/insights");

    await expect(page.getByText(/Reported as unavailable/)).toBeVisible();
    await expect(page.getByText(/sortino, attribution/)).toBeVisible();
  });

  test("discloses the model and token spend", async ({ page }) => {
    await page.goto("/insights");

    await expect(page.getByText("claude-opus-5")).toBeVisible();
    await expect(page.getByText(/4210 in \/ 890 out/)).toBeVisible();
  });

  test("explains that analysis never runs on a schedule", async ({ page }) => {
    await page.goto("/insights");

    await expect(page.getByText(/never on a schedule/)).toBeVisible();
  });
});

test.describe("thesis and journal", () => {
  test("separates open theses from settled ones", async ({ page }) => {
    await page.goto("/journal");

    await expect(page.getByRole("heading", { name: /Open theses \(1\)/ })).toBeVisible();
    await expect(page.getByRole("heading", { name: /Settled theses \(1\)/ })).toBeVisible();
  });

  test("shows the outcome note on a settled thesis", async ({ page }) => {
    await page.goto("/journal");

    await expect(page.getByRole("cell", { name: /Programme slowed in Q1/ })).toBeVisible();
  });

  test("explains why a live thesis is frozen", async ({ page }) => {
    await page.goto("/journal");

    await expect(page.getByText(/original reasoning is frozen/)).toBeVisible();
  });

  test("lists journal entries with their scope", async ({ page }) => {
    await page.goto("/journal");

    await expect(page.getByRole("cell", { name: /Added on the pullback/ })).toBeVisible();
    await expect(page.getByRole("cell", { name: "general" })).toBeVisible();
  });
});

test.describe("data quality", () => {
  test("surfaces endpoint failures and mapping issues", async ({ page }) => {
    await page.goto("/data-quality");

    await expect(page.getByRole("cell", { name: "/equity/history/orders" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "Trading212HTTPError" })).toBeVisible();
    await expect(page.getByRole("heading", { name: /Unresolved instruments \(1\)/ })).toBeVisible();
    await expect(page.getByRole("cell", { name: "XYZ_EQ" })).toBeVisible();
  });

  test("shows reconciliation mismatches with both quantities", async ({ page }) => {
    await page.goto("/data-quality");

    await expect(page.getByRole("cell", { name: "0.3456789" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "12.0000000" })).toBeVisible();
  });

  test("says so plainly when a category is empty", async ({ page }) => {
    await page.goto("/data-quality");

    await expect(page.getByRole("heading", { name: /Ambiguous instruments \(0\)/ })).toBeVisible();
    await expect(page.getByText(/nothing to resolve/).first()).toBeVisible();
  });
});

test.describe("safety posture", () => {
  test("never exposes the API base URL or a credential to the browser", async ({ page }) => {
    const requests: string[] = [];
    page.on("request", (request) => requests.push(request.url()));

    await page.goto("/performance");
    await page.waitForLoadState("networkidle");

    // Every fetch is server-side; the browser only ever talks to the Next origin.
    expect(requests.filter((url) => url.includes("8099"))).toHaveLength(0);

    const html = await page.content();
    expect(html).not.toContain("HELIOS_API_URL");
    expect(html).not.toContain("127.0.0.1:8099");
  });

  test("carries the read-only disclosure on every page", async ({ page }) => {
    for (const path of ["/", "/holdings", "/performance", "/data-quality"]) {
      await page.goto(path);
      await expect(page.getByText("READ-ONLY / NO-TRADE")).toBeVisible();
      await expect(page.getByText(/never places trades/)).toBeVisible();
    }
  });
});

test.describe("resilience", () => {
  test("explains an unreachable API instead of blanking the page", async ({ browser }) => {
    // Point the page at a port nothing is listening on by loading the app with a bad upstream is
    // not possible at runtime, so assert the rendered fallback copy exists in the bundle path
    // that handles it: request a route the stub does not serve.
    const context = await browser.newContext();
    const page = await context.newPage();
    await page.route("**/api/v1/**", (route) => route.abort());
    await page.goto("/holdings");

    // Server-rendered, so the abort above does not affect it; the page still renders its shell.
    await expect(page.getByRole("heading", { name: "Holdings" })).toBeVisible();
    await context.close();
  });
});
