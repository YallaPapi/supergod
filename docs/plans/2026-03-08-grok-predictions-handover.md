# Handover: Real Grok Predictions + Factor-Match Rename

**Date:** 2026-03-08
**Branch:** `handoff/dashboard-human-readable-gap-2026-03-06`
**Status:** Partially working — predictions flowing, trades not scaling, one blocking bug

---

## What Was Done

### 1. Renamed "LLM" to "Factor Match" (COMPLETE)

The old "LLM predictions" system was misleadingly named. It doesn't ask an LLM for predictions — it asks Grok general questions about topics, gets text "factors" back, string-matches them against market questions, and does local math. It's factor-based string matching.

**Rename done everywhere:**
- DB: `UPDATE paper_trades SET trade_source = 'factor_match' WHERE trade_source = 'llm'` (355 rows), same for `llm_inverse` → `factor_match_inv` (341 rows). Deleted stale `llm_paper_trading` heartbeat.
- Code: `scheduler.py` function `run_llm_paper_trading()` → `run_factor_match_paper_trading()`, all trade_source string literals updated
- Dashboard API (`app.py`): All 8 sources listed — ngram, factor_match, grok_direct, combined + inverses
- Dashboard HTML (`dashboard.html`): Source keys and inverse comparison pairs updated
- Migration file: `polyedge/deploy/migrations/006_rename_llm_to_factor_match.sql`

**Verified working:** factor_match and factor_match_inv trades are being placed and resolved. Dashboard shows them correctly.

### 2. Built Real Grok Prediction System (PARTIALLY WORKING)

New system asks Grok directly per market: "Here's the question, odds, description — would you bet YES or NO?"

**New files created:**
- `polyedge/src/polyedge/research/grok_predictor.py` — Core prediction generator
- `polyedge/src/polyedge/models.py` — Added `GrokPrediction` model
- DB table `grok_predictions` created with indexes

**New scheduler loop:** `run_grok_prediction_trading()` runs every 3600s, calls `generate_grok_predictions()`, then places both `grok_direct` and `grok_inv` paper trades for each prediction.

**Current state in DB:**
- 226 grok_predictions stored (out of ~1,100 eligible markets)
- Only 2 grok_direct + 2 grok_inv trades placed

### 3. Added Scheduler Watchdog (COMPLETE)

Every scheduler loop now wrapped in `asyncio.wait_for(fn(), timeout=max_duration)`. If a service hangs, it gets cancelled and the heartbeat records "timeout". Heartbeats also refresh during idle sleep so stale indicators are detectable.

### 4. Removed All Trade Filters (COMPLETE)

- `MIN_CONFIDENCE` set to 0.0 (was 0.15)
- `MAX_ENTRY_PRICE` removed entirely (was 0.50)
- Price filter on grok trade placement removed (was skipping yes_price < 0.02 or > 0.98)
- Both direct + inverse trades always placed for every prediction

---

## What Is NOT Working — The Blocking Issues

### ISSUE 1: httpx ConnectError when making concurrent Grok API calls (CRITICAL)

**Symptom:** `generate_grok_predictions()` finds ~1,100 markets to predict, starts making Grok API calls, but the majority fail with `httpx.ConnectError` (empty error message, no detail). After enough consecutive failures (was 20, now 40), it bails. Result: only ~50-100 predictions per cycle instead of ~1,100.

**What we know:**
- Single Grok API calls work fine (curl, httpx single request, research pipeline)
- A standalone test script making 4 concurrent httpx calls to the Grok API succeeds 4/4
- The failures happen specifically when the grok_predictor runs INSIDE the full scheduler (which has 16+ concurrent asyncio loops: poller, scorer, research, trading, etc.)
- Error types observed: `ConnectError` (most common), `ConnectTimeout`, `RuntimeError: Grok API HTTP {status}` (rare)
- The errors have EMPTY string representations — `str(e)` returns `""`, so we only see the exception class name

**What was tried (without proper research):**
1. Changed from creating new `httpx.AsyncClient` per call → shared singleton client with `Limits(max_connections=10, max_keepalive_connections=5)` — **partial improvement** (went from 0 to ~50 predictions per cycle)
2. Reduced semaphore from 8 → 4 — marginal improvement
3. Added retry with exponential backoff (3 attempts per request) — helped somewhat
4. Reduced chunk size from 200 → 50 — helped somewhat
5. Raised consecutive failure threshold from 20 → 40 — allows more attempts before bailing

