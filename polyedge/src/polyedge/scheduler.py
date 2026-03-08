"""Scheduler — runs all PolyEdge v3 loops."""
import asyncio
import json
import logging
import os
import socket
from datetime import date, datetime, timedelta, timezone

from polyedge.db import SessionLocal
from polyedge.db import settings as db_settings
from polyedge.poller import PolymarketPoller
from polyedge.analysis.scorer import score_resolved_markets
from polyedge.analysis.generate import generate_all_predictions
from polyedge.models import ServiceHeartbeat

log = logging.getLogger(__name__)


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _record_heartbeat(service: str, status: str, details: str = "") -> None:
    """Persist loop heartbeat so ops endpoints can verify runtime health."""
    try:
        async with SessionLocal() as session:
            row = await session.get(ServiceHeartbeat, service)
            now = _utcnow_naive()
            if row is None:
                row = ServiceHeartbeat(service=service)
                session.add(row)
            row.host = socket.gethostname()
            row.status = (status or "unknown")[:20]
            row.details = (details or "")[:2000]
            row.updated_at = now
            if row.status == "ok":
                row.last_success_at = now
            await session.commit()
    except Exception:
        log.exception("Failed to record heartbeat for %s", service)


def _scheduler_host_allowed() -> bool:
    """Optional hard guard: only run scheduler on one configured host."""
    required = (os.environ.get("POLYEDGE_SCHEDULER_HOST", "") or "").strip()
    if not required:
        return True
    current = socket.gethostname().strip().lower()
    if current != required.lower():
        log.error(
            "Scheduler host guard blocked execution (current=%s required=%s)",
            current,
            required,
        )
        return False
    return True



async def _safe_commit(session, *, loop_name: str) -> bool:
    """Commit and gracefully handle duplicate-open-trade races."""
    from sqlalchemy.exc import IntegrityError

    try:
        await session.commit()
        return True
    except IntegrityError as exc:
        await session.rollback()
        log.warning("%s commit conflict (likely duplicate open trade): %s", loop_name, exc)
        return False


# ============================================================
# EXISTING LOOPS (keep working)
# ============================================================

async def run_poller():
    """Poll Polymarket for market data."""
    poller = PolymarketPoller()
    try:
        count = await poller.poll_all()
        log.info("Poller cycle done: %d markets", count)
    finally:
        await poller.close()


async def poll_then_score():
    """Poll markets and score any newly resolved ones."""
    await run_poller()
    await score_resolved_markets()


# ============================================================
# NEW V3 LOOPS
# ============================================================

async def collect_daily_features():
    """Collect features from all available API connectors."""
    from polyedge.data.registry import discover_connectors, fetch_all_for_date
    from polyedge.models import DailyFeature
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    # Connector discovery + fetch path is sync/requests-heavy; run off the event loop.
    await asyncio.to_thread(discover_connectors)
    today = date.today()

    log.info("Collecting daily features for %s...", today)
    results = await asyncio.to_thread(fetch_all_for_date, today)

    if not results:
        log.warning("No features collected")
        return

    async with SessionLocal() as session:
        for source, category, name, value in results:
            stmt = pg_insert(DailyFeature).values(
                date=today, source=source, category=category,
                name=name, value=float(value),
            ).on_conflict_do_update(
                index_elements=["date", "name"],
                set_={"value": float(value), "source": source, "category": category},
            )
            await session.execute(stmt)
        await session.commit()

    log.info("Stored %d features for %s", len(results), today)


async def run_api_research():
    """Run Grok + Perplexity research and store parsed/distilled factors."""
    from polyedge.research.pipeline import run_api_research_cycle

    stats = await run_api_research_cycle()
    log.info("API research cycle: %s", stats)


async def run_supergod_research():
    """Dispatch codex-worker research tasks via the supergod orchestrator."""
    from polyedge.research.pipeline import run_supergod_research_dispatch

    stats = await run_supergod_research_dispatch()
    log.info("Supergod dispatch cycle: %s", stats)


