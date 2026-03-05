"""Fetch historical CLOB trading prices for all markets.

Run on the 256GB server (88.99.142.89):
    python price_fetcher.py

Reads: C:\\polyedge\\data\\resolved_markets.csv (has market IDs)
Writes: C:\\polyedge\\data\\results\\price_history_all.csv

Uses Gamma API for token IDs, CLOB API for price history.
ThreadPoolExecutor for parallelism with checkpointing.
"""

import csv
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger(__name__)

DATA_DIR = Path(r"C:\polyedge\data")
RESULTS_DIR = DATA_DIR / "results"
CHECKPOINT_FILE = RESULTS_DIR / "price_fetch_checkpoint.txt"
OUTPUT_FILE = RESULTS_DIR / "price_history_all.csv"

CLOB_URL = "https://clob.polymarket.com"
GAMMA_URL = "https://gamma-api.polymarket.com"

OUTPUT_FIELDS = ["market_id", "timestamp", "yes_price"]

session = requests.Session()
session.headers.update({"User-Agent": "polyedge/1.0"})


def get_token_id(market_id: str) -> str | None:
    """Get first clobTokenId for a market from Gamma API."""
    try:
        resp = session.get(f"{GAMMA_URL}/markets/{market_id}", timeout=10)
        if resp.status_code != 200:
            return None
        data = resp.json()
        tokens = data.get("clobTokenIds", "")
        if isinstance(tokens, str):
            tokens = json.loads(tokens) if tokens else []
        return tokens[0] if tokens else None
    except Exception:
        log.debug("Failed to get token ID for %s", market_id, exc_info=True)
        return None


def fetch_price_history(market_id: str, token_id: str) -> list[dict] | None:
    """Fetch full price history for a market's YES token."""
    try:
        resp = session.get(
            f"{CLOB_URL}/prices-history",
            params={"market": token_id, "interval": "all", "fidelity": 60},
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        history = data.get("history", []) if isinstance(data, dict) else []
        if len(history) < 2:
            return None
        return history
    except Exception:
        log.debug(
            "Failed to fetch history for %s (token %s)",
            market_id,
            token_id,
            exc_info=True,
        )
        return None


def process_market(market_id: str) -> list[dict] | None:
    """Fetch token ID and price history for one market.

    Returns list of dicts with keys: market_id, timestamp, yes_price.
    Returns None if token ID or history unavailable.
    """
    token_id = get_token_id(market_id)
    if not token_id:
        return None
    history = fetch_price_history(market_id, token_id)
    if not history:
        return None
    return [
        {
            "market_id": market_id,
            "timestamp": datetime.fromtimestamp(h["t"], tz=timezone.utc).isoformat(),
            "yes_price": h["p"],
        }
        for h in history
    ]


def load_checkpoint(checkpoint_path: Path | None = None) -> set:
    """Load set of already-processed market IDs."""
    path = checkpoint_path or CHECKPOINT_FILE
    if path.exists():
        text = path.read_text().strip()
        if text:
            return set(text.split("\n"))
    return set()


def save_checkpoint(market_id: str, checkpoint_path: Path | None = None):
    """Append a processed market ID to checkpoint."""
    path = checkpoint_path or CHECKPOINT_FILE
    with open(path, "a") as f:
        f.write(market_id + "\n")


def main():
    import pandas as pd

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    log.info("Loading markets...")
    markets_csv = DATA_DIR / "resolved_markets.csv"
    if not markets_csv.exists():
        log.error("resolved_markets.csv not found at %s", markets_csv)
        return

    df = pd.read_csv(markets_csv, low_memory=False)
    market_ids = list(df["id"].dropna().unique())
    log.info("Total markets: %d", len(market_ids))

    # Resume from checkpoint
    done = load_checkpoint()
    remaining = [mid for mid in market_ids if mid not in done]
    log.info("Already done: %d, remaining: %d", len(done), len(remaining))

    if not remaining:
        log.info("Nothing to do -- all markets already processed.")
        return

    # Open output CSV in append mode
    write_header = not OUTPUT_FILE.exists() or OUTPUT_FILE.stat().st_size == 0

    total_rows = 0
    fetched = 0
    failed = 0

    with open(OUTPUT_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        if write_header:
            writer.writeheader()

        BATCH_SIZE = 1000
        WORKERS = 30

        for batch_start in range(0, len(remaining), BATCH_SIZE):
            batch = remaining[batch_start : batch_start + BATCH_SIZE]
            batch_rows = 0

            with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                futures = {
                    pool.submit(process_market, mid): mid for mid in batch
                }
                for future in as_completed(futures):
                    mid = futures[future]
                    try:
                        rows = future.result()
                        if rows:
                            writer.writerows(rows)
                            total_rows += len(rows)
                            batch_rows += len(rows)
                            fetched += 1
                        else:
                            failed += 1
                    except Exception:
                        log.debug("Exception processing %s", mid, exc_info=True)
                        failed += 1
                    save_checkpoint(mid)

            f.flush()
            log.info(
                "Batch %d-%d: %d markets fetched, %d failed, "
                "%d price rows this batch, %d total rows",
                batch_start,
                batch_start + len(batch),
                fetched,
                failed,
                batch_rows,
                total_rows,
            )

    log.info(
        "DONE. Fetched %d markets, %d failed, %d total price rows saved to %s",
        fetched,
        failed,
        total_rows,
        OUTPUT_FILE,
    )


if __name__ == "__main__":
    main()
