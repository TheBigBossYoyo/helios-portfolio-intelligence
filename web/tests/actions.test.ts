import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { requestHeaders } from "./stubs/next-headers";
import {
  addJournalEntryAction,
  analyseWithAiAction,
  createDatabaseAction,
  createThesisAction,
  editThesisAction,
  replayPerformanceAction,
  restartApiAction,
  saveCredentialAction,
  saveSettingAction,
  switchDatabaseAction,
  syncNewsAction,
  syncPortfolioAction,
  transitionThesisAction,
} from "@/lib/actions";

/**
 * What these tests protect.
 *
 * The server actions are the only path from a click to a state change, and three things about
 * each call have to hold or the button is either broken or unsafe:
 *
 * - **The local-action header is present and correct.** The backend refuses every mutating
 *   route without it, so a wrong or missing value turns a button into a silent 403.
 * - **The payload uses the aliases the backend declares.** `t212Ticker`, `toStatus` and
 *   `thesisId` are the wire names; sending snake_case would be accepted by nothing.
 * - **A failure explains itself.** FastAPI puts the reason in `detail`; dropping it would
 *   surface "HTTP 409" where "Portfolio sync already running" was available.
 *
 * `fetch` is stubbed so no API is needed and the exact outgoing request can be inspected.
 */

interface Captured {
  url: string;
  method: string;
  headers: Record<string, string>;
  body: unknown;
}

let calls: Captured[] = [];

/** Stub `fetch`, recording each request and replying with the given status/body. */
function stubFetch(status = 200, body: unknown = {}) {
  const mock = vi.fn(async (url: string | URL, init?: RequestInit) => {
    const headers: Record<string, string> = {};
    for (const [key, value] of Object.entries((init?.headers ?? {}) as Record<string, string>)) {
      headers[key] = value;
    }
    calls.push({
      url: String(url),
      method: init?.method ?? "GET",
      headers,
      body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined,
    });
    return {
      ok: status >= 200 && status < 300,
      status,
      json: async () => body,
    } as Response;
  });
  vi.stubGlobal("fetch", mock);
  return mock;
}

beforeEach(() => {
  calls = [];
  process.env.HELIOS_API_URL = "http://api.test:8000";
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete process.env.HELIOS_API_URL;
});

describe("local-action header", () => {
  /**
   * The guarded boundary is mutating vs non-mutating, so thesis and journal writes carry the
   * header exactly like a sync does. A mismatch here is a 403 the user cannot diagnose.
   */
  it.each([
    ["sync", () => syncPortfolioAction(), "/api/v1/portfolio/sync"],
    ["replay", () => replayPerformanceAction(), "/api/v1/performance/replay"],
    ["news-sync", () => syncNewsAction(), "/api/v1/news/sync"],
    ["ai-analyse", () => analyseWithAiAction(), "/api/v1/ai/analyse"],
  ])("%s sends its action name to %s", async (action, run, path) => {
    stubFetch();

    await run();

    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe(`http://api.test:8000${path}`);
    expect(calls[0].method).toBe("POST");
    expect(calls[0].headers["X-Helios-Local-Action"]).toBe(action);
  });

  it("sends thesis-write for every thesis mutation", async () => {
    stubFetch();

    await createThesisAction(formData({ title: "T", body: "B" }));
    await editThesisAction(formData({ thesisId: "1", title: "New" }));
    await transitionThesisAction(formData({ thesisId: "1", toStatus: "active" }));

    expect(calls.map((call) => call.headers["X-Helios-Local-Action"])).toEqual([
      "thesis-write",
      "thesis-write",
      "thesis-write",
    ]);
  });

  it("sends journal-write for a journal entry", async () => {
    stubFetch();

    await addJournalEntryAction(formData({ note: "Added on the pullback" }));

    expect(calls[0].headers["X-Helios-Local-Action"]).toBe("journal-write");
  });
});

