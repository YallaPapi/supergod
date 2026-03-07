"""Bridge: convert new research factors into trading rules.

After Grok/Perplexity research generates new factors, this module:
1. Checks if new factors have arrived since last run
2. Re-mines ngrams from resolved markets (picks up new patterns)
3. Converts actionable ngrams into trading rules (skipping duplicates)
4. Stores qualified rules in the trading_rules table

Runs as a scheduled job every 6 hours.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from scipy import stats as sp_stats

from polyedge.db import SessionLocal
from polyedge.models import Factor, TradingRule
from polyedge.analysis.ngram_miner import run_ngram_mining, mine_ngrams, filter_actionable
from polyedge.analysis.rule_generator import generate_both_sides, store_rules

from sqlalchemy import select, func

log = logging.getLogger(__name__)


async def _get_existing_ngram_phrases() -> set[str]:
    """Load all existing ngram rule phrases to avoid duplicates."""
    async with SessionLocal() as session:
        rows = (await session.execute(
            select(TradingRule.conditions_json).where(
                TradingRule.rule_type == "ngram"
            )
        )).scalars().all()

    phrases = set()
    for cj in rows:
        try:
            cond = json.loads(cj) if isinstance(cj, str) else (cj or {})
            phrase = str(cond.get("ngram", "")).strip().lower()
            if phrase:
                phrases.add(phrase)
        except (json.JSONDecodeError, TypeError):
            continue
    return phrases


async def generate_rules_from_research() -> dict:
    """Check for new research factors and generate rules from them.

    1. Count factors added in the last 6 hours
    2. If new factors exist, re-mine ngrams from all resolved markets
    3. Convert new actionable ngrams (not already in trading_rules) into rules
    4. Store both sides via generate_both_sides + store_rules
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - timedelta(hours=6)

    async with SessionLocal() as session:
        new_factor_count = (await session.execute(
            select(func.count(Factor.id)).where(Factor.timestamp >= cutoff)
        )).scalar() or 0

    if new_factor_count == 0:
        log.info("No new research factors in last 6h, skipping rule generation")
        return {"new_factors": 0, "new_rules": 0}

    log.info("Research bridge: %d new factors found, re-mining ngrams", new_factor_count)

    # Re-run ngram mining (updates ngram_stats table)
    await run_ngram_mining()

    # Load existing ngram rule phrases to skip duplicates
    existing_phrases = await _get_existing_ngram_phrases()
    log.info("Research bridge: %d existing ngram rule phrases", len(existing_phrases))

    # Load fresh ngram stats from DB and find actionable ones not yet in rules
    from polyedge.models import NgramStat
    async with SessionLocal() as session:
        rows = (await session.execute(select(NgramStat))).scalars().all()

    ngram_stats = [
        {
            "ngram": r.ngram,
            "n": r.n,
            "total_markets": r.total_markets,
            "yes_count": r.yes_count,
            "no_count": r.no_count,
            "yes_rate": r.yes_rate,
            "no_rate": r.no_rate,
        }
        for r in rows
    ]

    actionable = filter_actionable(ngram_stats, min_samples=30, min_edge=0.05)

    # Filter out ngrams that already have rules
    new_ngrams = [ng for ng in actionable if ng["ngram"].lower() not in existing_phrases]

    if not new_ngrams:
        log.info("Research bridge: no new actionable ngrams found (all %d already have rules)", len(actionable))
        return {"new_factors": new_factor_count, "new_rules": 0}

    # Convert to CandidateRule-compatible dicts
    candidate_rules = []
    for ng in new_ngrams:
        side = "YES" if ng["yes_rate"] > 0.5 else "NO"
        wr = ng["yes_rate"] if side == "YES" else ng["no_rate"]

        # Binomial significance test
        successes = ng["yes_count"] if side == "YES" else ng["no_count"]
        p_val = sp_stats.binomtest(
            successes, ng["total_markets"], 0.5, alternative="greater"
        ).pvalue

        # Only include statistically significant patterns (p < 0.05)
        if p_val >= 0.05:
            continue

        candidate_rules.append({
            "name": f"ngram:{ng['ngram']}",
            "rule_type": "ngram",
            "conditions_json": json.dumps({"ngram": ng["ngram"], "n": ng["n"]}),
            "predicted_side": side,
            "win_rate": round(float(wr), 4),
            "sample_size": ng["total_markets"],
            "market_filter": "",
        })

    if not candidate_rules:
        log.info("Research bridge: no statistically significant new ngrams")
        return {"new_factors": new_factor_count, "new_rules": 0}

    # Generate both sides and store
    both_sides = generate_both_sides(candidate_rules, min_win_rate=0.0, min_sample_size=0)
    await store_rules(both_sides)

    log.info(
        "Research bridge: generated %d new rules (%d both-sides) from %d new ngrams",
        len(candidate_rules), len(both_sides), len(new_ngrams),
    )

    return {
        "new_factors": new_factor_count,
        "new_actionable_ngrams": len(new_ngrams),
        "new_rules": len(both_sides),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = asyncio.run(generate_rules_from_research())
    print(f"Research-to-rules bridge result: {result}")
