# PolyEdge Full System Review — Prompt for External Coding Agent

## Context

PolyEdge is a **prediction market trading system** built on top of Polymarket. It:
1. Polls ~8k active markets from Polymarket's Gamma API
2. Mines ngram patterns from 500k+ historical market resolutions
3. Generates trading rules (keyword patterns that predict YES/NO outcomes)
4. Runs LLM research (Grok/Perplexity) to produce market factors & predictions
5. Paper trades using 3 parallel strategies: ngram rules, LLM predictions, combined
6. Displays all results on a live dashboard at :8090

The system has been running for ~24 hours, has placed ~25,000 paper trades, and resolved ~2,000+ with ~$70 PnL.

**Goal of real trading**: Start with $200-300 real money on Polymarket within 3-4 days.

---

## Infrastructure

| Server | IP | Role |
|---|---|---|
| 256GB Windows | 88.99.142.89 | ALL compute: scheduler, paper trading, connectors, research, mining |
| CX22 Ubuntu | 89.167.99.187 | PostgreSQL DB, dashboard API (:8090), poller |
| Workers (3x) | 77.42.x, 89.167.107.x, 89.167.109.x | Codex CLI research agents |

**Database**: PostgreSQL on 89.167.99.187, user=polyedge, db=polyedge
- `markets` (503k rows), `predictions` (4M+), `factors` (24k+), `paper_trades`, `trading_rules`, `ngram_stats` (472k), `daily_features`, `price_snapshots`

---

## Files to Review (start here)

### Core Trading Logic
- `polyedge/src/polyedge/scheduler.py` — Main scheduler with all trading loops. **Critical**: `run_paper_trading()`, `run_llm_paper_trading()`, `run_combined_paper_trading()`, `check_resolutions()`, `run_forever()`
- `polyedge/src/polyedge/analysis/predictor.py` — Rule-based prediction engine. Calculates confidence scores. **Known issue**: confidence values (0.01-0.50) are NOT probabilities — they're rule agreement ratios. This has been a source of bugs.
- `polyedge/src/polyedge/analysis/scorer.py` — Scores resolved paper trades, calculates PnL
- `polyedge/src/polyedge/trading/pnl.py` — PnL calculation logic (Polymarket: win = 1.0 - entry, loss = -entry)

### Data Pipeline
- `polyedge/src/polyedge/poller.py` — Polls Polymarket Gamma API for market data
- `polyedge/src/polyedge/analysis/generate.py` — Generates predictions from rules + features
- `polyedge/src/polyedge/analysis/ngram_miner.py` — Mines keyword patterns from resolved markets
- `polyedge/src/polyedge/analysis/rule_generator.py` — Converts ngram stats to trading rules
- `polyedge/src/polyedge/analysis/correlation_engine.py` — Discovers feature-outcome correlations
- `polyedge/src/polyedge/analysis/feature_matrix.py` — Builds feature matrix from daily data
- `polyedge/src/polyedge/research/pipeline.py` — LLM research pipeline (Grok/Perplexity)
- `polyedge/src/polyedge/research/factor_features.py` — Converts LLM factors to numeric features

### Dashboard & API
- `polyedge/src/polyedge/app.py` — FastAPI dashboard with `/api/human-dashboard` endpoint
- `polyedge/src/polyedge/static/dashboard.html` — Single-page dashboard (vanilla JS, no framework)
- `polyedge/src/polyedge/query_filters.py` — Shared SQL filters for trade cohorts

### Models & DB
- `polyedge/src/polyedge/models.py` — SQLAlchemy models (Market, TradingRule, PaperTrade, Prediction, Factor, etc.)
- `polyedge/src/polyedge/db.py` — Database connection settings
- `polyedge/deploy/migrations/` — SQL migration files

### Market Classification
- `polyedge/src/polyedge/analysis/market_classifier.py` — Keyword-based market categorizer (12 categories)

---

## What to Review — Be Thorough

### 1. PnL Calculation Correctness
- Is the PnL math correct in `pnl.py` and `scorer.py`? Polymarket CLOB: YES+NO = $1.00, win = $1.00 - entry, loss = -entry.
- Are there any edge cases where PnL could be miscalculated? (e.g., partial fills, market resolution to neither YES nor NO)
- Is the win/loss determination correct? A YES trade wins if market resolves YES. A NO trade wins if market resolves NO.
- **IMPORTANT**: PnL matters more than win rate. A 40% win rate with low entry prices can be very profitable. A 60% win rate with high entry prices can lose money. Make sure the system is optimizing for PnL, not win rate.

