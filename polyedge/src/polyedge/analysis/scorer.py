"""Score predictions and update factor category weights."""
import json
import logging
from datetime import datetime
from sqlalchemy import select, and_
from polyedge.db import SessionLocal
from polyedge.models import Prediction, Market, FactorWeight

log = logging.getLogger(__name__)


def score_category(correct: int, total: int) -> dict:
    hit_rate = correct / total if total > 0 else 0.5
    return {
        "hit_rate": round(hit_rate, 4),
        "total_predictions": total,
        "correct_predictions": correct,
        "weight": _hit_rate_to_weight(hit_rate, total),
    }


def _hit_rate_to_weight(hit_rate: float, sample_size: int) -> float:
    if sample_size < 10:
        return 1.0
    if hit_rate <= 0.5:
        return 0.1
    return 1.0 + (hit_rate - 0.5) * 4


def _infer_resolution(market) -> str | None:
    """Infer resolution from market data even if not explicitly marked."""
    if market.resolution:
        return market.resolution
    # Price near 0/1 means effectively resolved
    if market.yes_price >= 0.95:
        return "YES"
    if market.no_price >= 0.95:
        return "NO"
    # Past end date with decisive price
    if market.end_date and market.end_date < datetime.utcnow():
        if market.yes_price >= 0.8:
            return "YES"
        if market.no_price >= 0.8:
            return "NO"
    return None


async def score_resolved_markets():
    async with SessionLocal() as session:
        # Score explicitly resolved markets
        stmt = (
            select(Prediction, Market)
            .join(Market, Prediction.market_id == Market.id)
            .where(Prediction.correct == None)
        )
        results = (await session.execute(stmt)).all()
        if not results:
            return
        scored = 0
        for pred, market in results:
            resolution = _infer_resolution(market)
            if not resolution:
                continue
            pred.correct = pred.predicted_outcome == resolution
            pred.resolved_at = datetime.utcnow()
            # Also set resolution on market if missing
            if not market.resolution:
                market.resolution = resolution
            scored += 1
        await session.commit()
        if scored:
            log.info("Scored %d predictions", scored)
    await recalculate_weights()


async def recalculate_weights():
    async with SessionLocal() as session:
        scored = (await session.execute(
            select(Prediction).where(Prediction.correct != None)
        )).scalars().all()

    cat_stats: dict[str, dict] = {}
    for pred in scored:
        cats = json.loads(pred.factor_categories) if pred.factor_categories else []
        for cat in cats:
            if cat not in cat_stats:
                cat_stats[cat] = {"correct": 0, "total": 0}
            cat_stats[cat]["total"] += 1
            if pred.correct:
                cat_stats[cat]["correct"] += 1

    async with SessionLocal() as session:
        for cat, stats in cat_stats.items():
            scores = score_category(stats["correct"], stats["total"])
            existing = await session.get(FactorWeight, cat)
            if existing:
                for k, v in scores.items():
                    setattr(existing, k, v)
                existing.updated_at = datetime.utcnow()
            else:
                session.add(FactorWeight(category=cat, **scores))
        await session.commit()
    log.info("Recalculated weights for %d categories", len(cat_stats))
