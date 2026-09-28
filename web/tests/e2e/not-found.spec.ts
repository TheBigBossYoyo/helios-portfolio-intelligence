import { expect, test } from "@playwright/test";

test.describe("not found", () => {
  test("renders the not-found page for an unknown route, with a way back", async ({ page }) => {
    const response = await page.goto("/this-route-does-not-exist");

    expect(response?.status()).toBe(404);
    await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();

    const back = page.getByRole("link", { name: /Back to overview/ });
    await expect(back).toHaveAttribute("href", "/");
    await back.click();
    await expect(page).toHaveURL(/\/$/);
  });

  test("still renders the app shell around the not-found panel", async ({ page }) => {
    await page.goto("/this-route-does-not-exist");

    await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();
    await expect(page.getByText("Read-only · never places trades")).toBeVisible();
  });
});
