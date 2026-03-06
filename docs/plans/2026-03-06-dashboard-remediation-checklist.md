# Dashboard Data Remediation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix expired/wrong-date dashboard behavior and paper-trade accuracy issues, while keeping short ngram rules (classified, not deleted), and excluding credential-rotation/doc-secret cleanup from this scope.

**Architecture:** Stabilize the dashboard by enforcing one canonical trade-eligibility filter, adding stale-market lifecycle handling, normalizing all datetime handling to UTC ISO output, and adding rule-tier metadata for transparent filtering. Changes are split into low-risk vertical slices with verification after each slice.

**Tech Stack:** FastAPI, SQLAlchemy async, PostgreSQL, static HTML/JS dashboard, pytest.

---

## Scope Note (User Constraint)

- **Explicitly out of scope for this plan:** credential rotation and removing secrets from existing handoff docs.
- This plan focuses only on runtime correctness, date integrity, and dashboard/paper-trade behavior.

---

### Task 1: Baseline Snapshot Before Any Fixes

**Files:**
- Create: `polyedge/scripts/audit_dashboard_state.py`
- Create: `polyedge/scripts/audit_stale_markets.py`
- Create: `polyedge/scripts/audit_rule_quality.py`

**Step 1: Write the scripts**

Script requirements:
- `audit_dashboard_state.py`: print `open_count`, `closed_count`, `pt_hit`, `ending_today`, `ending_this_week`, and count of opportunities with `resolves_at < now`.
- `audit_stale_markets.py`: count markets with `active=true`, `resolved=false`, `end_date < now`.
- `audit_rule_quality.py`: count rules by ngram length bands (1-3, 4-7, 8+ chars) and sample-size buckets.

**Step 2: Run baseline audit**

Run:
```bash
python polyedge/scripts/audit_dashboard_state.py
python polyedge/scripts/audit_stale_markets.py
python polyedge/scripts/audit_rule_quality.py
```

Expected:
- Current broken-state metrics captured and saved to terminal logs for before/after comparison.

**Step 3: Commit baseline scripts**

```bash
git add polyedge/scripts/audit_dashboard_state.py polyedge/scripts/audit_stale_markets.py polyedge/scripts/audit_rule_quality.py
git commit -m "chore: add baseline audit scripts for dashboard remediation"
```

---

### Task 2: Fix Human Dashboard Runtime Blocker (`open_count` unbound)

**Files:**
- Modify: `polyedge/src/polyedge/app.py`
- Test: `polyedge/tests/test_app.py`

**Step 1: Write failing test**

Add test case that mocks DB responses so:
- `opportunities=[]`
- `open_count > 0`
- endpoint `/api/human-dashboard` still returns 200 with action text (no exception).

**Step 2: Run test to confirm failure**

Run:
```bash
pytest polyedge/tests/test_app.py -k human_dashboard -v
```

Expected:
- FAIL on current code due to `open_count` referenced before assignment.

**Step 3: Implement minimal fix**

In `human_dashboard()`:
- Move paper-trade aggregate calculation block (`open_count`, `closed_count`, `total_pnl`, `wins`) above top-action branching.
- Keep behavior unchanged otherwise.

**Step 4: Re-run test**

Run:
```bash
pytest polyedge/tests/test_app.py -k human_dashboard -v
```

Expected:
- PASS.

**Step 5: Commit**

```bash
git add polyedge/src/polyedge/app.py polyedge/tests/test_app.py
git commit -m "fix: prevent human-dashboard crash when opportunities are empty"
```

---

### Task 3: Create Canonical Eligibility Filter for "Real" Trades

**Files:**
- Modify: `polyedge/src/polyedge/app.py`
- Create: `polyedge/src/polyedge/query_filters.py`
- Test: `polyedge/tests/test_app.py`

**Step 1: Add shared filter helper**