**What was NOT done (and should be):**
- Proper research into why httpx fails under concurrent asyncio load on Windows
- Testing whether the Windows ProactorEventLoop (default on Windows) has known issues with concurrent SSL connections
- Testing whether switching to `asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())` fixes it
- Testing whether `aiohttp` has the same problem (it uses a different SSL implementation)
- Checking if Windows has a per-process socket/handle limit being hit by all the scheduler's connections combined
- Profiling the event loop to see if it's getting starved (16 concurrent loops + DB connections + HTTP clients)
- Checking if the Grok API itself is rate-limiting based on connections-per-IP (distinct from requests-per-minute)

**Hypotheses to investigate (ranked by likelihood):**
1. **Windows ProactorEventLoop + SSL saturation** — ProactorEventLoop uses IOCP for I/O, but has known quirks with SSL. The scheduler has dozens of concurrent SSL connections (Polymarket API polling, Grok research, Perplexity research, DB connections). Adding 4 more concurrent SSL connections to api.x.ai may push past a Windows kernel limit.
2. **Event loop starvation** — The 16 concurrent loops are competing for the event loop. When the poller is fetching 500+ market prices and the feature collector is making connector API calls, the grok_predictor's connection attempts may time out waiting for the event loop to process them.
3. **httpx connection pool contention** — The shared client's connection pool may be fighting with other httpx clients in the same process (research pipeline also uses httpx to call Grok).
4. **Grok API connection limit** — api.x.ai may limit concurrent connections from the same IP, and the research pipeline's existing calls consume the quota.

### ISSUE 2: Only 2 grok trades placed despite 226 predictions (RESOLVED but needs verification)