async def ingest_supergod_research():
    """Ingest completed supergod task outputs into factor storage."""
    from polyedge.research.supergod_ingest import ingest_supergod_results

    ingested = await ingest_supergod_results(repo_path=db_settings.supergod_repo_path)
    log.info("Supergod ingest cycle: %d factors", ingested)


async def run_paper_trading():
    """Scan ALL active markets against ngram rules, open one paper trade per market
    where edge >= 5%. Fast: pure string matching, no heavy predict_market loop.

    Commits every 100 trades so the dashboard updates in real time.
    """
    from polyedge.models import Market, TradingRule, PaperTrade
    from sqlalchemy import select

    MIN_EDGE_TIER1 = 0.05
    MIN_EDGE_TIER2 = 0.08

    async with SessionLocal() as session:
        # Load quality ngram rules — these are fast to evaluate (string match)
        rules = (await session.execute(
            select(TradingRule).where(
                TradingRule.active == True,  # noqa: E712
                TradingRule.win_rate >= 0.60,
                TradingRule.sample_size >= 500,
                TradingRule.rule_type == "ngram",
                TradingRule.tier.in_([1, 2]),
            ).order_by(TradingRule.win_rate.desc())
        )).scalars().all()

        if not rules:
            log.info("No qualified ngram rules, skipping paper trading")
            return

        # Pre-parse rules into fast lookup structure
        parsed_rules: list[dict] = []
        for r in rules:
            try:
                cond = json.loads(r.conditions_json) if isinstance(r.conditions_json, str) else (r.conditions_json or {})
            except (json.JSONDecodeError, TypeError):
                continue
            phrase = str(cond.get("ngram", "")).strip().lower()
            if not phrase:
                continue
            tier = int(r.tier or 3)
            parsed_rules.append({
                "id": r.id, "phrase": phrase, "side": r.predicted_side,
                "win_rate": r.win_rate, "breakeven": r.breakeven_price or r.win_rate,
                "tier": tier,
                "min_edge": MIN_EDGE_TIER1 if tier == 1 else MIN_EDGE_TIER2,
                "market_filter": (r.market_filter or "").strip(),
            })
        log.info("Loaded %d ngram rules for paper trading", len(parsed_rules))

        # Load active markets ending within 48 hours (capital efficiency)
        now = _utcnow_naive()
        max_end = now + timedelta(hours=48)
        markets = (await session.execute(
            select(Market).where(
                Market.active == True,  # noqa: E712
                Market.end_date.is_not(None),
                Market.end_date > now,
                Market.end_date <= max_end,
            )
        )).scalars().all()
        log.info("Scanning %d active markets ending within 48h", len(markets))

        # Pre-load existing open ngram trades — one trade per market+side+source
        open_pt_rows = (await session.execute(
            select(PaperTrade.market_id, PaperTrade.side)
            .where(
                PaperTrade.resolved == False,  # noqa: E712
                PaperTrade.trade_source == "ngram",
            )
        )).all()
        existing_trades: set[tuple[str, str]] = {(r[0], r[1]) for r in open_pt_rows}

        # Pre-load existing open ngram_inverse trades
        open_inv_rows = (await session.execute(
            select(PaperTrade.market_id, PaperTrade.side)
            .where(
                PaperTrade.resolved == False,  # noqa: E712
                PaperTrade.trade_source == "ngram_inverse",
            )
        )).all()
        existing_inverse: set[tuple[str, str]] = {(r[0], r[1]) for r in open_inv_rows}

        trades_opened = 0
        skipped_expired = 0
        batch_pending = 0
        for i, market in enumerate(markets):
            q_lower = (market.question or "").lower()

            # Skip stale markets that are already past end date.
            if market.end_date is not None and market.end_date < now:
                skipped_expired += 1
                continue

            yes_price = float(market.yes_price or 0.5)
            no_price = float(market.no_price) if market.no_price is not None else max(0.001, 1.0 - yes_price)

            # Skip dead markets where both sides are near 0 or 1
            if yes_price <= 0.02 or yes_price >= 0.98:
                continue

            # Find all matching rules and pick the best one per side
            best_by_side: dict[str, dict] = {}  # side -> best match
            for rule in parsed_rules:
                if rule["phrase"] not in q_lower:
                    continue
                # Enforce category scope
                mf = rule["market_filter"]
                if mf and mf != (getattr(market, "market_category", "") or ""):
                    continue
                side = rule["side"]
                entry = yes_price if side == "YES" else no_price
                edge = rule["breakeven"] - entry
                if edge < rule["min_edge"]:
                    continue
                existing = best_by_side.get(side)
                if existing is None or edge > existing["edge"]:
                    best_by_side[side] = {
                        "rule_id": rule["id"], "side": side,
                        "entry": entry, "edge": edge,
                    }

            # Open one trade per side that has edge, plus inverse control trade
            for match in best_by_side.values():
                trade_key = (market.id, match["side"])
                if trade_key in existing_trades:
                    continue

                trade = PaperTrade(
                    market_id=market.id,
                    rule_id=match["rule_id"],
                    side=match["side"],
                    entry_price=match["entry"],
                    edge=match["edge"],
                    bet_size=1.0,
                    resolved=False,
                )
                session.add(trade)
                existing_trades.add(trade_key)
                trades_opened += 1
                batch_pending += 1

                # Inverse control: bet the opposite side at actual market price
                inv_side = "NO" if match["side"] == "YES" else "YES"
                inv_entry = no_price if match["side"] == "YES" else yes_price
                inv_key = (market.id, inv_side)
                if inv_key not in existing_inverse:
                    inv_trade = PaperTrade(
                        market_id=market.id,
                        rule_id=match["rule_id"],
                        side=inv_side,
                        entry_price=inv_entry,
                        edge=0.0,
                        bet_size=1.0,
                        trade_source="ngram_inverse",
                        resolved=False,
                    )
                    session.add(inv_trade)
                    existing_inverse.add(inv_key)
                    batch_pending += 1

            # Commit in batches so dashboard sees results immediately
            if batch_pending >= 100:
                await _safe_commit(session, loop_name="paper_trading")
                log.info("Paper trading: %d new trades so far (%d/%d markets)",
                         trades_opened, i + 1, len(markets))
                batch_pending = 0

        # Keep session state clean even on no-op cycles.
        if batch_pending > 0 or trades_opened == 0:
            await _safe_commit(session, loop_name="paper_trading")

    log.info(
        "Paper trading complete: %d new trades from %d markets (skipped %d expired)",
        trades_opened, len(markets), skipped_expired,
    )


