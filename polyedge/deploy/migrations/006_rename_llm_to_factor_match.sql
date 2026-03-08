-- Rename paper_trades trade_source: llm -> factor_match, llm_inverse -> factor_match_inv
BEGIN;
UPDATE paper_trades SET trade_source = 'factor_match' WHERE trade_source = 'llm';
UPDATE paper_trades SET trade_source = 'factor_match_inv' WHERE trade_source = 'llm_inverse';
DELETE FROM service_heartbeats WHERE service = 'llm_paper_trading';
COMMIT;

-- Create grok_predictions table
CREATE TABLE IF NOT EXISTS grok_predictions (
    id SERIAL PRIMARY KEY,
    market_id VARCHAR REFERENCES markets(id),
    predicted_side VARCHAR(3) NOT NULL,
    confidence FLOAT DEFAULT 0.5,
    reasoning TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_grok_pred_market_ts ON grok_predictions(market_id, created_at);
CREATE INDEX IF NOT EXISTS ix_grok_pred_created ON grok_predictions(created_at);
