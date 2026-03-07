# Adaptive Trading System — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Backtest all 1.58M rules against 508k resolved markets, discover rule combinations, detect when inverses outperform, track per-category per-rule performance, and show everything on a tabbed dashboard — without ever deactivating or hiding any rule.

**Architecture:** A batch backtest runner on the 256GB compute server processes rules against historical markets, stores results in 3 new DB tables, and a new scheduler loop keeps them updated. The dashboard gets a tab system (Overview, Backtest, Categories, Combinations, Inverse Analysis) so the growing data stays readable. A research-to-rules pipeline wires existing Grok/Perplexity factors into rule generation.

**Tech Stack:** Python 3.12, SQLAlchemy async (PostgreSQL), FastAPI, vanilla JS dashboard. All heavy compute runs on 88.99.142.89. DB on 89.167.99.187.

**Key Design Principle:** NEVER deactivate rules. NEVER hide data. Track everything always. PnL is THE metric.

---

## Task 1: Database Schema — New Tables

**Files:**
- Modify: `polyedge/src/polyedge/models.py` (append after `ServiceHeartbeat` class, ~line 168)

**Step 1: Add BacktestResult model**

Add to `models.py` after `ServiceHeartbeat`:

```python
class BacktestResult(Base):
    """Backtest performance of a single rule against historical markets."""
    __tablename__ = "backtest_results"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_id: Mapped[int] = mapped_column(Integer, ForeignKey("trading_rules.id"), index=True)
    total_matches: Mapped[int] = mapped_column(Integer, default=0)
    wins_direct: Mapped[int] = mapped_column(Integer, default=0)
    losses_direct: Mapped[int] = mapped_column(Integer, default=0)
    pnl_direct: Mapped[float] = mapped_column(Float, default=0.0)
    wins_inverse: Mapped[int] = mapped_column(Integer, default=0)
    losses_inverse: Mapped[int] = mapped_column(Integer, default=0)
    pnl_inverse: Mapped[float] = mapped_column(Float, default=0.0)
    recommended_side: Mapped[str] = mapped_column(String(10), default="direct")
    edge_magnitude: Mapped[float] = mapped_column(Float, default=0.0)
    run_date: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    __table_args__ = (
        Index("ix_bt_rule_date", "rule_id", "run_date"),
    )


class RuleCategoryPerformance(Base):
    """Per-category backtest performance for a rule."""
    __tablename__ = "rule_category_performance"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_id: Mapped[int] = mapped_column(Integer, ForeignKey("trading_rules.id"), index=True)
    category: Mapped[str] = mapped_column(String(50), index=True)
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    wins_direct: Mapped[int] = mapped_column(Integer, default=0)
    pnl_direct: Mapped[float] = mapped_column(Float, default=0.0)
    wins_inverse: Mapped[int] = mapped_column(Integer, default=0)
    pnl_inverse: Mapped[float] = mapped_column(Float, default=0.0)
    recommended_side: Mapped[str] = mapped_column(String(10), default="direct")
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    __table_args__ = (
        Index("ix_rcp_rule_cat", "rule_id", "category", unique=True),
    )


class AgreementSignal(Base):
    """Performance stats for N-rule agreement tiers."""
    __tablename__ = "agreement_signals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agreement_tier: Mapped[int] = mapped_column(Integer, index=True)
    category: Mapped[str] = mapped_column(String(50), default="all")
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    pnl: Mapped[float] = mapped_column(Float, default=0.0)
    avg_pnl_per_trade: Mapped[float] = mapped_column(Float, default=0.0)
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    __table_args__ = (
        Index("ix_ag_tier_cat", "agreement_tier", "category", unique=True),
    )
```

**Step 2: Write the migration SQL**

Create `polyedge/deploy/migrations/004_backtest_tables.sql`:

```sql
-- Backtest results per rule
CREATE TABLE IF NOT EXISTS backtest_results (
    id SERIAL PRIMARY KEY,
    rule_id INTEGER REFERENCES trading_rules(id),
    total_matches INTEGER DEFAULT 0,
    wins_direct INTEGER DEFAULT 0,
    losses_direct INTEGER DEFAULT 0,
    pnl_direct FLOAT DEFAULT 0.0,
    wins_inverse INTEGER DEFAULT 0,
    losses_inverse INTEGER DEFAULT 0,
    pnl_inverse FLOAT DEFAULT 0.0,
    recommended_side VARCHAR(10) DEFAULT 'direct',
    edge_magnitude FLOAT DEFAULT 0.0,
    run_date TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_bt_rule_date ON backtest_results(rule_id, run_date);

-- Per-category rule performance
CREATE TABLE IF NOT EXISTS rule_category_performance (
    id SERIAL PRIMARY KEY,
    rule_id INTEGER REFERENCES trading_rules(id),
    category VARCHAR(50),
    sample_size INTEGER DEFAULT 0,
    wins_direct INTEGER DEFAULT 0,
    pnl_direct FLOAT DEFAULT 0.0,
    wins_inverse INTEGER DEFAULT 0,
    pnl_inverse FLOAT DEFAULT 0.0,
    recommended_side VARCHAR(10) DEFAULT 'direct',
    last_updated TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_rcp_rule_cat ON rule_category_performance(rule_id, category);
CREATE UNIQUE INDEX IF NOT EXISTS ix_rcp_rule_cat_uniq ON rule_category_performance(rule_id, category);

-- Agreement tier signals
CREATE TABLE IF NOT EXISTS agreement_signals (
    id SERIAL PRIMARY KEY,
    agreement_tier INTEGER,
    category VARCHAR(50) DEFAULT 'all',
    sample_size INTEGER DEFAULT 0,
    wins INTEGER DEFAULT 0,
    pnl FLOAT DEFAULT 0.0,
    avg_pnl_per_trade FLOAT DEFAULT 0.0,
    last_updated TIMESTAMP DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_ag_tier_cat ON agreement_signals(agreement_tier, category);
```

**Step 3: Run migration on DB server**

```bash
ssh root@89.167.99.187 "psql -U polyedge -d polyedge -f -" < polyedge/deploy/migrations/004_backtest_tables.sql
```

**Step 4: Commit**

```bash
git add polyedge/src/polyedge/models.py polyedge/deploy/migrations/004_backtest_tables.sql
git commit -m "feat: add backtest_results, rule_category_performance, agreement_signals tables"
```

---

## Task 2: Backtest Runner — Ngram Rules

This is the highest priority. The existing `backtester.py` has helper classes but no async DB integration and no inverse tracking. We write a new `run_backtest_batch.py` script that runs on the 256GB server.

**Files:**
- Create: `polyedge/src/polyedge/analysis/backtest_runner.py`
- Test: `polyedge/tests/test_backtest_runner.py`

**Step 1: Write failing tests**

Create `polyedge/tests/test_backtest_runner.py`:

