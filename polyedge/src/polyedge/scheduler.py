"""Scheduler — runs all PolyEdge v3 loops."""
import asyncio
import json
import logging
import os
import socket
from datetime import date, datetime, timezone

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

    MIN_EDGE = 0.05  # 5% minimum edge

    async with SessionLocal() as session:
        # Load quality ngram rules — these are fast to evaluate (string match)
        rules = (await session.execute(
            select(TradingRule).where(
                TradingRule.active == True,  # noqa: E712
                TradingRule.win_rate >= 0.60,
                TradingRule.sample_size >= 500,
                TradingRule.rule_type == "ngram",
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
            if not phrase or len(phrase) < 4:
                continue
            parsed_rules.append({
                "id": r.id, "phrase": phrase, "side": r.predicted_side,
                "win_rate": r.win_rate, "breakeven": r.breakeven_price or r.win_rate,
            })
        log.info("Loaded %d ngram rules for paper trading", len(parsed_rules))

        # Load ALL active markets
        markets = (await session.execute(
            select(Market).where(Market.active == True)  # noqa: E712
        )).scalars().all()
        log.info("Scanning %d active markets for edges >= %d%%", len(markets), int(MIN_EDGE * 100))

        # Pre-load existing open trades — one trade per market+side
        open_pt_rows = (await session.execute(
            select(PaperTrade.market_id, PaperTrade.side)
            .where(PaperTrade.resolved == False)  # noqa: E712
        )).all()
        existing_trades: set[tuple[str, str]] = {(r[0], r[1]) for r in open_pt_rows}

        trades_opened = 0
        skipped_noise = 0
        batch_pending = 0
        for i, market in enumerate(markets):
            q_lower = (market.question or "").lower()

            # Skip high-frequency noise markets (5-min crypto predictions etc.)
            if "up or down" in q_lower:
                skipped_noise += 1
                continue

            yes_price = float(market.yes_price or 0.5)
            no_price = max(0.001, 1.0 - yes_price)

            # Skip dead markets where both sides are near 0 or 1
            if yes_price <= 0.02 or yes_price >= 0.98:
                continue

            # Find all matching rules and pick the best one per side
            best_by_side: dict[str, dict] = {}  # side -> best match
            for rule in parsed_rules:
                if rule["phrase"] not in q_lower:
                    continue
                side = rule["side"]
                entry = yes_price if side == "YES" else no_price
                edge = rule["breakeven"] - entry
                if edge < MIN_EDGE:
                    continue
                existing = best_by_side.get(side)
                if existing is None or edge > existing["edge"]:
                    best_by_side[side] = {
                        "rule_id": rule["id"], "side": side,
                        "entry": entry, "edge": edge,
                    }

            # Open one trade per side that has edge
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

            # Commit in batches so dashboard sees results immediately
            if batch_pending >= 100:
                await session.commit()
                log.info("Paper trading: %d new trades so far (%d/%d markets)",
                         trades_opened, i + 1, len(markets))
                batch_pending = 0

        if batch_pending > 0:
            await session.commit()

    log.info("Paper trading complete: %d new trades from %d markets (skipped %d noise)",
             trades_opened, len(markets), skipped_noise)


async def score_paper_trades():
    """Score paper trades for resolved markets."""
    from polyedge.models import Market, PaperTrade
    from polyedge.trading.pnl import calc_pnl
    from sqlalchemy import select, and_

    async with SessionLocal() as session:
        # Find open trades where market has resolved
        open_trades = (await session.execute(
            select(PaperTrade).where(PaperTrade.resolved == False)
        )).scalars().all()

        scored = 0
        for trade in open_trades:
            market = await session.get(Market, trade.market_id)
            if not market or not market.resolution:
                continue

            won = (market.resolution.upper() == trade.side)
            trade.won = won
            trade.pnl = calc_pnl(trade.entry_price, won)
            trade.resolved = True
            trade.resolved_at = datetime.utcnow()
            scored += 1

        await session.commit()

    if scored:
        log.info("Scored %d paper trades", scored)


async def run_correlation_refresh():
    """Re-run correlation engine with latest data (weekly)."""
    log.info("Starting correlation refresh...")
    try:
        from polyedge.analysis.feature_matrix import build_matrix
        from polyedge.analysis.correlation_engine import run_discovery
        from polyedge.analysis.rule_generator import generate_both_sides, store_rules

        # Build matrix
        df = await build_matrix(limit=50000)  # limit for performance
        if len(df) < 100:
            log.warning("Not enough data for correlation (%d rows)", len(df))
            return

        # Run discovery
        rules = run_discovery(df, min_samples=30, min_edge=0.05)
        log.info("Discovered %d rules", len(rules))

        # Generate both sides
        rule_dicts = [r.to_dict() for r in rules]
        both_sides = generate_both_sides(rule_dicts)

        # Store
        await store_rules(both_sides)
        log.info("Stored %d rules (both sides)", len(both_sides))

    except Exception as e:
        log.error("Correlation refresh failed: %s", e, exc_info=True)


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

        # Paper trading: predict + open trades (every 5 min)
        loop(run_paper_trading, 300, "paper_trading"),

        # Score paper trades (every 1 hour)
        loop(score_paper_trades, 3600, "score_paper_trades"),

        # Correlation refresh (every 24 hours)
        loop(run_correlation_refresh, 86400, "correlation_refresh"),
    )
