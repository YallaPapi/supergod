# Real Grok Predictions + Rename Factor-Match System

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add REAL LLM predictions (ask Grok "would you bet YES or NO on this market?") and rename the existing factor-string-matching system to what it actually is.

**Architecture:** New `grok_predictor.py` module asks Grok directly about each market, stores predictions in a new `GrokPrediction` table, and a new scheduler loop places paper trades (both direct + inverse) from those predictions. The existing factor-based system gets renamed from "llm"→"factor_match" everywhere (DB, code, dashboard). Both systems run in parallel — the old one stays because its inverse is profitable.

**Tech Stack:** Python 3.12, SQLAlchemy async, httpx, Grok API (xAI grok-3-mini), PostgreSQL, FastAPI dashboard

---

## Context for the Implementer

### What exists today (and what's wrong with it)

The system currently has these paper trading sources:
- `ngram` / `ngram_inverse` — historical text pattern mining (real data-driven, works well)
- `llm` / `llm_inverse` — **misleadingly named**. This does NOT ask an LLM for predictions. It: (1) asks Grok general questions about topics, (2) gets text "factors" back, (3) string-matches factors against market questions, (4) does local math to produce YES/NO. It's factor-based string matching, not LLM predictions.
- `combined` / `combined_inverse` — when ngram + factor-match agree

### What we're building

1. **Rename** `llm`→`factor_match` and `llm_inverse`→`factor_match_inv` everywhere
2. **Real Grok predictions**: For each active market, ask Grok: "Here's the market question, here are the odds. Would you bet YES or NO?" Store the answer. Place both direct and inverse paper trades.
3. Both systems continue running in parallel

### Key files

| File | Purpose |
|------|---------|
| `polyedge/src/polyedge/models.py` | SQLAlchemy models — add GrokPrediction |
| `polyedge/src/polyedge/research/grok.py` | Grok API client — `query_grok()` |
| `polyedge/src/polyedge/research/grok_predictor.py` | **NEW** — ask Grok YES/NO per market |
| `polyedge/src/polyedge/scheduler.py` | Scheduler loops — rename + new loop |
| `polyedge/src/polyedge/app.py` | Dashboard API — rename trade sources |
| `polyedge/src/polyedge/static/dashboard.html` | Dashboard UI — rename labels |

### API cost estimate

- ~2,000 markets ending within 7 days
- grok-3-mini: ~$0.30/1M input, ~$0.50/1M output
- ~400 tokens per market prompt (including description), ~50 tokens response
- 2,000 × 450 tokens = 900k tokens ≈ **$0.60/cycle**
- With 6-hour cooldown between re-predictions: ~4 cycles/day = **~$2.40/day**

### Database

- PostgreSQL on 89.167.99.187 (db=polyedge, user=polyedge, pass=polyedge)
- Connect string: `postgresql+asyncpg://polyedge:polyedge@89.167.99.187:5432/polyedge`
- PaperTrade.trade_source is `String(20)` — max 20 chars

### Deployment ordering (IMPORTANT)

The rename must happen in this order to avoid downtime:
1. Deploy NEW code that understands BOTH old names ("llm") and new names ("factor_match")
2. Run DB migration to rename existing rows
3. Remove old-name support from code

Since this is paper trading (not real money), we simplify: deploy code with new names, run DB migration, restart scheduler. A few minutes of mismatch is fine.

---

## Task 1: Add GrokPrediction model + create DB table

**Files:**
- Modify: `polyedge/src/polyedge/models.py` (after `Prediction` class, ~line 69)
- Test: `polyedge/tests/test_grok_predictor.py` (create new)

**Step 1: Write the failing test**

Create `polyedge/tests/test_grok_predictor.py`:

```python
"""Tests for Grok direct prediction system."""
import pytest


def test_grok_prediction_model_exists():
    from polyedge.models import GrokPrediction
    assert GrokPrediction.__tablename__ == "grok_predictions"
    assert hasattr(GrokPrediction, "market_id")
    assert hasattr(GrokPrediction, "predicted_side")
    assert hasattr(GrokPrediction, "confidence")
    assert hasattr(GrokPrediction, "reasoning")
    assert hasattr(GrokPrediction, "created_at")
```

**Step 2: Run test to verify it fails**

Run: `cd C:\Users\asus\Desktop\projects\supergod && python -m pytest polyedge/tests/test_grok_predictor.py::test_grok_prediction_model_exists -v`
Expected: FAIL with `ImportError: cannot import name 'GrokPrediction'`

**Step 3: Add the model to models.py**

In `polyedge/src/polyedge/models.py`, add after the `Prediction` class (after line 68):

```python
class GrokPrediction(Base):
    """Direct Grok YES/NO prediction for a market.

    Unlike the factor-based Prediction table, this stores Grok's actual
    answer to 'would you bet YES or NO on this market?'
    """
    __tablename__ = "grok_predictions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_id: Mapped[str] = mapped_column(String, ForeignKey("markets.id"), index=True)
    predicted_side: Mapped[str] = mapped_column(String(3))  # YES or NO
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    reasoning: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    __table_args__ = (
        Index("ix_grok_pred_market_ts", "market_id", "created_at"),
    )
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest polyedge/tests/test_grok_predictor.py::test_grok_prediction_model_exists -v`
Expected: PASS

**Step 5: Create the table in the database**

```bash
ssh root@89.167.99.187 "PGPASSWORD=polyedge psql -h localhost -U polyedge -d polyedge <<'SQL'
CREATE TABLE IF NOT EXISTS grok_predictions (
    id SERIAL PRIMARY KEY,
    market_id VARCHAR REFERENCES markets(id),
    predicted_side VARCHAR(3) NOT NULL,
    confidence FLOAT DEFAULT 0.5,
    reasoning TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_grok_pred_market_ts ON grok_predictions(market_id, created_at);
CREATE INDEX IF NOT EXISTS ix_grok_pred_created ON grok_predictions(created_at);
SQL"
```

Verify:
```bash
ssh root@89.167.99.187 "PGPASSWORD=polyedge psql -h localhost -U polyedge -d polyedge -c '\d grok_predictions'"
```
Expected: table with 6 columns (id, market_id, predicted_side, confidence, reasoning, created_at)

