# PolyEdge: Polymarket Prediction System — Full Brief

## What This Is

PolyEdge is a system that finds hidden statistical correlations to predict Polymarket outcomes. Polymarket is a betting market where people bet YES/NO on real-world questions ("Will Trump win?", "Will BTC hit 100k by June?", "Will it snow in NYC on March 10?"). Markets resolve to YES or NO when the answer is known, and the final price goes to $1.00 for the winning side and $0.00 for the losing side.

Our thesis: there are non-obvious correlations between seemingly unrelated real-world data and market outcomes. Maybe when gold is up AND it's a Tuesday AND NBA playoffs are happening, crypto-related markets resolve YES 73% of the time. Nobody is looking for these patterns. If we find them, that's the edge.

## The Approach (Two Phases)

### Phase 1: Backtest (the big opportunity)

We already have 326,000+ resolved markets in our database with known outcomes. We know the question, the category, the end date, and whether it resolved YES or NO.

For each resolved market, we can look up what the world looked like on the day(s) that market was active:
- What was gold/BTC/S&P doing?
- What day of the week was it?
- What was the weather in major cities?
- What was trending on social media?
- Were there any major geopolitical events?
- What moon phase was it?
- What sports were in season?
- What was the VIX (fear index)?
- Were there Fed meetings that week?

We collect these as structured, consistent data points — not prose, not "AI, what's happening today?" — actual numbers and categories that are the same every time.

Then we run correlation analysis: for each market category (politics, crypto, sports, etc.), which combinations of these data points correlate with YES vs NO outcomes? We're looking for patterns like:

- "When VIX > 25 AND BTC is down >5% in the past week, crypto markets resolve NO 68% of the time"
- "Sports markets on Mondays after a holiday weekend resolve YES 12% more often"
- "Political markets where the question contains 'by end of month' resolve NO 81% of the time when asked in the first week of the month"

This is pure data mining on historical outcomes. We don't need to wait for anything — the data already exists.

### Phase 2: Live Prediction

Once we have correlations from backtesting, we apply them to currently active markets:

1. Collect the same structured data points every day (prices, weather, calendar, etc.)
2. For each active market, look up which correlations apply
3. Generate a prediction weighted by correlation strength
4. Track whether predictions are right or wrong
5. Feed results back to improve correlations

We keep paper trading (fake bets) on every market to continuously measure our accuracy. When a specific correlation pattern consistently beats 55%+ hit rate across 100+ samples, that's a real signal worth betting real money on.

## Current System Architecture

### What Exists (deployed at 89.167.99.187)

- **PostgreSQL database** with tables: markets, factors, predictions, factor_weights, price_snapshots
- **Poller** — fetches market data from Polymarket's Gamma API every 5 minutes (~8k active + 500 recently closed)
- **Research layer** — calls Perplexity and Grok APIs to collect "factors" (data points about the world)
- **Predictor** — takes factors + market price, generates YES/NO prediction for every active market
- **Scorer** — when markets resolve, marks predictions correct/wrong, updates category weights
- **Dashboard** — FastAPI serving a real-time mission control UI at port 8090
- **Supergod integration** — dispatches deep research tasks to Codex workers via WebSocket

### What's Wrong With It

The research layer collects random prose ("Taylor Swift is trending!") instead of structured repeatable data. The same 200 generic factors get applied to all 7,500 markets. The predictor is a dumb keyword matcher (sees "positive" → votes YES). Result: 2.7% hit rate because it says YES to everything and most markets resolve NO.

There is no backtesting. There is no correlation analysis. There is no structured data collection.

## What Needs To Be Built

### 1. Structured Data Collector

Replace the current prose-based research with a structured data pipeline that collects the SAME data points on a fixed schedule:

**Daily snapshots (store as rows with date + category + name + numeric value):**
- Financial: S&P 500 close, NASDAQ close, BTC price, ETH price, gold, oil, VIX, 10yr yield, DXY
- Calendar: day of week, day of month, month, is_weekend, is_holiday, is_month_end, is_quarter_end
- Crypto: BTC dominance, total crypto market cap, ETH gas price, top gainer %, top loser %
- Weather: temperature in NYC/LA/London/Tokyo, any active hurricanes/earthquakes (count)
- Astronomical: moon phase (0.0-1.0), hours of daylight
- Social: number of trending topics mentioning politics/crypto/sports/tech (counts, not content)
- Volatility: VIX level, BTC 7-day volatility, S&P 7-day volatility
- Sports: are NFL/NBA/MLB/NHL/FIFA in season (boolean), any championship game today (boolean)
- Political: is Congress in session, days until next election, any Fed meeting this week

