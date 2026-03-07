-- Trade integrity and dashboard/perf indexes

-- 1) Deduplicate unresolved trades before adding uniqueness guard.
WITH ranked AS (
    SELECT
        id,
        row_number() OVER (
            PARTITION BY market_id, side, trade_source
            ORDER BY created_at DESC, id DESC
        ) AS rn
    FROM paper_trades
    WHERE resolved = FALSE
)
DELETE FROM paper_trades p
USING ranked r
WHERE p.id = r.id
  AND r.rn > 1;

-- 2) Prevent duplicate unresolved trades per market/side/source.
CREATE UNIQUE INDEX IF NOT EXISTS ux_open_trade_market_side_source
ON paper_trades (market_id, side, trade_source)
WHERE resolved = FALSE;

-- 3) Hot-path indexes for scheduler + dashboard queries.
CREATE INDEX IF NOT EXISTS ix_predictions_market_created_at
ON predictions (market_id, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_predictions_created_at
ON predictions (created_at DESC);

CREATE INDEX IF NOT EXISTS ix_paper_trades_resolved_source
ON paper_trades (resolved, trade_source);

CREATE INDEX IF NOT EXISTS ix_paper_trades_market_resolved
ON paper_trades (market_id, resolved);

CREATE INDEX IF NOT EXISTS ix_markets_active_end_date
ON markets (active, end_date);

CREATE INDEX IF NOT EXISTS ix_markets_category_end_date
ON markets (market_category, end_date);
