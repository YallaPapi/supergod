"""FastAPI dashboard for PolyEdge v3."""

from datetime import datetime, timedelta, timezone
import os
import socket

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from sqlalchemy import select, func, desc, text, case, cast, Numeric, delete
from polyedge.db import SessionLocal
from polyedge.analysis.scorer import prediction_metrics_cutoff
from polyedge.db import settings as db_settings
from polyedge.query_filters import noise_market_predicate, real_trade_predicates
from polyedge.models import (
    Market, TradingRule, PaperTrade, DailyFeature,
    NgramStat, Prediction, Factor, FactorWeight, PriceSnapshot, ServiceHeartbeat,
    StrategyProfile, StrategyProfileRule,
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


@app.middleware("http")
async def disable_api_cache(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/") or request.url.path == "/":
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


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


_DIRECT_TRADE_SOURCES = ("ngram", "factor_match", "grok_direct", "combined")
_INVERSE_TRADE_SOURCES = ("ngram_inverse", "factor_match_inv", "grok_inv", "combined_inverse")
_ALL_TRACKED_TRADE_SOURCES = _DIRECT_TRADE_SOURCES + _INVERSE_TRADE_SOURCES


def _compute_source_derived_metrics(
    *,
    closed: int,
    wins: int,
    pnl: float,
    open_count: int,
    avg_entry_open: float | None,
    avg_entry_closed: float | None,
) -> dict[str, float | None]:
    if closed <= 0:
        return {
            "win_rate_pct": None,
            "pnl_per_bet": None,
            "ev_per_bet": None,
            "expected_open_pnl": None,
        }
    win_rate = wins / closed
    pnl_per_bet = pnl / closed
    reference_entry = avg_entry_open if avg_entry_open is not None else avg_entry_closed
    ev_per_bet = (win_rate - reference_entry) if reference_entry is not None else None
    expected_open_pnl = (ev_per_bet * open_count) if ev_per_bet is not None else None
    return {
        "win_rate_pct": round(win_rate * 100, 1),
        "pnl_per_bet": pnl_per_bet,
        "ev_per_bet": ev_per_bet,
        "expected_open_pnl": expected_open_pnl,
    }


_KNOWN_SCHEDULER_SERVICES = {
    "poller+scorer",
    "predictions",
    "api_research",
    "supergod_dispatch",
    "supergod_ingest",
    "feature_collection",
    "paper_trading",
    "factor_match_trading",
    "grok_prediction_trading",
    "combined_paper_trading",
    "profile_paper_trading",
    "score_paper_trades",
    "resolution_check",
    "correlation_refresh",
    "research_rule_bridge",
    "backtest_refresh",
    "agreement_refresh",
}


def _select_relevant_heartbeats(heartbeats: list[ServiceHeartbeat], now: datetime) -> list[ServiceHeartbeat]:
    """Hide long-stale legacy heartbeat names so runtime cards stay meaningful."""
    selected: list[ServiceHeartbeat] = []
    for hb in heartbeats:
        age = _minutes_since(hb.updated_at, now) if hb.updated_at else None
        if hb.service in _KNOWN_SCHEDULER_SERVICES:
            selected.append(hb)
            continue
        if age is not None and age <= 120:
            selected.append(hb)
    return selected


_HUMAN_DASHBOARD_CACHE_TTL_SECONDS = 90
_human_dashboard_cache_payload: dict | None = None
_human_dashboard_cache_updated_at: datetime | None = None


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
                    select(func.count(func.distinct(TradingRule.name))).where(TradingRule.active == True)
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

            result = {
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
            return result
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

            result = {
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
    global _human_dashboard_cache_payload, _human_dashboard_cache_updated_at
    now = _utcnow_naive()
    if (
        _human_dashboard_cache_payload is not None
        and _human_dashboard_cache_updated_at is not None
        and (now - _human_dashboard_cache_updated_at).total_seconds() < _HUMAN_DASHBOARD_CACHE_TTL_SECONDS
    ):
        return _human_dashboard_cache_payload

    hour_cutoff = now - timedelta(hours=1)
    day_cutoff = now - timedelta(hours=24)

    try:
        async with SessionLocal() as session:
            # --- ACCURACY METRICS ---
            # Count rules by quality tier (deduplicated by name to avoid inflated counts)
            rule_count_high = (await session.execute(
                select(func.count(func.distinct(TradingRule.name))).where(
                    TradingRule.active == True,  # noqa: E712
                    TradingRule.sample_size >= 500,
                    TradingRule.win_rate >= 0.70,
                )
            )).scalar() or 0
            rule_count_mid = (await session.execute(
                select(func.count(func.distinct(TradingRule.name))).where(
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

            # --- TOP RULES (best win rate, serious sample sizes only, deduplicated) ---
            top_rules_rows_raw = (await session.execute(text("""
                SELECT DISTINCT ON (name)
                    id, name, rule_type, predicted_side, conditions_json,
                    win_rate, sample_size, tier, quality_label, avg_roi
                FROM trading_rules
                WHERE active = true AND sample_size >= 500
                ORDER BY name, win_rate DESC, sample_size DESC
            """))).all()
            # Sort by win_rate desc, take top 20
            top_rules_sorted = sorted(top_rules_rows_raw, key=lambda r: (r[5] or 0, r[6] or 0), reverse=True)[:20]
            # Build mock objects for _rule_to_plain_english
            class _RuleProxy:
                def __init__(self, row):
                    self.id = row[0]; self.name = row[1]; self.rule_type = row[2]
                    self.predicted_side = row[3]; self.conditions_json = row[4]
                    self.win_rate = row[5]; self.sample_size = row[6]
                    self.tier = row[7]; self.quality_label = row[8]; self.avg_roi = row[9]
            top_rules_rows = [_RuleProxy(r) for r in top_rules_sorted]

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
                select(PaperTrade, Market.question, Market.end_date, Market.volume, TradingRule)
                .join(Market, Market.id == PaperTrade.market_id)
                .outerjoin(TradingRule, TradingRule.id == PaperTrade.rule_id)
                .where(*real_trade_predicates(
                    now=now, resolved=False, require_future_end=True))
                .order_by(Market.end_date.asc())
                .limit(100)
            )).all()
            # Deduplicate by market_id, skip noise
            seen_ending: dict[str, dict] = {}
            for trade, question, end_date, volume, rule in ending_rows:
                mid = trade.market_id
                q = (question or "?")
                if mid in seen_ending:
                    continue
                reason = _rule_to_plain_english(rule) if rule else "Unknown pattern"
                seen_ending[mid] = {
                    "question": q[:120],
                    "side": trade.side,
                    "entry_price": round(float(trade.entry_price or 0), 3),
                    "edge_pct": round(float(trade.edge or 0) * 100, 1),
                    "resolves_at": _iso_utc(end_date) if end_date else "Unknown",
                    "volume": float(volume or 0),
                    "why": reason,
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

            # All-inclusive counts (including crypto up/down)
            # Trade-level counts (each strategy's bet counts separately)
            all_closed = (await session.execute(
                select(func.count(PaperTrade.id))
                .where(PaperTrade.resolved == True, PaperTrade.entry_price > 0.02)  # noqa: E712
            )).scalar() or 0
            all_open = (await session.execute(
                select(func.count(PaperTrade.id))
                .where(PaperTrade.resolved == False, PaperTrade.entry_price > 0.02)  # noqa: E712
            )).scalar() or 0
            all_wins = (await session.execute(
                select(func.count(PaperTrade.id))
                .where(PaperTrade.resolved == True, PaperTrade.entry_price > 0.02, PaperTrade.won == True)  # noqa: E712
            )).scalar() or 0
            all_pnl = (await session.execute(
                select(func.sum(PaperTrade.pnl))
                .where(PaperTrade.resolved == True, PaperTrade.entry_price > 0.02)  # noqa: E712
            )).scalar() or 0.0
            all_wr = round(all_wins / all_closed * 100, 1) if all_closed else None
            # Unique market counts (so we don't triple-count when multiple strategies bet same market)
            unique_markets_open = (await session.execute(
                select(func.count(func.distinct(PaperTrade.market_id)))
                .where(PaperTrade.resolved == False, PaperTrade.entry_price > 0.02)  # noqa: E712
            )).scalar() or 0
            unique_markets_closed = (await session.execute(
                select(func.count(func.distinct(PaperTrade.market_id)))
                .where(PaperTrade.resolved == True, PaperTrade.entry_price > 0.02)  # noqa: E712
            )).scalar() or 0

            if all_open == 0 and all_closed == 0:
                pt_status = "not_started"
                pt_explanation = (
                    "No paper trades have been placed yet. "
                    "The paper trading scheduler needs to be running on the compute server."
                )
            elif all_open > 0 and all_closed == 0:
                pt_status = "active_no_results"
                pt_explanation = (
                    f"{unique_markets_open:,} markets with open trades, waiting to resolve."
                )
            else:
                pt_explanation = (
                    f"{unique_markets_open:,} markets open, {unique_markets_closed:,} resolved. "
                    f"PnL: ${float(all_pnl):.2f} across {all_closed:,} trades."
                )
                pt_status = "active"

            # Recent closed trades for the ledger (all trades, entry > $0.02)
            recent_trades_rows = (await session.execute(
                select(PaperTrade, Market.question, Market.market_category)
                .join(Market, Market.id == PaperTrade.market_id)
                .where(PaperTrade.resolved == True, PaperTrade.entry_price > 0.02)  # noqa: E712
                .order_by(desc(PaperTrade.resolved_at))
                .limit(200)
            )).all()
            recent_trades = []
            for trade, question, market_cat in recent_trades_rows:
                recent_trades.append({
                    "question": (question or "?")[:80],
                    "side": trade.side,
                    "entry_price": round(float(trade.entry_price or 0), 3),
                    "won": trade.won,
                    "pnl": round(float(trade.pnl or 0), 4),
                    "resolved_at": _iso_utc(trade.resolved_at),
                    "source": trade.trade_source or "ngram",
                    "category": market_cat or "other",
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

            # Hourly resolution schedule — next 48 hours
            schedule_rows = (await session.execute(
                select(
                    func.date_trunc('hour', Market.end_date).label('hour_bucket'),
                    func.count(PaperTrade.id).label('trade_count'),
                )
                .join(Market, Market.id == PaperTrade.market_id)
                .where(
                    *open_real_preds,
                    Market.end_date > now,
                    Market.end_date < now + timedelta(hours=48),
                )
                .group_by('hour_bucket')
                .order_by('hour_bucket')
            )).all()
            resolution_schedule = [
                {"hour": _iso_utc(row[0]), "count": row[1]}
                for row in schedule_rows
            ]

            # Average edge
            avg_edge_raw = (await session.execute(
                select(func.avg(PaperTrade.edge))
                .join(Market, Market.id == PaperTrade.market_id)
                .where(*open_real_preds)
            )).scalar()
            avg_edge_pct = round(float(avg_edge_raw or 0) * 100, 1)

            # --- COHORT BREAKDOWN: crypto up/down vs everything else ---
            _crypto_pred = noise_market_predicate()  # Market.question ILIKE '%up or down%'
            _base_resolved = [PaperTrade.entry_price > 0.02, PaperTrade.resolved == True]

            def _cohort_stats(extra_pred):
                """Return (closed, wins, pnl) coroutines for a cohort."""
                preds = [*_base_resolved, extra_pred]
                return preds

            # Crypto cohort
            crypto_preds = _cohort_stats(_crypto_pred)
            crypto_closed = (await session.execute(
                select(func.count(PaperTrade.id))
                .join(Market, Market.id == PaperTrade.market_id)
                .where(*crypto_preds)
            )).scalar() or 0
            crypto_wins = (await session.execute(
                select(func.count(PaperTrade.id))
                .join(Market, Market.id == PaperTrade.market_id)
                .where(*crypto_preds, PaperTrade.won == True)
            )).scalar() or 0
            crypto_pnl = (await session.execute(
                select(func.sum(PaperTrade.pnl))
                .join(Market, Market.id == PaperTrade.market_id)
                .where(*crypto_preds)
            )).scalar() or 0.0
            crypto_wr = round(crypto_wins / crypto_closed * 100, 1) if crypto_closed else None
            crypto_open = (await session.execute(
                select(func.count(PaperTrade.id))
                .join(Market, Market.id == PaperTrade.market_id)
                .where(PaperTrade.entry_price > 0.02, _crypto_pred,
                       PaperTrade.resolved == False)
            )).scalar() or 0

            # Non-crypto cohort (already computed above as closed_count/wins/total_pnl)
            # Combined = crypto + non-crypto
            combined_closed = closed_count + crypto_closed
            combined_wins = wins + crypto_wins
            combined_pnl = float(total_pnl) + float(crypto_pnl)
            combined_wr = round(combined_wins / combined_closed * 100, 1) if combined_closed else None
            combined_open = open_count + crypto_open

            # --- TRADE SOURCE BREAKDOWN (ngram vs factor_match vs grok_direct vs combined) ---
            source_stats = {}
            _empty_src = {
                "open": 0,
                "closed": 0,
                "wins": 0,
                "win_rate_pct": None,
                "pnl": 0.0,
                "pnl_per_bet": None,
                "ev_per_bet": None,
                "expected_open_pnl": None,
                "avg_entry_open": None,
                "avg_entry_closed": None,
                "unique_markets_open": 0,
                "unique_markets_closed": 0,
                "book": "strategy",
            }
            source_open_preds = real_trade_predicates(resolved=False)
            source_closed_preds = real_trade_predicates(resolved=True)
            for src in _ALL_TRACKED_TRADE_SOURCES:
                src_closed = (await session.execute(
                    select(func.count(PaperTrade.id))
                    .join(Market, Market.id == PaperTrade.market_id)
                    .where(PaperTrade.trade_source == src, *source_closed_preds)
                )).scalar() or 0
                src_wins = (await session.execute(
                    select(func.count(PaperTrade.id))
                    .join(Market, Market.id == PaperTrade.market_id)
                    .where(PaperTrade.trade_source == src, PaperTrade.won == True, *source_closed_preds)  # noqa: E712
                )).scalar() or 0
                src_pnl = (await session.execute(
                    select(func.sum(PaperTrade.pnl))
                    .join(Market, Market.id == PaperTrade.market_id)
                    .where(PaperTrade.trade_source == src, *source_closed_preds)
                )).scalar() or 0.0
                src_open = (await session.execute(
                    select(func.count(PaperTrade.id))
                    .join(Market, Market.id == PaperTrade.market_id)
                    .where(PaperTrade.trade_source == src, *source_open_preds)
                )).scalar() or 0
                src_open_entry_sum = (await session.execute(
                    select(func.sum(PaperTrade.entry_price))
                    .join(Market, Market.id == PaperTrade.market_id)
                    .where(PaperTrade.trade_source == src, *source_open_preds)
                )).scalar() or 0.0
                src_closed_entry_sum = (await session.execute(
                    select(func.sum(PaperTrade.entry_price))
                    .join(Market, Market.id == PaperTrade.market_id)
                    .where(PaperTrade.trade_source == src, *source_closed_preds)
                )).scalar() or 0.0
                src_unique_open = (await session.execute(
                    select(func.count(func.distinct(PaperTrade.market_id)))
                    .join(Market, Market.id == PaperTrade.market_id)
                    .where(PaperTrade.trade_source == src, *source_open_preds)
                )).scalar() or 0
                src_unique_closed = (await session.execute(
                    select(func.count(func.distinct(PaperTrade.market_id)))
                    .join(Market, Market.id == PaperTrade.market_id)
                    .where(PaperTrade.trade_source == src, *source_closed_preds)
                )).scalar() or 0
                src_pnl_f = float(src_pnl or 0.0)
                avg_entry_open = (float(src_open_entry_sum) / src_open) if src_open else None
                avg_entry_closed = (float(src_closed_entry_sum) / src_closed) if src_closed else None
                derived = _compute_source_derived_metrics(
                    closed=int(src_closed),
                    wins=int(src_wins),
                    pnl=src_pnl_f,
                    open_count=int(src_open),
                    avg_entry_open=avg_entry_open,
                    avg_entry_closed=avg_entry_closed,
                )
                source_stats[src] = {
                    "open": src_open, "closed": src_closed,
                    "wins": src_wins, "win_rate_pct": derived["win_rate_pct"],
                    "pnl": round(src_pnl_f, 2),
                    "pnl_per_bet": (
                        round(float(derived["pnl_per_bet"]), 4)
                        if derived["pnl_per_bet"] is not None else None
                    ),
                    "ev_per_bet": (
                        round(float(derived["ev_per_bet"]), 4)
                        if derived["ev_per_bet"] is not None else None
                    ),
                    "expected_open_pnl": (
                        round(float(derived["expected_open_pnl"]), 2)
                        if derived["expected_open_pnl"] is not None else None
                    ),
                    "avg_entry_open": round(float(avg_entry_open), 4) if avg_entry_open is not None else None,
                    "avg_entry_closed": round(float(avg_entry_closed), 4) if avg_entry_closed is not None else None,
                    "unique_markets_open": src_unique_open,
                    "unique_markets_closed": src_unique_closed,
                    "book": "strategy" if src in _DIRECT_TRADE_SOURCES else "control",
                    "_open_entry_sum": float(src_open_entry_sum or 0.0),
                    "_closed_entry_sum": float(src_closed_entry_sum or 0.0),
                }

            def _aggregate_book(label: str, keys: tuple[str, ...]) -> dict:
                open_total = sum(int(source_stats.get(k, {}).get("open", 0) or 0) for k in keys)
                closed_total = sum(int(source_stats.get(k, {}).get("closed", 0) or 0) for k in keys)
                wins_total = sum(int(source_stats.get(k, {}).get("wins", 0) or 0) for k in keys)
                pnl_total = sum(float(source_stats.get(k, {}).get("pnl", 0.0) or 0.0) for k in keys)
                open_entry_sum = sum(float(source_stats.get(k, {}).get("_open_entry_sum", 0.0) or 0.0) for k in keys)
                closed_entry_sum = sum(float(source_stats.get(k, {}).get("_closed_entry_sum", 0.0) or 0.0) for k in keys)
                avg_open = (open_entry_sum / open_total) if open_total else None
                avg_closed = (closed_entry_sum / closed_total) if closed_total else None
                derived = _compute_source_derived_metrics(
                    closed=closed_total,
                    wins=wins_total,
                    pnl=pnl_total,
                    open_count=open_total,
                    avg_entry_open=avg_open,
                    avg_entry_closed=avg_closed,
                )
                return {
                    "label": label,
                    "open_count": open_total,
                    "closed_count": closed_total,
                    "wins": wins_total,
                    "win_rate_pct": derived["win_rate_pct"],
                    "total_pnl": round(pnl_total, 2),
                    "pnl_per_bet": (
                        round(float(derived["pnl_per_bet"]), 4)
                        if derived["pnl_per_bet"] is not None else None
                    ),
                    "ev_per_bet": (
                        round(float(derived["ev_per_bet"]), 4)
                        if derived["ev_per_bet"] is not None else None
                    ),
                    "expected_open_pnl": (
                        round(float(derived["expected_open_pnl"]), 2)
                        if derived["expected_open_pnl"] is not None else None
                    ),
                }

            strategy_book = _aggregate_book("Strategy (Direct)", _DIRECT_TRADE_SOURCES)
            control_book = _aggregate_book("Control (Inverse)", _INVERSE_TRADE_SOURCES)

            # --- PER-CATEGORY PERFORMANCE BREAKDOWN ---
            cat_rows = (await session.execute(
                select(
                    func.coalesce(Market.market_category, "other").label("cat"),
                    func.count(PaperTrade.id).label("closed"),
                    func.sum(case((PaperTrade.won == True, 1), else_=0)).label("wins"),
                    func.sum(PaperTrade.pnl).label("pnl"),
                )
                .join(Market, Market.id == PaperTrade.market_id)
                .where(
                    PaperTrade.resolved == True,  # noqa: E712
                    PaperTrade.entry_price > 0.02,
                    PaperTrade.trade_source.in_(_DIRECT_TRADE_SOURCES),
                )
                .group_by("cat")
                .order_by(func.sum(PaperTrade.pnl).desc())
            )).all()
            by_category = {}
            for cat, closed_c, wins_c, pnl_c in cat_rows:
                cat_str = str(cat or "other")
                closed_i = int(closed_c or 0)
                wins_i = int(wins_c or 0)
                pnl_f = float(pnl_c or 0.0)
                wr = round(wins_i / closed_i * 100, 1) if closed_i else None
                by_category[cat_str] = {
                    "closed": closed_i,
                    "wins": wins_i,
                    "win_rate_pct": wr,
                    "pnl": round(pnl_f, 2),
                }

            cat_rows_all = (await session.execute(
                select(
                    func.coalesce(Market.market_category, "other").label("cat"),
                    func.count(PaperTrade.id).label("closed"),
                    func.sum(case((PaperTrade.won == True, 1), else_=0)).label("wins"),
                    func.sum(PaperTrade.pnl).label("pnl"),
                )
                .join(Market, Market.id == PaperTrade.market_id)
                .where(PaperTrade.resolved == True, PaperTrade.entry_price > 0.02)  # noqa: E712
                .group_by("cat")
                .order_by(func.sum(PaperTrade.pnl).desc())
            )).all()
            by_category_all = {}
            for cat, closed_c, wins_c, pnl_c in cat_rows_all:
                cat_str = str(cat or "other")
                closed_i = int(closed_c or 0)
                wins_i = int(wins_c or 0)
                pnl_f = float(pnl_c or 0.0)
                wr = round(wins_i / closed_i * 100, 1) if closed_i else None
                by_category_all[cat_str] = {
                    "closed": closed_i,
                    "wins": wins_i,
                    "win_rate_pct": wr,
                    "pnl": round(pnl_f, 2),
                }

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
            heartbeats = _select_relevant_heartbeats(heartbeats, now)

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
                # Services with "ok" or "idle" status have a 10-min grace period;
                # "running" services get 60 min before stale (heavy ops are slow)
                stale_threshold = 10 if status_text in ("ok", "idle") else 60
                if age is not None and age > stale_threshold:
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
                select(func.count(func.distinct(TradingRule.name))).where(TradingRule.active == True)
            )).scalar() or 0
            ngram_count = (await session.execute(
                select(func.count(NgramStat.id))
            )).scalar() or 0

            def _public_source_stats(src_key: str, label: str) -> dict:
                src = {**source_stats.get(src_key, _empty_src)}
                for hidden_key in ("_open_entry_sum", "_closed_entry_sum"):
                    src.pop(hidden_key, None)
                src["label"] = label
                return src

            result = {
                "generated_at": _iso_utc(now),
                "action": action,
                "hit_rate": {
                    "primary_pct": all_wr,
                    "primary_label": "Paper Trade Results",
                    "rules_high_confidence": rule_count_high,
                    "rules_medium_confidence": rule_count_mid,
                    "opportunities_now": len(opportunities),
                    "paper_trade_pct": all_wr,
                    "paper_trade_scored": all_closed,
                    "paper_trade_correct": all_wins,
                    "explanation": (
                        f"Paper trade results: {all_wr}% win rate ({all_wins} wins out of {all_closed} trades). "
                        if all_closed > 0 and all_wr is not None else
                        f"{all_open:,} paper trades open, waiting for markets to resolve. "
                        if all_open > 0 else
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
                    "open_count": all_open,
                    "closed_count": all_closed,
                    "trade_counts": {"open": all_open, "closed": all_closed},
                    "total_pnl": round(float(all_pnl), 2),
                    "win_rate_pct": all_wr,
                    "unique_markets_open": unique_markets_open,
                    "unique_markets_closed": unique_markets_closed,
                    "unique_market_counts": {
                        "open": unique_markets_open,
                        "closed": unique_markets_closed,
                    },
                    "open_trades": open_trades_list,
                    "total_unique_open": total_unique_open,
                    "recent_closed": recent_trades,
                    "ending_today": ending_today,
                    "ending_this_week": ending_week,
                    "resolution_schedule": resolution_schedule,
                    "avg_edge_pct": avg_edge_pct,
                    "cohorts": {
                        "crypto": {
                            "label": "Crypto Up/Down",
                            "open": crypto_open,
                            "closed": crypto_closed,
                            "wins": crypto_wins,
                            "win_rate_pct": crypto_wr,
                            "pnl": round(float(crypto_pnl), 2),
                        },
                        "other": {
                            "label": "Non-Crypto Markets",
                            "open": open_count,
                            "closed": closed_count,
                            "wins": wins,
                            "win_rate_pct": pt_win_rate,
                            "pnl": round(float(total_pnl), 2),
                        },
                        "combined": {
                            "label": "All Markets",
                            "open": combined_open,
                            "closed": combined_closed,
                            "wins": combined_wins,
                            "win_rate_pct": combined_wr,
                            "pnl": round(combined_pnl, 2),
                        },
                    },
                    "by_source": {
                        "ngram": _public_source_stats("ngram", "Ngram Rules"),
                        "factor_match": _public_source_stats("factor_match", "Factor Match"),
                        "grok_direct": _public_source_stats("grok_direct", "Grok Predictions"),
                        "combined": _public_source_stats("combined", "Ngram + Factor"),
                        "ngram_inverse": _public_source_stats("ngram_inverse", "Ngram Inverse"),
                        "factor_match_inv": _public_source_stats("factor_match_inv", "Factor Match Inverse"),
                        "grok_inv": _public_source_stats("grok_inv", "Grok Inverse"),
                        "combined_inverse": _public_source_stats("combined_inverse", "Combined Inverse"),
                    },
                    "strategy_book": strategy_book,
                    "control_book": control_book,
                    "by_category": by_category,
                    "by_category_all": by_category_all,
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
            _human_dashboard_cache_payload = result
            _human_dashboard_cache_updated_at = _utcnow_naive()
            return result
    except Exception as e:
        log.exception("human_dashboard failed")
        if _human_dashboard_cache_payload is not None:
            return _human_dashboard_cache_payload
        return {"error": str(e), "generated_at": _iso_utc(now)}


def _profile_row_dict(profile: StrategyProfile) -> dict:
    return {
        "id": int(profile.id),
        "name": profile.name,
        "description": profile.description or "",
        "active": bool(profile.active),
        "include_inverse": bool(profile.include_inverse),
        "created_at": _iso_utc(profile.created_at),
        "updated_at": _iso_utc(profile.updated_at),
    }


async def _profile_side_stats(session, profile_id: int, side_source: str) -> dict:
    preds = [PaperTrade.profile_id == profile_id, PaperTrade.trade_source == side_source, PaperTrade.entry_price > 0.02]
    closed = (await session.execute(
        select(func.count(PaperTrade.id)).where(*preds, PaperTrade.resolved == True)  # noqa: E712
    )).scalar() or 0
    wins = (await session.execute(
        select(func.count(PaperTrade.id)).where(*preds, PaperTrade.resolved == True, PaperTrade.won == True)  # noqa: E712
    )).scalar() or 0
    pnl = (await session.execute(
        select(func.sum(PaperTrade.pnl)).where(*preds, PaperTrade.resolved == True)  # noqa: E712
    )).scalar() or 0.0
    open_count = (await session.execute(
        select(func.count(PaperTrade.id)).where(*preds, PaperTrade.resolved == False)  # noqa: E712
    )).scalar() or 0
    avg_entry_open = (await session.execute(
        select(func.avg(PaperTrade.entry_price)).where(*preds, PaperTrade.resolved == False)  # noqa: E712
    )).scalar()
    avg_entry_closed = (await session.execute(
        select(func.avg(PaperTrade.entry_price)).where(*preds, PaperTrade.resolved == True)  # noqa: E712
    )).scalar()
    derived = _compute_source_derived_metrics(
        closed=int(closed),
        wins=int(wins),
        pnl=float(pnl or 0.0),
        open_count=int(open_count),
        avg_entry_open=float(avg_entry_open) if avg_entry_open is not None else None,
        avg_entry_closed=float(avg_entry_closed) if avg_entry_closed is not None else None,
    )
    return {
        "open_count": int(open_count),
        "closed_count": int(closed),
        "wins": int(wins),
        "total_pnl": round(float(pnl or 0.0), 2),
        "win_rate_pct": derived["win_rate_pct"],
        "pnl_per_bet": round(float(derived["pnl_per_bet"]), 4) if derived["pnl_per_bet"] is not None else None,
        "ev_per_bet": round(float(derived["ev_per_bet"]), 4) if derived["ev_per_bet"] is not None else None,
        "expected_open_pnl": round(float(derived["expected_open_pnl"]), 2) if derived["expected_open_pnl"] is not None else None,
    }


@app.get("/api/profile/rule-leaderboard")
async def profile_rule_leaderboard(
    limit: int = Query(300, ge=1, le=2000),
    min_samples: int = Query(200, ge=1, le=100000),
):
    """Top rules for profile construction."""
    try:
        async with SessionLocal() as session:
            rows = (await session.execute(
                select(TradingRule)
                .where(
                    TradingRule.active == True,  # noqa: E712
                    TradingRule.rule_type == "ngram",
                    TradingRule.sample_size >= min_samples,
                )
                .order_by(
                    desc(TradingRule.avg_roi),
                    desc(TradingRule.win_rate),
                    desc(TradingRule.sample_size),
                )
                .limit(limit)
            )).scalars().all()

            realized_rows = (await session.execute(
                select(
                    PaperTrade.rule_id,
                    func.count(PaperTrade.id),
                    func.sum(case((PaperTrade.won == True, 1), else_=0)),
                    func.sum(PaperTrade.pnl),
                )
                .where(
                    PaperTrade.rule_id.is_not(None),
                    PaperTrade.resolved == True,  # noqa: E712
                    PaperTrade.entry_price > 0.02,
                    PaperTrade.trade_source.in_((*_DIRECT_TRADE_SOURCES, "profile_direct")),
                )
                .group_by(PaperTrade.rule_id)
            )).all()
            realized_by_rule = {
                int(rule_id): {
                    "closed": int(closed or 0),
                    "wins": int(wins or 0),
                    "pnl": float(pnl or 0.0),
                }
                for rule_id, closed, wins, pnl in realized_rows
                if rule_id is not None
            }

            out = []
            for rule in rows:
                realized = realized_by_rule.get(int(rule.id), {"closed": 0, "wins": 0, "pnl": 0.0})
                closed = realized["closed"]
                wins = realized["wins"]
                pnl = realized["pnl"]
                out.append({
                    "id": int(rule.id),
                    "name": rule.name,
                    "rule_type": rule.rule_type,
                    "predicted_side": rule.predicted_side,
                    "tier": int(rule.tier or 3),
                    "quality_label": rule.quality_label or "exploratory",
                    "sample_size": int(rule.sample_size or 0),
                    "win_rate_pct": round(float(rule.win_rate or 0) * 100, 1),
                    "avg_roi": round(float(rule.avg_roi or 0), 4),
                    "breakeven_price": round(float(rule.breakeven_price or 0), 4),
                    "plain_english": _rule_to_plain_english(rule),
                    "realized_closed": closed,
                    "realized_wins": wins,
                    "realized_win_rate_pct": round(wins / closed * 100, 1) if closed else None,
                    "realized_pnl": round(pnl, 2),
                    "realized_pnl_per_bet": round(pnl / closed, 4) if closed else None,
                })
            return {
                "generated_at": _iso_utc(_utcnow_naive()),
                "limit": limit,
                "min_samples": min_samples,
                "rows": out,
            }
    except Exception as e:
        log.exception("profile_rule_leaderboard failed")
        return {"error": str(e), "rows": []}


@app.get("/api/profiles")
async def list_profiles():
    """List strategy profiles + current paper-trading scoreboard."""
    try:
        async with SessionLocal() as session:
            profiles = (await session.execute(
                select(StrategyProfile).order_by(StrategyProfile.created_at.asc(), StrategyProfile.id.asc())
            )).scalars().all()
            if not profiles:
                return {"generated_at": _iso_utc(_utcnow_naive()), "profiles": []}

            rule_counts = (await session.execute(
                select(
                    StrategyProfileRule.profile_id,
                    func.count(StrategyProfileRule.id),
                )
                .where(StrategyProfileRule.enabled == True)  # noqa: E712
                .group_by(StrategyProfileRule.profile_id)
            )).all()
            rule_count_map = {int(pid): int(cnt or 0) for pid, cnt in rule_counts}

            out = []
            for profile in profiles:
                row = _profile_row_dict(profile)
                row["selected_rules"] = rule_count_map.get(int(profile.id), 0)
                row["direct"] = await _profile_side_stats(session, int(profile.id), "profile_direct")
                row["inverse"] = await _profile_side_stats(session, int(profile.id), "profile_inverse")
                out.append(row)

            return {"generated_at": _iso_utc(_utcnow_naive()), "profiles": out}
    except Exception as e:
        log.exception("list_profiles failed")
        return {"error": str(e), "profiles": []}


@app.post("/api/profiles")
async def create_profile(payload: dict = Body(...)):
    """Create a new strategy profile."""
    try:
        name = str((payload or {}).get("name", "")).strip()
        if not name:
            raise HTTPException(status_code=400, detail="name is required")
        if len(name) > 80:
            raise HTTPException(status_code=400, detail="name too long (max 80)")
        description = str((payload or {}).get("description", "") or "").strip()
        include_inverse = bool((payload or {}).get("include_inverse", True))

        async with SessionLocal() as session:
            existing = (await session.execute(
                select(StrategyProfile).where(func.lower(StrategyProfile.name) == name.lower())
            )).scalar_one_or_none()
            if existing is not None:
                raise HTTPException(status_code=409, detail="profile name already exists")
            row = StrategyProfile(
                name=name,
                description=description,
                include_inverse=include_inverse,
                active=True,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _profile_row_dict(row)
    except HTTPException:
        raise
    except Exception as e:
        log.exception("create_profile failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/profiles/{profile_id}/rules")
async def set_profile_rules(profile_id: int, payload: dict = Body(...)):
    """Replace profile rule set with the provided rule IDs."""
    try:
        rule_ids_raw = (payload or {}).get("rule_ids", [])
        if not isinstance(rule_ids_raw, list):
            raise HTTPException(status_code=400, detail="rule_ids must be a list")
        rule_ids = sorted({int(rid) for rid in rule_ids_raw if str(rid).strip()})

        async with SessionLocal() as session:
            profile = await session.get(StrategyProfile, profile_id)
            if profile is None:
                raise HTTPException(status_code=404, detail="profile not found")

            valid_rule_rows = (await session.execute(
                select(TradingRule.id).where(
                    TradingRule.id.in_(rule_ids),
                    TradingRule.active == True,  # noqa: E712
                    TradingRule.rule_type == "ngram",
                )
            )).all() if rule_ids else []
            valid_rule_ids = sorted({int(rid) for (rid,) in valid_rule_rows})

            await session.execute(
                delete(StrategyProfileRule).where(StrategyProfileRule.profile_id == profile_id)
            )
            for rid in valid_rule_ids:
                session.add(StrategyProfileRule(
                    profile_id=profile_id,
                    rule_id=rid,
                    enabled=True,
                ))
            profile.updated_at = _utcnow_naive()
            await session.commit()
            return {
                "profile_id": profile_id,
                "requested_rule_ids": rule_ids,
                "stored_rule_ids": valid_rule_ids,
                "stored_count": len(valid_rule_ids),
            }
    except HTTPException:
        raise
    except Exception as e:
        log.exception("set_profile_rules failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/profiles/{profile_id}/performance")
async def profile_performance(profile_id: int):
    """Detailed performance view for one profile."""
    try:
        async with SessionLocal() as session:
            profile = await session.get(StrategyProfile, profile_id)
            if profile is None:
                raise HTTPException(status_code=404, detail="profile not found")

            direct = await _profile_side_stats(session, profile_id, "profile_direct")
            inverse = await _profile_side_stats(session, profile_id, "profile_inverse")
            by_category_rows = (await session.execute(
                select(
                    func.coalesce(Market.market_category, "other").label("cat"),
                    func.count(PaperTrade.id).label("closed"),
                    func.sum(case((PaperTrade.won == True, 1), else_=0)).label("wins"),
                    func.sum(PaperTrade.pnl).label("pnl"),
                )
                .join(Market, Market.id == PaperTrade.market_id)
                .where(
                    PaperTrade.profile_id == profile_id,
                    PaperTrade.trade_source == "profile_direct",
                    PaperTrade.resolved == True,  # noqa: E712
                    PaperTrade.entry_price > 0.02,
                )
                .group_by("cat")
                .order_by(func.sum(PaperTrade.pnl).desc())
            )).all()
            by_category = {}
            for cat, closed_c, wins_c, pnl_c in by_category_rows:
                closed_i = int(closed_c or 0)
                wins_i = int(wins_c or 0)
                by_category[str(cat or "other")] = {
                    "closed": closed_i,
                    "wins": wins_i,
                    "win_rate_pct": round(wins_i / closed_i * 100, 1) if closed_i else None,
                    "pnl": round(float(pnl_c or 0.0), 2),
                }

            selected_rule_rows = (await session.execute(
                select(TradingRule.id, TradingRule.name)
                .join(StrategyProfileRule, StrategyProfileRule.rule_id == TradingRule.id)
                .where(
                    StrategyProfileRule.profile_id == profile_id,
                    StrategyProfileRule.enabled == True,  # noqa: E712
                )
                .order_by(TradingRule.win_rate.desc(), TradingRule.sample_size.desc())
            )).all()

            return {
                "generated_at": _iso_utc(_utcnow_naive()),
                "profile": _profile_row_dict(profile),
                "selected_rules": [{"id": int(rid), "name": name} for rid, name in selected_rule_rows],
                "direct": direct,
                "inverse": inverse,
                "by_category": by_category,
            }
    except HTTPException:
        raise
    except Exception as e:
        log.exception("profile_performance failed")
        raise HTTPException(status_code=500, detail=str(e))


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
            heartbeat_rows = _select_relevant_heartbeats(heartbeat_rows, now)

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


@app.get("/api/backtest-summary")
async def backtest_summary():
    """Backtest results: top rules by PnL, inverse flip candidates, agreement tiers, category matrix.

    Deduplicates by rule name to avoid showing the same rule 39 times when
    there are duplicate trading_rules rows with identical conditions.
    """
    try:
        async with SessionLocal() as session:
            from polyedge.models import AgreementSignal

            # Top 50 unique rules by direct PnL — deduplicate by rule name
            top_direct_rows = (await session.execute(text("""
                SELECT DISTINCT ON (tr.name)
                    br.rule_id, tr.name, tr.rule_type, tr.conditions_json,
                    tr.predicted_side, br.total_matches, br.wins_direct,
                    br.pnl_direct, br.wins_inverse, br.pnl_inverse,
                    br.recommended_side, br.edge_magnitude
                FROM backtest_results br
                JOIN trading_rules tr ON tr.id = br.rule_id
                ORDER BY tr.name, br.pnl_direct DESC
            """))).all()
            # Sort by pnl_direct desc and take top 50
            top_direct_sorted = sorted(top_direct_rows, key=lambda r: r[7], reverse=True)[:50]

            # Top 50 unique rules where inverse outperforms
            top_inverse_rows = [r for r in top_direct_rows if r[10] == "inverse"]
            top_inverse_sorted = sorted(top_inverse_rows, key=lambda r: r[9], reverse=True)[:50]

            # Summary counts (deduplicated)
            total_backtested = len(top_direct_rows)
            total_flip_candidates = len(top_inverse_rows)

            # Agreement signals
            agreement_rows = (await session.execute(
                select(AgreementSignal).order_by(
                    AgreementSignal.agreement_tier,
                    AgreementSignal.pnl.desc(),
                )
            )).scalars().all()

            # Category matrix: deduplicated by rule name + category
            cat_matrix_rows = (await session.execute(text("""
                SELECT DISTINCT ON (tr.name, rcp.category)
                    rcp.rule_id, tr.name, rcp.category, rcp.sample_size,
                    rcp.pnl_direct, rcp.pnl_inverse, rcp.recommended_side
                FROM rule_category_performance rcp
                JOIN trading_rules tr ON tr.id = rcp.rule_id
                WHERE rcp.sample_size >= 10
                ORDER BY tr.name, rcp.category, rcp.pnl_direct DESC
            """))).all()
            cat_matrix_sorted = sorted(cat_matrix_rows, key=lambda r: r[4], reverse=True)[:200]

            def _bt_row(r):
                matches = r[5] or 0
                return {
                    "rule_id": r[0],
                    "rule_name": r[1],
                    "rule_type": r[2],
                    "predicted_side": r[4],
                    "total_matches": matches,
                    "wins_direct": r[6],
                    "pnl_direct": round(r[7], 2),
                    "wins_inverse": r[8],
                    "pnl_inverse": round(r[9], 2),
                    "recommended_side": r[10],
                    "edge_magnitude": round(r[11], 4),
                    "win_rate_direct": round(r[6] / matches * 100, 1) if matches else 0,
                    "win_rate_inverse": round(r[8] / matches * 100, 1) if matches else 0,
                }

            return {
                "total_rules_backtested": total_backtested,
                "total_flip_candidates": total_flip_candidates,
                "top_rules_direct": [_bt_row(r) for r in top_direct_sorted],
                "top_rules_inverse": [_bt_row(r) for r in top_inverse_sorted],
                "agreement_tiers": [
                    {
                        "tier": a.agreement_tier,
                        "category": a.category,
                        "sample_size": a.sample_size,
                        "wins": a.wins,
                        "pnl": round(a.pnl, 2),
                        "avg_pnl": round(a.avg_pnl_per_trade, 4),
                        "win_rate_pct": round(a.wins / a.sample_size * 100, 1) if a.sample_size else 0,
                    }
                    for a in agreement_rows
                ],
                "category_matrix": [
                    {
                        "rule_id": r[0],
                        "rule_name": r[1],
                        "category": r[2],
                        "sample_size": r[3],
                        "pnl_direct": round(r[4], 2),
                        "pnl_inverse": round(r[5], 2),
                        "recommended_side": r[6],
                    }
                    for r in cat_matrix_sorted
                ],
            }
    except Exception as e:
        log.exception("backtest_summary failed")
        return {"error": str(e)}


@app.get("/api/rule-category-performance")
async def rule_category_performance(min_trades: int = 3, limit: int = 500):
    """Rule-level performance broken down by market category.

    Returns each specific rule (with its actual conditions like ngram phrase,
    threshold, etc.) and how it performs in each category.  Sorted by PnL desc.
    """
    try:
        async with SessionLocal() as session:
            rows = (await session.execute(text("""
                SELECT
                    tr.id AS rule_id,
                    tr.rule_type,
                    tr.name AS rule_name,
                    tr.conditions_json,
                    tr.predicted_side,
                    COALESCE(m.market_category, 'other') AS category,
                    COUNT(*) AS trades,
                    SUM(CASE WHEN pt.won THEN 1 ELSE 0 END) AS wins,
                    ROUND(SUM(pt.pnl)::numeric, 2) AS pnl,
                    ROUND(AVG(pt.entry_price)::numeric, 3) AS avg_entry,
                    ROUND(AVG(CASE WHEN pt.won THEN 1.0 ELSE 0.0 END)::numeric, 3) AS win_rate
                FROM paper_trades pt
                JOIN trading_rules tr ON pt.rule_id = tr.id
                JOIN markets m ON pt.market_id = m.id
                WHERE pt.resolved = true AND pt.entry_price > 0.02
                GROUP BY tr.id, tr.rule_type, tr.name, tr.conditions_json,
                         tr.predicted_side, m.market_category
                HAVING COUNT(*) >= :min_trades
                ORDER BY SUM(pt.pnl) DESC
                LIMIT :lim
            """), {"min_trades": min_trades, "lim": limit})).all()

            results = []
            for r in rows:
                row = dict(r._mapping)
                # Parse conditions to extract human-readable rule description
                cond = {}
                try:
                    cond = json.loads(row["conditions_json"]) if row["conditions_json"] else {}
                except (json.JSONDecodeError, TypeError):
                    pass

                rule_type = row["rule_type"] or "unknown"
                side = row["predicted_side"] or "?"
                description = ""
                if rule_type == "ngram":
                    phrase = cond.get("ngram", row["rule_name"].replace("ngram:", ""))
                    description = f'"{phrase}" -> {side}'
                elif rule_type == "single_threshold":
                    feat = (cond.get("feature") or "?").replace("_", " ")
                    op = cond.get("op", ">")
                    val = cond.get("value", "?")
                    description = f"{feat} {op} {val} -> {side}"
                elif rule_type == "two_feature":
                    parts = []
                    for f in cond.get("features", []):
                        feat = (f.get("feature") or "?").replace("_", " ")
                        parts.append(f"{feat} {f.get('op', '>')} {f.get('value', '?')}")
                    description = " AND ".join(parts) + f" -> {side}"
                elif rule_type == "decision_tree":
                    parts = []
                    for p in cond.get("path", []):
                        feat = (p.get("feature") or "?").replace("_", " ")
                        parts.append(f"{feat} {p.get('op', '>')} {p.get('value', '?')}")
                    description = " then ".join(parts) + f" -> {side}"
                else:
                    description = f"{row['rule_name']} -> {side}"

                results.append({
                    "rule_id": row["rule_id"],
                    "rule_type": rule_type,
                    "rule_name": row["rule_name"],
                    "description": description,
                    "predicted_side": side,
                    "category": row["category"],
                    "trades": int(row["trades"]),
                    "wins": int(row["wins"]),
                    "pnl": float(row["pnl"]),
                    "avg_entry": float(row["avg_entry"]),
                    "win_rate": float(row["win_rate"]),
                })

            # Also build a summary: best-performing rules across all categories
            rule_totals = {}
            for r in results:
                rid = r["rule_id"]
                if rid not in rule_totals:
                    rule_totals[rid] = {
                        "rule_id": rid, "description": r["description"],
                        "rule_type": r["rule_type"], "predicted_side": r["predicted_side"],
                        "total_trades": 0, "total_wins": 0, "total_pnl": 0.0,
                        "categories": {},
                    }
                rule_totals[rid]["total_trades"] += r["trades"]
                rule_totals[rid]["total_wins"] += r["wins"]
                rule_totals[rid]["total_pnl"] += r["pnl"]
                rule_totals[rid]["categories"][r["category"]] = {
                    "trades": r["trades"], "wins": r["wins"],
                    "pnl": r["pnl"], "win_rate": r["win_rate"],
                }

            top_rules = sorted(
                rule_totals.values(), key=lambda x: x["total_pnl"], reverse=True
            )[:100]
            for tr in top_rules:
                tr["total_pnl"] = round(tr["total_pnl"], 2)
                tr["total_win_rate"] = (
                    round(tr["total_wins"] / tr["total_trades"], 3)
                    if tr["total_trades"] > 0 else 0.0
                )

            return {
                "rule_category_rows": results,
                "top_rules_summary": top_rules,
                "total_rows": len(results),
            }
    except Exception as e:
        log.exception("rule_category_performance failed")
        return {"error": str(e), "rule_category_rows": [], "top_rules_summary": []}


# Backward compat
@app.get("/api/stats")
async def stats():
    return await dashboard_summary()