describe("payloads use the aliases the backend declares", () => {
  it("creates a thesis with camelCase wire names", async () => {
    stubFetch();

    await createThesisAction(
      formData({
        title: "Services compound",
        body: "Recurring revenue keeps growing.",
        t212Ticker: "AAPL_US_EQ",
        conviction: "high",
        openedOn: "2026-08-01",
      }),
    );

    expect(calls[0].body).toEqual({
      title: "Services compound",
      body: "Recurring revenue keeps growing.",
      t212Ticker: "AAPL_US_EQ",
      isin: null,
      conviction: "high",
      openedOn: "2026-08-01",
    });
  });

  it("defaults conviction rather than sending an empty string", async () => {
    stubFetch();

    await createThesisAction(formData({ title: "T", body: "B", conviction: "  " }));

    expect((calls[0].body as { conviction: string }).conviction).toBe("medium");
  });

  it("PATCHes only the fields that were actually filled in", async () => {
    stubFetch();

    // An untouched field is not an edit. Sending it as null would blank the stored value.
    await editThesisAction(formData({ thesisId: "7", body: "Revised reasoning" }));

    expect(calls[0].method).toBe("PATCH");
    expect(calls[0].url).toBe("http://api.test:8000/api/v1/theses/7");
    expect(calls[0].body).toEqual({ body: "Revised reasoning" });
  });

  it("sends the transition target and its outcome note", async () => {
    stubFetch();

    await transitionThesisAction(
      formData({ thesisId: "3", toStatus: "validated", outcomeNote: "Services grew." }),
    );

    expect(calls[0].url).toBe("http://api.test:8000/api/v1/theses/3/transition");
    expect(calls[0].body).toEqual({ toStatus: "validated", outcomeNote: "Services grew." });
  });

  it("sends a numeric thesisId, or null for a standalone note", async () => {
    stubFetch();

    await addJournalEntryAction(formData({ note: "Tied", thesisId: "4", tags: "macro" }));
    await addJournalEntryAction(formData({ note: "Standalone" }));

    expect(calls[0].body).toEqual({ note: "Tied", thesisId: 4, tags: "macro" });
    expect(calls[1].body).toEqual({ note: "Standalone", thesisId: null, tags: null });
  });
});

describe("validation happens before the request", () => {
  /**
   * A blank required field is a mistake the form can name immediately. Posting it would spend
   * a round trip to be told the same thing less clearly.
   */
  it.each([
    ["a thesis with no title", () => createThesisAction(formData({ body: "B" })), "Title"],
    ["a thesis with no reasoning", () => createThesisAction(formData({ title: "T" })), "Reasoning"],
    ["an edit with no thesis", () => editThesisAction(formData({ title: "T" })), "Thesis"],
    [
      "a transition with no target",
      () => transitionThesisAction(formData({ thesisId: "1" })),
      "New status",
    ],
    ["a journal entry with no note", () => addJournalEntryAction(formData({})), "Note"],
  ])("refuses %s without calling the API", async (_label, run, field) => {
    const mock = stubFetch();

    const result = await run();

    expect(mock).not.toHaveBeenCalled();
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error).toContain(field);
    }
  });

  it("treats a whitespace-only field as absent", async () => {
    const mock = stubFetch();

    const result = await createThesisAction(formData({ title: "   ", body: "B" }));

    expect(mock).not.toHaveBeenCalled();
    expect(result.ok).toBe(false);
  });
});

