import { expect, test } from "@playwright/test";

/**
 * Captures a full-page image of each dashboard section, in both themes, into
 * .sisyphus/evidence/ui/.
 *
 * Run with `npx playwright test screenshots`. These are review artefacts, not assertions — the
 * behavioural checks live in dashboard.spec.ts. Looking at them is the last step of every
 * visual change: the validators check colour, not layout.
 */
const PAGES = [
  { path: "/", name: "overview" },
  { path: "/holdings", name: "holdings" },
  { path: "/performance", name: "performance" },
  { path: "/card", name: "card" },
  { path: "/card?view=day", name: "card-day" },
  { path: "/card/merchant/Grocer", name: "merchant" },
  { path: "/holdings/AAPL_US_EQ", name: "holding-detail" },
  { path: "/plan", name: "plan" },
  { path: "/watchlist?q=apple", name: "watchlist" },
  { path: "/news", name: "news" },
  { path: "/insights", name: "insights" },
  { path: "/journal", name: "journal" },
  { path: "/journal/1", name: "thesis" },
  { path: "/data-quality", name: "data-quality" },
  { path: "/settings", name: "settings" },
] as const;

const THEMES = ["light", "dark"] as const;

for (const theme of THEMES) {
  for (const { path, name } of PAGES) {
    test(`screenshot ${name} (${theme})`, async ({ page }) => {
      await page.addInitScript((value) => {
        window.localStorage.setItem("helios-theme", value);
      }, theme);
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.goto(path);
      await page.waitForLoadState("networkidle");
      // Let the chart animations settle before capturing.
      await page.waitForTimeout(800);
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      await page.screenshot({
        path: `../.sisyphus/evidence/ui/${name}-${theme}.png`,
        fullPage: true,
      });
    });
  }
}
