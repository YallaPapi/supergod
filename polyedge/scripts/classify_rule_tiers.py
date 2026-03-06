"""Backfill tier/quality labels for trading rules without deleting any rule."""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys

from sqlalchemy import select

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from polyedge.db import SessionLocal  # noqa: E402
from polyedge.models import TradingRule  # noqa: E402


def classify_rule(rule: TradingRule) -> tuple[int, str]:
    if rule.rule_type != "ngram":
        return 3, "non_ngram"

    try:
        cond = json.loads(rule.conditions_json) if isinstance(rule.conditions_json, str) else (rule.conditions_json or {})
    except (json.JSONDecodeError, TypeError):
        cond = {}
    phrase = str(cond.get("ngram", "")).strip().lower()
    words = [w for w in phrase.split() if w]
    sample_size = int(rule.sample_size or 0)

    if sample_size >= 500 and len(words) >= 2 and all(len(w) >= 4 for w in words):
        return 1, "strong"
    if sample_size >= 500 and len(words) == 1 and len(words[0]) >= 4:
        return 2, "moderate"
    return 3, "exploratory"


async def main() -> None:
    async with SessionLocal() as session:
        rules = (await session.execute(select(TradingRule))).scalars().all()
        counts = {1: 0, 2: 0, 3: 0}
        for rule in rules:
            tier, quality = classify_rule(rule)
            rule.tier = tier
            rule.quality_label = quality
            counts[tier] += 1
        await session.commit()

    total = sum(counts.values())
    print(f"rules_classified: {total}")
    print(f"tier_1_strong: {counts[1]}")
    print(f"tier_2_moderate: {counts[2]}")
    print(f"tier_3_exploratory: {counts[3]}")


if __name__ == "__main__":
    asyncio.run(main())
