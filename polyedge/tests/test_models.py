from polyedge.models import Market, Factor, Prediction, FactorWeight, PriceSnapshot, Base


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


def test_factor_allows_null_market():
    f = Factor(category="weather", name="temp", value="hot", source="grok")
    assert f.market_id is None
