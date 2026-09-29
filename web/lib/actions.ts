"use server";

import { revalidatePath } from "next/cache";

import { apiBaseUrl } from "./api";

/**
 * Server actions for every mutating Helios operation.
 *
 * These run on the server, exactly like `lib/api.ts` reads do, so the browser still never
 * contacts the API and `HELIOS_API_URL` never reaches a client bundle. A form posts to Next,
 * Next posts to FastAPI, and the page re-renders from the fresh result.
 *
 * Every call carries `X-Helios-Local-Action`. The backend requires it on all mutating routes,
 * and it is what marks a request as Helios' own server-side caller rather than ambient
 * loopback traffic: a browser cannot set a custom header cross-origin without a CORS preflight
 * the API never grants. It is a local-caller gate, not authentication.
 *
 * Nothing here throws. A failure becomes an `ActionResult` the form renders inline, because a
 * button that silently does nothing is worse than one that says why it refused.
 */

/** Mutations are slower than reads: a sync walks every Trading 212 page, a replay refetches prices. */
const MUTATION_TIMEOUT_MS = 120_000;

/**
 * A first replay requests every symbol once, paced to the free price plans' per-minute
 * limits (about 8 a minute), so fifteen symbols take a couple of minutes. Later replays
 * only fetch missing days and finish quickly.
 */
const REPLAY_TIMEOUT_MS = 600_000;

export type ActionResult =
  | { ok: true; message: string; timestamp: string }
  | { ok: false; error: string; status: number | null; timestamp: string };

/** The action name each endpoint expects in `X-Helios-Local-Action`. */
type LocalAction =
  | "sync"
  | "replay"
  | "news-sync"
  | "card-refresh"
  | "card-budget"
  | "alerts-write"
  | "watchlist-write"
  | "ai-weekly"
  | "backup"
  | "storage-compact"
  | "ai-analyse"
  | "thesis-write"
  | "journal-write"
  | "settings-write"
  | "restart";

interface MutateOptions {
  method?: "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  /** Paths to re-render once the mutation lands. */
  revalidate?: string[];
  successMessage: string;
  /** Prefer the API's `detail` sentence as the success message when it has one. */
  messageFromResponse?: boolean;
  /**
   * Override the pipeline timeout. Settings writes are one short round trip at most, so waiting
   * two minutes on one would leave the form spinning long after the answer was decided.
   */
  timeoutMs?: number;
}

/**
 * Turn a backend failure into a sentence naming what actually went wrong.
 *
 * FastAPI puts the reason in `detail`; using it means a 409 reads "Portfolio sync already
 * running" rather than "HTTP 409", which is the difference between an actionable message and
 * a status code the reader has to go look up.
 */
async function describeFailure(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    if (body && typeof body === "object" && "detail" in body) {
      const detail = (body as { detail: unknown }).detail;
      if (typeof detail === "string" && detail.trim()) {
        return detail;
      }
    }
  } catch {
    // A non-JSON error body is not itself an error worth surfacing; fall through to the status.
  }
  return `HTTP ${response.status}`;
}

/** The API's own `detail` sentence on a success, when it says something specific. */
async function detailOf(response: Response): Promise<string | null> {
  try {
    const body: unknown = await response.json();
    if (body && typeof body === "object" && "detail" in body) {
      const detail = (body as { detail: unknown }).detail;
      if (typeof detail === "string" && detail.trim()) return detail;
    }
  } catch {
    // Fall back to the caller's generic message.
  }
  return null;
}

