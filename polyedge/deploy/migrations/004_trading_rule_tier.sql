-- Rule quality classification metadata.

ALTER TABLE trading_rules
ADD COLUMN IF NOT EXISTS tier INTEGER NOT NULL DEFAULT 3;

ALTER TABLE trading_rules
ADD COLUMN IF NOT EXISTS quality_label VARCHAR(20) NOT NULL DEFAULT 'exploratory';

CREATE INDEX IF NOT EXISTS ix_trading_rules_tier_active
ON trading_rules (tier, active);
