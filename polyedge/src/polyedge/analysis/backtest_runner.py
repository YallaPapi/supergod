"""Batch backtest runner — evaluates trading rules against all historical resolved markets.

Designed to run on the 256GB compute server. Loads all resolved markets and all
trading rules, then tests each rule against every market to compute direct and
inverse PnL, per-category breakdowns, and recommended side.

Usage:
    python -m polyedge.analysis.backtest_runner --rule-type ngram --batch-size 500
    python -m polyedge.analysis.backtest_runner --rule-type threshold --batch-size 1000
    python -m polyedge.analysis.backtest_runner --rule-type all
"""
import argparse
import asyncio
import json
import logging
import os
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import delete, select

from polyedge.db import SessionLocal
from polyedge.models import (
    BacktestResult,
    DailyFeature,
    Market,
    RuleCategoryPerformance,
    TradingRule,
)
from polyedge.trading.pnl import calc_pnl

log = logging.getLogger(__name__)


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def match_ngram_rule(rule: dict, market) -> Optional[dict]:
    """Test a single ngram rule against a single market.

    Args:
        rule: dict with keys: phrase, side, win_rate, breakeven
        market: Market ORM object with question, yes_price, no_price,
                resolution, market_category

    Returns:
        Trade result dict or None if no match / invalid market.
    """
    # Filter out markets with extreme prices or non-binary resolution
    resolution = (market.resolution or "").upper().strip()
    if resolution not in ("YES", "NO"):
        return None

    yes_price = float(market.yes_price or 0)
    no_price = float(market.no_price) if market.no_price is not None else max(0.001, 1.0 - yes_price)

    if yes_price <= 0.02 or yes_price >= 0.98:
        return None

    # Check phrase match
    q_lower = (market.question or "").lower()
    if rule["phrase"] not in q_lower:
        return None

    side = rule["side"]
    entry_price = yes_price if side == "YES" else no_price
    won = resolution == side
    pnl = calc_pnl(entry_price, won)
    category = (market.market_category or "other").strip() or "other"

    return {
        "side": side,
        "entry_price": entry_price,
        "won": won,
        "pnl": pnl,
        "category": category,
    }


def compute_inverse_stats(trades: list[dict]) -> dict:
    """Compute inverse-side statistics for a list of trade results.

    For each trade, the inverse is: flip entry to 1-entry, flip won,
    recalculate PnL. This tells us whether betting the opposite side
    of what the rule predicts would have been more profitable.

    Returns:
        dict with wins_inverse, losses_inverse, pnl_inverse
    """
    wins_inverse = 0
    losses_inverse = 0
    pnl_inverse = 0.0

    for trade in trades:
        inv_entry = 1.0 - trade["entry_price"]
        inv_won = not trade["won"]
        inv_pnl = calc_pnl(inv_entry, inv_won)

        if inv_won:
            wins_inverse += 1
        else:
            losses_inverse += 1
        pnl_inverse += inv_pnl

    return {
        "wins_inverse": wins_inverse,
        "losses_inverse": losses_inverse,
        "pnl_inverse": pnl_inverse,
    }


_SHARED_MARKETS: list[dict] = []
_SHARED_FEATURES_BY_DATE: dict[str, dict[str, float]] = {}


def _init_worker(
    market_dicts: list[dict],
    features_by_date: Optional[dict[str, dict[str, float]]] = None,
) -> None:
    """Initialize worker process with shared market/features data."""
    global _SHARED_MARKETS, _SHARED_FEATURES_BY_DATE
    _SHARED_MARKETS = market_dicts
    _SHARED_FEATURES_BY_DATE = features_by_date or {}


def _edge_from_rule_win_rate(rule: dict) -> float:
    win_rate = rule.get("win_rate", 0.5)
    try:
        wr = float(win_rate) if win_rate is not None else 0.5
    except (TypeError, ValueError):
        wr = 0.5
    return abs(wr - 0.5)


