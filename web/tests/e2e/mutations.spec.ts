import { expect, test } from "@playwright/test";
import { STUB_API_URL, STUB_API_PORT } from "./ports";

/**
 * The dashboard can now run every pipeline itself, so these prove the whole path: a click in the
 * browser reaches the API as a server-side call carrying `X-Helios-Local-Action`.
 *
 * The stub API enforces that header exactly as FastAPI does and records what it received, so a
 * regression that dropped the header would surface as a 403 on screen rather than passing
 * quietly. Assertions read the recording back from `/__mutations`.
 *
 * These run serially: they share one stub process, and the recording is global to it.
 */
test.describe.configure({ mode: "serial" });

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

test.describe("pipeline controls", () => {
  test("the overview strip runs a sync with the local-action header", async ({ page, request }) => {
    await page.goto("/");

    // A sync rewrites the ledger, so the first click only arms the control.
    const sync = page.getByRole("button", { name: "Sync", exact: true });
    await expect(sync).toBeVisible();
    await sync.click();

    const confirm = page.getByRole("button", { name: "Confirm sync" });
    await expect(confirm).toBeVisible();
    await confirm.click();

    await expect(page.getByText("Portfolio synced from Trading 212.")).toBeVisible();

    const call = lastFor(await recorded(request), "POST", /\/api\/v1\/portfolio\/sync$/);
    expect(call?.action).toBe("sync");
  });

  test("refreshing card history confirms first, because it notifies the phone", async ({
    page,
    request,
  }) => {
    await page.goto("/card");

    await page.getByRole("button", { name: "Refresh", exact: true }).click();
    await page.getByRole("button", { name: "Confirm — notifies your phone" }).click();

    await expect(page.getByText("Stored 5 cash rows from the export.")).toBeVisible();
    const call = lastFor(await recorded(request), "POST", /\/api\/v1\/card\/refresh$/);
    expect(call?.action).toBe("card-refresh");
  });

  test("a replay confirms first, then reports what it wrote", async ({ page, request }) => {
    await page.goto("/performance");

    const replay = page.getByRole("button", { name: "Replay", exact: true });
    await replay.click();
    await page.getByRole("button", { name: "Confirm replay" }).click();

    await expect(page.getByText("Daily NAV replayed.")).toBeVisible();

    const call = lastFor(await recorded(request), "POST", /\/api\/v1\/performance\/replay$/);
    expect(call?.action).toBe("replay");
  });

  test("a news sync runs without a confirmation step", async ({ page, request }) => {
    await page.goto("/news");

    // Syncing news costs nothing and changes no history, so it runs on the first click.
    await page.getByRole("button", { name: "Sync news" }).click();
    await expect(page.getByText("News feeds synced.")).toBeVisible();

    const call = lastFor(await recorded(request), "POST", /\/api\/v1\/news\/sync$/);
    expect(call?.action).toBe("news-sync");
  });

  test("the AI run names its cost in the confirmation", async ({ page, request }) => {
    await page.goto("/insights");

    await page.getByRole("button", { name: "Run analysis", exact: true }).click();
    // The confirmation says why it is asking, rather than a generic "are you sure".
    await expect(page.getByRole("button", { name: /this costs money/ })).toBeVisible();
    await page.getByRole("button", { name: /this costs money/ }).click();

    await expect(page.getByText("Analysis complete.")).toBeVisible();

    const call = lastFor(await recorded(request), "POST", /\/api\/v1\/ai\/analyse$/);
    expect(call?.action).toBe("ai-analyse");
  });
});

test.describe("thesis and journal writing", () => {
  test("creates a thesis from the page that displays them", async ({ page, request }) => {
    await page.goto("/journal");

    await page.locator("#thesis-title").fill("Margins hold through the cycle");
    await page.locator("#thesis-body").fill("Pricing power has survived two downturns.");
    await page.locator("#thesis-ticker").fill("AAPL_US_EQ");

    await page.getByRole("button", { name: "Create draft" }).click();
    await expect(page.getByText(/created as a draft/)).toBeVisible();

    const call = lastFor(await recorded(request), "POST", /\/api\/v1\/theses$/);
    expect(call?.action).toBe("thesis-write");
    // The payload uses the camelCase aliases the FastAPI request models declare.
    const payload = JSON.parse(call?.body ?? "{}");
    expect(payload.title).toBe("Margins hold through the cycle");
    expect(payload.t212Ticker).toBe("AAPL_US_EQ");
    expect(payload.conviction).toBe("medium");
  });

  test("adds a journal note and attaches it to a thesis", async ({ page, request }) => {
    await page.goto("/journal");

    await page.locator("#journal-note").fill("Trimmed slightly; reasoning unchanged.");
    await page.locator("#journal-thesis-id").selectOption("1");
    await page.locator("#journal-tags").fill("position");

    await page.getByRole("button", { name: "Add note" }).click();
    await expect(page.getByText("Journal entry added.")).toBeVisible();

    const call = lastFor(await recorded(request), "POST", /\/api\/v1\/journal$/);
    expect(call?.action).toBe("journal-write");
    const payload = JSON.parse(call?.body ?? "{}");
    expect(payload.note).toBe("Trimmed slightly; reasoning unchanged.");
    expect(payload.thesisId).toBe(1);
    expect(payload.tags).toBe("position");
  });

  test("settling a thesis requires the outcome note", async ({ page }) => {
    await page.goto("/journal");

    await page.locator("#transition-thesis-id").selectOption("1");
    await page.locator("#transition-to-status").selectOption("validated");

    // The note is what a settled thesis is for, so the form makes it mandatory rather than
    // letting the backend reject the request after the fact.
    const note = page.locator("#transition-outcome-note");
    await expect(note).toHaveAttribute("required", "");
  });
});

