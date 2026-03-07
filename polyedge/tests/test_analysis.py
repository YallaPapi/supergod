"""Tests for the v3 predictor and scorer."""
from polyedge.analysis.predictor import check_rule_conditions, predict_market
from polyedge.analysis.scorer import score_resolved_markets


def test_predictor_importable():
    """Smoke test that v3 predictor functions are importable."""
    assert callable(check_rule_conditions)
    assert callable(predict_market)


def test_scorer_importable():
    assert callable(score_resolved_markets)
