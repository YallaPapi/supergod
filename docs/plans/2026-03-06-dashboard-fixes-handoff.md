# PolyEdge Dashboard & Paper Trading — Full Problem List & Fix Plan

**Date:** 2026-03-06
**Context:** Paper trading system is live with ~25k trades, dashboard at http://89.167.99.187:8090/. Multiple issues need fixing before results are trustworthy.

---

## PROBLEM 1: Stale Markets with 2025 End Dates

**What's wrong:** 705+ paper trades are on markets with end_date in October-November 2025 — months ago. These markets clearly ended but Polymarket still marks them as `active=true` somehow, or our poller never updated them. They show up in "Ending Soonest" and inflate our trade count.

**Evidence:**
- First 25 "ending soonest" trades all have 2025 dates
- Markets like "Colorado State Rams vs. Saint Mary's Gaels" with end_date 2025-11-08 are still marked active
- These never resolved because the poller only fetches active markets from Polymarket, and these are in limbo

**Fix needed:**
1. Query Polymarket API directly for these specific market IDs to get their actual resolution status
2. Mark any market past its end_date by >7 days as inactive if it hasn't resolved
3. Close paper trades on these stale markets (mark resolved, score them)
4. Add a check in the scheduler to skip markets where end_date < now

**Files:** `polyedge/src/polyedge/scheduler.py` (skip stale markets), `polyedge/src/polyedge/poller.py` (fetch stale market resolutions)

---

## PROBLEM 2: Garbage Short Ngram Rules Matching Everything

**What's wrong:** 77% of paper trades (19,505 out of 25,453) are based on 1-3 character ngram rules like:
- `ngram:c` — matches any question containing the letter "c" (83% win rate, 4553 markets)
- `ngram:f` — the letter "f" (85%, 8324 markets)
- `ngram:st` — matches "State", "first", "best", etc. (84%, 297 markets)
- `ngram:tate` — matches "State" in team names (90%, 185 markets — also only 185 samples despite 500 min)

These match so broadly they're essentially just measuring the base rate of NO resolution across all Polymarket markets (~75%).

**User's instruction:** Do NOT delete these rules. They might still contain signal. But classify/tier them separately so they don't pollute the main results. "If 'st' has 99% NO rate, that's still interesting — we just need to categorize it."

**Fix needed:**
1. Add a `tier` or `quality` field to TradingRule or use a classification system:
   - **Tier 1 (Strong):** Multi-word phrases, 4+ chars per word, 500+ samples (e.g., "be between", "win the", "tournament")
   - **Tier 2 (Moderate):** Short but specific phrases, 4+ chars, could be meaningful (e.g., "musk", "gold", "feb")
   - **Tier 3 (Weak/Exploratory):** 1-3 char ngrams that match too broadly (e.g., "c", "f", "st")
2. Paper trader should primarily trade on Tier 1 rules, but can also trade Tier 2 with higher edge threshold
3. Dashboard should show rule tier so user can see quality at a glance
4. Keep all rules in DB — don't delete anything

**Files:** `polyedge/src/polyedge/scheduler.py` (tier-based trading), `polyedge/src/polyedge/app.py` (show tiers), `polyedge/src/polyedge/analysis/ngram_miner.py` (if adding tier field to rule generation)

---

## PROBLEM 3: "Up or Down" Crypto Noise Markets

**What's wrong:** ~2,530 paper trades were placed on 5-minute crypto prediction markets like "Bitcoin Up or Down - March 5, 3:30AM-3:35AM ET". These are essentially coin flips with tiny edge. The old scheduler (before filtering) placed trades on these. All 66 that resolved were losses.

**Current status:** Filter added to scheduler and dashboard to exclude `question ILIKE '%up or down%'`. New trades won't be placed on these. But old trades still exist in DB.

**Fix needed:**
1. Mark existing "up or down" paper trades as resolved with won=false (clean up DB)
2. OR just leave them but ensure they're always filtered from display and metrics (current approach)
3. Consider also filtering other high-frequency noise patterns: "O/U" spreads with very short timeframes, 5-minute weather predictions, etc.