def _empty_worker_result(rule: dict) -> dict:
    return {
        "rule_id": rule["id"],
        "total_matches": 0,
        "wins_direct": 0,
        "losses_direct": 0,
        "pnl_direct": 0.0,
        "wins_inverse": 0,
        "losses_inverse": 0,
        "pnl_inverse": 0.0,
        "recommended_side": "direct",
        "edge_magnitude": _edge_from_rule_win_rate(rule),
        "category_stats": {},
    }


def _check_threshold_condition(condition: dict, features: dict[str, float]) -> bool:
    feat = condition.get("feature", "")
    op = condition.get("op", ">")
    threshold = condition.get("value", 0)

    val = features.get(feat)
    # For ngram features, compute on the fly from question text
    if val is None and feat.startswith("ngram_"):
        q_lower = features.get("_question_lower", "")
        if q_lower:
            ngram_text = feat[6:].replace("_", " ")  # strip "ngram_" prefix, restore spaces
            val = 1.0 if ngram_text in q_lower else 0.0
    if val is None:
        return False

    if op == ">":
        return val > threshold
    if op == ">=":
        return val >= threshold
    if op == "<":
        return val < threshold
    if op == "<=":
        return val <= threshold
    if op == "==":
        return val == threshold
    return False


def _check_rule_conditions_inline(
    rule_type: str,
    conditions: dict,
    features: dict[str, float],
) -> bool:
    if rule_type == "single_threshold":
        return _check_threshold_condition(conditions, features)

    if rule_type == "two_feature":
        feature_conditions = conditions.get("features", [])
        return all(_check_threshold_condition(c, features) for c in feature_conditions)

    if rule_type == "ngram":
        ngram = conditions.get("ngram", "")
        val = features.get(f"ngram_{str(ngram).replace(' ', '_')}", None)
        if val is None:
            q_lower = features.get("_question_lower", "")
            val = 1.0 if (q_lower and ngram.lower() in q_lower) else 0.0
        return val > 0.5

    if rule_type == "decision_tree":
        path = conditions.get("path", [])
        return all(_check_threshold_condition(c, features) for c in path)

    if rule_type == "logistic_regression":
        feat = conditions.get("feature", "")
        direction = conditions.get("direction", "positive")
        val = features.get(feat, 0.0)
        return val > 0 if direction == "positive" else val < 0

    if rule_type == "combined":
        ngram = conditions.get("ngram", "")
        val = features.get(f"ngram_{str(ngram).replace(' ', '_')}", None)
        if val is None:
            q_lower = features.get("_question_lower", "")
            val = 1.0 if (q_lower and ngram.lower() in q_lower) else 0.0
        if val <= 0.5:
            return False
        return _check_threshold_condition(conditions, features)

    return False


