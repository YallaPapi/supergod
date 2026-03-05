from polyedge.analysis.generate import generate_all_predictions


def test_generate_is_callable():
    assert callable(generate_all_predictions)