async def score_paper_trades():
    """Score paper trades for resolved markets."""
    from polyedge.models import Market, PaperTrade
    from polyedge.trading.pnl import calc_pnl
    from sqlalchemy import select

    async with SessionLocal() as session:
        # Score only open trades whose markets have a concrete resolution label.
        rows = (await session.execute(
            select(PaperTrade, Market.resolution)
            .join(Market, Market.id == PaperTrade.market_id)
            .where(
                PaperTrade.resolved == False,  # noqa: E712
                Market.resolution.is_not(None),
                Market.resolution != "",
            )
        )).all()

        scored = 0
        for trade, resolution in rows:
            normalized = str(resolution or "").upper().strip()
            if normalized in ("YES", "NO"):
                won = (normalized == trade.side)
                trade.won = won
                trade.pnl = calc_pnl(trade.entry_price, won)
            else:
                # Non-binary outcomes (e.g. CANCELLED/N/A) are void for PnL accounting.
                trade.won = None
                trade.pnl = 0.0
            trade.resolved = True
            trade.resolved_at = _utcnow_naive()
            scored += 1

        await _safe_commit(session, loop_name="score_paper_trades")

    if scored:
        log.info("Scored %d paper trades", scored)


CATEGORIES = [
    "crypto_updown", "sports_ou", "sports_spread", "crypto_other",
    "sports_winner", "politics_us", "sports_props", "entertainment",
    "politics_intl", "science_tech", "economics", "other",
]


