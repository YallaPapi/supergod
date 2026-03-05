from polyedge.app import app


def test_stats_endpoint_registered():
    assert app.url_path_for("stats") == "/api/stats"


def test_markets_endpoint_registered():
    assert app.url_path_for("list_markets") == "/api/markets"


def test_factor_weights_endpoint_registered():
    assert app.url_path_for("factor_weights") == "/api/factors/weights"


def test_get_market_endpoint_registered():
    assert app.url_path_for("get_market", market_id="test") == "/api/markets/test"
