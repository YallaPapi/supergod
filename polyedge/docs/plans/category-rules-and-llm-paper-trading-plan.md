# Plan: Category-Specific Rules, LLM Paper Trading, and Combined Signal Testing

## Overview

We currently have three valuable but disconnected systems:

1. **Ngram rules** — 171k rules mined from 500k historical markets. Pattern matching on question text. Currently the ONLY thing driving paper trades. Profitable so far (+$55 in ~1,700 resolved trades).

2. **LLM research pipeline** — Grok, Perplexity, and Codex workers continuously analyze markets and produce factors (29k+ total: sentiment, expert opinions, data signals). These factors feed into 4M+ predictions with confidence scores. **None of this is used for trading.**

3. **Market categorization** — doesn't exist yet. All rules are trained on all markets blended together. A rule's 65% win rate is an average across sports, politics, crypto, etc. Per-category performance is unknown.

This plan creates three parallel experiments that run alongside the existing system without changing it:

- **Experiment A**: Category-specific ngram rules (mine and evaluate rules per market category)
- **Experiment B**: LLM-only paper trades (trade on predictions that already exist in the DB)
- **Experiment C**: Combined signal trades (only trade when ngram AND LLM agree)

All three run as separate paper trade tracks with their own labels so we can compare performance head-to-head.

---

## PHASE 1: Market Categorization

### Goal
Assign every market in the `markets` table a reliable category label.

### Why this comes first
Everything else depends on it — per-category rules, per-category LLM performance analysis, understanding where our edge actually comes from.

### Step 1.1: Define the category taxonomy

We need a fixed set of categories that are:
- Mutually exclusive (a market belongs to exactly one)
- Exhaustive (every market fits somewhere)
- Granular enough to be useful, but not so granular that sample sizes become tiny

Proposed taxonomy:

| Category ID | Label | Description | Example |
|---|---|---|---|
| `crypto_updown` | Crypto Up/Down | Short-term crypto price direction (the "up or down" markets) | "Will Bitcoin go up or down in the next 5 min?" |
| `crypto_other` | Crypto Other | Crypto markets that aren't up/down price direction | "Will Bitcoin hit $100k by March?" |
| `sports_ou` | Sports Over/Under | Sports total points/goals/sets over/under lines | "Bayern vs Gladbach: O/U 3.5" |
| `sports_spread` | Sports Spread | Point spread / handicap markets | "Spread: Al Ahli (-1.5)" |
| `sports_winner` | Sports Winner | Which team/player wins | "Will Bayern Munich win?" |
| `sports_props` | Sports Props | Player props, method of victory, draw markets | "Will the fight be won by KO?" |
| `politics_us` | US Politics | US elections, legislation, executive actions | "Will Trump visit China by March 31?" |
| `politics_intl` | Int'l Politics | Non-US political markets | "Will Macron resign?" |
| `economics` | Economics | Fed rates, GDP, unemployment, trade | "Will the Fed cut rates in March?" |
| `entertainment` | Entertainment | Movies, music, awards, streaming | "Will Taylor Swift's album go #1?" |
| `science_tech` | Science & Tech | Space, AI, climate, health | "Will SpaceX land on Mars by 2028?" |
| `other` | Other | Anything that doesn't fit above | Misc markets |

### Step 1.2: Build the classifier

**File**: `polyedge/src/polyedge/analysis/market_classifier.py`

The classifier runs in two tiers:

**Tier 1 — Keyword rules (fast, deterministic, handles ~80% of markets)**

```python
CLASSIFICATION_RULES = [
    # Order matters — first match wins
    ("crypto_updown", [r"up or down"]),
    ("sports_ou",     [r"o/u \d", r"over/under", r"total (sets|games|points|goals|runs|maps)"]),
    ("sports_spread", [r"spread:", r"handicap"]),
    ("sports_props",  [r"win by ko", r"win by tko", r"win by decision", r"end in a draw",
                       r"fight to go the distance", r"method of victory"]),
    ("sports_winner", [r"(vs\.|fc |united |city |rovers |wanderers |athletics ).*win",
                       r"will .* win on 20\d\d"]),
    ("economics",     [r"fed (increase|decrease|cut|raise|hold)", r"interest rate",
                       r"gdp", r"unemployment rate", r"inflation rate", r"cpi "]),
    ("politics_us",   [r"(trump|biden|harris|desantis|pence|obama) ",
                       r"(republican|democrat|gop|dnc|rnc) ",
                       r"(senate|congress|house of rep|supreme court|scotus)",
                       r"presidential (election|nomination|primary)"]),
    ("politics_intl", [r"(parliament|prime minister|brexit|eu |nato )",
                       r"(macron|trudeau|modi|putin|zelensky|netanyahu)"]),
    ("entertainment", [r"(album|song|movie|grammy|oscar|emmy|billboard|spotify|netflix|disney\+)",
                       r"(box office|streaming|#1 hit|chart)"]),
    ("crypto_other",  [r"(bitcoin|ethereum|btc|eth|solana|doge|xrp|crypto|token|defi|nft)",
                       r"(market cap|mining|halving)"]),
    ("science_tech",  [r"(spacex|nasa|mars|moon landing|ai |artificial intelligence)",
                       r"(climate|vaccine|fda approval|who |pandemic)"]),
]
```

