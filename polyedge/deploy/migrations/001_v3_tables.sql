-- v3 schema: DailyFeature, MarketPriceHistory, TradingRule, PaperTrade, NgramStat

CREATE TABLE IF NOT EXISTS daily_features (
    id SERIAL PRIMARY KEY,
    date DATE NOT NULL,
    source VARCHAR(50) NOT NULL,
    category VARCHAR(50) NOT NULL,
    name VARCHAR(200) NOT NULL,
    value FLOAT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_daily_features_date ON daily_features (date);
CREATE INDEX IF NOT EXISTS ix_daily_features_source ON daily_features (source);
CREATE INDEX IF NOT EXISTS ix_daily_features_category ON daily_features (category);
CREATE INDEX IF NOT EXISTS ix_daily_features_name ON daily_features (name);
CREATE UNIQUE INDEX IF NOT EXISTS ix_feat_date_name ON daily_features (date, name);


CREATE TABLE IF NOT EXISTS market_price_history (
    id SERIAL PRIMARY KEY,
    market_id VARCHAR NOT NULL REFERENCES markets(id),
    timestamp TIMESTAMP NOT NULL,
    yes_price FLOAT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_market_price_history_market_id ON market_price_history (market_id);
CREATE INDEX IF NOT EXISTS ix_market_price_history_timestamp ON market_price_history (timestamp);
CREATE INDEX IF NOT EXISTS ix_mph_market_ts ON market_price_history (market_id, timestamp);


CREATE TABLE IF NOT EXISTS trading_rules (
    id SERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    rule_type VARCHAR(50) NOT NULL,
    conditions_json TEXT NOT NULL,
    predicted_side VARCHAR(3) NOT NULL,
    win_rate FLOAT NOT NULL,
    sample_size INTEGER NOT NULL,
    breakeven_price FLOAT NOT NULL,
    avg_roi FLOAT DEFAULT 0,
    market_filter TEXT DEFAULT '',
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);


CREATE TABLE IF NOT EXISTS paper_trades (
    id SERIAL PRIMARY KEY,
    market_id VARCHAR NOT NULL REFERENCES markets(id),
    rule_id INTEGER NOT NULL REFERENCES trading_rules(id),
    side VARCHAR(3) NOT NULL,
    entry_price FLOAT NOT NULL,
    edge FLOAT NOT NULL,
    bet_size FLOAT DEFAULT 1.0,
    resolved BOOLEAN DEFAULT FALSE,
    won BOOLEAN,
    pnl FLOAT,
    created_at TIMESTAMP DEFAULT NOW(),
    resolved_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_paper_trades_market_id ON paper_trades (market_id);
CREATE INDEX IF NOT EXISTS ix_paper_trades_rule_id ON paper_trades (rule_id);


CREATE TABLE IF NOT EXISTS ngram_stats (
    id SERIAL PRIMARY KEY,
    ngram VARCHAR(200) NOT NULL UNIQUE,
    n INTEGER NOT NULL,
    total_markets INTEGER NOT NULL,
    yes_count INTEGER NOT NULL,
    no_count INTEGER NOT NULL,
    yes_rate FLOAT NOT NULL,
    no_rate FLOAT NOT NULL,
    updated_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_ngram_stats_ngram ON ngram_stats (ngram);