def _backtest_one_rule(rule: dict) -> dict:
    """Worker function for multiprocessing: backtest one rule against all markets.

    Markets are pre-loaded via _init_worker (not passed per-call).

    Returns:
        Dict with rule_id, trades list, and computed stats
    """
    trades = []
    for m in _SHARED_MARKETS:
        resolution = (m["resolution"] or "").upper().strip()
        if resolution not in ("YES", "NO"):
            continue
        yes_price = m["yes_price"]
        no_price = m["no_price"]
        if yes_price <= 0.02 or yes_price >= 0.98:
            continue
        q_lower = (m["question"] or "").lower()
        if rule["phrase"] not in q_lower:
            continue
        side = rule["side"]
        entry_price = yes_price if side == "YES" else no_price
        won = resolution == side
        # calc_pnl inline to avoid import in worker
        pnl = (1.0 - entry_price) if won else -entry_price
        category = (m["market_category"] or "other").strip() or "other"
        trades.append({
            "side": side,
            "entry_price": entry_price,
            "won": won,
            "pnl": pnl,
            "category": category,
        })

    # Compute stats
    if not trades:
        return _empty_worker_result(rule)

    wins_d = sum(1 for t in trades if t["won"])
    losses_d = len(trades) - wins_d
    pnl_d = sum(t["pnl"] for t in trades)

    # Inverse
    wins_i = losses_i = 0
    pnl_i = 0.0
    for t in trades:
        inv_entry = 1.0 - t["entry_price"]
        inv_won = not t["won"]
        inv_pnl = (1.0 - inv_entry) if inv_won else -inv_entry
        if inv_won:
            wins_i += 1
        else:
            losses_i += 1
        pnl_i += inv_pnl

    total = len(trades)
    recommended = "inverse" if pnl_i > pnl_d and total >= 30 else "direct"
    wr_d = wins_d / total if total > 0 else 0.5

    # Per-category
    cat_trades: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        cat_trades[t["category"]].append(t)

    cat_stats = {}
    for cat, ct in cat_trades.items():
        cw = sum(1 for t in ct if t["won"])
        cp = sum(t["pnl"] for t in ct)
        ciw = cil = 0
        cip = 0.0
        for t in ct:
            ie = 1.0 - t["entry_price"]
            iw = not t["won"]
            ip = (1.0 - ie) if iw else -ie
            if iw:
                ciw += 1
            else:
                cil += 1
            cip += ip
        cr = "inverse" if cip > cp and len(ct) >= 10 else "direct"
        cat_stats[cat] = {
            "sample_size": len(ct),
            "wins_direct": cw, "pnl_direct": round(cp, 6),
            "wins_inverse": ciw, "pnl_inverse": round(cip, 6),
            "recommended_side": cr,
        }

    return {
        "rule_id": rule["id"],
        "total_matches": total,
        "wins_direct": wins_d, "losses_direct": losses_d,
        "pnl_direct": round(pnl_d, 6),
        "wins_inverse": wins_i, "losses_inverse": losses_i,
        "pnl_inverse": round(pnl_i, 6),
        "recommended_side": recommended,
        "edge_magnitude": round(abs(wr_d - 0.5), 6),
        "category_stats": cat_stats,
    }


def _backtest_one_threshold_rule(rule: dict) -> dict:
    """Worker function for threshold-like rule types."""
    try:
        conditions = (
            json.loads(rule["conditions_json"])
            if isinstance(rule.get("conditions_json"), str)
            else (rule.get("conditions_json") or {})
        )
    except (json.JSONDecodeError, TypeError):
        return _empty_worker_result(rule)

    rule_type = str(rule.get("rule_type", "") or "")
    side = str(rule.get("predicted_side", "YES") or "YES")
    trades = []

    for m in _SHARED_MARKETS:
        resolution = (m["resolution"] or "").upper().strip()
        if resolution not in ("YES", "NO"):
            continue

        yes_price = m["yes_price"]
        no_price = m["no_price"]
        if yes_price <= 0.02 or yes_price >= 0.98:
            continue

        # Merge date-level features + market-level features
        date_key = m.get("date_key")
        date_feats = _SHARED_FEATURES_BY_DATE.get(date_key, {}) if date_key else {}
        market_feats = m.get("market_features", {})

        # Combined: date features as base, market features overlay
        # (market_feats always has _question_lower for ngram matching)
        features = {**date_feats, **market_feats}

        if not _check_rule_conditions_inline(rule_type, conditions, features):
            continue

        entry_price = yes_price if side == "YES" else no_price
        won = resolution == side
        pnl = (1.0 - entry_price) if won else -entry_price
        category = (m["market_category"] or "other").strip() or "other"

        trades.append({
            "side": side,
            "entry_price": entry_price,
            "won": won,
            "pnl": pnl,
            "category": category,
        })

    if not trades:
        return _empty_worker_result(rule)

    wins_d = sum(1 for t in trades if t["won"])
    losses_d = len(trades) - wins_d
    pnl_d = sum(t["pnl"] for t in trades)

    wins_i = losses_i = 0
    pnl_i = 0.0
    for t in trades:
        inv_entry = 1.0 - t["entry_price"]
        inv_won = not t["won"]
        inv_pnl = (1.0 - inv_entry) if inv_won else -inv_entry
        if inv_won:
            wins_i += 1
        else:
            losses_i += 1
        pnl_i += inv_pnl

    total = len(trades)
    recommended = "inverse" if pnl_i > pnl_d and total >= 30 else "direct"
    wr_d = wins_d / total if total > 0 else 0.5

    cat_trades: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        cat_trades[t["category"]].append(t)

    cat_stats = {}
    for cat, ct in cat_trades.items():
        cw = sum(1 for t in ct if t["won"])
        cp = sum(t["pnl"] for t in ct)
        ciw = cil = 0
        cip = 0.0
        for t in ct:
            ie = 1.0 - t["entry_price"]
            iw = not t["won"]
            ip = (1.0 - ie) if iw else -ie
            if iw:
                ciw += 1
            else:
                cil += 1
            cip += ip
        cr = "inverse" if cip > cp and len(ct) >= 10 else "direct"
        cat_stats[cat] = {
            "sample_size": len(ct),
            "wins_direct": cw,
            "pnl_direct": round(cp, 6),
            "wins_inverse": ciw,
            "pnl_inverse": round(cip, 6),
            "recommended_side": cr,
        }

    return {
        "rule_id": rule["id"],
        "total_matches": total,
        "wins_direct": wins_d,
        "losses_direct": losses_d,
        "pnl_direct": round(pnl_d, 6),
        "wins_inverse": wins_i,
        "losses_inverse": losses_i,
        "pnl_inverse": round(pnl_i, 6),
        "recommended_side": recommended,
        "edge_magnitude": round(abs(wr_d - 0.5), 6),
        "category_stats": cat_stats,
    }


