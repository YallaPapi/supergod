# PolyEdge Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a Polymarket prediction system that continuously researches factors, predicts every market, and tracks hit rates to find an edge.

**Architecture:** Core FastAPI service polls Polymarket, stores data in PostgreSQL. Scheduler dispatches research to supergod workers + Perplexity + Grok APIs. Correlation engine scores factor categories by prediction accuracy.

**Tech Stack:** Python 3.12+, FastAPI, PostgreSQL, SQLAlchemy, APScheduler, httpx, pandas, React (dashboard)

---

## Task 1: Project Scaffold & Database Schema

**Files:**
- Create: `polyedge/pyproject.toml`
- Create: `polyedge/src/polyedge/__init__.py`
- Create: `polyedge/src/polyedge/db.py`
- Create: `polyedge/src/polyedge/models.py`
- Create: `polyedge/alembic.ini`
- Create: `polyedge/alembic/env.py`
- Test: `polyedge/tests/test_models.py`

This is a NEW repo/directory at the project root level, separate from the supergod codebase.

**Step 1: Create project structure**

```
polyedge/
├── pyproject.toml
├── alembic.ini
├── alembic/
│   └── env.py
├── src/polyedge/
│   ├── __init__.py
│   ├── db.py
│   └── models.py
├── prompts/
│   └── (empty, populated in Task 6)
└── tests/
    └── test_models.py
```

`pyproject.toml`:
```toml
[project]
name = "polyedge"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.34",
    "sqlalchemy[asyncio]>=2.0",
    "asyncpg>=0.30",
    "alembic>=1.14",
    "httpx>=0.28",
    "apscheduler>=3.10",
    "pandas>=2.2",
    "numpy>=2.0",
    "click>=8.1",
    "pydantic>=2.10",
    "pydantic-settings>=2.7",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-asyncio>=0.25", "pytest-httpx>=0.35"]

[build-system]
requires = ["setuptools>=75"]
build-backend = "setuptools.backends._legacy:_Backend"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

**Step 2: Write database models**

`src/polyedge/db.py` — async engine + session factory:
```python
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://polyedge:polyedge@localhost:5432/polyedge"
    perplexity_api_key: str = ""
    grok_api_key: str = ""

    class Config:
        env_prefix = "POLYEDGE_"

settings = Settings()
engine = create_async_engine(settings.database_url)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
```

`src/polyedge/models.py` — SQLAlchemy models:
```python
from datetime import datetime
from sqlalchemy import String, Float, DateTime, Boolean, Text, Integer, ForeignKey, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
import uuid

class Base(DeclarativeBase):
    pass

