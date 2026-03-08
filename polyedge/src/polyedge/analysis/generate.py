"""Generate factor-based prediction snapshots for active markets."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from polyedge.db import SessionLocal
from polyedge.models import Factor, FactorWeight, Market, Prediction

log = logging.getLogger(__name__)


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _utcnow_naive() -> datetime:
    """Return UTC now as naive datetime for compatibility with existing schema."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


_POSITIVE_TOKENS = {
    "bullish", "positive", "yes", "up", "rise", "rising", "increase", "higher",
    "gain", "gains", "win", "winning", "over", "likely",
}
_NEGATIVE_TOKENS = {
    "bearish", "negative", "no", "down", "drop", "fall", "falling", "decrease",
    "lower", "loss", "losing", "under", "unlikely",
}
_NEUTRAL_TOKENS = {"neutral", "mixed", "uncertain", "unknown"}


def _direction_from_factor(factor: Factor) -> float:
    """Estimate directional signal in [-1, 1] from factor text fields."""
    texts = [
        str(getattr(factor, "value", "") or "").lower(),
        str(getattr(factor, "name", "") or "").lower(),
        str(getattr(factor, "subcategory", "") or "").lower(),
    ]
    blob = " ".join(texts)
    token_set = set(re.findall(r"[a-zA-Z_]+", blob))

    if token_set & _NEUTRAL_TOKENS:
        return 0.0

    pos_hits = len(token_set & _POSITIVE_TOKENS)
    neg_hits = len(token_set & _NEGATIVE_TOKENS)
    if pos_hits and not neg_hits:
        return 1.0
    if neg_hits and not pos_hits:
        return -1.0
    if pos_hits and neg_hits:
        return 0.0

    # Numeric fallback from value text.
    value = str(getattr(factor, "value", "") or "").replace(",", "")
    pct = re.search(r"([-+]?\d+(?:\.\d+)?)\s*%", value)
    if pct:
        num = float(pct.group(1))
        if num > 0:
            return 1.0
        if num < 0:
            return -1.0

    signed = re.search(r"([-+]\d+(?:\.\d+)?)", value)
    if signed:
        num = float(signed.group(1))
        if num > 0:
            return 1.0
        if num < 0:
            return -1.0

    return 0.0


def _predict_from_factors(
    *,
    factors: list[Factor],
    market_yes_price: float,
    weights: dict[str, float],
) -> dict:
    """Convert factors into a YES/NO prediction.

    Direction is parsed from factor text (`value`/`name`/`subcategory`).
    Confidence is treated as signal strength, not direction.
    """
    prior_weight = 3.0
    yes_score = _clamp(market_yes_price) * prior_weight
    no_score = (1.0 - _clamp(market_yes_price)) * prior_weight

    factor_ids: list[str] = []
    categories: set[str] = set()

    for factor in factors:
        category = (factor.category or "unknown").strip().lower() or "unknown"
        categories.add(category)
        factor_ids.append(factor.id)

        weight = float(weights.get(category, 1.0))
        strength = _clamp(float(factor.confidence or 0.5))
        direction = _direction_from_factor(factor)
        signal = _clamp(0.5 + (0.5 * direction * strength))

        # Global factors can add context but should not dominate market-specific factors.
        if getattr(factor, "market_id", None) is None:
            weight *= 0.35

        yes_score += weight * signal
        no_score += weight * (1.0 - signal)

    total = yes_score + no_score
    yes_prob = (yes_score / total) if total > 0 else _clamp(market_yes_price)
    predicted_outcome = "YES" if yes_prob >= 0.5 else "NO"
    confidence = abs(yes_prob - 0.5) * 2.0

    return {
        "predicted_outcome": predicted_outcome,
        "confidence": round(_clamp(confidence), 4),
        "factor_ids": factor_ids,
        "factor_categories": sorted(categories),
    }