describe("failures explain themselves", () => {
  it("surfaces the backend's detail rather than the status code", async () => {
    stubFetch(409, { detail: "Portfolio sync already running" });

    const result = await syncPortfolioAction();

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error).toBe("Portfolio sync already running");
      expect(result.status).toBe(409);
    }
  });

  it("falls back to the status when there is no usable detail", async () => {
    stubFetch(500, { detail: "   " });

    const result = await replayPerformanceAction();

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error).toBe("HTTP 500");
    }
  });

  it("survives an error body that is not JSON at all", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false,
        status: 502,
        json: async () => {
          throw new SyntaxError("Unexpected token");
        },
      })) as unknown as typeof fetch,
    );

    const result = await syncNewsAction();

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error).toBe("HTTP 502");
    }
  });

  it("reports a transport failure instead of throwing into the page", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("connect ECONNREFUSED");
      }) as unknown as typeof fetch,
    );

    const result = await analyseWithAiAction();

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error).toBe("connect ECONNREFUSED");
      expect(result.status).toBeNull();
    }
  });

  it("says a timed-out run may still be going, because it may be", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        const error = new Error("The operation was aborted due to timeout");
        error.name = "TimeoutError";
        throw error;
      }) as unknown as typeof fetch,
    );

    const result = await syncPortfolioAction();

    expect(result.ok).toBe(false);
    if (!result.ok) {
      // The server does not stop working because the client stopped waiting. Telling the user
      // it failed outright would invite a duplicate run against a metered quota.
      expect(result.error).toContain("still be running");
    }
  });
});

describe("success", () => {
  it("reports what happened and when", async () => {
    stubFetch();

    const result = await syncPortfolioAction();

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.message).toContain("Trading 212");
      expect(Number.isNaN(Date.parse(result.timestamp))).toBe(false);
    }
  });

  it("names the thesis it created, so the confirmation is specific", async () => {
    stubFetch();

    const result = await createThesisAction(formData({ title: "Services compound", body: "B" }));

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.message).toContain("Services compound");
    }
  });
});

describe("settings writes", () => {
  it("PUTs a credential with the settings-write header", async () => {
    stubFetch();

    await saveCredentialAction(formData({ field: "anthropic_api_key", value: "sk-ant-123456" }));

    expect(calls[0].url).toBe("http://api.test:8000/api/v1/settings/credential");
    expect(calls[0].method).toBe("PUT");
    expect(calls[0].headers["X-Helios-Local-Action"]).toBe("settings-write");
    expect(calls[0].body).toEqual({ field: "anthropic_api_key", value: "sk-ant-123456" });
  });

  it("never puts a secret in the result it hands back to the page", async () => {
    stubFetch(200, { field: "anthropic_api_key", storedIn: "keyring", detail: "Stored." });

    const secret = "sk-ant-do-not-echo-this";
    const result = await saveCredentialAction(
      formData({ field: "anthropic_api_key", value: secret }),
    );

    // The value goes one way only. A success message that quoted the key back would put it in
    // the DOM, in React's server-action payload, and in any error overlay that rendered it.
    expect(JSON.stringify(result)).not.toContain(secret);
  });

  it("sends a blank value through, because blank is how a credential is cleared", async () => {
    const mock = stubFetch();

    // Deliberately not treated as a missing field: an empty submit is the only way to remove a
    // stored credential from the dashboard, so it must reach the API rather than be validated off.
    const result = await saveCredentialAction(formData({ field: "openfigi_api_key", value: "" }));

    expect(mock).toHaveBeenCalled();
    expect(calls[0].body).toEqual({ field: "openfigi_api_key", value: "" });
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.message).toContain("cleared");
    }
  });

  it("refuses a credential write with no field, without calling the API", async () => {
    const mock = stubFetch();

    const result = await saveCredentialAction(formData({ value: "orphan" }));

    expect(mock).not.toHaveBeenCalled();
    expect(result.ok).toBe(false);
  });

  it("PUTs a non-credential setting", async () => {
    stubFetch();

    await saveSettingAction(
      formData({ field: "t212_base_url", value: "https://live.trading212.com/api/v0" }),
    );

    expect(calls[0].url).toBe("http://api.test:8000/api/v1/settings/value");
    expect(calls[0].method).toBe("PUT");
    expect(calls[0].body).toEqual({
      field: "t212_base_url",
      value: "https://live.trading212.com/api/v0",
    });
  });

  it("surfaces the allowlist refusal verbatim", async () => {
    stubFetch(400, {
      detail: "Trading 212 base URL must be one of: https://demo.trading212.com/api/v0",
    });

    const result = await saveSettingAction(
      formData({ field: "t212_base_url", value: "https://evil.example.com/api/v0" }),
    );

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error).toContain("must be one of");
    }
  });
});

