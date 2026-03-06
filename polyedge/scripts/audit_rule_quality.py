"""Audit ngram rule quality distribution by phrase length and sample size."""

import asyncio
import json
import pathlib
import sys
from collections import Counter

from sqlalchemy import select

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from polyedge.db import SessionLocal  # noqa: E402
from polyedge.models import TradingRule  # noqa: E402


def phrase_len_bucket(value: int) -> str:
    if value <= 3:
        return "1-3 chars"
    if value <= 7:
        return "4-7 chars"
    return "8+ chars"


def sample_bucket(value: int) -> str:
    if value < 200:
        return "<200"
    if value < 500:
        return "200-499"
    if value < 1000:
        return "500-999"
    return "1000+"


async def main() -> None:
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(TradingRule).where(
                    TradingRule.active == True,  # noqa: E712
                    TradingRule.rule_type == "ngram",
                )
            )
        ).scalars().all()

    total = len(rows)
    by_len = Counter()
    by_sample = Counter()
    by_len_sample = Counter()

    for row in rows:
        try:
            cond = json.loads(row.conditions_json) if isinstance(row.conditions_json, str) else (row.conditions_json or {})
        except json.JSONDecodeError:
            cond = {}
        phrase = str(cond.get("ngram", "")).strip().lower()
        length = len(phrase)
        len_key = phrase_len_bucket(length)
        sample_key = sample_bucket(int(row.sample_size or 0))
        by_len[len_key] += 1
        by_sample[sample_key] += 1
        by_len_sample[(len_key, sample_key)] += 1

    print(f"active_ngram_rules_total: {total}")
    print("by_phrase_length:")
    for key in ("1-3 chars", "4-7 chars", "8+ chars"):
        print(f"  - {key}: {by_len[key]}")
    print("by_sample_size:")
    for key in ("<200", "200-499", "500-999", "1000+"):
        print(f"  - {key}: {by_sample[key]}")
    print("cross_tab_length_x_sample:")
    for len_key in ("1-3 chars", "4-7 chars", "8+ chars"):
        for sample_key in ("<200", "200-499", "500-999", "1000+"):
            print(f"  - {len_key} / {sample_key}: {by_len_sample[(len_key, sample_key)]}")


if __name__ == "__main__":
    asyncio.run(main())
