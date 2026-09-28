# Helios — Master Resume Plan (M3 → M7) — **HISTORICAL RECORD**

> **STATUS: SUPERSEDED. M1–M7 are all built, committed, and green.** This file is kept as the
> record of how the milestones were planned and what was decided along the way. It is **not** a
> live roadmap, and the sections below describing "uncommitted M3 work" and "17 open defects"
> describe a state that no longer exists. Read the roadmap section at the bottom for what is
> actually left.

## TL;DR

> **Quick Summary**: Helios is a local-first, read-only Trading 212 portfolio intelligence
> platform. Milestones 1–7 are complete on `main`. M3's 17-point correctness fix list was
> finished (see `.sisyphus/plans/m3-performance-correctness.md`, all TODOs marked done) and the
> analytics, dashboard, news, AI, and thesis/journal engines all shipped. M8 (final review) was
> deliberately left out of scope.
>
> **Deliverables** (by milestone, all delivered):
> - **M3**: Correct, non-deceptive performance/risk analytics with daily NAV/TWR/XIRR
>   reconstruction, benchmarks, attribution, VaR/CVaR, FF5+momentum, and a passive counterfactual.
> - **M4**: Frontend web dashboard rendering positions, valuation, and analytics.
> - **M5**: Financial news ingestion and enrichment per instrument.
> - **M6**: AI analysis. **Delivered against Claude, not Ollama** — the user changed this after
>   the plan was written, so every "Ollama" reference below is historical intent, not the
>   implementation. See `src/helios/ai.py`.
> - **M7**: Thesis/journal feature tracking investment ideas and notes.
>
> **What is actually left** (not milestone work — infrastructure and completion gaps found in a
> later full-project audit): the dashboard's mutation surface, environment reproducibility, CI,
> and documentation drift. Those are tracked in the "Post-milestone gaps" section at the end.

---

## Context

### Original Request
User requested a full Helios portfolio intelligence app over Trading 212 (EUR Invest account, demo first, local-only). Eight milestones. Never place trades. Everything financial gets a test. Raw snapshots non-negotiable. Join on ISIN not ticker. Decimal never float. Work milestone-by-milestone; commit/push often.

### Interview Summary
**Key Decisions**:
- Trading 212 stays strictly GET-only; OpenFIGI POST isolated in its own client.
- Alembic is the sole schema authority.
- Financial values as canonical decimal strings in SQLite `TEXT` (never SQLite floating `NUMERIC`).
- Join/resolve instruments via ISIN; manual mappings override providers.
- Replay per-fill quantities, not aggregate order quantities.
- Non-`TRADE` fills → explicit `UNSUPPORTED_ACTION`.
- Manual sync protected by `X-Helios-Local-Action: sync`.
- EUR base currency; M3 reconstructs daily holdings/NAV because T212 has no historical NAV endpoint.
- Benchmarks are labeled ETF proxies (CSPX=SP500, SWDA=MSCI World, VWRP=FTSE All-World) + mandatory "you-but-passive" counterfactual — never official licensed index levels.
- ECB EUR-base FX (`D.<CCY>.EUR.SP00.A`, CCY per EUR); forward-fill last known fix with explicit staleness.
- Ollama now; paid AI deferred; no paid market-data credential → market data optional/configurable, deterministic under fixtures, never fabricated.
- Demo T212 credentials were exposed in verification output; user will rotate them (blocking follow-up).

**Research Findings** (5 M3 discovery tracks, all retrieved):
- `bg_817eab05` (ledger map): M3 = NEW service/read-model, never mutate `PortfolioSyncService`; `transactions` lost ticker/ISIN; `positions_live` is snapshot-only; don't overload `SyncStatus`.
- `bg_5930aa65` (providers): no clean free provider; Twelve Data best paid; yfinance unofficial/research-only; keep provider abstraction; avoid Stooq.
- `bg_485540bc` (FX/benchmarks): ECB EXR protocol; ETF proxies with inception-date limits (CSPX 2010, SWDA 2009, VWRP 2019).
- `bg_5d8a6ac8` (quant methodology): GIPS-conformant TWR, labeled flow timing, bracketed XIRR, explicit annualization basis/ddof/NaN policies, Brinson-Fachler preferred.
- `bg_37e2e0a4` (testing): no conftest/fixtures, no calendar abstraction, Hypothesis present but unused; keep SQLite authoritative.