**Step 6: Commit**

```bash
git add polyedge/src/polyedge/models.py polyedge/tests/test_grok_predictor.py
git commit -m "feat: add GrokPrediction model for direct LLM predictions"
```

---

## Task 2: Build grok_predictor.py — prompt builder + response parser

**Files:**
- Create: `polyedge/src/polyedge/research/grok_predictor.py`
- Modify: `polyedge/tests/test_grok_predictor.py`

**Step 1: Write the failing tests**

Add to `polyedge/tests/test_grok_predictor.py`:

```python
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
    """Grok might include nested objects — parser should still extract side/confidence."""
    from polyedge.research.grok_predictor import parse_grok_response
    raw = '{"side": "YES", "confidence": 8, "reasoning": "test", "details": {"source": "news"}}'
    result = parse_grok_response(raw)
    assert result is not None
    assert result["side"] == "YES"
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest polyedge/tests/test_grok_predictor.py -v -k "parse or build"`
Expected: FAIL with `ModuleNotFoundError: No module named 'polyedge.research.grok_predictor'`

**Step 3: Create grok_predictor.py**

Create `polyedge/src/polyedge/research/grok_predictor.py`:

```python
"""Ask Grok directly: would you bet YES or NO on this market?

This is a REAL LLM prediction — Grok sees the market question, the current
odds, and decides which side it would bet on.  No string matching, no factor
math.  Just: "here's the bet, what would you do?"
"""
from __future__ import annotations

import json
import logging
import re

log = logging.getLogger(__name__)

_MAX_DESCRIPTION_LEN = 500


def build_prediction_prompt(
    *,
    question: str,
    description: str = "",
    yes_price: float,
    no_price: float,
    end_date: str,
    category: str = "",
) -> str:
    """Build a prompt that asks Grok to make a direct market prediction."""
    desc_block = ""
    if description:
        truncated = description[:_MAX_DESCRIPTION_LEN]
        desc_block = f"\nResolution criteria: {truncated}\n"

    return f"""You are a prediction market trader deciding how to bet $1.

Market question: {question}
Category: {category or "general"}{desc_block}
Current prices:
  YES: ${yes_price:.2f} (pay ${yes_price:.2f}, profit ${1-yes_price:.2f} if YES wins)
  NO:  ${no_price:.2f} (pay ${no_price:.2f}, profit ${1-no_price:.2f} if NO wins)
Market closes: {end_date}

Would you bet YES or NO? Consider the odds, the question, and what you know.

Reply with ONLY this JSON object:
{{"side": "YES", "confidence": 7, "reasoning": "one sentence"}}

Rules:
- "side" must be exactly "YES" or exactly "NO"
- "confidence" is 1-10 (1=pure guess, 10=certain)
- No markdown, no explanation outside the JSON"""


def parse_grok_response(raw: str | None) -> dict | None:
    """Parse Grok's YES/NO prediction from its response text.

    Returns dict with keys: side, confidence (0.0-1.0), reasoning.
    Returns None if unparseable.
    """
    if not raw:
        return None

    cleaned = raw.strip()

    # Try markdown code fence first
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1)
    else:
        # Find the last JSON object (handles nested objects better than [^{}]*)
        # Use greedy match from first { to last }
        brace_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if brace_match:
            cleaned = brace_match.group(0)

    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        log.warning("Could not parse Grok prediction JSON: %.200s", raw)
        return None

    if not isinstance(data, dict):
        return None

    side = str(data.get("side", "")).upper().strip()
    if side not in ("YES", "NO"):
        log.warning("Invalid side in Grok prediction: %r", data.get("side"))
        return None

    # Confidence: Grok returns 1-10, we normalize to 0.0-1.0.
    # If already 0.0-1.0, keep as-is.
    raw_conf_str = str(data.get("confidence", "5")).strip()
    # Handle "7/10" format
    slash_match = re.match(r"(\d+(?:\.\d+)?)\s*/\s*10", raw_conf_str)
    if slash_match:
        raw_conf = float(slash_match.group(1))
    else:
        try:
            raw_conf = float(raw_conf_str)
        except (ValueError, TypeError):
            raw_conf = 5.0

    if raw_conf > 1.0:
        confidence = raw_conf / 10.0
    else:
        confidence = raw_conf
    confidence = max(0.0, min(1.0, confidence))

    reasoning = str(data.get("reasoning", ""))[:500]

    return {
        "side": side,
        "confidence": round(confidence, 4),
        "reasoning": reasoning,
    }
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest polyedge/tests/test_grok_predictor.py -v -k "parse or build"`
Expected: all 12 tests PASS

**Step 5: Commit**

```bash
git add polyedge/src/polyedge/research/grok_predictor.py polyedge/tests/test_grok_predictor.py
git commit -m "feat: add grok_predictor with prompt builder and response parser"
```

---

## Task 3: Add the async prediction generator to grok_predictor.py

**Files:**
- Modify: `polyedge/src/polyedge/research/grok_predictor.py`
- Modify: `polyedge/tests/test_grok_predictor.py`

**Step 1: Write the failing test**

Add to `polyedge/tests/test_grok_predictor.py`:

```python
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timedelta


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


@pytest.mark.asyncio
async def test_generate_grok_predictions_calls_grok_per_market():
    """Mock DB + Grok API, verify predictions are created for each market."""
    from polyedge.research.grok_predictor import generate_grok_predictions

    fake_markets = [_make_fake_market(f"m{i}") for i in range(3)]
    grok_response = '{"side": "YES", "confidence": 7, "reasoning": "likely"}'

    # We need to mock SessionLocal, the DB queries, and query_grok
    mock_session = AsyncMock()

    # Mock: markets query returns our fake markets
    mock_markets_result = MagicMock()
    mock_markets_result.scalars.return_value.all.return_value = fake_markets

    # Mock: recent predictions query returns empty (no cooldown hits)
    mock_recent_result = MagicMock()
    mock_recent_result.scalars.return_value.all.return_value = []

    mock_session.execute = AsyncMock(side_effect=[mock_markets_result, mock_recent_result])
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()

    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("polyedge.research.grok_predictor.SessionLocal", return_value=mock_session_ctx), \
         patch("polyedge.research.grok_predictor.query_grok", new_callable=AsyncMock, return_value=grok_response):
        count = await generate_grok_predictions(cooldown_hours=6, max_concurrent=2, max_markets=10)

    assert count == 3
    assert mock_session.add.call_count == 3


@pytest.mark.asyncio
async def test_generate_grok_predictions_skips_recently_predicted():
    """Markets predicted within cooldown window should be skipped."""
    from polyedge.research.grok_predictor import generate_grok_predictions

    fake_markets = [_make_fake_market("m1"), _make_fake_market("m2")]
    grok_response = '{"side": "NO", "confidence": 6, "reasoning": "test"}'

    mock_session = AsyncMock()

    mock_markets_result = MagicMock()
    mock_markets_result.scalars.return_value.all.return_value = fake_markets

    # m1 was predicted recently — should be skipped
    mock_recent_result = MagicMock()
    mock_recent_result.scalars.return_value.all.return_value = ["m1"]

    mock_session.execute = AsyncMock(side_effect=[mock_markets_result, mock_recent_result])
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()

    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("polyedge.research.grok_predictor.SessionLocal", return_value=mock_session_ctx), \
         patch("polyedge.research.grok_predictor.query_grok", new_callable=AsyncMock, return_value=grok_response):
        count = await generate_grok_predictions(cooldown_hours=6, max_concurrent=2, max_markets=10)

    # Only m2 should be predicted (m1 was in cooldown)
    assert count == 1
    assert mock_session.add.call_count == 1


@pytest.mark.asyncio
async def test_generate_grok_predictions_handles_api_failure():
    """If Grok API fails for a market, skip it and continue."""
    from polyedge.research.grok_predictor import generate_grok_predictions

    fake_markets = [_make_fake_market("m1"), _make_fake_market("m2")]

    mock_session = AsyncMock()
    mock_markets_result = MagicMock()
    mock_markets_result.scalars.return_value.all.return_value = fake_markets
    mock_recent_result = MagicMock()
    mock_recent_result.scalars.return_value.all.return_value = []
    mock_session.execute = AsyncMock(side_effect=[mock_markets_result, mock_recent_result])
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()

    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    call_count = 0
    async def _flaky_grok(prompt):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ConnectionError("Grok is down")
        return '{"side": "YES", "confidence": 8, "reasoning": "test"}'

    with patch("polyedge.research.grok_predictor.SessionLocal", return_value=mock_session_ctx), \
         patch("polyedge.research.grok_predictor.query_grok", side_effect=_flaky_grok):
        count = await generate_grok_predictions(cooldown_hours=6, max_concurrent=2, max_markets=10)

    # m1 fails, m2 succeeds
    assert count == 1
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest polyedge/tests/test_grok_predictor.py -v -k "generate"`
Expected: FAIL with `ImportError: cannot import name 'generate_grok_predictions'`

**Step 3: Add generate_grok_predictions() to grok_predictor.py**

Append to `polyedge/src/polyedge/research/grok_predictor.py`:

```python
import asyncio
from datetime import datetime, timedelta, timezone


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# Lazy imports to avoid circular deps at module level
SessionLocal = None
Market = None
GrokPrediction = None
query_grok = None


def _ensure_imports():
    global SessionLocal, Market, GrokPrediction, query_grok
    if SessionLocal is None:
        from polyedge.db import SessionLocal as _SL
        from polyedge.models import Market as _M, GrokPrediction as _GP
        from polyedge.research.grok import query_grok as _qg
        SessionLocal = _SL
        Market = _M
        GrokPrediction = _GP
        query_grok = _qg


async def generate_grok_predictions(
    *,
    cooldown_hours: int = 6,
    max_concurrent: int = 8,
    max_markets: int = 2000,
    commit_every: int = 200,
) -> int:
    """Ask Grok for a YES/NO prediction on each active market.

    - Only predicts markets ending within 7 days
    - Skips markets already predicted within cooldown_hours
    - Stores results in grok_predictions table
    - Commits in batches of commit_every to avoid memory bloat
    - Logs progress every 100 predictions
    - Returns count of new predictions stored

    This is a REAL LLM prediction: Grok sees the question, description,
    odds, and decides what bet it would place.
    """
    from sqlalchemy import select

    _ensure_imports()

    now = _utcnow_naive()
    max_end = now + timedelta(days=7)
    cooldown_cutoff = now - timedelta(hours=cooldown_hours)

    async with SessionLocal() as session:
        # Get markets ending within 7 days
        markets = (await session.execute(
            select(Market).where(
                Market.active == True,  # noqa: E712
                Market.end_date.is_not(None),
                Market.end_date > now,
                Market.end_date <= max_end,
            ).order_by(Market.volume.desc()).limit(max_markets)
        )).scalars().all()

        if not markets:
            log.info("Grok predictions: 0 active markets in 7d window")
            return 0

        # Find markets already predicted recently
        recent_preds = set(
            (await session.execute(
                select(GrokPrediction.market_id)
                .where(GrokPrediction.created_at >= cooldown_cutoff)
                .distinct()
            )).scalars().all()
        )

        to_predict = [m for m in markets if m.id not in recent_preds]
        log.info(
            "Grok predictions: %d markets in window, %d already predicted, %d to predict",
            len(markets), len(recent_preds), len(to_predict),
        )

        if not to_predict:
            return 0

    # --- Detach from session for the long-running API calls ---
    # Build prompt data outside the session to avoid detached instance errors
    prompt_data: list[dict] = []
    for m in to_predict:
        prompt_data.append({
            "market_id": m.id,
            "question": m.question or "",
            "description": (m.description or "")[:_MAX_DESCRIPTION_LEN],
            "yes_price": float(m.yes_price or 0.5),
            "no_price": float(m.no_price) if m.no_price is not None else max(0.001, 1.0 - float(m.yes_price or 0.5)),
            "end_date": m.end_date.strftime("%Y-%m-%d %H:%M UTC") if m.end_date else "unknown",
            "category": m.market_category or m.category or "",
        })

    semaphore = asyncio.Semaphore(max_concurrent)
    consecutive_failures = 0
    max_consecutive_failures = 20  # bail if Grok is completely down

    async def _predict_one(pd: dict) -> tuple[str, dict | None]:
        nonlocal consecutive_failures
        if consecutive_failures >= max_consecutive_failures:
            return pd["market_id"], None

        prompt = build_prediction_prompt(
            question=pd["question"],
            description=pd["description"],
            yes_price=pd["yes_price"],
            no_price=pd["no_price"],
            end_date=pd["end_date"],
            category=pd["category"],
        )

        async with semaphore:
            try:
                raw = await query_grok(prompt)
                consecutive_failures = 0  # reset on success
            except Exception as e:
                consecutive_failures += 1
                if consecutive_failures >= max_consecutive_failures:
                    log.error("Grok API failed %d times consecutively, bailing: %s", consecutive_failures, e)
                else:
                    log.warning("Grok API failed for %s: %s", pd["market_id"], e)
                return pd["market_id"], None

        parsed = parse_grok_response(raw)
        return pd["market_id"], parsed

    # Run predictions in chunks to manage memory
    created = 0
    chunk_size = commit_every
    for chunk_start in range(0, len(prompt_data), chunk_size):
        if consecutive_failures >= max_consecutive_failures:
            log.error("Bailing on remaining markets due to consecutive Grok failures")
            break

        chunk = prompt_data[chunk_start:chunk_start + chunk_size]
        results = await asyncio.gather(
            *[_predict_one(pd) for pd in chunk],
            return_exceptions=True,
        )

        # Open a fresh session for each commit batch
        async with SessionLocal() as session:
            batch_count = 0
            for result in results:
                if isinstance(result, Exception):
                    log.warning("Grok prediction task error: %s", result)
                    continue
                market_id, parsed = result
                if parsed is None:
                    continue
                session.add(GrokPrediction(
                    market_id=market_id,
                    predicted_side=parsed["side"],
                    confidence=parsed["confidence"],
                    reasoning=parsed["reasoning"],
                ))
                batch_count += 1

            if batch_count > 0:
                await session.commit()
                created += batch_count

        log.info("Grok predictions progress: %d/%d done, %d stored so far",
                 min(chunk_start + chunk_size, len(prompt_data)), len(prompt_data), created)

    log.info("Grok predictions complete: %d new predictions from %d markets", created, len(prompt_data))
    return created
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest polyedge/tests/test_grok_predictor.py -v`
Expected: all tests PASS

