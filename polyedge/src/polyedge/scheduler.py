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


CATEGORY_PROMPTS_PERPLEXITY = [
    ("financial", 'Date: {today}\nWhat are the current prices and 24h changes for: S&P 500, NASDAQ, Dow, BTC, ETH, SOL, gold, oil, EUR/USD, 10yr Treasury yield? Also note any major market moves, IPOs, or earnings reports today.\nOutput ONLY valid JSON: {{"factors": [{{"category": "financial", "subcategory": "<asset_or_event>", "name": "<ticker>", "value": "<price and direction e.g. up 2.3%>", "confidence": 0.8}}]}}'),
    ("political", 'Date: {today}\nWhat political events are happening today globally? Congressional votes, executive orders, court rulings, elections, diplomatic meetings, sanctions, trade deals. Cover US, EU, China, Russia, Middle East.\nOutput ONLY valid JSON: {{"factors": [{{"category": "political", "subcategory": "<region>", "name": "<event>", "value": "<likely outcome or status>", "confidence": <0.0-1.0>}}]}}'),
    ("weather_climate", 'Date: {today}\nWhat extreme weather or climate events are happening right now? Hurricanes, wildfires, droughts, floods, heat waves, cold snaps, earthquakes, volcanic activity. Include location and severity.\nOutput ONLY valid JSON: {{"factors": [{{"category": "weather", "subcategory": "<event_type>", "name": "<location>", "value": "<severity and status>", "confidence": <0.0-1.0>}}]}}'),
    ("technology", 'Date: {today}\nWhat major tech news happened in the last 24 hours? AI announcements, product launches, regulatory actions on tech, cybersecurity incidents, startup funding, Big Tech earnings or layoffs.\nOutput ONLY valid JSON: {{"factors": [{{"category": "technology", "subcategory": "<area>", "name": "<company_or_event>", "value": "<what happened>", "confidence": <0.0-1.0>}}]}}'),
    ("geopolitical", 'Date: {today}\nWhat military conflicts, territorial disputes, refugee crises, or international tensions are active right now? Include Ukraine-Russia, Israel-Palestine, Taiwan strait, North Korea, and any other active situations.\nOutput ONLY valid JSON: {{"factors": [{{"category": "geopolitical", "subcategory": "<conflict>", "name": "<development>", "value": "<escalating/de-escalating/stable>", "confidence": <0.0-1.0>}}]}}'),
    ("health_science", 'Date: {today}\nAny major health or science news? Disease outbreaks, drug approvals, clinical trial results, space launches, scientific discoveries, pandemic updates.\nOutput ONLY valid JSON: {{"factors": [{{"category": "health_science", "subcategory": "<area>", "name": "<event>", "value": "<significance>", "confidence": <0.0-1.0>}}]}}'),
    ("sports", 'Date: {today}\nWhat major sports events happened or are happening today? NFL, NBA, soccer, UFC, tennis, F1, esports. Include scores, injuries, trades, suspensions.\nOutput ONLY valid JSON: {{"factors": [{{"category": "sports", "subcategory": "<sport>", "name": "<team_or_event>", "value": "<result or prediction>", "confidence": <0.0-1.0>}}]}}'),
    ("crypto_defi", 'Date: {today}\nWhat is happening in crypto right now? Major token moves, DeFi exploits, regulatory news, ETF flows, whale transactions, new token launches, exchange issues.\nOutput ONLY valid JSON: {{"factors": [{{"category": "crypto", "subcategory": "<area>", "name": "<token_or_event>", "value": "<what happened>", "confidence": <0.0-1.0>}}]}}'),
]

CATEGORY_PROMPTS_GROK = [
    ("social_trending", "What are the top 20 trending topics on X/Twitter RIGHT NOW? For each: the topic, why trending, general sentiment.\n"
     'Output ONLY valid JSON: {{"factors": [{{"category": "social", "subcategory": "trending", "name": "<topic>", "value": "<positive/negative/neutral/mixed>", "confidence": 0.6}}]}}'),
    ("celebrity", "What celebrity or public figure news is trending on X right now? Scandals, deaths, marriages, arrests, viral moments, feuds. 10-15 items.\n"
     'Output ONLY valid JSON: {{"factors": [{{"category": "celebrity", "subcategory": "<type>", "name": "<person>", "value": "<what happened>", "confidence": 0.5}}]}}'),
    ("public_opinion", "What are people on X/Twitter most angry, excited, or worried about today? Give 10 topics with the dominant emotion and whether it's growing or fading.\n"
     'Output ONLY valid JSON: {{"factors": [{{"category": "public_mood", "subcategory": "<emotion>", "name": "<topic>", "value": "<growing/fading/stable>", "confidence": 0.5}}]}}'),
    ("regulatory", "Any government regulatory actions, investigations, or legal rulings being discussed on X right now? SEC, DOJ, FTC, EU, crypto regulation, tech antitrust.\n"
     'Output ONLY valid JSON: {{"factors": [{{"category": "regulatory", "subcategory": "<agency>", "name": "<action>", "value": "<likely outcome>", "confidence": <0.0-1.0>}}]}}'),
]