This is a function that takes a market question string, lowercases it, runs through the rules in order, and returns the first matching category. If nothing matches, returns `"other"`.

**Tier 2 — LLM classification for "other" markets (optional, uses existing Grok calls)**

For markets that Tier 1 classified as "other", we can batch them and ask Grok to classify them. This is optional and can be added later. Tier 1 alone should handle the majority.

### Step 1.3: Backfill all markets

**Script**: `polyedge/scripts/backfill_categories.py`

1. Add a `market_category` column to the `markets` table (varchar(30), nullable, indexed)
2. Run the Tier 1 classifier on all 500k markets
3. Update the column in batches of 5,000 (to avoid long transactions)
4. Log the distribution: how many markets per category

This is pure compute, no API calls, should take <5 minutes on the 256GB server.

### Step 1.4: Auto-classify new markets

Add a call to the classifier inside the poller's `parse_market()` function so that every new market gets classified on ingestion. One line of code.

### Deliverables
- `market_classifier.py` with the keyword classifier function
- `backfill_categories.py` script
- Migration to add `market_category` column
- Poller updated to classify on ingestion
- Dashboard updated to show category breakdown

---

## PHASE 2: Per-Category Rule Evaluation and Mining

### Goal
Answer two questions:
1. How do our existing 171k ngram rules perform when evaluated per category?
2. What new rules emerge when we mine per category?

### Step 2.1: Evaluate existing rules per category

**Script**: `polyedge/scripts/evaluate_rules_by_category.py`

For each active trading rule, calculate its win rate within each category separately.

```
For rule "ngram:another party" (blended win rate: 65%, sample: 500):
  - politics_us:    72% win rate (280 markets) ← strong signal
  - sports_winner:  41% win rate (120 markets) ← actively harmful
  - crypto_updown:  55% win rate (60 markets)  ← noise
  - other:          61% win rate (40 markets)   ← maybe useful
```

**How it works:**

1. Query all resolved markets with their category labels
2. For each rule, find all markets where the rule's ngram appears in the question
3. Group by category, calculate win rate per category
4. Store results in a new table: `rule_category_performance`

**New table**: `rule_category_performance`

| Column | Type | Description |
|---|---|---|
| rule_id | int FK | References trading_rules.id |
| market_category | varchar(30) | The category |
| sample_size | int | Number of resolved markets in this category where rule matched |
| win_rate | float | Win rate within this category |
| pnl_per_trade | float | Average PnL per trade (assuming $1 bet at avg entry price) |
| edge_vs_blended | float | Difference between category win rate and blended win rate |

This tells us exactly which rules to use in which categories, and which to avoid.

### Step 2.2: Mine new category-specific rules

**Script**: `polyedge/scripts/mine_category_rules.py`

Run the existing ngram miner (`polyedge/src/polyedge/analysis/ngram_miner.py`) separately on each category's subset of resolved markets.

For each category:
1. Select all resolved markets with that category label
2. Extract ngrams (1-gram through 4-gram) from market questions
3. Calculate win rates for each ngram within that category
4. Filter: win_rate >= 60%, sample_size >= 50 (lower threshold per category since subsets are smaller)
5. Store as new trading rules with a `category_scope` field

**Why this finds new rules:**

Consider the phrase "premier league" — in the blended dataset of 500k markets, it might only have a 52% win rate because it appears in diverse contexts. But within `sports_winner` markets specifically, it might have a 68% win rate because teams in certain league positions tend to win at predictable rates. The blended miner would discard this rule; the category miner would find it.

**New column on `trading_rules` table**: `category_scope` (varchar(30), nullable)
- NULL = blended rule (applies to all categories, existing behavior)
- "sports_ou" = rule only applies within that category

### Step 2.3: Backtest per-category rules vs blended rules

**Script**: `polyedge/scripts/backtest_category_rules.py`

