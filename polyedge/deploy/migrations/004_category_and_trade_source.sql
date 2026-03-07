-- Add market_category column to markets table
ALTER TABLE markets ADD COLUMN IF NOT EXISTS market_category VARCHAR(30);
CREATE INDEX IF NOT EXISTS ix_markets_market_category ON markets (market_category);

-- Add trade_source column to paper_trades table
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS trade_source VARCHAR(20) DEFAULT 'ngram';
CREATE INDEX IF NOT EXISTS ix_paper_trades_trade_source ON paper_trades (trade_source);

-- Make rule_id nullable (LLM trades have no rule)
ALTER TABLE paper_trades ALTER COLUMN rule_id DROP NOT NULL;

-- Backfill existing paper trades as ngram
UPDATE paper_trades SET trade_source = 'ngram' WHERE trade_source IS NULL;
