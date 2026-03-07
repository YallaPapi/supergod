"""FastAPI dashboard for PolyEdge v3."""

from datetime import datetime, timedelta, timezone
import os
import socket

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from sqlalchemy import select, func, desc, text, case, cast, Numeric
from polyedge.db import SessionLocal
from polyedge.analysis.scorer import prediction_metrics_cutoff
from polyedge.db import settings as db_settings
from polyedge.query_filters import real_trade_predicates
from polyedge.models import (
    Market, TradingRule, PaperTrade, DailyFeature,
    NgramStat, Prediction, Factor, FactorWeight, PriceSnapshot, ServiceHeartbeat,
)
import json
import pathlib
import logging

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Human-readable helpers
# ---------------------------------------------------------------------------

def _rule_to_plain_english(rule) -> str:
    """Translate a TradingRule into a sentence a non-technical person can read."""
    try:
        cond = (
            json.loads(rule.conditions_json)
            if isinstance(rule.conditions_json, str)
            else (rule.conditions_json or {})
        )
    except (json.JSONDecodeError, TypeError):
        cond = {}

    side = rule.predicted_side or "?"
    pct = f"{(rule.win_rate or 0) * 100:.0f}%"
    n = rule.sample_size or 0

    if rule.rule_type == "ngram":
        phrase = cond.get("ngram", rule.name.replace("ngram:", ""))
        return (
            f'Markets with "{phrase}" in the question resolve {side} '
            f"{pct} of the time ({n:,} markets tested)"
        )

    if rule.rule_type == "single_threshold":
        feat = (cond.get("feature") or "?").replace("_", " ")
        op = cond.get("op", ">")
        val = cond.get("value", "?")
        return (
            f"When {feat} is {op} {val}, markets tend to resolve {side} "
            f"({pct} over {n:,} tests)"
        )

    if rule.rule_type == "two_feature":
        parts = []
        for f in cond.get("features", []):
            feat = (f.get("feature") or "?").replace("_", " ")
            parts.append(f"{feat} {f.get('op', '>')} {f.get('value', '?')}")
        combo = " AND ".join(parts) or "multiple conditions"
        return f"When {combo}, markets resolve {side} ({pct} over {n:,} tests)"

    if rule.rule_type == "decision_tree":
        parts = []
        for p in cond.get("path", []):
            feat = (p.get("feature") or "?").replace("_", " ")
            parts.append(f"{feat} {p.get('op', '>')} {p.get('value', '?')}")
        path_str = " then ".join(parts) or "complex pattern"
        return f"Decision path: {path_str} -> {side} ({pct} over {n:,} tests)"

    return f"Rule '{rule.name}' predicts {side} ({pct} over {n:,} tests)"


def _confidence_label(pct: float) -> str:
    if pct >= 80:
        return "High"
    if pct >= 55:
        return "Medium"
    if pct >= 30:
        return "Low"
    return "Very Low"


def _freshness_label(minutes: int | None) -> str:
    if minutes is None:
        return "Unknown"
    if minutes <= 15:
        return "Fresh"
    if minutes <= 60:
        return "Recent"
    if minutes <= 360:
        return "Stale"
    return "Old"

app = FastAPI(title="PolyEdge v3")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso_utc(ts: datetime | None) -> str | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    else:
        ts = ts.astimezone(timezone.utc)
    return ts.isoformat().replace("+00:00", "Z")


def _market_dict(m: Market) -> dict:
    return {
        "id": m.id,
        "question": m.question,
        "slug": m.slug,
        "category": m.category,
        "yes_price": m.yes_price,
        "no_price": m.no_price,
        "volume": m.volume,
        "liquidity": m.liquidity,
        "active": m.active,
        "resolved": m.resolved,
        "resolution": m.resolution,
        "resolution_source": m.resolution_source,
        "end_date": _iso_utc(m.end_date),
        "updated_at": _iso_utc(m.updated_at),
    }


def _factor_dict(f: Factor) -> dict:
    return {
        "id": f.id,
        "market_id": f.market_id,
        "category": f.category,
        "subcategory": f.subcategory,
        "name": f.name,
        "value": f.value,
        "source": f.source,
        "confidence": f.confidence,
        "timestamp": _iso_utc(f.timestamp),
    }


def _prediction_dict(p: Prediction) -> dict:
    return {
        "id": p.id,
        "market_id": p.market_id,
        "predicted_outcome": p.predicted_outcome,
        "confidence": p.confidence,
        "entry_yes_price": p.entry_yes_price,
        "factor_ids": p.factor_ids,
        "factor_categories": p.factor_categories,
        "correct": p.correct,
        "created_at": _iso_utc(p.created_at),
        "resolved_at": _iso_utc(p.resolved_at),
    }


def _backtest_results_candidates() -> list[pathlib.Path]:
    configured = (os.environ.get("POLYEDGE_BACKTEST_PATH", "") or "").strip()
    candidates: list[pathlib.Path] = []
    if configured:
        candidates.append(pathlib.Path(configured))

    repo_root = pathlib.Path(__file__).resolve().parents[2]
    candidates.extend(
        [
            pathlib.Path("data/results/latest_backtest.json"),
            repo_root / "data" / "results" / "latest_backtest.json",
            pathlib.Path("C:/polyedge/data/results/latest_backtest.json"),
        ]
    )
    return candidates


def _prediction_edge_pct(
    predicted_outcome: str | None,
    confidence: float | None,
    yes_price: float | None,
) -> float | None:
    """Return edge in percentage points relative to market price."""
    if predicted_outcome is None or confidence is None or yes_price is None:
        return None
    if confidence < 0 or confidence > 1 or yes_price < 0 or yes_price > 1:
        return None

    side = predicted_outcome.upper().strip()
    if side == "YES":
        return round((confidence - yes_price) * 100.0, 2)
    if side == "NO":
        market_no = 1.0 - yes_price
        return round((confidence - market_no) * 100.0, 2)
    return None