test.describe("settings", () => {
  test("saves a credential without ever showing one", async ({ page, request }) => {
    await page.goto("/settings");

    // What the page may know about an existing key: that it exists, and its last four
    // characters. The stub returns exactly that, because the real API returns exactly that.
    await expect(page.getByText(/set …7f2a/).first()).toBeVisible();

    const secret = "sk-ant-must-not-appear-anywhere";
    await page.locator("#credential-anthropic_api_key").fill(secret);
    await page
      .locator("#credential-anthropic_api_key")
      .locator("xpath=ancestor::form")
      .getByRole("button", { name: "Save" })
      .click();

    await expect(page.getByText("Credential saved.").first()).toBeVisible();

    const call = lastFor(await recorded(request), "PUT", /\/settings\/credential$/);
    expect(call?.action).toBe("settings-write");
    expect(JSON.parse(call?.body ?? "{}").field).toBe("anthropic_api_key");

    // The value travelled one way. Nothing echoed it back into the page.
    expect(await page.content()).not.toContain(secret);
  });

  test("the credential input is write-only and clears after a save", async ({ page }) => {
    await page.goto("/settings");

    const input = page.locator("#credential-market_data_api_key");
    // A password field with no value: there is nothing to prefill, because the server never
    // sends a stored credential back.
    await expect(input).toHaveAttribute("type", "password");
    await expect(input).toHaveValue("");

    await input.fill("td-key-123456");
    await input
      .locator("xpath=ancestor::form")
      .getByRole("button", { name: "Save" })
      .click();
    await expect(page.getByText("Credential saved.").first()).toBeVisible();

    // Leaving a rejected or accepted key sitting in the DOM is a copy nobody asked for.
    await expect(input).toHaveValue("");
  });

  test("the Trading 212 environment is a fixed choice, not a text box", async ({ page }) => {
    await page.goto("/settings");

    // Two labelled cards, one per environment the API allows: nothing free-text, and no raw
    // host names (a dropdown of URLs read to a first-time user as "only demo exists").
    const group = page.getByRole("radiogroup", { name: "Environment" });
    await expect(group.getByRole("radio")).toHaveCount(2);
    await expect(group.getByRole("radio").first()).toHaveAttribute("value", "demo");
    await expect(group.getByRole("radio").last()).toHaveAttribute("value", "live");
    await expect(page.locator("#setting-t212_base_url")).toHaveCount(0);

    // "In use" is what the running API syncs from, not whichever card was last clicked: a
    // selection-driven badge once said Live while every check still ran against demo.
    const practiceCard = group.locator("label", { hasText: "Practice (demo)" });
    const liveCard = group.locator("label", { hasText: "Live (real money)" });
    await expect(practiceCard.getByText("In use")).toBeVisible();
    await liveCard.click();
    await expect(group.getByRole("radio").last()).toBeChecked();
    await expect(liveCard.getByText("In use")).toHaveCount(0);
    await expect(practiceCard.getByText("In use")).toBeVisible();
  });

  test("connecting sends environment, key and secret together", async ({ page, request }) => {
    await page.goto("/settings");

    const group = page.getByRole("radiogroup", { name: "Environment" });
    await group.locator("label", { hasText: "Live (real money)" }).click();
    await page.locator("#t212-api-key").fill("live-key-0001");
    await page.locator("#t212-api-secret").fill("live-secret-0001");
    await page.getByRole("button", { name: "Verify & connect" }).click();

    // The API's own sentence, naming the account it verified against.
    await expect(page.getByText(/Verified against your Live account/)).toBeVisible();

    const call = lastFor(await recorded(request), "PUT", /\/settings\/trading212$/);
    expect(call?.action).toBe("settings-write");
    expect(JSON.parse(call?.body ?? "{}")).toEqual({
      environment: "live",
      apiKey: "live-key-0001",
      apiSecret: "live-secret-0001",
    });
    // Written, never shown back: both inputs clear, and the secret is nowhere in the page.
    await expect(page.locator("#t212-api-key")).toHaveValue("");
    expect(await page.content()).not.toContain("live-secret-0001");
  });

  test("a half-filled connection is refused before it reaches the API", async ({
    page,
    request,
  }) => {
    await page.goto("/settings");
    const before = (await recorded(request)).length;

    await page.locator("#t212-api-key").fill("only-the-key");
    await page.getByRole("button", { name: "Verify & connect" }).click();

    await expect(page.getByText(/Paste both the API key and the API secret/)).toBeVisible();
    const after = await recorded(request);
    expect(after.slice(before).filter((row) => /trading212/.test(row.path))).toHaveLength(0);
  });

  test("saving a setting reaches the API as settings-write", async ({ page, request }) => {
    await page.goto("/settings");

    await page.locator("#setting-news_sec_user_agent").fill("Test Person test@example.com");
    await page
      .locator("#setting-news_sec_user_agent")
      .locator("xpath=ancestor::form")
      .getByRole("button", { name: "Save" })
      .click();
    await expect(page.getByText("Setting saved.").first()).toBeVisible();

    const call = lastFor(await recorded(request), "PUT", /\/settings\/value$/);
    expect(call?.action).toBe("settings-write");
    const payload = JSON.parse(call?.body ?? "{}");
    expect(payload.field).toBe("news_sec_user_agent");
    expect(payload.value).toBe("Test Person test@example.com");
  });

  test("says where a credential is stored, and what that does not protect", async ({ page }) => {
    await page.goto("/settings");

    // The honesty the whole surface rests on: this is a local-caller gate, not authentication,
    // and the page says so rather than letting the reader assume otherwise. Asserted as one
    // phrase rather than three loose words, which would match almost any paragraph on the page.
    await expect(page.getByText(/single-user loopback install/).first()).toBeVisible();
    await expect(page.getByText(/needs real auth and CSRF/).first()).toBeVisible();

    // And where a saved value actually goes, named rather than implied.
    await expect(page.getByText(/Credentials go to your OS keyring/).first()).toBeVisible();
  });

  test("creates a database and refuses to switch without confirming", async ({ page, request }) => {
    await page.goto("/settings");

    // A name the stub does not already list: helios.sqlite3 and helios-live.sqlite3 both exist
    // there, and the real API refuses to overwrite, so reusing one would test the wrong path.
    await page.locator("#database-filename").fill("helios-2026.sqlite3");
    await page.getByRole("button", { name: "Create", exact: true }).click();
    await expect(page.getByText(/created/).first()).toBeVisible();

    const created = lastFor(await recorded(request), "POST", /\/settings\/databases$/);
    expect(created?.action).toBe("settings-write");

    // Switching changes every figure on every page, so the first click only arms it.
    await page.getByRole("button", { name: "Switch", exact: true }).click();
    await expect(page.getByRole("button", { name: /every page changes/ })).toBeVisible();

    await page.getByRole("button", { name: "Cancel" }).first().click();
    const after = await recorded(request);
    expect(lastFor(after, "PUT", /\/databases\/active$/)).toBeUndefined();
  });

  test("a restart confirms first and reports acceptance, not completion", async ({
    page,
    request,
  }) => {
    await page.goto("/settings");

    await page.getByRole("button", { name: "Restart API" }).first().click();
    await expect(page.getByRole("button", { name: /drops in-flight requests/ })).toBeVisible();
    await page.getByRole("button", { name: /drops in-flight requests/ }).click();

    // The backend answers before it re-execs, so "reload in a few seconds" is the honest tense.
    await expect(page.getByText(/Reload/).first()).toBeVisible();

    const call = lastFor(await recorded(request), "POST", /\/settings\/restart$/);
    expect(call?.action).toBe("restart");
  });

  test("under Compose every control is disabled and the page says where to edit instead", async ({
    page,
    request,
  }) => {
    await request.get(`${STUB_API_URL}/__settings-mode?writable=false`);
    try {
      await page.goto("/settings");

      const banner = page.getByTestId("settings-read-only");
      await expect(banner).toBeVisible();
      await expect(banner).toContainText("docker compose up -d");

      // Disabled, not hidden: the operator should still see what is configured.
      await expect(page.locator("#database-filename")).toBeDisabled();
      await expect(page.getByRole("button", { name: "Create", exact: true })).toBeDisabled();
      // Restarting cannot apply anything the page could have saved, so it is not offered.
      await expect(page.getByRole("button", { name: "Restart API" })).toHaveCount(0);
      // The keyring note would point at a .env inside the container, which is the wrong file.
      await expect(page.getByText(/Credentials go to your OS keyring/)).toHaveCount(0);
    } finally {
      await request.get(`${STUB_API_URL}/__settings-mode?writable=true`);
    }
  });
});

test.describe("mutation safety", () => {
  test("the browser still never contacts the API directly", async ({ page }) => {
    const requests: string[] = [];
    page.on("request", (request) => requests.push(request.url()));

    await page.goto("/news");
    await page.getByRole("button", { name: "Sync news" }).click();
    await expect(page.getByText("News feeds synced.")).toBeVisible();

    // The mutation went through the Next server, exactly as the reads do.
    expect(requests.filter((url) => url.includes(String(STUB_API_PORT)))).toHaveLength(0);
  });
});
