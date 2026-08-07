# M3 Performance Correctness Fixes

## TL;DR

> **Quick Summary**: The first Milestone 3 implementation pass (delegated, `ses_02c1e0a34ffeN34rCnP7M7VxGF`) builds and passes 92 tests, but parent review found 17 defects: placeholder/fake analytics, wrong financial semantics (non-EUR cash, cache merge, stale prices, drawdown recovery, TWR timing), a missing passive counterfactual, missing 30/90 rolling windows and correlation clustering, and unrelated M2 format-only changes. This plan fixes all of them.
>
> **Deliverables**:
> - Financially correct daily replay, price/FX caches, and TWR/XIRR semantics
> - Real (non-placeholder) analytics: contribution, Brinson, rolling 30/90, passive counterfactual, correlation clustering, VaR/CVaR, FF5+momentum with explicit availability status
> - Reverted M2-only format changes
> - Tests proving each correction
>
> **Estimated Effort**: Large
> **Parallel Execution**: NO - sequential (one focused fix pass over a single module + tests)
> **Critical Path**: performance.py + models.py + migration 0005 + test_performance.py → validation → review

---

## Context

### Original Request
Milestone 3 of Helios: reconstruct daily holdings/NAV from the M2 ledger and compute quant-grade performance/risk analytics. The first delegated implementation pass is incomplete and partially incorrect.

### Interview Summary
**Key Discussions**:
- SQLite/Alembic stays authoritative; M3 is a separate read-model pipeline (never mutate `PortfolioSyncService` semantics).
- Exact text-backed `Decimal` for all financial values; UTC-only datetimes.
- ECB EUR-base FX (`D.<CCY>.EUR.SP00.A`, CCY per EUR); forward-fill last known fix on non-publication days with explicit staleness.
- Benchmarks are labeled ETF proxies (CSPX/SWDA/VWRP), never official index levels.
- No paid market-data credential present → market data must be optional/configurable and deterministic under fixtures; never fabricate or interpolate.
- XIRR via bracketed root solver; explicit insufficient-data results, never misleading numbers.

**Research Findings**:
- `bg_817eab05` (ledger map): replay must be a NEW service; `transactions` lost ticker/ISIN; `positions_live` is snapshot-only; don't overload `SyncStatus`.
- `bg_5930aa65` (providers): no clean free provider; Twelve Data best paid default; yfinance unofficial; keep provider abstraction.
- `bg_485540bc` (FX/benchmarks): ECB EXR protocol; ETF proxies CSPX/SWDA/VWRP with inception-date limits.
- `bg_5d8a6ac8` (quant methodology): GIPS TWR, flow timing must be labeled, XIRR bracketed, explicit annualization basis/ddof/NaN policies, Brinson-Fachler preferred.
- `bg_37e2e0a4` (testing): no conftest/fixtures, no calendar abstraction, Hypothesis present but unused.

### Metis Review
Metis aborted in an earlier session; replaced by the five specialist discovery tracks above.

---

## Work Objectives

### Core Objective
Make Milestone 3 financially correct and non-deceptive: every reported metric must come from real persisted inputs or an explicit unavailable/insufficient status.

### Concrete Deliverables
- Corrected `src/helios/performance.py` (replay, caches, analytics)
- `src/helios/models.py` + migration `0005` additions if new tables needed (passive series, factor cache, correlation clustering output storage)
- `src/helios/schemas.py` + `api.py`/`cli.py` contract updates (rolling 30/90, clustering, passive)
- `tests/test_performance.py` + related test updates proving each fix
- Reverted format-only diffs in `portfolio_sync.py`, `resolver.py`, `test_client.py`, `test_resolver.py`

### Definition of Done
- [x] `pytest -q` green with new correction tests (target ≥ 100 tests)
- [x] `ruff check src tests` clean
- [x] strict `mypy` clean
- [x] `lsp_diagnostics` clean on changed files
- [x] No `as any`/`@ts-ignore`/empty catches (N/A Python), no naive datetimes, no float persisted values
- [x] No placeholder metrics presented as real (grep for `([], [])` calls and zero-return stubs)