In `query_filters.py`, add reusable SQLAlchemy expressions:
- `is_noise_market(question)`: includes `%up or down%`.
- `is_real_trade(PaperTrade, Market, now)`: `resolved/open as caller decides`, `entry_price > 0.02`, not noise, and for open-trade dashboard contexts require `end_date >= now`.

**Step 2: Replace duplicated ad-hoc filters**

Apply shared filter in all dashboard subqueries:
- opportunities
- ending_soonest
- open/closed counts
- ending_today/ending_this_week
- avg_edge
- recent_closed
- open_trades list

**Step 3: Add tests**

Add tests verifying a trade is excluded from all relevant metrics when:
- question contains `up or down`
- entry_price <= 0.02
- end_date < now for open-trade panels

**Step 4: Run tests**

```bash
pytest polyedge/tests/test_app.py -k "human_dashboard or paper" -v
```

Expected:
- PASS and consistent metrics.

**Step 5: Commit**

```bash
git add polyedge/src/polyedge/app.py polyedge/src/polyedge/query_filters.py polyedge/tests/test_app.py
git commit -m "refactor: centralize real-trade eligibility filters across dashboard metrics"
```

---

### Task 4: Add Stale-Market Lifecycle Handling

**Files:**
- Modify: `polyedge/src/polyedge/scheduler.py`
- Modify: `polyedge/src/polyedge/poller.py`
- Create: `polyedge/scripts/reconcile_stale_markets.py`
- Test: `polyedge/tests/test_scheduler.py`

**Step 1: Add scheduler guard**

In `run_paper_trading()` skip market when:
- `market.end_date is not None`
- `market.end_date < utc_now`

**Step 2: Add reconciliation script**

`reconcile_stale_markets.py`:
- Query DB markets where `resolved=false AND end_date < now - interval '7 days'`.
- For each market id, call `GET /markets/{id}` from Gamma API.
- Update `active`, `resolved`, `resolution`, `resolution_source`, `updated_at`.
- Print summary counts: checked, updated, newly resolved.

**Step 3: Add poller stale backfill hook**

In poller cycle (or a dedicated scheduled job), process stale unresolved ids in bounded batches (e.g., 200 per cycle) so old closures are eventually reconciled even if not in "recently closed 500".

**Step 4: Add tests**

Test `run_paper_trading` never opens trades on expired markets.

**Step 5: Run tests**

```bash
pytest polyedge/tests/test_scheduler.py -k "paper_trading or stale" -v
```

Expected:
- PASS.

**Step 6: Commit**

```bash
git add polyedge/src/polyedge/scheduler.py polyedge/src/polyedge/poller.py polyedge/scripts/reconcile_stale_markets.py polyedge/tests/test_scheduler.py
git commit -m "fix: add stale-market guard and reconciliation path for unresolved expired markets"
```

---

### Task 5: Normalize Datetime Output and Frontend Parsing

**Files:**
- Modify: `polyedge/src/polyedge/app.py`
- Modify: `polyedge/src/polyedge/poller.py`
- Modify: `polyedge/src/polyedge/static/dashboard.html`
- Test: `polyedge/tests/test_app.py`

**Step 1: Standardize backend datetime serialization**

Replace `str(dt)` with explicit UTC ISO:
- `dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")`

Apply to:
- `resolves_at`
- `generated_at`
- `resolved_at`
- any datetime returned in dashboard payload.

**Step 2: Frontend parse strictly as UTC**

In `fmtDate`:
- parse only ISO UTC strings.
- fallback display `TBD` if invalid.
- add tooltip/full timestamp in UTC for debugging.

**Step 3: Add tests**

Backend tests:
- assert datetime fields end with `Z`.
- assert no `"YYYY-MM-DD HH:MM:SS"` naive string formats remain.

**Step 4: Run tests**

```bash
pytest polyedge/tests/test_app.py -k "datetime or human_dashboard" -v
```

Expected:
- PASS.

**Step 5: Commit**

```bash
git add polyedge/src/polyedge/app.py polyedge/src/polyedge/static/dashboard.html polyedge/tests/test_app.py
git commit -m "fix: normalize dashboard datetimes to UTC ISO format"
```