class Market(Base):
    __tablename__ = "markets"
    id: Mapped[str] = mapped_column(String, primary_key=True)  # polymarket ID
    question: Mapped[str] = mapped_column(Text)
    slug: Mapped[str] = mapped_column(String, index=True)
    category: Mapped[str] = mapped_column(String, index=True, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    end_date: Mapped[datetime | None] = mapped_column(DateTime)
    yes_price: Mapped[float] = mapped_column(Float, default=0.5)
    no_price: Mapped[float] = mapped_column(Float, default=0.5)
    volume: Mapped[float] = mapped_column(Float, default=0)
    liquidity: Mapped[float] = mapped_column(Float, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    resolution: Mapped[str | None] = mapped_column(String)  # "YES" / "NO" / None
    clob_token_ids: Mapped[str] = mapped_column(Text, default="")  # JSON array as string
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_id: Mapped[str] = mapped_column(String, ForeignKey("markets.id"), index=True)
    yes_price: Mapped[float] = mapped_column(Float)
    no_price: Mapped[float] = mapped_column(Float)
    volume_24h: Mapped[float] = mapped_column(Float, default=0)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

class Factor(Base):
    __tablename__ = "factors"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    market_id: Mapped[str | None] = mapped_column(String, ForeignKey("markets.id"), index=True)  # None = global factor
    category: Mapped[str] = mapped_column(String, index=True)  # "weather", "financial", "historical", etc.
    subcategory: Mapped[str] = mapped_column(String, default="")
    name: Mapped[str] = mapped_column(String)  # "S&P 500 daily change"
    value: Mapped[str] = mapped_column(Text)  # "+2.3%" or "heavy rain in DC" — always string for flexibility
    source: Mapped[str] = mapped_column(String)  # "perplexity", "grok", "codex"
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    __table_args__ = (Index("ix_factor_cat_ts", "category", "timestamp"),)

class Prediction(Base):
    __tablename__ = "predictions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    market_id: Mapped[str] = mapped_column(String, ForeignKey("markets.id"), index=True)
    predicted_outcome: Mapped[str] = mapped_column(String)  # "YES" or "NO"
    confidence: Mapped[float] = mapped_column(Float)  # 0.0 - 1.0
    entry_yes_price: Mapped[float] = mapped_column(Float)  # price when prediction was made
    factor_ids: Mapped[str] = mapped_column(Text, default="")  # JSON array of factor IDs that informed this
    factor_categories: Mapped[str] = mapped_column(Text, default="")  # JSON array of categories used
    correct: Mapped[bool | None] = mapped_column(Boolean)  # None until resolved
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)

class FactorWeight(Base):
    __tablename__ = "factor_weights"
    category: Mapped[str] = mapped_column(String, primary_key=True)
    total_predictions: Mapped[int] = mapped_column(Integer, default=0)
    correct_predictions: Mapped[int] = mapped_column(Integer, default=0)
    hit_rate: Mapped[float] = mapped_column(Float, default=0.5)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
```

**Step 3: Write test**

```python
# tests/test_models.py
from polyedge.models import Market, Factor, Prediction, FactorWeight, PriceSnapshot, Base

def test_all_models_have_tablename():
    for model in [Market, Factor, Prediction, FactorWeight, PriceSnapshot]:
        assert hasattr(model, "__tablename__")

def test_market_defaults():
    m = Market(id="test", question="Will X?", slug="will-x")
    assert m.yes_price == 0.5
    assert m.active is True
    assert m.resolved is False

def test_factor_allows_null_market():
    f = Factor(category="weather", name="temp", value="hot", source="grok")
    assert f.market_id is None
```

**Step 4: Run tests**

```bash
cd polyedge && pip install -e ".[dev]" && pytest tests/test_models.py -v
```

**Step 5: Set up alembic and generate initial migration**

```bash
cd polyedge && alembic init alembic
# Edit alembic/env.py to use async engine and Base.metadata
alembic revision --autogenerate -m "initial schema"
```

**Step 6: Commit**

```bash
git add polyedge/
git commit -m "feat(polyedge): scaffold project with DB models

Markets, factors, predictions, price snapshots, factor weights.
PostgreSQL via SQLAlchemy async."
```

---

## Task 2: Polymarket Poller

**Files:**
- Create: `polyedge/src/polyedge/poller.py`
- Test: `polyedge/tests/test_poller.py`

Polls `https://gamma-api.polymarket.com/markets` every 5 minutes. Upserts markets, records price snapshots, detects newly resolved markets.

**Step 1: Write failing test**

```python
# tests/test_poller.py
import pytest
from polyedge.poller import parse_market, PolymarketPoller

SAMPLE_MARKET = {
    "id": "12345",
    "question": "Will Bitcoin hit $100k by July 2026?",
    "slug": "will-bitcoin-hit-100k",
    "category": "Crypto",
    "description": "Market on BTC price target",
    "endDate": "2026-07-01T00:00:00Z",
    "outcomePrices": ["0.65", "0.35"],
    "volume": "500000",
    "liquidity": "50000",
    "active": True,
    "closed": False,
    "clobTokenIds": ["tok_yes", "tok_no"],
    "volume24hr": "12000",
}

def test_parse_market():
    m = parse_market(SAMPLE_MARKET)
    assert m["id"] == "12345"
    assert m["yes_price"] == 0.65
    assert m["no_price"] == 0.35
    assert m["active"] is True
    assert m["resolved"] is False

def test_parse_resolved_market():
    resolved = {**SAMPLE_MARKET, "closed": True, "active": False}
    m = parse_market(resolved)
    assert m["resolved"] is True
    assert m["active"] is False
```

**Step 2: Run test — should fail**

```bash
pytest tests/test_poller.py -v
```

**Step 3: Implement poller**

```python
# src/polyedge/poller.py
import logging
from datetime import datetime
import httpx
from sqlalchemy import select
from polyedge.db import SessionLocal
from polyedge.models import Market, PriceSnapshot

log = logging.getLogger(__name__)
GAMMA_URL = "https://gamma-api.polymarket.com"

def parse_market(raw: dict) -> dict:
    prices = raw.get("outcomePrices", ["0.5", "0.5"])
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
        """Fetch all markets, upsert into DB, record price snapshots. Returns count."""
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
                        was_active = existing.active
                        for k, v in parsed.items():
                            setattr(existing, k, v)
                        existing.updated_at = datetime.utcnow()
                        # Detect resolution
                        if was_active and existing.resolved:
                            log.info("Market resolved: %s -> %s", existing.question[:60], existing.resolution)
                    else:
                        session.add(Market(**parsed))
                    # Price snapshot
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
```

**Step 4: Run tests**

```bash
pytest tests/test_poller.py -v
```

**Step 5: Commit**

```bash
git add polyedge/src/polyedge/poller.py polyedge/tests/test_poller.py
git commit -m "feat(polyedge): Polymarket poller — fetches markets, upserts, snapshots prices"
```

---

## Task 3: Perplexity & Grok Research Clients

**Files:**
- Create: `polyedge/src/polyedge/research/perplexity.py`
- Create: `polyedge/src/polyedge/research/grok.py`
- Create: `polyedge/src/polyedge/research/__init__.py`
- Create: `polyedge/src/polyedge/research/ingest.py`
- Test: `polyedge/tests/test_research.py`

**Step 1: Write failing tests**

```python
# tests/test_research.py
import json
from polyedge.research.ingest import parse_factors_json

def test_parse_factors_json_valid():
    raw = json.dumps({"factors": [
        {"category": "weather", "name": "NYC temp", "value": "92F", "description": "Heat wave"},
        {"category": "financial", "name": "S&P 500", "value": "+1.2%", "description": "Rally"},
    ]})
    factors = parse_factors_json(raw, source="perplexity")
    assert len(factors) == 2
    assert factors[0]["category"] == "weather"
    assert factors[0]["source"] == "perplexity"

def test_parse_factors_json_extracts_from_markdown():
    raw = "Here are the factors:\n```json\n" + json.dumps({"factors": [
        {"category": "sports", "name": "NBA finals", "value": "Lakers won", "description": "Game 7"}
    ]}) + "\n```\nHope that helps!"
    factors = parse_factors_json(raw, source="grok")
    assert len(factors) == 1
    assert factors[0]["source"] == "grok"

