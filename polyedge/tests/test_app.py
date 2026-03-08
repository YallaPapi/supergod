"""Tests for the v3 FastAPI app endpoints."""
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

import polyedge.app as app_module
from polyedge.app import (
    app,
    _compute_source_derived_metrics,
    _iso_utc,
    _prediction_edge_pct,
    _rule_to_plain_english,
    human_dashboard,
    paper_trading_real_audit,
)


def test_stats_endpoint_registered():
    assert app.url_path_for("stats") == "/api/stats"


def test_dashboard_endpoint_registered():
    assert app.url_path_for("dashboard") == "/"


def test_dashboard_summary_endpoint():
    assert app.url_path_for("dashboard_summary") == "/api/dashboard"


@pytest.mark.asyncio
async def test_dashboard_summary_returns_payload_not_null(monkeypatch):
    class _Result:
        def scalar(self):
            return 0

        def all(self):
            return []

    class _Session:
        async def execute(self, *_args, **_kwargs):
            return _Result()

    class _SessionCtx:
        async def __aenter__(self):
            return _Session()

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(app_module, "SessionLocal", lambda: _SessionCtx())
    result = await app_module.dashboard_summary()
    assert isinstance(result, dict)
    assert "total_markets" in result


def test_pnl_endpoint():
    assert app.url_path_for("paper_trading_pnl") == "/api/pnl"


def test_pnl_real_audit_endpoint():
    assert app.url_path_for("paper_trading_real_audit") == "/api/pnl/real-audit"


def test_rules_endpoint():
    assert app.url_path_for("list_rules") == "/api/rules"


def test_positions_endpoint():
    assert app.url_path_for("open_positions") == "/api/positions"


def test_features_status_endpoint():
    assert app.url_path_for("feature_status") == "/api/features/status"


def test_activity_endpoint():
    assert app.url_path_for("recent_activity") == "/api/activity"


def test_mission_control_endpoint():
    assert app.url_path_for("mission_control") == "/api/mission-control"


def test_ops_runtime_endpoint():
    assert app.url_path_for("ops_runtime_status") == "/api/ops/runtime"


def test_profile_rule_leaderboard_endpoint_registered():
    assert app.url_path_for("profile_rule_leaderboard") == "/api/profile/rule-leaderboard"


def test_profiles_list_endpoint_registered():
    assert app.url_path_for("list_profiles") == "/api/profiles"


def test_profiles_create_endpoint_registered():
    assert app.url_path_for("create_profile") == "/api/profiles"


def test_profile_rules_update_endpoint_registered():
    assert app.url_path_for("set_profile_rules", profile_id=1) == "/api/profiles/1/rules"


def test_profile_performance_endpoint_registered():
    assert app.url_path_for("profile_performance", profile_id=1) == "/api/profiles/1/performance"


def test_markets_endpoint_registered():
    assert app.url_path_for("list_markets") == "/api/markets"


def test_market_detail_endpoint_registered():
    assert app.url_path_for("get_market", market_id="mkt_123") == "/api/markets/mkt_123"


def test_factors_recent_endpoint_registered():
    assert app.url_path_for("factors_recent") == "/api/factors/recent"


def test_predictions_recent_endpoint_registered():
    assert app.url_path_for("predictions_recent") == "/api/predictions/recent"


def test_analysis_scored_endpoint_registered():
    assert app.url_path_for("analysis_scored") == "/api/analysis/scored"


def test_factor_weights_endpoint_registered():
    assert app.url_path_for("factor_weights") == "/api/factors/weights"


def test_backtest_endpoint_reads_env_path(tmp_path, monkeypatch):
    out = tmp_path / "latest_backtest.json"
    out.write_text('{"status":"ok","trades":7}', encoding="utf-8")
    monkeypatch.setenv("POLYEDGE_BACKTEST_PATH", str(out))
    client = TestClient(app)
    resp = client.get("/api/backtest")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert resp.json()["trades"] == 7


def test_api_responses_disable_cache():
    client = TestClient(app)
    resp = client.get("/api/backtest")
    assert resp.status_code == 200
    assert "no-store" in resp.headers.get("cache-control", "")


def test_dashboard_html_response_disables_cache():
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "no-store" in resp.headers.get("cache-control", "")


def test_prediction_edge_pct_yes():
    assert _prediction_edge_pct("YES", 0.70, 0.55) == 15.0


def test_prediction_edge_pct_no():
    # Market no-price = 1 - yes_price = 0.45, model no-confidence = 0.60 => +15%
    assert _prediction_edge_pct("NO", 0.60, 0.55) == 15.0


def test_prediction_edge_pct_invalid_input():
    assert _prediction_edge_pct("MAYBE", 0.60, 0.55) is None
    assert _prediction_edge_pct("YES", None, 0.55) is None
    assert _prediction_edge_pct("YES", 0.60, None) is None