**Files:** `polyedge/src/polyedge/scheduler.py` (already filtered), `polyedge/src/polyedge/app.py` (already filtered in display)

---

## PROBLEM 4: Dead Markets with $0.001 Entry Price

**What's wrong:** ~1,025 trades have entry_price <= $0.02. These are markets where one side is essentially 0 — nobody is actually trading at that price. The "edge" looks huge (97%+) but it's fake because:
- The market is already resolved in practice
- There's no liquidity at $0.001
- You couldn't actually execute this trade

**Current status:** Filter added to exclude entry_price <= 0.02 from display and metrics.

**Fix needed:**
1. Current filtering is working but the trades still exist and get counted in raw totals
2. Consider adding an entry_price minimum to the scheduler itself (currently done: `yes_price <= 0.02 or >= 0.98` skip)
3. Verify the scheduler skip is working by checking if new dead-market trades stop appearing

**Files:** `polyedge/src/polyedge/scheduler.py` (already has price filter)

---

## PROBLEM 5: Dashboard "Hit Rate" Section Can Show Misleading Data

**What's wrong:** The "Are We Making Money?" card showed "0/66 paper trades won" because all 66 scored trades were junk (up-or-down crypto noise from old scheduler). This made us look terrible when really zero real trades had been scored yet.

**Current status:** Fixed by filtering junk from metrics. Now shows "21,703 paper trades open, waiting for markets to resolve."

**Fix needed:**
1. Continue monitoring — when real trades start resolving, verify the hit rate updates correctly
2. The explanation text should clearly distinguish between "no results yet" and "results are bad"
3. Add a "last scored at" timestamp so user can see when the scorer last ran

**Files:** `polyedge/src/polyedge/app.py` (hit_rate section)

---

## PROBLEM 6: Scorer Runs Too Infrequently

**What's wrong:** The scorer (`score_paper_trades`) runs every 3600 seconds (1 hour). Markets can resolve anytime. The poller runs every 5 minutes to pick up resolutions from Polymarket, but scoring only happens hourly.

**Fix needed:**
1. Reduce scorer interval to 300 seconds (5 minutes) — run right after the poller
2. Or trigger scoring immediately after each poller run
3. This way, results appear on the dashboard within 5-10 minutes of market resolution instead of up to 1 hour

**Files:** `polyedge/src/polyedge/scheduler.py` line 365: `loop(score_paper_trades, 3600, "score_paper_trades")` → change 3600 to 300

---

## PROBLEM 7: No Market Categories from Polymarket

**What's wrong:** 542,067 out of 546,434 markets have blank category field. Only ~4,000 old markets have categories like "Sports", "Crypto", "Pop-Culture". The Polymarket API used to return categories but newer markets don't have them.

**Evidence:** Checked API — `raw.get("category", "")` returns empty for most current markets.

**Fix needed:**
1. Check if Polymarket has category info in a different field or endpoint (maybe in the event-level data, not market-level)
2. If not available from API, consider a lightweight classifier based on keywords (user said NO to this — revisit if API truly has nothing)
3. Or use the event/group structure from Polymarket to derive categories

**Files:** `polyedge/src/polyedge/poller.py` (check for category in API response)

---

## PROBLEM 8: Paper Trade Deduplication Issues

**What's wrong:** User reported seeing duplicate markets in the dashboard. Fixed by grouping by market_id and showing best edge per market. But the deduplication only happens at the display level — the DB still has multiple trades per market if multiple rules matched.

**Current status:** Display deduplication is working. DB-level deduplication exists (one trade per market_id+side combo).

**Fix needed:** Verify no new duplicates are appearing. The scheduler tracks `existing_trades: set[tuple[str, str]]` to prevent duplicates.

---

## PROBLEM 9: Past-Due Markets Still Pollute Open-Trade Views

**What's wrong:** Past-due markets can still inflate open-trade counts and appear in broader panels if stale unresolved rows are not reconciled. The `ending_soonest` query already applies `Market.end_date >= now`, so this is primarily a stale-market lifecycle and date-normalization problem (Problem 1), not only a sort/filter placement issue.