### Must Have
- Non-EUR cash flows either FX-converted at the flow date or excluded with an explicit note; never added as EUR.
- Weekend/market-holiday price carry-forward with `FORWARD_FILL` provenance and a bounded stale-price cutoff (configurable max stale days).
- Cache merge that preserves cached observations and the true provider symbol; `_missing_price_requests` keyed on market-observation coverage, not calendar-day counts.
- Drawdown recovery compared against the peak of the worst drawdown.
- TWR with explicit cash-flow timing (`flow_at_open` / `flow_at_close` / `intraday_split`) and a corrected test (deposit-only day return must be 0 under close convention when deposit is at close... choose one and label it).
- Alpha Vantage currency must come from trusted instrument/provider metadata or the provider stays disabled; never hardcode USD.
- Dividend not double-counted across dividends and transactions ledgers.
- `you-but-passive` counterfactual: actual external contributions invested in configured proxy at flow dates.
- Rolling beta and volatility for BOTH 30-day and 90-day windows.
- Correlation clustering in the report contract.
- Contribution from real per-holding period returns, not forced zeros.
- Brinson-Fachler includes benchmark-only sectors; total effect reconciles to active return.
- VaR/CVaR: actual sample-size reporting when insufficient; labeled quantile/sign conventions.
- FF5+momentum regression on excess returns; if live factor ingestion is unavailable, explicit `unavailable` status (pure function stays fully tested).
- Validate all new settings; base currency constrained to EUR; never `date.today()` as clock.

### Must NOT Have (Guardrails)
- No fabricated prices, returns, currencies, or interpolated values.
- No weakening or deleting existing passing tests.
- No change to accepted M2 ingestion semantics (`PortfolioSyncService`).
- No official S&P/MSCI/FTSE index levels without license.
- No yfinance/Yahoo/Stooq as canonical mandatory dependency.
- No format-only edits to `portfolio_sync.py`, `resolver.py`, `test_client.py`, `test_resolver.py` (revert unless a fix truly requires it).
- No commit/push; no `.env` reads; no secret exposure.

---

## Verification Strategy (MANDATORY)

### Test Decision
- **Infrastructure exists**: YES (pytest + Hypothesis, strict mypy, Ruff)
- **Automated tests**: TDD-style - fix each defect with a regression test first or in the same change
- **Framework**: pytest + Hypothesis (already in dev deps)

### QA Policy
Every fix verified by running tests + targeted REPL checks; evidence captured to `.sisyphus/evidence/`.

---

## Execution Strategy

Sequential single-wave fix pass (one executor, one module + tests), because all defects interlock in `performance.py`.

```
Wave 1 (single executor - sequential fix pass):
├── Fix 1: TWR flow-timing contract + corrected test
├── Fix 2: non-EUR cash / dividend double-count
├── Fix 3: weekend price carry-forward + stale-price cutoff
├── Fix 4: cache merge + _missing_price_requests + provider symbol
├── Fix 5: drawdown recovery (verify current state; re-apply if needed)
├── Fix 6: Alpha Vantage currency handling
├── Fix 7: contribution from real returns
├── Fix 8: Brinson benchmark-only sectors + reconciliation
├── Fix 9: rolling 30/90 beta + volatility
├── Fix 10: you-but-passive counterfactual
├── Fix 11: correlation clustering
├── Fix 12: VaR/CVaR sample-size + conventions
├── Fix 13: FF5+momentum excess returns + availability status
├── Fix 14: config validation + base-currency constraint
├── Fix 15: revert M2 format-only changes
└── Fix 16: schema/api/cli contract updates + tests

Wave FINAL (reviews):
├── F1: Plan compliance audit (oracle)
├── F2: Code quality review (unspecified-high)
├── F3: Real manual QA - execute all new QA scenarios (unspecified-high)
└── F4: Scope fidelity check (deep)
```

Critical Path: Fix 1 → Fix 3 → Fix 7 → Fix 9 → Fix 10 → Fix 16 → F1-F4 → user okay

---

## Execution record (2026-08-06)