def test_iso_utc_formats_with_z_suffix():
    assert _iso_utc(app_module.datetime(2026, 3, 6, 12, 0, 0)).endswith("Z")


def test_compute_source_derived_metrics_with_open_book():
    out = _compute_source_derived_metrics(
        closed=10,
        wins=6,
        pnl=1.5,
        open_count=4,
        avg_entry_open=0.52,
        avg_entry_closed=0.49,
    )
    assert out["win_rate_pct"] == 60.0
    assert out["pnl_per_bet"] == pytest.approx(0.15, abs=1e-9)
    assert out["ev_per_bet"] == pytest.approx(0.08, abs=1e-9)
    assert out["expected_open_pnl"] == pytest.approx(0.32, abs=1e-9)


def test_compute_source_derived_metrics_without_closed_trades():
    out = _compute_source_derived_metrics(
        closed=0,
        wins=0,
        pnl=0.0,
        open_count=7,
        avg_entry_open=0.48,
        avg_entry_closed=None,
    )
    assert out["win_rate_pct"] is None
    assert out["pnl_per_bet"] is None
    assert out["ev_per_bet"] is None
    assert out["expected_open_pnl"] is None


def test_human_dashboard_endpoint_registered():
    assert app.url_path_for("human_dashboard") == "/api/human-dashboard"


@pytest.mark.asyncio
async def test_human_dashboard_handles_empty_opportunities_without_unbound_error(monkeypatch):
    class _Result:
        def scalar(self):
            return None

        def scalars(self):
            return self

        def all(self):
            return []

    class _Session:
        async def execute(self, *_args, **_kwargs):
            return _Result()

    class _SessionCtx:
        async def __aenter__(self):
            return _Session()

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(app_module, "SessionLocal", lambda: _SessionCtx())

    result = await human_dashboard()
    assert "error" not in result
    assert result["generated_at"].endswith("Z")


@pytest.mark.asyncio
async def test_real_audit_uses_real_trade_cohort_math(monkeypatch):
    class _Result:
        def __init__(self, one_value=None, scalar_value=None):
            self._one_value = one_value
            self._scalar_value = scalar_value

        def one(self):
            return self._one_value

        def scalar(self):
            return self._scalar_value

    class _Session:
        def __init__(self):
            self._results = [
                _Result(one_value=(201, 93.7995, 22.2005)),  # closed_count, total_entry_cost, total_pnl
                _Result(scalar_value=116),                   # wins
            ]

        async def execute(self, *_args, **_kwargs):
            return self._results.pop(0)

    class _SessionCtx:
        async def __aenter__(self):
            return _Session()

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(app_module, "SessionLocal", lambda: _SessionCtx())

    result = await paper_trading_real_audit()
    assert result["closed_count"] == 201
    assert result["wins"] == 116
    assert result["losses"] == 85
    assert result["total_entry_cost"] == pytest.approx(93.7995, abs=1e-6)
    assert result["total_pnl"] == pytest.approx(22.2005, abs=1e-6)
    assert result["avg_entry_price"] == pytest.approx(93.7995 / 201, abs=1e-6)
    assert result["observed_win_rate"] == pytest.approx(116 / 201, abs=1e-6)
    assert result["breakeven_win_rate"] == pytest.approx(93.7995 / 201, abs=1e-6)
    assert result["roi_on_deployed_capital"] == pytest.approx(22.2005 / 93.7995, abs=1e-6)


class _FakeRule:
    def __init__(self, name, rule_type, conditions_json, predicted_side, win_rate, sample_size):
        self.name = name
        self.rule_type = rule_type
        self.conditions_json = conditions_json
        self.predicted_side = predicted_side
        self.win_rate = win_rate
        self.sample_size = sample_size
        self.breakeven_price = win_rate


def test_rule_to_plain_english_ngram():
    r = _FakeRule("ngram:another party", "ngram", '{"ngram": "another party", "n": 2}',
                  "NO", 1.0, 153)
    text = _rule_to_plain_english(r)
    assert "another party" in text
    assert "NO" in text
    assert "100%" in text
    assert "153" in text


def test_rule_to_plain_english_single_threshold():
    r = _FakeRule("vix_high", "single_threshold",
                  '{"feature": "vix_close", "op": ">", "value": 25}',
                  "NO", 0.64, 120)
    text = _rule_to_plain_english(r)
    assert "vix close" in text
    assert "NO" in text
    assert "64%" in text


def test_rule_to_plain_english_two_feature():
    r = _FakeRule("combo", "two_feature",
                  '{"features": [{"feature": "fred_spread", "op": ">", "value": 0.5}, {"feature": "fear_index", "op": "<", "value": 45}]}',
                  "YES", 0.72, 85)
    text = _rule_to_plain_english(r)
    assert "YES" in text
    assert "72%" in text
