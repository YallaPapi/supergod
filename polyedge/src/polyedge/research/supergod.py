import json
import logging

log = logging.getLogger(__name__)

PROMPT_TEMPLATES = {
    "historical_precedent": """Market: "{question}"
Current odds: {yes_price_pct}% YES

You are a research analyst. Find 3-5 closest historical parallels to this exact situation.
For each parallel:
1. What was the event?
2. When did it happen?
3. What was the outcome?
4. How similar is it to the current situation (0.0-1.0)?
5. What key differences exist?

Output ONLY valid JSON, no markdown:
{{"factors": [{{"category": "historical", "subcategory": "precedent", "name": "<event>", "value": "<outcome>", "description": "<1-2 sentence summary>", "confidence": <0.0-1.0>}}]}}""",

    "contrarian_analysis": """Market: "{question}"
Current odds: {yes_price_pct}% YES

You are a contrarian analyst. The market consensus is {consensus} at {yes_price_pct}%.
1. What are 3 reasons the market could be WRONG?
2. What information might the market be ignoring?
3. What biases could be inflating/deflating the price?
4. Are there any similar markets where the consensus was wrong?

Output ONLY valid JSON, no markdown:
{{"factors": [{{"category": "contrarian", "subcategory": "<type>", "name": "<factor>", "value": "<assessment>", "description": "<reasoning>", "confidence": <0.0-1.0>}}]}}""",

    "global_sweep": """Date: {today}

You are a data collector. Research what is happening RIGHT NOW globally. For each item, provide a structured factor.

Research ALL of the following categories:
- Weather: any extreme weather events, temperature records, natural disasters
- Financial markets: S&P 500, NASDAQ, BTC, ETH, gold, oil — current direction and magnitude
- Social media: top 5 trending topics on X/Twitter right now
- Celebrity/public figure: any major news about public figures today
- Sports: major game results or upcoming events
- Historical: what notable events happened on this date in history
- Political: any government actions, hearings, votes today
- Global: major international events, conflicts, trade deals
- Cultural: holidays, major releases, conferences happening now
- Unusual: anything weird, unprecedented, or statistically unlikely happening today

Output ONLY valid JSON, no markdown. Aim for 15-30 factors:
{{"factors": [{{"category": "<category>", "subcategory": "<specific>", "name": "<short name>", "value": "<data point>", "description": "<1 sentence>"}}]}}""",

    "sentiment_deep_dive": """Market: "{question}"
Current odds: {yes_price_pct}% YES

You are a sentiment analyst. Research public opinion and expert sentiment on this market.
1. What do mainstream media articles say?
2. What do experts/analysts outside prediction markets think?
3. What is the social media sentiment?
4. Are there any notable public figures who have commented?
5. What is the "vibe" — is this getting more or less attention over time?

Output ONLY valid JSON, no markdown:
{{"factors": [{{"category": "sentiment", "subcategory": "<type>", "name": "<source/person>", "value": "<bullish/bearish/neutral>", "description": "<what they said or the overall tone>", "confidence": <0.0-1.0>}}]}}""",
}


def build_research_prompt(
    category: str,
    market_question: str = "",
    yes_price: float = 0.5,
    today: str = "",
) -> str:
    template = PROMPT_TEMPLATES.get(category)
    if not template:
        raise ValueError(f"Unknown prompt category: {category}")
    yes_price_pct = round(yes_price * 100, 1)
    consensus = "YES" if yes_price > 0.5 else "NO"
    return template.format(
        question=market_question,
        yes_price_pct=yes_price_pct,
        consensus=consensus,
        today=today or "today",
    )


async def submit_to_supergod(prompt: str, orchestrator_url: str = "ws://89.167.99.187:8080") -> str | None:
    import websockets
    try:
        async with websockets.connect(f"{orchestrator_url}/ws/client", close_timeout=10) as ws:
            msg = json.dumps({"type": "task", "prompt": prompt, "priority": 1})
            await ws.send(msg)
            resp = json.loads(await ws.recv())
            if resp.get("type") == "task_accepted":
                task_id = resp.get("task_id")
                log.info("Supergod accepted research task: %s", task_id)
                return task_id
            log.warning("Supergod rejected task: %s", resp)
            return None
    except Exception as e:
        log.error("Failed to submit to supergod: %s", e)
        return None