```python
"""Tests for the backtest runner."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace
from datetime import datetime

from polyedge.analysis.backtest_runner import (
    match_ngram_rule,
    backtest_ngram_rules,
    compute_inverse_stats,
)


def test_match_ngram_rule_positive():
    rule = {"phrase": "basketball", "side": "YES", "win_rate": 0.75, "breakeven": 0.75}
    market = SimpleNamespace(
        question="Will the basketball game end over 200?",
        yes_price=0.40, no_price=0.60,
        resolution="YES", market_category="sports_ou",
    )
    result = match_ngram_rule(rule, market)
    assert result is not None
    assert result["side"] == "YES"
    assert result["entry_price"] == 0.40
    assert result["won"] is True


def test_match_ngram_rule_no_match():
    rule = {"phrase": "football", "side": "YES", "win_rate": 0.70, "breakeven": 0.70}
    market = SimpleNamespace(
        question="Will basketball team win?",
        yes_price=0.50, no_price=0.50,
        resolution="YES", market_category="sports_winner",
    )
    result = match_ngram_rule(rule, market)
    assert result is None


def test_compute_inverse_stats():
    trades = [
        {"won": True, "entry_price": 0.40},   # direct won: pnl = +0.60
        {"won": False, "entry_price": 0.30},   # direct lost: pnl = -0.30
        {"won": True, "entry_price": 0.35},    # direct won: pnl = +0.65
    ]
    stats = compute_inverse_stats(trades)
    # Inverse: won=False means inverse won (1 trade), won=True means inverse lost (2 trades)
    assert stats["wins_inverse"] == 1
    assert stats["losses_inverse"] == 2
    # Inverse PnL: when direct loses at 0.30, inverse wins at (1-0.30)=0.70 entry -> pnl = 1-0.70 = 0.30
    # When direct wins at 0.40, inverse loses at 0.60 entry -> pnl = -0.60
    # When direct wins at 0.35, inverse loses at 0.65 entry -> pnl = -0.65
    # Total inverse PnL = 0.30 - 0.60 - 0.65 = -0.95
    assert round(stats["pnl_inverse"], 2) == -0.95
```

**Step 2: Run tests to verify they fail**

```bash
cd polyedge && python -m pytest tests/test_backtest_runner.py -v
```