### Metis Review
Metis aborted in an earlier session (unrelated). Replaced by the five specialist discovery tracks above and direct history recovery. For future milestone planning, prefer Librarian/Oracle research over Metis if Metis continues to fail.

---

## Repository State **as this plan was written** (historical — see the note at the top)

> Everything in this section is superseded. M3 was corrected and committed; the "uncommitted
> working tree" it describes no longer exists. See "Post-milestone gaps" at the end for the
> current state.

### Committed / Accepted (on `main`, HEAD = `8b9eb13`)
- M1: GET-only async T212 client, raw snapshots, rate limits, SQLite WAL, Alembic, FastAPI, CLI, worker, Next.js shell, Docker Compose, secret controls. Ports: web `3001`, API `8001`.
- M2: official permissive DTOs, paginated order/dividend/transaction ingestion, exact text-backed Decimal ledger, metadata cache (24h TTL), OpenFIGI resolution with overrides, fill replay + reconciliation, sync lease, quality reporting, worker scheduling, runtime hardening. Migration head `0004_align_cash_transactions`. 81 tests green at commit time.

### Uncommitted in working tree (M3 first-pass — DO NOT commit as-is)
- **New:** `src/helios/performance.py`, `alembic/versions/0005_m3_performance_foundation.py`, `tests/test_performance.py`.
- **Modified:** `models.py` (M3 tables), `portfolio_repository.py` (replay/cache/report helpers), `schemas.py`, `api.py` (replay/report endpoints), `cli.py` (performance-replay/report), `dependencies.py` (provider wiring), `worker.py` (replay after sync), `config.py` (new settings), `pyproject.toml` (analytics deps added), plus edits to `tests/test_api.py`, `test_cli.py`, `test_db.py`, `test_dependencies.py`, `test_worker.py`, `README.md`.
- **Unrelated format-only diffs that MUST be reverted:** `src/helios/portfolio_sync.py`, `src/helios/resolver.py`, `tests/test_client.py`, `tests/test_resolver.py`.
- Current suite passes ~92 tests but encodes some wrong behavior (see M3 fix list).

### Where the M3 work stands
- `performance.py` implements: provider protocols, ECB provider, optional AlphaVantage provider, daily replay service, TWR/XIRR, volatility/downside/Sharpe/Sortino/Calmar, drawdown, beta/alpha/R²/corr/TE/IR, contribution, Brinson, HHI/effective/top5, VaR/CVaR, FF5+momentum, rolling vol/beta.
- **Known defects (must fix):** placeholders wired to empty inputs; non-currency cash added as EUR; no weekend price carry-forward / stale cutoff; cache merge drops cached points; `_missing_price_requests` uses calendar-day count not market-observation coverage; wrong drawdown recovery peak; TWR deposit-only day test wrong; Alpha Vantage hardcodes USD; dividend double-count risk; contribution stubs zero returns; missing "you-but-passive"; missing 30/90 rolling; missing correlation clustering; FF5 not on excess returns + no factor source; flow timing not a labeled contract.
- A full fix spec already exists at **`.sisyphus/plans/m3-performance-correctness.md`** (17 TODOs). This master plan folds M3 into the roadmap as Milestone M3-COMP with the correct file as its runnable fix-list.

---

## Milestone Roadmap (M3 → M7)

### M3-COMP — Complete & Accept Milestone 3 (IN PROGRESS, uncommitted)
**Objective:** Deliver correct, non-deceptive M3 performance/risk analytics, committed and accepted.
**Runnable spec:** `.sisyphus/plans/m3-performance-correctness.md` (authoritative, 17 TODOs).

