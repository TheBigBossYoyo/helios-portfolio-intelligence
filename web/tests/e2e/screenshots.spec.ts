import { expect, test } from "@playwright/test";

/**
 * Captures a full-page image of each dashboard section into .sisyphus/evidence/m4/.
 *
 * Run with `npx playwright test screenshots`. These are review artefacts, not assertions — the
 * behavioural checks live in dashboard.spec.ts.
 */
const PAGES = [
  { path: "/", name: "overview" },
  { path: "/holdings", name: "holdings" },
  { path: "/performance", name: "performance" },
  { path: "/news", name: "news" },
  { path: "/insights", name: "insights" },
  { path: "/journal", name: "journal" },
  { path: "/data-quality", name: "data-quality" },
] as const;

for (const { path, name } of PAGES) {
  test(`screenshot ${name}`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    // Let the chart animations settle before capturing.
    await page.waitForTimeout(800);
    await expect(page.locator("body")).toBeVisible();
    await page.screenshot({
      path: `../.sisyphus/evidence/m4/${name}.png`,
      fullPage: true,
    });
  });
}
