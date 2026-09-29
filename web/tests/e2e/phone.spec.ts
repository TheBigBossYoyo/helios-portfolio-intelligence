import { expect, test } from "@playwright/test";

/** Helios on a phone: an app-style tab bar, nothing wider than the screen, installable. */
test.describe("phone", () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

  test("the tab bar and the More sheet reach every page", async ({ page }) => {
    await page.goto("/");
    const tabs = page.getByRole("navigation", { name: "Tabs" });
    await expect(tabs).toBeVisible();
    await expect(tabs.getByRole("link", { name: "Overview" })).toHaveAttribute("aria-current", "page");

    await tabs.getByRole("link", { name: "Holdings" }).click();
    await expect(page).toHaveURL(/\/holdings$/);
    await expect(tabs.getByRole("link", { name: "Holdings" })).toHaveAttribute("aria-current", "page");

    await tabs.getByRole("button", { name: "More" }).click();
    const sheet = page.getByRole("dialog", { name: "More pages" });
    await expect(sheet).toBeVisible();
    await sheet.getByRole("link", { name: "Performance" }).click();
    await expect(page).toHaveURL(/\/performance$/);
    await expect(sheet).toBeHidden();
    // On a page outside the four tabs, More is the highlighted tab.
    await expect(tabs.getByRole("button", { name: "More" })).toHaveClass(/text-accent-ink/);
  });

  for (const path of ["/", "/holdings", "/holdings/AAPL_US_EQ", "/performance", "/card", "/calendar", "/plan", "/targets", "/news", "/settings"]) {
    test(`${path} never scrolls sideways`, async ({ page }) => {
      await page.goto(path);
      await page.waitForLoadState("networkidle");
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow).toBeLessThanOrEqual(0);
    });
  }

  test("wide tables become one card per row", async ({ page }) => {
    await page.goto("/card");
    const cell = page.locator("table.stack-sm td[data-label='Merchant']").first();
    await expect(cell).toBeVisible();
    const label = await cell.evaluate((node) => getComputedStyle(node, "::before").content);
    expect(label).toBe('"Merchant"');
  });

  test("it installs to the home screen", async ({ page, request }) => {
    const manifest = await (await request.get("/manifest.webmanifest")).json();
    expect(manifest.name).toBe("Helios");
    expect(manifest.display).toBe("standalone");
    await page.goto("/");
    await expect(page.locator('link[rel="apple-touch-icon"]')).toHaveCount(1);
    await expect(page.locator('meta[name="apple-mobile-web-app-capable"], meta[name="mobile-web-app-capable"]')).not.toHaveCount(0);
  });
});

test.describe("a paired phone through the gateway", () => {
  test.use({ extraHTTPHeaders: { "x-helios-remote": "1" } });

  test("sees its own settings, never keys or pairing", async ({ page }) => {
    await page.goto("/settings");
    await expect(page.getByTestId("settings-remote")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Phone", exact: true })).toHaveCount(0);
    await expect(page.getByTestId("pairing-code")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Pair a phone" })).toHaveCount(0);
  });
});
