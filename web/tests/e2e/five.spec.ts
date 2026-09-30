import { expect, test } from "@playwright/test";
import { STUB_API_URL } from "./ports";

interface Recorded {
  method: string;
  path: string;
  action: string | null;
  body: string | null;
}

async function recorded(request: { get: (url: string) => Promise<{ json: () => Promise<Recorded[]> }> }) {
  return (await request.get(`${STUB_API_URL}/__mutations`)).json();
}

function lastFor(rows: Recorded[], method: string, path: RegExp): Recorded | undefined {
  return [...rows].reverse().find((row) => row.method === method && path.test(row.path));
}

test.describe("what you own", () => {
  test("opens funds up into companies, countries and sectors, and warns on concentration", async ({ page, request }) => {
    await page.goto("/exposure");
    await expect(page.getByText("Apple Inc. is 61.3% of everything you hold")).toBeVisible();
    await expect(page.getByText("€2,065.30 held directly · €21.40 through VUAG")).toBeVisible();
    await expect(page.getByText("Through VUAG", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("United Kingdom")).toBeVisible();
    await expect(page.getByRole("cell", { name: "Vanguard S&P 500 ETF (VOO), same index" })).toBeVisible();

    await page.getByRole("button", { name: "Refresh" }).click();
    await expect(page.getByText("Updated from the SEC.")).toBeVisible();
    expect(lastFor(await recorded(request), "POST", /\/api\/v1\/exposure\/refresh$/)?.action).toBe("sec-refresh");
  });
});

test.describe("company facts", () => {
  test("the stock page shows what the company reports", async ({ page }) => {
    await page.goto("/holdings/AAPL_US_EQ");
    const panel = page.locator("section", { has: page.getByRole("heading", { name: "The company", exact: true }) });
    await expect(panel.getByText("Electronic Computers · Technology · US")).toBeVisible();
    await expect(panel.getByText("$2.82tn")).toBeVisible();
    await expect(panel.getByText("28.4")).toBeVisible();
    await expect(panel.getByText("−4.3%")).toBeVisible();
    await expect(panel.getByText("54% of the way up")).toBeVisible();
  });
});

test.describe("goals", () => {
  test("show progress on the Plan and Overview, and are added and removed by name", async ({ page, request }) => {
    await page.goto("/plan");
    const goals = page.getByTestId("goal");
    await expect(goals).toHaveCount(2);
    await expect(goals.first().getByText("On track")).toBeVisible();
    await expect(goals.nth(1).getByText("Behind")).toBeVisible();

    await page.getByLabel("Name").fill("House deposit");
    await page.getByLabel("Target (€)").fill("25000");
    await page.getByLabel("By").fill("2031-06-30");
    await page.getByRole("button", { name: "Add goal" }).click();
    await expect(page.getByText("Goal added.")).toBeVisible();
    const added = lastFor(await recorded(request), "POST", /\/api\/v1\/goals$/);
    expect(added?.action).toBe("goals-write");
    expect(JSON.parse(added?.body ?? "{}")).toEqual({
      kind: "value",
      name: "House deposit",
      targetAmount: "25000",
      targetDate: "2031-06-30",
    });

    await goals.first().getByRole("button", { name: "Remove" }).click();
    await goals.first().getByRole("button", { name: "Remove" }).click();
    await expect(page.getByText("Goal removed.")).toBeVisible();
    expect(lastFor(await recorded(request), "DELETE", /\/api\/v1\/goals\/1$/)?.action).toBe("goals-write");

    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Your goals" })).toBeVisible();
  });
});

test.describe("the phone app shell", () => {
  test("serves a service worker that is never cached and handles pushes", async ({ request }) => {
    const response = await request.get("/sw.js");
    expect(response.ok()).toBe(true);
    expect(response.headers()["cache-control"]).toContain("no-cache");
    const script = await response.text();
    expect(script).toContain('addEventListener("push"');
    expect(script).toContain('addEventListener("notificationclick"');
  });

  test.describe("from a paired phone", () => {
    test.use({ extraHTTPHeaders: { "x-helios-remote": "1" } });

    test("settings offer notifications for this phone", async ({ page }) => {
      await page.goto("/settings");
      await expect(page.getByTestId("push-toggle")).toBeVisible();
    });
  });
});
