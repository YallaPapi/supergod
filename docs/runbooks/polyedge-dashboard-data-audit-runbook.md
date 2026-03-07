# PolyEdge Dashboard Data Audit Runbook

## Purpose
Operational checklist to verify dashboard metrics are truthful and internally consistent before/after trading logic changes.

## 1) API health
Run:
```bash
curl -sS http://89.167.99.187:8090/api/human-dashboard
```
Expect:
- valid JSON
- no `error` field

## 2) Count consistency checks
Validate:
- `paper_trades.trade_counts.open == paper_trades.open_count`
- `paper_trades.trade_counts.closed == paper_trades.closed_count`
- `paper_trades.unique_market_counts.open == paper_trades.unique_markets_open`
- `paper_trades.unique_market_counts.closed == paper_trades.unique_markets_closed`
- `hit_rate.paper_trade_scored == paper_trades.closed_count`
- `hit_rate.paper_trade_pct == paper_trades.win_rate_pct`

## 3) Source-level consistency checks
For each source (`ngram`, `llm`, `combined`):
- `unique_markets_open <= open`
- `unique_markets_closed <= closed`
- PnL and win-rate fields present (win-rate can be null when closed=0)

## 4) Time/schedule checks
- `paper_trades.resolution_schedule` buckets should be non-empty during active trading windows.
- Bucket timestamps should be ISO UTC (`...Z`).
- Dashboard UI should show `scheduleTimezone` text to clarify local rendering.

## 5) UI content checks
Fetch page HTML and verify:
- Hero PnL does not use absolute-value-only formatting for total PnL.
- Strategy table includes both trade counts and market counts.

## 6) DB integrity checks (duplicates)
On DB host:
```sql
SELECT market_id, side, trade_source, COUNT(*)
FROM paper_trades
WHERE resolved = FALSE
GROUP BY market_id, side, trade_source
HAVING COUNT(*) > 1;
```
Expect: zero rows.

## 7) Migration/index checks
Confirm indexes exist:
- `ux_open_trade_market_side_source`
- `ix_predictions_market_created_at`
- `ix_predictions_created_at`
- `ix_paper_trades_resolved_source`
- `ix_paper_trades_market_resolved`
- `ix_markets_active_end_date`
- `ix_markets_category_end_date`

## 8) Regression tests
Run:
```bash
cd polyedge
pytest -q tests
```
Expect:
- no failures

## 9) Escalation criteria
Do not proceed to real-money changes if any of the following occur:
- count consistency mismatch
- duplicate unresolved open trades found
- API payload contains `error`
- source stats show impossible relationships (`unique > trade count`)
- PnL display regression (missing negative sign)