These should be numeric or boolean values, not text. "Gold price: 2,847.30" not "Gold is doing well today."

Use free/cheap APIs: CoinGecko (crypto), Yahoo Finance or Alpha Vantage (stocks), OpenWeatherMap (weather), some astronomy API (moon phase). For things without APIs, use Perplexity/Grok but force numeric output.

### 2. Historical Data Backfiller

For each resolved market in the database:
- Look up the market's active period (first_seen to end_date or resolution date)
- Pull historical data for those dates from the same sources
- Store as the same structured format

This is the training data. 326k resolved markets × ~50 data points per day = millions of data rows to mine.

Not all historical data is free. Prioritize what's available:
- BTC/ETH historical prices: free from CoinGecko
- S&P/stock prices: free from Yahoo Finance
- Weather: historical data from Open-Meteo (free)
- Calendar data: trivially computed
- Moon phases: trivially computed
- Social/trending: harder to get historically, skip for now

### 3. Correlation Engine

After collecting historical data, run statistical correlation analysis:

For each market category (politics, crypto, sports, entertainment, etc.):
1. Gather all resolved markets in that category with their outcomes (YES/NO)
2. For each market, look up what the structured data points were on that date
3. Run correlation analysis: which individual factors correlate with YES outcomes? Which combinations?
4. Use methods like: logistic regression, decision trees, or even just brute-force conditional probability ("P(YES | gold_up AND tuesday AND vix_low)")
5. Rank correlations by strength and sample size
6. Filter out anything with <50 samples or <55% hit rate

The output is a set of "rules" like:
```
IF market_category = "crypto" AND btc_7d_change > 5% AND vix < 20 THEN predict YES (confidence: 0.63, samples: 847)
IF market_category = "sports" AND day_of_week = "Monday" THEN predict NO (confidence: 0.58, samples: 2341)
IF market_category = "politics" AND congress_in_session = True AND days_to_election < 30 THEN predict YES (confidence: 0.61, samples: 156)
```

### 4. New Predictor

Replace the current keyword-matching predictor with one that:
1. Looks up today's structured data snapshot
2. Finds all correlation rules that match the current market's category + today's data
3. Combines matching rules (weighted by confidence and sample size)
4. Outputs YES/NO prediction with a real confidence score

### 5. Continuous Learning

As new markets resolve:
1. Add their outcome to the training set
2. Re-run correlation analysis (daily or weekly)
3. Update rules
4. Drop rules that stopped working, promote new ones that emerged

## Technical Details

### Stack
- Python 3.12, FastAPI, SQLAlchemy async, PostgreSQL
- **256GB Compute Server**: 88.99.142.89 (Windows Server 2022, 256GB RAM, 6c/12t, 480GB disk)
  - All heavy compute: data collection, correlation engine, backtests, feature matrix, API connectors, paper trader
  - Project dir: C:\polyedge\, Data: C:\polyedge\data\, Results: C:\polyedge\data\results\
  - SQLite: C:\polyedge\data\paper_trades.db
  - User: Administrator, Pass: <SET IN .env>
  - Windows scheduled tasks for paper trading (every 6h)
- **DB + Dashboard Server**: 89.167.99.187 (Hetzner CX22, 4GB RAM)
  - PostgreSQL, Polymarket poller, FastAPI dashboard :8090
  - Systemd services: polyedge (scheduler) + polyedge-api (dashboard)
- Source: C:\Users\asus\Desktop\projects\supergod\polyedge

### Key Files
- `src/polyedge/models.py` — DB models (Market, Factor, Prediction, FactorWeight, PriceSnapshot)
- `src/polyedge/poller.py` — Polymarket Gamma API poller
- `src/polyedge/scheduler.py` — Main loop orchestrating poll/research/predict/score cycles
- `src/polyedge/analysis/predictor.py` — Current (bad) prediction engine
- `src/polyedge/analysis/scorer.py` — Scores predictions after market resolution
- `src/polyedge/analysis/generate.py` — Generates predictions for all active markets
- `src/polyedge/research/` — Perplexity, Grok, Supergod API clients + JSON parser
- `src/polyedge/app.py` — FastAPI dashboard + API endpoints
- `src/polyedge/static/dashboard.html` — Mission control UI
- `src/polyedge/db.py` — Database connection + settings

