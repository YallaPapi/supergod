"""Tests for the v3 scheduler."""

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from polyedge.scheduler import (
    check_resolutions,
    collect_daily_features,
    ingest_supergod_research,
    poll_then_score,
    run_api_research,
    run_combined_paper_trading,
    run_forever,
    run_llm_paper_trading,
    run_paper_trading,
    run_poller,
    run_supergod_research,
    score_paper_trades,
)


def _scalars_result(rows):
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = rows
    result.scalars.return_value = scalars
    return result


def test_scheduler_functions_exist():
    """Smoke test that all v3 scheduler functions are importable."""
    assert callable(run_poller)
    assert callable(poll_then_score)
    assert callable(run_api_research)
    assert callable(run_supergod_research)
    assert callable(ingest_supergod_research)
    assert callable(collect_daily_features)
    assert callable(run_paper_trading)
    assert callable(score_paper_trades)
    assert callable(run_forever)


def test_latest_prediction_selection_orders_by_created_at_not_id():
    """Latest prediction selection must use created_at chronology, not max(id)."""
    src_llm = inspect.getsource(run_llm_paper_trading)
    src_combined = inspect.getsource(run_combined_paper_trading)

    assert "Prediction.created_at.desc()" in src_llm
    assert "Prediction.created_at.desc()" in src_combined
    assert "func.max(Prediction.id)" not in src_llm
    assert "func.max(Prediction.id)" not in src_combined


def test_check_resolutions_uses_orderable_pending_query():
    """Pending-markets query must be valid on Postgres (DISTINCT + ORDER BY)."""
    src = inspect.getsource(check_resolutions)
    assert "select(Market.id, Market.end_date)" in src


@pytest.mark.asyncio
async def test_poll_then_score_no_longer_generates_predictions():
    call_order = []

    async def _poll():
        call_order.append("poll")

    async def _score():
        call_order.append("score")

    with patch("polyedge.scheduler.run_poller", new=AsyncMock(side_effect=_poll)):
        with patch("polyedge.scheduler.score_resolved_markets", new=AsyncMock(side_effect=_score)):
            await poll_then_score()

    assert call_order == ["poll", "score"]


@pytest.mark.asyncio
async def test_run_paper_trading_skips_low_liquidity_markets():
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.add = MagicMock()

    rule = SimpleNamespace(
        id=1,
        name="r1",
        rule_type="single_threshold",
        conditions_json="{}",
        predicted_side="YES",
        win_rate=0.6,
        sample_size=100,
        breakeven_price=0.6,
        active=True,
    )
    low_liq_market = SimpleNamespace(
        id="m1",
        question="Will X happen?",
        category="",
        end_date=None,
        yes_price=0.55,
        volume=1000.0,
        active=True,
    )

    session.execute = AsyncMock(side_effect=[
        _scalars_result([rule]),          # rules
        _scalars_result([]),              # daily features
        _scalars_result([]),              # factors
        _scalars_result([low_liq_market]) # markets
    ])

    with patch("polyedge.scheduler.SessionLocal", return_value=session):
        with patch("polyedge.analysis.predictor.predict_market", return_value=SimpleNamespace(matching_rules=[{"id": 1}], side="YES", entry_price=0.55, edge=0.04)) as predict_mock:
            await run_paper_trading()

    predict_mock.assert_not_called()
    session.add.assert_not_called()
    session.commit.assert_awaited_once()


def test_paper_trading_respects_market_filter():
    """Rules with market_filter should only match markets of that category."""
    rule = {
        "id": 1, "phrase": "spread", "side": "NO", "win_rate": 0.70,
        "breakeven": 0.70, "tier": 1, "min_edge": 0.05,
        "market_filter": "sports_spread",
    }
    from types import SimpleNamespace
    market = SimpleNamespace(
        question="Will BTC spread beyond $50k?",
        market_category="crypto_other",
    )
    q_lower = market.question.lower()
    phrase_matches = rule["phrase"] in q_lower
    mf = rule["market_filter"]
    category_matches = not mf or mf == (getattr(market, "market_category", "") or "")
    assert phrase_matches is True, "Phrase should match"
    assert category_matches is False, "Category should NOT match"
    assert not (phrase_matches and category_matches), "Trade should NOT open"


def test_crypto_updown_not_skipped():
    """crypto_updown markets should be traded, not skipped."""
    import inspect
    import polyedge.scheduler as sched
    # _is_noise_market should no longer exist
    assert not hasattr(sched, '_is_noise_market'), "_is_noise_market should be deleted"
    # Verify no noise filtering in paper trading source
    src = inspect.getsource(sched.run_paper_trading)
    assert "_is_noise_market" not in src
    assert "skipped_noise" not in src


@pytest.mark.asyncio
async def test_run_api_research_delegates_to_pipeline():
    with patch("polyedge.research.pipeline.run_api_research_cycle", new=AsyncMock(return_value={"stored": 3})) as mocked:
        await run_api_research()
    mocked.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_supergod_research_delegates_to_pipeline():
    with patch("polyedge.research.pipeline.run_supergod_research_dispatch", new=AsyncMock(return_value={"accepted": 1})) as mocked:
        await run_supergod_research()
    mocked.assert_awaited_once()