async function mutate(
  path: string,
  action: LocalAction,
  options: MutateOptions,
): Promise<ActionResult> {
  const timestamp = () => new Date().toISOString();
  try {
    const response = await fetch(`${apiBaseUrl()}${path}`, {
      method: options.method ?? "POST",
      cache: "no-store",
      signal: AbortSignal.timeout(options.timeoutMs ?? MUTATION_TIMEOUT_MS),
      headers: {
        "X-Helios-Local-Action": action,
        ...(options.body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
    if (!response.ok) {
      return {
        ok: false,
        error: await describeFailure(response),
        status: response.status,
        timestamp: timestamp(),
      };
    }
    // Every route is force-dynamic with no-store, so this only needs to re-run the render.
    for (const target of options.revalidate ?? []) {
      revalidatePath(target);
    }
    const message = options.messageFromResponse
      ? ((await detailOf(response)) ?? options.successMessage)
      : options.successMessage;
    return { ok: true, message, timestamp: timestamp() };
  } catch (error: unknown) {
    const message =
      error instanceof Error && error.name === "TimeoutError"
        ? "The request took too long. It may still be running on the server."
        : error instanceof Error
          ? error.message
          : "Network error";
    return { ok: false, error: message, status: null, timestamp: timestamp() };
  }
}

// --- form helpers ----------------------------------------------------------

/** Read a trimmed field, or `null` when it is absent or blank. */
function optionalField(form: FormData, name: string): string | null {
  const value = form.get(name);
  if (typeof value !== "string") {
    return null;
  }
  const trimmed = value.trim();
  return trimmed === "" ? null : trimmed;
}

function requiredField(form: FormData, name: string): string | null {
  return optionalField(form, name);
}

function missingFieldResult(label: string): ActionResult {
  return {
    ok: false,
    error: `${label} is required.`,
    status: null,
    timestamp: new Date().toISOString(),
  };
}

// --- pipeline actions ------------------------------------------------------

export async function syncPortfolioAction(): Promise<ActionResult> {
  return mutate("/api/v1/portfolio/sync", "sync", {
    successMessage: "Portfolio synced from Trading 212.",
    revalidate: ["/", "/holdings", "/data-quality", "/performance"],
  });
}

export async function replayPerformanceAction(): Promise<ActionResult> {
  return mutate("/api/v1/performance/replay", "replay", {
    timeoutMs: REPLAY_TIMEOUT_MS,
    successMessage: "Daily NAV replayed.",
    revalidate: ["/", "/performance"],
  });
}

export async function syncNewsAction(): Promise<ActionResult> {
  return mutate("/api/v1/news/sync", "news-sync", {
    successMessage: "News feeds synced.",
    revalidate: ["/", "/news"],
  });
}

/**
 * Collect a finished card export, or ask Trading 212 for a new one. Asking sends a
 * notification to the Trading 212 app, which is why the button confirms first.
 */
export async function refreshCardHistoryAction(): Promise<ActionResult> {
  return mutate("/api/v1/card/refresh", "card-refresh", {
    successMessage: "Card history refreshed.",
    messageFromResponse: true,
    revalidate: ["/", "/card", "/performance"],
  });
}

/** Set a category's monthly card budget; an empty amount removes it. */
export async function setCardBudgetAction(form: FormData): Promise<ActionResult> {
  const category = optionalField(form, "category");
  if (category === null) return missingFieldResult("Category");
  const raw = optionalField(form, "limit");
  const limit = raw === null ? null : Number(raw.replace(",", "."));
  if (limit !== null && (!Number.isFinite(limit) || limit < 0)) {
    return {
      ok: false,
      error: "Enter a budget of zero or more, or leave it empty to remove it.",
      status: null,
      timestamp: new Date().toISOString(),
    };
  }
  return mutate("/api/v1/card/budgets", "card-budget", {
    method: "PUT",
    body: { category, monthlyLimit: limit === null ? null : limit.toFixed(2) },
    successMessage: limit === null ? "Budget removed." : "Budget saved.",
    revalidate: ["/card"],
  });
}

const ALERT_KINDS = new Set(["above", "below", "gain_pct", "loss_pct"]);

/** Add a price or gain/loss alert for one ticker. */
export async function createAlertAction(form: FormData): Promise<ActionResult> {
  const ticker = optionalField(form, "ticker");
  const kind = optionalField(form, "kind");
  const raw = optionalField(form, "threshold");
  if (ticker === null) return missingFieldResult("Ticker");
  if (kind === null || !ALERT_KINDS.has(kind)) return missingFieldResult("Alert type");
  const threshold = raw === null ? Number.NaN : Number(raw.replace(",", "."));
  if (!Number.isFinite(threshold) || threshold <= 0) {
    return {
      ok: false,
      error: "Enter a number above zero.",
      status: null,
      timestamp: new Date().toISOString(),
    };
  }
  return mutate("/api/v1/alerts", "alerts-write", {
    body: { ticker, kind, threshold: String(threshold), note: optionalField(form, "note") },
    successMessage: "Alert set. You'll get a Windows notification when it's met.",
    revalidate: [`/holdings/${encodeURIComponent(ticker)}`, "/"],
  });
}

export async function deleteAlertAction(id: number, ticker: string): Promise<ActionResult> {
  return mutate(`/api/v1/alerts/${id}`, "alerts-write", {
    method: "DELETE",
    successMessage: "Alert removed.",
    revalidate: [`/holdings/${encodeURIComponent(ticker)}`],
  });
}

/** Follow an instrument: its prices and news are fetched in the background. */
export async function watchTickerAction(ticker: string): Promise<ActionResult> {
  return mutate("/api/v1/watchlist", "watchlist-write", {
    body: { ticker },
    successMessage: "Watching. Prices and news arrive within a minute or two.",
    revalidate: ["/watchlist", `/holdings/${encodeURIComponent(ticker)}`],
  });
}

export async function unwatchTickerAction(ticker: string): Promise<ActionResult> {
  return mutate(`/api/v1/watchlist/${encodeURIComponent(ticker)}`, "watchlist-write", {
    method: "DELETE",
    successMessage: "Removed from the watchlist.",
    revalidate: ["/watchlist", `/holdings/${encodeURIComponent(ticker)}`],
  });
}

export async function analyseWithAiAction(): Promise<ActionResult> {
  return mutate("/api/v1/ai/analyse", "ai-analyse", {
    successMessage: "Analysis complete.",
    revalidate: ["/", "/insights"],
  });
}

/** Write this week's review now. Bills the Anthropic account, like an analysis run. */
export async function writeWeeklyReviewAction(): Promise<ActionResult> {
  return mutate("/api/v1/ai/weekly", "ai-weekly", {
    successMessage: "This week's review is written.",
    revalidate: ["/insights"],
  });
}

/** A verified copy of the database, now. */
export async function backupNowAction(): Promise<ActionResult> {
  return mutate("/api/v1/backups", "backup", {
    successMessage: "Backed up and verified.",
    revalidate: ["/settings"],
  });
}

/** Drop raw data nothing replays any more and shrink the database file. */
export async function compactStorageAction(): Promise<ActionResult> {
  return mutate("/api/v1/storage/compact", "storage-compact", {
    successMessage: "Storage compacted.",
    messageFromResponse: true,
    revalidate: ["/settings"],
    timeoutMs: 120_000,
  });
}

// --- thesis and journal actions --------------------------------------------

export async function createThesisAction(form: FormData): Promise<ActionResult> {
  const title = requiredField(form, "title");
  const body = requiredField(form, "body");
  if (title === null) {
    return missingFieldResult("Title");
  }
  if (body === null) {
    return missingFieldResult("Reasoning");
  }
  return mutate("/api/v1/theses", "thesis-write", {
    successMessage: `Thesis "${title}" created as a draft.`,
    revalidate: ["/journal"],
    body: {
      title,
      body,
      t212Ticker: optionalField(form, "t212Ticker"),
      isin: optionalField(form, "isin"),
      conviction: optionalField(form, "conviction") ?? "medium",
      openedOn: optionalField(form, "openedOn"),
    },
  });
}

export async function editThesisAction(form: FormData): Promise<ActionResult> {
  const id = optionalField(form, "thesisId");
  if (id === null) {
    return missingFieldResult("Thesis");
  }
  // Only send what the operator actually changed: the backend rejects edits once a thesis
  // leaves draft, and an unchanged field is not a reason to attempt one.
  const payload: Record<string, string> = {};
  for (const field of ["title", "body", "conviction"] as const) {
    const value = optionalField(form, field);
    if (value !== null) {
      payload[field] = value;
    }
  }
  return mutate(`/api/v1/theses/${encodeURIComponent(id)}`, "thesis-write", {
    method: "PATCH",
    successMessage: "Thesis updated.",
    revalidate: ["/journal", `/journal/${encodeURIComponent(id)}`],
    body: payload,
  });
}

export async function transitionThesisAction(form: FormData): Promise<ActionResult> {
  const id = optionalField(form, "thesisId");
  const toStatus = optionalField(form, "toStatus");
  if (id === null) {
    return missingFieldResult("Thesis");
  }
  if (toStatus === null) {
    return missingFieldResult("New status");
  }
  return mutate(`/api/v1/theses/${encodeURIComponent(id)}/transition`, "thesis-write", {
    successMessage: `Thesis #${id} moved to ${toStatus}.`,
    revalidate: ["/journal", `/journal/${encodeURIComponent(id)}`],
    body: { toStatus, outcomeNote: optionalField(form, "outcomeNote") },
  });
}

export async function addJournalEntryAction(form: FormData): Promise<ActionResult> {
  const note = requiredField(form, "note");
  if (note === null) {
    return missingFieldResult("Note");
  }
  const thesisId = optionalField(form, "thesisId");
  return mutate("/api/v1/journal", "journal-write", {
    successMessage: "Journal entry added.",
    revalidate:
      thesisId === null ? ["/journal"] : ["/journal", `/journal/${encodeURIComponent(thesisId)}`],
    body: {
      note,
      thesisId: thesisId === null ? null : Number(thesisId),
      tags: optionalField(form, "tags"),
    },
  });
}

// --- settings actions ------------------------------------------------------
//
// The value submitted here is a credential, so it travels exactly one way: browser -> Next ->
// FastAPI -> OS keyring. It is never echoed back, never written to a log, and never returned in
// the result. The success message names where it landed, not what it was.

/** A credential write is one round trip to Trading 212 at most, so it does not need two minutes. */
const SETTINGS_TIMEOUT_MS = 30_000;

export async function saveCredentialAction(form: FormData): Promise<ActionResult> {
  const field = optionalField(form, "field");
  if (field === null) {
    return missingFieldResult("Credential");
  }
  // Deliberately *not* `optionalField`: a blank value is meaningful here. It clears the stored
  // credential, which is the only way to remove one from the dashboard.
  const raw = form.get("value");
  const value = typeof raw === "string" ? raw : "";

  return mutate("/api/v1/settings/credential", "settings-write", {
    method: "PUT",
    timeoutMs: SETTINGS_TIMEOUT_MS,
    successMessage: value.trim() === "" ? "Credential cleared." : "Credential saved.",
    revalidate: ["/settings", "/"],
    body: { field, value },
  });
}

/**
 * Connect a Trading 212 account: environment, key and secret in one request.
 *
 * They only mean anything as a set, so the API verifies them together against the environment
 * being chosen and stores nothing unless that passes. The success message is the API's own,
 * because it names the account it verified against.
 */
export async function connectTrading212Action(form: FormData): Promise<ActionResult> {
  const environment = optionalField(form, "environment");
  if (environment !== "demo" && environment !== "live") {
    return missingFieldResult("Environment");
  }
  const apiKey = optionalField(form, "apiKey");
  const apiSecret = optionalField(form, "apiSecret");
  if (apiKey === null || apiSecret === null) {
    return {
      ok: false,
      error: "Paste both the API key and the API secret — Trading 212 issues them as a pair.",
      status: null,
      timestamp: new Date().toISOString(),
    };
  }
  return mutate("/api/v1/settings/trading212", "settings-write", {
    method: "PUT",
    timeoutMs: SETTINGS_TIMEOUT_MS,
    successMessage: "Connected. Restart Helios to start syncing.",
    messageFromResponse: true,
    revalidate: ["/settings", "/"],
    body: { environment, apiKey, apiSecret },
  });
}

export async function saveSettingAction(form: FormData): Promise<ActionResult> {
  const field = optionalField(form, "field");
  const value = optionalField(form, "value");
  if (field === null) {
    return missingFieldResult("Setting");
  }
  if (value === null) {
    return missingFieldResult("Value");
  }
  return mutate("/api/v1/settings/value", "settings-write", {
    method: "PUT",
    timeoutMs: SETTINGS_TIMEOUT_MS,
    successMessage: "Setting saved.",
    revalidate: ["/settings"],
    body: { field, value },
  });
}

export async function createDatabaseAction(form: FormData): Promise<ActionResult> {
  const filename = optionalField(form, "filename");
  if (filename === null) {
    return missingFieldResult("Database name");
  }
  return mutate("/api/v1/settings/databases", "settings-write", {
    timeoutMs: SETTINGS_TIMEOUT_MS,
    successMessage: `${filename} created. Switch to it when you are ready.`,
    revalidate: ["/settings"],
    body: { filename },
  });
}

export async function switchDatabaseAction(form: FormData): Promise<ActionResult> {
  const filename = optionalField(form, "filename");
  if (filename === null) {
    return missingFieldResult("Database");
  }
  return mutate("/api/v1/settings/databases/active", "settings-write", {
    method: "PUT",
    timeoutMs: SETTINGS_TIMEOUT_MS,
    successMessage: `Helios will use ${filename} after a restart. The previous one is kept.`,
    revalidate: ["/settings"],
    body: { filename },
  });
}

/**
 * Bounce the API so saved settings take effect.
 *
 * The backend answers before it re-execs, so a success here means "accepted", not "already
 * back". The page tells the reader to wait a few seconds rather than implying the new
 * configuration is live the instant this returns.
 */
export async function restartApiAction(): Promise<ActionResult> {
  return mutate("/api/v1/settings/restart", "restart", {
    timeoutMs: SETTINGS_TIMEOUT_MS,
    successMessage: "Restarting. Reload in a few seconds to see the new configuration.",
    revalidate: ["/settings"],
  });
}
