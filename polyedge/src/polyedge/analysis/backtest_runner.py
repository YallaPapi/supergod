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


def _init_worker(market_dicts: list[dict]) -> None:
    """Initialize worker process with shared market data (called once per worker)."""
    global _SHARED_MARKETS
    _SHARED_MARKETS = market_dicts


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
        return {
            "rule_id": rule["id"],
            "total_matches": 0,
            "wins_direct": 0, "losses_direct": 0, "pnl_direct": 0.0,
            "wins_inverse": 0, "losses_inverse": 0, "pnl_inverse": 0.0,
            "recommended_side": "direct",
            "edge_magnitude": abs(rule["win_rate"] - 0.5),
            "category_stats": {},
        }

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

async def backtest_threshold_rules(batch_size: int = 1000) -> dict:
    """Backtest threshold-based rules (single_threshold, two_feature) against
    all historical resolved markets.

    These rules require daily features to evaluate their conditions. We load
    the DailyFeature table and build a features-by-date lookup, then for each
    market use the features from market.end_date (falling back to first_seen).

    Args:
        batch_size: number of rules to process before logging progress

    Returns:
        Summary dict with totals
    """
    from polyedge.analysis.predictor import check_rule_conditions

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
                TradingRule.rule_type.in_(["single_threshold", "two_feature"]),
            )
        )).scalars().all()

        features_raw = (await session.execute(
            select(DailyFeature)
        )).scalars().all()

    log.info(
        "Threshold backtest: loaded %d resolved markets, %d rules, %d feature rows",
        len(markets), len(rules), len(features_raw),
    )

    if not markets or not rules:
        log.warning("Nothing to backtest (markets=%d, rules=%d)", len(markets), len(rules))
        return {"rules": 0, "markets": 0, "total_trades": 0}

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

    for batch_start in range(0, len(parsed_rules), batch_size):
        batch = parsed_rules[batch_start : batch_start + batch_size]
        batch_results: list[tuple[int, BacktestResult, list[RuleCategoryPerformance]]] = []

        for rule in batch:
            trades: list[dict] = []

            for market in markets:
                resolution = (market.resolution or "").upper().strip()
                if resolution not in ("YES", "NO"):
                    continue

                yes_price = float(market.yes_price or 0)
                no_price = float(market.no_price) if market.no_price is not None else max(0.001, 1.0 - yes_price)

                if yes_price <= 0.02 or yes_price >= 0.98:
                    continue

                # Get features for this market's date
                # Prefer end_date, fall back to first_seen
                date_key = None
                if market.end_date:
                    date_key = market.end_date.date().isoformat() if hasattr(market.end_date, 'date') else str(market.end_date)
                elif market.first_seen:
                    date_key = market.first_seen.date().isoformat() if hasattr(market.first_seen, 'date') else str(market.first_seen)

                if not date_key:
                    continue

                features = features_by_date.get(date_key, {})
                if not features:
                    continue

                # Check rule conditions
                if not check_rule_conditions(rule, features):
                    continue

                side = rule["predicted_side"]
                entry_price = yes_price if side == "YES" else no_price
                won = resolution == side
                pnl = calc_pnl(entry_price, won)
                category = (market.market_category or "other").strip() or "other"

                trades.append({
                    "side": side,
                    "entry_price": entry_price,
                    "won": won,
                    "pnl": pnl,
                    "category": category,
                })

            if not trades:
                bt = BacktestResult(
                    rule_id=rule["id"],
                    total_matches=0,
                    wins_direct=0, losses_direct=0, pnl_direct=0.0,
                    wins_inverse=0, losses_inverse=0, pnl_inverse=0.0,
                    recommended_side="direct",
                    edge_magnitude=abs(rule["win_rate"] - 0.5),
                    run_date=now,
                )
                batch_results.append((rule["id"], bt, []))
                continue

            # Direct stats
            wins_direct = sum(1 for t in trades if t["won"])
            losses_direct = len(trades) - wins_direct
            pnl_direct = sum(t["pnl"] for t in trades)

            # Inverse stats
            inv = compute_inverse_stats(trades)

            total_matches = len(trades)
            if inv["pnl_inverse"] > pnl_direct and total_matches >= 30:
                recommended_side = "inverse"
            else:
                recommended_side = "direct"

            win_rate_direct = wins_direct / total_matches if total_matches > 0 else 0.5
            edge_magnitude = abs(win_rate_direct - 0.5)

            bt = BacktestResult(
                rule_id=rule["id"],
                total_matches=total_matches,
                wins_direct=wins_direct,
                losses_direct=losses_direct,
                pnl_direct=round(pnl_direct, 6),
                wins_inverse=inv["wins_inverse"],
                losses_inverse=inv["losses_inverse"],
                pnl_inverse=round(inv["pnl_inverse"], 6),
                recommended_side=recommended_side,
                edge_magnitude=round(edge_magnitude, 6),
                run_date=now,
            )

            # Per-category breakdown
            cat_trades: dict[str, list[dict]] = defaultdict(list)
            for t in trades:
                cat_trades[t["category"]].append(t)

            cat_rows: list[RuleCategoryPerformance] = []
            for cat, cat_t in cat_trades.items():
                cat_wins = sum(1 for t in cat_t if t["won"])
                cat_pnl = sum(t["pnl"] for t in cat_t)
                cat_inv = compute_inverse_stats(cat_t)

                cat_recommended = "direct"
                if cat_inv["pnl_inverse"] > cat_pnl and len(cat_t) >= 10:
                    cat_recommended = "inverse"

                cat_rows.append(RuleCategoryPerformance(
                    rule_id=rule["id"],
                    category=cat,
                    sample_size=len(cat_t),
                    wins_direct=cat_wins,
                    pnl_direct=round(cat_pnl, 6),
                    wins_inverse=cat_inv["wins_inverse"],
                    pnl_inverse=round(cat_inv["pnl_inverse"], 6),
                    recommended_side=cat_recommended,
                    last_updated=now,
                ))

            batch_results.append((rule["id"], bt, cat_rows))
            total_trades += total_matches

        # ---- Persist batch to DB ------------------------------------------
        async with SessionLocal() as session:
            for rule_id, bt, cat_rows in batch_results:
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
            "Threshold backtest progress: %d/%d rules (%d trades so far, %.1fs elapsed)",
            rules_processed, len(parsed_rules), total_trades, elapsed,
        )

    elapsed = time.monotonic() - t0
    summary = {
        "rule_type": "threshold",
        "rules": len(parsed_rules),
        "markets": len(markets),
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
        results["threshold"] = await backtest_threshold_rules(batch_size=args.batch_size)

    log.info("Backtest runner finished: %s", results)
    return results


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(_main())
