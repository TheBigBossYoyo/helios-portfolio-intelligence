// Captures the README screenshots. The dashboard is pointed at the stub API (fixtures only,
// no database, no credentials) on private ports, so nothing real can appear.
// Usage: STUB=8199 WEB=3199 DIST=.next-demo node tests/e2e/demo-screenshots.mjs
import { chromium } from "@playwright/test";

const WEB = process.env.WEB || "3199";
const shots = [
  { path: "/", name: "overview" },
  { path: "/holdings", name: "holdings" },
  { path: "/performance", name: "performance" },
];
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await page.addInitScript(() => window.localStorage.setItem("helios-theme", "dark"));
for (const { path, name } of shots) {
  await page.goto(`http://127.0.0.1:${WEB}${path}`);
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(1000);
  await page.screenshot({ path: `../docs/screenshots/${name}.png`, fullPage: false });
}
await browser.close();