MARKET_ANALYSIS_PROMPTS = [
    ("expert_opinion", 'Market: "{question}"\nCurrent odds: {pct}% YES\n\nWhat do subject matter experts and analysts think about this? Find 5-10 expert opinions, data points, or official statements directly relevant to this question.\nOutput ONLY valid JSON: {{"factors": [{{"category": "expert", "subcategory": "<field>", "name": "<source>", "value": "<supports YES or NO>", "confidence": <0.0-1.0>}}]}}'),
    ("data_driven", 'Market: "{question}"\nCurrent odds: {pct}% YES\n\nWhat hard data, statistics, or measurable indicators are relevant to predicting this outcome? Base rates, historical frequency, current trends. 5-10 data points.\nOutput ONLY valid JSON: {{"factors": [{{"category": "data", "subcategory": "<metric_type>", "name": "<indicator>", "value": "<supports YES or NO with number>", "confidence": <0.0-1.0>}}]}}'),
    ("timeline", 'Market: "{question}"\nCurrent odds: {pct}% YES\n\nWhat upcoming events, deadlines, or milestones could affect this market? Scheduled dates, decision points, catalysts. List 5-10.\nOutput ONLY valid JSON: {{"factors": [{{"category": "timeline", "subcategory": "<type>", "name": "<event>", "value": "<date and likely impact YES/NO>", "confidence": <0.0-1.0>}}]}}'),
]


async def run_api_research():
    """Run diverse research across many categories via Perplexity + Grok."""
    today = date.today().isoformat()

    # Category-specific sweeps via Perplexity (8 prompts)
    for cat_name, prompt_template in CATEGORY_PROMPTS_PERPLEXITY:
        try:
            prompt = prompt_template.format(today=today)
            resp = await query_perplexity(prompt)
            factors = parse_factors_json(resp, source="perplexity")
            await _store_factors(factors)
            log.info("Perplexity %s: %d factors", cat_name, len(factors))
        except Exception as e:
            log.error("Perplexity %s failed: %s", cat_name, e)

    # Category-specific sweeps via Grok (4 prompts)
    for cat_name, prompt_template in CATEGORY_PROMPTS_GROK:
        try:
            prompt = f"Date: {today}\n{prompt_template}"
            resp = await query_grok(prompt)
            factors = parse_factors_json(resp, source="grok")
            await _store_factors(factors)
            log.info("Grok %s: %d factors", cat_name, len(factors))
        except Exception as e:
            log.error("Grok %s failed: %s", cat_name, e)

    # Market-specific analysis for top 10 markets (3 prompts each = 30 API calls)
    async with SessionLocal() as session:
        top_markets = (await session.execute(
            select(Market).where(Market.active == True).order_by(Market.volume.desc()).limit(10)
        )).scalars().all()

    for market in top_markets:
        for prompt_name, prompt_template in MARKET_ANALYSIS_PROMPTS:
            try:
                prompt = prompt_template.format(
                    question=market.question,
                    pct=round(market.yes_price * 100, 1),
                )
                # Alternate between APIs to spread the load
                if prompt_name == "expert_opinion":
                    resp = await query_perplexity(prompt)
                    factors = parse_factors_json(resp, source="perplexity", market_id=market.id)
                else:
                    resp = await query_grok(prompt)
                    factors = parse_factors_json(resp, source="grok", market_id=market.id)
                await _store_factors(factors)
            except Exception as e:
                log.error("Market %s %s failed: %s", market.id[:8], prompt_name, e)


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

    async def poll_then_score():
        await run_poller()
        await score_resolved_markets()

    await asyncio.gather(
        loop(poll_then_score, 300, "poller+scorer"),           # every 5 min
        loop(run_api_research, 1800, "api_research"),          # every 30 min
        loop(run_supergod_research, 1800, "supergod"),         # every 30 min
        loop(generate_all_predictions, 3600, "predictions"),   # every 1 hour, ONCE
    )
