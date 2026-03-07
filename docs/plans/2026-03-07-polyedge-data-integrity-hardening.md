# PolyEdge Dashboard/Data Integrity Hardening (2026-03-07)

## Objective
Implement and verify structural fixes for dashboard accuracy and trading-data integrity, with explicit evidence and code references.

## Scope Completed

### 1) Latest prediction selection fixed (chronological, not lexical ID)
- Problem: LLM/combined traders selected "latest" prediction using `max(Prediction.id)`, which is unsafe for UUID-like IDs.
- Fix:
  - `run_llm_paper_trading` now ranks by `Prediction.created_at DESC, Prediction.id DESC` via window function.
  - `run_combined_paper_trading` uses the same ranking logic.
- Code:
  - `polyedge/src/polyedge/scheduler.py:405-408`
  - `polyedge/src/polyedge/scheduler.py:566-568`
- Why: Guarantees temporal recency, avoids stale or random row selection.

### 2) Strategy universe consistency (noise-market policy aligned)
- Problem: LLM path could include crypto "up or down" noise while ngram/combined excluded it.
- Fix:
  - Added `_is_noise_market()` helper for category + question fallback.
  - Applied in all strategy loops.
- Code:
  - `polyedge/src/polyedge/scheduler.py:59-64`
  - `polyedge/src/polyedge/scheduler.py:239`
  - `polyedge/src/polyedge/scheduler.py:446`
  - `polyedge/src/polyedge/scheduler.py:597`
- Why: Source comparison requires same market universe.

### 3) Duplicate-open-trade hardening (DB + runtime)
- Problem: Duplicate prevention was app-side only.
- Fix:
  - Added migration to dedupe unresolved duplicates and enforce partial unique index:
    - `ux_open_trade_market_side_source` on `(market_id, side, trade_source)` where `resolved = FALSE`.
  - Added hot-path indexes for predictions/paper_trades/markets.
  - Added `_safe_commit()` wrapper to gracefully handle integrity conflicts.
- Code:
  - `polyedge/deploy/migrations/005_trade_integrity_and_perf.sql:3-41`
  - `polyedge/src/polyedge/scheduler.py:68-77`
  - `polyedge/src/polyedge/scheduler.py:658`
- Why: Prevents accidental double-open positions under concurrent/restarted loops.

### 4) Void/non-binary resolution handling in paper-trade scoring
- Problem: Non-binary outcomes could be treated as losses.
- Fix:
  - `score_paper_trades` now resolves YES/NO normally.
  - Non-binary outcomes (e.g. CANCELLED/N/A) are marked resolved with `won=None`, `pnl=0.0`.
- Code:
  - `polyedge/src/polyedge/scheduler.py:329-337`
- Why: Avoids false loss attribution when market resolves to non-tradable outcome.

### 5) Dashboard source stats made comparable and explicit
- Problem: by-source stats mixed incompatible cohorts and lacked explicit market-vs-trade counts.
- Fix:
  - `by_source` now uses canonical real-trade predicates and joins `markets` for consistent filtering.
  - Added per-source `unique_markets_open` and `unique_markets_closed`.
  - Added explicit response objects for `trade_counts` and `unique_market_counts`.
- Code:
  - `polyedge/src/polyedge/app.py:1144-1182`
  - `polyedge/src/polyedge/app.py:1327-1333`
- Why: Prevents misinterpretation and clarifies overlap/duplication semantics.

### 6) Noise filter upgraded from text-only to category-aware
- Fix:
  - `noise_market_predicate()` now checks `market_category == "crypto_updown"` with question fallback.
- Code:
  - `polyedge/src/polyedge/query_filters.py:15-16`
- Why: Category-based filtering is more stable/performant than brittle substring-only logic.

### 7) Prediction scoring filter made configurable and stricter
- Fix:
  - Added env-driven source parsing: `prediction_resolution_sources`.
  - Scoring now only processes explicit binary resolutions (`YES`/`NO`).
- Code:
  - `polyedge/src/polyedge/analysis/scorer.py:52-62`
  - `polyedge/src/polyedge/analysis/scorer.py:75-78`
  - `polyedge/src/polyedge/db.py:10`
  - `polyedge/.env.example:5`
- Why: Avoids contamination from ambiguous outcomes and supports controlled source policy.

### 8) PnL sign rendering fix + clearer strategy table
- Problem: Hero card used absolute value formatting and could hide negative sign.
- Fix:
  - PnL now renders directly (`ptPnl.toFixed(2)`), preserving sign.
  - Strategy table now shows trade counts and market counts side-by-side.
- Code:
  - `polyedge/src/polyedge/static/dashboard.html:480`
  - `polyedge/src/polyedge/static/dashboard.html:557`
- Why: Prevents misleading UI in loss periods and reduces count confusion.

## Tests Added/Updated (TDD)
- `polyedge/tests/test_scheduler.py`
  - latest prediction ordering by `created_at` (no `max(id)`)
- `polyedge/tests/test_scheduler_remediation.py`
  - void outcome scoring behavior
  - llm noise-market exclusion
- `polyedge/tests/test_query_filters.py`
  - category-aware noise predicate present in compiled SQL
- `polyedge/tests/test_dashboard_static.py`
  - asserts no `Math.abs(ptPnl)` in hero PnL display
- `polyedge/tests/test_scorer.py`
  - resolution-source parser tests
- `polyedge/tests/test_db.py`
  - env read test for `POLYEDGE_PREDICTION_RESOLUTION_SOURCES`

## Verification Evidence

### Local test suite
- Command: `cd polyedge && pytest -q tests`
- Result: `510 passed, 16 warnings`
- Notes: warnings are pre-existing deprecation warnings in rule-generator datetime usage.

### Live API/dashboard manual review (eyes-on)
- Deployment target: `89.167.99.187`
- Files deployed:
  - `app.py`, `query_filters.py`, `dashboard.html`, `scorer.py`, `db.py`, `scheduler.py`, migration `005_trade_integrity_and_perf.sql`
- Migration applied:
  - `DELETE 0`, indexes created successfully (7 create-index statements)

#### Live payload audit snapshot
- Endpoint: `http://89.167.99.187:8090/api/human-dashboard`
- Snapshot at: `2026-03-07T07:33:44.452073Z`
- Key values reviewed:
  - `open_count/closed_count`: `24504 / 1633`
  - `unique_open/unique_closed`: `24147 / 1519`
  - `trade_counts`: `{open:24504, closed:1633}`
  - `unique_market_counts`: `{open:24147, closed:1519}`
  - `by_source.ngram`: `open 22242, open_mkts 22173, closed 571, closed_mkts 571`
  - `by_source.llm`: `open 308, open_mkts 308, closed 0, closed_mkts 0`
  - `by_source.combined`: `open 30, open_mkts 30, closed 0, closed_mkts 0`
- Consistency checks run:
  - `trade_counts` == scalar counts
  - `unique_market_counts` == scalar unique counts
  - `hit_rate.paper_trade_scored == paper_trades.closed_count`
  - per-source `unique <= trade_count`
- Result: no consistency errors.

#### Live HTML audit snapshot
- Endpoint: `http://89.167.99.187:8090/`
- Checks:
  - `Math.abs(ptPnl)` absent
  - `scheduleTimezone` label present
  - strategy columns include `Open Mkts`/`Closed Mkts`
- Result: all checks passed.

## Risk Notes / Follow-ups
- Existing historical data still reflects prior behavior; new logic applies going forward.
- If compute scheduler runs on separate host, copy the same `scheduler.py`/`scorer.py` there to align runtime behavior.
- Consider adding dedicated DB migration tracking table if not already present.