def _dedupe_latest_factors(factors: list[Factor]) -> list[Factor]:
    """Keep only the latest factor per semantic key to avoid giant duplicate fanout."""
    latest: dict[tuple, Factor] = {}
    for factor in factors:
        key = (
            str(getattr(factor, "market_id", "") or ""),
            str(getattr(factor, "category", "") or "").strip().lower(),
            str(getattr(factor, "subcategory", "") or "").strip().lower(),
            str(getattr(factor, "name", "") or "").strip().lower(),
            str(getattr(factor, "source", "") or "").strip().lower(),
        )
        prev = latest.get(key)
        if prev is None:
            latest[key] = factor
            continue
        prev_ts = getattr(prev, "timestamp", None)
        cur_ts = getattr(factor, "timestamp", None)
        if cur_ts is not None and (prev_ts is None or cur_ts > prev_ts):
            latest[key] = factor
    return list(latest.values())


async def generate_all_predictions(
    *,
    factor_window_hours: int = 24,
    cooldown_minutes: int = 60,
    max_factor_ids: int = 100,
    commit_every: int = 500,
) -> int:
    """Generate at most one fresh prediction per market within cooldown window.

    Returns:
        Number of predictions created in this cycle.
    """
    now = _utcnow_naive()
    factor_cutoff = now - timedelta(hours=factor_window_hours)
    recent_cutoff = now - timedelta(minutes=cooldown_minutes)

    async with SessionLocal() as session:
        weights_rows = (
            await session.execute(select(FactorWeight))
        ).scalars().all()
        # Build nested weights: {market_category: {factor_category: weight}}
        weights_by_mcat: dict[str, dict[str, float]] = {}
        for row in weights_rows:
            mcat = (row.market_category or "all").strip().lower() or "all"
            fcat = (row.category or "unknown").strip().lower() or "unknown"
            if mcat not in weights_by_mcat:
                weights_by_mcat[mcat] = {}
            weights_by_mcat[mcat][fcat] = float(row.weight)

        markets = (
            await session.execute(select(Market).where(Market.active == True))
        ).scalars().all()

        recent_market_ids = set(
            (
                await session.execute(
                    select(Prediction.market_id)
                    .where(Prediction.created_at >= recent_cutoff)
                    .distinct()
                )
            ).scalars().all()
        )

        factor_rows = (
            await session.execute(
                select(Factor)
                .where(Factor.timestamp >= factor_cutoff)
            )
        ).scalars().all()
        factor_rows = _dedupe_latest_factors(factor_rows)
        global_factors: list[Factor] = []
        market_factor_map: dict[str, list[Factor]] = {}
        for factor in factor_rows:
            if getattr(factor, "market_id", None) is None:
                global_factors.append(factor)
                continue
            market_factor_map.setdefault(factor.market_id, []).append(factor)

        created = 0
        pending_since_commit = 0
        for market in markets:
            if market.id in recent_market_ids:
                continue

            factors = list(global_factors)
            factors.extend(market_factor_map.get(market.id, []))

            if not factors:
                continue

            # Get weights for this market's category, fallback to "all"
            mkt_cat = (getattr(market, "market_category", "") or "other").strip().lower() or "other"
            weights = weights_by_mcat.get(mkt_cat, weights_by_mcat.get("all", {}))

            signal = _predict_from_factors(
                factors=factors,
                market_yes_price=float(market.yes_price or 0.5),
                weights=weights,
            )

            session.add(
                Prediction(
                    market_id=market.id,
                    predicted_outcome=signal["predicted_outcome"],
                    confidence=signal["confidence"],
                    entry_yes_price=float(market.yes_price or 0.5),
                    factor_ids=json.dumps(signal["factor_ids"][:max_factor_ids]),
                    factor_categories=json.dumps(signal["factor_categories"]),
                )
            )
            created += 1
            pending_since_commit += 1

            if pending_since_commit >= commit_every:
                await session.commit()
                pending_since_commit = 0

        await session.commit()

    if created:
        log.info("Generated %d factor-based predictions", created)
    return created
