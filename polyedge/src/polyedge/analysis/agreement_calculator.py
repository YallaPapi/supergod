"""Calculate performance when multiple rules agree on the same market prediction.

For each resolved market, finds all matching ngram rules, determines the
majority-vote side, counts how many rules agree, and buckets results into
agreement tiers (1+, 2+, 3+, 5+, 10+).  Higher agreement tiers should show
stronger edge if the rules are capturing real signal.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import delete, select

from polyedge.db import SessionLocal
from polyedge.models import AgreementSignal, Market, TradingRule
from polyedge.trading.pnl import calc_pnl

log = logging.getLogger(__name__)

AGREEMENT_TIERS = [1, 2, 3, 5, 10]


def compute_agreement_tiers(
    market_rule_matches: dict[str, list[dict]],
) -> dict[int, dict[str, dict]]:
    """Aggregate per-tier, per-category performance from raw rule matches.

    Args:
        market_rule_matches: ``{market_id: [match_dicts]}`` where each match
            dict contains *rule_id*, *side*, *won*, *pnl*, *category*.

    Returns:
        ``{tier: {category: {sample_size, wins, pnl, avg_pnl_per_trade}}}``
    """
    # Accumulator: tier -> category -> running stats
    accum: dict[int, dict[str, dict]] = {
        t: defaultdict(lambda: {"sample_size": 0, "wins": 0, "pnl": 0.0})
        for t in AGREEMENT_TIERS
    }

    for market_id, matches in market_rule_matches.items():
        if not matches:
            continue

        # Group matches by predicted side
        side_matches: dict[str, list[dict]] = defaultdict(list)
        for m in matches:
            side_matches[m["side"]].append(m)

        # Determine majority side (most rules agree on)
        majority_side = max(side_matches, key=lambda s: len(side_matches[s]))
        agreement_count = len(side_matches[majority_side])
        majority_matches = side_matches[majority_side]

        # Pick a representative match for outcome stats (all share the same
        # market, so won/pnl are identical within same side).
        rep = majority_matches[0]

        # Determine the category for this market (use the first match's
        # category, they all share the same market).
        category = rep.get("category") or "unknown"

        for tier in AGREEMENT_TIERS:
            if agreement_count >= tier:
                # "all" bucket
                bucket_all = accum[tier]["all"]
                bucket_all["sample_size"] += 1
                bucket_all["wins"] += 1 if rep["won"] else 0
                bucket_all["pnl"] += rep["pnl"]

                # per-category bucket
                bucket_cat = accum[tier][category]
                bucket_cat["sample_size"] += 1
                bucket_cat["wins"] += 1 if rep["won"] else 0
                bucket_cat["pnl"] += rep["pnl"]

    # Compute avg_pnl_per_trade
    result: dict[int, dict[str, dict]] = {}
    for tier in AGREEMENT_TIERS:
        result[tier] = {}
        for category, stats in accum[tier].items():
            ss = stats["sample_size"]
            result[tier][category] = {
                "sample_size": ss,
                "wins": stats["wins"],
                "pnl": round(stats["pnl"], 6),
                "avg_pnl_per_trade": round(stats["pnl"] / ss, 6) if ss > 0 else 0.0,
            }

    return result


async def run_agreement_analysis() -> dict:
    """Run full agreement analysis: load data, compute tiers, persist results.

    Returns:
        Summary dict with tier counts and total markets analysed.
    """
    async with SessionLocal() as session:
        # ------------------------------------------------------------------
        # 1. Load all resolved markets
        # ------------------------------------------------------------------
        rows = (
            await session.execute(
                select(Market).where(Market.resolution.in_(["YES", "NO"]))
            )
        ).scalars().all()

        markets_by_id: dict[str, Market] = {m.id: m for m in rows}
        log.info("Loaded %d resolved markets", len(markets_by_id))

        if not markets_by_id:
            return {"markets": 0, "tiers": {}}

        # ------------------------------------------------------------------
        # 2. Load all ngram trading rules, parse phrase from conditions_json
        # ------------------------------------------------------------------
        rule_rows = (
            await session.execute(
                select(TradingRule).where(TradingRule.rule_type == "ngram")
            )
        ).scalars().all()
        log.info("Loaded %d ngram rules", len(rule_rows))

        rules: list[dict] = []
        for r in rule_rows:
            try:
                conditions = json.loads(r.conditions_json)
            except (json.JSONDecodeError, TypeError):
                continue
            phrase = conditions.get("ngram")
            if not phrase:
                continue
            rules.append({
                "id": r.id,
                "phrase": phrase.lower(),
                "predicted_side": r.predicted_side,
            })

        if not rules:
            log.warning("No valid ngram rules found")
            return {"markets": len(markets_by_id), "rules": 0, "tiers": {}}

        # ------------------------------------------------------------------
        # 3. Match rules to markets, compute PnL for each match
        # ------------------------------------------------------------------
        market_rule_matches: dict[str, list[dict]] = defaultdict(list)

        for i, (market_id, market) in enumerate(markets_by_id.items()):
            # Yield event loop every 500 markets so other services aren't starved
            if i % 500 == 0 and i > 0:
                await asyncio.sleep(0)
            question_lower = (market.question or "").lower()
            category = market.market_category or "unknown"

            for rule in rules:
                if rule["phrase"] not in question_lower:
                    continue

                side = rule["predicted_side"]
                entry_price = (
                    float(market.yes_price) if side == "YES"
                    else float(market.no_price)
                )

                # Skip degenerate prices
                if entry_price <= 0 or entry_price >= 1:
                    continue

                won = (market.resolution == side)
                pnl = calc_pnl(entry_price, won)

                market_rule_matches[market_id].append({
                    "rule_id": rule["id"],
                    "side": side,
                    "won": won,
                    "pnl": pnl,
                    "category": category,
                })

        matched_markets = len(market_rule_matches)
        total_matches = sum(len(v) for v in market_rule_matches.values())
        log.info(
            "Matched %d rules across %d markets (%d total match pairs)",
            len(rules), matched_markets, total_matches,
        )

        # ------------------------------------------------------------------
        # 4. Compute agreement tiers
        # ------------------------------------------------------------------
        tiers = compute_agreement_tiers(market_rule_matches)

        # ------------------------------------------------------------------
        # 5. Persist: delete old rows, insert fresh
        # ------------------------------------------------------------------
        await session.execute(delete(AgreementSignal))

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        inserted = 0
        for tier, categories in tiers.items():
            for category, stats in categories.items():
                if stats["sample_size"] == 0:
                    continue
                session.add(AgreementSignal(
                    agreement_tier=tier,
                    category=category,
                    sample_size=stats["sample_size"],
                    wins=stats["wins"],
                    pnl=stats["pnl"],
                    avg_pnl_per_trade=stats["avg_pnl_per_trade"],
                    last_updated=now,
                ))
                inserted += 1

        await session.commit()
        log.info("Persisted %d AgreementSignal rows", inserted)

    # Build summary
    summary: dict = {
        "markets": len(markets_by_id),
        "rules": len(rules),
        "matched_markets": matched_markets,
        "total_match_pairs": total_matches,
        "tiers": {},
    }
    for tier in AGREEMENT_TIERS:
        all_stats = tiers.get(tier, {}).get("all")
        if all_stats and all_stats["sample_size"] > 0:
            summary["tiers"][tier] = all_stats

    return summary


if __name__ == "__main__":
    import asyncio
    logging.basicConfig(level=logging.INFO)
    result = asyncio.run(run_agreement_analysis())
    print("=== Agreement Analysis Complete ===")
    for tier in AGREEMENT_TIERS:
        stats = result.get("tiers", {}).get(tier)
        if stats:
            wr = stats["wins"] / stats["sample_size"] if stats["sample_size"] else 0
            print(
                f"  Tier {tier:>2}+: {stats['sample_size']:>5} markets, "
                f"PnL=${stats['pnl']:>8.2f}, "
                f"avg=${stats['avg_pnl_per_trade']:>6.4f}/trade, "
                f"WR={wr:.1%}"
            )
    print(f"Total resolved markets: {result['markets']}")
    print(f"Ngram rules used: {result.get('rules', 0)}")
    print(f"Markets with matches: {result.get('matched_markets', 0)}")
