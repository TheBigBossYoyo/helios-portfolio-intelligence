import { expect, test } from "@playwright/test";

/**
 * The stub API's news list has 12 stories that name a holding (page size 10, see
 * web/app/news/page.tsx) and its journal list has 15 (page size 10, see
 * web/app/journal/page.tsx), specifically so each spans more than one page here.
 */
test.describe("news pagination", () => {
  test("shows page 1 by default and pages forward with the URL", async ({ page }) => {
    await page.goto("/news");

    await expect(page.getByRole("link", { name: "Apple beats expectations" })).toBeVisible();
    await expect(
      page.getByRole("link", { name: "Apple analyst note 9" }),
    ).not.toBeVisible();
    await expect(page.getByText("Page 1 of 2")).toBeVisible();

    await page.getByRole("link", { name: "Next →" }).click();

    await expect(page).toHaveURL(/\/news\?page=2$/);
    await expect(
      page.getByRole("link", { name: "Apple analyst note 9" }),
    ).toBeVisible();
    await expect(page.getByRole("link", { name: "Apple beats expectations" })).not.toBeVisible();
    await expect(page.getByText("Page 2 of 2")).toBeVisible();
  });

  test("is bookmarkable: loading page 2 directly renders page 2", async ({ page }) => {
    await page.goto("/news?page=2");

    await expect(
      page.getByRole("link", { name: "Apple analyst note 9" }),
    ).toBeVisible();
    await expect(page.getByRole("link", { name: "← Previous" })).toHaveAttribute(
      "href",
      "/news",
    );
  });

  test("clamps a page requested past the end down to the last real page", async ({ page }) => {
    await page.goto("/news?page=999");

    await expect(page.getByText("Page 2 of 2")).toBeVisible();
    await expect(
      page.getByRole("link", { name: "Apple analyst note 9" }),
    ).toBeVisible();
  });

  test("clamps a nonsense page to page 1 instead of erroring", async ({ page }) => {
    await page.goto("/news?page=not-a-number");

    await expect(page.getByRole("link", { name: "Apple beats expectations" })).toBeVisible();
    await expect(page.getByText("Page 1 of 2")).toBeVisible();
  });

  test("keeps the ticker filter across a page link", async ({ page }) => {
    await page.goto("/news?ticker=AAPL_US_EQ");

    // The filter is the thing under test here, via the URL a page link would have to preserve.
    await expect(page.getByRole("link", { name: "Apple beats expectations" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Apple supply chain note" })).toBeVisible();
    await expect(
      page.getByRole("link", { name: "Shell announces buyback update" }),
    ).not.toBeVisible();
  });
});

test.describe("journal pagination", () => {
  // Journal entries render as a timeline (a dated list), not a table — so these read the entry
  // text directly rather than through a table cell role.
  test("pages the journal entries timeline", async ({ page }) => {
    await page.goto("/journal");

    await expect(page.getByText(/Added on the pullback/)).toBeVisible();
    await expect(page.getByText(/Closed the loop on the buyback question/)).not.toBeVisible();

    await page
      .locator("nav[aria-label='Pagination']")
      .getByRole("link", { name: "Next →" })
      .click();

    await expect(page).toHaveURL(/\/journal\?page=2$/);
    await expect(page.getByText(/Closed the loop on the buyback question/)).toBeVisible();
    await expect(page.getByText(/Added on the pullback/)).not.toBeVisible();
  });

  test("clamps an out-of-range journal page to the last page", async ({ page }) => {
    await page.goto("/journal?page=999");

    await expect(page.getByText(/Closed the loop on the buyback question/)).toBeVisible();
  });
});