**Highlights of remaining M3 work:**
- Flow-time contract (`flow_at_open/close/intraday_split`) + corrected TWR test.
- Non-EUR cash conversion at flow date (or explicit exclusion), dividend double-count guard.
- Weekend/holiday price carry-forward with `FORWARD_FILL` + configurable stale-price cutoff.
- Cache merge preserving cached observations + true provider_symbol; `_missing_price_requests` by market-observation coverage.
- Revert M2-format-only diffs in `portfolio_sync.py`, `resolver.py`, `test_client.py`, `test_resolver.py` (git checkout).
- Contribution from real per-holding period returns (not `0.0` stubs).
- Brinson includes benchmark-only sectors; sum = active return.
- Rolling beta AND volatility for BOTH 30-day and 90-day windows in schema/report.
- "You-but-passive" counterfactual (external contributions invested in configured proxy at flow dates).
- Correlation clustering in report contract (distance → linkage; insufficient-data explicit).
- VaR/CVaR actual sample-size reporting + labeled quantile/sign; alphavantage currency via trusted metadata or disabled.
- FF5+momentum regression on excess returns; `FactorDataProvider` if live factor ingest; else explicit unavailable status.
- Config validation (provider enum, flow literal, positive stale-days, base currency MUST be EUR); never `date.today()` as clock.
- Schema/api/cli/app contract updates for all the above; README M3 disclosure.
- Full validation: `pytest -q`, `ruff check src tests`, strict `mypy`, `lsp_diagnostics`.

**Acceptance for M3:** all 17 fix TODOs done with regression tests; zero placeholder metrics; M2-format diffs reverted; all suites green; F1–F4 review (oracle compliance, code-quality, manual QA, scope fidelity) all approve; then commit+pull atomically (per-topic commits, Sisyphus attribution footer per repo style). **Rotate the leaked demo T212 credentials before/while doing M3.**

---

### M4 — Frontend Web Dashboard — BUILT (uncommitted, 2026-08-06)

**Delivered:** four server-rendered routes (`/`, `/holdings`, `/performance`, `/data-quality`) in
`web/`, plus `web/lib/{api,types,format,series,viz}.ts`, `web/components/**`, vitest (41 tests) and
Playwright (21 scenarios + 4 screenshot captures). Backend gained exactly one thin projection —
`navSeries` on the performance report — as the plan allows.

**Decisions taken:**
- **No login.** The plan's default (local-only, loopback-bound) was adopted. Open decision #3 is
  therefore resolved unless the user says otherwise.
- **All fetching in RSCs.** `lib/api.ts` imports `server-only`; the browser never contacts the API,
  so `HELIOS_API_URL` and all backend credentials stay server-side. An E2E test asserts no browser
  request reaches the API origin and that the URL never appears in page HTML.
- **Recharts 3** for charts, per the plan's suggestion.
- **Palette validated, not eyeballed.** Ran the dataviz validator against this app's own surface
  (`#09090b`): reference dark slots 1-3 pass all-pairs (CVD ΔE 9.4, normal-vision 20.9, all ≥3:1).
  The brand amber `#ffb000` FAILS the lightness band (L 0.812), so it stays UI chrome and is not a
  series color. Recorded in `web/lib/viz.ts`.
- **Ports moved to the planned 3001/8001** as compose defaults; `.env` overrides still win.

