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
    """Generate predictions for every active market with recent factors."""
    async with SessionLocal() as session:
        weights_rows = (await session.execute(select(FactorWeight))).scalars().all()
        weights = {w.category: w.weight for w in weights_rows}

        markets = (await session.execute(
            select(Market).where(Market.active == True)
        )).scalars().all()

        count = 0
        for market in markets:
            cutoff = datetime.utcnow() - timedelta(hours=24)
            factors_rows = (await session.execute(
                select(Factor).where(
                    and_(
                        Factor.timestamp > cutoff,
                        (Factor.market_id == market.id) | (Factor.market_id == None),
                    )
                )
            )).scalars().all()

            if not factors_rows:
                continue

            factors = [{"category": f.category, "value": f.value, "confidence": f.confidence} for f in factors_rows]
            factor_ids = [f.id for f in factors_rows]
            categories = list({f.category for f in factors_rows})

            result = make_prediction(factors, market.yes_price, weights)

            session.add(Prediction(
                market_id=market.id,
                predicted_outcome=result["predicted_outcome"],
                confidence=result["confidence"],
                entry_yes_price=market.yes_price,
                factor_ids=json.dumps(factor_ids[:100]),
                factor_categories=json.dumps(categories),
            ))
            count += 1

        await session.commit()
        log.info("Generated %d predictions", count)
