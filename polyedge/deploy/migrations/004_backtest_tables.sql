-- Backtest results per rule
CREATE TABLE IF NOT EXISTS backtest_results (
    id SERIAL PRIMARY KEY,
    rule_id INTEGER REFERENCES trading_rules(id),
    total_matches INTEGER DEFAULT 0,
    wins_direct INTEGER DEFAULT 0,
    losses_direct INTEGER DEFAULT 0,
    pnl_direct FLOAT DEFAULT 0.0,
    wins_inverse INTEGER DEFAULT 0,
    losses_inverse INTEGER DEFAULT 0,
    pnl_inverse FLOAT DEFAULT 0.0,
    recommended_side VARCHAR(10) DEFAULT 'direct',
    edge_magnitude FLOAT DEFAULT 0.0,
    run_date TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_bt_rule_date ON backtest_results(rule_id, run_date);

-- Per-category rule performance
CREATE TABLE IF NOT EXISTS rule_category_performance (
    id SERIAL PRIMARY KEY,
    rule_id INTEGER REFERENCES trading_rules(id),
    category VARCHAR(50),
    sample_size INTEGER DEFAULT 0,
    wins_direct INTEGER DEFAULT 0,
    pnl_direct FLOAT DEFAULT 0.0,
    wins_inverse INTEGER DEFAULT 0,
    pnl_inverse FLOAT DEFAULT 0.0,
    recommended_side VARCHAR(10) DEFAULT 'direct',
    last_updated TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_rcp_rule_cat ON rule_category_performance(rule_id, category);
CREATE UNIQUE INDEX IF NOT EXISTS ix_rcp_rule_cat_uniq ON rule_category_performance(rule_id, category);

-- Agreement tier signals
CREATE TABLE IF NOT EXISTS agreement_signals (
    id SERIAL PRIMARY KEY,
    agreement_tier INTEGER,
    category VARCHAR(50) DEFAULT 'all',
    sample_size INTEGER DEFAULT 0,
    wins INTEGER DEFAULT 0,
    pnl FLOAT DEFAULT 0.0,
    avg_pnl_per_trade FLOAT DEFAULT 0.0,
    last_updated TIMESTAMP DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_ag_tier_cat ON agreement_signals(agreement_tier, category);