async def run_correlation_refresh():
    """Re-run correlation engine: once globally, then once per category."""
    from polyedge.analysis.feature_matrix import build_matrix
    from polyedge.analysis.correlation_engine import run_discovery
    from polyedge.analysis.rule_generator import generate_both_sides, store_rules

    total_stored = 0

    # Global run (no category filter)
    try:
        df = await build_matrix(limit=50000)
        if len(df) >= 100:
            rules = run_discovery(df, min_samples=30, min_edge=0.05)
            rule_dicts = [r.to_dict() for r in rules]
            both_sides = generate_both_sides(rule_dicts)
            await store_rules(both_sides)
            total_stored += len(both_sides)
            log.info("Global correlation: %d rules discovered, %d stored", len(rules), len(both_sides))
    except Exception as e:
        log.error("Global correlation failed: %s", e, exc_info=True)

    # Per-category runs
    for cat in CATEGORIES:
        try:
            df = await build_matrix(limit=50000, category_filter=cat)
            if len(df) < 50:
                log.info("Category %s: only %d markets, skipping", cat, len(df))
                continue
            rules = run_discovery(df, min_samples=20, min_edge=0.05)
            # Tag rules with market_filter
            rule_dicts = []
            for r in rules:
                d = r.to_dict()
                d["market_filter"] = cat
                rule_dicts.append(d)
            both_sides = generate_both_sides(rule_dicts)
            await store_rules(both_sides)
            total_stored += len(both_sides)
            log.info("Category %s: %d rules discovered, %d stored", cat, len(rules), len(both_sides))
        except Exception as e:
            log.error("Category %s correlation failed: %s", cat, e, exc_info=True)

    log.info("Correlation refresh complete: %d total rules stored", total_stored)


