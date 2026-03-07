from pathlib import Path


def test_hit_rate_card_keeps_negative_sign_for_pnl():
    dashboard = Path("src/polyedge/static/dashboard.html").read_text(encoding="utf-8")
    assert "Math.abs(ptPnl).toFixed(2)" not in dashboard
