import json
import logging
from datetime import datetime
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
    end_date = datetime.fromisoformat(end_raw.replace("Z", "+00:00")) if end_raw else None
    closed = raw.get("closed", False)
    return {
        "id": raw["id"],
        "question": raw.get("question", ""),
        "slug": raw.get("slug", ""),
        "category": raw.get("category", ""),
        "description": raw.get("description", ""),
        "end_date": end_date,
        "yes_price": yes_price,
        "no_price": no_price,
        "volume": float(raw.get("volume", 0)),
        "liquidity": float(raw.get("liquidity", 0)),
        "active": raw.get("active", True) and not closed,
        "resolved": closed,
        "clob_token_ids": str(raw.get("clobTokenIds", [])),
    }


class PolymarketPoller:
    def __init__(self):
        self.client = httpx.AsyncClient(base_url=GAMMA_URL, timeout=30)

    async def fetch_markets(self, limit: int = 100, offset: int = 0) -> list[dict]:
        resp = await self.client.get("/markets", params={"limit": limit, "offset": offset})
        resp.raise_for_status()
        return resp.json()

    async def poll_all(self) -> int:
        offset = 0
        total = 0
        while True:
            raw_markets = await self.fetch_markets(limit=100, offset=offset)
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
        log.info("Polled %d markets", total)
        return total

    async def close(self):
        await self.client.aclose()
