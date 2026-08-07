# Helios

Local-first, read-only Trading 212 portfolio intelligence: backend sync/runtime/performance
foundation plus a self-hosted dashboard.

## Running the stack

```bash
cp .env.example .env      # fill in credentials; nothing is committed
make dev                  # docker compose up --build
```

- Dashboard: <http://127.0.0.1:3001>
- API: <http://127.0.0.1:8001>

Both ports bind to loopback only. Override with `HELIOS_WEB_PORT` / `HELIOS_API_PORT`.

## Accounts you need

**Helios runs end-to-end with zero accounts.** Everything below is optional; each one unlocks a
feature, and anything unconfigured reports itself as unavailable rather than degrading silently.

| # | Account | Cost | Unlocks | Without it |
| --- | --- | --- | --- | --- |
| 1 | **Trading 212** API key (Settings → API) | free | Everything — your own portfolio data | Nothing works; this is the one that matters |
| 2 | **Anthropic** — <https://console.anthropic.com> | pay per use, ~$0.02–0.10/run | AI analysis (Insights page) | Insights reports "unavailable"; the rest is unaffected |
| 3 | **Marketaux** — <https://www.marketaux.com> | free tier, 100 req/day | Ticker-tagged international news + sentiment | That one source is skipped; other news sources still work |
| 4 | **OpenFIGI** — <https://www.openfigi.com/api> | free | Higher instrument-mapping rate limits | Mapping still works, just slower |
| 5 | **Alpha Vantage** — <https://www.alphavantage.co> | free tier unusable, $49.99+/mo | Live market prices for M3 analytics | Benchmarks and price-dependent metrics report `unavailable` |

