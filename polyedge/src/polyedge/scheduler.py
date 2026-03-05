"""Scheduler — runs polling, research, and supergod loops."""

import asyncio
import logging
from datetime import date

from sqlalchemy import select

from polyedge.db import SessionLocal
from polyedge.models import Market, Factor
from polyedge.poller import PolymarketPoller
from polyedge.research.perplexity import query_perplexity
from polyedge.research.grok import query_grok
from polyedge.research.supergod import build_research_prompt, submit_to_supergod
from polyedge.research.ingest import parse_factors_json
from polyedge.analysis.generate import generate_all_predictions
from polyedge.analysis.scorer import score_resolved_markets, recalculate_weights

log = logging.getLogger(__name__)


async def run_poller():
    """Poll Polymarket for market data."""
    poller = PolymarketPoller()
    try:
        count = await poller.poll_all()
        log.info("Poller cycle done: %d markets", count)
    finally:
        await poller.close()


async def run_api_research():
    """Run Perplexity + Grok research sweep."""
    today = date.today().isoformat()

    # Global sweep via Perplexity
    try:
        perp_prompt = build_research_prompt(category="global_sweep", today=today)
        perp_response = await query_perplexity(perp_prompt)
        factors = parse_factors_json(perp_response, source="perplexity")
        await _store_factors(factors)
        log.info("Perplexity global sweep: %d factors", len(factors))
    except Exception as e:
        log.error("Perplexity sweep failed: %s", e)

    # Trending topics via Grok
    try:
        grok_prompt = (
            f"Date: {today}\n"
            "What are the top 20 trending topics on X/Twitter right now? "
            "For each, give the topic, why it's trending, and the general sentiment.\n"
            'Output ONLY valid JSON: {{"factors": [{{"category": "social", "subcategory": "trending", '
            '"name": "<topic>", "value": "<sentiment>", "description": "<why trending>"}}]}}'
        )
        grok_response = await query_grok(grok_prompt)
        factors = parse_factors_json(grok_response, source="grok")
        await _store_factors(factors)
        log.info("Grok trending sweep: %d factors", len(factors))
    except Exception as e:
        log.error("Grok sweep failed: %s", e)

    # Per-market sentiment via Grok for top 5 active markets by volume
    async with SessionLocal() as session:
        top_markets = (await session.execute(
            select(Market).where(Market.active == True).order_by(Market.volume.desc()).limit(5)
        )).scalars().all()

    for market in top_markets:
        try:
            prompt = (
                f'Market: "{market.question}"\nCurrent odds: {round(market.yes_price * 100, 1)}% YES\n\n'
                "What is X/Twitter saying about this? Give 5-10 sentiment signals.\n"
                'Output ONLY valid JSON: {{"factors": [{{"category": "sentiment", "subcategory": "twitter", '
                '"name": "<signal>", "value": "<bullish/bearish/neutral>", "description": "<summary>"}}]}}'
            )
            resp = await query_grok(prompt)
            factors = parse_factors_json(resp, source="grok", market_id=market.id)
            await _store_factors(factors)
        except Exception as e:
            log.error("Grok market sentiment failed for %s: %s", market.id, e)


async def run_supergod_research():
    """Dispatch deep research tasks to supergod workers."""
    async with SessionLocal() as session:
        markets = (await session.execute(
            select(Market).where(Market.active == True).order_by(Market.volume.desc()).limit(8)
        )).scalars().all()

    if not markets:
        log.warning("No active markets to research")
        return

    categories = ["historical_precedent", "contrarian_analysis", "sentiment_deep_dive"]
    tasks_submitted = 0

    for market in markets[:4]:
        cat = categories[tasks_submitted % len(categories)]
        prompt = build_research_prompt(
            category=cat,
            market_question=market.question,
            yes_price=market.yes_price,
        )
        task_id = await submit_to_supergod(prompt)
        if task_id:
            tasks_submitted += 1
            log.info("Dispatched %s research for: %s", cat, market.question[:50])

    # Also dispatch a global sweep
    prompt = build_research_prompt(category="global_sweep", today=date.today().isoformat())
    await submit_to_supergod(prompt)

    log.info("Supergod research cycle: %d tasks submitted", tasks_submitted + 1)


async def _store_factors(factors: list[dict]):
    """Store a batch of parsed factors into the DB."""
    if not factors:
        return
    async with SessionLocal() as session:
        for f in factors:
            session.add(Factor(**f))
        await session.commit()


async def run_forever():
    """Main scheduler loop."""
    log.info("PolyEdge scheduler starting")

    async def loop(fn, interval_seconds: int, name: str):
        while True:
            try:
                await fn()
            except Exception as e:
                log.error("%s failed: %s", name, e, exc_info=True)
            await asyncio.sleep(interval_seconds)

    async def research_then_predict():
        await run_api_research()
        await generate_all_predictions()

    async def supergod_then_predict():
        await run_supergod_research()
        await generate_all_predictions()

    async def poll_then_score():
        await run_poller()
        await score_resolved_markets()

    await asyncio.gather(
        loop(poll_then_score, 300, "poller+scorer"),           # every 5 min
        loop(research_then_predict, 1800, "api_research"),     # every 30 min
        loop(supergod_then_predict, 1800, "supergod"),         # every 30 min
    )