**Step 5: Commit**

```bash
git add polyedge/src/polyedge/research/grok_predictor.py polyedge/tests/test_grok_predictor.py
git commit -m "feat: add generate_grok_predictions with batching, rate limit bail, progress logging"
```

---

## Task 4: Rename "llm" → "factor_match" in scheduler.py

**Files:**
- Modify: `polyedge/src/polyedge/scheduler.py`
- Modify: `polyedge/tests/test_grok_predictor.py`

**Step 1: Write the failing test**

Add to `polyedge/tests/test_grok_predictor.py`:

```python
def test_factor_match_trading_function_exists():
    """After rename, the function should be run_factor_match_paper_trading."""
    from polyedge import scheduler
    assert hasattr(scheduler, "run_factor_match_paper_trading")
    assert callable(scheduler.run_factor_match_paper_trading)
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest polyedge/tests/test_grok_predictor.py::test_factor_match_trading_function_exists -v`
Expected: FAIL with `AttributeError`

**Step 3: Rename in scheduler.py**

Make ALL of these exact changes in `polyedge/src/polyedge/scheduler.py`:

**3a. Rename the function** (line ~425):

```
OLD: async def run_llm_paper_trading():
NEW: async def run_factor_match_paper_trading():
```

**3b. Update the docstring** (lines ~426-436):

```
OLD: """Open paper trades based on LLM predictions (Grok/Perplexity factors).
NEW: """Open paper trades based on factor-match predictions (Grok/Perplexity factors → string match → math).
```

**3c. Update trade_source queries** (line ~486):

```
OLD: PaperTrade.trade_source == "llm",
NEW: PaperTrade.trade_source == "factor_match",
```

**3d. Update trade_source queries** (line ~496):

```
OLD: PaperTrade.trade_source == "llm_inverse",
NEW: PaperTrade.trade_source == "factor_match_inv",
```

**3e. Update direct trade creation** (line ~539):

```
OLD: trade_source="llm",
NEW: trade_source="factor_match",
```

**3f. Update inverse trade creation** (line ~556):

```
OLD: trade_source="llm_inverse",
NEW: trade_source="factor_match_inv",
```

**3g. Update log message** (line ~565):

```
OLD: log.info("LLM paper trading: %d direct + %d inverse trades from %d predictions", trades_opened, inverse_opened, len(pred_rows))
NEW: log.info("Factor-match paper trading: %d direct + %d inverse trades from %d predictions", trades_opened, inverse_opened, len(pred_rows))
```

**3h. Update _safe_commit loop name** (line ~563):

```
OLD: await _safe_commit(session, loop_name="llm_paper_trading")
NEW: await _safe_commit(session, loop_name="factor_match_trading")
```

**3i. Update combined paper trading docstring** (line ~569):

```
OLD: """Open paper trades only when BOTH ngram rules AND LLM predictions agree.
NEW: """Open paper trades only when BOTH ngram rules AND factor-match predictions agree.
```

**3j. Update combined docstring** (line ~573):

```
OLD: 2. An LLM prediction with edge >= 10%
NEW: 2. A factor-match prediction with confidence >= 15%
```

**3k. Update combined comment** (line ~706):

```
OLD: # Check LLM prediction (confidence is conviction, not probability)
NEW: # Check factor-match prediction (confidence is conviction, not probability)
```

**3l. Update scheduler loop** (line ~913):

```
OLD: loop(run_llm_paper_trading, 300, "llm_paper_trading"),
NEW: loop(run_factor_match_paper_trading, 300, "factor_match_trading"),
```

**3m. Update scheduler comment** (line ~912):

```
OLD: # Paper trading: LLM predictions (every 5 min, 48h horizon)
NEW: # Paper trading: factor-match predictions (every 5 min, 7d horizon)
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest polyedge/tests/test_grok_predictor.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add polyedge/src/polyedge/scheduler.py polyedge/tests/test_grok_predictor.py
git commit -m "refactor: rename llm -> factor_match in scheduler"
```

