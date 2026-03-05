"""FastAPI service for PolyEdge dashboard."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, func, desc
from polyedge.db import SessionLocal
from polyedge.models import Market, Factor, Prediction, FactorWeight

app = FastAPI(title="PolyEdge")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/api/markets")
async def list_markets(active: bool = True, limit: int = 50):
    async with SessionLocal() as session:
        q = select(Market).order_by(desc(Market.volume)).limit(limit)
        if active:
            q = q.where(Market.active == True)
        markets = (await session.execute(q)).scalars().all()
        return [_market_dict(m) for m in markets]


@app.get("/api/markets/{market_id}")
async def get_market(market_id: str):
    async with SessionLocal() as session:
        market = await session.get(Market, market_id)
        if not market:
            return {"error": "not found"}
        factors = (await session.execute(
            select(Factor).where(Factor.market_id == market_id).order_by(desc(Factor.timestamp)).limit(50)
        )).scalars().all()
        predictions = (await session.execute(
            select(Prediction).where(Prediction.market_id == market_id).order_by(desc(Prediction.created_at))
        )).scalars().all()
        return {
            **_market_dict(market),
            "factors": [_factor_dict(f) for f in factors],
            "predictions": [_pred_dict(p) for p in predictions],
        }


@app.get("/api/factors/weights")
async def factor_weights():
    async with SessionLocal() as session:
        weights = (await session.execute(
            select(FactorWeight).order_by(desc(FactorWeight.hit_rate))
        )).scalars().all()
        return [{"category": w.category, "hit_rate": w.hit_rate, "total": w.total_predictions,
                 "correct": w.correct_predictions, "weight": w.weight} for w in weights]


@app.get("/api/stats")
async def stats():
    async with SessionLocal() as session:
        total_markets = (await session.execute(select(func.count(Market.id)))).scalar()
        active_markets = (await session.execute(select(func.count(Market.id)).where(Market.active == True))).scalar()
        total_factors = (await session.execute(select(func.count(Factor.id)))).scalar()
        total_predictions = (await session.execute(select(func.count(Prediction.id)))).scalar()
        correct = (await session.execute(select(func.count(Prediction.id)).where(Prediction.correct == True))).scalar()
        scored = (await session.execute(select(func.count(Prediction.id)).where(Prediction.correct != None))).scalar()
        return {
            "total_markets": total_markets, "active_markets": active_markets,
            "total_factors": total_factors, "total_predictions": total_predictions,
            "scored_predictions": scored, "correct_predictions": correct,
            "overall_hit_rate": round(correct / scored, 4) if scored else None,
        }


def _market_dict(m):
    return {"id": m.id, "question": m.question, "category": m.category,
            "yes_price": m.yes_price, "volume": m.volume, "active": m.active,
            "resolved": m.resolved, "resolution": m.resolution, "end_date": str(m.end_date)}


def _factor_dict(f):
    return {"id": f.id, "category": f.category, "name": f.name, "value": f.value,
            "source": f.source, "confidence": f.confidence, "timestamp": str(f.timestamp)}


def _pred_dict(p):
    return {"id": p.id, "predicted_outcome": p.predicted_outcome, "confidence": p.confidence,
            "entry_yes_price": p.entry_yes_price, "correct": p.correct, "created_at": str(p.created_at)}