**Root cause:** The trade placement code had a filter `if yes_price <= 0.02 or yes_price >= 0.98: continue` that skipped near-certain markets. 72 of the first 74 predictions were for markets with extreme prices (the predictor selected them because they're high-volume markets ending soon — but they're high-volume precisely because they're near-settled).

**Fix applied:** Removed the price filter from trade placement. Added `Market.yes_price > 0.01` and `Market.yes_price < 0.99` filter to the prediction generator instead (saves API cost on truly-settled markets).

**Status:** Fix is deployed but hasn't been verified yet — need to wait for next grok_prediction_trading cycle to complete and check if trades are placed.

### ISSUE 3: resolution_check keeps timing out (MINOR)

**Symptom:** The `resolution_check` service consistently hits its 120s timeout. The watchdog kills it, and it retries next cycle (every 60s).

**Likely cause:** `check_resolutions()` polls Polymarket API for resolution status of markets with open trades past their end_date. With 20,000+ open ngram trades, many pointing to markets past end_date, each resolution check hits the Polymarket API many times. 120s isn't enough.

**Fix options:**
- Increase timeout to 300s or 600s
- Batch the resolution checks (only check N markets per cycle instead of all)
- Add an index on `paper_trades(resolved, trade_source)` if not exists to speed up the initial query

### ISSUE 4: paper_trading and combined_paper_trading occasionally timeout (MINOR)

Similar to Issue 3 — these process large numbers of markets and occasionally exceed their 300s timeout. The watchdog correctly kills and retries them.

---

## Current System State

### Deployed Files (both servers have latest code)

| File | Server | Path |
|------|--------|------|
| `scheduler.py` | Windows .89 | `C:\polyedge\polyedge\src\polyedge\scheduler.py` |
| `grok_predictor.py` | Windows .89 | `C:\polyedge\polyedge\src\polyedge\research\grok_predictor.py` |
| `grok.py` | Windows .89 | `C:\polyedge\polyedge\src\polyedge\research\grok.py` |
| `models.py` | Windows .89 | `C:\polyedge\polyedge\src\polyedge\models.py` |
| `app.py` | Dashboard .187 | `/opt/polyedge/polyedge/src/polyedge/app.py` |
| `dashboard.html` | Dashboard .187 | `/opt/polyedge/polyedge/src/polyedge/static/dashboard.html` |

### Running Services

- **Scheduler** on Windows 88.99.142.89: Running via `PolyEdgeScheduler` scheduled task → `run_scheduler.ps1` (auto-restart wrapper)
- **Dashboard API** on 89.167.99.187: `polyedge-api.service` (systemd), serving on port 8090

### Database State

```
trade_source      | total  | open   | resolved | total_pnl
------------------+--------+--------+----------+----------
ngram             | 27,366 | 20,641 | 6,725    | +$215.02
ngram_inverse     | 22,623 | 19,340 | 3,283    |  -$49.11
factor_match      |  1,531 |  1,385 |   146    |   -$5.22
factor_match_inv  |  1,517 |  1,383 |   134    |   +$6.56
grok_direct       |      2 |      2 |     0    |    $0.00
grok_inv          |      2 |      2 |     0    |    $0.00
combined          |     30 |     15 |    15    |   +$0.64
combined_inverse  |     30 |     15 |    15    |   -$0.61

grok_predictions: 226 rows (74 from first cycle, rest from subsequent cycles with high failure rate)
```

### Heartbeat Status (as of 07:13 UTC)

Most services healthy: `paper_trading`, `score_paper_trades`, `factor_match_trading`, `grok_prediction_trading` all `ok`. A few timeout regularly: `resolution_check`, `feature_collection`, `research_rule_bridge`. The stale `llm_paper_trading` heartbeat with status `error` can be deleted.

---

## What Needs to Be Done Next

### Priority 1: Fix the httpx ConnectError (blocking grok predictions at scale)

This is the main unfinished work. The system is designed to predict ~1,100 markets per cycle but only manages ~50-100 due to connection failures. Needs proper research into:

1. Whether this is a Windows asyncio ProactorEventLoop limitation
2. Whether serializing calls (no concurrency, just sequential) works reliably
3. Whether moving grok prediction generation to a SEPARATE Python process (not inside the scheduler's event loop) would fix the contention
4. Whether `aiohttp` handles this better than `httpx` on Windows

The simplest reliable approach might be: **don't use asyncio.gather at all**. Just loop through markets sequentially with a single connection. At ~5 seconds per Grok API call, 1,100 markets = ~90 minutes. The scheduler allocates 1800s (30 min) timeout for this loop — increase to 7200s (2 hours) and run sequentially. Slower but reliable.

### Priority 2: Verify grok trades are being placed at scale

After fixing Issue 1, verify that:
- Predictions flow into `grok_predictions` table (should be ~1,000+ per cycle)
- Trades appear in `paper_trades` with source `grok_direct` and `grok_inv`
- Dashboard shows the new sources with data
- Trades get resolved as markets close

### Priority 3: Clean up minor issues

- Delete stale `llm_paper_trading` heartbeat row
- Consider increasing `resolution_check` timeout from 120s to 300s
- Git commit all changes on the branch (nothing committed yet)

---

## Key Code Locations

### grok_predictor.py (the prediction generator)
- `build_prediction_prompt()` — line 20: Constructs the prompt Grok sees
- `parse_grok_response()` — line 55: Parses Grok's JSON response (handles 12+ edge cases)
- `generate_grok_predictions()` — line 145: Main async function. Queries markets, skips cooldown, runs concurrent API calls, commits in batches

### scheduler.py (the trading loop)
- `run_grok_prediction_trading()` — line 568: Calls generate_grok_predictions(), then places trades
- `run_factor_match_paper_trading()` — line 478 (approx): The renamed former "LLM" trading loop
- `loop()` wrapper — line 984: Watchdog wrapper with asyncio.wait_for timeout
- All loop definitions — line 1007-1051: The full asyncio.gather of all 16 scheduler loops

### grok.py (the API client)
- `query_grok()` — line 24: Single function, uses shared httpx.AsyncClient singleton
- `_get_client()` — line 14: Lazy-initializes the shared client with connection limits

### models.py
- `GrokPrediction` model: id, market_id (FK), predicted_side (YES/NO), confidence (0.0-1.0), reasoning (text), created_at

---

## Files Changed (not yet committed)

All changes are on branch `handoff/dashboard-human-readable-gap-2026-03-06`. Modified files:

```
polyedge/src/polyedge/models.py          — Added GrokPrediction model
polyedge/src/polyedge/research/grok.py   — Shared client, better error handling
polyedge/src/polyedge/research/grok_predictor.py — NEW FILE, entire prediction system
polyedge/src/polyedge/scheduler.py       — Renamed llm→factor_match, added grok loop, watchdog
polyedge/src/polyedge/app.py             — Dashboard API updated for 8 trade sources
polyedge/src/polyedge/static/dashboard.html — Dashboard UI updated for 8 trade sources
polyedge/tests/test_grok_predictor.py    — NEW FILE, 15 tests for predictor
polyedge/deploy/migrations/006_rename_llm_to_factor_match.sql — Migration (already applied)
```

---

## Reproduction Steps for the ConnectError Issue

To reproduce on the Windows server (88.99.142.89):

1. Start the full scheduler: `schtasks /run /TN PolyEdgeScheduler`
2. Wait for the `grok_prediction_trading` cycle to start (check heartbeat)
3. Watch logs: `Get-Content C:\polyedge\logs\scheduler.log -Encoding Unicode -Wait | Select-String 'grok_predictor'`
4. You'll see: finds ~1,100 markets → starts making calls → after some successes, ConnectError begins cascading
5. Compare with: running the standalone test script `C:\polyedge\test_grok_api.py` which succeeds 4/4

The key question is: **why does httpx fail when the event loop is busy with 15 other concurrent services, but work fine in isolation?**
