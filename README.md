# Helios

Helios is a local-first, read-only portfolio tracker for Trading 212: a Python backend that syncs your positions and computes performance/risk analytics, plus a self-hosted Next.js dashboard on top of it. Everything lives in your own SQLite database, on your own machine.

## Why I built it

Trading 212's app shows you positions and a simple return figure, but nothing like proper time-weighted return, drawdown, factor exposure, or a "you, but if you'd just bought a world index instead" comparison. I wanted those numbers computed correctly from my own trade history rather than approximated, and I wanted a place to record *why* I bought something before I find out whether I was right, since that's the part that's easy to forget in hindsight.

## What it does

- Syncs positions, orders, dividends and cash transactions from Trading 212's read-only API, and stores every raw response before parsing it.
- Replays your full trade history into a daily NAV series and computes time-weighted and money-weighted (XIRR) returns, volatility, Sharpe/Sortino, drawdown, VaR/CVaR, rolling beta, and per-holding contribution.
- Compares your portfolio against configurable ETF benchmark proxies and against the Kenneth French factor library (FF5 + momentum).
- Pulls in free news feeds (Yahoo Finance, SEC filings, Google News, optionally Marketaux) with cross-source deduplication.
- Optionally asks Claude to describe what the computed analytics show, in plain language, with every claim tied to a specific number.
- Splits every period (a day, a week, a month, the year, since you started) into money you moved in or out and what the investments actually made, and splits that result stock by stock, so a deposit never looks like a gain and you can see exactly which holding dropped.
- Gives each holding its own page: price chart with your trades and average cost marked, your position against the money you put in, results by period, dividends, news that actually names the company, and price alerts.
- Reads your 212 Card history from Trading 212's CSV export: spending by day, week and month, by merchant and category, recurring charges, monthly budgets, and cashback counted as income rather than as money you added.
- Lets you follow stocks you don't own on a watchlist: same page as a holding, with daily prices, news and alerts on live quotes.
- Shows how much you deposit each month, how much goes back out by card and how much stays invested, and projects where steady saving could take the portfolio as a range of outcomes from assumptions you set (not a forecast).
- Sends a Windows notification when an alert fires, plus a short summary each evening, and can have Claude write a weekly review of what moved and why it made the news (on request, or weekly if you switch it on).
- Lets you record an investment thesis before you know the outcome, then journal updates and later mark it validated, invalidated or closed.
- Ships a dashboard (overview, holdings, watchlist, performance, card, plan, news, insights, journal, data-quality, settings) that reads the same API as the CLI, and runs as a desktop app with a tray icon.

## How it works

The backend treats Alembic/SQLite as the source of truth and Trading 212's live positions endpoint as reconciliation only, never as a source for history. Instead, the performance engine replays daily holdings and NAV purely from individual `TRADE` fills, one calendar day at a time, so the numbers are reproducible from the ledger rather than trusting a snapshot. Every monetary, quantity and FX value that gets persisted uses a text-backed exact decimal type instead of a float, because compounding rounding errors across years of fills is exactly the kind of bug that's invisible until it matters.

A rule I tried to follow everywhere: never fabricate a number to fill a gap. If a price is missing, that day is marked `STALE_PRICE` or `FORWARD_FILL` rather than silently reusing an old close forever; if an FX fix isn't available, the cash flow is excluded and reported rather than treated as zero-cost; if a benchmark's currency isn't configured, that metric reports `unavailable` instead of guessing. Metrics like factor regression or correlation clustering report `insufficient_data` until there's actually enough history behind them, rather than showing a number computed from too little.

