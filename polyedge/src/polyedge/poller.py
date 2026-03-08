import json
import logging
from datetime import datetime, timedelta, timezone
import httpx
from sqlalchemy import select
from polyedge.db import SessionLocal
from polyedge.models import Market, PriceSnapshot

log = logging.getLogger(__name__)
GAMMA_URL = "https://gamma-api.polymarket.com"


def parse_market(raw: dict) -> dict:
    prices_raw = raw.get("outcomePrices", '["0.5", "0.5"]')
    if isinstance(prices_raw, str):
        try:
            prices = json.loads(prices_raw)
        except (json.JSONDecodeError, TypeError):
            prices = ["0.5", "0.5"]
    else:
        prices = prices_raw
    yes_price = float(prices[0]) if prices else 0.5
    no_price = float(prices[1]) if len(prices) > 1 else 1.0 - yes_price
    end_raw = raw.get("endDate")
    if end_raw:
        end_date = datetime.fromisoformat(end_raw.replace("Z", "+00:00")).replace(tzinfo=None)
    else:
        end_date = None
    closed = raw.get("closed", False)
    resolved_by = raw.get("resolvedBy") or ""
    # Detect resolution from outcome prices: [1, 0] = YES, [0, 1] = NO
    # Also detect when resolvedBy is set (market resolved but closed=false sometimes)
    resolution = None
    resolution_source = ""
    is_settled = closed or bool(resolved_by)
    if is_settled:
        if yes_price >= 0.99:
            resolution = "YES"
            resolution_source = "polymarket_api"
        elif no_price >= 0.99:
            resolution = "NO"
            resolution_source = "polymarket_api"
        elif yes_price <= 0.01:
            resolution = "NO"
            resolution_source = "polymarket_api"
        elif no_price <= 0.01:
            resolution = "YES"
            resolution_source = "polymarket_api"
    from polyedge.analysis.market_classifier import classify_market
    question = raw.get("question", "")
    return {
        "id": raw["id"],
        "question": question,
        "slug": raw.get("slug", ""),
        "category": raw.get("category", ""),
        "market_category": classify_market(question),
        "description": raw.get("description", ""),
        "end_date": end_date,
        "yes_price": yes_price,
        "no_price": no_price,
        "volume": float(raw.get("volume", 0)),
        "liquidity": float(raw.get("liquidity", 0)),
        "active": raw.get("active", True) and not is_settled,
        "resolved": is_settled or bool(resolution),
        "resolution": resolution,
        "resolution_source": resolution_source,
        "clob_token_ids": str(raw.get("clobTokenIds", [])),
    }


class PolymarketPoller:
    def __init__(self):
        self.client = httpx.AsyncClient(base_url=GAMMA_URL, timeout=30)

    async def fetch_markets(self, limit: int = 100, offset: int = 0, **params) -> list[dict]:
        params.update({"limit": limit, "offset": offset})
        resp = await self.client.get("/markets", params=params)
        resp.raise_for_status()
        return resp.json()

    async def fetch_market_by_id(self, market_id: str) -> dict | None:
        resp = await self.client.get(f"/markets/{market_id}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    async def _poll_batch(self, max_results: int = 0, **params) -> int:
        """Poll markets with given filter params."""
        offset = 0
        total = 0
        while True:
            raw_markets = await self.fetch_markets(limit=100, offset=offset, **params)
            if not raw_markets:
                break
            async with SessionLocal() as session:
                for raw in raw_markets:
                    parsed = parse_market(raw)
                    existing = await session.get(Market, parsed["id"])
                    if existing:
                        for k, v in parsed.items():
                            setattr(existing, k, v)
                        existing.updated_at = datetime.utcnow()
                    else:
                        session.add(Market(**parsed))
                    session.add(PriceSnapshot(
                        market_id=parsed["id"],
                        yes_price=parsed["yes_price"],
                        no_price=parsed["no_price"],
                        volume_24h=float(raw.get("volume24hr", 0)),
                    ))
                await session.commit()
            total += len(raw_markets)
            offset += 100
            if len(raw_markets) < 100:
                break
            if max_results and total >= max_results:
                break
        return total

    async def poll_all(self) -> int:
        # Only poll active (non-closed) markets — ~8k instead of 326k
        active = 0
        closed = 0
        stale = 0
        try:
            active = await self._poll_batch(closed="false")
        except Exception as exc:
            log.warning("Active market polling failed: %r", exc, exc_info=True)
        # Also poll recently closed to catch resolutions
        try:
            closed = await self._poll_batch(
                closed="true", order="updatedAt", ascending="false", max_results=500
            )
        except Exception as exc:
            log.warning("Closed market polling failed: %r", exc, exc_info=True)
        try:
            stale = await self.refresh_stale_unresolved(max_markets=200, grace_days=7)
        except Exception as exc:
            log.warning("Stale-market reconciliation failed: %r", exc, exc_info=True)
        total = active + closed + stale
        log.info(
            "Polled %d markets (%d active, %d recently closed, %d stale reconciled)",
            total, active, closed, stale,
        )
        return total

    async def close(self):
        await self.client.aclose()

    async def refresh_stale_unresolved(self, max_markets: int = 200, grace_days: int = 7) -> int:
        """Reconcile unresolved markets whose end date is already stale."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        cutoff = now - timedelta(days=grace_days)

        async with SessionLocal() as session:
            stale_ids = (
                await session.execute(
                    select(Market.id)
                    .where(
                        Market.resolved == False,  # noqa: E712
                        Market.end_date.is_not(None),
                        Market.end_date < cutoff,
                    )
                    .order_by(Market.end_date.asc())
                    .limit(max_markets)
                )
            ).scalars().all()

        if not stale_ids:
            return 0

        fetched: dict[str, dict] = {}
        for market_id in stale_ids:
            try:
                raw = await self.fetch_market_by_id(market_id)
                if raw is not None:
                    fetched[market_id] = raw
            except Exception:
                log.exception("Failed stale-market fetch for %s", market_id)

        if not fetched:
            return 0

        async with SessionLocal() as session:
            reconciled = 0
            for market_id, raw in fetched.items():
                parsed = parse_market(raw)
                if (
                    parsed.get("resolved") is False
                    and parsed.get("end_date") is not None
                    and parsed["end_date"] < cutoff
                ):
                    # Guard rail: stale unresolved markets should no longer be treated as active.
                    parsed["active"] = False
                existing = await session.get(Market, market_id)
                if existing is None:
                    session.add(Market(**parsed))
                else:
                    for key, value in parsed.items():
                        setattr(existing, key, value)
                    existing.updated_at = datetime.utcnow()
                reconciled += 1
            await session.commit()

        return reconciled