---

## Task 5: Rename "llm" → "factor_match" in app.py

**Files:**
- Modify: `polyedge/src/polyedge/app.py`

**Step 1: Make ALL of these exact changes in app.py**

**5a. Line 1154 — comment:**

```
OLD: # --- TRADE SOURCE BREAKDOWN (ngram vs llm vs combined) ---
NEW: # --- TRADE SOURCE BREAKDOWN (ngram vs factor_match vs grok_direct vs combined) ---
```

**5b. Line 1158 — source list:**

```
OLD: for src in ("ngram", "llm", "combined", "ngram_inverse", "llm_inverse", "combined_inverse"):
NEW: for src in ("ngram", "factor_match", "grok_direct", "combined", "ngram_inverse", "factor_match_inv", "grok_inv", "combined_inverse"):
```

**5c. Lines 1412-1417 — label mapping (replace entire block):**

```
OLD:
                    "by_source": {
                        "ngram": {**source_stats["ngram"], "label": "Ngram Rules"},
                        "llm": {**source_stats["llm"], "label": "LLM Predictions"},
                        "combined": {**source_stats["combined"], "label": "Ngram + LLM"},
                        "ngram_inverse": {**source_stats["ngram_inverse"], "label": "Ngram Inverse"},
                        "llm_inverse": {**source_stats["llm_inverse"], "label": "LLM Inverse"},
                        "combined_inverse": {**source_stats["combined_inverse"], "label": "Combined Inverse"},
                    },
NEW:
                    "by_source": {
                        "ngram": {**source_stats.get("ngram", _empty_src), "label": "Ngram Rules"},
                        "factor_match": {**source_stats.get("factor_match", _empty_src), "label": "Factor Match"},
                        "grok_direct": {**source_stats.get("grok_direct", _empty_src), "label": "Grok Predictions"},
                        "combined": {**source_stats.get("combined", _empty_src), "label": "Ngram + Factor"},
                        "ngram_inverse": {**source_stats.get("ngram_inverse", _empty_src), "label": "Ngram Inverse"},
                        "factor_match_inv": {**source_stats.get("factor_match_inv", _empty_src), "label": "Factor Match Inverse"},
                        "grok_inv": {**source_stats.get("grok_inv", _empty_src), "label": "Grok Inverse"},
                        "combined_inverse": {**source_stats.get("combined_inverse", _empty_src), "label": "Combined Inverse"},
                    },
```

**5d. Add `_empty_src` helper** above the source_stats loop (around line 1155):

```python
_empty_src = {"open": 0, "closed": 0, "wins": 0, "win_rate_pct": None, "pnl": 0.0, "unique_markets_open": 0, "unique_markets_closed": 0}
```

This prevents `KeyError` when a source has no trades yet (e.g., grok_direct on first deploy).

**Step 2: Verify no other "llm" references remain**

Search app.py for any remaining `"llm"` strings that need updating. The `trade.trade_source` field at line 1022 doesn't need changing — it dynamically reads whatever's in the DB.

**Step 3: Verify app imports**

Run: `python -c "from polyedge.app import app; print('ok')"`
Expected: `ok`

**Step 4: Commit**

```bash
git add polyedge/src/polyedge/app.py
git commit -m "refactor: rename llm -> factor_match in dashboard API, add grok_direct/grok_inv sources"
```

---

## Task 6: Rename "llm" → "factor_match" in dashboard.html

**Files:**
- Modify: `polyedge/src/polyedge/static/dashboard.html`

**Step 1: Make ALL of these exact changes**

**6a. Line 971 — comment:**

```
OLD: // Show trade source experiment breakdown (ngram vs llm vs combined) — PnL first
NEW: // Show trade source experiment breakdown — PnL first
```

**6b. Line 978 — strategy comparison source list:**

```
OLD: for (const key of ['ngram', 'llm', 'combined', 'ngram_inverse', 'llm_inverse', 'combined_inverse']) {
NEW: for (const key of ['ngram', 'factor_match', 'grok_direct', 'combined', 'ngram_inverse', 'factor_match_inv', 'grok_inv', 'combined_inverse']) {
```

**6c. Line 1050 — inverse comparison pairs:**

```
OLD: [['ngram','ngram_inverse'],['llm','llm_inverse'],['combined','combined_inverse']].forEach(function(pair) {
NEW: [['ngram','ngram_inverse'],['factor_match','factor_match_inv'],['grok_direct','grok_inv'],['combined','combined_inverse']].forEach(function(pair) {
```

**Step 2: Verify no other "llm" references remain in dashboard.html**

Search the file for any remaining `llm` strings.

**Step 3: Commit**

```bash
git add polyedge/src/polyedge/static/dashboard.html
git commit -m "refactor: rename llm -> factor_match in dashboard UI, add grok sources"
```

---

## Task 7: Rename "llm" → "factor_match" in database + clean up heartbeats

**Files:**
- Create: `polyedge/deploy/migrations/004_rename_llm_to_factor_match.sql`

**Step 1: Create the migration file**

Create `polyedge/deploy/migrations/004_rename_llm_to_factor_match.sql`:

```sql
-- Rename paper_trades trade_source: llm -> factor_match, llm_inverse -> factor_match_inv
BEGIN;
UPDATE paper_trades SET trade_source = 'factor_match' WHERE trade_source = 'llm';
UPDATE paper_trades SET trade_source = 'factor_match_inv' WHERE trade_source = 'llm_inverse';

-- Clean up old heartbeat row
DELETE FROM service_heartbeats WHERE service = 'llm_paper_trading';
COMMIT;
```

**Step 2: Run the migration**

```bash
ssh root@89.167.99.187 "PGPASSWORD=polyedge psql -h localhost -U polyedge -d polyedge < /dev/stdin" < polyedge/deploy/migrations/004_rename_llm_to_factor_match.sql
```

If the above doesn't work due to path issues, run directly:

