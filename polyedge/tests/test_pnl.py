"""Tests for the canonical PnL calculation library."""

import pytest

from polyedge.trading.pnl import (
    calc_breakeven,
    calc_edge,
    calc_kelly_fraction,
    calc_pnl,
    calc_roi,
)


class TestCalcPnl:
    def test_pnl_win(self):
        """Buy at $0.80, win -> PnL = $0.20."""
        assert calc_pnl(0.80, won=True) == pytest.approx(0.20)

    def test_pnl_loss(self):
        """Buy at $0.80, lose -> PnL = -$0.80."""
        assert calc_pnl(0.80, won=False) == pytest.approx(-0.80)

    def test_pnl_cheap_win(self):
        """Buy at $0.10, win -> PnL = $0.90."""
        assert calc_pnl(0.10, won=True) == pytest.approx(0.90)

    def test_pnl_expensive_win(self):
        """Buy at $0.95, win -> PnL = $0.05."""
        assert calc_pnl(0.95, won=True) == pytest.approx(0.05)

    def test_pnl_zero_price(self):
        """Edge case: entry_price = 0."""
        assert calc_pnl(0.0, won=True) == pytest.approx(1.0)
        assert calc_pnl(0.0, won=False) == pytest.approx(0.0)


class TestCalcRoi:
    def test_roi_win(self):
        """Buy at $0.80, win -> ROI = 25%."""
        assert calc_roi(0.80, won=True) == pytest.approx(0.25)

    def test_roi_loss(self):
        """Buy at $0.80, lose -> ROI = -100%."""
        assert calc_roi(0.80, won=False) == pytest.approx(-1.0)


class TestCalcEdge:
    def test_edge_positive(self):
        """87% win rate, entry $0.80 -> edge = $0.07."""
        assert calc_edge(0.87, 0.80) == pytest.approx(0.07)

    def test_edge_zero(self):
        """80% win rate, entry $0.80 -> edge = $0.00."""
        assert calc_edge(0.80, 0.80) == pytest.approx(0.00)

    def test_edge_negative(self):
        """70% win rate, entry $0.80 -> edge = -$0.10."""
        assert calc_edge(0.70, 0.80) == pytest.approx(-0.10)


class TestCalcBreakeven:
    def test_breakeven(self):
        """87% win rate -> breakeven = $0.87."""
        assert calc_breakeven(0.87) == pytest.approx(0.87)


class TestCalcKellyFraction:
    def test_kelly_positive_edge(self):
        """Should return > 0 when edge exists."""
        f = calc_kelly_fraction(0.87, 0.80)
        assert f > 0.0

    def test_kelly_no_edge(self):
        """Should return 0 when no edge."""
        assert calc_kelly_fraction(0.70, 0.80) == 0.0
        assert calc_kelly_fraction(0.80, 0.80) == 0.0
