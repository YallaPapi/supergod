"""Generate predictions for all active markets."""

import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import select, and_
from polyedge.db import SessionLocal
from polyedge.models import Market, Factor, Prediction, FactorWeight
from polyedge.analysis.predictor import make_prediction

log = logging.getLogger(__name__)


async def generate_all_predictions():
    """Generate predictions for EVERY active market. No exceptions."""
    async with SessionLocal() as session:
        weights_rows = (await session.execute(select(FactorWeight))).scalars().all()
        weights = {w.category: w.weight for w in weights_rows}

        markets = (await session.execute(
            select(Market).where(Market.active == True)
        )).scalars().all()

        # Load global factors once (market_id IS NULL, last 24h)
        cutoff = datetime.utcnow() - timedelta(hours=24)
        global_factors = (await session.execute(
            select(Factor).where(
                and_(Factor.timestamp > cutoff, Factor.market_id == None)
            )
        )).scalars().all()

        global_dicts = [{"category": f.category, "value": f.value, "confidence": f.confidence} for f in global_factors]
        global_ids = [f.id for f in global_factors]
        global_cats = {f.category for f in global_factors}

        count = 0
        for market in markets:
            # Market-specific factors
            market_factors = (await session.execute(
                select(Factor).where(
                    and_(Factor.timestamp > cutoff, Factor.market_id == market.id)
                )
            )).scalars().all()

            market_dicts = [{"category": f.category, "value": f.value, "confidence": f.confidence} for f in market_factors]
            market_ids = [f.id for f in market_factors]
            market_cats = {f.category for f in market_factors}

            all_factors = global_dicts + market_dicts
            all_ids = global_ids + market_ids
            all_cats = list(global_cats | market_cats)

            # Predict even with zero factors — use market price as baseline
            result = make_prediction(all_factors, market.yes_price, weights)

            session.add(Prediction(
                market_id=market.id,
                predicted_outcome=result["predicted_outcome"],
                confidence=result["confidence"],
                entry_yes_price=market.yes_price,
                factor_ids=json.dumps(all_ids[:100]),
                factor_categories=json.dumps(all_cats),
            ))
            count += 1

        await session.commit()
        log.info("Generated %d predictions for %d active markets", count, len(markets))