### Database
- PostgreSQL on localhost, database "polyedge", user "polyedge"
- ~326k markets (7.5k active), ~500 factors, ~50k predictions, ~22k scored
- Connection: `postgresql+asyncpg://polyedge:polyedge@localhost:5432/polyedge`

### API Keys (in systemd env)
- Perplexity: `<SET IN .env>`
- Grok (xAI): `<SET IN .env>`

### Polymarket API
- Public, no auth needed
- Base URL: `https://gamma-api.polymarket.com`
- GET `/markets?closed=false&limit=100&offset=0` for active markets
- Resolution detected from outcomePrices: [1,0] = YES won, [0,1] = NO won

### Supergod Worker Cluster (already running)
- 4 Hetzner boxes with Codex CLI workers connecting to orchestrator at 89.167.99.187:8080
- Workers: 77.42.67.96, 89.167.107.163, 89.167.109.57, 89.167.99.187
- Each runs `codex exec --full-auto` as subprocess, communicates via WebSocket
- Can scale up easily — Codex accounts ~$30/yr each, Hetzner CX22 ~$5/mo
- Workers take task prompts, browse the web, research, return structured output

## Data Strategy: Two-Layer Research

### Layer 1: Free API Baseline (per-date, no LLMs)

Backfill ~50 structured numeric features for every date we have markets:
- **yfinance**: S&P, NASDAQ, Dow, VIX, gold, oil, any ticker — full daily history, free
- **CoinGecko**: BTC, ETH, SOL, total market cap, dominance — free, 30 calls/min
- **Open-Meteo**: Temperature/rain/wind for major cities — free, no key, historical
- **FRED API**: Fed rate, CPI, unemployment, treasury yields — free with key
- **Python math**: Day of week, month, holidays, moon phase, quarter-end — zero API calls
- **Finnhub**: Earnings calendar, IPO dates, economic events — free tier
- **TheSportsDB**: Sports seasons, major game dates — free
- **USGS**: Earthquake data — free
- **Wikimedia**: Wikipedia pageview spikes (attention proxy) — free

This covers every date instantly. No LLM cost. ~50 features per day × ~1,000 unique dates = baseline feature matrix.

### Layer 2: LLM Deep Research (per-date + per-market, uses workers)

Workers research each date to generate 500-1000 additional tagged features:
- Every major news headline that week
- Social media trends and sentiment
- Celebrity/public figure events
- Political developments, bills, votes
- Cultural events, releases, conferences
- Memes, viral moments
- Regional events by country/city

Per-DATE research covers everything that applies to all markets active that day.
Per-MARKET research (second pass) adds context for specific markets — the 7-30 day leadup, not just resolution day.

With 4-6 workers, ~1,000 unique dates takes about a week.

### The Correlation Engine (pure math, no LLMs)

After data collection:
1. Build feature matrix: rows = resolved markets, columns = all features active during that market
2. Group by market category (politics, crypto, sports, etc.)
3. For each category, run statistical analysis: which features correlate with YES vs NO?
4. Methods: logistic regression, decision trees, conditional probability brute-force
5. Output: ranked rules with confidence and sample size
6. Filter: minimum 50 samples, minimum 55% hit rate

Example output:
```
IF market_category="crypto" AND btc_7d_change > 5% AND vix < 20 THEN YES (63%, n=847)
IF market_category="sports" AND day_of_week="Monday" THEN NO (58%, n=2341)
```

## Priority Order

1. **Free API backfiller** — get baseline 50 features per date for all historical dates (instant, free)
2. **Worker date-research pipeline** — dispatch workers to enrich each date with 500+ LLM-researched features
3. **Correlation engine** — mine the feature matrix against 326k resolved outcomes
4. **New predictor** — use discovered rules to predict active markets
5. **Dashboard updates** — show correlations, rule performance, backtesting results
6. **Continuous learning** — as new markets resolve, re-run correlations, update rules

The backtesting on historical data is the highest-leverage thing. We have 326k resolved markets. If we can find even one correlation pattern that hits 60%+ on a common market category, that's real money.
