"""Tests for Grok direct prediction system."""
import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# --- Task 1: Model tests ---


def test_grok_prediction_model_exists():
    from polyedge.models import GrokPrediction
    assert GrokPrediction.__tablename__ == "grok_predictions"
    assert hasattr(GrokPrediction, "market_id")
    assert hasattr(GrokPrediction, "predicted_side")
    assert hasattr(GrokPrediction, "confidence")
    assert hasattr(GrokPrediction, "reasoning")
    assert hasattr(GrokPrediction, "created_at")


# --- Task 2: Prompt builder tests ---


def test_build_prediction_prompt_includes_question():
    from polyedge.research.grok_predictor import build_prediction_prompt
    prompt = build_prediction_prompt(
        question="Will BTC exceed $100k by March 2026?",
        description="Resolves YES if Bitcoin trades above $100,000 at any point.",
        yes_price=0.65,
        no_price=0.35,
        end_date="2026-03-31 00:00 UTC",
        category="crypto_updown",
    )
    assert "Will BTC exceed $100k" in prompt
    assert "0.65" in prompt
    assert "0.35" in prompt
    assert "crypto_updown" in prompt
    assert "Resolves YES if Bitcoin" in prompt
    assert "2026-03-31" in prompt


def test_build_prediction_prompt_truncates_long_description():
    from polyedge.research.grok_predictor import build_prediction_prompt
    prompt = build_prediction_prompt(
        question="Test?",
        description="A" * 2000,
        yes_price=0.50,
        no_price=0.50,
        end_date="2026-04-01",
    )
    # Description should be truncated to 500 chars
    assert len(prompt) < 1500


# --- Task 2: Response parser tests ---


def test_parse_grok_response_yes():
    from polyedge.research.grok_predictor import parse_grok_response
    raw = '{"side": "YES", "confidence": 8, "reasoning": "Strong momentum"}'
    result = parse_grok_response(raw)
    assert result is not None
    assert result["side"] == "YES"
    assert result["confidence"] == 0.8  # scaled from 1-10 to 0-1
    assert "momentum" in result["reasoning"].lower()


def test_parse_grok_response_no():
    from polyedge.research.grok_predictor import parse_grok_response
    raw = '{"side": "NO", "confidence": 3, "reasoning": "Unlikely outcome"}'
    result = parse_grok_response(raw)
    assert result is not None
    assert result["side"] == "NO"
    assert result["confidence"] == 0.3


def test_parse_grok_response_markdown_wrapped():
    """Grok sometimes wraps JSON in markdown code fences."""
    from polyedge.research.grok_predictor import parse_grok_response
    raw = 'Here is my analysis:\n```json\n{"side": "YES", "confidence": 7, "reasoning": "test"}\n```\nHope this helps!'
    result = parse_grok_response(raw)
    assert result is not None
    assert result["side"] == "YES"
    assert result["confidence"] == 0.7


def test_parse_grok_response_lowercase_side():
    """Grok might return lowercase yes/no."""
    from polyedge.research.grok_predictor import parse_grok_response
    raw = '{"side": "yes", "confidence": 6, "reasoning": "probably"}'
    result = parse_grok_response(raw)
    assert result is not None
    assert result["side"] == "YES"


def test_parse_grok_response_confidence_as_string():
    """Grok might return confidence as '7/10' or '7'."""
    from polyedge.research.grok_predictor import parse_grok_response
    raw = '{"side": "NO", "confidence": "7", "reasoning": "test"}'
    result = parse_grok_response(raw)
    assert result is not None
    assert result["confidence"] == 0.7


def test_parse_grok_response_confidence_already_decimal():
    """If Grok returns confidence as 0.0-1.0 already, don't divide by 10."""
    from polyedge.research.grok_predictor import parse_grok_response
    raw = '{"side": "YES", "confidence": 0.85, "reasoning": "very likely"}'
    result = parse_grok_response(raw)
    assert result is not None
    assert result["confidence"] == 0.85


def test_parse_grok_response_garbage():
    from polyedge.research.grok_predictor import parse_grok_response
    assert parse_grok_response("I cannot predict this market") is None
    assert parse_grok_response("") is None
    assert parse_grok_response(None) is None


def test_parse_grok_response_invalid_side():
    from polyedge.research.grok_predictor import parse_grok_response
    raw = '{"side": "MAYBE", "confidence": 5, "reasoning": "unsure"}'
    assert parse_grok_response(raw) is None


def test_parse_grok_response_nested_json():
    """Grok might include nested objects -- parser should still extract side/confidence."""
    from polyedge.research.grok_predictor import parse_grok_response
    raw = '{"side": "YES", "confidence": 8, "reasoning": "test", "details": {"source": "news"}}'
    result = parse_grok_response(raw)
    assert result is not None
    assert result["side"] == "YES"


# --- Task 3: Async generator tests ---