All 17 TODOs applied. `pytest -q` 130 passed, `ruff check src tests` clean, strict `mypy` clean.
Evidence: `.sisyphus/evidence/m3-end-to-end-smoke.txt` (+ the runnable `m3_smoke.py`).

**Deviations from the written spec, and why:**

1. **TODO 2 — `intraday_split` is not flow-neutral.** The spec asked for all conventions to be
   flow-neutral. Modified Dietz half-weights the flow by construction, so a deposit-only day
   returns a non-zero number under it. Rather than fake neutrality, `flow_at_open` and
   `flow_at_close` are asserted exactly flow-neutral and `intraday_split` is documented and
   tested as the labelled Modified Dietz approximation.
2. **TODO 7 — per-holding returns use EUR *unit* price, not `market_value_eur`.** The spec said
   "percent change of market_value_eur"; that is wrong on any day a holding is bought or sold,
   because the quantity change would be reported as a return. The implementation divides by
   quantity first (`market_value_eur / quantity`), which equals the value change when quantity is
   constant and stays correct when it is not.
3. **Annualisation basis is 365, not 252.** The replay emits one NAV row per *calendar* day, so a
   252 trading-day basis would overstate every annualised figure. `ANNUALIZATION_DAYS = 365` is a
   module constant, surfaced in the report as `annualizationDays` and in each metric's `detail`.
4. **Attribution is `unavailable`, not an empty list.** Brinson-Fachler needs benchmark
   constituent weights; no licensed source is configured. The pure function is fully implemented
   and tested (including benchmark-only sectors and active-return reconciliation).
5. **Extra fix not in the spec: trade cash FX.** `wallet_net_value` was being multiplied by
   `wallet_fx_rate`. That rate is instrument-currency -> wallet-currency; `netValue` is already in
   wallet currency. Trade cash is now converted from `wallet_currency` to EUR via the ECB map at
   the trade date, or excluded with a note.
6. **Extra disclosure: flat-day dampening.** The calendar-day series contains forward-filled
   non-trading days, which pushed the 95% 1-day VaR to exactly 0.00 in the smoke run. Not
   fabrication, but misleading if unlabelled, so every VaR/CVaR `detail` now reports the
   observation count, tail count, and flat-observation share, and the report carries a note.
7. **`ruff format --check` still flags the 4 reverted M2 files.** They were not format-clean at
   the accepted M2 commit; that is exactly why the format-only diffs existed. TODO 15 requires
   reverting them, so this is pre-existing and deliberately left alone.

## TODOs

- [x] 1. Verify current state of `compute_drawdown` (an earlier edit may or may not have been applied). Ensure recovery uses the worst-drawdown peak; add regression test: peak → trough → partial recovery → new lower peak → full recovery only counted at the correct peak.

- [x] 2. Define `analytics_flow_timing: Literal["flow_at_open", "flow_at_close", "intraday_split"]` setting (default `flow_at_close`). Fix `compute_daily_twr` formula to match the convention and update `test_daily_twr_is_flow_neutral_for_midstream_deposit` (the asserted 0.0909090909 for a deposit-only doubling day is wrong under close/start conventions; pick one and assert the correct neutral return).

- [x] 3. In `_build_daily_replay`: non-EUR external flows must be FX-converted at the flow date using `fx_rates` or excluded with an explicit note (never added as EUR). Guard against dividend double-counting between `dividends` ledger and `transactions` dividend rows.

- [x] 4. Add `analytics_max_price_stale_days: int` setting (default e.g. 10). In daily valuation, carry forward the last close with `FORWARD_FILL` provenance; if the last known close is older than the cutoff, mark `STALE_PRICE`/`MISSING_PRICE` instead of valuing. Tests for weekend gap and cutoff boundary.

- [x] 5. Rewrite `_missing_price_requests` to decide coverage by market-observation boundaries (max cached `price_date` vs `end_date` and min vs `start_date`), not `(end-start).days+1` row counts. Rewrite `_merge_market_price_maps`/`_merge_fx_maps` to merge cached + fetched per date, keep the true `provider_symbol`, and never drop cached points.