```bash
ssh root@89.167.99.187 "PGPASSWORD=polyedge psql -h localhost -U polyedge -d polyedge -c \"
BEGIN;
UPDATE paper_trades SET trade_source = 'factor_match' WHERE trade_source = 'llm';
UPDATE paper_trades SET trade_source = 'factor_match_inv' WHERE trade_source = 'llm_inverse';
DELETE FROM service_heartbeats WHERE service = 'llm_paper_trading';
COMMIT;
\""
```

**Step 3: Verify the migration**

```bash
ssh root@89.167.99.187 "PGPASSWORD=polyedge psql -h localhost -U polyedge -d polyedge -c \"SELECT trade_source, COUNT(*) FROM paper_trades GROUP BY trade_source ORDER BY trade_source;\""
```

Expected: no rows with `llm` or `llm_inverse`. Should see `factor_match` and `factor_match_inv`.

Also verify heartbeat cleanup:
```bash
ssh root@89.167.99.187 "PGPASSWORD=polyedge psql -h localhost -U polyedge -d polyedge -c \"SELECT service FROM service_heartbeats WHERE service LIKE '%llm%';\""
```

Expected: 0 rows.

**Step 4: Commit**

```bash
git add polyedge/deploy/migrations/004_rename_llm_to_factor_match.sql
git commit -m "chore: rename llm -> factor_match in DB, clean up old heartbeat"
```

---

## Task 8: Add Grok prediction paper trading to scheduler

**Files:**
- Modify: `polyedge/src/polyedge/scheduler.py`
- Modify: `polyedge/tests/test_grok_predictor.py`

**Step 1: Write the failing test**

Add to `polyedge/tests/test_grok_predictor.py`:

```python
def test_grok_prediction_trading_function_exists():
    """Verify the scheduler has the grok prediction trading function."""
    from polyedge import scheduler
    assert hasattr(scheduler, "run_grok_prediction_trading")
    assert callable(scheduler.run_grok_prediction_trading)


@pytest.mark.asyncio
async def test_grok_prediction_trading_places_both_sides():
    """Mock DB, verify both grok_direct and grok_inv trades are created."""
    from polyedge.scheduler import run_grok_prediction_trading
    from unittest.mock import AsyncMock, MagicMock, patch, call

    # Fake GrokPrediction
    pred = MagicMock()
    pred.predicted_side = "YES"
    pred.confidence = 0.7

    # Fake Market
    market = MagicMock()
    market.id = "mkt1"
    market.yes_price = 0.60
    market.no_price = 0.40
    market.active = True
    market.end_date = datetime.utcnow() + timedelta(days=1)

    mock_session = AsyncMock()

    # Mock generate_grok_predictions
    mock_gen = AsyncMock(return_value=1)

    # Mock: preds query returns our fake pair
    mock_preds_result = MagicMock()
    mock_preds_result.all.return_value = [(pred, market)]

    # Mock: open trades queries return empty
    mock_empty = MagicMock()
    mock_empty.all.return_value = []

    mock_session.execute = AsyncMock(side_effect=[mock_preds_result, mock_empty, mock_empty])
    mock_session.add = MagicMock()

    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("polyedge.scheduler.SessionLocal", return_value=mock_session_ctx), \
         patch("polyedge.research.grok_predictor.generate_grok_predictions", mock_gen), \
         patch("polyedge.scheduler._safe_commit", new_callable=AsyncMock, return_value=True):
        await run_grok_prediction_trading()

    # Should have added 2 trades: grok_direct (YES) + grok_inv (NO)
    assert mock_session.add.call_count == 2
    added_trades = [c.args[0] for c in mock_session.add.call_args_list]
    sources = {t.trade_source for t in added_trades}
    assert sources == {"grok_direct", "grok_inv"}
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest polyedge/tests/test_grok_predictor.py -v -k "grok_prediction_trading"`
Expected: FAIL with `ImportError`

**Step 3: Add run_grok_prediction_trading() to scheduler.py**

Add after `run_factor_match_paper_trading()` (the renamed function), before `run_combined_paper_trading()`:

```python
async def run_grok_prediction_trading():
    """Generate Grok predictions and open paper trades for each.

    This is the REAL LLM prediction system: Grok sees each market's question,
    description, and odds, decides YES or NO, and we place both the direct
    bet and the inverse bet. No filters — every prediction becomes two trades.
    """
    from polyedge.models import Market, PaperTrade
    from polyedge.research.grok_predictor import generate_grok_predictions
    from sqlalchemy import select

    # Step 1: Generate new predictions (calls Grok API)
    new_preds = await generate_grok_predictions()

    # Step 2: Find all grok predictions that don't have trades yet
    from polyedge.models import GrokPrediction

    async with SessionLocal() as session:
        now = _utcnow_naive()
        cutoff = now - timedelta(days=7)

        preds = (await session.execute(
            select(GrokPrediction, Market)
            .join(Market, Market.id == GrokPrediction.market_id)
            .where(
                GrokPrediction.created_at >= cutoff,
                Market.active == True,  # noqa: E712
                Market.end_date.is_not(None),
                Market.end_date > now,
            )
        )).all()

        if not preds:
            log.info("Grok trading: 0 predictions to trade")
            return

        # Pre-load existing open trades to avoid duplicates
        open_direct = set(
            (r[0], r[1]) for r in (await session.execute(
                select(PaperTrade.market_id, PaperTrade.side)
                .where(
                    PaperTrade.resolved == False,  # noqa: E712
                    PaperTrade.trade_source == "grok_direct",
                )
            )).all()
        )
        open_inverse = set(
            (r[0], r[1]) for r in (await session.execute(
                select(PaperTrade.market_id, PaperTrade.side)
                .where(
                    PaperTrade.resolved == False,  # noqa: E712
                    PaperTrade.trade_source == "grok_inv",
                )
            )).all()
        )

        direct_opened = 0
        inverse_opened = 0

        for pred, market in preds:
            side = pred.predicted_side.upper()
            if side not in ("YES", "NO"):
                continue

            yes_price = float(market.yes_price or 0.5)
            no_price = float(market.no_price) if market.no_price is not None else max(0.001, 1.0 - yes_price)

            # Skip near-certain markets (no edge possible)
            if yes_price <= 0.02 or yes_price >= 0.98:
                continue

            entry = yes_price if side == "YES" else no_price
            inv_side = "NO" if side == "YES" else "YES"
            inv_entry = no_price if side == "YES" else yes_price

            # Direct trade — bet what Grok says
            if (market.id, side) not in open_direct:
                session.add(PaperTrade(
                    market_id=market.id,
                    rule_id=None,
                    side=side,
                    entry_price=entry,
                    edge=1.0 - entry - entry,
                    bet_size=1.0,
                    trade_source="grok_direct",
                    resolved=False,
                ))
                open_direct.add((market.id, side))
                direct_opened += 1

            # Inverse trade — bet the opposite of Grok
            if (market.id, inv_side) not in open_inverse:
                session.add(PaperTrade(
                    market_id=market.id,
                    rule_id=None,
                    side=inv_side,
                    entry_price=inv_entry,
                    edge=1.0 - inv_entry - inv_entry,
                    bet_size=1.0,
                    trade_source="grok_inv",
                    resolved=False,
                ))
                open_inverse.add((market.id, inv_side))
                inverse_opened += 1

        await _safe_commit(session, loop_name="grok_prediction_trading")

    log.info(
        "Grok trading: %d new preds, %d direct + %d inverse trades opened",
        new_preds, direct_opened, inverse_opened,
    )
```

