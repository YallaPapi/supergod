"""Audit unresolved active markets that are already past end date."""

import asyncio
import pathlib
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import asc, desc, func, select

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from polyedge.db import SessionLocal  # noqa: E402
from polyedge.models import Market, PaperTrade  # noqa: E402


def utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def main() -> None:
    now = utcnow_naive()
    cutoff_7d = now - timedelta(days=7)

    async with SessionLocal() as session:
        stale_active = (
            await session.execute(
                select(func.count(Market.id)).where(
                    Market.active == True,  # noqa: E712
                    Market.resolved == False,  # noqa: E712
                    Market.end_date.is_not(None),
                    Market.end_date < now,
                )
            )
        ).scalar() or 0

        stale_active_7d = (
            await session.execute(
                select(func.count(Market.id)).where(
                    Market.active == True,  # noqa: E712
                    Market.resolved == False,  # noqa: E712
                    Market.end_date.is_not(None),
                    Market.end_date < cutoff_7d,
                )
            )
        ).scalar() or 0

        stale_open_trades = (
            await session.execute(
                select(func.count(PaperTrade.id))
                .join(Market, Market.id == PaperTrade.market_id)
                .where(
                    PaperTrade.resolved == False,  # noqa: E712
                    Market.end_date.is_not(None),
                    Market.end_date < now,
                )
            )
        ).scalar() or 0

        oldest = (
            await session.execute(
                select(Market.id, Market.end_date, Market.question)
                .where(
                    Market.active == True,  # noqa: E712
                    Market.resolved == False,  # noqa: E712
                    Market.end_date.is_not(None),
                    Market.end_date < now,
                )
                .order_by(asc(Market.end_date))
                .limit(5)
            )
        ).all()

        newest_stale = (
            await session.execute(
                select(Market.id, Market.end_date, Market.question)
                .where(
                    Market.active == True,  # noqa: E712
                    Market.resolved == False,  # noqa: E712
                    Market.end_date.is_not(None),
                    Market.end_date < now,
                )
                .order_by(desc(Market.end_date))
                .limit(5)
            )
        ).all()

    print(f"now_utc: {now.isoformat()}Z")
    print(f"stale_active_markets: {stale_active}")
    print(f"stale_active_markets_over_7d: {stale_active_7d}")
    print(f"open_paper_trades_on_stale_markets: {stale_open_trades}")
    if oldest:
        print("oldest_stale_markets:")
        for mid, end_date, question in oldest:
            print(f"  - {mid} | {end_date} | {question[:110]}")
    if newest_stale:
        print("most_recent_stale_markets:")
        for mid, end_date, question in newest_stale:
            print(f"  - {mid} | {end_date} | {question[:110]}")


if __name__ == "__main__":
    asyncio.run(main())
