"""Ask Grok directly: would you bet YES or NO on this market?

This is a REAL LLM prediction -- Grok sees the market question, the current
odds, and decides which side it would bet on.  No string matching, no factor
math.  Just: "here's the bet, what would you do?"
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone

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
        # Find the JSON object -- use greedy match from first { to last }
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


# ---------------------------------------------------------------------------
# Async prediction generator
# ---------------------------------------------------------------------------

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
    max_concurrent: int = 4,
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
                # Skip already-settled markets (save API cost)
                Market.yes_price > 0.01,
                Market.yes_price < 0.99,
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
    max_consecutive_failures = 40  # bail if Grok is completely down

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
            # Retry up to 2 times with backoff
            for attempt in range(3):
                try:
                    raw = await query_grok(prompt)
                    consecutive_failures = 0  # reset on success
                    break
                except Exception as e:
                    if attempt < 2:
                        await asyncio.sleep(2 ** attempt)  # 1s, 2s backoff
                        continue
                    consecutive_failures += 1
                    err_detail = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
                    if consecutive_failures >= max_consecutive_failures:
                        log.error("Grok API failed %d times consecutively, bailing: %s", consecutive_failures, err_detail)
                    elif consecutive_failures % 5 == 0:  # log every 5th failure to reduce noise
                        log.warning("Grok API failed for %s (attempt %d): %s", pd["market_id"], consecutive_failures, err_detail)
                    return pd["market_id"], None
            else:
                return pd["market_id"], None

        parsed = parse_grok_response(raw)
        return pd["market_id"], parsed

    # Run predictions in small chunks to avoid overwhelming connections
    created = 0
    chunk_size = min(commit_every, 50)  # smaller chunks for gentler API load
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