**No account needed** for: Yahoo Finance news, Google News, SEC EDGAR (set
`HELIOS_NEWS_SEC_USER_AGENT` to a real contact — the SEC's policy requires it, not an account),
ECB foreign exchange, or your own publisher RSS feeds.

### What AI actually costs

A portfolio analysis is roughly 4k input / 1k output tokens. Analysis only runs when you ask —
it is deliberately **not** on the worker schedule.

| Model | Per run | Daily use |
| --- | --- | --- |
| `claude-haiku-4-5` | ~$0.01 | ~$0.30/mo |
| `claude-sonnet-5` | ~$0.03 | ~$0.90/mo |
| `claude-opus-5` (default) | ~$0.05 | ~$1.50/mo |

The default is Opus 5 for quality; set `HELIOS_ANTHROPIC_MODEL` to trade down. The system prompt
is cached, so repeat runs cost less than the first.

## Commands

- `helios positions`
- `helios sync [--force-metadata]`
- `helios quality`
- `helios performance-replay`
- `helios performance-report`
- `helios news-sync`
- `helios news [--ticker T] [--isin I] [--limit N]`
- `helios ai-analyse` · `helios ai-latest`
- `helios thesis list|create|show|transition` · `helios journal add|list`
- `helios-worker`

## API

- `GET /health`
- `GET /api/v1/t212/positions`
- `POST /api/v1/portfolio/sync?force_metadata=false`
- `GET /api/v1/portfolio/data-quality`
- `POST /api/v1/performance/replay`
- `GET /api/v1/performance/report`
- `POST /api/v1/news/sync`
- `GET /api/v1/news?ticker=&isin=&limit=`
- `POST /api/v1/ai/analyse` · `GET /api/v1/ai/latest`
- `GET|POST /api/v1/theses` · `GET|PATCH /api/v1/theses/{id}` · `POST /api/v1/theses/{id}/transition`
- `GET|POST /api/v1/journal`

Example sync request:

```bash
curl -X POST \
  -H "X-Helios-Local-Action: sync" \
  "http://127.0.0.1:8000/api/v1/portfolio/sync?force_metadata=false"

curl -X POST \
  -H "X-Helios-Local-Action: replay" \
  "http://127.0.0.1:8000/api/v1/performance/replay"
```

## Performance / analytics

- Alembic/SQLite stay authoritative.
- Milestone 3 replays daily holdings and NAV from per-fill `TRADE` rows only.
- `positions_live` remains reconciliation-only; it is never used as historical NAV.
- Exact persisted monetary/quantity/FX fields use text-backed `ExactDecimal` storage.

### Conventions

| Setting | Default | Meaning |
| --- | --- | --- |
| `HELIOS_BASE_CURRENCY` | `EUR` | Only `EUR` is supported; anything else is rejected at startup. |
| `HELIOS_ANALYTICS_FLOW_TIMING` | `flow_at_close` | Cash-flow timing for daily TWR. `flow_at_open`, `flow_at_close`, or `intraday_split` (Modified Dietz half-day weight). The first two are exactly flow-neutral — a pure deposit moves NAV without moving return. `intraday_split` is a deliberate approximation: half-weighting the flow means a deposit-only day shows a small non-zero return. Pick it only if you want Modified Dietz semantics. |
| `HELIOS_ANALYTICS_MAX_PRICE_STALE_DAYS` | `10` | How far a close may be carried forward across weekends/holidays before the day is marked `STALE_PRICE` instead of valued. |
| `HELIOS_ANALYTICS_MAX_FX_STALE_DAYS` | `10` | Same cutoff for ECB FX fixes (`STALE_FX`). |
| `HELIOS_ANALYTICS_PASSIVE_BENCHMARK_KEY` | `vwrp` | Proxy used for the "you, but passive" counterfactual. |
| `HELIOS_MARKET_DATA_PROVIDER` | `disabled` | `disabled` or `alphavantage`. |
| `HELIOS_FACTOR_DATA_PROVIDER` | `disabled` | No licensed FF5+momentum feed ships with Helios. |

- Annualisation uses a **365 calendar-day** basis, because the replay emits one NAV observation per calendar day (not per trading day).
- Forward-filled valuations carry `FORWARD_FILL` provenance on both the price and FX columns, and the day's status becomes `FORWARD_FILL` rather than `VALUED`.
- Non-EUR cash flows (deposits, withdrawals, trade wallet impact, dividends without `amountInEuro`) are converted at the flow date's ECB fix. If no fix is available inside the stale cutoff, the flow is **excluded** and reported in `excludedFlowCurrencies` — it is never added as if it were EUR.
- Dividend cash comes from the dividends ledger only; dividend-typed cash transactions are skipped so the same payment is not counted twice.
- Live market data is optional. Without `HELIOS_MARKET_DATA_PROVIDER=alphavantage` plus `HELIOS_MARKET_DATA_API_KEY`, replay/report surfaces still start, but missing prices remain explicit instead of fabricated.

### Benchmarks and unavailable analytics

Benchmarks are configurable ETF proxies, not official index levels:

- CSPX = S&P 500 ETF proxy
- SWDA = MSCI World ETF proxy
- VWRP = FTSE All-World ETF proxy

Each proxy's quotation currency (`HELIOS_BENCHMARK_CSPX_CURRENCY`, `..._SWDA_...`, `..._VWRP_...`) is **unset by default**. Helios never guesses a listing currency: a price request without a trusted currency is skipped, and the affected benchmark, passive counterfactual, and beta figures report an explicit `unavailable` status instead of a number.

Metrics that report a status rather than a value when their inputs are absent:

- `attribution` — Brinson-Fachler needs benchmark constituent weights and sector returns; no licensed constituent source is configured, so the report is `unavailable`.
- `ff5MomentumRegression` — `unavailable` while `HELIOS_FACTOR_DATA_PROVIDER=disabled`; the regression itself runs on *excess* returns (return minus the risk-free rate).
- `correlationClusters` — `insufficient_data` until enough aligned per-holding observations exist.
- `passiveCounterfactual` — a counterfactual built from proxy prices; not an index, not achievable, not advice.
- `contributions` — a holding without two consecutive valued observations is reported as `insufficient_data`, never as a 0% contribution.
- VaR/CVaR — historical simulation; losses are negative returns, quantiles use `linear` interpolation, and the actual observation count is always reported.

> **Reading the risk numbers.** Volatility, Sharpe/Sortino, and VaR/CVaR are computed on the calendar-day NAV series, which includes weekends and market holidays valued from a carried-forward close. Those flat days dampen volatility and can pull a 95% VaR toward zero. Each VaR/CVaR metric's `detail` reports the observation count, the tail count, and the share of flat observations so the effect is visible rather than hidden.

## News

Helios combines several free sources. Every one is **disabled by default** in
`config/news_feeds.yaml`; enable what you are entitled to use.

| Source | Account | Coverage | Trust |
| --- | --- | --- | --- |
| **Yahoo Finance** | none | Per-holding headlines, incl. European listings | 40 |
| **SEC EDGAR** | none (needs a contact User-Agent) | US filings — the event itself, not commentary | 100 |
| **Marketaux** | free tier | Ticker-tagged international news + sentiment | 60 |
| **Google News** | none | Broad fallback; median item age ~6.6 days, so backfill only | 20 |
| **Your publisher feeds** | per publisher | Whatever you choose | 80 |

### Why combining sources needs more than a URL check

The obvious dedupe key — `(url, published_at)` — breaks as soon as two sources cover the same
story: Yahoo links to `finance.yahoo.com`, Marketaux links to the publisher, and the publisher
feed links to its own canonical page. Three URLs, one article. So Helios dedupes on two keys:

1. **Canonical URL** — tracking parameters (`utm_*`, `fbclid`, …) stripped, host normalised.
2. **Title key** — normalised headline, matched within `HELIOS_NEWS_DEDUPE_WINDOW_HOURS`.

When a duplicate is found the **higher-trust** copy wins, so a primary SEC filing outranks the
aggregator commentary quoting it. Both keys are checked against already-stored items too, so a
source added later does not re-import history you already have.

### Template placeholders

`url_template` is filled from data Helios already holds — never inferred:

| Placeholder | Source |
| --- | --- |
| `{ticker}` | Trading 212 ticker (`AAPL_US_EQ`) |
| `{yahoo_ticker}` | Resolved market symbol from the M2 OpenFIGI mapping (`AAPL`) |
| `{isin}` | ISIN |
| `{name}` | Instrument name, URL-encoded |

An instrument missing a field its template needs is skipped with a stated reason.

### Rules the pipeline enforces

- **Feeds and documented APIs only, never scraping.** The article link is never followed and
  article bodies are never fetched or stored, so paywalled text is never copied.
- **Raw-first.** The response body lands in `raw_news` before parsing, so a parser fix is
  replayable against history.
- **Linkage is declared, never guessed.** No headline scanning for company names.
- **Hardened parsing.** `defusedxml` for untrusted feeds (a billion-laughs payload is rejected);
  http/https only; body size capped. The Marketaux key is attached at fetch time so it never
  reaches the stored raw URL.
- **One bad publisher cannot break a sync.** Failures are collected per source and reported.

## AI analysis (Claude)

The Insights page asks Claude to **describe** analytics Helios already computed. It is not
investment advice, and the design makes that structural rather than aspirational:

- **The output schema has no field for advice.** No rating, no price target, no buy/sell action,
  and `additionalProperties: false` — so there is nowhere for one to go, even if asked.
- **Every observation cites its evidence.** `evidence` is a required schema field naming the
  figure the claim rests on. An observation without one is dropped, not softened.
- **Unknowns stay unknown.** Metrics M3 reported as `insufficient_data` or `unavailable` are
  passed through with that status, so the model says so rather than estimating.
- **Raw-first.** Prompt and response are stored in `ai_runs`, so any published statement traces
  back to the numbers behind it.
- **Never on a schedule.** Each run costs money, so it runs only when you ask.

Uses adaptive thinking, structured outputs, prompt caching on the system prompt, and server-side
refusal fallbacks (Opus 5's classifiers can decline; the request re-runs on the fallback model
rather than surfacing a refusal).

## Thesis and journal

Record *why* you hold something, before the outcome is known.

```bash
helios thesis create --title "Services compound" --body "..." --ticker AAPL_US_EQ --conviction high
helios thesis transition 1 --to active
helios journal add --note "Added on the pullback" --thesis-id 1
helios thesis transition 1 --to validated --note "Services grew as expected"
```

**A thesis is editable only while it is a draft.** Once activated, the original reasoning is
frozen — later thinking goes in the journal, and the outcome goes in the note you must write to
close it. Editing a thesis to match what happened would destroy the only thing it is for.

Status flow: `draft → active → validated | invalidated → closed`. `closed` is terminal, and a
draft cannot jump straight to an outcome without having been live.

## Dashboard

A Next.js app in `web/`, rendering four sections:

| Route | Shows |
| --- | --- |
| `/` | NAV hero figure, headline return/risk metrics, NAV chart, largest holdings, stack health |
| `/holdings` | Live Trading 212 positions with exact fractional quantities |
| `/performance` | Every M3 analytic: TWR/XIRR, NAV and drawdown charts, rolling vol/beta 30-90d, VaR/CVaR, contribution, benchmarks, concentration, correlation clusters, factor exposure |
| `/news` | Configured-feed articles, filterable by holding, each linking to its publisher |
| `/insights` | Claude's description of your analytics, every observation citing its evidence |
| `/journal` | Theses and dated notes, open vs settled |
| `/data-quality` | Endpoint sync health, instrument mapping issues, reconciliation mismatches |

Design and safety rules the UI holds to:

- **The browser never talks to the API.** Every read happens in a React Server Component, so
  `HELIOS_API_URL` and every backend credential stay server-side. `lib/api.ts` imports
  `server-only`, which makes an accidental client import a build error. An E2E test asserts no
  browser request ever reaches the API origin.
- **A missing value renders as `—`, never as `0`.** Metrics the backend flagged
  `insufficient_data` or `unavailable` show that status and its reason, so M3's honesty about
  what it cannot compute survives all the way to the screen.
- **Every chart has a table-view twin**, so no value is reachable only by hovering.
- **Status is never carried by color alone** — each badge pairs a glyph with a text label.
- Chart colors come from a palette validated against this app's own surface for colorblind
  separation and contrast (`web/lib/viz.ts` records the command and its result). The brand amber
  is deliberately not a series color: it fails the lightness band a data mark needs here.
- Decimal strings from the API stay strings until the render boundary, so exact backend values
  are never round-tripped through a float.

### Web commands

```bash
cd web
npm run dev          # http://127.0.0.1:3000 against HELIOS_API_URL
npm run lint
npm run typecheck
npm test             # vitest: parsers, series shaping, component render
npm run test:e2e     # playwright against a stubbed API, no credentials needed
```

`npx playwright test screenshots` writes full-page captures to `.sisyphus/evidence/m4/`.

## Safety

- Trading 212 access is read-only and GET-only.
- No trades or account mutations are performed.
- OpenFIGI use is optional and isolated from Trading 212 credentials.
- Trading 212 cash transactions are not replayed into quantities; quantity-changing non-TRADE fills remain `UNSUPPORTED_ACTION`.
- Market data is optional/configurable; Helios does not silently invent missing prices, FX, or benchmark levels.

## Credentials

- Keep demo and live Trading 212 credentials separate.
- Do not place secret values in tracked files.
- Local bindings stay on localhost; users often override to `3001/8001` via env.

## Instrument overrides

Use `config/instrument_overrides.yaml` for verified manual mappings only.

- Prefer snake_case fields: `yahoo_ticker`, `preferred_exchange`, `quote_currency`
- Existing camelCase aliases are still accepted
- Never guess Yahoo/LSE mappings

## Docker Compose

`compose.yaml` mounts `/app/config`, keeps API and worker on shared backend image, and
makes the worker wait for a healthy API to reduce first-start migration contention.
The worker still migrates on startup so standalone worker runs remain safe.
