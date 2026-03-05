from polyedge.analysis.predictor import make_prediction
from polyedge.analysis.scorer import score_category, _hit_rate_to_weight


def test_make_prediction_with_factors():
    factors = [
        {"category": "historical", "value": "YES in 4/5 cases", "confidence": 0.8},
        {"category": "sentiment", "value": "bullish", "confidence": 0.7},
        {"category": "weather", "value": "sunny", "confidence": 0.3},
    ]
    pred = make_prediction(factors, current_yes_price=0.5)
    assert pred["predicted_outcome"] in ("YES", "NO")
    assert 0.0 <= pred["confidence"] <= 1.0
    assert len(pred["factor_categories"]) > 0


def test_make_prediction_no_factors():
    pred = make_prediction([], current_yes_price=0.6)
    assert pred["predicted_outcome"] == "YES"
    assert pred["confidence"] == 0.3


def test_make_prediction_bearish_factors():
    factors = [
        {"category": "sentiment", "value": "bearish", "confidence": 0.9},
        {"category": "contrarian", "value": "unlikely", "confidence": 0.8},
    ]
    pred = make_prediction(factors, current_yes_price=0.7)
    assert pred["predicted_outcome"] == "NO"


def test_make_prediction_with_weights():
    factors = [
        {"category": "historical", "value": "bullish", "confidence": 0.6},
        {"category": "weather", "value": "bearish", "confidence": 0.6},
    ]
    weights = {"historical": 2.0, "weather": 0.1}
    pred = make_prediction(factors, current_yes_price=0.5, factor_weights=weights)
    assert pred["predicted_outcome"] == "YES"


def test_score_category():
    result = score_category(correct=30, total=50)
    assert result["hit_rate"] == 0.6
    assert result["total_predictions"] == 50
    assert result["correct_predictions"] == 30


def test_score_category_zero():
    result = score_category(correct=0, total=0)
    assert result["hit_rate"] == 0.5


def test_hit_rate_to_weight_low_sample():
    assert _hit_rate_to_weight(0.8, 5) == 1.0  # not enough data


def test_hit_rate_to_weight_coin_flip():
    assert _hit_rate_to_weight(0.5, 100) == 0.1  # no better than random


def test_hit_rate_to_weight_good():
    w = _hit_rate_to_weight(0.6, 100)
    assert w > 1.0  # should amplify
