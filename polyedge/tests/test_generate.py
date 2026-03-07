"""Tests for prediction generation."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from polyedge.analysis.generate import _predict_from_factors, generate_all_predictions


def _scalars_result(rows):
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = rows
    result.scalars.return_value = scalars
    return result


def _scalar_one_result(row):
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    return result


@pytest.mark.asyncio
async def test_generate_creates_prediction_when_market_has_factors_and_no_recent_prediction():
    session = AsyncMock()
    session.add = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    market = SimpleNamespace(id="m1", yes_price=0.55, active=True)
    factor = SimpleNamespace(
        id="f1",
        category="weather",
        value="storm",
        confidence=0.8,
        market_id="m1",
    )
    weight = SimpleNamespace(category="weather", weight=1.5)

    session.execute = AsyncMock(side_effect=[
        _scalars_result([weight]),
        _scalars_result([market]),
        _scalars_result([]),
        _scalars_result([factor]),
    ])

    with patch("polyedge.analysis.generate.SessionLocal", return_value=session):
        created = await generate_all_predictions(cooldown_minutes=60)

    assert created == 1
    session.add.assert_called_once()
    added = session.add.call_args[0][0]
    assert added.market_id == "m1"
    assert added.factor_categories
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_skips_market_when_recent_prediction_exists():
    session = AsyncMock()
    session.add = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    market = SimpleNamespace(id="m1", yes_price=0.55, active=True)
    recent_prediction = SimpleNamespace(
        created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5)
    )

    session.execute = AsyncMock(side_effect=[
        _scalars_result([]),
        _scalars_result([market]),
        _scalars_result([market.id]),
        _scalars_result([]),
    ])

    with patch("polyedge.analysis.generate.SessionLocal", return_value=session):
        created = await generate_all_predictions(cooldown_minutes=60)

    assert created == 0
    session.add.assert_not_called()
    session.commit.assert_awaited_once()


def test_predict_from_factors_neutral_values_do_not_bias_yes_direction():
    factors = [
        SimpleNamespace(id=f"f{i}", category="Social media", value="neutral", confidence=0.95)
        for i in range(5)
    ]
    signal = _predict_from_factors(
        factors=factors,
        market_yes_price=0.2,
        weights={"social media": 1.0},
    )
    # With neutral evidence, prediction should stay aligned with prior (< 0.5 => NO side).
    assert signal["predicted_outcome"] == "NO"


def test_predict_from_factors_uses_value_direction_not_confidence_alone():
    bullish = SimpleNamespace(id="b1", category="sentiment", value="bullish", confidence=0.9)
    bearish = SimpleNamespace(id="b2", category="sentiment", value="bearish", confidence=0.9)

    bull_signal = _predict_from_factors(
        factors=[bullish],
        market_yes_price=0.5,
        weights={"sentiment": 1.0},
    )
    bear_signal = _predict_from_factors(
        factors=[bearish],
        market_yes_price=0.5,
        weights={"sentiment": 1.0},
    )

    assert bull_signal["predicted_outcome"] == "YES"
    assert bear_signal["predicted_outcome"] == "NO"


def test_predict_from_factors_normalizes_categories_to_lowercase():
    factors = [
        SimpleNamespace(id="f1", category="Social media", value="bullish", confidence=0.8),
        SimpleNamespace(id="f2", category="social media", value="bearish", confidence=0.8),
    ]
    signal = _predict_from_factors(
        factors=factors,
        market_yes_price=0.5,
        weights={"social media": 1.0},
    )
    assert signal["factor_categories"] == ["social media"]