**Two issues found by looking at the rendered output** (the dataviz procedure's final step), both
fixed: the NAV y-axis was anchored at €0 and squashed the series into a flat band (now a fitted
domain, with a comment on why bars would be the opposite case); and the 120-row NAV table rendered
inline, making the performance page 7,588px tall (now a 420px scroll container with a sticky
header, page down to 3,619px). A third, smaller fix: the holdings price cell had no real space
between value and currency, so its accessible name read `182.40USD`.

**Evidence:** `.sisyphus/evidence/m4/*.png` (full-page captures of all four routes).

**Validation:** web lint clean, `tsc --noEmit` clean, `next build` clean, vitest 41 passed,
Playwright 25 passed; backend still 130 passed / ruff clean / strict mypy clean.

### M4 — Frontend Web Dashboard (original plan)
**Objective:** A self-hosted Next.js dashboard rendering portfolio valuation, valuation history, and M3 analytics from the API.
**Tasks (parallel / multiple chunks):**
- Dashboard layout + navigation (portfolio, holdings, performance, data-quality pages).
- Positions table (live from `/api/v1/t212/positions`): ticker, quantity, current price, wallet values.
- Instrument mapping / quality issues page (from `/api/v1/portfolio/data-quality`).
- Performance page: cumulative/annualized/TWR/XIRR, daily NAV chart, drawdown, Sharpe/Sortino/Calmar, rolling vol/beta 30/90, benchmark comparisons, attribution, VaR/CVaR.
- Chart library integration (e.g., Recharts/ECharts) from typed API DTOs.
- Auth/security: keep read-only, local binding (web `3001`), no exposed credentials; API keys stay backend-only.
- Wire toward cash/log at localhost (web `3001`, API `8001`); keep compose working.
- Tests: component render + integration slice; latest tile press uses E2E (Playwright skill for UI).
- M4 only renders; no new backend endpoints unless a thin projection is needed.
**Acceptance:** Review social; 100% E2E QA scenarios pass; builds; compose validated

---

### M5/M6/M7 — BUILT overnight 2026-08-06/07

**M5+ (multi-source news).** Reworked from single-provider RSS into a combined stack: Yahoo
Finance per-holding, SEC EDGAR filings, Marketaux JSON, Google News, plus operator feeds — all
disabled by default. Key finding from thinking it through: **combining sources breaks the
`(url, published_at)` dedupe rule**, because the same story arrives under three different URLs.
Added canonical-URL normalisation + title-key matching in a time window, with per-source trust so
a primary SEC filing outranks the aggregator quoting it. Dedupe runs both within a sync and
against stored history. Added `{yahoo_ticker}` / `{name}` placeholders (the gap that made Yahoo
RSS unusable).

**M6 (Claude, replacing Ollama at user's request).** `src/helios/ai.py`. Guardrails are
structural, not just prompt text: the output schema has no field for a rating/target/action and
sets `additionalProperties: false`; `evidence` is required and uncited observations are dropped;
M3's `insufficient_data`/`unavailable` statuses travel with the numbers. Adaptive thinking,
structured outputs, prompt caching, server-side refusal fallbacks. Runs only on demand —
deliberately not on the worker schedule, because each run costs money.

**M7 (thesis/journal).** `src/helios/thesis.py`. One rule drives the design: **the original
reasoning is frozen once a thesis leaves draft.** Status is a state machine
(`draft → active → validated|invalidated → closed`, closed terminal); closing requires an outcome
note. Later thinking goes to the journal.

**Dashboard polish.** Layered background wash, panel depth via inset highlights, animated nav
underline, gradient hero, focus-visible ring, styled scrollbars — all inside the existing
industrial theme, with reduced-motion guards.

**Validation:** backend 223 passed / ruff / strict mypy clean. Web: lint + tsc + build clean,
vitest 51, Playwright 42.

### M5 — Financial News — BUILT (uncommitted, 2026-08-06)

**Open decision #4 (news sources) resolved by design rather than by picking publishers.** The
plan's own constraints (licensing-safe, no paywalled scraping, label the source) pointed at one
answer: Helios ships **zero feeds**. `config/news_feeds.yaml` is empty and the operator declares
publishers they are entitled to read. RSS 2.0 + Atom via a `NewsFeedProvider` protocol, so a paid
API can slot in later without touching the pipeline. No credentials, no cost, deterministic under
fixtures.

**Delivered:** `src/helios/news.py`, migration `0006` (`raw_news`, `news_items`), repository
methods, `POST /api/v1/news/sync` + `GET /api/v1/news`, `helios news-sync` / `helios news`, a
worker job on its own cadence, a `/news` page with per-holding filtering, and a news panel on the
overview. 38 backend tests + 10 web tests + 5 E2E scenarios.

**Rules enforced in code, not just documented:**
- Feeds only — the article link is never followed, article bodies are never fetched or stored.
- Raw-first: the feed body lands in `raw_news` before parsing, so a parser fix is replayable.
- Instrument linkage is **declared, never guessed**. No headline scanning for company names. A
  feed bound to several instruments is stored market-wide rather than attributed to one.
- Dedupe on (url, published_at), hashed into the PK so a null timestamp can't slip past a UNIQUE
  index. Timezone-independent: the same instant in two offsets is one article.
- `defusedxml` for untrusted feed bodies (a billion-laughs test proves the bomb is rejected);
  http/https schemes only (blocks `file://`); body size capped.
- One failing publisher cannot abort a sync or the portfolio loop — separate worker job, separate
  log event.
- Undated articles are labelled "Undated" and sorted last; never given a guessed date.

**Validation:** backend 172 passed / ruff clean / strict mypy clean; web lint + tsc clean,
vitest 51 passed, Playwright 31 passed, `next build` clean.

### M5 — Financial News & Incorporate (original plan)
**Goal:** Fetch and store news per instrument, enrich or render alongside analytics, legal/system-safe.
**Tasks:**
- **Sources**: documented, licensing-safe providers only (e.g., configurable RSS / syndicated feeds, or official platform endpoints). No scraping of copyrighted paywalled content. Label source.
- **Ingest**: worker job fetches news on a cadence; persist raw responses as `raw_news` (raw-first pattern), then parsed `news_items`.
- **Repository + model + migration (0006)**: news_item (id, ticker/ISIN, headline, source, url, published_at, fetched_at, raw ref). Exact timestamps UTC.
- **Enrichment**: map news to instruments by ISIN/ticker; allow manual source filtering; dedupe by (url, published_at).
- **API**: GET news list, by ticker/ISIN; worker schedules news sync; CLI `helios news-sync`.
- **Frontend (M4 reuse)**: news panel/feed per instrument.
- **Tests**: raw-before-parse, dedupe, mapping, empty/missing source, timezone.
**Acceptance:** safe fetch pipeline, persisted raw + parsed, API/CLI/worker wired, tests + review, committed atomically.

---

### M6 — AI Recommendations via Ollama
**Goal:** Use local Ollama (no external fee API) to produce descriptive, labeled factor/risk Returns possible with rank table + per-month cost estimate; paid providers deferred; no full advisory.
**Tasks:**
- **Config**: `HELIOS_IVLLAMA_BASE_URL` (default `http://localhost:11434`), `HELIOS_OLAMA_MODEL`, optional model list.
- **Providers**: `AIOllamaProvider` protocol + optional resigned provider; deterministic fixtures; no paid key required for dev.
- **Prompt building**: take M3 analytics (concentration, attribution, VaR/CVaR, sector/ports) to produce a bounded, factual narrative. NEVER claim exact forecasts or borrow-finger financial volume to be a licensed financial advisory product. Add explicit "not financial advice" disclosure.
- **Ranking / recommendations**: structural per-holding thesis-grade signals (concentration > X% flagged, factor exposure, compare exposure). Output ranked JSON.
- **Persistence (0007)**: ai_recommendations / ai_runs tables (prompt+vars, provider, model, raw LLM response, parsed), for audit/replay.
- **API/CLI/worker**: GET/POST recommend; `helios ai-recommend`; optional scheduled rerun.
- **Frontend (from M4)**: analytics page AI panel with disclosure.
- **Cost table**: (research + librarian) provider/model comparison = EUR/month estimate; store as markdown/report.
**Acceptance:** Ollama callable locally, raw responses stored, deterministic tests with mock Ollama (fixed fixture JSON), no fake trust, disabled-graceful absence warning.

---

### M7 — Thesis / Journal
**Goal:** Track an investment thesis and journal entries tied to instruments/portfolio, local-first.
**Tasks:**
- **Model + migration (0008)**: `theses` (id, isin, title, body, decision_date, status: local/active/won/abandoned), `journal_entries` (id, thesis_id, created_at, note, tags), `journal_files` (optional attachments).
- **Repository/API/CLI**: CRUD-thesis-create/update/close; journal add/list by thesis; `helios thesis`, `helios journal`.
- **Enrichment**: attach M3 stats / news (M5) / AI comments (M6) into thesis page context (read-only references).
- **Frontend (M4)**: thesis & journal views.
- **Tests**: CRUD, validation (status transitions), references lazy/empty.
**Acceptance:** CRUD-driven workflow works end-to-end, tests, review.

---

## Cross-Milestone Engineering Guidelines (apply to M4–M7)
- Everything financially or source-trust gets text-backed `Decimal`, UTC timestamps, raw-first persistence.
- No scraping copyrighted content; no unofficial APIs as canonical; typed labels of data provenance/source everywhere.
- Provider protocols + DI in `dependencies.py`; new alembic revisions 0006–0008 after 0005.
- Every new endpoint/UI fully E2E-verified (Playwright for UI) with captured evidence; no human-in-the-loop acceptance.
- Never place trades; local-only; secrets never in repo; rotate exposed demo credentials.
- Milestone at a time: each milestone fully accepts (tests + review + commit/push) before next.

---

## Success Criteria (per milestone)
Each milestone independently satisfies:
- [ ] Tests + lint + strict mypy + diagnostics green.
- [ ] No placeholders / no fabricated data / no secrets.
- [ ] Agent-executed QA scenarios (Playwright for UI, curl for API, tmux for CLI) all pass with captured evidence.
- [ ] F1 plan-compliance, F2 code-quality, F3 manual-QA, F4 scope-fidelity review approve.
- [ ] Committed and pushed atomically (semantic style, Sisyphus footer), user approval.
- [ ] Master success = all eight milestones delivered; current goal = M3 accepted, then M4→M7.

---

## Decisions Needed (open, expect user input)
1. **M3 fix executor**: the continuation `deep` task aborts; fresh `deep` delegation worked once. Confirm delegation continues to be allowed/the reliable path, or whether I must pause planning only and a human/other service runs the fix. (Currently: stop delegation per user instruction; plan records intent.)
2. **T212 demo credential rotation** — urgent operational follow-up, blocks live-data validation. Only M3+|M4 host can verify.
3. **M4 auth**: local-only is fine by default; confirm no login requirement.
4. **M5 sources**: which news providers/sources are acceptable (licensing, cost, scope) — needs an explicit choice before M5 planning, via Librarian/explore first.
5. **M6**: confirm Ollama config/model acceptable; whether to include the provider month-cost comparison table.
6. **M8 final** is intentionally out of scope of this plan (stop at M7) unless re-included.

---

## How to Use This Plan

This is a **historical record**, not a set of instructions. The M3 resume steps that used to be
here described work that is finished; following them now would revert shipped code. Read it to
understand *why* something is the way it is, and read the section below for what remains.

---

## Post-milestone gaps

Found by a full-project audit after M7. None of these are milestone work — the milestones are
done; these are the completion and infrastructure gaps between "all features built" and
"finished system".

### Closed

- **Dashboard mutation surface.** The dashboard was entirely read-only: no `<form>`, no
  `<button>`, no server actions. Every write told the user to go run a CLI command, which gutted
  the journal in particular — a thesis is worth recording at the moment you have the thought.
  Now `web/lib/actions.ts` exposes a server action per mutating endpoint, with controls on the
  overview, performance, news, insights, and journal pages. Expensive or irreversible actions
  (sync, replay, AI analysis, thesis transition) confirm first; routine writes do not.
- **Replay concurrency.** `POST /api/v1/performance/replay` had no lease, so two concurrent runs
  would duplicate every provider fetch against a metered quota and race to be the last writer.
  Harmless while only a human at a terminal could call it; a prerequisite once a button exists.
  The sync lease was generalised to be endpoint-keyed and replay now takes its own.
- **Inconsistent mutation guard.** `X-Helios-Local-Action` protected the four *expensive*
  endpoints but not the four thesis/journal writes. The right boundary is **mutating vs
  non-mutating**, not expensive vs cheap. All eight are now guarded via a shared
  `require_local_action` dependency, with a test that fails if a mutating route is added without
  one.
- **Environment reproducibility.** Four declared runtime dependencies (`anthropic`, `numpy`,
  `scipy`, `defusedxml`) were absent from the venv, so all 15 test files failed at collection.
  `constraints.txt` pins a known-green set, `.python-version` pins the interpreter, and
  `helios-preflight` fails loudly with the missing names rather than letting the first symptom be
  a collection error deep in a test run.
- **Documentation drift.** The README claimed every news source ships disabled while the shipped
  YAML enables three; `.env.example` omitted 25 real settings. Both corrected.

### Closed in the completion pass (2026-09-26)

Every item below was verified by running it, not just by tests: the settings flow was exercised
against a live API process, and the backend suite was run on both the 3.14 dev venv and a clean
3.12 install from `constraints.txt` (the CI and Docker interpreter).

- **CI.** `.github/workflows/ci.yml` runs backend (preflight, ruff, strict mypy, pytest on 3.12
  with pinned installs), web (eslint, tsc, vitest) and Playwright. Not yet run on GitHub: the
  branch has not been pushed.
- **EUR holdings were never valued (engine bug).** Found by the golden-dataset work. The daily
  replay looked up a EUR->EUR FX series for base-currency holdings; none exists by design, so
  any Euronext/Xetra position read `MISSING_FX` and NAV went `PARTIAL` permanently from the day
  it was bought. Fixed in `_build_daily_replay`; pinned by
  `test_a_base_currency_holding_is_valued_without_fx`. **Existing databases need a replay** to
  pick up the corrected NAV.
- **Frozen reference-dataset regression.** `tests/test_golden_portfolio.py`: a USD + GBP
  portfolio through the real replay/report path, pinned NAV/TWR/XIRR/vol/Sharpe/Sortino/Calmar/
  drawdown, with the pins cross-checked against an independent plain-Decimal re-implementation
  and hand arithmetic.
- **Settings page was dead under Docker Compose.** The web container reaches the API over the
  bridge network, so the loopback guard 403'd every settings request; and writes could never
  have applied anyway (compose's injected environment outranks the container's `.env`, and the
  image has no keyring). Compose now sets `HELIOS_SETTINGS_TRUSTED_PEERS=web` (one resolved
  host, not the network) and `HELIOS_SETTINGS_WRITABLE=false`; the page renders read-only and
  says to edit the host `.env`; writes return 409.
- **Saved settings were discarded on restart (bare metal).** Nothing loaded `.env`, so a value
  the page wrote there never came back. `Settings` now reads `./.env` (environment still wins);
  `HELIOS_DISABLE_DOTENV` keeps a developer's `.env` out of the test suite, like the keyring
  switch. Round-trip test added.
- **Restart killed the API on Windows.** `os.execv` does not quote arguments on Windows, so the
  interpreter path under `Portolio Tracker/` split at the space; and re-running `sys.argv` under
  `python -m uvicorn` shadowed stdlib `logging`. Now built from `sys.orig_argv`, quoted on
  Windows. Verified live: save -> restart -> new value served.
- **Docker image unpinned.** The Dockerfile installed without `constraints.txt`; now pinned.
- **Backup story.** `helios backup` / `make backup`: SQLite online backup API (WAL-safe while
  running), `integrity_check` on the copy, retention that only prunes its own files. Restore is
  documented and deliberately manual. Compose now caps logs (json-file 3 x 10 MB) and sets
  CPU/memory limits.
- **`raw_news` is now read.** `helios news-reparse` replays stored feed bodies through the
  current parser and the live dedupe, offline and idempotently; unattributable rows are counted.
- **UX affordances.** Root `loading.tsx`, `error.tsx` (never renders the error message; shows the
  digest), `not-found.tsx`; URL-driven, bookmarkable pagination on holdings, news and journal.
- **Doc drift.** README: settings page and routes, backups, Docker behaviour, benchmark currency
  defaults (`USD`, not unset), ports. `.env.example` coverage is now enforced by a test.
- **Desktop app (user request: "I hate launching with Docker").** `src/helios/desktop.py`,
  `helios-desktop`, Desktop + Start Menu shortcuts. A tray-resident supervisor runs API, worker
  and the standalone Next server as windowless children in a kill-on-close job object; the
  dashboard opens in Edge app mode with a private profile; the Restart button restarts API and
  worker together (`HELIOS_RESTART_MODE=exit`); crash-looping services are left down with a
  notice; the dashboard is rebuilt only when web sources' content hash changes, into
  `%LOCALAPPDATA%\Helios\web`, isolated from `web/.next`. Verified live: shortcut launch, all
  routes, settings save -> restart -> value live, second launch reuses the instance, killing the
  launcher leaves no orphans.
- **`.env` location was cwd-dependent.** `HELIOS_ENV_FILE` now pins the file both `Settings`
  and the settings page use; found because a non-editable 3.12 install failed a test that
  chdir'd away from the migrations.
- **Edge offered to machine-translate the dashboard** on a French-language system, which would
  rewrite figures and labels in place. The page now declares `translate="no"`.

### Closed in the second pass (2026-09-27)

- **Sector attribution works, from declared inputs.** `src/helios/attribution.py`. Holding
  sectors come from `sector` on `config/instrument_overrides.yaml` entries; benchmark sector
  weights, `as_of`, `source` citation and a priceable sector-ETF proxy per sector come from
  `config/benchmark_sectors.yaml`. Both ship empty, so the default stays `unavailable` (naming
  the two files). >10% unclassified weight, a proxy without prices, or a holding without a
  measurable return -> `insufficient_data` with names; never a zero. Single-period
  approximation with the residual stated. Hand-verified two-sector test.
- **`raw_snapshots` is read.** `helios t212-reparse` (`src/helios/t212_reparse.py`) replays
  stored orders/dividends/transactions/metadata pages through the live parsers and upserts,
  offline and idempotently, under the sync lease. Pagination does not matter because every
  history item has a natural key. Positions/account are live-state and never replayed.
- **Thesis detail page.** `/journal/{id}`: frozen reasoning, lifecycle and allowed transitions,
  enrichment, attached notes, edit (draft only), transition, add-note.
- **Docker verified.** Both images build; the backend installs from `constraints.txt` on Linux
  3.12. The stack was brought up on a scratch data directory (see below).
- **Tray Quit verified** by invoking the real menu item: services stop, ports free, exit 0.
- **Start with Windows**: tray checkbox and `helios-desktop --autostart on|off`; starts in the
  tray without a window. Off by default.
- **constraints.txt** verified on 3.12 and 3.14 (full suite, both green).

### Still open

- **A dynamic page's `notFound()` answers HTTP 200.** The root `loading.tsx` streams a 200
  shell before `/journal/{id}` can decide the thesis does not exist; the not-found *page*
  renders correctly, only the status code is wrong. A known App Router limitation; harmless
  for a single-user local app with no crawlers. Removing the root loading UI would fix it.
- **CI has never run on GitHub** -- the work is uncommitted and unpushed.
- **Things only the operator can do:** credentials, sector declarations, rotating the demo
  key that was exposed during M2 verification. See the README and the hand-off steps.

### Watch out for

- The local-action header is a **local-caller gate, not authentication**. It works because Helios
  binds to loopback and a browser cannot set a custom header cross-origin without a CORS
  preflight the API never grants. If Helios ever stops being loopback-only or gains a second
  user, this must be replaced with real auth and CSRF protection.