Expected: FAIL with ImportError (module doesn't exist yet)

**Step 3: Implement backtest_runner.py**

Create `polyedge/src/polyedge/analysis/backtest_runner.py`:

```python
"""Batch backtest runner — evaluates rules against all resolved markets.

Designed to run on the 256GB compute server. Processes rules in batches,
tracks both direct and inverse performance, stores results per-rule and
per-category.

Usage:
    python -m polyedge.analysis.backtest_runner --rule-type ngram --batch-size 500
"""
import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from polyedge.trading.pnl import calc_pnl

log = logging.getLogger(__name__)


def match_ngram_rule(rule: dict, market) -> Optional[dict]:
    """Check if an ngram rule matches a market and return trade details.

    Args:
        rule: dict with phrase, side, win_rate, breakeven
        market: object with question, yes_price, no_price, resolution, market_category

    Returns:
        dict with side, entry_price, won, pnl, category or None if no match
    """
    question = (getattr(market, "question", "") or "").lower()
    phrase = rule["phrase"]
    if phrase not in question:
        return None

    resolution = (getattr(market, "resolution", "") or "").upper().strip()
    if resolution not in ("YES", "NO"):
        return None

    side = rule["side"]
    yes_price = float(getattr(market, "yes_price", 0.5) or 0.5)
    no_price = float(getattr(market, "no_price", 0.5) or 0.5)

    if yes_price <= 0.02 or yes_price >= 0.98:
        return None

    entry_price = yes_price if side == "YES" else no_price
    won = (resolution == side)
    pnl = calc_pnl(entry_price, won)
    category = getattr(market, "market_category", "other") or "other"

    return {
        "side": side,
        "entry_price": entry_price,
        "won": won,
        "pnl": pnl,
        "category": category,
    }


def compute_inverse_stats(trades: list[dict]) -> dict:
    """Given a list of direct trade results, compute inverse performance.

    For each direct trade:
    - If direct won at entry E, inverse lost at entry (1-E) -> pnl = -(1-E)
    - If direct lost at entry E, inverse won at entry (1-E) -> pnl = 1-(1-E) = E

    Wait, that's wrong. Let's think carefully:
    - Direct: bet side X at price E. Win = 1-E, Loss = -E.
    - Inverse: bet opposite side at price (1-E). Win = 1-(1-E)=E, Loss = -(1-E).
    - Inverse wins when direct loses (opposite resolution).
    """
    wins_inv = 0
    losses_inv = 0
    pnl_inv = 0.0

    for t in trades:
        inv_entry = 1.0 - t["entry_price"]
        inv_won = not t["won"]
        inv_pnl = calc_pnl(inv_entry, inv_won)
        if inv_won:
            wins_inv += 1
        else:
            losses_inv += 1
        pnl_inv += inv_pnl

    return {
        "wins_inverse": wins_inv,
        "losses_inverse": losses_inv,
        "pnl_inverse": pnl_inv,
    }


async def backtest_ngram_rules(batch_size: int = 500) -> dict:
    """Backtest all ngram rules against all resolved markets.

    Loads markets in memory (they have resolution), iterates rules in batches,
    stores results in backtest_results and rule_category_performance tables.

    Returns summary stats dict.
    """
    from polyedge.db import SessionLocal
    from polyedge.models import (
        Market, TradingRule, BacktestResult, RuleCategoryPerformance,
    )
    from sqlalchemy import select, delete
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from collections import defaultdict

    log.info("Loading resolved markets...")
    async with SessionLocal() as session:
        markets = (await session.execute(
            select(Market).where(
                Market.resolution.in_(["YES", "NO"]),
            )
        )).scalars().all()
    log.info("Loaded %d resolved markets", len(markets))

    # Load all ngram rules
    async with SessionLocal() as session:
        rules_raw = (await session.execute(
            select(TradingRule).where(
                TradingRule.rule_type == "ngram",
            )
        )).scalars().all()
    log.info("Loaded %d ngram rules", len(rules_raw))

    # Parse rules
    parsed_rules = []
    for r in rules_raw:
        try:
            cond = json.loads(r.conditions_json) if isinstance(r.conditions_json, str) else (r.conditions_json or {})
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
    log.info("Parsed %d ngram rules", len(parsed_rules))

    # Process in batches
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    total_trades = 0
    rules_processed = 0
    start = time.time()

    for batch_start in range(0, len(parsed_rules), batch_size):
        batch = parsed_rules[batch_start:batch_start + batch_size]
        bt_rows = []
        rcp_rows = []

        for rule in batch:
            trades = []
            cat_trades = defaultdict(list)

            for market in markets:
                result = match_ngram_rule(rule, market)
                if result is None:
                    continue
                trades.append(result)
                cat_trades[result["category"]].append(result)

            if not trades:
                continue

            # Compute direct stats
            wins_d = sum(1 for t in trades if t["won"])
            losses_d = len(trades) - wins_d
            pnl_d = sum(t["pnl"] for t in trades)

            # Compute inverse stats
            inv = compute_inverse_stats(trades)

            # Determine recommended side
            if inv["pnl_inverse"] > pnl_d and len(trades) >= 30:
                rec_side = "inverse"
            else:
                rec_side = "direct"

            best_pnl = max(pnl_d, inv["pnl_inverse"])
            wr_direct = wins_d / len(trades) if trades else 0.5
            edge_mag = abs(wr_direct - 0.5)

            bt_rows.append({
                "rule_id": rule["id"],
                "total_matches": len(trades),
                "wins_direct": wins_d,
                "losses_direct": losses_d,
                "pnl_direct": round(pnl_d, 4),
                "wins_inverse": inv["wins_inverse"],
                "losses_inverse": inv["losses_inverse"],
                "pnl_inverse": round(inv["pnl_inverse"], 4),
                "recommended_side": rec_side,
                "edge_magnitude": round(edge_mag, 4),
                "run_date": now,
            })

            # Per-category breakdown
            for cat, cat_t in cat_trades.items():
                cat_wins_d = sum(1 for t in cat_t if t["won"])
                cat_pnl_d = sum(t["pnl"] for t in cat_t)
                cat_inv = compute_inverse_stats(cat_t)
                cat_rec = "inverse" if cat_inv["pnl_inverse"] > cat_pnl_d and len(cat_t) >= 10 else "direct"

                rcp_rows.append({
                    "rule_id": rule["id"],
                    "category": cat,
                    "sample_size": len(cat_t),
                    "wins_direct": cat_wins_d,
                    "pnl_direct": round(cat_pnl_d, 4),
                    "wins_inverse": cat_inv["wins_inverse"],
                    "pnl_inverse": round(cat_inv["pnl_inverse"], 4),
                    "recommended_side": cat_rec,
                    "last_updated": now,
                })

            total_trades += len(trades)
            rules_processed += 1

        # Store batch
        if bt_rows or rcp_rows:
            async with SessionLocal() as session:
                if bt_rows:
                    # Delete old results for these rules, then insert new
                    rule_ids = [r["rule_id"] for r in bt_rows]
                    await session.execute(
                        delete(BacktestResult).where(
                            BacktestResult.rule_id.in_(rule_ids)
                        )
                    )
                    for row in bt_rows:
                        session.add(BacktestResult(**row))

                if rcp_rows:
                    rule_ids = list(set(r["rule_id"] for r in rcp_rows))
                    await session.execute(
                        delete(RuleCategoryPerformance).where(
                            RuleCategoryPerformance.rule_id.in_(rule_ids)
                        )
                    )
                    for row in rcp_rows:
                        session.add(RuleCategoryPerformance(**row))

                await session.commit()

        elapsed = time.time() - start
        log.info(
            "Backtest batch %d-%d: %d rules, %d trades total (%.1fs elapsed)",
            batch_start, batch_start + len(batch), len(batch), total_trades, elapsed,
        )

    elapsed = time.time() - start
    summary = {
        "rules_processed": rules_processed,
        "total_trades": total_trades,
        "elapsed_seconds": round(elapsed, 1),
    }
    log.info("Backtest complete: %s", summary)
    return summary


async def backtest_threshold_rules(batch_size: int = 1000) -> dict:
    """Backtest single_threshold and two_feature rules.

    These need the daily_features table to check conditions.
    More complex than ngram — requires feature lookup per market date.
    Runs after ngram backtest.
    """
    from polyedge.db import SessionLocal
    from polyedge.models import (
        Market, TradingRule, DailyFeature, BacktestResult, RuleCategoryPerformance,
    )
    from polyedge.analysis.predictor import check_rule_conditions
    from sqlalchemy import select, delete
    from collections import defaultdict

    log.info("Loading resolved markets for threshold backtest...")
    async with SessionLocal() as session:
        markets = (await session.execute(
            select(Market).where(
                Market.resolution.in_(["YES", "NO"]),
            )
        )).scalars().all()

    # Build feature lookup: date -> {feature_name: value}
    log.info("Loading daily features...")
    async with SessionLocal() as session:
        features_raw = (await session.execute(
            select(DailyFeature)
        )).scalars().all()

    features_by_date: dict[str, dict[str, float]] = defaultdict(dict)
    for f in features_raw:
        date_key = str(f.date)
        features_by_date[date_key][f.name] = f.value
    log.info("Loaded features for %d dates", len(features_by_date))

    # Load threshold rules
    async with SessionLocal() as session:
        rules_raw = (await session.execute(
            select(TradingRule).where(
                TradingRule.rule_type.in_(["single_threshold", "two_feature"]),
            )
        )).scalars().all()
    log.info("Loaded %d threshold rules", len(rules_raw))

    # Parse rules into dicts for check_rule_conditions
    parsed_rules = []
    for r in rules_raw:
        parsed_rules.append({
            "id": r.id,
            "conditions_json": r.conditions_json,
            "rule_type": r.rule_type,
            "predicted_side": r.predicted_side,
            "win_rate": r.win_rate,
            "breakeven_price": r.breakeven_price or r.win_rate,
            "active": True,
        })
    log.info("Parsed %d threshold rules", len(parsed_rules))

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    total_trades = 0
    rules_processed = 0
    start = time.time()

    for batch_start in range(0, len(parsed_rules), batch_size):
        batch = parsed_rules[batch_start:batch_start + batch_size]
        bt_rows = []
        rcp_rows = []

        for rule in batch:
            trades = []
            cat_trades = defaultdict(list)

            for market in markets:
                # Get features for the market's end_date (or first_seen date)
                market_date = None
                if market.end_date:
                    market_date = str(market.end_date.date())
                elif market.first_seen:
                    market_date = str(market.first_seen.date())
                if not market_date:
                    continue

                features = features_by_date.get(market_date, {})
                if not features:
                    continue

                if not check_rule_conditions(rule, features):
                    continue

                resolution = (market.resolution or "").upper().strip()
                if resolution not in ("YES", "NO"):
                    continue

                side = rule["predicted_side"]
                yes_price = float(market.yes_price or 0.5)
                no_price = float(market.no_price or 0.5)
                if yes_price <= 0.02 or yes_price >= 0.98:
                    continue

                entry_price = yes_price if side == "YES" else no_price
                won = (resolution == side)
                pnl = calc_pnl(entry_price, won)
                category = getattr(market, "market_category", "other") or "other"

                trades.append({
                    "side": side,
                    "entry_price": entry_price,
                    "won": won,
                    "pnl": pnl,
                    "category": category,
                })
                cat_trades[category].append(trades[-1])

            if not trades:
                continue

            wins_d = sum(1 for t in trades if t["won"])
            losses_d = len(trades) - wins_d
            pnl_d = sum(t["pnl"] for t in trades)
            inv = compute_inverse_stats(trades)
            rec_side = "inverse" if inv["pnl_inverse"] > pnl_d and len(trades) >= 30 else "direct"
            wr_direct = wins_d / len(trades) if trades else 0.5
            edge_mag = abs(wr_direct - 0.5)

            bt_rows.append({
                "rule_id": rule["id"],
                "total_matches": len(trades),
                "wins_direct": wins_d,
                "losses_direct": losses_d,
                "pnl_direct": round(pnl_d, 4),
                "wins_inverse": inv["wins_inverse"],
                "losses_inverse": inv["losses_inverse"],
                "pnl_inverse": round(inv["pnl_inverse"], 4),
                "recommended_side": rec_side,
                "edge_magnitude": round(edge_mag, 4),
                "run_date": now,
            })

            for cat, cat_t in cat_trades.items():
                cat_wins_d = sum(1 for t in cat_t if t["won"])
                cat_pnl_d = sum(t["pnl"] for t in cat_t)
                cat_inv = compute_inverse_stats(cat_t)
                cat_rec = "inverse" if cat_inv["pnl_inverse"] > cat_pnl_d and len(cat_t) >= 10 else "direct"
                rcp_rows.append({
                    "rule_id": rule["id"],
                    "category": cat,
                    "sample_size": len(cat_t),
                    "wins_direct": cat_wins_d,
                    "pnl_direct": round(cat_pnl_d, 4),
                    "wins_inverse": cat_inv["wins_inverse"],
                    "pnl_inverse": round(cat_inv["pnl_inverse"], 4),
                    "recommended_side": cat_rec,
                    "last_updated": now,
                })

            total_trades += len(trades)
            rules_processed += 1

        if bt_rows or rcp_rows:
            async with SessionLocal() as session:
                if bt_rows:
                    rule_ids = [r["rule_id"] for r in bt_rows]
                    await session.execute(
                        delete(BacktestResult).where(BacktestResult.rule_id.in_(rule_ids))
                    )
                    for row in bt_rows:
                        session.add(BacktestResult(**row))
                if rcp_rows:
                    rule_ids = list(set(r["rule_id"] for r in rcp_rows))
                    await session.execute(
                        delete(RuleCategoryPerformance).where(
                            RuleCategoryPerformance.rule_id.in_(rule_ids)
                        )
                    )
                    for row in rcp_rows:
                        session.add(RuleCategoryPerformance(**row))
                await session.commit()

        elapsed = time.time() - start
        log.info(
            "Threshold backtest batch %d-%d: %d rules, %d trades (%.1fs)",
            batch_start, batch_start + len(batch), len(batch), total_trades, elapsed,
        )

    elapsed = time.time() - start
    summary = {
        "rules_processed": rules_processed,
        "total_trades": total_trades,
        "elapsed_seconds": round(elapsed, 1),
    }
    log.info("Threshold backtest complete: %s", summary)
    return summary


if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Run backtest against historical markets")
    parser.add_argument("--rule-type", choices=["ngram", "threshold", "all"], default="ngram")
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()

    async def main():
        if args.rule_type in ("ngram", "all"):
            await backtest_ngram_rules(batch_size=args.batch_size)
        if args.rule_type in ("threshold", "all"):
            await backtest_threshold_rules(batch_size=args.batch_size)

    asyncio.run(main())
```

**Step 4: Run tests to verify they pass**

```bash
cd polyedge && python -m pytest tests/test_backtest_runner.py -v
```

Expected: 3 tests PASS

**Step 5: Commit**

```bash
git add polyedge/src/polyedge/analysis/backtest_runner.py polyedge/tests/test_backtest_runner.py
git commit -m "feat: add batch backtest runner for ngram and threshold rules"
```

---

## Task 3: Agreement Signal Calculator

After backtest populates results, this second pass calculates how performance changes when multiple rules agree on the same market.

**Files:**
- Create: `polyedge/src/polyedge/analysis/agreement_calculator.py`
- Test: `polyedge/tests/test_agreement_calculator.py`

**Step 1: Write failing test**

Create `polyedge/tests/test_agreement_calculator.py`:

```python
"""Tests for agreement signal calculation."""
import pytest
from polyedge.analysis.agreement_calculator import compute_agreement_tiers


def test_compute_agreement_tiers_basic():
    # 3 rules match market A, 1 rule matches market B
    market_rule_matches = {
        "market_a": [
            {"rule_id": 1, "side": "YES", "won": True, "pnl": 0.60, "category": "sports_winner"},
            {"rule_id": 2, "side": "YES", "won": True, "pnl": 0.55, "category": "sports_winner"},
            {"rule_id": 3, "side": "YES", "won": True, "pnl": 0.50, "category": "sports_winner"},
        ],
        "market_b": [
            {"rule_id": 4, "side": "NO", "won": False, "pnl": -0.40, "category": "crypto_other"},
        ],
    }
    tiers = compute_agreement_tiers(market_rule_matches)
    # Tier 1 (1+ rules agree): both markets, 2 markets total
    assert tiers[1]["all"]["sample_size"] == 2
    # Tier 3 (3+ rules agree): only market_a
    assert tiers[3]["all"]["sample_size"] == 1
    assert tiers[3]["all"]["wins"] == 1
```

**Step 2: Run test to verify it fails**

```bash
cd polyedge && python -m pytest tests/test_agreement_calculator.py -v
```

**Step 3: Implement agreement_calculator.py**

Create `polyedge/src/polyedge/analysis/agreement_calculator.py`:

```python
"""Calculate performance by rule-agreement tiers.

For each resolved market, count how many rules matched it and what side
they predicted. Track performance at different agreement levels:
- Tier 1: 1+ rules agree
- Tier 2: 2+ rules agree
- Tier 3: 3+ rules agree
- Tier 5: 5+ rules agree
- Tier 10: 10+ rules agree
"""
import asyncio
import json
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone

from polyedge.trading.pnl import calc_pnl

log = logging.getLogger(__name__)

AGREEMENT_TIERS = [1, 2, 3, 5, 10]


def compute_agreement_tiers(
    market_rule_matches: dict[str, list[dict]],
) -> dict[int, dict[str, dict]]:
    """Compute performance stats for each agreement tier.

    Args:
        market_rule_matches: {market_id: [list of match dicts]}
            Each match dict has: rule_id, side, won, pnl, category

    Returns:
        {tier: {category: {sample_size, wins, pnl, avg_pnl_per_trade}}}
        Special category "all" aggregates across all categories.
    """
    results: dict[int, dict[str, dict]] = {}
    for tier in AGREEMENT_TIERS:
        results[tier] = {}

    for market_id, matches in market_rule_matches.items():
        if not matches:
            continue

        # Group by predicted side — use majority side
        side_counts: dict[str, list[dict]] = defaultdict(list)
        for m in matches:
            side_counts[m["side"]].append(m)

        # Pick the majority side
        majority_side = max(side_counts, key=lambda s: len(side_counts[s]))
        agreement_count = len(side_counts[majority_side])
        majority_matches = side_counts[majority_side]

        # Use the first match's result (all matches for same market have same resolution)
        representative = majority_matches[0]
        won = representative["won"]
        pnl = representative["pnl"]
        category = representative.get("category", "other")

        for tier in AGREEMENT_TIERS:
            if agreement_count >= tier:
                for cat in [category, "all"]:
                    if cat not in results[tier]:
                        results[tier][cat] = {
                            "sample_size": 0, "wins": 0, "pnl": 0.0,
                            "avg_pnl_per_trade": 0.0,
                        }
                    results[tier][cat]["sample_size"] += 1
                    if won:
                        results[tier][cat]["wins"] += 1
                    results[tier][cat]["pnl"] += pnl

    # Compute averages
    for tier in results:
        for cat in results[tier]:
            s = results[tier][cat]
            s["avg_pnl_per_trade"] = round(
                s["pnl"] / s["sample_size"], 4
            ) if s["sample_size"] > 0 else 0.0
            s["pnl"] = round(s["pnl"], 4)

    return results


async def run_agreement_analysis() -> dict:
    """Run full agreement analysis across all ngram rules and resolved markets.

    1. Load all ngram rules
    2. For each resolved market, find all matching rules
    3. Compute agreement tiers
    4. Store in agreement_signals table
    """
    from polyedge.db import SessionLocal
    from polyedge.models import Market, TradingRule, AgreementSignal
    from sqlalchemy import select, delete

    log.info("Loading data for agreement analysis...")
    async with SessionLocal() as session:
        markets = (await session.execute(
            select(Market).where(Market.resolution.in_(["YES", "NO"]))
        )).scalars().all()

        rules_raw = (await session.execute(
            select(TradingRule).where(TradingRule.rule_type == "ngram")
        )).scalars().all()

    # Parse rules
    parsed_rules = []
    for r in rules_raw:
        try:
            cond = json.loads(r.conditions_json) if isinstance(r.conditions_json, str) else (r.conditions_json or {})
        except (json.JSONDecodeError, TypeError):
            continue
        phrase = str(cond.get("ngram", "")).strip().lower()
        if not phrase:
            continue
        parsed_rules.append({
            "id": r.id, "phrase": phrase, "side": r.predicted_side,
            "win_rate": r.win_rate,
        })

    log.info("Matching %d rules against %d markets...", len(parsed_rules), len(markets))
    start = time.time()

    # Build market_rule_matches
    market_rule_matches: dict[str, list[dict]] = defaultdict(list)

    for market in markets:
        q_lower = (market.question or "").lower()
        resolution = (market.resolution or "").upper().strip()
        yes_price = float(market.yes_price or 0.5)
        no_price = float(market.no_price or 0.5)
        category = getattr(market, "market_category", "other") or "other"

        if yes_price <= 0.02 or yes_price >= 0.98:
            continue

        for rule in parsed_rules:
            if rule["phrase"] not in q_lower:
                continue
            side = rule["side"]
            entry = yes_price if side == "YES" else no_price
            won = (resolution == side)
            pnl = calc_pnl(entry, won)

            market_rule_matches[market.id].append({
                "rule_id": rule["id"],
                "side": side,
                "won": won,
                "pnl": pnl,
                "category": category,
            })

    log.info("Found matches for %d markets (%.1fs)", len(market_rule_matches), time.time() - start)

    # Compute tiers
    tiers = compute_agreement_tiers(market_rule_matches)

    # Store results
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with SessionLocal() as session:
        await session.execute(delete(AgreementSignal))
        for tier, cats in tiers.items():
            for cat, stats in cats.items():
                session.add(AgreementSignal(
                    agreement_tier=tier,
                    category=cat,
                    sample_size=stats["sample_size"],
                    wins=stats["wins"],
                    pnl=stats["pnl"],
                    avg_pnl_per_trade=stats["avg_pnl_per_trade"],
                    last_updated=now,
                ))
        await session.commit()

    log.info("Agreement analysis complete. Tiers stored: %s",
             {t: len(c) for t, c in tiers.items()})
    return {"tiers": {t: tiers[t].get("all", {}) for t in AGREEMENT_TIERS}}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(run_agreement_analysis())
```

**Step 4: Run tests**

```bash
cd polyedge && python -m pytest tests/test_agreement_calculator.py -v
```

**Step 5: Commit**

```bash
git add polyedge/src/polyedge/analysis/agreement_calculator.py polyedge/tests/test_agreement_calculator.py
git commit -m "feat: add rule agreement tier calculator"
```

---

## Task 4: Backtest API Endpoints

Add new API endpoints to serve backtest data to the dashboard.

**Files:**
- Modify: `polyedge/src/polyedge/app.py` (add new endpoints after existing ones)

**Step 1: Add /api/backtest-summary endpoint**

Add after the last `@app.get(...)` endpoint in `app.py`:

```python
@app.get("/api/backtest-summary")
async def backtest_summary():
    """Backtest results: top rules, inverse flip candidates, agreement tiers, category matrix."""
    try:
        async with SessionLocal() as session:
            from polyedge.models import BacktestResult, RuleCategoryPerformance, AgreementSignal, TradingRule

            # Top rules by direct PnL
            top_direct = (await session.execute(
                select(BacktestResult, TradingRule.name, TradingRule.rule_type,
                       TradingRule.conditions_json, TradingRule.predicted_side)
                .join(TradingRule, TradingRule.id == BacktestResult.rule_id)
                .order_by(BacktestResult.pnl_direct.desc())
                .limit(50)
            )).all()

            # Top rules by inverse PnL (flip candidates)
            top_inverse = (await session.execute(
                select(BacktestResult, TradingRule.name, TradingRule.rule_type,
                       TradingRule.conditions_json, TradingRule.predicted_side)
                .join(TradingRule, TradingRule.id == BacktestResult.rule_id)
                .where(BacktestResult.recommended_side == "inverse")
                .order_by(BacktestResult.pnl_inverse.desc())
                .limit(50)
            )).all()

            # Agreement signals
            agreement_rows = (await session.execute(
                select(AgreementSignal).order_by(
                    AgreementSignal.agreement_tier,
                    AgreementSignal.pnl.desc(),
                )
            )).scalars().all()

            # Category matrix: top 20 rule × category combos by PnL
            cat_matrix = (await session.execute(
                select(RuleCategoryPerformance, TradingRule.name)
                .join(TradingRule, TradingRule.id == RuleCategoryPerformance.rule_id)
                .where(RuleCategoryPerformance.sample_size >= 10)
                .order_by(RuleCategoryPerformance.pnl_direct.desc())
                .limit(200)
            )).all()

            # Summary stats
            total_backtested = (await session.execute(
                select(func.count(BacktestResult.id))
            )).scalar() or 0
            total_flip_candidates = (await session.execute(
                select(func.count(BacktestResult.id)).where(
                    BacktestResult.recommended_side == "inverse"
                )
            )).scalar() or 0

            def _bt_row(bt, name, rule_type, cond_json, pred_side):
                return {
                    "rule_id": bt.rule_id,
                    "rule_name": name,
                    "rule_type": rule_type,
                    "predicted_side": pred_side,
                    "total_matches": bt.total_matches,
                    "wins_direct": bt.wins_direct,
                    "pnl_direct": round(bt.pnl_direct, 2),
                    "wins_inverse": bt.wins_inverse,
                    "pnl_inverse": round(bt.pnl_inverse, 2),
                    "recommended_side": bt.recommended_side,
                    "edge_magnitude": round(bt.edge_magnitude, 4),
                    "win_rate_direct": round(bt.wins_direct / bt.total_matches * 100, 1) if bt.total_matches else 0,
                    "win_rate_inverse": round(bt.wins_inverse / bt.total_matches * 100, 1) if bt.total_matches else 0,
                }

            return {
                "total_rules_backtested": total_backtested,
                "total_flip_candidates": total_flip_candidates,
                "top_rules_direct": [_bt_row(*r) for r in top_direct],
                "top_rules_inverse": [_bt_row(*r) for r in top_inverse],
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
                        "rule_id": rcp.rule_id,
                        "rule_name": name,
                        "category": rcp.category,
                        "sample_size": rcp.sample_size,
                        "pnl_direct": round(rcp.pnl_direct, 2),
                        "pnl_inverse": round(rcp.pnl_inverse, 2),
                        "recommended_side": rcp.recommended_side,
                    }
                    for rcp, name in cat_matrix
                ],
            }
    except Exception as e:
        log.exception("backtest_summary failed")
        return {"error": str(e)}
```

**Step 2: Test the endpoint manually**

After deployment:
```bash
curl http://89.167.99.187:8090/api/backtest-summary | python -m json.tool | head -30
```

**Step 3: Commit**

```bash
git add polyedge/src/polyedge/app.py
git commit -m "feat: add /api/backtest-summary endpoint"
```

---

## Task 5: Dashboard Overhaul — Tab System

Replace the single-scroll dashboard with a tabbed layout. 5 tabs: Overview, Backtest, Categories, Combinations, Inverse Analysis.

**Files:**
- Modify: `polyedge/src/polyedge/static/dashboard.html`

**Step 1: Add tab CSS and navigation**

Add after the existing `</style>` closing tag (line 101), insert tab styles. Then wrap existing content in a tab div and add new tab content.

The tab system approach:
- Add `<div class="tab-bar">` with 5 buttons after the header
- Wrap ALL existing dashboard content in `<div class="tab-content" id="tabOverview">`
- Add 4 new `<div class="tab-content" id="tabBacktest">` etc. divs
- Add tab switching JS at the top of the script block

**CSS to add** (inside `<style>` block, before closing `</style>`):

```css
/* Tab navigation */
.tab-bar { display: flex; gap: 4px; margin-bottom: 16px; border-bottom: 1px solid var(--border); padding-bottom: 8px; }
.tab-btn {
  background: transparent; border: none; color: var(--muted); padding: 8px 16px;
  font-size: 0.85rem; font-weight: 600; cursor: pointer; border-radius: 6px 6px 0 0;
  transition: background 0.2s, color 0.2s;
}
.tab-btn:hover { background: var(--bg-2); color: var(--fg); }
.tab-btn.active { background: var(--bg-2); color: var(--blue); border-bottom: 2px solid var(--blue); }
.tab-content { display: none; }
.tab-content.active { display: block; }
```

**HTML tab bar** (insert after the header div, before hero-row):

```html
<div class="tab-bar" id="tabBar">
  <button class="tab-btn active" onclick="switchTab('overview')">Overview</button>
  <button class="tab-btn" onclick="switchTab('backtest')">Backtest Results</button>
  <button class="tab-btn" onclick="switchTab('categories')">Categories</button>
  <button class="tab-btn" onclick="switchTab('combinations')">Combinations</button>
  <button class="tab-btn" onclick="switchTab('inverse')">Inverse Analysis</button>
</div>
```

**Tab content wrappers:**
- Wrap everything from `<div class="hero-row"...` through the COUNTS section in `<div class="tab-content active" id="tabOverview">`
- Add new tab divs after it for: tabBacktest, tabCategories, tabCombinations, tabInverse

**New tab content HTML** (after the Overview tab closing div):

```html
<!-- BACKTEST TAB -->
<div class="tab-content" id="tabBacktest">
  <div class="hero-row" style="grid-template-columns: 1fr 1fr 1fr">
    <div class="hero-card">
      <h3>Rules Backtested</h3>
      <div class="stat-big neutral" id="btRulesCount">--</div>
      <div class="stat-sub" id="btRulesDetail">Loading...</div>
    </div>
    <div class="hero-card">
      <h3>Flip Candidates</h3>
      <div class="stat-big neutral" id="btFlipCount">--</div>
      <div class="stat-sub">Rules where inverse outperforms direct</div>
    </div>
    <div class="hero-card">
      <h3>Best Rule PnL</h3>
      <div class="stat-big positive" id="btBestPnl">--</div>
      <div class="stat-sub" id="btBestRule">Loading...</div>
    </div>
  </div>
  <div class="section">
    <div class="panel">
      <div class="panel-title">Top Rules by Backtest PnL</div>
      <div class="panel-explain">Rules ranked by historical profit. These patterns made the most money when tested against all resolved markets.</div>
      <div style="overflow-x:auto; max-height:500px; overflow-y:auto;">
        <table id="btTopRulesTable">
          <thead><tr>
            <th>Rule</th><th>Matches</th><th>Win Rate</th><th>Direct PnL</th><th>Inverse PnL</th><th>Best Side</th>
          </tr></thead>
          <tbody><tr><td colspan="6" class="empty-msg">Run backtest first...</td></tr></tbody>
        </table>
      </div>
    </div>
  </div>
</div>

<!-- CATEGORIES TAB -->
<div class="tab-content" id="tabCategories">
  <div class="section">
    <div class="panel">
      <div class="panel-title">Performance by Category (Live Paper Trades)</div>
      <div class="panel-explain">How each market category performs across all strategies. Sorted by PnL.</div>
      <div id="catLiveTable" style="overflow-x:auto;"></div>
    </div>
  </div>
  <div class="section">
    <div class="panel">
      <div class="panel-title">Rule x Category Matrix (Backtest)</div>
      <div class="panel-explain">Which rules work best in which categories. Green = profitable, red = losing. Based on historical backtest.</div>
      <div id="catMatrixTable" style="overflow-x:auto; max-height:600px; overflow-y:auto;"></div>
    </div>
  </div>
</div>

<!-- COMBINATIONS TAB -->
<div class="tab-content" id="tabCombinations">
  <div class="section">
    <div class="panel">
      <div class="panel-title">Rule Agreement Tiers</div>
      <div class="panel-explain">When more rules agree on a market, is the prediction better? Tier N means N or more rules predicted the same side.</div>
      <div id="agreementTable" style="overflow-x:auto;"></div>
    </div>
  </div>
</div>

<!-- INVERSE TAB -->
<div class="tab-content" id="tabInverse">
  <div class="hero-row" style="grid-template-columns: 1fr 1fr">
    <div class="hero-card">
      <h3>Live: Direct vs Inverse</h3>
      <div id="invLiveComparison">Loading...</div>
    </div>
    <div class="hero-card">
      <h3>Backtest: Flip Candidates</h3>
      <div id="invFlipCandidates">Loading...</div>
    </div>
  </div>
  <div class="section">
    <div class="panel">
      <div class="panel-title">Rules That Should Be Flipped</div>
      <div class="panel-explain">These rules lose money when bet directly but make money when inverted. Based on backtest against all historical markets.</div>
      <div style="overflow-x:auto; max-height:500px; overflow-y:auto;">
        <table id="invFlipTable">
          <thead><tr>
            <th>Rule</th><th>Matches</th><th>Direct PnL</th><th>Inverse PnL</th><th>Difference</th>
          </tr></thead>
          <tbody><tr><td colspan="5" class="empty-msg">Run backtest first...</td></tr></tbody>
        </table>
      </div>
    </div>
  </div>
</div>
```

**JavaScript additions** (add at the top of the `<script>` block, inside the IIFE):

```javascript
// Tab switching
window.switchTab = function(tabName) {
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
  const tab = document.getElementById('tab' + tabName.charAt(0).toUpperCase() + tabName.slice(1));
  if (tab) tab.classList.add('active');
  const btn = document.querySelector(`.tab-btn[onclick*="${tabName}"]`);
  if (btn) btn.classList.add('active');
  // Load backtest data on first visit
  if (tabName !== 'overview' && !window._backtestLoaded) {
    loadBacktestData();
  }
};

async function loadBacktestData() {
  window._backtestLoaded = true;
  const bt = await fetchJSON('/api/backtest-summary');
  if (!bt || bt.error) return;

  // Backtest tab
  $('btRulesCount').textContent = num(bt.total_rules_backtested);
  $('btFlipCount').textContent = num(bt.total_flip_candidates);
  if (bt.top_rules_direct && bt.top_rules_direct.length > 0) {
    const best = bt.top_rules_direct[0];
    $('btBestPnl').textContent = '$' + Number(best.pnl_direct).toFixed(2);
    $('btBestRule').textContent = best.rule_name;
  }

  // Top rules table
  const topBody = $('btTopRulesTable').querySelector('tbody');
  if (bt.top_rules_direct && bt.top_rules_direct.length > 0) {
    topBody.innerHTML = bt.top_rules_direct.map(r => {
      const dPnl = Number(r.pnl_direct || 0);
      const iPnl = Number(r.pnl_inverse || 0);
      const dColor = dPnl >= 0 ? 'var(--green)' : 'var(--red)';
      const iColor = iPnl >= 0 ? 'var(--green)' : 'var(--red)';
      const bestSide = r.recommended_side === 'inverse'
        ? '<span style="color:var(--red);font-weight:600">FLIP</span>'
        : '<span style="color:var(--green)">Direct</span>';
      return `<tr>
        <td>${esc(r.rule_name)}</td>
        <td>${num(r.total_matches)}</td>
        <td>${r.win_rate_direct}%</td>
        <td style="color:${dColor};font-weight:600">$${dPnl.toFixed(2)}</td>
        <td style="color:${iColor}">$${iPnl.toFixed(2)}</td>
        <td>${bestSide}</td>
      </tr>`;
    }).join('');
  }

  // Agreement tiers
  const agEl = $('agreementTable');
  const agAll = (bt.agreement_tiers || []).filter(a => a.category === 'all');
  if (agAll.length > 0) {
    let html = '<table style="width:100%;font-size:0.82rem"><thead><tr><th>Agreement Level</th><th>Markets</th><th>Wins</th><th>Win Rate</th><th>Total PnL</th><th>Avg PnL/Trade</th></tr></thead><tbody>';
    for (const a of agAll) {
      const pnlColor = a.pnl >= 0 ? 'var(--green)' : 'var(--red)';
      html += `<tr>
        <td style="font-weight:600">${a.tier}+ rules agree</td>
        <td>${num(a.sample_size)}</td>
        <td>${num(a.wins)}</td>
        <td>${a.win_rate_pct}%</td>
        <td style="color:${pnlColor};font-weight:600">$${Number(a.pnl).toFixed(2)}</td>
        <td>$${Number(a.avg_pnl).toFixed(4)}</td>
      </tr>`;
    }
    html += '</tbody></table>';
    agEl.innerHTML = html;
  }

  // Category matrix
  const cmEl = $('catMatrixTable');
  if (bt.category_matrix && bt.category_matrix.length > 0) {
    let html = '<table style="width:100%;font-size:0.78rem"><thead><tr><th>Rule</th><th>Category</th><th>Samples</th><th>Direct PnL</th><th>Inverse PnL</th><th>Best</th></tr></thead><tbody>';
    for (const c of bt.category_matrix.slice(0, 100)) {
      const dColor = c.pnl_direct >= 0 ? 'var(--green)' : 'var(--red)';
      const iColor = c.pnl_inverse >= 0 ? 'var(--green)' : 'var(--red)';
      html += `<tr>
        <td>${esc(c.rule_name)}</td>
        <td>${esc(c.category)}</td>
        <td>${c.sample_size}</td>
        <td style="color:${dColor}">$${Number(c.pnl_direct).toFixed(2)}</td>
        <td style="color:${iColor}">$${Number(c.pnl_inverse).toFixed(2)}</td>
        <td>${c.recommended_side === 'inverse' ? '<span style="color:var(--red)">FLIP</span>' : 'Direct'}</td>
      </tr>`;
    }
    html += '</tbody></table>';
    cmEl.innerHTML = html;
  }

  // Inverse flip table
  const flipBody = $('invFlipTable').querySelector('tbody');
  if (bt.top_rules_inverse && bt.top_rules_inverse.length > 0) {
    flipBody.innerHTML = bt.top_rules_inverse.map(r => {
      const diff = Number(r.pnl_inverse) - Number(r.pnl_direct);
      return `<tr>
        <td>${esc(r.rule_name)}</td>
        <td>${num(r.total_matches)}</td>
        <td style="color:var(--red)">$${Number(r.pnl_direct).toFixed(2)}</td>
        <td style="color:var(--green)">$${Number(r.pnl_inverse).toFixed(2)}</td>
        <td style="color:var(--green);font-weight:600">+$${diff.toFixed(2)}</td>
      </tr>`;
    }).join('');
  }

  // Inverse live comparison (from main dashboard data)
  const invComp = $('invLiveComparison');
  // This gets populated from the main refresh() data — we'll wire it below
}
```

**Step 2: Wire live inverse comparison into the refresh() function**

In the existing `refresh()` function, after the by_source table rendering, add:

```javascript
// Populate inverse tab live comparison
const invComp = $('invLiveComparison');
if (bs && invComp) {
  let html = '<div style="font-size:0.82rem;line-height:2">';
  for (const pair of [['ngram', 'ngram_inverse'], ['llm', 'llm_inverse'], ['combined', 'combined_inverse']]) {
    const d = bs[pair[0]] || {};
    const i = bs[pair[1]] || {};
    const dPnl = Number(d.pnl || 0);
    const iPnl = Number(i.pnl || 0);
    const winner = iPnl > dPnl ? 'INVERSE' : 'DIRECT';
    const wColor = iPnl > dPnl ? 'var(--red)' : 'var(--green)';
    html += `<div><strong>${pair[0]}:</strong> Direct <span style="color:${dPnl>=0?'var(--green)':'var(--red)'}">$${dPnl.toFixed(2)}</span> vs Inverse <span style="color:${iPnl>=0?'var(--green)':'var(--red)'}">$${iPnl.toFixed(2)}</span> → <span style="color:${wColor};font-weight:700">${winner}</span></div>`;
  }
  html += '</div>';
  invComp.innerHTML = html;
}
```

**Step 3: Also populate the Categories tab live table from existing by_category data**

In `refresh()`, after the by_category rendering in the overview tab, add:

```javascript
// Also populate Categories tab
const catLiveEl = $('catLiveTable');
if (byCat && Object.keys(byCat).length > 0 && catLiveEl) {
  let html = '<table style="width:100%;font-size:0.82rem"><thead><tr><th>Category</th><th>PnL</th><th>Trades</th><th>Wins</th><th>Win Rate</th></tr></thead><tbody>';
  const entries = Object.entries(byCat).sort((a,b) => (b[1].pnl||0) - (a[1].pnl||0));
  for (const [cat, s] of entries) {
    const cc = catColors[cat] || '#7f8c8d';
    const pnlColor = (s.pnl||0) >= 0 ? 'var(--green)' : 'var(--red)';
    html += `<tr>
      <td><span style="padding:2px 8px;border-radius:4px;background:${cc}22;color:${cc};font-weight:600">${cat.replace(/_/g,' ')}</span></td>
      <td style="color:${pnlColor};font-weight:600">$${Number(s.pnl||0).toFixed(2)}</td>
      <td>${s.closed||0}</td><td>${s.wins||0}</td>
      <td>${s.win_rate_pct != null ? s.win_rate_pct+'%' : '-'}</td>
    </tr>`;
  }
  html += '</tbody></table>';
  catLiveEl.innerHTML = html;
}
```

**Step 4: Commit**

```bash
git add polyedge/src/polyedge/static/dashboard.html polyedge/src/polyedge/app.py
git commit -m "feat: tabbed dashboard with backtest, categories, combinations, inverse tabs"
```

---

## Task 6: Scheduler Integration — Periodic Backtest

Add a scheduler loop that re-runs the backtest weekly and the agreement analysis daily.

**Files:**
- Modify: `polyedge/src/polyedge/scheduler.py` (~line 815, in `run_forever`)

**Step 1: Add backtest scheduler functions**

Add before `run_forever()` in `scheduler.py`:

```python
async def run_backtest_refresh():
    """Re-run full backtest (weekly). Heavy computation."""
    from polyedge.analysis.backtest_runner import backtest_ngram_rules
    stats = await backtest_ngram_rules(batch_size=500)
    log.info("Backtest refresh: %s", stats)


async def run_agreement_refresh():
    """Re-run agreement analysis (daily)."""
    from polyedge.analysis.agreement_calculator import run_agreement_analysis
    stats = await run_agreement_analysis()
    log.info("Agreement refresh: %s", stats)
```

**Step 2: Add to run_forever() gather block**

Add these two lines inside the `asyncio.gather(...)` in `run_forever()`:

```python
        # Backtest refresh (weekly — 604800 seconds)
        loop(run_backtest_refresh, 604800, "backtest_refresh"),

        # Agreement analysis (daily — 86400 seconds)
        loop(run_agreement_refresh, 86400, "agreement_refresh"),
```

**Step 3: Update test**

In `polyedge/tests/test_scheduler_remediation.py`, update the `test_score_paper_trades_interval_is_5_minutes` test's source check if needed (this test inspects `run_forever` source code).

**Step 4: Run tests**

```bash
cd polyedge && python -m pytest tests/test_scheduler_remediation.py -v
```

**Step 5: Commit**

```bash
git add polyedge/src/polyedge/scheduler.py
git commit -m "feat: add backtest and agreement refresh to scheduler"
```

---

## Task 7: Research-to-Rules Pipeline

Wire existing Grok/Perplexity factors into rule generation. Currently factors feed LLM predictions but don't create new trading rules.

**Files:**
- Create: `polyedge/src/polyedge/analysis/research_rule_bridge.py`
- Modify: `polyedge/src/polyedge/scheduler.py` (add to run_forever)

**Step 1: Create the bridge module**

Create `polyedge/src/polyedge/analysis/research_rule_bridge.py`:

```python
"""Bridge: convert new research factors into trading rules.

After Grok/Perplexity research generates new factors, this module:
1. Checks if new factors create new feature patterns
2. Runs the rule generator on fresh data
3. Backtests new rules immediately
4. Stores qualified rules in the trading_rules table

Runs as a scheduled job after research ingestion.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)


async def generate_rules_from_research() -> dict:
    """Check for new research factors and generate rules from them.

    1. Find factors added in the last 6 hours
    2. Extract feature patterns from them
    3. Run ngram mining on any new question-text patterns
    4. Store new rules
    """
    from polyedge.db import SessionLocal
    from polyedge.models import Factor, TradingRule, NgramStat
    from polyedge.analysis.ngram_miner import mine_ngrams_from_resolved
    from sqlalchemy import select, func

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - timedelta(hours=6)

    async with SessionLocal() as session:
        # Count new factors since cutoff
        new_factor_count = (await session.execute(
            select(func.count(Factor.id)).where(Factor.timestamp >= cutoff)
        )).scalar() or 0

        if new_factor_count == 0:
            log.info("No new research factors in last 6h, skipping rule generation")
            return {"new_factors": 0, "new_rules": 0}

        # Get existing ngram rule phrases to avoid duplicates
        existing_ngrams = (await session.execute(
            select(TradingRule.conditions_json).where(
                TradingRule.rule_type == "ngram"
            )
        )).scalars().all()

    existing_phrases = set()
    for cj in existing_ngrams:
        try:
            cond = json.loads(cj) if isinstance(cj, str) else (cj or {})
            phrase = str(cond.get("ngram", "")).strip().lower()
            if phrase:
                existing_phrases.add(phrase)
        except (json.JSONDecodeError, TypeError):
            continue

    log.info(
        "Research bridge: %d new factors, %d existing ngram phrases",
        new_factor_count, len(existing_phrases),
    )

    # Re-mine ngrams from resolved markets — this discovers new patterns
    # The ngram_miner already exists and works, we just need to call it
    new_rules = await mine_ngrams_from_resolved(
        min_markets=30,
        min_deviation=0.10,
        existing_phrases=existing_phrases,
    )

    log.info("Research bridge: generated %d new rules", new_rules)
    return {"new_factors": new_factor_count, "new_rules": new_rules}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(generate_rules_from_research())
```

**Step 2: Add to scheduler**

Add before `run_forever()`:

```python
async def run_research_rule_bridge():
    """Generate new rules from recent research factors."""
    from polyedge.analysis.research_rule_bridge import generate_rules_from_research
    stats = await generate_rules_from_research()
    log.info("Research-to-rules bridge: %s", stats)
```

Add to `asyncio.gather(...)`:

```python
        # Research-to-rules bridge (every 6 hours)
        loop(run_research_rule_bridge, 21600, "research_rule_bridge"),
```

**Step 3: Commit**

```bash
git add polyedge/src/polyedge/analysis/research_rule_bridge.py polyedge/src/polyedge/scheduler.py
git commit -m "feat: add research-to-rules pipeline bridge"
```

---

## Task 8: Run Initial Backtest

This is not code — it's the actual execution on the 256GB server.

**Step 1: Deploy code to compute server**

```bash
scp -r polyedge/src/polyedge/analysis/backtest_runner.py Administrator@88.99.142.89:C:/polyedge/polyedge/src/polyedge/analysis/
scp -r polyedge/src/polyedge/analysis/agreement_calculator.py Administrator@88.99.142.89:C:/polyedge/polyedge/src/polyedge/analysis/
scp polyedge/src/polyedge/models.py Administrator@88.99.142.89:C:/polyedge/polyedge/src/polyedge/
```

**Step 2: Run migration on DB**

```bash
ssh root@89.167.99.187 "psql -U polyedge -d polyedge -f -" < polyedge/deploy/migrations/004_backtest_tables.sql
```

**Step 3: Run ngram backtest (expect 2-4 hours)**

```bash
ssh Administrator@88.99.142.89 "cd C:\polyedge && python -m polyedge.analysis.backtest_runner --rule-type ngram --batch-size 500"
```

**Step 4: Run agreement analysis (expect 30-60 minutes)**

```bash
ssh Administrator@88.99.142.89 "cd C:\polyedge && python -m polyedge.analysis.agreement_calculator"
```

**Step 5: Verify results**

```bash
ssh root@89.167.99.187 "psql -U polyedge -d polyedge -c 'SELECT count(*) FROM backtest_results; SELECT count(*) FROM rule_category_performance; SELECT count(*) FROM agreement_signals;'"
```

**Step 6: Run threshold backtest overnight**

```bash
ssh Administrator@88.99.142.89 "cd C:\polyedge && python -m polyedge.analysis.backtest_runner --rule-type threshold --batch-size 1000"
```

---

## Task 9: Deploy Dashboard and API Updates

**Step 1: Deploy to dashboard server**

```bash
scp polyedge/src/polyedge/app.py root@89.167.99.187:/opt/polyedge/polyedge/src/polyedge/app.py
scp polyedge/src/polyedge/static/dashboard.html root@89.167.99.187:/opt/polyedge/polyedge/src/polyedge/static/dashboard.html
scp polyedge/src/polyedge/models.py root@89.167.99.187:/opt/polyedge/polyedge/src/polyedge/models.py
```

**Step 2: Restart dashboard service**

```bash
ssh root@89.167.99.187 "systemctl restart polyedge-dashboard"
```

**Step 3: Verify**

```bash
curl -s http://89.167.99.187:8090/api/backtest-summary | python -m json.tool | head -10
```

**Step 4: Deploy updated scheduler to compute server**

```bash
scp polyedge/src/polyedge/scheduler.py Administrator@88.99.142.89:C:/polyedge/polyedge/src/polyedge/
```

Then restart the scheduler (kill old process, start new one via schtasks).

---

## Summary & Execution Order

| # | Task | Time Est. | Depends On |
|---|------|-----------|------------|
| 1 | DB Schema + Migration | 10 min | — |
| 2 | Backtest Runner | 30 min | Task 1 |
| 3 | Agreement Calculator | 20 min | Task 1 |
| 4 | Backtest API Endpoint | 15 min | Task 1 |
| 5 | Dashboard Tab Overhaul | 30 min | Task 4 |
| 6 | Scheduler Integration | 10 min | Tasks 2, 3 |
| 7 | Research-to-Rules Bridge | 15 min | — |
| 8 | Run Initial Backtest | 4-12 hours (compute) | Tasks 1, 2, 3 |
| 9 | Deploy Everything | 15 min | All above |

**Tasks 1-7 are code.** Can be parallelized: Tasks 2+3 in parallel, Task 4+5 in parallel.
**Task 8 is compute.** Kick off ngram backtest ASAP, let threshold grind overnight.
**Task 9 is deploy.** After code is verified locally.

**Critical path:** Task 1 → Task 2 → Task 8 (start backtest) → Tasks 3-7 while backtest runs → Task 9 deploy.