The news pipeline has the same raw-first idea: every fetched response is stored before parsing, so a parser bug can be fixed and replayed against history without re-downloading anything. Combining several feeds for the same story turned out to need more than matching URLs, since the same article shows up under three different links (the aggregator's, the source's, Yahoo's), so articles are deduplicated on a normalized canonical URL and a normalized headline within a time window, and when two sources cover the same story the more trustworthy one wins.

Keeping every raw response is what makes that replay possible, and it was also most of the database, so it is stored delta-compressed rather than thrown away: each download is compressed with zstd against the previous download of the same feed or endpoint, which a new copy of an hourly feed shrinks to a few hundred bytes. Every frame carries a checksum, chains restart from a standalone frame every 32 rows, and nothing is ever deleted to save space. The worker hands free pages back to the disk shortly after start and then daily (incremental auto-vacuum, `VACUUM` when worth it); `helios compact` or Settings → Storage does it on demand. On my own database that took about 103 MB of raw responses down to 1.8 MB, and the file from 114 MB to 13 MB with every row kept.

The AI analysis feature is built to avoid the obvious failure mode of an LLM "advising" trades. The structured output schema literally has no field for a rating, a price target or a buy/sell call, so there's nowhere for one to end up even if the model tried. Every observation it produces is required to cite the specific metric it's based on, and any metric the backend already flagged as unavailable is passed through as unavailable rather than estimated. It only ever runs when you ask for it, since each call costs a small amount of money and I didn't want it quietly running on a schedule. The one exception is the weekly review, and only if you turn it on with `HELIOS_WEEKLY_REVIEW_ENABLED`; it's also told that a headline in the same week as a move is coverage, not a cause.

## Running it

As a desktop app (what I use day to day, no Docker):

```bash
make install                                  # pip install -e ".[dev,desktop]" -c constraints.txt
helios-desktop --install-shortcut             # Desktop + Start Menu shortcuts
```

Then open Helios from the shortcut. It builds the dashboard the first time, starts the API, the worker and the web server, opens the app window and sits in the tray. Keys go in through the Settings page, which checks them before saving. `helios-desktop --autostart on` starts it with Windows.

Or with Docker:

```bash
cp .env.example .env      # add your Trading 212 key and any optional API keys
make dev                  # docker compose up --build
```

- Dashboard: http://127.0.0.1:3001
- API: http://127.0.0.1:8001

Both bind to loopback only; override with `HELIOS_WEB_PORT` / `HELIOS_API_PORT`. To run the backend directly instead of in Docker, `pip install -e ".[dev]"` (Python 3.12+) gives you the `helios` CLI and `helios-worker`; `make test`, `make lint` and `make typecheck` run pytest, ruff and mypy. The dashboard has its own commands under `web/`: `npm run dev`, `npm run lint`, `npm run typecheck`, `npm test` (Vitest), and `npm run test:e2e` (Playwright against a stubbed API, no real credentials needed).

## Accounts and cost

The only account you actually need is your own Trading 212 API key (Settings → API in the T212 app), which is free. Everything else is optional and free-tier by default:

| Account | Unlocks | Cost |
| --- | --- | --- |
| Twelve Data | Daily prices, so performance/risk metrics compute at all | free tier, 800 calls/day (US listings) |
| EODHD | Full daily history for US and international listings (London, Xetra, Euronext...) | paid; free tier is 20 calls/day, one year |
| Alpha Vantage | A free second source for London listings | free tier, 25 calls/day, last ~100 trading days only |
| Anthropic | The Insights page (Claude describing your analytics) | pay-per-use, a small fraction of a cent to a few cents per run |
| Marketaux | Ticker-tagged international news | free tier, 100 req/day |
| OpenFIGI | Faster instrument mapping | free |

ECB FX rates, the Kenneth French factor data, Yahoo Finance and Google News all work with no account. SEC EDGAR filings need one setting rather than an account: the SEC requires a User-Agent naming a real contact, so that feed is skipped until you set `HELIOS_NEWS_SEC_USER_AGENT="Your Name your@email.com"`.

Twelve Data's free tier only covers US exchanges, so London-listed ETFs need a second price source, picked in Settings: Alpha Vantage's free key works but only reaches back about 100 trading days, and days it can't price are left out of returns rather than guessed at; EODHD gets the full history in one call per holding. AI analysis costs work the same way: Claude Opus is the default model for quality, Haiku is the cheap option, and the system prompt is cached so a second run in the same session costs less than the first.

## Not financial advice

The passive-benchmark comparison is a counterfactual built from proxy ETF prices, not an index and not something you could have actually achieved. The AI analysis describes numbers Helios already computed; it never rates, targets or recommends anything. None of this is investment advice, and I built it to be honest about what it doesn't know rather than to look confident.

## Safety and privacy

Trading 212 access is read-only: every request is a GET, with one exception. Card payments only get a merchant name in Trading 212's CSV export, so Helios asks for that report of your own history, at most once a day. That request has a fixed body and can't do anything else, and no trade or account change is ever sent. Trading 212 sends a notification to your phone each time a report is made, which is why it's daily and not more often. Everything Helios computes stays in your own local SQLite database. Nothing is sent anywhere except the specific third-party API you've configured, and only the data that call needs (Trading 212 credentials never leave that one client, for instance). Keep demo and live Trading 212 keys separate, and don't commit secrets into tracked files.

## Limitations and what I'd improve

- Sector-level (Brinson-Fachler) attribution isn't implemented yet: there's no licensed index-constituent feed wired in, so that panel reports `unavailable`.
- Full price history for non-US holdings needs a paid provider (EODHD); the free sources cover the last few months at best.
- The factor regression trails real time by about a month, since the Kenneth French library is only published monthly.
- Card payments made since the last daily export show up as "not labelled yet" until the next export names the merchant; the transactions API alone can't tell a card payment from a bank withdrawal.
- Price alerts use Trading 212's live price for things you hold and the last daily close for anything else, so an alert on a stock you don't own fires on the close, not intraday.