### 2. Trading Logic Bugs
- `run_paper_trading()`: Does the 48h horizon filter work correctly? Are we missing markets?
- `run_llm_paper_trading()`: The confidence is NOT a probability — it's a rule agreement ratio (0-0.50). Is the current approach (conviction threshold >= 0.15, entry <= 0.50) correct? Should we also consider betting the opposite side when entry is cheap?
- `run_combined_paper_trading()`: Does the ngram+LLM agreement check work? Are there edge cases?
- `check_resolutions()`: Does this correctly identify resolved markets? Does it handle Polymarket's resolution edge cases (e.g., markets that close without resolution, markets resolved to "N/A")?
- Are there race conditions between the trading loops and the resolution checker?
- Do we ever create duplicate trades on the same market/side/source?

### 3. Data Integrity
- Is the poller correctly parsing all market fields from the Gamma API?
- Are end_dates being stored correctly (timezone handling — naive UTC vs aware)?
- Could markets be incorrectly marked as resolved? Or fail to be marked as resolved?
- Is the `refresh_stale_unresolved()` logic correct? Does it correctly handle markets that passed their end_date but haven't been resolved on Polymarket?
- Are predictions being generated correctly from factors? Is the factor → prediction pipeline losing information?

### 4. Performance & Scalability
- The scheduler runs ~12 async loops via `asyncio.gather`. Are there any blocking operations that could stall the event loop?
- The dashboard API queries are complex with multiple JOINs. Are there missing indexes? Could any query take too long?
- With 25k+ open paper trades, are the scoring and resolution check queries efficient?
- Memory usage on the 256GB server — any leaks in the long-running scheduler?

### 5. Dashboard Accuracy
- Does the dashboard accurately reflect the database state?
- Are the cohort breakdowns (crypto vs non-crypto, ngram vs LLM vs combined) correct?
- Does the "unique markets" count correctly de-duplicate across strategies?
- Is the resolution schedule chart correctly calculating hourly buckets?
- Are timezone conversions handled correctly throughout (server stores naive UTC, frontend converts to local)?

### 6. Market Classification Quality
- Review `market_classifier.py` — are the regex patterns catching the right markets?
- Are there categories that should be split further or merged?
- Any markets being misclassified?
- The classifier uses "first match wins" — is the ordering correct?

### 7. Security & Reliability
- Are there any SQL injection risks? (using SQLAlchemy ORM, but check raw queries)
- Error handling in the scheduler — if one loop crashes, does it affect others?
- Database connection handling — are sessions properly closed? Any connection pool exhaustion risks?
- API rate limiting on Polymarket Gamma API — could we get rate-limited during resolution checks?
- Are API keys properly handled (not hardcoded in committed code)?

### 8. Ideas for Improvement
- **Backtesting**: The backtester (`backtester.py`) exists but has never been run. How should we validate rules before trading real money?
- **Per-category rules**: Should we mine separate ngram rules per category (crypto, sports, politics, etc.) instead of global rules?
- **LLM prediction quality**: 4M predictions but confidence values are mostly garbage (53% below 0.05). How can we improve the prediction pipeline?
- **Capital allocation**: With $200-300 starting capital, how should we prioritize which trades to take? Currently all bets are $1. Should we size bets based on edge/confidence?
- **Risk management**: What happens if we hit a losing streak? Should there be circuit breakers?
- **Feature engineering**: 170 daily features from 23 sources — are we using them effectively? Or is it noise?
- **Real-time vs batch**: The scheduler runs on fixed intervals (60s-6h). Would event-driven be better?
- **Resolution speed**: Some Polymarket markets take days/weeks to resolve after end_date. How to handle capital tied up in unresolved markets?

### 9. Code Quality
- Are there any obvious anti-patterns, code duplication, or dead code?
- Are the tests adequate? Run `pytest polyedge/tests/` and check coverage.
- Is the module structure clean? Should anything be refactored?
- Are there any circular imports or import-time side effects?

### 10. Pre-Real-Money Checklist
Before deploying real money trading, what MUST be verified?
- PnL calculation correctness (double-check the math)
- Duplicate trade prevention (same market, same side)
- Maximum exposure limits (how much capital at risk?)
- Order execution logic (not implemented yet — how to integrate with Polymarket CLOB API?)
- Slippage and fees (Polymarket charges fees — are they accounted for in PnL?)
- Manual kill switch (how to stop all trading immediately?)
- Monitoring and alerts (how do we know if something goes wrong?)

---

## How to Run the Code

```bash
# On local machine (Windows)
cd C:\Users\asus\Desktop\projects\supergod\polyedge

# Run tests
python -m pytest tests/ -v

# The dashboard is live at http://89.167.99.187:8090/
# API: http://89.167.99.187:8090/api/human-dashboard

# DB access (from allowed IPs only):
# psql -h 89.167.99.187 -U polyedge -d polyedge
# Password: polyedge
```

## Output Format

Please provide:
1. **Critical issues** — Things that MUST be fixed before real money trading
2. **Important issues** — Things that should be fixed soon
3. **Suggestions** — Nice-to-have improvements
4. **Architecture review** — High-level observations about the system design
5. **Specific code concerns** — File:line references with explanations
6. **Pre-real-money verdict** — Is this system ready for real money? What's missing?