async def run_llm_paper_trading():
    """Open paper trades based on LLM predictions (Grok/Perplexity factors).

    Reads existing predictions from the predictions table. The confidence
    values are NOT probabilities — they are conviction scores (typically
    0.01-0.50) from rule agreement. We use confidence as a conviction
    filter and entry price as a risk filter, not confidence-minus-price.

    Criteria to trade:
    - Confidence >= 0.15 (top ~25% of predictions, system has conviction)
    - Entry price <= 0.50 (favorable risk/reward — win big, lose small)
    - Market ends within 48h
    """
    from polyedge.models import Market, Prediction, PaperTrade
    from sqlalchemy import func, select

    MIN_CONFIDENCE = 0.15  # conviction threshold (top ~25% of predictions)
    MAX_ENTRY_PRICE = 0.50  # risk/reward filter (win >= loss)
    now = _utcnow_naive()
    max_end = now + timedelta(hours=48)

    async with SessionLocal() as session:
        # Get the most recent prediction per market by created_at (tie-break on id).
        latest_pred_ranked = (
            select(
                Prediction.id.label("prediction_id"),
                Prediction.market_id,
                func.row_number().over(
                    partition_by=Prediction.market_id,
                    order_by=(Prediction.created_at.desc(), Prediction.id.desc()),
                ).label("rn"),
            )
            .subquery()
        )

        # Join to get full prediction rows for markets ending within 48h
        pred_rows = (await session.execute(
            select(Prediction, Market)
            .join(
                latest_pred_ranked,
                Prediction.id == latest_pred_ranked.c.prediction_id,
            )
            .join(Market, Market.id == Prediction.market_id)
            .where(
                latest_pred_ranked.c.rn == 1,
                Market.active == True,  # noqa: E712
                Market.end_date.is_not(None),
                Market.end_date > now,
                Market.end_date <= max_end,
            )
        )).all()

        if not pred_rows:
            log.info("LLM paper trading: 0 predictions for 48h markets")
            return

        # Pre-load existing open LLM trades
        open_pt_rows = (await session.execute(
            select(PaperTrade.market_id, PaperTrade.side)
            .where(
                PaperTrade.resolved == False,  # noqa: E712
                PaperTrade.trade_source == "llm",
            )
        )).all()
        existing: set[tuple[str, str]] = {(r[0], r[1]) for r in open_pt_rows}

        # Pre-load existing open llm_inverse trades
        open_inv_rows = (await session.execute(
            select(PaperTrade.market_id, PaperTrade.side)
            .where(
                PaperTrade.resolved == False,  # noqa: E712
                PaperTrade.trade_source == "llm_inverse",
            )
        )).all()
        existing_inverse: set[tuple[str, str]] = {(r[0], r[1]) for r in open_inv_rows}

        trades_opened = 0
        for pred, market in pred_rows:
            side = (pred.predicted_outcome or "").upper()
            if side not in ("YES", "NO"):
                continue

            # Skip low-conviction predictions
            if pred.confidence < MIN_CONFIDENCE:
                continue

            yes_price = float(market.yes_price or 0.5)
            no_price = float(market.no_price) if market.no_price is not None else max(0.001, 1.0 - yes_price)

            # Skip dead markets
            if yes_price <= 0.02 or yes_price >= 0.98:
                continue

            if side == "YES":
                entry = yes_price
            else:
                entry = no_price

            # Only trade when entry price gives favorable risk/reward
            if entry > MAX_ENTRY_PRICE:
                continue

            # Edge = potential win (1 - entry) vs potential loss (entry)
            # At entry 0.30: win $0.70, lose $0.30 — great risk/reward
            # At entry 0.50: win $0.50, lose $0.50 — breakeven risk/reward
            edge = 1.0 - entry - entry  # net edge = win - loss = (1-entry) - entry

            trade_key = (market.id, side)
            if trade_key in existing:
                continue

            trade = PaperTrade(
                market_id=market.id,
                rule_id=None,
                side=side,
                entry_price=entry,
                edge=edge,
                bet_size=1.0,
                trade_source="llm",
                resolved=False,
            )
            session.add(trade)
            existing.add(trade_key)
            trades_opened += 1

            # Inverse control: bet the opposite side at actual market price
            inv_side = "NO" if side == "YES" else "YES"
            inv_entry = no_price if side == "YES" else yes_price
            inv_key = (market.id, inv_side)
            if inv_key not in existing_inverse:
                inv_trade = PaperTrade(
                    market_id=market.id,
                    rule_id=None,
                    side=inv_side,
                    entry_price=inv_entry,
                    edge=0.0,
                    bet_size=1.0,
                    trade_source="llm_inverse",
                    resolved=False,
                )
                session.add(inv_trade)
                existing_inverse.add(inv_key)

        await _safe_commit(session, loop_name="llm_paper_trading")

    log.info("LLM paper trading: %d new trades from %d predictions", trades_opened, len(pred_rows))