**Fix needed:**
1. Keep all user-facing open-trade metrics on one canonical "real trade + future end date" predicate.
2. Reconcile stale unresolved markets so expired rows stop polluting aggregate counts.
3. Normalize UTC serialization/parsing so date labels are consistent across environments.

**Files:** `polyedge/src/polyedge/app.py` (ending_soonest query)

---

## PROBLEM 10: Scheduler Auto-Restarts but Doesn't Log

**What's wrong:** On the Windows box (88.99.142.89), when the scheduler process is killed, it auto-restarts (likely a Windows service or scheduled task). But there's no easy way to see scheduler logs — output goes to nowhere.

**Fix needed:**
1. Find out what's auto-restarting the scheduler (Windows Task Scheduler? NSSM service?)
2. Configure log output to a file (e.g., `C:\polyedge\polyedge\scheduler.log`)
3. Add a log viewer endpoint to the dashboard (or just tail the log file)

**Files:** `polyedge/run_scheduler.py`, Windows task/service config

---

## DEPLOYMENT INFO

### Servers
| Server | IP | Role |
|--------|-----|------|
| 256GB Windows | 88.99.142.89 | Scheduler, paper trader, all compute |
| CX22 Ubuntu | 89.167.99.187 | PostgreSQL, dashboard (:8090), supergod orchestrator |

### SSH Access
- Windows: `ssh Administrator@88.99.142.89` (password: TmgDE3gktMBDg%)
- Ubuntu: `ssh root@89.167.99.187`

### Key Paths
- Windows source: `C:\polyedge\polyedge\src\polyedge\` (editable install)
- Ubuntu source: `/opt/polyedge/polyedge/src/polyedge/` (editable install)
- Dashboard HTML: `src/polyedge/static/dashboard.html`
- Main backend: `src/polyedge/app.py`
- Scheduler: `src/polyedge/scheduler.py`
- Models: `src/polyedge/models.py`

### Deploy Process
1. Edit files locally in `C:\Users\asus\Desktop\projects\supergod\polyedge\`
2. SCP to target server: `scp -O -o StrictHostKeyChecking=no <file> <user>@<ip>:<path>`
3. For dashboard (Ubuntu): kill old process, delete `__pycache__`, restart `polyedge serve`
4. For scheduler (Windows): editable install picks up changes on next cycle, or kill/restart the process
5. **CRITICAL:** Always delete .pyc cache on Ubuntu before restarting or you get stale code

### Database
- PostgreSQL on 89.167.99.187: db=polyedge, user=polyedge, pass=polyedge
- Key tables: markets, paper_trades, trading_rules, ngram_stats, factors, predictions

### Current State (as of this handoff)
- **25,453 paper trades open** (but ~19,500 are from garbage short ngrams)
- **339 rules** with 500+ sample size and 60%+ win rate
- **1,709 markets ending today** (March 6)
- **Scorer runs hourly** — first real results should appear within hours
- **Dashboard is live** at http://89.167.99.187:8090/
- **Scheduler running** on Windows box, auto-restarts on kill

### What Was Already Fixed This Session
- Dashboard rewritten to human-readable format
- "Up or Down" noise filtered from trades and display
- Dead market ($0.001 entry) filtered
- Duplicate trades cleaned (one per market per side)
- Resolution dates added to dashboard
- "Ending Soonest" section added
- Rule explanations in plain English
- Hit rate metrics exclude junk trades
- Minimum sample size raised from 30 → 500
- Scheduler skip for dead prices (yes_price ≤ 0.02 or ≥ 0.98)

### Priority Order
1. **Fix stale 2025-dated markets** (Problem 1) — most impactful, blocks accurate results
2. **Tier/classify ngram rules** (Problem 2) — user explicitly wants this, don't delete rules
3. **Speed up scorer to 5 min** (Problem 6) — quick win
4. **Clean up "up or down" old trades** (Problem 3) — cosmetic but important
5. **Categories from Polymarket** (Problem 7) — nice to have
6. **Everything else** — lower priority

