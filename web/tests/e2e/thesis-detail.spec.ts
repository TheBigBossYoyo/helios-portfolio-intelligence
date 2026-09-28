import { expect, test } from "@playwright/test";
import { STUB_API_URL } from "./ports";

/**
 * `/journal/[id]` — a single thesis read in full: frozen reasoning, its place in the
 * draft → active → validated|invalidated → closed lifecycle, and every journal entry attached
 * to it. Fixtures: thesis #1 is active (AAPL_US_EQ), #2 is invalidated/settled (SHEL_EQ), and #3
 * is a draft that exists only behind the detail route (see `stub-api.mjs`) so a still-editable
 * thesis and a "no enrichment yet" one are both exercisable without perturbing the list counts
 * `dashboard.spec.ts` already asserts on.
 */

interface Recorded {
  method: string;
  path: string;
  action: string | null;
  body: string | null;
}

async function recorded(request: { get: (url: string) => Promise<{ json: () => Promise<Recorded[]> }> }) {
  const response = await request.get(`${STUB_API_URL}/__mutations`);
  return response.json();
}

function lastFor(rows: Recorded[], method: string, pathPattern: RegExp): Recorded | undefined {
  return [...rows].reverse().find((row) => row.method === method && pathPattern.test(row.path));
}

test.describe("thesis detail", () => {
  test("navigates from the journal list to a thesis's detail page", async ({ page }) => {
    await page.goto("/journal");

    await page
      .getByRole("link", { name: "Services revenue compounds faster than hardware" })
      .click();

    await expect(page).toHaveURL(/\/journal\/1$/);
    await expect(
      page.getByRole("heading", { name: "Services revenue compounds faster than hardware" }),
    ).toBeVisible();
  });

  test("shows the frozen reasoning and the journal entries attached to the thesis", async ({
    page,
  }) => {
    await page.goto("/journal/1");

    await expect(page.getByText(/frozen exactly as it was written/)).toBeVisible();
    await expect(
      page.getByText("Recurring services margin should keep expanding."),
    ).toBeVisible();
    await expect(page.getByText("Added on the pullback; thesis unchanged.")).toBeVisible();
    await expect(page.getByText("Trimmed a bit into strength.")).toBeVisible();
  });

  test("shows the outcome note and closed date for a settled thesis", async ({ page }) => {
    await page.goto("/journal/2");

    await expect(page.getByRole("heading", { name: "Outcome" })).toBeVisible();
    await expect(page.getByText("Programme slowed in Q1; the premise did not hold.")).toBeVisible();
  });

  // Both cases call `notFound()`, which renders `app/not-found.tsx` exactly like an unmatched
  // route does (see `not-found.spec.ts`). They do not additionally assert `response.status()`:
  // this app has a root `app/loading.tsx`, so every route streams behind that Suspense boundary,
  // and Next flushes its 200 shell before an inner `notFound()` resolves — a long-standing
  // App Router limitation (the status can only be corrected before anything has streamed), not
  // something this page can opt out of without removing the loading UI for the whole app. A
  // plain unmatched URL is unaffected because the router decides "no route matches" before any
  // page-level render begins, which is what keeps `not-found.spec.ts` green.
  test("an unknown id renders the not-found page", async ({ page }) => {
    await page.goto("/journal/999");

    await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();
  });

  test("a non-numeric id also renders the not-found page", async ({ page }) => {
    await page.goto("/journal/not-a-number");

    await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();
  });

  test("a draft thesis offers its edit control; an active one does not", async ({ page }) => {
    await page.goto("/journal/3");

    await expect(page.getByRole("heading", { name: "Edit this draft" })).toBeVisible();
    await expect(page.locator("#edit-thesis-title")).toBeVisible();
    // No enrichment is wired to this thesis (it holds no ticker), and it says so plainly.
    await expect(page.getByText(/None yet — no live weight/)).toBeVisible();

    await page.goto("/journal/1");

    await expect(page.getByRole("heading", { name: "Edit this draft" })).toHaveCount(0);
    await expect(page.locator("#edit-thesis-title")).toHaveCount(0);
  });

  test("adding a note from the detail page attaches it to that thesis", async ({
    page,
    request,
  }) => {
    await page.goto("/journal/1");

    await page.locator("#journal-note").fill("Checked in from the thesis page itself.");
    await page.getByRole("button", { name: "Add note" }).click();
    await expect(page.getByText("Journal entry added.")).toBeVisible();

    const call = lastFor(await recorded(request), "POST", /\/api\/v1\/journal$/);
    expect(call?.action).toBe("journal-write");
    const payload = JSON.parse(call?.body ?? "{}");
    expect(payload.note).toBe("Checked in from the thesis page itself.");
    expect(payload.thesisId).toBe(1);
  });
});