def _make_fake_market(market_id="m1", question="Will X happen?", description="Resolves YES if X.",
                      yes_price=0.6, no_price=0.4, volume=1000, category="other"):
    m = MagicMock()
    m.id = market_id
    m.question = question
    m.description = description
    m.yes_price = yes_price
    m.no_price = no_price
    m.volume = volume
    m.market_category = category
    m.category = category
    m.end_date = datetime.utcnow() + timedelta(days=2)
    m.active = True
    return m


def _mock_session_factory(fake_markets, recent_market_ids):
    """Create a mock SessionLocal that returns proper query results.

    Returns (mock_session_factory_callable, mock_session) where mock_session
    is the inner session used for assertions (add.call_count, etc.).
    """
    # We need separate sessions: one for the read phase, one for the commit phase
    # The code does: async with SessionLocal() as session (read), then
    # async with SessionLocal() as session (commit batch)
    # So SessionLocal is called multiple times.

    read_session = AsyncMock()
    mock_markets_result = MagicMock()
    mock_markets_result.scalars.return_value.all.return_value = fake_markets
    mock_recent_result = MagicMock()
    mock_recent_result.scalars.return_value.all.return_value = recent_market_ids

    read_session.execute = AsyncMock(side_effect=[mock_markets_result, mock_recent_result])

    # Commit session (shared for assertions)
    commit_session = AsyncMock()
    commit_session.add = MagicMock()
    commit_session.commit = AsyncMock()
    # commit session also needs execute in case the code reuses it
    commit_session.execute = AsyncMock(return_value=MagicMock())

    call_idx = {"n": 0}

    def session_factory():
        ctx = AsyncMock()
        if call_idx["n"] == 0:
            ctx.__aenter__ = AsyncMock(return_value=read_session)
        else:
            ctx.__aenter__ = AsyncMock(return_value=commit_session)
        ctx.__aexit__ = AsyncMock(return_value=False)
        call_idx["n"] += 1
        return ctx

    return session_factory, commit_session


def _patch_generate(session_factory, grok_side_effect_or_value):
    """Return a context manager that patches all module-level globals for generate_grok_predictions."""
    from polyedge.models import Market as RealMarket, GrokPrediction as RealGrokPrediction

    patches = [
        patch("polyedge.research.grok_predictor.SessionLocal", side_effect=session_factory),
        patch("polyedge.research.grok_predictor.Market", RealMarket),
        patch("polyedge.research.grok_predictor.GrokPrediction", RealGrokPrediction),
    ]
    if callable(grok_side_effect_or_value) and not isinstance(grok_side_effect_or_value, str):
        patches.append(patch("polyedge.research.grok_predictor.query_grok", side_effect=grok_side_effect_or_value))
    else:
        patches.append(patch("polyedge.research.grok_predictor.query_grok", new_callable=AsyncMock, return_value=grok_side_effect_or_value))

    from contextlib import ExitStack
    stack = ExitStack()
    for p in patches:
        stack.enter_context(p)
    return stack


@pytest.mark.asyncio
async def test_generate_grok_predictions_calls_grok_per_market():
    """Mock DB + Grok API, verify predictions are created for each market."""
    from polyedge.research.grok_predictor import generate_grok_predictions

    fake_markets = [_make_fake_market(f"m{i}") for i in range(3)]
    grok_response = '{"side": "YES", "confidence": 7, "reasoning": "likely"}'

    session_factory, commit_session = _mock_session_factory(fake_markets, [])

    with _patch_generate(session_factory, grok_response):
        count = await generate_grok_predictions(cooldown_hours=6, max_concurrent=2, max_markets=10)

    assert count == 3
    assert commit_session.add.call_count == 3


@pytest.mark.asyncio
async def test_generate_grok_predictions_skips_recently_predicted():
    """Markets predicted within cooldown window should be skipped."""
    from polyedge.research.grok_predictor import generate_grok_predictions

    fake_markets = [_make_fake_market("m1"), _make_fake_market("m2")]
    grok_response = '{"side": "NO", "confidence": 6, "reasoning": "test"}'

    # m1 was predicted recently -- should be skipped
    session_factory, commit_session = _mock_session_factory(fake_markets, ["m1"])

    with _patch_generate(session_factory, grok_response):
        count = await generate_grok_predictions(cooldown_hours=6, max_concurrent=2, max_markets=10)

    # Only m2 should be predicted (m1 was in cooldown)
    assert count == 1
    assert commit_session.add.call_count == 1


@pytest.mark.asyncio
async def test_generate_grok_predictions_handles_api_failure():
    """If Grok API fails for a market, skip it and continue."""
    from polyedge.research.grok_predictor import generate_grok_predictions

    fake_markets = [_make_fake_market("m1"), _make_fake_market("m2")]

    session_factory, commit_session = _mock_session_factory(fake_markets, [])

    call_count = 0
    async def _flaky_grok(prompt):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ConnectionError("Grok is down")
        return '{"side": "YES", "confidence": 8, "reasoning": "test"}'

    with _patch_generate(session_factory, _flaky_grok):
        count = await generate_grok_predictions(cooldown_hours=6, max_concurrent=2, max_markets=10)

    # m1 fails, m2 succeeds
    assert count == 1