**Step 4: Wire into the scheduler loop**

In `run_scheduler()`, add to the `asyncio.gather()` block, after the factor_match line:

```python
        # Paper trading: Grok direct predictions (every 1 hour — calls Grok API)
        loop(run_grok_prediction_trading, 3600, "grok_prediction_trading"),
```

**Step 5: Run tests**

Run: `python -m pytest polyedge/tests/test_grok_predictor.py -v`
Expected: all tests PASS

**Step 6: Commit**

```bash
git add polyedge/src/polyedge/scheduler.py polyedge/tests/test_grok_predictor.py
git commit -m "feat: add grok_prediction_trading loop — real LLM predictions, both sides"
```

---

## Task 9: Deploy to Windows server and verify

**Step 1: Kill all existing scheduler processes on the Windows server**

First, find the PIDs:
```bash
ssh -o StrictHostKeyChecking=no Administrator@88.99.142.89 "wmic process where \"name='python.exe'\" get ProcessId,CommandLine /format:list"
```

Kill ALL polyedge scheduler processes (look for `polyedge.exe run` in the CommandLine):
```bash
ssh Administrator@88.99.142.89 "taskkill /F /PID <pid1> /PID <pid2>"
```

Replace `<pid1>`, `<pid2>` with actual PIDs from the output above. Do NOT kill the supergod worker daemon process.

Verify they're dead:
```bash
ssh Administrator@88.99.142.89 "wmic process where \"name='python.exe'\" get ProcessId,CommandLine /format:list"
```
Expected: only the supergod worker daemon remains.

**Step 2: Copy updated code to Windows server**

From the project root on the local machine:
```bash
scp -r polyedge/src/polyedge/research/grok_predictor.py Administrator@88.99.142.89:"C:/polyedge/polyedge/src/polyedge/research/grok_predictor.py"
scp polyedge/src/polyedge/scheduler.py Administrator@88.99.142.89:"C:/polyedge/polyedge/src/polyedge/scheduler.py"
scp polyedge/src/polyedge/models.py Administrator@88.99.142.89:"C:/polyedge/polyedge/src/polyedge/models.py"
scp polyedge/src/polyedge/app.py Administrator@88.99.142.89:"C:/polyedge/polyedge/src/polyedge/app.py"
scp polyedge/src/polyedge/static/dashboard.html Administrator@88.99.142.89:"C:/polyedge/polyedge/src/polyedge/static/dashboard.html"
```

**Step 3: Copy app.py and dashboard.html to the dashboard server too**

```bash
scp polyedge/src/polyedge/app.py root@89.167.99.187:/opt/polyedge/src/polyedge/app.py
scp polyedge/src/polyedge/static/dashboard.html root@89.167.99.187:/opt/polyedge/src/polyedge/static/dashboard.html
scp polyedge/src/polyedge/models.py root@89.167.99.187:/opt/polyedge/src/polyedge/models.py
```

Restart the dashboard:
```bash
ssh root@89.167.99.187 "systemctl restart polyedge-dashboard"
```

**Step 4: Start ONE scheduler process on the Windows server**

```bash
ssh Administrator@88.99.142.89 "cd C:\polyedge\polyedge && start /B .venv\Scripts\python.exe -m polyedge run > scheduler.log 2>&1"
```

Verify only one process is running:
```bash
ssh Administrator@88.99.142.89 "wmic process where \"name='python.exe' and commandline like '%polyedge%run%'\" get ProcessId /format:list"
```
Expected: exactly 1 PID.

**Step 5: Verify heartbeats (wait 5 minutes)**

```bash
ssh root@89.167.99.187 "PGPASSWORD=polyedge psql -h localhost -U polyedge -d polyedge -c \"
SELECT service, status, last_success_at, updated_at
FROM service_heartbeats
WHERE service IN ('factor_match_trading', 'grok_prediction_trading', 'poller+scorer', 'score_paper_trades')
ORDER BY updated_at DESC;
\""
```

Expected: all 4 services show `status = 'ok'` or `status = 'running'` with recent `updated_at`.

**Step 6: Verify grok predictions are being generated**

After 10-15 minutes (first Grok prediction cycle):
```bash
ssh root@89.167.99.187 "PGPASSWORD=polyedge psql -h localhost -U polyedge -d polyedge -c \"
SELECT COUNT(*) as total, MIN(created_at) as first, MAX(created_at) as last
FROM grok_predictions;
\""
```

Expected: rows with recent timestamps.

**Step 7: Verify trades are being placed**

```bash
ssh root@89.167.99.187 "PGPASSWORD=polyedge psql -h localhost -U polyedge -d polyedge -c \"
SELECT trade_source, COUNT(*) as trades, SUM(CASE WHEN resolved THEN 1 ELSE 0 END) as resolved
FROM paper_trades
GROUP BY trade_source
ORDER BY trade_source;
\""
```

Expected: `grok_direct` and `grok_inv` rows with trade counts > 0. No rows for `llm` or `llm_inverse`.

**Step 8: Verify dashboard shows new sources**