async def run_combined_paper_trading():
    """Open paper trades only when BOTH ngram rules AND LLM predictions agree.

    Checks markets ending within 48h. For each market, looks for:
    1. A matching ngram rule with edge >= 5%
    2. An LLM prediction with edge >= 10%
    If both exist and predict the SAME side, opens a combined trade.
    """
    from polyedge.models import Market, PaperTrade, Prediction, TradingRule
    from sqlalchemy import func, select

    MIN_NGRAM_EDGE = 0.05
    now = _utcnow_naive()
    max_end = now + timedelta(hours=48)

    async with SessionLocal() as session:
        # Load ngram rules
        rules = (await session.execute(
            select(TradingRule).where(
                TradingRule.active == True,  # noqa: E712
                TradingRule.win_rate >= 0.60,
                TradingRule.sample_size >= 500,
                TradingRule.rule_type == "ngram",
                TradingRule.tier.in_([1, 2]),
            )
        )).scalars().all()

        if not rules:
            log.info("Combined paper trading: no qualified ngram rules")
            return

        parsed_rules: list[dict] = []
        for r in rules:
            try:
                cond = json.loads(r.conditions_json) if isinstance(r.conditions_json, str) else (r.conditions_json or {})
            except (json.JSONDecodeError, TypeError):
                continue
            phrase = str(cond.get("ngram", "")).strip().lower()
            if not phrase:
                continue
            parsed_rules.append({
                "id": r.id, "phrase": phrase, "side": r.predicted_side,
                "win_rate": r.win_rate, "breakeven": r.breakeven_price or r.win_rate,
                "market_filter": (r.market_filter or "").strip(),
            })

        # Load markets ending within 48h
        markets = (await session.execute(
            select(Market).where(
                Market.active == True,  # noqa: E712
                Market.end_date.is_not(None),
                Market.end_date > now,
                Market.end_date <= max_end,
            )
        )).scalars().all()

        if not markets:
            log.info("Combined paper trading: no markets ending within 48h")
            return

        # Load latest predictions keyed by market_id (latest by created_at).
        market_ids = [m.id for m in markets]
        latest_pred_ranked = (
            select(
                Prediction.id.label("prediction_id"),
                Prediction.market_id,
                func.row_number().over(
                    partition_by=Prediction.market_id,
                    order_by=(Prediction.created_at.desc(), Prediction.id.desc()),
                ).label("rn"),
            )
            .where(Prediction.market_id.in_(market_ids))
            .subquery()
        )
        pred_rows = (await session.execute(
            select(Prediction)
            .join(
                latest_pred_ranked,
                Prediction.id == latest_pred_ranked.c.prediction_id,
            )
            .where(latest_pred_ranked.c.rn == 1)
        )).scalars().all()
        preds_by_market: dict[str, Prediction] = {p.market_id: p for p in pred_rows}

        # Pre-load existing open combined trades
        open_pt_rows = (await session.execute(
            select(PaperTrade.market_id, PaperTrade.side)
            .where(
                PaperTrade.resolved == False,  # noqa: E712
                PaperTrade.trade_source == "combined",
            )
        )).all()
        existing: set[tuple[str, str]] = {(r[0], r[1]) for r in open_pt_rows}

        # Pre-load existing open combined_inverse trades
        open_inv_rows = (await session.execute(
            select(PaperTrade.market_id, PaperTrade.side)
            .where(
                PaperTrade.resolved == False,  # noqa: E712
                PaperTrade.trade_source == "combined_inverse",
            )
        )).all()
        existing_inverse: set[tuple[str, str]] = {(r[0], r[1]) for r in open_inv_rows}

        trades_opened = 0
        for market in markets:
            q_lower = (market.question or "").lower()

            yes_price = float(market.yes_price or 0.5)
            no_price = float(market.no_price) if market.no_price is not None else max(0.001, 1.0 - yes_price)
            if yes_price <= 0.02 or yes_price >= 0.98:
                continue

            # Find best ngram signal per side
            ngram_signals: dict[str, dict] = {}
            for rule in parsed_rules:
                if rule["phrase"] not in q_lower:
                    continue
                # Enforce category scope
                mf = rule["market_filter"]
                if mf and mf != (getattr(market, "market_category", "") or ""):
                    continue
                side = rule["side"]
                entry = yes_price if side == "YES" else no_price
                edge = rule["breakeven"] - entry
                if edge < MIN_NGRAM_EDGE:
                    continue
                existing_sig = ngram_signals.get(side)
                if existing_sig is None or edge > existing_sig["edge"]:
                    ngram_signals[side] = {"rule_id": rule["id"], "side": side, "entry": entry, "edge": edge}

            if not ngram_signals:
                continue

            # Check LLM prediction (confidence is conviction, not probability)
            pred = preds_by_market.get(market.id)
            if pred is None:
                continue

            llm_side = (pred.predicted_outcome or "").upper()
            if llm_side not in ("YES", "NO"):
                continue
            if pred.confidence < 0.15:
                continue

            # Check agreement
            if llm_side not in ngram_signals:
                continue

            ngram_sig = ngram_signals[llm_side]
            combined_edge = ngram_sig["edge"]  # use ngram edge (LLM confidence is conviction, not edge)

            trade_key = (market.id, llm_side)
            if trade_key in existing:
                continue

            trade = PaperTrade(
                market_id=market.id,
                rule_id=ngram_sig["rule_id"],
                side=llm_side,
                entry_price=ngram_sig["entry"],
                edge=combined_edge,
                bet_size=1.0,
                trade_source="combined",
                resolved=False,
            )
            session.add(trade)
            existing.add(trade_key)
            trades_opened += 1

            # Inverse control: bet the opposite side at actual market price
            inv_side = "NO" if llm_side == "YES" else "YES"
            inv_entry = no_price if llm_side == "YES" else yes_price
            inv_key = (market.id, inv_side)
            if inv_key not in existing_inverse:
                inv_trade = PaperTrade(
                    market_id=market.id,
                    rule_id=ngram_sig["rule_id"],
                    side=inv_side,
                    entry_price=inv_entry,
                    edge=0.0,
                    bet_size=1.0,
                    trade_source="combined_inverse",
                    resolved=False,
                )
                session.add(inv_trade)
                existing_inverse.add(inv_key)

        await _safe_commit(session, loop_name="combined_paper_trading")

    log.info("Combined paper trading: %d new trades", trades_opened)


