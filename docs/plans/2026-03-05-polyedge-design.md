# PolyEdge: Polymarket Prediction System Design

## Overview

A research-heavy prediction system for Polymarket that uses supergod workers + Perplexity + Grok for continuous parallel research, tracks thousands of factors (including random/non-obvious ones), generates predictions on every market, and measures hit rate per factor category to find a real edge. Paper trading first, then live at small scale.

## Core Thesis

Everyone prices in the obvious (polls, news, expert opinion). The edge is in random factors nobody else tracks: weather, moon phase, day of week, stock market moves, trending tweets, historical anniversaries, sports results, celebrity activity. Track thousands of these, predict every market, and let statistics reveal which factor categories actually have predictive power.

## Architecture

Three layers, one repo, one deployable service.

### Layer 1: Core Service (24/7 on Hetzner box)

- Polls Polymarket public API every 5 min — all active markets, prices, volumes
- PostgreSQL stores: markets, prices, factors, predictions, hit rates
- Scheduler dispatches research tasks to supergod + Perplexity + Grok
- Nightly correlation analysis on resolved markets vs factor data
- Simple web dashboard for viewing predictions, hit rates, factor scores

### Layer 2: Research Layer (three sources)

**Supergod workers (~192 tasks/day)**
- Every 30 min: 4 workers each deep-research 1 active market
- Category-specific structured prompt templates (not "tell me everything")
- Each prompt forces JSON output with specific factor fields
- Free — uses Codex accounts, no API cost
- Handles: historical precedents, complex reasoning, multi-step analysis

**Perplexity API (~300 calls/day, ~$1.50/day)**
- Every 30 min: global events sweep
- Every 30 min: per-category sweeps (politics, crypto, sports, etc.)
- Real-time web access for current events
- Handles: what's happening right now, breaking news, data lookups

**Grok API (~150 calls/day, ~$1.50/day)**
- Every 30 min: trending X/Twitter topics
- Every hour: social sentiment per active market
- Real-time X/Twitter data
- Handles: social pulse, viral events, public opinion shifts

All research outputs structured JSON, ingested into PostgreSQL.

### Layer 3: Prediction & Scoring

- Every active market gets a prediction based on accumulated factor data
- No limits on number of predictions — maximize volume for faster learning
- Track hit rate per factor category, not P&L
- Key metric: which factor categories consistently beat 50%?

**Scoring after market resolution:**
- Which factors were present?
- Did the prediction match the outcome?
- Update factor category weights
- Drop categories that perform at or below coin-flip level
- Double down research on categories that show edge

## Factor Database

Two types:

**Obvious factors** (baseline, everyone has these):
- Polls, expert opinions, news headlines, market volume/momentum

**Random factors** (the edge):
- Weather: temperature extremes, natural disasters, seasonal patterns
- Astronomical: moon phase, day of week, time of year
- Financial: S&P movement, crypto prices, VIX, gold
- Social: trending topics, celebrity activity, viral events
- Sports: major game results, upset patterns
- Historical: what happened on this date historically, anniversary effects
- Political calendar: days until election, congressional schedule, UN sessions
- Cultural: holidays, movie releases, conferences
- Behavioral: Polymarket volume patterns, time-of-day effects, weekend drift
- Global: geopolitical events in unrelated regions, trade data releases

Schema:
```
factor_id | market_id | timestamp | category | subcategory | name | value | source | confidence
```

## Research Prompt Design

Prompts are templated per factor category. Each forces structured JSON output. Examples:

**Historical precedent template:**
```
Market: "{market_question}"
Current odds: {yes_price}% YES

Find 3-5 closest historical parallels to this situation.
For each: what happened, when, what was the outcome, what was different.
Output JSON: {parallels: [{event, date, outcome, similarity_score, key_differences}]}
```

**Random factor sweep template:**
```
Date: {today}
Research the following for today:
- Major weather events globally
- Stock market moves (S&P, NASDAQ, BTC, ETH)
- Trending social media topics (top 10)
- Celebrity/public figure news
- Sports results
- Historical events on this date
- Upcoming scheduled events this week
Output JSON: {factors: [{category, name, value, description}]}
```

Prompt templates live in a `prompts/` directory and are iterated on frequently as we learn what produces useful vs noisy factors.

## Paper Trading Rules

- Predict EVERY market. Low confidence predictions still get recorded.
- Score by hit rate per factor category, not by P&L
- No position limits, no entry timing rules during paper phase
- Maximize prediction volume = maximize learning speed

**Going live:**
- When factor categories show consistent >55% hit rate over 2+ weeks with 20+ resolved predictions
- Start with $100, scale up as confidence builds
- Add position sizing and risk rules only at live stage

## Tech Stack

- Python, FastAPI
- PostgreSQL
- APScheduler for research/polling schedules
- Pandas/numpy for correlation analysis
- React dashboard (simple)
- Supergod orchestrator for worker dispatch

## Repo Structure

```
polyedge/
├── core/           # FastAPI service, DB models, scheduler, Polymarket poller
├── research/       # Perplexity client, Grok client, result ingestion
├── analysis/       # Correlation engine, factor scoring, nightly batch
├── trading/        # Prediction generator, paper trading, eventually live
├── dashboard/      # React frontend
└── prompts/        # Factor category prompt templates
```

## Deployment

- Core service runs on an existing Hetzner box (same network as supergod workers)
- Supergod workers on existing servers — just receive research tasks via orchestrator
- Single systemd service for the core, cron or APScheduler for research dispatch

## Research Volume

- ~500 total research actions per day
- ~192 deep Codex research tasks (free)
- ~300 Perplexity calls (~$1.50/day)
- ~150 Grok calls (~$1.50/day)
- Total cost: ~$3/day for research
- Every active market researched multiple times daily

## Success Criteria

1. System runs autonomously, collecting factors 24/7
2. After 2 weeks: enough resolved markets to run first correlation analysis
3. After 4 weeks: identify which factor categories have predictive power
4. Factor categories with >55% hit rate over 50+ predictions = real edge
5. Go live with small bankroll, scale based on continued performance
