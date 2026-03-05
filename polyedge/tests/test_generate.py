"""Tests for prediction generation with dedupe logic."""

import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from polyedge.analysis.generate import generate_all_predictions
from polyedge.models import Market, Factor, Prediction, FactorWeight


def _make_market(market_id="mkt-001", question="Will X happen?", yes_price=0.6):
    m = MagicMock(spec=Market)
    m.id = market_id
    m.question = question
    m.yes_price = yes_price
    m.active = True
    return m


def _make_prediction_result():
    return {"predicted_outcome": "YES", "confidence": 0.7, "factor_categories": ["financial"]}


class FakeScalarsAll:
    """Helper to simulate session.execute(...).scalars().all() chains."""

    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class FakeResult:
    def __init__(self, items=None, scalar_value=None):
        self._items = items or []
        self._scalar_value = scalar_value

    def scalars(self):
        return FakeScalarsAll(self._items)

    def scalar(self):
        return self._scalar_value


class FakeSession:
    """Async context manager that tracks adds and commits."""

    def __init__(self, execute_results):
        self._execute_results = list(execute_results)
        self._call_idx = 0
        self.added = []
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def execute(self, stmt):
        result = self._execute_results[self._call_idx]
        self._call_idx += 1
        return result

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True


@pytest.mark.asyncio
@patch("polyedge.analysis.generate.make_prediction", return_value=_make_prediction_result())
@patch("polyedge.analysis.generate.SessionLocal")
async def test_generates_prediction_for_market_without_recent(mock_session_local, mock_predict):
    """A market with no recent predictions should get a new prediction."""
    market = _make_market()

    # execute order: weights, markets, dedupe count, market_factors
    session = FakeSession([
        FakeResult(items=[]),           # FactorWeight query
        FakeResult(items=[market]),     # Market query
        FakeResult(scalar_value=0),    # dedupe count = 0 (no recent prediction)
        FakeResult(items=[]),           # market-specific factors
    ])
    # Also need global factors result inserted at the right spot
    # Actual order: weights, markets, global_factors, then per-market: dedupe, market_factors
    session._execute_results = [
        FakeResult(items=[]),           # FactorWeight query
        FakeResult(items=[market]),     # Market query
        FakeResult(items=[]),           # global factors
        FakeResult(scalar_value=0),    # dedupe count = 0
        FakeResult(items=[]),           # market-specific factors
    ]
    session._call_idx = 0

    mock_session_local.return_value = session

    await generate_all_predictions()

    assert session.committed
    assert len(session.added) == 1, f"Expected 1 prediction, got {len(session.added)}"


@pytest.mark.asyncio
@patch("polyedge.analysis.generate.make_prediction", return_value=_make_prediction_result())
@patch("polyedge.analysis.generate.SessionLocal")
async def test_skips_market_with_recent_prediction(mock_session_local, mock_predict):
    """A market predicted less than 1 hour ago should be skipped."""
    market = _make_market()

    session = FakeSession([
        FakeResult(items=[]),           # FactorWeight query
        FakeResult(items=[market]),     # Market query
        FakeResult(items=[]),           # global factors
        FakeResult(scalar_value=1),    # dedupe count = 1 (recent prediction exists)
    ])
    mock_session_local.return_value = session

    await generate_all_predictions()

    assert session.committed
    assert len(session.added) == 0, "Should not add prediction for recently-predicted market"
    mock_predict.assert_not_called()


@pytest.mark.asyncio
@patch("polyedge.analysis.generate.make_prediction", return_value=_make_prediction_result())
@patch("polyedge.analysis.generate.SessionLocal")
async def test_mixed_markets_dedupe(mock_session_local, mock_predict):
    """With two markets, one recent and one not, only the stale one gets a prediction."""
    market_a = _make_market(market_id="mkt-a")
    market_b = _make_market(market_id="mkt-b")

    session = FakeSession([
        FakeResult(items=[]),                       # FactorWeight
        FakeResult(items=[market_a, market_b]),     # Markets
        FakeResult(items=[]),                       # global factors
        # market_a: recent prediction exists
        FakeResult(scalar_value=1),                 # dedupe count for mkt-a
        # market_b: no recent prediction
        FakeResult(scalar_value=0),                 # dedupe count for mkt-b
        FakeResult(items=[]),                       # market_b factors
    ])
    mock_session_local.return_value = session

    await generate_all_predictions()

    assert session.committed
    assert len(session.added) == 1, f"Expected 1 prediction (mkt-b only), got {len(session.added)}"


def test_generate_is_callable():
    assert callable(generate_all_predictions)
