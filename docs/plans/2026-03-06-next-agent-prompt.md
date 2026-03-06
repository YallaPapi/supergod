# Agent Prompt — PolyEdge Dashboard & Paper Trading Fixes

Read the full problem list and deployment info at `docs/plans/2026-03-06-dashboard-fixes-handoff.md` before doing anything.

## Your Tasks (in order)

### 1. Fix Stale 2025-Dated Markets
705+ markets have end_date in 2025 but are still marked active with open paper trades. These are months past due.

- Write a one-off script that queries the Polymarket Gamma API (`https://gamma-api.polymarket.com/markets/{id}`) for each market with `end_date < '2026-01-01' AND active = true` to get their real resolution status.
- Update those markets in the DB with the correct resolution and mark them inactive.
- Then run `score_paper_trades()` to grade the paper trades on those markets.
- Add a guard to `run_paper_trading()` in `scheduler.py`: skip any market where `end_date` is not None and `end_date < now`.
- Deploy to both servers. Verify "Ending Soonest" on the dashboard no longer shows 2025 dates.

### 2. Tier/Classify Ngram Rules
19,505 out of 25,453 trades use garbage 1-3 character ngram rules. Don't delete them — classify them.

Add a tiering system:
- **Tier 1:** ngram phrase is 2+ words, each word 4+ chars, sample_size >= 500. Examples: "be between", "win the", "tournament"
- **Tier 2:** ngram phrase is 1 word but 4+ chars, sample_size >= 500. Examples: "musk", "gold", "leader"
- **Tier 3:** everything else (short chars, low samples). Examples: "c", "f", "st"

Implementation:
- Add a `tier` column to `trading_rules` table (integer, default 3). Run a migration or ALTER TABLE.
- Write a one-off script to classify all existing rules.
- Update `run_paper_trading()` to only trade on Tier 1 and Tier 2 rules.
- Update the dashboard to show the tier as a colored tag next to each rule/trade explanation.
- Don't delete ANY rules. The user wants to keep them all — they might contain signal.

### 3. Speed Up the Scorer
In `scheduler.py` line ~365, change `loop(score_paper_trades, 3600, "score_paper_trades")` to `loop(score_paper_trades, 300, "score_paper_trades")`. Deploy to Windows box.

### 4. Verify Everything Works
After all changes:
- Run `python3 polyedge/audit_dash.py` (already exists) to check the dashboard API for issues
- Check that "Ending Soonest" shows only future dates
- Check that paper trades use Tier 1/2 rules only
- Check that the scorer is running every 5 minutes (look at heartbeat timestamps)
- Verify dashboard at http://89.167.99.187:8090/ looks clean

### 5. Clean Up Old Junk Trades (Optional, non-destructive)
Do **not** rewrite historical outcomes for cosmetic reporting. Keep historical rows intact and:
- exclude junk cohorts from user-facing metrics with explicit filters
- show an "excluded cohort" count if needed for transparency
- preserve original `won` / `pnl` values for auditability

## Rules
- ONE CHANGE AT A TIME. Deploy it, test it, confirm it works, then move on.
- ALWAYS clear `__pycache__` on the Ubuntu server before restarting the dashboard.
- ALWAYS verify the dashboard API output after each deploy — don't just assume it worked.
- Read the handoff doc for server IPs, SSH creds, file paths, and deploy process.
- Do NOT delete any trading rules. Classify them, don't destroy them.
- The scheduler on Windows auto-restarts — editable install means code changes take effect on next cycle.