async def check_resolutions():
    """Fast-path: only fetch markets we have open trades on that should have resolved.

    Runs every 60s. Instead of polling ALL markets, fetches by ID only the ones
    whose end_date has passed and we still have unresolved paper trades on.
    Then immediately scores any that resolved.
    """
    from polyedge.models import Market, PaperTrade
    from sqlalchemy import select

    now = _utcnow_naive()

    # Find markets with open paper trades whose end_date has passed.
    # Prioritize the most recently ended markets so today's trades get resolved first,
    # not blocked behind months-old stuck markets.
    async with SessionLocal() as session:
        pending_rows = (await session.execute(
            select(Market.id, Market.end_date)
            .join(PaperTrade, PaperTrade.market_id == Market.id)
            .where(
                PaperTrade.resolved == False,  # noqa: E712
                Market.end_date.is_not(None),
                Market.end_date < now,
                Market.resolution.is_(None) | (Market.resolution == ""),
            )
            .distinct()
            .order_by(Market.end_date.desc())
            .limit(500)
        )).all()
        pending_ids = [row[0] for row in pending_rows]

    if not pending_ids:
        log.info("Resolution check: 0 markets pending")
        return

    log.info("Resolution check: fetching %d markets that should have resolved", len(pending_ids))

    poller = PolymarketPoller()
    resolved_count = 0
    try:
        async with SessionLocal() as session:
            for market_id in pending_ids:
                try:
                    raw = await poller.fetch_market_by_id(market_id)
                    if raw is None:
                        continue
                    from polyedge.poller import parse_market
                    parsed = parse_market(raw)
                    existing = await session.get(Market, market_id)
                    if existing:
                        for k, v in parsed.items():
                            setattr(existing, k, v)
                        existing.updated_at = now
                        if parsed.get("resolution"):
                            resolved_count += 1
                except Exception:
                    log.debug("Failed to fetch market %s", market_id, exc_info=True)
            await session.commit()
    finally:
        await poller.close()

    log.info("Resolution check: %d/%d markets now resolved", resolved_count, len(pending_ids))

    # Immediately score the newly resolved trades
    if resolved_count > 0:
        await score_paper_trades()


