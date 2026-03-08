"""Tests for scorer — verify no label contamination from inferred resolution."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from polyedge.analysis.scorer import (
    _parse_metrics_cutoff,
    _parse_resolution_sources,
    score_resolved_markets,
)


def _make_market(id="m1", resolution=None, yes_price=0.5, no_price=0.5, end_date=None):
    m = MagicMock()
    m.id = id
    m.resolution = resolution
    m.yes_price = yes_price
    m.no_price = no_price
    m.end_date = end_date
    return m


def _make_prediction(id="p1", market_id="m1", predicted_outcome="YES", correct=None):
    p = MagicMock()
    p.id = id
    p.market_id = market_id
    p.predicted_outcome = predicted_outcome
    p.correct = correct
    p.resolved_at = None
    return p


@pytest.fixture
def mock_session():
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


@pytest.mark.asyncio
async def test_scores_market_with_explicit_resolution(mock_session):
    """Markets with explicit resolution should be scored correctly."""
    market = _make_market(resolution="YES")
    pred = _make_prediction(predicted_outcome="YES")

    result_mock = MagicMock()
    result_mock.all.return_value = [(pred, market)]
    mock_session.execute = AsyncMock(return_value=result_mock)

    with patch("polyedge.analysis.scorer.SessionLocal", return_value=mock_session):
        with patch("polyedge.analysis.scorer.recalculate_weights", new_callable=AsyncMock):
            await score_resolved_markets()

    assert pred.correct is True
    assert pred.resolved_at is not None


@pytest.mark.asyncio
async def test_correct_false_when_prediction_wrong(mock_session):
    """Prediction that doesn't match resolution should be marked incorrect."""
    market = _make_market(resolution="NO")
    pred = _make_prediction(predicted_outcome="YES")

    result_mock = MagicMock()
    result_mock.all.return_value = [(pred, market)]
    mock_session.execute = AsyncMock(return_value=result_mock)

    with patch("polyedge.analysis.scorer.SessionLocal", return_value=mock_session):
        with patch("polyedge.analysis.scorer.recalculate_weights", new_callable=AsyncMock):
            await score_resolved_markets()

    assert pred.correct is False


@pytest.mark.asyncio
async def test_no_results_returns_early(mock_session):
    """When no unscored predictions exist, function returns without error."""
    result_mock = MagicMock()
    result_mock.all.return_value = []
    mock_session.execute = AsyncMock(return_value=result_mock)

    with patch("polyedge.analysis.scorer.SessionLocal", return_value=mock_session):
        with patch("polyedge.analysis.scorer.recalculate_weights", new_callable=AsyncMock) as mock_recalc:
            await score_resolved_markets()

    mock_session.commit.assert_not_called()
    mock_recalc.assert_not_called()


@pytest.mark.asyncio
async def test_resolution_field_never_modified_by_scorer(mock_session):
    """The scorer must NEVER write back to market.resolution."""
    market = _make_market(resolution="YES")
    pred = _make_prediction(predicted_outcome="YES")

    # Track all attribute sets on market
    resolution_writes = []
    original_setattr = type(market).__setattr__

    def tracking_setattr(self, name, value):
        if name == "resolution":
            resolution_writes.append(value)
        original_setattr(self, name, value)

    result_mock = MagicMock()
    result_mock.all.return_value = [(pred, market)]
    mock_session.execute = AsyncMock(return_value=result_mock)

    with patch("polyedge.analysis.scorer.SessionLocal", return_value=mock_session):
        with patch("polyedge.analysis.scorer.recalculate_weights", new_callable=AsyncMock):
            with patch.object(type(market), "__setattr__", tracking_setattr):
                await score_resolved_markets()

    assert resolution_writes == [], (
        f"Scorer wrote to market.resolution: {resolution_writes}"
    )


@pytest.mark.asyncio
async def test_high_price_no_resolution_not_scored(mock_session):
    """Markets with yes_price=0.99 but no resolution must NOT be scored.

    The SQL query filters out markets where resolution is None,
    so they should never appear in results. This test verifies the
    old _infer_resolution behavior is gone.
    """
    # These markets should never reach the scorer loop because
    # the SQL WHERE clause filters them out. We verify the function
    # doesn't contain _infer_resolution by checking the module.
    import polyedge.analysis.scorer as scorer_mod
    assert not hasattr(scorer_mod, "_infer_resolution"), (
        "_infer_resolution still exists in scorer module — label contamination risk"
    )


@pytest.mark.asyncio
async def test_past_end_date_high_price_no_resolution_not_scored(mock_session):
    """Markets past end_date with high price but no resolution must NOT be scored."""
    import polyedge.analysis.scorer as scorer_mod
    # The old code would infer resolution for markets past end_date with
    # yes_price >= 0.8. Verify that code path is gone.
    import inspect
    source = inspect.getsource(scorer_mod.score_resolved_markets)
    assert "_infer_resolution" not in source
    assert "0.95" not in source
    assert "0.8" not in source
    assert "resolution_source" in source


@pytest.mark.asyncio
async def test_multiple_predictions_scored(mock_session):
    """Multiple predictions across different markets all get scored."""
    m1 = _make_market(id="m1", resolution="YES")
    m2 = _make_market(id="m2", resolution="NO")
    p1 = _make_prediction(id="p1", market_id="m1", predicted_outcome="YES")
    p2 = _make_prediction(id="p2", market_id="m2", predicted_outcome="YES")

    result_mock = MagicMock()
    result_mock.all.return_value = [(p1, m1), (p2, m2)]
    mock_session.execute = AsyncMock(return_value=result_mock)

    with patch("polyedge.analysis.scorer.SessionLocal", return_value=mock_session):
        with patch("polyedge.analysis.scorer.recalculate_weights", new_callable=AsyncMock):
            await score_resolved_markets()

    assert p1.correct is True
    assert p2.correct is False
    mock_session.commit.assert_called_once()


def test_parse_metrics_cutoff_empty():
    assert _parse_metrics_cutoff("") is None
    assert _parse_metrics_cutoff(None) is None


def test_parse_metrics_cutoff_iso_z():
    cutoff = _parse_metrics_cutoff("2026-03-06T00:00:00Z")
    assert isinstance(cutoff, datetime)
    assert cutoff.year == 2026
    assert cutoff.month == 3
    assert cutoff.day == 6


def test_parse_metrics_cutoff_invalid_returns_none():
    assert _parse_metrics_cutoff("not-a-date") is None


def test_parse_resolution_sources_defaults():
    assert _parse_resolution_sources("") == {"polymarket_api"}
    assert _parse_resolution_sources(None) == {"polymarket_api"}


def test_parse_resolution_sources_csv_and_wildcard():
    assert _parse_resolution_sources("polymarket_api,manual_override") == {
        "polymarket_api", "manual_override",
    }
    assert _parse_resolution_sources("*") == {"*"}


def test_score_category_math():
    from polyedge.analysis.scorer import score_category
    result = score_category(correct=60, total=100)
    assert result["hit_rate"] == 0.6
    assert result["total_predictions"] == 100
    assert result["correct_predictions"] == 60
    # weight for 60% hit rate: 1.0 + (0.6 - 0.5) * 4 = 1.4
    assert abs(result["weight"] - 1.4) < 0.01


def test_score_category_below_50():
    from polyedge.analysis.scorer import score_category
    result = score_category(correct=40, total=100)
    assert result["weight"] == 0.1  # <= 50% gets downweighted