def test_parse_factors_json_handles_garbage():
    factors = parse_factors_json("I don't know what you mean", source="grok")
    assert factors == []
```

**Step 2: Implement**

`src/polyedge/research/ingest.py`:
```python
import json
import re
import logging

log = logging.getLogger(__name__)

def parse_factors_json(text: str, source: str, market_id: str | None = None) -> list[dict]:
    """Extract factor list from LLM response text. Handles raw JSON, markdown fences, and garbage."""
    text = text.strip()
    # Try direct parse
    for attempt_text in [text, _extract_fenced(text)]:
        if not attempt_text:
            continue
        try:
            data = json.loads(attempt_text)
            if isinstance(data, dict) and "factors" in data:
                return _normalize(data["factors"], source, market_id)
            if isinstance(data, list):
                return _normalize(data, source, market_id)
        except json.JSONDecodeError:
            continue
    # Try regex for JSON object
    match = re.search(r'\{[^{}]*"factors"\s*:\s*\[.*?\]\s*\}', text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group())
            return _normalize(data.get("factors", []), source, market_id)
        except json.JSONDecodeError:
            pass
    log.warning("Could not parse factors from %s response (%d chars)", source, len(text))
    return []

def _extract_fenced(text: str) -> str | None:
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    return match.group(1).strip() if match else None

def _normalize(items: list, source: str, market_id: str | None) -> list[dict]:
    factors = []
    for item in items:
        if not isinstance(item, dict):
            continue
        factors.append({
            "market_id": market_id,
            "category": item.get("category", "unknown"),
            "subcategory": item.get("subcategory", ""),
            "name": item.get("name", "unnamed"),
            "value": str(item.get("value", "")),
            "source": source,
            "confidence": float(item.get("confidence", 0.5)),
        })
    return factors
```

`src/polyedge/research/perplexity.py`:
```python
import httpx
import logging
from polyedge.db import settings

log = logging.getLogger(__name__)

PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"