describe("database administration", () => {
  it("POSTs a create and PUTs a switch, both as settings-write", async () => {
    stubFetch();

    await createDatabaseAction(formData({ filename: "helios-live.sqlite3" }));
    await switchDatabaseAction(formData({ filename: "helios-live.sqlite3" }));

    expect(calls[0].method).toBe("POST");
    expect(calls[0].url).toBe("http://api.test:8000/api/v1/settings/databases");
    expect(calls[1].method).toBe("PUT");
    expect(calls[1].url).toBe("http://api.test:8000/api/v1/settings/databases/active");
    expect(calls.map((call) => call.headers["X-Helios-Local-Action"])).toEqual([
      "settings-write",
      "settings-write",
    ]);
  });

  it("says the previous database is kept, because it is", async () => {
    stubFetch();

    const result = await switchDatabaseAction(formData({ filename: "helios-live.sqlite3" }));

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.message).toContain("previous one is kept");
    }
  });

  it("passes a path-like filename to the backend to reject", async () => {
    // The containment boundary is server-side on purpose: a client-side check would be the
    // only thing standing between a settings write and an arbitrary file if it were the check.
    stubFetch(400, { detail: "'../../x.sqlite3' looks like a path." });

    const result = await createDatabaseAction(formData({ filename: "../../x.sqlite3" }));

    expect(calls).toHaveLength(1);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error).toContain("looks like a path");
    }
  });
});

describe("restart", () => {
  it("sends the restart action name, not settings-write", async () => {
    stubFetch();

    await restartApiAction();

    expect(calls[0].url).toBe("http://api.test:8000/api/v1/settings/restart");
    expect(calls[0].headers["X-Helios-Local-Action"]).toBe("restart");
  });

  it("reports acceptance rather than claiming the API is already back", async () => {
    stubFetch();

    const result = await restartApiAction();

    expect(result.ok).toBe(true);
    if (result.ok) {
      // The backend answers before it re-execs, so "restarting" is the honest tense here.
      expect(result.message).toContain("Reload");
    }
  });

  it("surfaces the rate limit instead of retrying into it", async () => {
    stubFetch(429, { detail: "Too many attempts. 3 allowed per 300s; try again in 120s." });

    const result = await restartApiAction();

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error).toContain("Too many attempts");
      expect(result.status).toBe(429);
    }
  });
});

function formData(fields: Record<string, string>): FormData {
  const form = new FormData();
  for (const [key, value] of Object.entries(fields)) {
    form.set(key, value);
  }
  return form;
}

describe("from a paired phone", () => {
  afterEach(() => {
    requestHeaders.delete("x-helios-remote");
  });

  it("refuses settings changes and restarts without calling the API", async () => {
    const mock = stubFetch();
    requestHeaders.set("x-helios-remote", "1");

    const setting = await saveSettingAction(formData({ field: "t212_base_url", value: "x" }));
    const restart = await restartApiAction();

    expect(mock).not.toHaveBeenCalled();
    for (const result of [setting, restart]) {
      expect(result.ok).toBe(false);
      if (!result.ok) expect(result.error).toBe("Change this on the computer running Helios.");
    }
  });

  it("still syncs: everyday actions work from the phone", async () => {
    stubFetch(200, {});
    requestHeaders.set("x-helios-remote", "1");

    await syncPortfolioAction();

    expect(calls[0].headers["X-Helios-Local-Action"]).toBe("sync");
  });
});