Simulate paper trading on historical data using three strategies:

**Strategy A: Blended (current system)**
- Use existing blended ngram rules on all markets regardless of category
- This is our baseline — what we're already doing

**Strategy B: Category-filtered**
- Use existing blended rules BUT only apply them in categories where their per-category win rate >= 55%
- Skip trades where the rule underperforms in that category
- This should eliminate bad trades without changing any rules

**Strategy C: Category-specific**
- Use category-specific rules (mined in Step 2.2) for each category
- Fall back to blended rules only if no category-specific rule exists
- This should find additional opportunities the blended miner missed

For each strategy, compute:
- Total trades placed
- Win rate
- Total PnL
- PnL per trade
- Max drawdown
- Per-category breakdown

Store results in `backtest_results` table for dashboard display.

### Deliverables
- `evaluate_rules_by_category.py` script
- `mine_category_rules.py` script
- `backtest_category_rules.py` script
- `rule_category_performance` table migration
- `category_scope` column on `trading_rules`
- `backtest_results` table migration

---

## PHASE 3: LLM-Only Paper Trading (Experiment B)

### Goal
Open a separate track of paper trades based purely on the LLM predictions that are already being generated. Compare their performance against ngram paper trades.

### Step 3.1: Understand the existing prediction data

The `predictions` table already has 4M+ rows. Each prediction has:
- `market_id` — which market
- `predicted_side` — YES or NO
- `confidence` — how confident the model is (0 to 1)
- `created_at` — when it was generated

The predictions are generated by `polyedge/src/polyedge/analysis/predictor.py` which reads factors from Grok/Perplexity/Codex and produces a directional prediction with confidence.

**Key question**: How do we determine if a prediction represents an edge?

The edge is: `confidence - market_price`. If the LLM says YES with 75% confidence and the market YES price is $0.55, that's a 20% edge. Same concept as ngram rules — the LLM's confidence is its estimate of true probability, the market price is the market's estimate, and the gap is our edge.

### Step 3.2: Build the LLM paper trader

**File**: `polyedge/src/polyedge/scheduler.py` — new function `run_llm_paper_trading()`

**Logic:**

1. Query all open (non-resolved) markets where:
   - `end_date` is within the next 48 hours (capital efficiency — focus on soon-resolving)
   - Market is active, price between 0.02 and 0.98 (not dead)
   - We don't already have an LLM paper trade on this market+side

2. For each market, get the most recent prediction from the `predictions` table

3. Calculate edge:
   ```
   If predicted_side == "YES":
       edge = confidence - yes_price
   If predicted_side == "NO":
       edge = confidence - no_price
   ```

4. If edge >= 0.10 (10% minimum — we can tune this later), open a paper trade

5. **Critical**: Store these with a different `trade_source` so we can distinguish them from ngram trades

**New column on `paper_trades` table**: `trade_source` (varchar(20), default 'ngram')
- `'ngram'` = existing ngram-based trades (current system, default for all existing rows)
- `'llm'` = LLM prediction-based trades (this experiment)
- `'combined'` = both ngram AND LLM agree (Phase 4)

### Step 3.3: Add to scheduler

Add `run_llm_paper_trading` to the scheduler's `run_forever()` function. Run it every 5 minutes, same as the ngram paper trader.

The existing `score_paper_trades()` function doesn't care about trade_source — it scores all unresolved trades whose markets have resolved. So LLM trades get scored automatically.

### Step 3.4: Dashboard integration

Add an "LLM Trades" section or tab to the dashboard showing:
- LLM trade count (open / closed)
- LLM win rate
- LLM PnL
- Side-by-side comparison: Ngram vs LLM vs Combined

### Deliverables
- `run_llm_paper_trading()` function in scheduler
- `trade_source` column migration on `paper_trades`
- Backfill existing trades: `UPDATE paper_trades SET trade_source = 'ngram' WHERE trade_source IS NULL`
- Dashboard updates for LLM trade tracking
- Scheduler updated to run LLM paper trader every 5 minutes

---

## PHASE 4: Combined Signal Trading (Experiment C)

### Goal
Open paper trades only when BOTH the ngram rule AND the LLM prediction agree on direction. Theory: agreement between two independent methods = stronger signal, fewer false positives.

### Step 4.1: Build the combined paper trader

**File**: `polyedge/src/polyedge/scheduler.py` — new function `run_combined_paper_trading()`

**Logic:**

1. For each active market ending within 48 hours:

2. **Check ngram signal**: Does any qualified ngram rule match this market with edge >= 5%?
   - If yes, record the ngram's predicted side and edge