async def query_perplexity(prompt: str, model: str = "sonar") -> str:
    """Send a prompt to Perplexity and return the text response."""
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            PERPLEXITY_URL,
            headers={"Authorization": f"Bearer {settings.perplexity_api_key}"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
```

`src/polyedge/research/grok.py`:
```python
import httpx
import logging
from polyedge.db import settings

log = logging.getLogger(__name__)

GROK_URL = "https://api.x.ai/v1/chat/completions"

async def query_grok(prompt: str, model: str = "grok-3-mini") -> str:
    """Send a prompt to Grok and return the text response."""
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            GROK_URL,
            headers={"Authorization": f"Bearer {settings.grok_api_key}"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
```

**Step 3: Run tests**

```bash
pytest tests/test_research.py -v
```

**Step 4: Commit**

```bash
git add polyedge/src/polyedge/research/ polyedge/tests/test_research.py
git commit -m "feat(polyedge): Perplexity + Grok clients and factor JSON parser"
```

---

## Task 4: Supergod Research Dispatcher

**Files:**
- Create: `polyedge/src/polyedge/research/supergod.py`
- Test: `polyedge/tests/test_supergod_dispatch.py`

This submits research tasks to the supergod orchestrator via WebSocket (same protocol the CLI uses). Each task is a structured prompt that tells a Codex worker to research a specific market or factor category and return JSON.

**Step 1: Write test**

```python
# tests/test_supergod_dispatch.py
from polyedge.research.supergod import build_research_prompt

def test_build_research_prompt_market():
    prompt = build_research_prompt(
        market_question="Will Bitcoin hit $100k by July?",
        yes_price=0.65,
        category="historical_precedent",
    )
    assert "Will Bitcoin hit $100k by July?" in prompt
    assert "65%" in prompt or "0.65" in prompt
    assert "JSON" in prompt

def test_build_research_prompt_global_sweep():
    prompt = build_research_prompt(category="global_sweep")
    assert "weather" in prompt.lower()
    assert "JSON" in prompt
```

**Step 2: Implement**

```python
# src/polyedge/research/supergod.py
import json
import logging
import httpx

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
    """Submit a research task to supergod orchestrator. Returns task_id or None on failure."""
    import websockets
    try:
        async with websockets.connect(f"{orchestrator_url}/ws/client", close_timeout=10) as ws:
            msg = json.dumps({"type": "task", "prompt": prompt, "priority": 1})
            await ws.send(msg)
            # Wait for acceptance
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
```

**Step 3: Run tests**

```bash
pytest tests/test_supergod_dispatch.py -v
```

**Step 4: Commit**

```bash
git add polyedge/src/polyedge/research/supergod.py polyedge/tests/test_supergod_dispatch.py
git commit -m "feat(polyedge): supergod research dispatcher with 4 prompt templates"
```

---

## Task 5: Scheduler — Ties Everything Together

**Files:**
- Create: `polyedge/src/polyedge/scheduler.py`
- Test: `polyedge/tests/test_scheduler.py`

The scheduler runs three loops:
1. Poll Polymarket every 5 min
2. Perplexity + Grok research every 30 min
3. Supergod deep research every 30 min

**Step 1: Implement scheduler**

```python
# src/polyedge/scheduler.py
import asyncio
import json
import logging
from datetime import datetime, date
from sqlalchemy import select, and_

from polyedge.db import SessionLocal
from polyedge.models import Market, Factor
from polyedge.poller import PolymarketPoller
from polyedge.research.perplexity import query_perplexity
from polyedge.research.grok import query_grok
from polyedge.research.supergod import build_research_prompt, submit_to_supergod
from polyedge.research.ingest import parse_factors_json

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
        # Pick top active markets by volume that have fewest recent factors
        markets = (await session.execute(
            select(Market).where(Market.active == True).order_by(Market.volume.desc()).limit(8)
        )).scalars().all()

    if not markets:
        log.warning("No active markets to research")
        return

    categories = ["historical_precedent", "contrarian_analysis", "sentiment_deep_dive"]
    tasks_submitted = 0

    for market in markets[:4]:  # 4 markets per cycle = 4 workers
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

    await asyncio.gather(
        loop(run_poller, 300, "poller"),           # every 5 min
        loop(run_api_research, 1800, "api_research"),  # every 30 min
        loop(run_supergod_research, 1800, "supergod"),  # every 30 min
    )
```

**Step 2: Write basic test**

```python
# tests/test_scheduler.py
from polyedge.scheduler import run_poller, run_api_research, run_supergod_research

def test_scheduler_functions_exist():
    """Smoke test that all scheduler functions are importable."""
    assert callable(run_poller)
    assert callable(run_api_research)
    assert callable(run_supergod_research)
```

**Step 3: Run tests**

```bash
pytest tests/test_scheduler.py -v
```

**Step 4: Commit**

```bash
git add polyedge/src/polyedge/scheduler.py polyedge/tests/test_scheduler.py
git commit -m "feat(polyedge): scheduler — polls markets, dispatches research every 30 min"
```

---

## Task 6: Prediction Engine & Scoring

**Files:**
- Create: `polyedge/src/polyedge/analysis/predictor.py`
- Create: `polyedge/src/polyedge/analysis/scorer.py`
- Create: `polyedge/src/polyedge/analysis/__init__.py`
- Test: `polyedge/tests/test_analysis.py`

**Step 1: Write failing tests**

```python
# tests/test_analysis.py
from polyedge.analysis.predictor import make_prediction
from polyedge.analysis.scorer import score_category

def test_make_prediction_with_factors():
    factors = [
        {"category": "historical", "value": "YES in 4/5 cases", "confidence": 0.8},
        {"category": "sentiment", "value": "bullish", "confidence": 0.7},
        {"category": "weather", "value": "sunny", "confidence": 0.3},
    ]
    pred = make_prediction(factors, current_yes_price=0.5)
    assert pred["predicted_outcome"] in ("YES", "NO")
    assert 0.0 <= pred["confidence"] <= 1.0
    assert len(pred["factor_categories"]) > 0

def test_make_prediction_no_factors():
    pred = make_prediction([], current_yes_price=0.6)
    # With no factors, just go with market price
    assert pred["predicted_outcome"] == "YES"
    assert pred["confidence"] < 0.6  # low confidence

def test_score_category():
    results = score_category(correct=30, total=50)
    assert results["hit_rate"] == 0.6
    assert results["total_predictions"] == 50
```

**Step 2: Implement**

`src/polyedge/analysis/predictor.py`:
```python
"""Generate predictions for markets based on accumulated factors."""

def make_prediction(
    factors: list[dict],
    current_yes_price: float,
    factor_weights: dict[str, float] | None = None,
) -> dict:
    """Generate a prediction from a list of factors.

    Simple weighted voting: each factor votes YES or NO based on its value/confidence.
    Factor category weights amplify or dampen votes from categories with proven track records.
    """
    if not factors:
        return {
            "predicted_outcome": "YES" if current_yes_price > 0.5 else "NO",
            "confidence": 0.3,  # low confidence with no research
            "factor_categories": [],
        }

    weights = factor_weights or {}
    yes_score = 0.0
    no_score = 0.0
    categories_used = set()

    for f in factors:
        cat = f.get("category", "unknown")
        conf = f.get("confidence", 0.5)
        cat_weight = weights.get(cat, 1.0)
        vote_strength = conf * cat_weight

        value = str(f.get("value", "")).lower()
        # Simple heuristic: bullish/yes/positive/up -> YES, else -> NO
        yes_signals = ["yes", "bullish", "positive", "up", "likely", "probable", "true", "support"]
        no_signals = ["no", "bearish", "negative", "down", "unlikely", "improbable", "false", "oppose"]

        if any(sig in value for sig in yes_signals):
            yes_score += vote_strength
        elif any(sig in value for sig in no_signals):
            no_score += vote_strength
        else:
            # Ambiguous — small push toward market consensus
            if current_yes_price > 0.5:
                yes_score += vote_strength * 0.1
            else:
                no_score += vote_strength * 0.1

        categories_used.add(cat)

    total = yes_score + no_score
    if total == 0:
        confidence = 0.3
        outcome = "YES" if current_yes_price > 0.5 else "NO"
    else:
        yes_pct = yes_score / total
        outcome = "YES" if yes_pct > 0.5 else "NO"
        confidence = abs(yes_pct - 0.5) * 2  # 0.0 at 50/50, 1.0 at unanimous

    return {
        "predicted_outcome": outcome,
        "confidence": round(confidence, 3),
        "factor_categories": sorted(categories_used),
    }
```

`src/polyedge/analysis/scorer.py`:
```python
"""Score predictions and update factor category weights."""
import logging
from datetime import datetime
from sqlalchemy import select, update
from polyedge.db import SessionLocal
from polyedge.models import Prediction, Market, FactorWeight

log = logging.getLogger(__name__)

def score_category(correct: int, total: int) -> dict:
    hit_rate = correct / total if total > 0 else 0.5
    return {
        "hit_rate": round(hit_rate, 4),
        "total_predictions": total,
        "correct_predictions": correct,
        "weight": _hit_rate_to_weight(hit_rate, total),
    }

def _hit_rate_to_weight(hit_rate: float, sample_size: int) -> float:
    """Convert hit rate to a weight. Require minimum sample size for confidence."""
    if sample_size < 10:
        return 1.0  # not enough data, neutral weight
    if hit_rate <= 0.5:
        return 0.1  # coin flip or worse — nearly ignore
    return 1.0 + (hit_rate - 0.5) * 4  # 55% -> 1.2, 60% -> 1.4, 75% -> 2.0


async def score_resolved_markets():
    """Check for newly resolved markets and score their predictions."""
    async with SessionLocal() as session:
        # Find predictions that haven't been scored yet on resolved markets
        stmt = (
            select(Prediction, Market)
            .join(Market, Prediction.market_id == Market.id)
            .where(Market.resolved == True)
            .where(Prediction.correct == None)
        )
        results = (await session.execute(stmt)).all()

        if not results:
            return

        for pred, market in results:
            if not market.resolution:
                continue
            pred.correct = pred.predicted_outcome == market.resolution
            pred.resolved_at = datetime.utcnow()

        await session.commit()
        log.info("Scored %d predictions", len(results))

    # Recalculate category weights
    await recalculate_weights()


async def recalculate_weights():
    """Recalculate factor category weights based on all scored predictions."""
    async with SessionLocal() as session:
        scored = (await session.execute(
            select(Prediction).where(Prediction.correct != None)
        )).scalars().all()

    # Aggregate by category
    cat_stats: dict[str, dict] = {}
    for pred in scored:
        import json
        cats = json.loads(pred.factor_categories) if pred.factor_categories else []
        for cat in cats:
            if cat not in cat_stats:
                cat_stats[cat] = {"correct": 0, "total": 0}
            cat_stats[cat]["total"] += 1
            if pred.correct:
                cat_stats[cat]["correct"] += 1

    # Upsert weights
    async with SessionLocal() as session:
        for cat, stats in cat_stats.items():
            scores = score_category(stats["correct"], stats["total"])
            existing = await session.get(FactorWeight, cat)
            if existing:
                for k, v in scores.items():
                    setattr(existing, k, v)
                existing.updated_at = datetime.utcnow()
            else:
                session.add(FactorWeight(category=cat, **scores))
        await session.commit()

    log.info("Recalculated weights for %d categories", len(cat_stats))
```

**Step 3: Run tests**

```bash
pytest tests/test_analysis.py -v
```

**Step 4: Commit**

```bash
git add polyedge/src/polyedge/analysis/ polyedge/tests/test_analysis.py
git commit -m "feat(polyedge): prediction engine + factor category scoring"
```

---

## Task 7: FastAPI Service & CLI

**Files:**
- Create: `polyedge/src/polyedge/app.py`
- Create: `polyedge/src/polyedge/cli.py`
- Test: `polyedge/tests/test_app.py`

**Step 1: Implement FastAPI app**

```python
# src/polyedge/app.py
import json
from datetime import datetime
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, func, desc
from polyedge.db import SessionLocal
from polyedge.models import Market, Factor, Prediction, FactorWeight, PriceSnapshot

app = FastAPI(title="PolyEdge")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.get("/api/markets")
async def list_markets(active: bool = True, limit: int = 50):
    async with SessionLocal() as session:
        q = select(Market).order_by(desc(Market.volume)).limit(limit)
        if active:
            q = q.where(Market.active == True)
        markets = (await session.execute(q)).scalars().all()
        return [_market_dict(m) for m in markets]

@app.get("/api/markets/{market_id}")
async def get_market(market_id: str):
    async with SessionLocal() as session:
        market = await session.get(Market, market_id)
        if not market:
            return {"error": "not found"}
        factors = (await session.execute(
            select(Factor).where(Factor.market_id == market_id).order_by(desc(Factor.timestamp)).limit(50)
        )).scalars().all()
        predictions = (await session.execute(
            select(Prediction).where(Prediction.market_id == market_id).order_by(desc(Prediction.created_at))
        )).scalars().all()
        return {
            **_market_dict(market),
            "factors": [_factor_dict(f) for f in factors],
            "predictions": [_pred_dict(p) for p in predictions],
        }

@app.get("/api/factors/weights")
async def factor_weights():
    async with SessionLocal() as session:
        weights = (await session.execute(
            select(FactorWeight).order_by(desc(FactorWeight.hit_rate))
        )).scalars().all()
        return [{"category": w.category, "hit_rate": w.hit_rate, "total": w.total_predictions,
                 "correct": w.correct_predictions, "weight": w.weight} for w in weights]

@app.get("/api/stats")
async def stats():
    async with SessionLocal() as session:
        total_markets = (await session.execute(select(func.count(Market.id)))).scalar()
        active_markets = (await session.execute(select(func.count(Market.id)).where(Market.active == True))).scalar()
        total_factors = (await session.execute(select(func.count(Factor.id)))).scalar()
        total_predictions = (await session.execute(select(func.count(Prediction.id)))).scalar()
        correct = (await session.execute(select(func.count(Prediction.id)).where(Prediction.correct == True))).scalar()
        scored = (await session.execute(select(func.count(Prediction.id)).where(Prediction.correct != None))).scalar()
        return {
            "total_markets": total_markets, "active_markets": active_markets,
            "total_factors": total_factors, "total_predictions": total_predictions,
            "scored_predictions": scored, "correct_predictions": correct,
            "overall_hit_rate": round(correct / scored, 4) if scored else None,
        }

def _market_dict(m):
    return {"id": m.id, "question": m.question, "category": m.category,
            "yes_price": m.yes_price, "volume": m.volume, "active": m.active,
            "resolved": m.resolved, "resolution": m.resolution, "end_date": str(m.end_date)}

def _factor_dict(f):
    return {"id": f.id, "category": f.category, "name": f.name, "value": f.value,
            "source": f.source, "confidence": f.confidence, "timestamp": str(f.timestamp)}

def _pred_dict(p):
    return {"id": p.id, "predicted_outcome": p.predicted_outcome, "confidence": p.confidence,
            "entry_yes_price": p.entry_yes_price, "correct": p.correct, "created_at": str(p.created_at)}
```

`src/polyedge/cli.py`:
```python
import asyncio
import click
import logging

@click.group()
def cli():
    """PolyEdge — Polymarket prediction engine."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

@cli.command()
def serve():
    """Run the FastAPI server."""
    import uvicorn
    uvicorn.run("polyedge.app:app", host="0.0.0.0", port=8090, reload=False)

@cli.command()
def run():
    """Run the scheduler (poller + research + predictions)."""
    from polyedge.scheduler import run_forever
    asyncio.run(run_forever())

@cli.command()
def poll():
    """Run a single poll cycle."""
    from polyedge.poller import PolymarketPoller
    async def _poll():
        p = PolymarketPoller()
        count = await p.poll_all()
        click.echo(f"Polled {count} markets")
        await p.close()
    asyncio.run(_poll())

@cli.command()
def stats():
    """Show current stats."""
    from polyedge.db import SessionLocal
    from polyedge.models import Market, Factor, Prediction
    from sqlalchemy import select, func
    async def _stats():
        async with SessionLocal() as session:
            markets = (await session.execute(select(func.count(Market.id)))).scalar()
            active = (await session.execute(select(func.count(Market.id)).where(Market.active == True))).scalar()
            factors = (await session.execute(select(func.count(Factor.id)))).scalar()
            preds = (await session.execute(select(func.count(Prediction.id)))).scalar()
            click.echo(f"Markets: {markets} ({active} active)")
            click.echo(f"Factors: {factors}")
            click.echo(f"Predictions: {preds}")
    asyncio.run(_stats())

if __name__ == "__main__":
    cli()
```

Add to `pyproject.toml`:
```toml
[project.scripts]
polyedge = "polyedge.cli:cli"
```

**Step 2: Write test**

```python
# tests/test_app.py
from fastapi.testclient import TestClient
from polyedge.app import app

def test_stats_endpoint():
    # Will fail without DB but confirms the route exists
    client = TestClient(app)
    # Just test the route is registered
    assert app.url_path_for("stats") == "/api/stats"

def test_markets_endpoint_registered():
    assert app.url_path_for("list_markets") == "/api/markets"

def test_factor_weights_endpoint_registered():
    assert app.url_path_for("factor_weights") == "/api/factors/weights"
```

**Step 3: Run tests**

```bash
pytest tests/test_app.py -v
```

**Step 4: Commit**

```bash
git add polyedge/src/polyedge/app.py polyedge/src/polyedge/cli.py polyedge/tests/test_app.py
git commit -m "feat(polyedge): FastAPI server + CLI (serve, run, poll, stats)"
```

---

## Task 8: Prediction Generation Loop

**Files:**
- Modify: `polyedge/src/polyedge/scheduler.py` — add prediction generation
- Create: `polyedge/src/polyedge/analysis/generate.py`
- Test: `polyedge/tests/test_generate.py`

After every research cycle, generate predictions for ALL active markets that have factors but no prediction in the last hour.

**Step 1: Implement**

```python
# src/polyedge/analysis/generate.py
import json
import logging
from datetime import datetime, timedelta
from sqlalchemy import select, and_
from polyedge.db import SessionLocal
from polyedge.models import Market, Factor, Prediction, FactorWeight
from polyedge.analysis.predictor import make_prediction

log = logging.getLogger(__name__)

async def generate_all_predictions():
    """Generate predictions for every active market with recent factors."""
    async with SessionLocal() as session:
        # Load factor weights
        weights_rows = (await session.execute(select(FactorWeight))).scalars().all()
        weights = {w.category: w.weight for w in weights_rows}

        # Get active markets
        markets = (await session.execute(
            select(Market).where(Market.active == True)
        )).scalars().all()

        count = 0
        for market in markets:
            # Get factors from last 24h for this market + global factors
            cutoff = datetime.utcnow() - timedelta(hours=24)
            factors_rows = (await session.execute(
                select(Factor).where(
                    and_(
                        Factor.timestamp > cutoff,
                        (Factor.market_id == market.id) | (Factor.market_id == None),
                    )
                )
            )).scalars().all()

            if not factors_rows:
                continue

            factors = [{"category": f.category, "value": f.value, "confidence": f.confidence} for f in factors_rows]
            factor_ids = [f.id for f in factors_rows]
            categories = list({f.category for f in factors_rows})

            result = make_prediction(factors, market.yes_price, weights)

            session.add(Prediction(
                market_id=market.id,
                predicted_outcome=result["predicted_outcome"],
                confidence=result["confidence"],
                entry_yes_price=market.yes_price,
                factor_ids=json.dumps(factor_ids[:100]),  # cap at 100 to avoid huge rows
                factor_categories=json.dumps(categories),
            ))
            count += 1

        await session.commit()
        log.info("Generated %d predictions", count)
```

**Step 2: Add to scheduler** — add `generate_all_predictions` call after each research cycle, and `score_resolved_markets` as a nightly job.

**Step 3: Test**

```python
# tests/test_generate.py
from polyedge.analysis.generate import generate_all_predictions

def test_generate_is_callable():
    assert callable(generate_all_predictions)
```

**Step 4: Commit**

```bash
git add polyedge/src/polyedge/analysis/generate.py polyedge/tests/test_generate.py polyedge/src/polyedge/scheduler.py
git commit -m "feat(polyedge): prediction generation loop — predicts every active market"
```

---

## Task 9: Deploy to Hetzner

**Files:**
- Create: `polyedge/deploy/setup.sh`
- Create: `polyedge/deploy/polyedge.service`

**Step 1: Write systemd service files**

`deploy/polyedge.service` (scheduler + data collection):
```ini
[Unit]
Description=PolyEdge Scheduler
After=postgresql.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/polyedge
ExecStart=/opt/polyedge/.venv/bin/polyedge run
Restart=always
RestartSec=10
Environment=POLYEDGE_DATABASE_URL=postgresql+asyncpg://polyedge:polyedge@localhost:5432/polyedge
Environment=POLYEDGE_PERPLEXITY_API_KEY=
Environment=POLYEDGE_GROK_API_KEY=

[Install]
WantedBy=multi-user.target
```

`deploy/polyedge-api.service` (web dashboard):
```ini
[Unit]
Description=PolyEdge API
After=postgresql.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/polyedge
ExecStart=/opt/polyedge/.venv/bin/polyedge serve
Restart=always
RestartSec=10
Environment=POLYEDGE_DATABASE_URL=postgresql+asyncpg://polyedge:polyedge@localhost:5432/polyedge

[Install]
WantedBy=multi-user.target
```

`deploy/setup.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail

# Install PostgreSQL if not present
if ! command -v psql &>/dev/null; then
    apt-get update && apt-get install -y postgresql postgresql-contrib
    systemctl enable postgresql && systemctl start postgresql
fi

# Create DB and user
sudo -u postgres psql -c "CREATE USER polyedge WITH PASSWORD 'polyedge';" 2>/dev/null || true
sudo -u postgres psql -c "CREATE DATABASE polyedge OWNER polyedge;" 2>/dev/null || true

# Clone/update repo
if [ ! -d /opt/polyedge ]; then
    git clone <REPO_URL> /opt/polyedge
fi
cd /opt/polyedge/polyedge

# Set up venv
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# Run migrations
.venv/bin/alembic upgrade head

# Install systemd services
cp deploy/polyedge.service /etc/systemd/system/
cp deploy/polyedge-api.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable polyedge polyedge-api
systemctl restart polyedge polyedge-api

echo "PolyEdge deployed. API at http://$(hostname -I | awk '{print $1}'):8090"
```

**Step 2: Commit**

```bash
git add polyedge/deploy/
git commit -m "feat(polyedge): deployment scripts and systemd services"
```

---

## Task 10: Supergod Result Ingestion

**Files:**
- Create: `polyedge/src/polyedge/research/supergod_ingest.py`
- Modify: `polyedge/src/polyedge/scheduler.py`

When supergod workers complete research tasks, they push branches with JSON output. The core service needs to poll for completed branches, read the output, and ingest factors.

**Step 1: Implement**

```python
# src/polyedge/research/supergod_ingest.py
import json
import logging
import subprocess

from polyedge.research.ingest import parse_factors_json

log = logging.getLogger(__name__)

async def ingest_supergod_results(repo_path: str = "/opt/polyedge") -> int:
    """Check for new supergod task branches, extract research output, parse factors."""
    # Fetch latest
    subprocess.run(["git", "fetch", "origin"], cwd=repo_path, capture_output=True)

    # Find branches with research results
    result = subprocess.run(
        ["git", "branch", "-r", "--sort=-committerdate"],
        cwd=repo_path, capture_output=True, text=True
    )

    factors_ingested = 0
    for line in result.stdout.strip().split("\n"):
        branch = line.strip()
        if not branch.startswith("origin/task/"):
            continue

        # Check if we've already processed this branch (simple: check a marker file or DB flag)
        # For now, just read the latest commit message for the research output
        try:
            show = subprocess.run(
                ["git", "log", branch, "-1", "--format=%B"],
                cwd=repo_path, capture_output=True, text=True
            )
            # The actual research output would be in files on the branch
            # Read the branch's files to find JSON research output
            diff = subprocess.run(
                ["git", "diff", "origin/main..." + branch, "--name-only"],
                cwd=repo_path, capture_output=True, text=True
            )
            # Process new files on the branch
            for filepath in diff.stdout.strip().split("\n"):
                if not filepath.endswith(".json"):
                    continue
                content = subprocess.run(
                    ["git", "show", f"{branch}:{filepath}"],
                    cwd=repo_path, capture_output=True, text=True
                )
                factors = parse_factors_json(content.stdout, source="codex")
                factors_ingested += len(factors)
                # Store factors via _store_factors from scheduler
        except Exception as e:
            log.warning("Failed to process branch %s: %s", branch, e)

    return factors_ingested
```

Note: This is a starting point. The exact ingestion mechanism depends on how supergod workers format their research output. We'll iterate on this once we see what the workers actually produce.

**Step 2: Commit**

```bash
git add polyedge/src/polyedge/research/supergod_ingest.py
git commit -m "feat(polyedge): supergod result ingestion — reads research from task branches"
```

---

## Execution Order Summary

| Task | What | Depends On |
|------|------|-----------|
| 1 | Project scaffold + DB models | — |
| 2 | Polymarket poller | 1 |
| 3 | Perplexity + Grok clients | 1 |
| 4 | Supergod research dispatcher | 1 |
| 5 | Scheduler (ties 2+3+4) | 2, 3, 4 |
| 6 | Prediction engine + scoring | 1 |
| 7 | FastAPI + CLI | 1, 6 |
| 8 | Prediction generation loop | 5, 6 |
| 9 | Deploy to Hetzner | all above |
| 10 | Supergod result ingestion | 4, 9 |

Tasks 2, 3, 4 can run in parallel. Task 6 can run in parallel with 2-4.