async def run_research_rule_bridge():
    """Generate new rules from recent research factors — global + per-category."""
    from polyedge.analysis.research_rule_bridge import generate_rules_from_research

    # Global run
    stats = await generate_rules_from_research()
    log.info("Research-to-rules bridge (global): %s", stats)

    # Per-category runs
    for cat in CATEGORIES:
        try:
            cat_stats = await generate_rules_from_research(category_filter=cat)
            if cat_stats.get("new_rules", 0) > 0:
                log.info("Research-to-rules bridge (%s): %s", cat, cat_stats)
        except Exception as e:
            log.error("Research rule bridge for %s failed: %s", cat, e, exc_info=True)


async def run_backtest_refresh():
    """Re-run full ngram backtest (weekly). Heavy computation."""
    from polyedge.analysis.backtest_runner import backtest_ngram_rules
    stats = await backtest_ngram_rules(batch_size=500)
    log.info("Backtest refresh: %s", stats)


async def run_agreement_refresh():
    """Re-run agreement analysis (daily)."""
    from polyedge.analysis.agreement_calculator import run_agreement_analysis
    stats = await run_agreement_analysis()
    log.info("Agreement refresh: %s", stats)


# ============================================================
# MAIN SCHEDULER
# ============================================================

async def run_forever():
    """Main scheduler loop with all v3 systems."""
    if not _scheduler_host_allowed():
        return
    log.info("PolyEdge v3 scheduler starting")

    async def loop(fn, interval_seconds: int, name: str):
        while True:
            try:
                log.info("Running %s...", name)
                await _record_heartbeat(name, "running", details="cycle_started")
                result = await fn()
                await _record_heartbeat(name, "ok", details=str(result))
            except Exception as e:
                log.error("%s failed: %s", name, e, exc_info=True)
                await _record_heartbeat(name, "error", details=str(e))
            await asyncio.sleep(interval_seconds)

    await asyncio.gather(
        # Core polling + scoring (every 5 min)
        loop(poll_then_score, 300, "poller+scorer"),

        # Prediction generation (every 1 hour, independent to prevent duplicates)
        loop(generate_all_predictions, 3600, "predictions"),

        # LLM API research and codex-worker distillation pipeline
        loop(run_api_research, 1800, "api_research"),
        loop(run_supergod_research, 1800, "supergod_dispatch"),
        loop(ingest_supergod_research, 600, "supergod_ingest"),

        # Feature collection (every 6 hours)
        loop(collect_daily_features, 21600, "feature_collection"),

        # Paper trading: ngram rules (every 5 min, 48h horizon)
        loop(run_paper_trading, 300, "paper_trading"),

        # Paper trading: LLM predictions (every 5 min, 48h horizon)
        loop(run_llm_paper_trading, 300, "llm_paper_trading"),

        # Paper trading: combined ngram+LLM (every 5 min, 48h horizon)
        loop(run_combined_paper_trading, 300, "combined_paper_trading"),

        # Score paper trades (every 5 min)
        loop(score_paper_trades, 300, "score_paper_trades"),

        # Fast resolution checker — only polls markets with open trades past end_date (every 60s)
        loop(check_resolutions, 60, "resolution_check"),

        # Correlation refresh (every 24 hours)
        loop(run_correlation_refresh, 86400, "correlation_refresh"),

        # Research-to-rules bridge (every 6 hours — 21600 seconds)
        loop(run_research_rule_bridge, 21600, "research_rule_bridge"),

        # Backtest refresh (weekly — 604800 seconds)
        loop(run_backtest_refresh, 604800, "backtest_refresh"),

        # Agreement analysis (daily — 86400 seconds)
        loop(run_agreement_refresh, 86400, "agreement_refresh"),
    )
