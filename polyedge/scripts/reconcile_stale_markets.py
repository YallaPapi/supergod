"""One-off stale market reconciliation via Polymarket market-id lookups."""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from polyedge.poller import PolymarketPoller  # noqa: E402


async def main(limit: int, grace_days: int) -> None:
    poller = PolymarketPoller()
    try:
        reconciled = await poller.refresh_stale_unresolved(
            max_markets=limit,
            grace_days=grace_days,
        )
        print(f"stale_markets_reconciled: {reconciled}")
    finally:
        await poller.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reconcile stale unresolved markets.")
    parser.add_argument("--limit", type=int, default=1000, help="Max stale markets to reconcile in this run.")
    parser.add_argument("--grace-days", type=int, default=7, help="Only reconcile markets older than this cutoff.")
    args = parser.parse_args()
    asyncio.run(main(args.limit, args.grace_days))