---

### Task 6: Improve Paper-Trade Scoring Cadence and Throughput

**Files:**
- Modify: `polyedge/src/polyedge/scheduler.py`
- Modify: `polyedge/src/polyedge/app.py`
- Test: `polyedge/tests/test_scheduler.py`

**Step 1: Reduce scoring interval**

Change:
- `loop(score_paper_trades, 3600, "score_paper_trades")`
to:
- `loop(score_paper_trades, 300, "score_paper_trades")`

**Step 2: Optimize scoring query**

Refactor scoring to:
- select only open trades joined with markets where `resolution IS NOT NULL AND resolution != ''`.
- avoid one `session.get()` per trade.

**Step 3: Add "last scored at" to dashboard**

Expose timestamp in API:
- `paper_trades.last_scored_at`
- include in hero detail text.

**Step 4: Add tests**

- interval config assertion
- scoring handles batch with joined rows
- timestamp field is present when scoring occurs

**Step 5: Run tests**

```bash
pytest polyedge/tests/test_scheduler.py polyedge/tests/test_app.py -k "score_paper_trades or last_scored" -v
```

Expected:
- PASS.

**Step 6: Commit**

```bash
git add polyedge/src/polyedge/scheduler.py polyedge/src/polyedge/app.py polyedge/tests/test_scheduler.py polyedge/tests/test_app.py
git commit -m "perf: score paper trades every 5 minutes with join-based batch scoring"
```

---

### Task 7: Implement Rule Tiering (Classify, Do Not Delete)

**Files:**
- Modify: `polyedge/src/polyedge/models.py`
- Create: `polyedge/deploy/migrations/004_trading_rule_tier.sql`
- Create: `polyedge/scripts/classify_rule_tiers.py`
- Modify: `polyedge/src/polyedge/scheduler.py`
- Modify: `polyedge/src/polyedge/app.py`
- Modify: `polyedge/src/polyedge/static/dashboard.html`
- Test: `polyedge/tests/test_scheduler.py`
- Test: `polyedge/tests/test_app.py`

**Step 1: Add schema**

`004_trading_rule_tier.sql`:
```sql
ALTER TABLE trading_rules ADD COLUMN IF NOT EXISTS tier INTEGER NOT NULL DEFAULT 3;
ALTER TABLE trading_rules ADD COLUMN IF NOT EXISTS quality_label VARCHAR(20) NOT NULL DEFAULT 'exploratory';
CREATE INDEX IF NOT EXISTS ix_trading_rules_tier_active ON trading_rules (tier, active);
```

**Step 2: Backfill classifier script**

`classify_rule_tiers.py` logic:
- Tier 1: ngram, 2+ words, each token length >= 4, sample_size >= 500
- Tier 2: ngram, one token length >= 4, sample_size >= 500
- Tier 3: everything else
- set `quality_label` accordingly.

**Step 3: Scheduler policy**

In `run_paper_trading`:
- allow Tier 1 always (subject to edge threshold)
- allow Tier 2 only with stricter `MIN_EDGE` (ex: 0.08)
- keep Tier 3 in DB but do not trade by default.

**Step 4: Dashboard visibility**

Include tier and quality label in:
- `top_rules`
- opportunity reasons
- open trade rows (badge/tag).

**Step 5: Tests**

- classifier assigns expected tier for short and long phrases
- scheduler includes/excludes tiers per policy
- API returns tier metadata

**Step 6: Run tests**

```bash
pytest polyedge/tests/test_scheduler.py polyedge/tests/test_app.py -k "tier or rules" -v
```

Expected:
- PASS.

**Step 7: Commit**

```bash
git add polyedge/src/polyedge/models.py polyedge/deploy/migrations/004_trading_rule_tier.sql polyedge/scripts/classify_rule_tiers.py polyedge/src/polyedge/scheduler.py polyedge/src/polyedge/app.py polyedge/src/polyedge/static/dashboard.html polyedge/tests/test_scheduler.py polyedge/tests/test_app.py
git commit -m "feat: add non-destructive trading rule tier classification and dashboard visibility"
```