3. **Check LLM signal**: Does the most recent prediction for this market have confidence-based edge >= 10%?
   - If yes, record the LLM's predicted side and edge

4. **Agreement check**:
   - If both signals exist AND both predict the SAME side → open a combined trade
   - Use the lower of the two edges as the trade's edge (conservative)
   - Store with `trade_source = 'combined'`

5. If they disagree or only one signal exists → skip (do nothing, the individual traders handle those separately)

### Step 4.2: What we expect

- **Fewer trades** than either ngram or LLM alone (only fires when both agree)
- **Higher win rate** (two independent methods agreeing should be more reliable)
- **Lower total PnL** in absolute terms (fewer trades) but higher PnL per trade
- If combined outperforms on a per-trade basis, we can scale capital allocation toward it

### Step 4.3: Dashboard tracking

The combined trades show up alongside ngram and LLM trades in the dashboard comparison panel. Three columns:

```
               Ngram Only    LLM Only    Combined
Trades:        1,200         300          80
Win Rate:      42%           55%          68%
PnL:           +$45          +$12         +$15
PnL/Trade:     +$0.04        +$0.04       +$0.19
```

(Numbers are hypothetical — the whole point is to find out which actually performs best.)

### Deliverables
- `run_combined_paper_trading()` function in scheduler
- Scheduler updated to run combined trader every 5 minutes
- Dashboard comparison panel

---

## PHASE 5: LLM-Assisted Market Categorization (Optional Enhancement)

### Goal
Use our existing Grok API calls to classify the ~20% of markets that Tier 1 keyword rules mark as "other".

### Step 5.1: Batch classify with Grok

For markets where `market_category = 'other'`:

1. Batch them in groups of 50
2. Send to Grok with prompt:
   ```
   Classify each market question into exactly one category:
   crypto_updown, crypto_other, sports_ou, sports_spread, sports_winner,
   sports_props, politics_us, politics_intl, economics, entertainment,
   science_tech, other

   Markets:
   1. "Will the price of gold exceed $3000 by June?"
   2. "Will the 2026 World Cup final have more than 2.5 goals?"
   ...

   Respond with just the number and category, one per line.
   ```
3. Update the `market_category` column with Grok's classifications
4. This uses existing Grok API budget — we're already paying for calls, might as well use them for this

### Why this is optional
Tier 1 keyword rules should handle ~80% correctly. The remaining 20% "other" bucket is a mix of niche markets that may not have enough volume to mine meaningful rules from anyway. But if we want completeness, Grok can do it cheaply.

---

## Implementation Order

| Step | Phase | Description | Depends On | Compute | Effort |
|------|-------|-------------|------------|---------|--------|
| 1 | 1.1-1.2 | Build keyword classifier | Nothing | Local | Small |
| 2 | 1.3 | Backfill all 500k markets with categories | Step 1 | 256GB server | Small |
| 3 | 1.4 | Auto-classify new markets in poller | Step 1 | Deploy | Tiny |
| 4 | 3.2 | Build LLM paper trader | Nothing (predictions exist) | Deploy | Medium |
| 5 | 3.3 | Add to scheduler + add trade_source column | Step 4 | Deploy | Small |
| 6 | 2.1 | Evaluate existing rules per category | Step 2 | 256GB server | Medium |
| 7 | 2.2 | Mine category-specific rules | Step 2 | 256GB server | Medium |
| 8 | 2.3 | Backtest category vs blended | Steps 6, 7 | 256GB server | Medium |
| 9 | 4.1 | Build combined paper trader | Steps 4, existing ngram | Deploy | Small |
| 10 | All | Dashboard updates for all experiments | Steps 4-9 | Deploy | Medium |

**Steps 1-3 and 4-5 can run in parallel** — categorization and LLM paper trading are independent.

**Steps 6-8 depend on categorization** being complete.

**Step 9 depends on the LLM paper trader** being live (needs both signals available).

## Success Criteria

After 1 week of all three experiments running:

1. **Minimum 500 resolved trades per experiment** to have statistical significance
2. **Clear winner identified** — which approach has the best PnL per trade?
3. **Category insights** — which categories are profitable, which are not?
4. **Decision point**: Scale real money toward the winning strategy

## What Does NOT Change

- The existing ngram paper trader keeps running exactly as-is
- The existing LLM research pipeline keeps running exactly as-is
- The existing poller/scorer keeps running exactly as-is
- No existing paper trades are modified or deleted
- All new experiments run alongside, with separate tracking via `trade_source`