# ---------------------------------------------------------------------------
# Ngram backtest
# ---------------------------------------------------------------------------

async def backtest_ngram_rules(batch_size: int = 500, n_workers: int = 0) -> dict:
    """Backtest ALL ngram rules against all historical resolved markets.

    Loads every resolved market and every ngram rule (regardless of win_rate
    or tier), then evaluates each rule against all markets using parallel
    workers. Stores results in backtest_results and rule_category_performance.

    Args:
        batch_size: number of rules to process before logging progress
        n_workers: number of parallel worker processes (0 = all CPUs)

    Returns:
        Summary dict with totals
    """
    t0 = time.monotonic()

    # ---- Load data --------------------------------------------------------
    async with SessionLocal() as session:
        markets = (await session.execute(
            select(Market).where(
                Market.resolution.in_(["YES", "NO"]),
            )
        )).scalars().all()

        rules = (await session.execute(
            select(TradingRule).where(
                TradingRule.rule_type == "ngram",
            )
        )).scalars().all()

    log.info(
        "Ngram backtest: loaded %d resolved markets and %d ngram rules",
        len(markets), len(rules),
    )

    if not markets or not rules:
        log.warning("Nothing to backtest (markets=%d, rules=%d)", len(markets), len(rules))
        return {"rules": 0, "markets": 0, "total_trades": 0}

    # ---- Parse rules into dicts -------------------------------------------
    parsed_rules: list[dict] = []
    for r in rules:
        try:
            cond = (
                json.loads(r.conditions_json)
                if isinstance(r.conditions_json, str)
                else (r.conditions_json or {})
            )
        except (json.JSONDecodeError, TypeError):
            continue
        phrase = str(cond.get("ngram", "")).strip().lower()
        if not phrase:
            continue
        parsed_rules.append({
            "id": r.id,
            "phrase": phrase,
            "side": r.predicted_side,
            "win_rate": r.win_rate,
            "breakeven": r.breakeven_price or r.win_rate,
        })

    log.info("Parsed %d ngram rules (skipped %d unparseable)", len(parsed_rules), len(rules) - len(parsed_rules))

    # ---- Serialize markets for multiprocessing ----------------------------
    n_markets = len(markets)
    market_dicts = [
        {
            "question": m.question,
            "yes_price": float(m.yes_price or 0),
            "no_price": float(m.no_price) if m.no_price is not None else max(0.001, 1.0 - float(m.yes_price or 0)),
            "resolution": m.resolution,
            "market_category": m.market_category,
        }
        for m in markets
    ]
    del markets  # free ORM objects

    # ---- Process rules in parallel ----------------------------------------
    total_trades = 0
    rules_processed = 0
    now = _utcnow_naive()
    n_workers = n_workers or os.cpu_count() or 4
    log.info("Using %d parallel workers for backtest", n_workers)

    for batch_start in range(0, len(parsed_rules), batch_size):
        batch = parsed_rules[batch_start : batch_start + batch_size]

        # Fan out rules to worker processes (market data sent once via initializer)
        with ProcessPoolExecutor(
            max_workers=n_workers,
            initializer=_init_worker,
            initargs=(market_dicts,),
        ) as pool:
            worker_results = list(pool.map(_backtest_one_rule, batch))

        # Convert worker results to ORM objects and persist
        batch_db: list[tuple[int, BacktestResult, list[RuleCategoryPerformance]]] = []
        for wr in worker_results:
            bt = BacktestResult(
                rule_id=wr["rule_id"],
                total_matches=wr["total_matches"],
                wins_direct=wr["wins_direct"],
                losses_direct=wr["losses_direct"],
                pnl_direct=wr["pnl_direct"],
                wins_inverse=wr["wins_inverse"],
                losses_inverse=wr["losses_inverse"],
                pnl_inverse=wr["pnl_inverse"],
                recommended_side=wr["recommended_side"],
                edge_magnitude=wr["edge_magnitude"],
                run_date=now,
            )
            cat_rows = []
            for cat, cs in wr["category_stats"].items():
                cat_rows.append(RuleCategoryPerformance(
                    rule_id=wr["rule_id"],
                    category=cat,
                    sample_size=cs["sample_size"],
                    wins_direct=cs["wins_direct"],
                    pnl_direct=cs["pnl_direct"],
                    wins_inverse=cs["wins_inverse"],
                    pnl_inverse=cs["pnl_inverse"],
                    recommended_side=cs["recommended_side"],
                    last_updated=now,
                ))
            batch_db.append((wr["rule_id"], bt, cat_rows))
            total_trades += wr["total_matches"]

        # ---- Persist batch to DB ------------------------------------------
        async with SessionLocal() as session:
            for rule_id, bt, cat_rows in batch_db:
                await session.execute(
                    delete(BacktestResult).where(BacktestResult.rule_id == rule_id)
                )
                await session.execute(
                    delete(RuleCategoryPerformance).where(RuleCategoryPerformance.rule_id == rule_id)
                )
                session.add(bt)
                for cr in cat_rows:
                    session.add(cr)
            await session.commit()

        rules_processed += len(batch)
        elapsed = time.monotonic() - t0
        log.info(
            "Ngram backtest progress: %d/%d rules (%d trades so far, %.1fs elapsed)",
            rules_processed, len(parsed_rules), total_trades, elapsed,
        )

    elapsed = time.monotonic() - t0
    summary = {
        "rule_type": "ngram",
        "rules": len(parsed_rules),
        "markets": n_markets,
        "total_trades": total_trades,
        "elapsed_seconds": round(elapsed, 1),
    }
    log.info("Ngram backtest complete: %s", summary)
    return summary