Open http://89.167.99.187:8090/ in browser. Check:
- Strategy Comparison table shows "Grok Predictions" and "Grok Inverse" rows
- "Factor Match" and "Factor Match Inverse" appear instead of "LLM Predictions" and "LLM Inverse"
- Inverse tab shows grok_direct vs grok_inv comparison

**Step 9: Commit the deploy evidence**

```bash
git add -A
git commit -m "feat: deploy grok predictions + factor_match rename — verified live"
```

---

## Summary of trade sources after this plan

| Source | What it does | How it decides |
|--------|-------------|----------------|
| `ngram` | Bet based on historical text patterns | Data-driven, mined from resolved markets |
| `ngram_inverse` | Opposite of ngram | Inverse of above |
| `factor_match` | ~~Was "llm"~~ String-match Grok research factors to markets | Grok text factors → string match → local math |
| `factor_match_inv` | ~~Was "llm_inverse"~~ Opposite of factor_match | Inverse of above |
| `grok_direct` | **NEW** Ask Grok "would you bet YES or NO?" | Real LLM prediction — sees question, description, odds |
| `grok_inv` | **NEW** Opposite of what Grok says | Inverse of above |
| `combined` | When ngram + factor_match agree | Both systems must agree |
| `combined_inverse` | Opposite of combined | Inverse of above |

## Task 10: Add scheduler watchdog — auto-recover stuck services

**Problem:** Scheduler loop tasks (poller, scorer, etc.) can hang indefinitely. When this happens, the dashboard goes stale and PnL stops updating. The heartbeat shows "running" but the task never completes.

**Fix:** Add a timeout wrapper to the scheduler's `loop()` function. If any loop iteration takes longer than a configurable max duration, cancel it and let the next iteration run normally.

**Files:**
- Modify: `polyedge/src/polyedge/scheduler.py`

**Step 1: Modify the `loop()` function inside `run_scheduler()`**

The current loop function (around line 875) is:

```python
    async def loop(fn, interval_seconds: int, name: str):
        while True:
            try:
                log.info("Running %s...", name)
                await _record_heartbeat(name, "running", details="cycle_started")
                result = await fn()
                await _record_heartbeat(name, "ok", details=str(result))
            except Exception as e:
                log.error("%s failed: %s", name, e, exc_info=True)
                await _record_heartbeat(name, "error", details=str(e))
            # Sleep in 5-min chunks, refreshing heartbeat to prevent stale indicators
            remaining = interval_seconds
            while remaining > 0:
                chunk = min(remaining, 300)
                await asyncio.sleep(chunk)
                remaining -= chunk
                if remaining > 0:
                    await _record_heartbeat(name, "idle", details=f"next_run_in_{remaining}s")
```

Replace with:

```python
    async def loop(fn, interval_seconds: int, name: str, max_duration: int = 600):
        """Run fn() every interval_seconds. If fn() takes longer than max_duration
        seconds, cancel it and log an error. This prevents one hung task from
        blocking the scheduler permanently.
        """
        while True:
            try:
                log.info("Running %s...", name)
                await _record_heartbeat(name, "running", details="cycle_started")
                result = await asyncio.wait_for(fn(), timeout=max_duration)
                await _record_heartbeat(name, "ok", details=str(result))
            except asyncio.TimeoutError:
                log.error("%s TIMED OUT after %ds — cancelling this cycle", name, max_duration)
                await _record_heartbeat(name, "timeout", details=f"killed_after_{max_duration}s")
            except Exception as e:
                log.error("%s failed: %s", name, e, exc_info=True)
                await _record_heartbeat(name, "error", details=str(e)[:2000])
            # Sleep in 5-min chunks, refreshing heartbeat to prevent stale indicators
            remaining = interval_seconds
            while remaining > 0:
                chunk = min(remaining, 300)
                await asyncio.sleep(chunk)
                remaining -= chunk
                if remaining > 0:
                    await _record_heartbeat(name, "idle", details=f"next_run_in_{remaining}s")
```

**Step 2: Set appropriate timeouts per service in the asyncio.gather block**

Most services should complete in under 5 minutes. The Grok prediction loop may take up to 30 minutes (2000 API calls). Update the `asyncio.gather()` calls:

```python
    await asyncio.gather(
        loop(poll_then_score, 300, "poller+scorer", max_duration=300),
        loop(generate_all_predictions, 3600, "predictions", max_duration=600),
        loop(run_api_research, 1800, "api_research", max_duration=600),
        loop(run_supergod_research, 1800, "supergod_dispatch", max_duration=300),
        loop(ingest_supergod_research, 600, "supergod_ingest", max_duration=300),
        loop(collect_daily_features, 21600, "feature_collection", max_duration=600),
        loop(run_paper_trading, 300, "paper_trading", max_duration=300),
        loop(run_factor_match_paper_trading, 300, "factor_match_trading", max_duration=300),
        loop(run_grok_prediction_trading, 3600, "grok_prediction_trading", max_duration=1800),
        loop(run_combined_paper_trading, 300, "combined_paper_trading", max_duration=300),
        loop(score_paper_trades, 300, "score_paper_trades", max_duration=300),
        loop(check_resolutions, 60, "resolution_check", max_duration=120),
        loop(run_correlation_refresh, 86400, "correlation_refresh", max_duration=3600),
        loop(run_research_rule_bridge, 21600, "research_rule_bridge", max_duration=1800),
        loop(run_backtest_refresh, 604800, "backtest_refresh", max_duration=3600),
        loop(run_agreement_refresh, 86400, "agreement_refresh", max_duration=1800),
    )
```

**Step 3: Verify**

Run: `python -c "from polyedge.scheduler import run_scheduler; print('ok')"`
Expected: `ok`

**Step 4: Commit**

```bash
git add polyedge/src/polyedge/scheduler.py
git commit -m "fix: add timeout watchdog to scheduler loops — auto-cancel stuck services"
```

---

## Cleanup note

The `grok_predictions` table will grow over time. After the system is running for a week, add a cleanup job to delete predictions older than 30 days:

```sql
DELETE FROM grok_predictions WHERE created_at < NOW() - INTERVAL '30 days';
```

This can be added as a scheduler loop later (not blocking for initial deploy).