def _minutes_since(ts: datetime | None, now: datetime) -> int | None:
    if ts is None:
        return None
    return max(0, int((now - ts).total_seconds() // 60))


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    html_path = pathlib.Path(__file__).parent / "static" / "dashboard.html"
    return HTMLResponse(html_path.read_text())


@app.get("/api/dashboard")
async def dashboard_summary():
    """Top-bar summary: system health overview."""
    try:
        async with SessionLocal() as session:
            total_markets = (
                await session.execute(select(func.count(Market.id)))
            ).scalar() or 0
            active_markets = (
                await session.execute(
                    select(func.count(Market.id)).where(Market.active == True)
                )
            ).scalar() or 0

            total_rules = (
                await session.execute(
                    select(func.count(TradingRule.id)).where(TradingRule.active == True)
                )
            ).scalar() or 0

            total_features_today = (
                await session.execute(
                    select(func.count(DailyFeature.id)).where(
                        DailyFeature.date == text("CURRENT_DATE")
                    )
                )
            ).scalar() or 0

            open_trades = (
                await session.execute(
                    select(func.count(PaperTrade.id)).where(
                        PaperTrade.resolved == False
                    )
                )
            ).scalar() or 0

            closed_trades = (
                await session.execute(
                    select(func.count(PaperTrade.id)).where(
                        PaperTrade.resolved == True
                    )
                )
            ).scalar() or 0

            total_pnl = (
                await session.execute(
                    select(func.sum(PaperTrade.pnl)).where(
                        PaperTrade.resolved == True
                    )
                )
            ).scalar() or 0.0

            wins = (
                await session.execute(
                    select(func.count(PaperTrade.id)).where(
                        PaperTrade.resolved == True, PaperTrade.won == True
                    )
                )
            ).scalar() or 0

            win_rate = wins / closed_trades if closed_trades > 0 else 0.0

            # Prediction accuracy metrics: optionally filtered to post-fix window.
            cutoff = prediction_metrics_cutoff()
            scored_stmt = select(func.count(Prediction.id)).where(Prediction.correct != None)
            correct_stmt = select(func.count(Prediction.id)).where(Prediction.correct == True)
            if cutoff is not None:
                scored_stmt = scored_stmt.where(Prediction.created_at >= cutoff)
                correct_stmt = correct_stmt.where(Prediction.created_at >= cutoff)

            scored_predictions = (await session.execute(scored_stmt)).scalar() or 0
            correct_predictions = (await session.execute(correct_stmt)).scalar() or 0
            prediction_hit_rate = (
                correct_predictions / scored_predictions if scored_predictions > 0 else None
            )

            resolved_total = (
                await session.execute(
                    select(func.count(Market.id)).where(
                        Market.resolution != None,  # noqa: E711
                        Market.resolution != "",
                    )
                )
            ).scalar() or 0
            resolved_authoritative = (
                await session.execute(
                    select(func.count(Market.id)).where(
                        Market.resolution != None,  # noqa: E711
                        Market.resolution != "",
                        Market.resolution_source == "polymarket_api",
                    )
                )
            ).scalar() or 0
            resolved_with_source = (
                await session.execute(
                    select(func.count(Market.id)).where(
                        Market.resolution != None,  # noqa: E711
                        Market.resolution != "",
                        Market.resolution_source != "",
                    )
                )
            ).scalar() or 0
            resolved_inferred = (
                await session.execute(
                    select(func.count(Market.id)).where(
                        Market.resolution != None,  # noqa: E711
                        Market.resolution != "",
                        Market.resolution_source == "inferred",
                    )
                )
            ).scalar() or 0
            authoritative_label_rate = (
                resolved_authoritative / resolved_total if resolved_total else None
            )
            labeled_source_coverage = (
                resolved_with_source / resolved_total if resolved_total else None
            )
            inferred_label_rate = (
                resolved_inferred / resolved_total if resolved_total else None
            )

            recent_cutoff = _utcnow_naive() - timedelta(hours=24)
            dup_rows = (
                await session.execute(
                    select(
                        Prediction.market_id,
                        Prediction.predicted_outcome,
                        cast(Prediction.entry_yes_price, Numeric(10, 3)),
                        func.count(Prediction.id),
                    )
                    .where(Prediction.created_at >= recent_cutoff)
                    .group_by(
                        Prediction.market_id,
                        Prediction.predicted_outcome,
                        cast(Prediction.entry_yes_price, Numeric(10, 3)),
                    )
                )
            ).all()
            dup_total = len(dup_rows)
            dup_groups = sum(1 for _, _, _, count in dup_rows if (count or 0) > 1)
            duplicate_prediction_rate = (dup_groups / dup_total) if dup_total else None

            return {
                "total_markets": total_markets,
                "active_markets": active_markets,
                "active_rules": total_rules,
                "features_today": total_features_today,
                "open_trades": open_trades,
                "closed_trades": closed_trades,
                "total_pnl": round(float(total_pnl), 2),
                "win_rate": round(win_rate, 4),
                "prediction_scored": scored_predictions,
                "prediction_correct": correct_predictions,
                "prediction_hit_rate": (
                    round(prediction_hit_rate, 4) if prediction_hit_rate is not None else None
                ),
                "authoritative_label_rate": (
                    round(authoritative_label_rate, 4)
                    if authoritative_label_rate is not None
                    else None
                ),
                "labeled_source_coverage": (
                    round(labeled_source_coverage, 4)
                    if labeled_source_coverage is not None
                    else None
                ),
                "inferred_label_rate": (
                    round(inferred_label_rate, 4)
                    if inferred_label_rate is not None
                    else None
                ),
                "duplicate_prediction_rate_24h": (
                    round(duplicate_prediction_rate, 4)
                    if duplicate_prediction_rate is not None
                    else None
                ),
                "prediction_metrics_cutoff": str(cutoff) if cutoff else None,
            }
    except Exception as e:
        log.exception("dashboard_summary failed")
        return {
            "total_markets": 0, "active_markets": 0, "active_rules": 0,
            "features_today": 0, "open_trades": 0, "closed_trades": 0,
            "total_pnl": 0.0, "win_rate": 0.0, "error": str(e),
        }


@app.get("/api/mission-control")
async def mission_control():
    """Plain-language summary of current edges, data quality, and discovery health."""
    now = _utcnow_naive()
    day_cutoff = now - timedelta(hours=24)
    hour_cutoff = now - timedelta(hours=1)

    try:
        async with SessionLocal() as session:
            open_trades = (
                await session.execute(
                    select(PaperTrade)
                    .where(PaperTrade.resolved == False)  # noqa: E712
                    .order_by(desc(PaperTrade.edge))
                    .limit(5)
                )
            ).scalars().all()

            edge_opportunities = []
            for trade in open_trades:
                market = await session.get(Market, trade.market_id)
                rule = await session.get(TradingRule, trade.rule_id)
                edge_opportunities.append(
                    {
                        "market_id": trade.market_id,
                        "question": (market.question if market else "")[:140],
                        "side": trade.side,
                        "edge_pct": round(float(trade.edge or 0.0) * 100.0, 2),
                        "entry_price": round(float(trade.entry_price or 0.0), 3),
                        "rule_name": (rule.name if rule else f"rule#{trade.rule_id}")[:80],
                    }
                )

            if not edge_opportunities:
                recent_predictions = (
                    await session.execute(
                        select(Prediction, Market)
                        .join(Market, Market.id == Prediction.market_id)
                        .where(
                            Prediction.created_at >= hour_cutoff,
                            Market.active == True,  # noqa: E712
                        )
                        .order_by(desc(Prediction.created_at))
                        .limit(800)
                    )
                ).all()

                best_by_market: dict[str, dict] = {}
                for pred, market in recent_predictions:
                    edge_pct = _prediction_edge_pct(
                        pred.predicted_outcome,
                        pred.confidence,
                        market.yes_price,
                    )
                    if edge_pct is None or edge_pct <= 0:
                        continue
                    side = (pred.predicted_outcome or "").upper()
                    if side not in {"YES", "NO"}:
                        continue
                    existing = best_by_market.get(pred.market_id)
                    if existing and existing.get("edge_pct", 0.0) >= edge_pct:
                        continue
                    entry_price = market.yes_price if side == "YES" else market.no_price
                    best_by_market[pred.market_id] = {
                        "market_id": pred.market_id,
                        "question": (market.question if market else "")[:140],
                        "side": side,
                        "edge_pct": round(edge_pct, 2),
                        "entry_price": round(float(entry_price or 0.0), 3),
                        "rule_name": "model projection",
                    }
                edge_opportunities = sorted(
                    best_by_market.values(),
                    key=lambda row: row.get("edge_pct", 0.0),
                    reverse=True,
                )[:5]

            top_rules = (
                await session.execute(
                    select(TradingRule)
                    .where(TradingRule.active == True)  # noqa: E712
                    .order_by(desc(TradingRule.avg_roi), desc(TradingRule.win_rate))
                    .limit(5)
                )
            ).scalars().all()
            top_rule_rows = [
                {
                    "id": r.id,
                    "name": r.name,
                    "predicted_side": r.predicted_side,
                    "win_rate": round(float(r.win_rate or 0.0), 4),
                    "sample_size": int(r.sample_size or 0),
                    "avg_roi": round(float(r.avg_roi or 0.0), 4),
                }
                for r in top_rules
            ]

            new_rules_24h = (
                await session.execute(
                    select(func.count(TradingRule.id)).where(TradingRule.created_at >= day_cutoff)
                )
            ).scalar() or 0
            features_today = (
                await session.execute(
                    select(func.count(DailyFeature.id)).where(
                        DailyFeature.date == text("CURRENT_DATE")
                    )
                )
            ).scalar() or 0
            recent_factor_rows = (
                await session.execute(
                    select(Factor.source, func.count(Factor.id))
                    .where(Factor.timestamp >= hour_cutoff)
                    .group_by(Factor.source)
                )
            ).all()
            factors_last_hour = {str(source): int(count) for source, count in recent_factor_rows}
            latest_factor_at = (
                await session.execute(select(func.max(Factor.timestamp)))
            ).scalar()
            latest_prediction_at = (
                await session.execute(select(func.max(Prediction.created_at)))
            ).scalar()
            factor_age_minutes = _minutes_since(latest_factor_at, now)
            prediction_age_minutes = _minutes_since(latest_prediction_at, now)

            resolved_total = (
                await session.execute(
                    select(func.count(Market.id)).where(
                        Market.resolution != None,  # noqa: E711
                        Market.resolution != "",
                    )
                )
            ).scalar() or 0
            resolved_authoritative = (
                await session.execute(
                    select(func.count(Market.id)).where(
                        Market.resolution != None,  # noqa: E711
                        Market.resolution != "",
                        Market.resolution_source == "polymarket_api",
                    )
                )
            ).scalar() or 0
            resolved_with_source = (
                await session.execute(
                    select(func.count(Market.id)).where(
                        Market.resolution != None,  # noqa: E711
                        Market.resolution != "",
                        Market.resolution_source != "",
                    )
                )
            ).scalar() or 0
            resolved_inferred = (
                await session.execute(
                    select(func.count(Market.id)).where(
                        Market.resolution != None,  # noqa: E711
                        Market.resolution != "",
                        Market.resolution_source == "inferred",
                    )
                )
            ).scalar() or 0
            authoritative_label_rate = (
                resolved_authoritative / resolved_total if resolved_total else None
            )
            labeled_source_coverage = (
                resolved_with_source / resolved_total if resolved_total else None
            )
            inferred_label_rate = (
                resolved_inferred / resolved_total if resolved_total else None
            )
            source_breakdown_rows = (
                await session.execute(
                    select(Market.resolution_source, func.count(Market.id))
                    .where(
                        Market.resolution != None,  # noqa: E711
                        Market.resolution != "",
                    )
                    .group_by(Market.resolution_source)
                    .order_by(desc(func.count(Market.id)))
                    .limit(5)
                )
            ).all()
            resolution_source_breakdown = {
                (str(source).strip() or "unknown"): int(count)
                for source, count in source_breakdown_rows
            }

            dup_rows = (
                await session.execute(
                    select(
                        Prediction.market_id,
                        Prediction.predicted_outcome,
                        cast(Prediction.entry_yes_price, Numeric(10, 3)),
                        func.count(Prediction.id),
                    )
                    .where(Prediction.created_at >= day_cutoff)
                    .group_by(
                        Prediction.market_id,
                        Prediction.predicted_outcome,
                        cast(Prediction.entry_yes_price, Numeric(10, 3)),
                    )
                )
            ).all()
            dup_total = len(dup_rows)
            dup_groups = sum(1 for _, _, _, count in dup_rows if (count or 0) > 1)
            duplicate_prediction_rate = (dup_groups / dup_total) if dup_total else None

            summary_lines = []
            if open_trades and edge_opportunities:
                top = edge_opportunities[0]
                summary_lines.append(
                    f"Top live edge: {top['side']} on '{top['question'][:70]}' with {top['edge_pct']:.1f}% estimated edge."
                )
            elif edge_opportunities:
                top = edge_opportunities[0]
                summary_lines.append(
                    f"No open paper trades yet. Top model-priced edge now: {top['side']} on '{top['question'][:70]}' with {top['edge_pct']:.1f}% edge."
                )
            else:
                summary_lines.append("No open paper-trading edge right now; waiting for fresh setups.")

            if top_rule_rows:
                best_rule = top_rule_rows[0]
                summary_lines.append(
                    f"Best active rule is '{best_rule['name'][:60]}' at {best_rule['win_rate'] * 100:.1f}% hit rate over {best_rule['sample_size']} samples."
                )
            else:
                summary_lines.append("No active rules loaded yet.")

            if authoritative_label_rate is None:
                summary_lines.append("Label quality check is waiting for resolved-market data.")
            else:
                summary_lines.append(
                    f"Label quality: {authoritative_label_rate * 100:.1f}% authoritative settlement labels, {inferred_label_rate * 100:.1f}% inferred labels."
                )

            if duplicate_prediction_rate is not None:
                summary_lines.append(
                    f"Duplicate prediction signature rate (24h): {duplicate_prediction_rate * 100:.1f}%."
                )

            if factor_age_minutes is None and prediction_age_minutes is None:
                summary_lines.append("Freshness check: no factor/prediction timestamps available yet.")
            elif factor_age_minutes is not None and prediction_age_minutes is not None:
                summary_lines.append(
                    f"Freshness: latest factors {factor_age_minutes} min ago, latest predictions {prediction_age_minutes} min ago."
                )
            elif factor_age_minutes is not None:
                summary_lines.append(f"Freshness: latest factors {factor_age_minutes} min ago.")
            else:
                summary_lines.append(f"Freshness: latest predictions {prediction_age_minutes} min ago.")

            return {
                "summary_lines": summary_lines,
                "edge_opportunities": edge_opportunities,
                "top_rules": top_rule_rows,
                "new_discoveries": {
                    "new_rules_24h": int(new_rules_24h),
                    "features_today": int(features_today),
                    "factors_last_hour": factors_last_hour,
                    "latest_factor_at": str(latest_factor_at) if latest_factor_at else None,
                    "latest_prediction_at": (
                        str(latest_prediction_at) if latest_prediction_at else None
                    ),
                    "factor_age_minutes": factor_age_minutes,
                    "prediction_age_minutes": prediction_age_minutes,
                },
                "data_quality": {
                    "resolved_total": int(resolved_total),
                    "resolved_authoritative": int(resolved_authoritative),
                    "resolved_with_source": int(resolved_with_source),
                    "resolved_inferred": int(resolved_inferred),
                    "authoritative_label_rate": (
                        round(authoritative_label_rate, 4)
                        if authoritative_label_rate is not None
                        else None
                    ),
                    "labeled_source_coverage": (
                        round(labeled_source_coverage, 4)
                        if labeled_source_coverage is not None
                        else None
                    ),
                    "inferred_label_rate": (
                        round(inferred_label_rate, 4)
                        if inferred_label_rate is not None
                        else None
                    ),
                    "duplicate_prediction_rate_24h": (
                        round(duplicate_prediction_rate, 4)
                        if duplicate_prediction_rate is not None
                        else None
                    ),
                    "resolution_source_breakdown": resolution_source_breakdown,
                },
            }
    except Exception as e:
        log.exception("mission_control failed")
        return {
            "summary_lines": [
                "Mission control data is temporarily unavailable.",
                f"Error: {e}",
            ],
            "edge_opportunities": [],
            "top_rules": [],
            "new_discoveries": {
                "new_rules_24h": 0,
                "features_today": 0,
                "factors_last_hour": {},
                "latest_factor_at": None,
                "latest_prediction_at": None,
                "factor_age_minutes": None,
                "prediction_age_minutes": None,
            },
            "data_quality": {
                "resolved_total": 0,
                "resolved_authoritative": 0,
                "resolved_with_source": 0,
                "resolved_inferred": 0,
                "authoritative_label_rate": None,
                "labeled_source_coverage": None,
                "inferred_label_rate": None,
                "duplicate_prediction_rate_24h": None,
                "resolution_source_breakdown": {},
            },
        }


@app.get("/api/human-dashboard")
async def human_dashboard():
    """Single endpoint powering the plain-English dashboard.

    Returns everything a non-technical user needs to understand:
    - What should we trade right now? (action + reasoning)
    - How accurate are we? (hit rate with time windows)
    - What are the best rules? (plain English)
    - Paper trade status
    - Data freshness
    - System health
    """
    now = _utcnow_naive()
    hour_cutoff = now - timedelta(hours=1)
    day_cutoff = now - timedelta(hours=24)

    try:
        async with SessionLocal() as session:
            # --- ACCURACY METRICS ---
            # Count rules by quality tier
            rule_count_high = (await session.execute(
                select(func.count(TradingRule.id)).where(
                    TradingRule.active == True,  # noqa: E712
                    TradingRule.sample_size >= 500,
                    TradingRule.win_rate >= 0.70,
                )
            )).scalar() or 0
            rule_count_mid = (await session.execute(
                select(func.count(TradingRule.id)).where(
                    TradingRule.active == True,  # noqa: E712
                    TradingRule.sample_size >= 500,
                    TradingRule.win_rate >= 0.55,
                    TradingRule.win_rate < 0.70,
                )
            )).scalar() or 0

            # How many current opportunities have real edge?
            # (we'll count from the opportunities list built below)

            # Paper trade win rate — only count real trades (not "up or down" noise, not dead markets)
            _pt_filter = (
                select(PaperTrade.id)
                .join(Market, Market.id == PaperTrade.market_id)
                .where(*real_trade_predicates(resolved=True))
            )
            pt_scored = (await session.execute(
                select(func.count()).select_from(_pt_filter.subquery())
            )).scalar() or 0
            _pt_correct_q = (
                select(PaperTrade.id)
                .join(Market, Market.id == PaperTrade.market_id)
                .where(
                    PaperTrade.pnl > 0,
                    *real_trade_predicates(resolved=True),
                )
            )
            pt_correct = (await session.execute(
                select(func.count()).select_from(_pt_correct_q.subquery())
            )).scalar() or 0
            pt_hit = round(pt_correct / pt_scored * 100, 1) if pt_scored else None

            # --- TOP RULES (best win rate, serious sample sizes only) ---
            top_rules_rows = (await session.execute(
                select(TradingRule)
                .where(TradingRule.active == True, TradingRule.sample_size >= 500)
                .order_by(desc(TradingRule.win_rate), desc(TradingRule.sample_size))
                .limit(20)
            )).scalars().all()

            top_rules = []
            for r in top_rules_rows:
                top_rules.append({
                    "id": r.id,
                    "plain_english": _rule_to_plain_english(r),
                    "side": r.predicted_side,
                    "win_rate_pct": round((r.win_rate or 0) * 100, 1),
                    "sample_size": r.sample_size or 0,
                    "rule_type": r.rule_type,
                    "name": r.name,
                    "tier": int(r.tier or 3),
                    "quality_label": r.quality_label or "exploratory",
                })

            # --- TOP OPPORTUNITIES (from open paper trades — already scanned all markets) ---
            # Filter: entry > $0.02 (skip dead markets)
            opp_rows = (await session.execute(
                select(PaperTrade, Market.question, Market.end_date, Market.volume, TradingRule)
                .join(Market, Market.id == PaperTrade.market_id)
                .outerjoin(TradingRule, TradingRule.id == PaperTrade.rule_id)
                .where(*real_trade_predicates(
                    now=now, resolved=False, require_future_end=True))
                .order_by(desc(PaperTrade.edge))
                .limit(30)
            )).all()

            opportunities = []
            for trade, question, end_date, volume, rule in opp_rows:
                if "up or down" in (question or "").lower():
                    continue
                side = trade.side
                entry = float(trade.entry_price or 0)
                edge_pct = round(float(trade.edge or 0) * 100, 1)
                wr_pct = round((rule.win_rate or 0) * 100, 1) if rule else 0
                action_text = f"Buy {side} at ${entry:.2f}"
                reason = _rule_to_plain_english(rule) if rule else "Unknown pattern"
                rule_tier = int(rule.tier or 3) if rule else 3
                quality_label = (rule.quality_label or "exploratory") if rule else "unknown"
                opportunities.append({
                    "question": (question or "?")[:120],
                    "action": action_text,
                    "side": side,
                    "edge_pct": edge_pct,
                    "entry_price": round(entry, 3),
                    "confidence": _confidence_label(wr_pct),
                    "rules_agreeing": 1,
                    "reasons": [f"[Tier {rule_tier}] {reason}"],
                    "rule_tier": rule_tier,
                    "quality_label": quality_label,
                    "volume": float(volume or 0),
                    "resolves_at": _iso_utc(end_date) if end_date else "Unknown",
                })

            # --- ENDING SOONEST (markets we have trades on, resolving soon) ---
            ending_rows = (await session.execute(
                select(PaperTrade, Market.question, Market.end_date, Market.volume)
                .join(Market, Market.id == PaperTrade.market_id)
                .where(*real_trade_predicates(
                    now=now, resolved=False, require_future_end=True))
                .order_by(Market.end_date.asc())
                .limit(100)
            )).all()
            # Deduplicate by market_id, skip noise
            seen_ending: dict[str, dict] = {}
            for trade, question, end_date, volume in ending_rows:
                mid = trade.market_id
                q = (question or "?")
                if mid in seen_ending or "up or down" in q.lower():
                    continue
                seen_ending[mid] = {
                    "question": q[:120],
                    "side": trade.side,
                    "entry_price": round(float(trade.entry_price or 0), 3),
                    "edge_pct": round(float(trade.edge or 0) * 100, 1),
                    "resolves_at": _iso_utc(end_date) if end_date else "Unknown",
                    "volume": float(volume or 0),
                }
            ending_soonest = list(seen_ending.values())[:20]

            # Pre-compute paper trade counts for action-card fallback and summary sections.
            open_real_preds = real_trade_predicates(
                now=now, resolved=False, require_future_end=True)
            resolved_real_preds = real_trade_predicates(resolved=True)
            open_count = (await session.execute(
                select(func.count(PaperTrade.id))
                .join(Market, Market.id == PaperTrade.market_id)
                .where(*open_real_preds)
            )).scalar() or 0
            closed_count = (await session.execute(
                select(func.count(PaperTrade.id))
                .join(Market, Market.id == PaperTrade.market_id)
                .where(*resolved_real_preds)
            )).scalar() or 0
            total_pnl = (await session.execute(
                select(func.sum(PaperTrade.pnl))
                .join(Market, Market.id == PaperTrade.market_id)
                .where(*resolved_real_preds)
            )).scalar() or 0.0
            wins = (await session.execute(
                select(func.count(PaperTrade.id))
                .join(Market, Market.id == PaperTrade.market_id)
                .where(PaperTrade.won == True, *resolved_real_preds)
            )).scalar() or 0
            pt_win_rate = round(wins / closed_count * 100, 1) if closed_count else None

            # --- TOP ACTION (best single opportunity) ---
            if opportunities:
                top = opportunities[0]
                action = {
                    "text": f"Buy {top['side']} on \"{top['question'][:80]}\"",
                    "side": top["side"],
                    "edge_pct": top["edge_pct"],
                    "confidence": top["confidence"],
                    "market_question": top["question"],
                    "entry_price": top["entry_price"],
                    "reasoning": top["reasons"],
                }
            elif open_count > 0:
                action = {
                    "text": f"{open_count:,} trades open — waiting for markets to resolve.",
                    "side": None,
                    "edge_pct": 0,
                    "confidence": "None",
                    "market_question": None,
                    "entry_price": None,
                    "reasoning": [
                        f"The scanner placed {open_count:,} paper trades. "
                        "Edges were found when trades were opened. "
                        "New opportunities appear when prices move."
                    ],
                }
            else:
                action = {
                    "text": "No strong edge detected right now. Waiting for a good setup.",
                    "side": None,
                    "edge_pct": 0,
                    "confidence": "None",
                    "market_question": None,
                    "entry_price": None,
                    "reasoning": [
                        "No rules currently match active markets with enough edge to trade."
                    ],
                }

            if open_count == 0 and closed_count == 0:
                pt_status = "not_started"
                pt_explanation = (
                    "No paper trades have been placed yet. "
                    "The paper trading scheduler needs to be running on the compute server."
                )
            elif open_count > 0 and closed_count == 0:
                pt_status = "active_no_results"
                pt_explanation = (
                    f"{open_count:,} paper trades are open but none have resolved yet. "
                    "Trades resolve when markets settle on Polymarket."
                )
            else:
                pt_status = "active"
                pt_explanation = (
                    f"{open_count:,} open, {closed_count} closed. "
                    f"Win rate: {pt_win_rate}%. Total PnL: ${total_pnl:.2f}."
                )

            # Recent closed trades for the ledger (exclude junk)
            recent_trades_rows = (await session.execute(
                select(PaperTrade, Market.question)
                .join(Market, Market.id == PaperTrade.market_id)
                .where(*resolved_real_preds)
                .order_by(desc(PaperTrade.resolved_at))
                .limit(20)
            )).all()
            recent_trades = []
            for trade, question in recent_trades_rows:
                recent_trades.append({
                    "question": (question or "?")[:80],
                    "side": trade.side,
                    "entry_price": round(float(trade.entry_price or 0), 3),
                    "won": trade.won,
                    "pnl": round(float(trade.pnl or 0), 4),
                    "resolved_at": _iso_utc(trade.resolved_at),
                })

            # Open trades — with rule explanation, filter junk, dedup by market
            page_size = 30
            open_trades_rows = (await session.execute(
                select(PaperTrade, Market.question, Market.end_date, TradingRule)
                .join(Market, Market.id == PaperTrade.market_id)
                .outerjoin(TradingRule, TradingRule.id == PaperTrade.rule_id)
                .where(*open_real_preds)
                .order_by(desc(PaperTrade.edge))
            )).all()
            # Group by market_id — keep best edge, attach rule reason
            seen_markets: dict[str, dict] = {}
            for trade, question, end_date, rule in open_trades_rows:
                mid = trade.market_id
                q = (question or "?")
                # Skip high-frequency noise
                if "up or down" in q.lower():
                    continue
                if mid not in seen_markets:
                    reason = _rule_to_plain_english(rule) if rule else "Unknown pattern"
                    rule_tier = int(rule.tier or 3) if rule else 3
                    quality_label = (rule.quality_label or "exploratory") if rule else "unknown"
                    seen_markets[mid] = {
                        "question": q[:120],
                        "side": trade.side,
                        "entry_price": round(float(trade.entry_price or 0), 3),
                        "edge_pct": round(float(trade.edge or 0) * 100, 1),
                        "created_at": _iso_utc(trade.created_at),
                        "resolves_at": _iso_utc(end_date) if end_date else "Unknown",
                        "rule_reason": reason,
                        "rule_tier": rule_tier,
                        "quality_label": quality_label,
                        "rules_matched": 1,
                    }
                else:
                    seen_markets[mid]["rules_matched"] += 1
            all_open_trades = sorted(
                seen_markets.values(), key=lambda x: x["edge_pct"], reverse=True
            )
            open_trades_list = all_open_trades[:page_size]
            total_unique_open = len(all_open_trades)

            # --- PAPER TRADE STATS ---
            # Count trades ending today / this week
            from sqlalchemy import cast
            ending_today = (await session.execute(
                select(func.count(func.distinct(PaperTrade.market_id)))
                .join(Market, Market.id == PaperTrade.market_id)
                .where(
                    *open_real_preds,
                    Market.end_date < now.replace(hour=23, minute=59, second=59),
                )
            )).scalar() or 0
            ending_week = (await session.execute(
                select(func.count(func.distinct(PaperTrade.market_id)))
                .join(Market, Market.id == PaperTrade.market_id)
                .where(
                    *open_real_preds,
                    Market.end_date < now.replace(hour=0, minute=0, second=0) + timedelta(days=7),
                )
            )).scalar() or 0

            # Average edge
            avg_edge_raw = (await session.execute(
                select(func.avg(PaperTrade.edge))
                .join(Market, Market.id == PaperTrade.market_id)
                .where(*open_real_preds)
            )).scalar()
            avg_edge_pct = round(float(avg_edge_raw or 0) * 100, 1)

            # --- DATA FRESHNESS ---
            latest_factor_at = (await session.execute(
                select(func.max(Factor.timestamp))
            )).scalar()
            factor_age = _minutes_since(latest_factor_at, now)
            factors_total = (await session.execute(
                select(func.count(Factor.id))
            )).scalar() or 0
            factors_grok = (await session.execute(
                select(func.count(Factor.id)).where(Factor.source == "grok")
            )).scalar() or 0
            factors_perplexity = (await session.execute(
                select(func.count(Factor.id)).where(Factor.source == "perplexity")
            )).scalar() or 0
            features_today = (await session.execute(
                select(func.count(DailyFeature.id)).where(
                    DailyFeature.date == text("CURRENT_DATE"))
            )).scalar() or 0
            feature_sources = (await session.execute(
                select(func.count(func.distinct(DailyFeature.source))).where(
                    DailyFeature.date == text("CURRENT_DATE"))
            )).scalar() or 0

            freshness_status = _freshness_label(factor_age)
            freshness_explanation = (
                f"{features_today} features collected today from {feature_sources} data sources. "
                f"{factors_total:,} total research factors "
                f"({factors_grok:,} from Grok, {factors_perplexity:,} from Perplexity). "
            )
            if factor_age is not None:
                freshness_explanation += f"Last research update: {factor_age} min ago."
            else:
                freshness_explanation += "No recent research updates."

            # --- SYSTEM HEALTH ---
            heartbeats = (await session.execute(
                select(ServiceHeartbeat)
            )).scalars().all()

            expected_host = (os.environ.get("POLYEDGE_SCHEDULER_HOST", "") or "").strip()
            compute_hosts = set()
            heavy_services = {
                "feature_collection", "paper_trading", "correlation_refresh",
                "api_research", "supergod_dispatch", "supergod_ingest", "predictions",
            }
            services_summary = []
            for hb in heartbeats:
                age = _minutes_since(hb.updated_at, now) if hb.updated_at else None
                if hb.service in heavy_services and hb.host:
                    compute_hosts.add(hb.host)
                status_text = hb.status or "unknown"
                if age is not None and age > 30:
                    status_text = f"stale ({age}m ago)"
                services_summary.append({
                    "name": hb.service,
                    "host": hb.host or "?",
                    "status": status_text,
                    "age_minutes": age,
                })

            source_counts = {}
            for hb in heartbeats:
                pass  # heartbeats don't have source counts
            # Get factor source activity from last hour
            source_rows = (await session.execute(
                select(Factor.source, func.count(Factor.id))
                .where(Factor.timestamp >= hour_cutoff)
                .group_by(Factor.source)
            )).all()
            for src, cnt in source_rows:
                source_counts[str(src)] = int(cnt)

            score_hb = next((hb for hb in heartbeats if hb.service == "score_paper_trades"), None)
            last_scored_at = _iso_utc(score_hb.last_success_at if score_hb else None)

            compute_ok = None
            if expected_host and compute_hosts:
                compute_ok = all(
                    h.lower() == expected_host.lower() for h in compute_hosts
                )
            actual_host = sorted(compute_hosts)[0] if compute_hosts else "not detected"

            # Cluster report
            cluster_path = pathlib.Path("incident_artifacts/cluster_baseline.latest.json")
            workers_active = 0
            workers_total = 0
            if cluster_path.exists():
                try:
                    cluster = json.loads(cluster_path.read_text())
                    workers_active = cluster.get("report", {}).get(
                        "snapshot_active_workers_total", 0) or 0
                    workers_total = cluster.get("report", {}).get(
                        "snapshot_workers_total", 0) or 0
                except Exception:
                    pass

            # --- COUNTS for context ---
            total_markets = (await session.execute(
                select(func.count(Market.id))
            )).scalar() or 0
            active_markets = (await session.execute(
                select(func.count(Market.id)).where(Market.active == True)
            )).scalar() or 0
            total_rules = (await session.execute(
                select(func.count(TradingRule.id)).where(TradingRule.active == True)
            )).scalar() or 0
            ngram_count = (await session.execute(
                select(func.count(NgramStat.id))
            )).scalar() or 0

            return {
                "generated_at": _iso_utc(now),
                "action": action,
                "hit_rate": {
                    "primary_pct": pt_hit,
                    "primary_label": "Paper Trade Results",
                    "rules_high_confidence": rule_count_high,
                    "rules_medium_confidence": rule_count_mid,
                    "opportunities_now": len(opportunities),
                    "paper_trade_pct": pt_hit,
                    "paper_trade_scored": pt_scored,
                    "paper_trade_correct": pt_correct,
                    "explanation": (
                        f"Paper trade results: {pt_hit}% win rate ({pt_correct} wins out of {pt_scored} trades). "
                        if pt_scored > 0 and pt_hit is not None else
                        f"{open_count:,} paper trades open, waiting for markets to resolve. "
                        if open_count > 0 else
                        "Paper trader hasn't started yet. "
                    ) + (
                        f"Each trade is backed by a rule proven on 500+ resolved markets."
                    ),
                },
                "opportunities": opportunities,
                "ending_soonest": ending_soonest,
                "top_rules": top_rules,
                "paper_trades": {
                    "status": pt_status,
                    "explanation": pt_explanation,
                    "last_scored_at": last_scored_at,
                    "open_count": open_count,
                    "closed_count": closed_count,
                    "total_pnl": round(float(total_pnl), 2),
                    "win_rate_pct": pt_win_rate,
                    "open_trades": open_trades_list,
                    "total_unique_open": total_unique_open,
                    "recent_closed": recent_trades,
                    "ending_today": ending_today,
                    "ending_this_week": ending_week,
                    "avg_edge_pct": avg_edge_pct,
                },
                "data_freshness": {
                    "status": freshness_status,
                    "factors_age_minutes": factor_age,
                    "factors_total": factors_total,
                    "factors_grok": factors_grok,
                    "factors_perplexity": factors_perplexity,
                    "features_today": features_today,
                    "feature_sources": feature_sources,
                    "explanation": freshness_explanation,
                },
                "system_health": {
                    "compute_host_ok": compute_ok,
                    "compute_host_expected": expected_host or "not configured",
                    "compute_host_actual": actual_host,
                    "grok_active": source_counts.get("grok", 0) > 0,
                    "perplexity_active": source_counts.get("perplexity", 0) > 0,
                    "workers_active": workers_active,
                    "workers_total": workers_total,
                    "services": services_summary,
                },
                "counts": {
                    "total_markets": total_markets,
                    "active_markets": active_markets,
                    "total_rules": total_rules,
                    "ngram_patterns": ngram_count,
                    "factors_total": factors_total,
                    "features_today": features_today,
                },
            }
    except Exception as e:
        log.exception("human_dashboard failed")
        return {"error": str(e), "generated_at": _iso_utc(now)}


@app.get("/api/markets")
async def list_markets(active: bool = True, limit: int = 50):
    """Backward-compatible markets feed for older dashboards/clients."""
    try:
        async with SessionLocal() as session:
            q = select(Market).order_by(desc(Market.volume)).limit(limit)
            if active:
                q = q.where(Market.active == True)  # noqa: E712
            markets = (await session.execute(q)).scalars().all()
            return [_market_dict(m) for m in markets]
    except Exception:
        log.exception("list_markets failed")
        return []


@app.get("/api/markets/{market_id}")
async def get_market(market_id: str):
    """Backward-compatible market detail feed."""
    try:
        async with SessionLocal() as session:
            market = await session.get(Market, market_id)
            if not market:
                return {"error": "not found"}

            factors = (
                await session.execute(
                    select(Factor)
                    .where(Factor.market_id == market_id)
                    .order_by(desc(Factor.timestamp))
                    .limit(100)
                )
            ).scalars().all()
            predictions = (
                await session.execute(
                    select(Prediction)
                    .where(Prediction.market_id == market_id)
                    .order_by(desc(Prediction.created_at))
                    .limit(100)
                )
            ).scalars().all()

            return {
                **_market_dict(market),
                "factors": [_factor_dict(f) for f in factors],
                "predictions": [_prediction_dict(p) for p in predictions],
            }
    except Exception:
        log.exception("get_market failed")
        return {"error": "unavailable"}


@app.get("/api/factors/recent")
async def factors_recent(limit: int = 200, market_id: str | None = None):
    """Backward-compatible factor feed."""
    try:
        async with SessionLocal() as session:
            q = select(Factor).order_by(desc(Factor.timestamp)).limit(limit)
            if market_id:
                q = q.where(Factor.market_id == market_id)
            rows = (await session.execute(q)).scalars().all()
            return [_factor_dict(f) for f in rows]
    except Exception:
        log.exception("factors_recent failed")
        return []


@app.get("/api/predictions/recent")
async def predictions_recent(
    limit: int = 200,
    market_id: str | None = None,
    scored_only: bool = False,
):
    """Backward-compatible predictions feed."""
    try:
        async with SessionLocal() as session:
            q = select(Prediction).order_by(desc(Prediction.created_at)).limit(limit)
            if market_id:
                q = q.where(Prediction.market_id == market_id)
            if scored_only:
                q = q.where(Prediction.correct != None)  # noqa: E711
            rows = (await session.execute(q)).scalars().all()
            return [_prediction_dict(p) for p in rows]
    except Exception:
        log.exception("predictions_recent failed")
        return []


@app.get("/api/analysis/scored")
async def analysis_scored(limit: int = 200):
    """Scored prediction rows with market question for legacy analysis pages."""
    try:
        async with SessionLocal() as session:
            rows = (
                await session.execute(
                    select(Prediction, Market.question)
                    .join(Market, Market.id == Prediction.market_id)
                    .where(Prediction.correct != None)  # noqa: E711
                    .order_by(desc(Prediction.created_at))
                    .limit(limit)
                )
            ).all()
            return [
                {
                    **_prediction_dict(pred),
                    "question": question,
                }
                for pred, question in rows
            ]
    except Exception:
        log.exception("analysis_scored failed")
        return []


@app.get("/api/factors/weights")
async def factor_weights():
    """Backward-compatible factor weight endpoint."""
    try:
        async with SessionLocal() as session:
            rows = (
                await session.execute(
                    select(FactorWeight).order_by(desc(FactorWeight.hit_rate))
                )
            ).scalars().all()
            return [
                {
                    "category": r.category,
                    "total_predictions": r.total_predictions,
                    "correct_predictions": r.correct_predictions,
                    "hit_rate": r.hit_rate,
                    "weight": r.weight,
                    "updated_at": str(r.updated_at) if r.updated_at else None,
                }
                for r in rows
            ]
    except Exception:
        log.exception("factor_weights failed")
        return []


@app.get("/api/pnl")
async def paper_trading_pnl():
    """Paper trading PnL summary + daily series."""
    try:
        async with SessionLocal() as session:
            trades = (
                await session.execute(
                    select(PaperTrade)
                    .where(PaperTrade.resolved == True)
                    .order_by(PaperTrade.resolved_at)
                )
            ).scalars().all()

            total_pnl = sum(t.pnl or 0 for t in trades)
            wins = sum(1 for t in trades if t.won)

            # Daily PnL series (last 30 days)
            daily_pnl: dict[str, float] = {}
            for t in trades:
                if t.resolved_at:
                    day = str(t.resolved_at.date())
                    daily_pnl[day] = daily_pnl.get(day, 0) + (t.pnl or 0)

            daily_series = [
                {"date": d, "pnl": round(v, 4)}
                for d, v in sorted(daily_pnl.items())
            ]

            return {
                "total_pnl": round(total_pnl, 4),
                "total_trades": len(trades),
                "wins": wins,
                "losses": len(trades) - wins,
                "win_rate": round(wins / len(trades), 4) if trades else 0,
                "daily_series": daily_series[-30:],
            }
    except Exception as e:
        log.exception("paper_trading_pnl failed")
        return {
            "total_pnl": 0, "total_trades": 0, "wins": 0, "losses": 0,
            "win_rate": 0, "daily_series": [], "error": str(e),
        }


@app.get("/api/pnl/real-audit")
async def paper_trading_real_audit():
    """Audit metrics for the exact real-trade cohort shown on the dashboard."""
    try:
        async with SessionLocal() as session:
            resolved_real_preds = real_trade_predicates(resolved=True)
            totals = (
                await session.execute(
                    select(
                        func.count(PaperTrade.id),
                        func.sum(PaperTrade.entry_price),
                        func.sum(PaperTrade.pnl),
                    )
                    .join(Market, Market.id == PaperTrade.market_id)
                    .where(*resolved_real_preds)
                )
            ).one()
            closed_count = int(totals[0] or 0)
            total_entry_cost = float(totals[1] or 0.0)
            total_pnl = float(totals[2] or 0.0)

            wins = (
                await session.execute(
                    select(func.count(PaperTrade.id))
                    .join(Market, Market.id == PaperTrade.market_id)
                    .where(PaperTrade.won == True, *resolved_real_preds)  # noqa: E712
                )
            ).scalar() or 0
            wins = int(wins)
            losses = max(0, closed_count - wins)

            avg_entry_price = total_entry_cost / closed_count if closed_count else 0.0
            avg_pnl_per_trade = total_pnl / closed_count if closed_count else 0.0
            observed_win_rate = wins / closed_count if closed_count else 0.0
            breakeven_win_rate = avg_entry_price
            roi_on_deployed_capital = (
                total_pnl / total_entry_cost if total_entry_cost else 0.0
            )

            return {
                "generated_at": _iso_utc(_utcnow_naive()),
                "cohort": "real_trades_only",
                "closed_count": closed_count,
                "wins": wins,
                "losses": losses,
                "total_entry_cost": round(total_entry_cost, 4),
                "total_pnl": round(total_pnl, 4),
                "avg_entry_price": round(avg_entry_price, 6),
                "avg_pnl_per_trade": round(avg_pnl_per_trade, 6),
                "observed_win_rate": round(observed_win_rate, 6),
                "breakeven_win_rate": round(breakeven_win_rate, 6),
                "roi_on_deployed_capital": round(roi_on_deployed_capital, 6),
                "formulas": {
                    "pnl_if_win": "1 - entry_price",
                    "pnl_if_loss": "-entry_price",
                    "breakeven_win_rate": "avg_entry_price",
                    "roi_on_deployed_capital": "total_pnl / total_entry_cost",
                },
            }
    except Exception as e:
        log.exception("paper_trading_real_audit failed")
        return {
            "generated_at": _iso_utc(_utcnow_naive()),
            "cohort": "real_trades_only",
            "closed_count": 0,
            "wins": 0,
            "losses": 0,
            "total_entry_cost": 0.0,
            "total_pnl": 0.0,
            "avg_entry_price": 0.0,
            "avg_pnl_per_trade": 0.0,
            "observed_win_rate": 0.0,
            "breakeven_win_rate": 0.0,
            "roi_on_deployed_capital": 0.0,
            "formulas": {},
            "error": str(e),
        }


@app.get("/api/rules")
async def list_rules(active_only: bool = True, limit: int = 100):
    """All active rules with stats."""
    try:
        async with SessionLocal() as session:
            q = (
                select(TradingRule)
                .order_by(desc(TradingRule.win_rate))
                .limit(limit)
            )
            if active_only:
                q = q.where(TradingRule.active == True)
            rules = (await session.execute(q)).scalars().all()

            return [
                {
                    "id": r.id,
                    "name": r.name,
                    "rule_type": r.rule_type,
                    "predicted_side": r.predicted_side,
                    "win_rate": r.win_rate,
                    "sample_size": r.sample_size,
                    "breakeven_price": r.breakeven_price,
                    "avg_roi": r.avg_roi,
                    "active": r.active,
                    "conditions": r.conditions_json,
                }
                for r in rules
            ]
    except Exception as e:
        log.exception("list_rules failed")
        return []


@app.get("/api/positions")
async def open_positions(limit: int = 50):
    """Open paper trades with current prices."""
    try:
        async with SessionLocal() as session:
            trades = (
                await session.execute(
                    select(PaperTrade)
                    .where(PaperTrade.resolved == False)
                    .order_by(desc(PaperTrade.edge))
                    .limit(limit)
                )
            ).scalars().all()

            result = []
            for t in trades:
                market = await session.get(Market, t.market_id)
                result.append(
                    {
                        "market_id": t.market_id,
                        "question": market.question[:80] if market else "?",
                        "side": t.side,
                        "entry_price": t.entry_price,
                        "edge": t.edge,
                        "rule_id": t.rule_id,
                        "created_at": str(t.created_at),
                        "current_yes_price": market.yes_price if market else None,
                        "current_no_price": (
                            round(1.0 - market.yes_price, 4)
                            if market and market.yes_price is not None
                            else None
                        ),
                    }
                )
            return result
    except Exception as e:
        log.exception("open_positions failed")
        return []


@app.get("/api/features/status")
async def feature_status():
    """Connector health + feature counts by source."""
    try:
        async with SessionLocal() as session:
            rows = (
                await session.execute(
                    select(
                        DailyFeature.source,
                        func.count(DailyFeature.id),
                        func.max(DailyFeature.date),
                    ).group_by(DailyFeature.source)
                )
            ).all()

            return [
                {
                    "source": r[0],
                    "total_features": r[1],
                    "last_date": str(r[2]) if r[2] else None,
                }
                for r in rows
            ]
    except Exception as e:
        log.exception("feature_status failed")
        return []


@app.get("/api/backtest")
async def backtest_results():
    """Latest backtest results summary."""
    for results_path in _backtest_results_candidates():
        if not results_path.exists():
            continue
        try:
            return json.loads(results_path.read_text(encoding="utf-8"))
        except Exception:
            return {
                "status": "backtest file unreadable",
                "path": str(results_path),
            }

    # Fallback: derive a scored-analysis summary from historical predictions.
    try:
        async with SessionLocal() as session:
            scored_total = (
                await session.execute(
                    select(func.count(Prediction.id)).where(Prediction.correct != None)  # noqa: E711
                )
            ).scalar() or 0
            correct_total = (
                await session.execute(
                    select(func.count(Prediction.id)).where(Prediction.correct == True)
                )
            ).scalar() or 0
            hit_rate = (correct_total / scored_total) if scored_total else None

            side_rows = (
                await session.execute(
                    select(
                        Prediction.predicted_outcome,
                        func.count(Prediction.id),
                        func.sum(case((Prediction.correct == True, 1), else_=0)),
                    )
                    .where(Prediction.correct != None)  # noqa: E711
                    .group_by(Prediction.predicted_outcome)
                )
            ).all()
            by_side = []
            for side, total, correct in side_rows:
                total_i = int(total or 0)
                correct_i = int(correct or 0)
                by_side.append(
                    {
                        "predicted_outcome": side,
                        "total": total_i,
                        "correct": correct_i,
                        "hit_rate": round(correct_i / total_i, 4) if total_i else None,
                    }
                )

            recent_cutoff = _utcnow_naive() - timedelta(days=30)
            daily_rows = (
                await session.execute(
                    select(
                        func.date(Prediction.created_at),
                        func.count(Prediction.id),
                        func.sum(case((Prediction.correct == True, 1), else_=0)),
                    )
                    .where(
                        Prediction.correct != None,  # noqa: E711
                        Prediction.created_at >= recent_cutoff,
                    )
                    .group_by(func.date(Prediction.created_at))
                    .order_by(func.date(Prediction.created_at))
                )
            ).all()
            daily_series = []
            for day, total, correct in daily_rows:
                total_i = int(total or 0)
                correct_i = int(correct or 0)
                daily_series.append(
                    {
                        "date": str(day),
                        "total": total_i,
                        "correct": correct_i,
                        "hit_rate": round(correct_i / total_i, 4) if total_i else None,
                    }
                )

            return {
                "status": "generated_from_scored_predictions",
                "summary": {
                    "total_scored_predictions": int(scored_total),
                    "correct_predictions": int(correct_total),
                    "hit_rate": round(hit_rate, 4) if hit_rate is not None else None,
                },
                "by_predicted_side": by_side,
                "daily_series_30d": daily_series,
            }
    except Exception as e:
        log.exception("backtest_results fallback failed")
        return {
            "status": "no backtest results available",
            "error": str(e),
        }


@app.get("/api/activity")
async def recent_activity(limit: int = 50):
    """Recent system events log."""
    try:
        async with SessionLocal() as session:
            trades = (
                await session.execute(
                    select(PaperTrade)
                    .where(PaperTrade.resolved == True)
                    .order_by(desc(PaperTrade.resolved_at))
                    .limit(limit)
                )
            ).scalars().all()

            events = []
            for t in trades:
                market = await session.get(Market, t.market_id)
                q = market.question[:60] if market else "?"
                pnl_val = t.pnl or 0
                if t.won:
                    events.append(
                        {
                            "type": "trade_won",
                            "message": f"WON {t.side}@${t.entry_price:.2f} on '{q}' -> +${pnl_val:.2f}",
                            "timestamp": str(t.resolved_at),
                        }
                    )
                else:
                    events.append(
                        {
                            "type": "trade_lost",
                            "message": f"LOST {t.side}@${t.entry_price:.2f} on '{q}' -> ${pnl_val:.2f}",
                            "timestamp": str(t.resolved_at),
                        }
                    )
            return events
    except Exception as e:
        log.exception("recent_activity failed")
        return []


@app.get("/api/ops/runtime")
async def ops_runtime_status():
    """Operational status for scheduler host placement and worker-cluster checks."""
    now = _utcnow_naive()
    expected_scheduler_host = (os.environ.get("POLYEDGE_SCHEDULER_HOST", "") or "").strip()
    local_host = socket.gethostname()

    cluster_report_path = pathlib.Path("incident_artifacts/cluster_baseline.latest.json")
    cluster_report = None
    if cluster_report_path.exists():
        try:
            cluster_report = json.loads(cluster_report_path.read_text())
        except json.JSONDecodeError:
            cluster_report = {"ok": False, "error": "invalid_cluster_report_json"}

    try:
        async with SessionLocal() as session:
            heartbeat_rows = (
                await session.execute(select(ServiceHeartbeat).order_by(ServiceHeartbeat.service))
            ).scalars().all()

            services = []
            heavy_compute_services = {
                "feature_collection",
                "paper_trading",
                "correlation_refresh",
                "api_research",
                "supergod_dispatch",
                "supergod_ingest",
                "predictions",
            }
            compute_hosts: set[str] = set()

            for row in heartbeat_rows:
                age_seconds = None
                if row.updated_at:
                    age_seconds = max(0, int((now - row.updated_at).total_seconds()))
                if row.service in heavy_compute_services and row.host:
                    compute_hosts.add(row.host)
                services.append(
                    {
                        "service": row.service,
                        "host": row.host,
                        "status": row.status,
                        "age_seconds": age_seconds,
                        "last_success_at": str(row.last_success_at) if row.last_success_at else None,
                    }
                )

            hour_cutoff = now - timedelta(hours=1)
            source_rows = (
                await session.execute(
                    select(Factor.source, func.count(Factor.id))
                    .where(Factor.timestamp >= hour_cutoff)
                    .group_by(Factor.source)
                )
            ).all()
            source_counts = {str(source): int(count) for source, count in source_rows}

            heavy_compute_ok = None
            if expected_scheduler_host and compute_hosts:
                heavy_compute_ok = all(
                    host.lower() == expected_scheduler_host.lower()
                    for host in compute_hosts
                )

            return {
                "expected_scheduler_host": expected_scheduler_host,
                "api_process_host": local_host,
                "scheduler_service_hosts": sorted(compute_hosts),
                "heavy_compute_on_expected_host": heavy_compute_ok,
                "services": services,
                "analysis_last_hour_by_source": source_counts,
                "grok_active_last_hour": source_counts.get("grok", 0) > 0,
                "perplexity_active_last_hour": source_counts.get("perplexity", 0) > 0,
                "codex_distilled_active_last_hour": source_counts.get("codex_distilled", 0) > 0,
                "supergod_cluster_report": cluster_report,
                "supergod_orchestrator_url": db_settings.supergod_orchestrator_url,
            }
    except Exception as e:
        log.exception("ops_runtime_status failed")
        return {
            "expected_scheduler_host": expected_scheduler_host,
            "api_process_host": local_host,
            "scheduler_service_hosts": [],
            "heavy_compute_on_expected_host": None,
            "services": [],
            "analysis_last_hour_by_source": {},
            "grok_active_last_hour": False,
            "perplexity_active_last_hour": False,
            "codex_distilled_active_last_hour": False,
            "supergod_cluster_report": cluster_report,
            "error": str(e),
        }


# Backward compat
@app.get("/api/stats")
async def stats():
    return await dashboard_summary()