# ---------------------------------------------------------------------------
# Threshold / two-feature backtest
# ---------------------------------------------------------------------------

async def backtest_threshold_rules(batch_size: int = 1000, n_workers: int = 0) -> dict:
    """Backtest feature-based rules against
    all historical resolved markets.

    These rules require daily features to evaluate their conditions. We load
    the DailyFeature table and build a features-by-date lookup, then for each
    market use the features from market.end_date (falling back to first_seen).
    Rule evaluation is parallelized with ProcessPoolExecutor.

    Args:
        batch_size: number of rules to process before logging progress
        n_workers: number of parallel worker processes (0 = all CPUs)

    Returns:
        Summary dict with totals
    """
    t0 = time.monotonic()

    # ---- Load data --------------------------------------------------------
    async with SessionLocal() as session:
        markets = (await session.execute(
            select(Market).where(
                Market.resolution.in_(["YES", "NO"]),
            )
        )).scalars().all()

        rules = (await session.execute(
            select(TradingRule).where(
                TradingRule.rule_type.in_([
                    "single_threshold",
                    "two_feature",
                    "combined",
                    "decision_tree",
                    "logistic_regression",
                ]),
            )
        )).scalars().all()

        features_raw = (await session.execute(
            select(DailyFeature)
        )).scalars().all()

        # Load research factors (Grok/Perplexity) per market
        from sqlalchemy import text
        factor_rows = (await session.execute(text(
            "SELECT market_id, category, name, value, confidence, source "
            "FROM factors WHERE market_id IS NOT NULL"
        ))).all()

    log.info(
        "Threshold backtest: loaded %d resolved markets, %d rules, %d feature rows, %d research factors",
        len(markets), len(rules), len(features_raw), len(factor_rows),
    )

    if not markets or not rules:
        log.warning("Nothing to backtest (markets=%d, rules=%d)", len(markets), len(rules))
        return {"rules": 0, "markets": 0, "total_trades": 0}

    # ---- Build research features per market ------------------------------
    from polyedge.research.factor_features import extract_market_features
    from polyedge.analysis.question_parser import parse_question_features

    research_by_market: dict[str, list[dict]] = defaultdict(list)
    for row in factor_rows:
        research_by_market[row[0]].append({
            "category": row[1], "name": row[2], "value": row[3],
            "confidence": row[4], "source": row[5],
        })
    del factor_rows

    # ---- Serialize markets for multiprocessing ----------------------------
    n_markets = len(markets)
    market_dicts = []
    for m in markets:
        date_key = None
        end_date_obj = None
        if m.end_date:
            date_key = m.end_date.date().isoformat() if hasattr(m.end_date, "date") else str(m.end_date)
            end_date_obj = m.end_date.date() if hasattr(m.end_date, "date") else None
        elif m.first_seen:
            date_key = m.first_seen.date().isoformat() if hasattr(m.first_seen, "date") else str(m.first_seen)
            end_date_obj = m.first_seen.date() if hasattr(m.first_seen, "date") else None

        yes_price = float(m.yes_price or 0)
        no_price = float(m.no_price) if m.no_price is not None else max(0.001, 1.0 - yes_price)

        # Build per-market features (research + question)
        market_feats: dict[str, float] = {}

        # Research factors
        factors = research_by_market.get(m.id, [])
        if factors:
            for fname, fval in extract_market_features(factors):
                market_feats[fname] = fval

        # Question features
        q_feats = parse_question_features(
            m.question or "",
            category=m.market_category or "",
            end_date=end_date_obj,
        )
        market_feats.update(q_feats)

        # Include question text for on-the-fly ngram matching
        market_feats["_question_lower"] = (m.question or "").lower()

        market_dicts.append({
            "yes_price": yes_price,
            "no_price": no_price,
            "resolution": m.resolution,
            "market_category": m.market_category,
            "date_key": date_key,
            "market_features": market_feats,
        })
    del markets
    del research_by_market

    # ---- Build features-by-date lookup ------------------------------------
    features_by_date: dict[str, dict[str, float]] = defaultdict(dict)
    for f in features_raw:
        date_key = f.date.isoformat() if f.date else ""
        if date_key:
            features_by_date[date_key][f.name] = f.value

    log.info("Built feature lookup for %d dates", len(features_by_date))

    # ---- Pre-process rules into dicts for check_rule_conditions -----------
    parsed_rules: list[dict] = []
    for r in rules:
        parsed_rules.append({
            "id": r.id,
            "name": r.name,
            "rule_type": r.rule_type,
            "conditions_json": r.conditions_json,
            "predicted_side": r.predicted_side,
            "win_rate": r.win_rate,
            "breakeven_price": r.breakeven_price or r.win_rate,
            "sample_size": r.sample_size,
            "active": r.active,
        })

    # ---- Process rules in batches -----------------------------------------
    total_trades = 0
    rules_processed = 0
    now = _utcnow_naive()
    n_workers = n_workers or os.cpu_count() or 4
    log.info("Using %d parallel workers for threshold backtest", n_workers)

    with ProcessPoolExecutor(
        max_workers=n_workers,
        initializer=_init_worker,
        initargs=(market_dicts, dict(features_by_date)),
    ) as pool:
        for batch_start in range(0, len(parsed_rules), batch_size):
            batch = parsed_rules[batch_start : batch_start + batch_size]
            worker_results = list(pool.map(_backtest_one_threshold_rule, batch))

            batch_results: list[tuple[int, BacktestResult, list[RuleCategoryPerformance]]] = []
            rule_ids = []

            for wr in worker_results:
                bt = BacktestResult(
                    rule_id=wr["rule_id"],
                    total_matches=wr["total_matches"],
                    wins_direct=wr["wins_direct"],
                    losses_direct=wr["losses_direct"],
                    pnl_direct=wr["pnl_direct"],
                    wins_inverse=wr["wins_inverse"],
                    losses_inverse=wr["losses_inverse"],
                    pnl_inverse=wr["pnl_inverse"],
                    recommended_side=wr["recommended_side"],
                    edge_magnitude=wr["edge_magnitude"],
                    run_date=now,
                )

                cat_rows = []
                for cat, cs in wr["category_stats"].items():
                    cat_rows.append(RuleCategoryPerformance(
                        rule_id=wr["rule_id"],
                        category=cat,
                        sample_size=cs["sample_size"],
                        wins_direct=cs["wins_direct"],
                        pnl_direct=cs["pnl_direct"],
                        wins_inverse=cs["wins_inverse"],
                        pnl_inverse=cs["pnl_inverse"],
                        recommended_side=cs["recommended_side"],
                        last_updated=now,
                    ))

                batch_results.append((wr["rule_id"], bt, cat_rows))
                rule_ids.append(wr["rule_id"])
                total_trades += wr["total_matches"]

            # ---- Persist batch to DB --------------------------------------
            async with SessionLocal() as session:
                if rule_ids:
                    await session.execute(
                        delete(BacktestResult).where(BacktestResult.rule_id.in_(rule_ids))
                    )
                    await session.execute(
                        delete(RuleCategoryPerformance).where(RuleCategoryPerformance.rule_id.in_(rule_ids))
                    )

                for _, bt, cat_rows in batch_results:
                    session.add(bt)
                    for cr in cat_rows:
                        session.add(cr)

                await session.commit()

            rules_processed += len(batch)
            elapsed = time.monotonic() - t0
            log.info(
                "Threshold backtest progress: %d/%d rules (%d trades so far, %.1fs elapsed)",
                rules_processed, len(parsed_rules), total_trades, elapsed,
            )

    elapsed = time.monotonic() - t0
    summary = {
        "rule_type": "threshold",
        "rules": len(parsed_rules),
        "markets": n_markets,
        "total_trades": total_trades,
        "elapsed_seconds": round(elapsed, 1),
    }
    log.info("Threshold backtest complete: %s", summary)
    return summary


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

async def _main():
    parser = argparse.ArgumentParser(description="Batch backtest runner for trading rules")
    parser.add_argument(
        "--rule-type",
        choices=["ngram", "threshold", "all"],
        default="all",
        help="Which rule types to backtest (default: all)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Number of rules per batch (default: 500)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=0,
        help="Number of parallel workers (default: 0 = all CPUs)",
    )
    args = parser.parse_args()

    results = {}

    if args.rule_type in ("ngram", "all"):
        results["ngram"] = await backtest_ngram_rules(batch_size=args.batch_size, n_workers=args.workers)

    if args.rule_type in ("threshold", "all"):
        results["threshold"] = await backtest_threshold_rules(
            batch_size=args.batch_size,
            n_workers=args.workers,
        )

    log.info("Backtest runner finished: %s", results)
    return results


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(_main())
