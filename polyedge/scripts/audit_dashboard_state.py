"""Baseline dashboard metrics audit from database state."""

import asyncio
import pathlib
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, func, select

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from polyedge.db import SessionLocal  # noqa: E402
from polyedge.models import Market, PaperTrade  # noqa: E402


def utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def main() -> None:
    now = utcnow_naive()
    day_end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    week_end = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=7)
    junk = Market.question.ilike("%up or down%")

    async with SessionLocal() as session:
        open_count = (
            await session.execute(
                select(func.count(PaperTrade.id))
                .join(Market, Market.id == PaperTrade.market_id)
                .where(
                    PaperTrade.resolved == False,  # noqa: E712
                    PaperTrade.entry_price > 0.02,
                    ~junk,
                )
            )
        ).scalar() or 0

        closed_count = (
            await session.execute(
                select(func.count(PaperTrade.id))
                .join(Market, Market.id == PaperTrade.market_id)
                .where(
                    PaperTrade.resolved == True,  # noqa: E712
                    PaperTrade.entry_price > 0.02,
                    ~junk,
                )
            )
        ).scalar() or 0

        pt_scored = (
            await session.execute(
                select(func.count(PaperTrade.id))
                .join(Market, Market.id == PaperTrade.market_id)
                .where(
                    PaperTrade.resolved == True,  # noqa: E712
                    PaperTrade.entry_price > 0.02,
                    ~junk,
                )
            )
        ).scalar() or 0

        pt_correct = (
            await session.execute(
                select(func.count(PaperTrade.id))
                .join(Market, Market.id == PaperTrade.market_id)
                .where(
                    PaperTrade.resolved == True,  # noqa: E712
                    PaperTrade.pnl > 0,
                    PaperTrade.entry_price > 0.02,
                    ~junk,
                )
            )
        ).scalar() or 0
        pt_hit = round(pt_correct / pt_scored * 100, 1) if pt_scored else None

        ending_today = (
            await session.execute(
                select(func.count(func.distinct(PaperTrade.market_id)))
                .join(Market, Market.id == PaperTrade.market_id)
                .where(
                    PaperTrade.resolved == False,  # noqa: E712
                    Market.end_date >= now,
                    Market.end_date < day_end,
                )
            )
        ).scalar() or 0

        ending_this_week = (
            await session.execute(
                select(func.count(func.distinct(PaperTrade.market_id)))
                .join(Market, Market.id == PaperTrade.market_id)
                .where(
                    PaperTrade.resolved == False,  # noqa: E712
                    Market.end_date >= now,
                    Market.end_date < week_end,
                )
            )
        ).scalar() or 0

        stale_opportunities = (
            await session.execute(
                select(func.count(PaperTrade.id))
                .join(Market, Market.id == PaperTrade.market_id)
                .where(
                    PaperTrade.resolved == False,  # noqa: E712
                    PaperTrade.entry_price > 0.02,
                    ~junk,
                    Market.end_date.is_not(None),
                    Market.end_date < now,
                )
            )
        ).scalar() or 0

        latest_stale = (
            await session.execute(
                select(Market.question, Market.end_date)
                .join(PaperTrade, PaperTrade.market_id == Market.id)
                .where(
                    PaperTrade.resolved == False,  # noqa: E712
                    PaperTrade.entry_price > 0.02,
                    ~junk,
                    Market.end_date.is_not(None),
                    Market.end_date < now,
                )
                .order_by(desc(Market.end_date))
                .limit(5)
            )
        ).all()

    print(f"now_utc: {now.isoformat()}Z")
    print(f"open_count: {open_count}")
    print(f"closed_count: {closed_count}")
    print(f"paper_trade_scored: {pt_scored}")
    print(f"paper_trade_correct: {pt_correct}")
    print(f"pt_hit_pct: {pt_hit}")
    print(f"ending_today: {ending_today}")
    print(f"ending_this_week: {ending_this_week}")
    print(f"stale_opportunities: {stale_opportunities}")
    if latest_stale:
        print("sample_stale:")
        for q, end_date in latest_stale:
            print(f"  - {end_date} | {q[:120]}")


if __name__ == "__main__":
    asyncio.run(main())
