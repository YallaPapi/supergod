from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import inspect
from datetime import datetime
import pytest

from polyedge.scheduler import (
    run_forever,
    run_factor_match_paper_trading,
    run_paper_trading,
    score_paper_trades,
)


def _scalars_result(rows):
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = rows
    result.scalars.return_value = scalars
    return result


def _rows_result(rows):
    result = MagicMock()
    result.all.return_value = rows
    return result


def test_score_paper_trades_interval_is_5_minutes():
    source = inspect.getsource(run_forever)
    assert 'loop(score_paper_trades, 300, "score_paper_trades"' in source


@pytest.mark.asyncio
async def test_run_paper_trading_skips_expired_market():
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.add = MagicMock()

    rule = SimpleNamespace(
        id=1,
        conditions_json='{"ngram":"tournament"}',
        predicted_side="NO",
        win_rate=0.8,
        breakeven_price=0.8,
        tier=1,
        sample_size=800,
        active=True,
        rule_type="ngram",
        market_filter="",
    )
    expired_market = SimpleNamespace(
        id="m1",
        question="Will this tournament settle soon?",
        end_date=datetime(2025, 1, 1, 0, 0, 0),
        yes_price=0.40,
        no_price=0.60,
        active=True,
    )

    # Force deterministic datetime comparison by patching _utcnow_naive.
    now = datetime(2026, 1, 1, 0, 0, 0)

    session.execute = AsyncMock(side_effect=[
        _scalars_result([rule]),     # rules
        _scalars_result([expired_market]),  # markets
        _rows_result([]),            # open ngram paper-trade keys
        _rows_result([]),            # open ngram_inverse paper-trade keys
    ])

    with patch("polyedge.scheduler.SessionLocal", return_value=session):
        with patch("polyedge.scheduler._utcnow_naive", return_value=now):
            await run_paper_trading()

    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_score_paper_trades_scores_joined_rows():
    trade = SimpleNamespace(
        side="YES",
        entry_price=0.55,
        won=None,
        pnl=None,
        resolved=False,
        resolved_at=None,
    )

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock(return_value=_rows_result([(trade, "YES")]))
    session.commit = AsyncMock()

    with patch("polyedge.scheduler.SessionLocal", return_value=session):
        with patch("polyedge.scheduler._utcnow_naive", return_value=datetime(2026, 1, 1, 0, 0, 0)):
            with patch("polyedge.trading.pnl.calc_pnl", return_value=0.45):
                await score_paper_trades()

    assert trade.won is True
    assert trade.pnl == 0.45
    assert trade.resolved is True
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_score_paper_trades_void_resolution_marks_resolved_without_loss():
    trade = SimpleNamespace(
        side="YES",
        entry_price=0.42,
        won=None,
        pnl=None,
        resolved=False,
        resolved_at=None,
    )

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock(return_value=_rows_result([(trade, "CANCELLED")]))
    session.commit = AsyncMock()

    with patch("polyedge.scheduler.SessionLocal", return_value=session):
        with patch("polyedge.scheduler._utcnow_naive", return_value=datetime(2026, 1, 1, 0, 0, 0)):
            with patch("polyedge.trading.pnl.calc_pnl") as calc_mock:
                await score_paper_trades()

    calc_mock.assert_not_called()
    assert trade.won is None
    assert trade.pnl == 0.0
    assert trade.resolved is True
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_factor_match_paper_trading_trades_crypto_updown_markets():
    """crypto_updown markets are now traded (no longer skipped)."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.add = MagicMock()
    session.commit = AsyncMock()

    pred = SimpleNamespace(
        id="p1",
        market_id="m1",
        predicted_outcome="YES",
        confidence=0.9,
    )
    market = SimpleNamespace(
        id="m1",
        active=True,
        end_date=datetime(2026, 1, 1, 1, 0, 0),
        yes_price=0.3,
        no_price=0.7,
        question="Will BTC go up or down in the next 5 minutes?",
        market_category="crypto_updown",
    )

    session.execute = AsyncMock(side_effect=[
        _rows_result([(pred, market)]),  # prediction rows
        _rows_result([]),                # existing open factor_match trades
        _rows_result([]),                # existing open factor_match_inv trades
    ])

    with patch("polyedge.scheduler.SessionLocal", return_value=session):
        with patch("polyedge.scheduler._utcnow_naive", return_value=datetime(2026, 1, 1, 0, 0, 0)):
            await run_factor_match_paper_trading()

    # crypto_updown should now be traded, not skipped
    assert session.add.call_count >= 1, "crypto_updown market should produce trades"
    session.commit.assert_awaited_once()
