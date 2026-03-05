"""FastAPI service for PolyEdge dashboard."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from sqlalchemy import select, func, desc, text
from polyedge.db import SessionLocal
from polyedge.models import Market, Factor, Prediction, FactorWeight, PriceSnapshot
import json
import importlib.resources as pkg_resources

app = FastAPI(title="PolyEdge")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    import pathlib
    html_path = pathlib.Path(__file__).parent / "static" / "dashboard.html"
    return HTMLResponse(html_path.read_text())


@app.get("/api/stats")
async def stats():
    async with SessionLocal() as session:
        total_markets = (await session.execute(select(func.count(Market.id)))).scalar()
        active_markets = (await session.execute(select(func.count(Market.id)).where(Market.active == True))).scalar()
        total_factors = (await session.execute(select(func.count(Factor.id)))).scalar()
        total_predictions = (await session.execute(select(func.count(Prediction.id)))).scalar()
        correct = (await session.execute(select(func.count(Prediction.id)).where(Prediction.correct == True))).scalar()
        scored = (await session.execute(select(func.count(Prediction.id)).where(Prediction.correct != None))).scalar()

        # Factor breakdown by source
        source_counts = (await session.execute(
            select(Factor.source, func.count(Factor.id)).group_by(Factor.source)
        )).all()

        # Factor breakdown by category (top 15)
        cat_counts = (await session.execute(
            select(Factor.category, func.count(Factor.id))
            .group_by(Factor.category).order_by(desc(func.count(Factor.id))).limit(15)
        )).all()

        # Recent activity timestamps
        last_factor = (await session.execute(
            select(Factor.timestamp).order_by(desc(Factor.timestamp)).limit(1)
        )).scalar()
        last_prediction = (await session.execute(
            select(Prediction.created_at).order_by(desc(Prediction.created_at)).limit(1)
        )).scalar()
        last_poll = (await session.execute(
            select(Market.updated_at).order_by(desc(Market.updated_at)).limit(1)
        )).scalar()

        return {
            "total_markets": total_markets, "active_markets": active_markets,
            "total_factors": total_factors, "total_predictions": total_predictions,
            "scored_predictions": scored, "correct_predictions": correct,
            "overall_hit_rate": round(correct / scored, 4) if scored else None,
            "factors_by_source": {row[0]: row[1] for row in source_counts},
            "factors_by_category": {row[0]: row[1] for row in cat_counts},
            "last_factor_at": str(last_factor) if last_factor else None,
            "last_prediction_at": str(last_prediction) if last_prediction else None,
            "last_poll_at": str(last_poll) if last_poll else None,
        }


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


@app.get("/api/factors/recent")
async def recent_factors(limit: int = 50):
    async with SessionLocal() as session:
        factors = (await session.execute(
            select(Factor).order_by(desc(Factor.timestamp)).limit(limit)
        )).scalars().all()
        return [_factor_dict(f) for f in factors]


@app.get("/api/factors/weights")
async def factor_weights():
    async with SessionLocal() as session:
        weights = (await session.execute(
            select(FactorWeight).order_by(desc(FactorWeight.hit_rate))
        )).scalars().all()
        return [{"category": w.category, "hit_rate": w.hit_rate, "total": w.total_predictions,
                 "correct": w.correct_predictions, "weight": w.weight} for w in weights]


@app.get("/api/predictions/recent")
async def recent_predictions(limit: int = 50):
    async with SessionLocal() as session:
        preds = (await session.execute(
            select(Prediction).order_by(desc(Prediction.created_at)).limit(limit)
        )).scalars().all()
        result = []
        for p in preds:
            market = await session.get(Market, p.market_id)
            result.append({
                **_pred_dict(p),
                "market_question": market.question if market else "?",
                "current_yes_price": market.yes_price if market else None,
            })
        return result


@app.get("/api/predictions/scored")
async def scored_predictions(limit: int = 50):
    async with SessionLocal() as session:
        preds = (await session.execute(
            select(Prediction).where(Prediction.correct != None)
            .order_by(desc(Prediction.resolved_at)).limit(limit)
        )).scalars().all()
        result = []
        for p in preds:
            market = await session.get(Market, p.market_id)
            result.append({
                **_pred_dict(p),
                "market_question": market.question if market else "?",
            })
        return result


@app.get("/api/predictions/{prediction_id}/analysis")
async def prediction_analysis(prediction_id: str):
    """Full post-resolution factor breakdown for a single prediction."""
    async with SessionLocal() as session:
        pred = await session.get(Prediction, prediction_id)
        if not pred:
            return {"error": "not found"}
        market = await session.get(Market, pred.market_id)

        # Load the actual factors that were used
        factor_ids = json.loads(pred.factor_ids) if pred.factor_ids else []
        factors = []
        if factor_ids:
            factor_rows = (await session.execute(
                select(Factor).where(Factor.id.in_(factor_ids[:100]))
            )).scalars().all()
            factors = [_factor_dict(f) for f in factor_rows]

        # Compute per-factor vote direction (same logic as predictor)
        yes_signals = ["yes", "bullish", "positive", "up", "likely", "probable", "true", "support"]
        no_signals = ["no", "bearish", "negative", "down", "unlikely", "improbable", "false", "oppose"]
        for f in factors:
            val = (f.get("value") or "").lower()
            if any(s in val for s in yes_signals):
                f["vote"] = "YES"
            elif any(s in val for s in no_signals):
                f["vote"] = "NO"
            else:
                f["vote"] = "neutral"

        # Category-level summary
        cats = json.loads(pred.factor_categories) if pred.factor_categories else []
        weights = (await session.execute(select(FactorWeight))).scalars().all()
        weight_map = {w.category: {"weight": w.weight, "hit_rate": w.hit_rate, "total": w.total_predictions} for w in weights}
        category_breakdown = []
        for cat in cats:
            cat_factors = [f for f in factors if f.get("category") == cat]
            w = weight_map.get(cat, {})
            category_breakdown.append({
                "category": cat,
                "factor_count": len(cat_factors),
                "weight": w.get("weight", 1.0),
                "hit_rate": w.get("hit_rate"),
                "total_scored": w.get("total", 0),
                "factors": cat_factors,
            })

        return {
            "prediction": {**_pred_dict(pred), "market_question": market.question if market else "?",
                           "market_resolution": market.resolution if market else None,
                           "current_yes_price": market.yes_price if market else None},
            "total_factors_used": len(factor_ids),
            "factors_found": len(factors),
            "category_breakdown": category_breakdown,
        }


@app.get("/api/analysis/scored")
async def scored_analysis(limit: int = 20):
    """Scored predictions with inline factor breakdown — for the dashboard."""
    async with SessionLocal() as session:
        preds = (await session.execute(
            select(Prediction).where(Prediction.correct != None)
            .order_by(desc(Prediction.resolved_at)).limit(limit)
        )).scalars().all()
        if not preds:
            return []

        # Batch-load all factor IDs
        all_fids = []
        for p in preds:
            all_fids.extend(json.loads(p.factor_ids) if p.factor_ids else [])
        factor_map = {}
        if all_fids:
            rows = (await session.execute(
                select(Factor).where(Factor.id.in_(list(set(all_fids))[:500]))
            )).scalars().all()
            factor_map = {f.id: _factor_dict(f) for f in rows}

        result = []
        for p in preds:
            market = await session.get(Market, p.market_id)
            fids = json.loads(p.factor_ids) if p.factor_ids else []
            cats = json.loads(p.factor_categories) if p.factor_categories else []
            factors = [factor_map[fid] for fid in fids if fid in factor_map]
            result.append({
                **_pred_dict(p),
                "market_question": market.question if market else "?",
                "market_resolution": market.resolution if market else None,
                "factors_used": len(fids),
                "categories": cats,
                "top_factors": factors[:5],  # first 5 for dashboard brevity
            })
        return result


def _market_dict(m):
    return {"id": m.id, "question": m.question, "category": m.category,
            "yes_price": m.yes_price, "volume": m.volume, "active": m.active,
            "resolved": m.resolved, "resolution": m.resolution, "end_date": str(m.end_date)}


def _factor_dict(f):
    return {"id": f.id, "category": f.category, "subcategory": f.subcategory,
            "name": f.name, "value": f.value, "source": f.source,
            "confidence": f.confidence, "timestamp": str(f.timestamp),
            "market_id": f.market_id}


def _pred_dict(p):
    return {"id": p.id, "predicted_outcome": p.predicted_outcome, "confidence": p.confidence,
            "entry_yes_price": p.entry_yes_price, "correct": p.correct,
            "created_at": str(p.created_at), "market_id": p.market_id,
            "factor_categories": p.factor_categories}