- [x] 6. Alpha Vantage: build the request contract with a trusted currency from instrument metadata; if the provider cannot know the currency safely, return `{}` (disabled behavior) and log/note. Never hardcode USD. Keep provider optional (`market_data_provider: "disabled"` default).

- [x] 7. `_latest_returns` must compute real per-holding period returns from consecutive valued holding prices/FX (percent change of market_value_eur over the last two valued days), not `0.0` stubs. Add a test where a holding's price moves and the contribution reflects it.

- [x] 8. `brinson_fachler_attribution`: include benchmark-only sectors as rows with allocation effect (portfolio weight 0), and add a test asserting sum of total effects equals the active return to tolerance.

- [x] 9. Replace the single `analytics_rolling_window_days` rolling series with both 30-day and 90-day rolling beta and volatility in the report/schema (`rolling_volatility_30d`, `rolling_volatility_90d`, `rolling_beta_30d`, `rolling_beta_90d`), keeping the pure rolling functions parameterized.

- [x] 10. Implement `you-but-passive` counterfactual: starting from first external flow date, invest each external contribution into the configured passive proxy symbol at the flow date (or next available price), holding through `end_date`; label it clearly as a counterfactual using ETF proxy data. Persist series in `daily_nav`-style read model or compute on demand; add test with known flows and known proxy prices.

- [x] 11. Add correlation clustering metric: build correlation matrix from aligned per-holding return series, convert to distance, cluster (scipy linkage), and expose cluster assignments/weights in the report schema. Insufficient-data → explicit status. Test on a toy 2-3 holding set.

- [x] 12. VaR/CVaR: report actual observations count when insufficient; document and test quantile method (`linear`) and loss sign convention; 10d uses overlapping compounded returns.

- [x] 13. FF5+momentum regression: use excess portfolio returns (subtract risk-free); add a `FactorDataProvider` protocol + optional factor cache/ingestion; if no factor data available, `get_report` returns `unavailable` status (not fabricated). Keep `ff5_momentum_regression` pure and tested (R², coefficient shapes).

- [x] 14. Settings: validate `market_data_provider` enum, `analytics_flow_timing` literal, positive `analytics_max_price_stale_days`, base currency must be `EUR` for this implementation (raise otherwise). Remove/replace `Settings.today` property (use injected clock).

- [x] 15. Revert format-only diffs in `portfolio_sync.py`, `resolver.py`, `test_client.py`, `test_resolver.py` to the accepted M2 state unless a fix truly requires touching them.

- [x] 16. Update `schemas.py`/`api.py`/`cli.py`/`dependencies.py` and `test_performance.py` for all contract changes (rolling 30/90, clustering, passive, flow timing, stale policy). Add regression tests for each Fix 1-15. Update README M3 section (flow timing default, stale cutoff, passive counterfactual, provider disabled default, proxy disclosure).

- [x] 17. Run full validation: `pytest -q`, `ruff check src tests`, strict `mypy`, `lsp_diagnostics` on changed files. Fix only issues caused by this work.

---

## Final Verification Wave

- [ ] F1. **Plan Compliance Audit** — `oracle`: verify each Must Have implemented; grep for placeholder patterns (`([], [])`, zero-return stubs); evidence files exist.
- [ ] F2. **Code Quality Review** — `unspecified-high`: build/lint/tests; check for fabrication, empty catches, `date.today()`, naive datetimes.
- [ ] F3. **Real Manual QA** — `unspecified-high`: execute every new QA scenario from clean state; edge cases (weekend gap, stale cutoff, non-EUR flow, deposit-only TWR day).
- [ ] F4. **Scope Fidelity Check** — `deep`: every fix 1:1 against this plan; M2 files clean; no unaccounted changes.

## Commit Strategy
- Deferred until user approves plan execution. Then: multiple atomic commits (fix-per-concern), semantic style per repo history, Sisyphus attribution footer.

## Success Criteria
- All 17 TODOs done with passing regression tests.
- 100% of placeholder analytics removed or explicit unavailable.
- M2 diffs reverted to accepted state.
- Full validation green.
- User explicitly approves before any commit/push.