---

### Task 8: Deployment Script Consistency for Required Migrations

**Files:**
- Modify: `polyedge/deploy/setup.sh`
- Modify: `polyedge/deploy/deploy_256gb.ps1`

**Step 1: Apply all required migrations**

Update both deploy paths to run:
- `001_v3_tables.sql`
- `002_market_resolution_source.sql`
- `003_service_heartbeats.sql`
- `004_trading_rule_tier.sql`

**Step 2: Add idempotent migration logging**

Print each migration name before execution and continue safely on "already exists" style conflicts.

**Step 3: Verify migration script order**

Run dry checks:
```bash
bash -n polyedge/deploy/setup.sh
powershell -NoProfile -Command "Get-Content polyedge/deploy/deploy_256gb.ps1 | Out-Null; Write-Output 'ok'"
```

Expected:
- syntax checks pass.

**Step 4: Commit**

```bash
git add polyedge/deploy/setup.sh polyedge/deploy/deploy_256gb.ps1
git commit -m "chore: align deployment scripts with full dashboard remediation migration chain"
```

---

### Task 9: Prompt + Problem List Corrections (Behavioral, Not Secret Cleanup)

**Files:**
- Modify: `docs/plans/2026-03-06-dashboard-fixes-handoff.md`
- Modify: `docs/plans/2026-03-06-next-agent-prompt.md`

**Step 1: Correct contradictory statements**

Update handoff text to:
- distinguish "stale open trades inflate counts" from "ending_soonest query includes past dates".
- specify UTC date assumptions.
- remove mutually inconsistent claims.

**Step 2: Remove data-corrupting recommendation**

In prompt, replace optional instruction to force-resolve junk trades with:
- "do not mutate historical outcome fields for cosmetic reporting."
- use filters and explicit labels for excluded cohorts.

**Step 3: Keep user-requested exclusions**

Do not add credential-rotation tasks in these docs.

**Step 4: Commit**

```bash
git add docs/plans/2026-03-06-dashboard-fixes-handoff.md docs/plans/2026-03-06-next-agent-prompt.md
git commit -m "docs: align handoff and agent prompt with verified behavior and non-destructive data policy"
```

---

### Task 10: End-to-End Verification Gate

**Files:**
- Reuse scripts from Task 1

**Step 1: Run tests**

```bash
pytest polyedge/tests/test_app.py polyedge/tests/test_scheduler.py polyedge/tests/test_poller.py -v
```

Expected:
- all targeted tests pass.

**Step 2: Run local API smoke**

```bash
python polyedge/scripts/audit_dashboard_state.py
python polyedge/scripts/audit_stale_markets.py
python polyedge/scripts/audit_rule_quality.py
```

Expected:
- no opportunities with past resolve date in dashboard payload
- stale unresolved markets trending down after reconciliation
- tier distributions visible and non-empty.

**Step 3: Manual acceptance checklist**

- "Ending Soonest" shows future-only dates.
- "Are We Making Money?" explains no-results vs bad-results clearly.
- Stats bar aligns with filtered real-trade cohort.
- Rule badges display tier/quality.
- Paper-trade counts reconcile with SQL audits.

**Step 4: Final commit**

```bash
git add -A
git commit -m "chore: finalize dashboard remediation and verification artifacts"
```

---

## Release Acceptance Criteria

1. No `/api/human-dashboard` runtime error when opportunities are empty.
2. All dashboard date fields are UTC ISO strings with `Z`.
3. Open-trade panels exclude expired markets and known junk cohorts consistently.
4. Stale unresolved market backlog is actively reconciled and measurable.
5. Paper-trade scoring runs at 5-minute cadence with visible `last_scored_at`.
6. Short ngram rules are retained in DB and classified; trading policy uses tiers.
7. Prompt and handoff docs reflect verified behavior and avoid destructive data advice.

