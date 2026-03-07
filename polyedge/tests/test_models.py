from polyedge.models import (
    Market, Factor, Prediction, FactorWeight, PriceSnapshot, Base,
    DailyFeature, MarketPriceHistory, TradingRule, PaperTrade, NgramStat, ServiceHeartbeat,
)


def test_all_models_have_tablename():
    for model in [Market, Factor, Prediction, FactorWeight, PriceSnapshot]:
        assert hasattr(model, "__tablename__")


def test_market_defaults():
    """Column defaults are applied at flush/insert time by SQLAlchemy,
    so we verify the default values are declared on the column."""
    table = Market.__table__
    assert table.c.yes_price.default.arg == 0.5
    assert table.c.active.default.arg is True
    assert table.c.resolved.default.arg is False
    assert table.c.resolution_source.default.arg == ""


def test_factor_allows_null_market():
    f = Factor(category="weather", name="temp", value="hot", source="grok")
    assert f.market_id is None


# --- v3 models ---

def test_v3_models_importable():
    """All 5 new models can be imported."""
    assert DailyFeature is not None
    assert MarketPriceHistory is not None
    assert TradingRule is not None
    assert PaperTrade is not None
    assert NgramStat is not None
    assert ServiceHeartbeat is not None


def test_v3_tablenames():
    assert DailyFeature.__tablename__ == "daily_features"
    assert MarketPriceHistory.__tablename__ == "market_price_history"
    assert TradingRule.__tablename__ == "trading_rules"
    assert PaperTrade.__tablename__ == "paper_trades"
    assert NgramStat.__tablename__ == "ngram_stats"
    assert ServiceHeartbeat.__tablename__ == "service_heartbeats"


def test_daily_feature_unique_index():
    """DailyFeature has a unique composite index on (date, name)."""
    indexes = DailyFeature.__table__.indexes
    unique_idx = [idx for idx in indexes if idx.name == "ix_feat_date_name"]
    assert len(unique_idx) == 1
    idx = unique_idx[0]
    assert idx.unique is True
    col_names = [c.name for c in idx.columns]
    assert col_names == ["date", "name"]


def test_trading_rule_columns():
    """TradingRule has all expected columns."""
    table = TradingRule.__table__
    expected = [
        "id", "name", "rule_type", "conditions_json", "predicted_side",
        "win_rate", "sample_size", "breakeven_price", "avg_roi",
        "market_filter", "active", "created_at", "updated_at",
    ]
    actual = [c.name for c in table.columns]
    for col in expected:
        assert col in actual, f"Missing column: {col}"
