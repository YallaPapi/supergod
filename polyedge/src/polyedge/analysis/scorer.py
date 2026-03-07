"""Score predictions and update factor category weights."""
import json
import logging
from datetime import datetime, timezone
from sqlalchemy import select
from polyedge.db import SessionLocal
from polyedge.db import settings as db_settings
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


def _parse_metrics_cutoff(raw: str | None) -> datetime | None:
    """Parse optional prediction-metrics cutoff from env config."""
    if not raw:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        log.warning("Invalid POLYEDGE_PREDICTION_METRICS_CUTOFF=%r; ignoring cutoff", raw)
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def prediction_metrics_cutoff() -> datetime | None:
    return _parse_metrics_cutoff(getattr(db_settings, "prediction_metrics_cutoff", ""))


def _parse_resolution_sources(raw: str | None) -> set[str]:
    if raw is None:
        return {"polymarket_api"}
    text = str(raw).strip()
    if not text:
        return {"polymarket_api"}
    values = {s.strip().lower() for s in text.split(",") if s.strip()}
    return values or {"polymarket_api"}


def prediction_resolution_sources() -> set[str]:
    return _parse_resolution_sources(
        getattr(db_settings, "prediction_resolution_sources", "polymarket_api")
    )


async def score_resolved_markets():
    async with SessionLocal() as session:
        sources = prediction_resolution_sources()
        stmt = (
            select(Prediction, Market)
            .join(Market, Prediction.market_id == Market.id)
            .where(Prediction.correct == None)
            .where(Market.resolution.in_(["YES", "NO"]))
        )
        if "*" not in sources:
            stmt = stmt.where(Market.resolution_source.in_(sources))
        results = (await session.execute(stmt)).all()
        if not results:
            return
        scored = 0
        for pred, market in results:
            pred.correct = pred.predicted_outcome == market.resolution
            pred.resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)
            scored += 1
        await session.commit()
        if scored:
            log.info("Scored %d predictions", scored)
    await recalculate_weights()


async def recalculate_weights():
    async with SessionLocal() as session:
        stmt = select(Prediction).where(Prediction.correct != None)
        cutoff = prediction_metrics_cutoff()
        if cutoff is not None:
            stmt = stmt.where(Prediction.created_at >= cutoff)
        scored = (await session.execute(stmt)).scalars().all()

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
