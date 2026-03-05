"""
Advanced feature extraction:
1. Richer text features (dollar amounts, percentages, time references, named entities)
2. Time-to-resolution features
3. Market structural features (volume, price at creation, etc.)
4. FRED economic data (Fed rate, CPI, unemployment, yield curve)
"""
import re
import json
import logging
import math
import time
from datetime import datetime, date, timedelta
from pathlib import Path

import pandas as pd
import numpy as np
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DATA_DIR = Path(r"C:\polyedge\data")
FEATURES_DIR = DATA_DIR / "features"
FEATURES_DIR.mkdir(parents=True, exist_ok=True)

# FRED API key (free, get from https://fred.stlouisfed.org/docs/api/api_key.html)
# Using a public demo key that works for basic requests
# FRED API key — set via environment variable
# Get your own free key at https://fred.stlouisfed.org/docs/api/api_key.html
import os
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")


# ──────────────────────────────────────────────────────────────
# 1. ADVANCED TEXT FEATURES
# ──────────────────────────────────────────────────────────────

def extract_advanced_text(question: str) -> dict:
    """Extract richer features from market question text."""
    q = str(question).lower().strip()
    qorig = str(question).strip()
    f = {}

    # Extract dollar amounts
    dollar_matches = re.findall(r'\$[\d,]+(?:\.\d+)?(?:\s*(?:k|m|b|t|thousand|million|billion|trillion))?', q)
    f["tx_dollar_count"] = len(dollar_matches)
    if dollar_matches:
        amounts = []
        for m in dollar_matches:
            num_str = re.sub(r'[,$]', '', m)
            try:
                val = float(re.search(r'[\d.]+', num_str).group())
                if 'k' in m or 'thousand' in m: val *= 1e3
                elif 'm' in m or 'million' in m: val *= 1e6
                elif 'b' in m or 'billion' in m: val *= 1e9
                elif 't' in m or 'trillion' in m: val *= 1e12
                amounts.append(val)
            except Exception:
                pass
        if amounts:
            f["tx_dollar_max"] = max(amounts)
            f["tx_dollar_min"] = min(amounts)
            f["tx_dollar_log_max"] = math.log10(max(amounts) + 1)
        else:
            f["tx_dollar_max"] = 0
            f["tx_dollar_min"] = 0
            f["tx_dollar_log_max"] = 0
    else:
        f["tx_dollar_max"] = 0
        f["tx_dollar_min"] = 0
        f["tx_dollar_log_max"] = 0

    # Extract plain numbers (non-dollar)
    numbers = re.findall(r'(?<!\$)\b\d[\d,]*(?:\.\d+)?\b', q)
    f["tx_number_count"] = len(numbers)
    if numbers:
        nums = []
        for n in numbers:
            try:
                nums.append(float(n.replace(',', '')))
            except Exception:
                pass
        if nums:
            f["tx_number_max"] = max(nums)
            f["tx_number_log_max"] = math.log10(max(nums) + 1)
        else:
            f["tx_number_max"] = 0
            f["tx_number_log_max"] = 0
    else:
        f["tx_number_max"] = 0
        f["tx_number_log_max"] = 0

    # Extract percentage values
    pct_matches = re.findall(r'(\d+(?:\.\d+)?)\s*%', q)
    f["tx_pct_count"] = len(pct_matches)
    if pct_matches:
        pcts = [float(p) for p in pct_matches]
        f["tx_pct_max"] = max(pcts)
        f["tx_pct_min"] = min(pcts)
    else:
        f["tx_pct_max"] = 0
        f["tx_pct_min"] = 0

    # Question type classification
    f["tx_type_will"] = 1 if q.startswith("will ") else 0
    f["tx_type_how"] = 1 if q.startswith("how ") else 0
    f["tx_type_what"] = 1 if q.startswith("what ") else 0
    f["tx_type_who"] = 1 if q.startswith("who ") else 0
    f["tx_type_when"] = 1 if q.startswith("when ") else 0
    f["tx_type_which"] = 1 if q.startswith("which ") else 0

    # Comparison / threshold patterns
    f["tx_above"] = 1 if any(w in q for w in ['above', 'over', 'exceed', 'surpass', 'more than', 'greater than', 'higher than', 'at least']) else 0
    f["tx_below"] = 1 if any(w in q for w in ['below', 'under', 'less than', 'lower than', 'at most', 'fewer than']) else 0
    f["tx_between"] = 1 if 'between' in q else 0
    f["tx_close_at"] = 1 if any(w in q for w in ['close at', 'close above', 'close below', 'closing price', 'close on']) else 0
    f["tx_reach"] = 1 if any(w in q for w in ['reach', 'hit', 'touch', 'break through', 'break above']) else 0

    # Crypto-specific
    f["tx_btc"] = 1 if any(w in q for w in ['bitcoin', 'btc']) else 0
    f["tx_eth"] = 1 if any(w in q for w in ['ethereum', 'eth']) else 0
    f["tx_sol"] = 1 if any(w in q for w in ['solana', 'sol']) else 0
    f["tx_altcoin"] = 1 if any(w in q for w in ['doge', 'xrp', 'ada', 'bnb', 'matic', 'avax', 'dot', 'link', 'shib', 'pepe', 'meme coin']) else 0

    # People
    f["tx_trump"] = 1 if 'trump' in q else 0
    f["tx_biden"] = 1 if 'biden' in q else 0
    f["tx_elon"] = 1 if any(w in q for w in ['elon', 'musk']) else 0

    # Specificity of the question
    f["tx_yes_no_binary"] = 1 if any(w in q for w in ['yes or no', 'yes/no', 'will it', 'will they', 'will he', 'will she']) else 0
    f["tx_range_question"] = 1 if f["tx_between"] or (f["tx_above"] and f["tx_below"]) else 0
    f["tx_exact_match"] = 1 if any(w in q for w in ['exactly', 'precise', 'equal to']) else 0

    # Day-of-week mentions
    f["tx_mentions_monday"] = 1 if 'monday' in q else 0
    f["tx_mentions_friday"] = 1 if 'friday' in q else 0
    f["tx_mentions_weekend"] = 1 if any(w in q for w in ['weekend', 'saturday', 'sunday']) else 0

    # Month mentions (for seasonal effects)
    months = ['january', 'february', 'march', 'april', 'may', 'june',
              'july', 'august', 'september', 'october', 'november', 'december']
    f["tx_month_mentioned"] = 0
    for i, m in enumerate(months, 1):
        if m in q:
            f["tx_month_mentioned"] = i
            break

    # Year mentioned
    year_match = re.search(r'\b(202[0-9])\b', q)
    f["tx_year_mentioned"] = int(year_match.group(1)) if year_match else 0

    return f


# ──────────────────────────────────────────────────────────────
# 2. TIME-TO-RESOLUTION FEATURES
# ──────────────────────────────────────────────────────────────

def time_features(row) -> dict:
    """Features based on market timing."""
    f = {}

    end_date = row.get("end_date")
    first_seen = row.get("first_seen")

    if pd.notna(end_date) and pd.notna(first_seen):
        try:
            end_dt = pd.to_datetime(end_date)
            first_dt = pd.to_datetime(first_seen)
            duration = (end_dt - first_dt).total_seconds() / 86400  # days
            f["tm_duration_days"] = max(0, duration)
            f["tm_duration_log"] = math.log10(max(1, duration))
            f["tm_is_short"] = 1 if duration < 1 else 0  # <1 day
            f["tm_is_medium"] = 1 if 1 <= duration <= 7 else 0  # 1-7 days
            f["tm_is_long"] = 1 if 7 < duration <= 30 else 0  # 1-4 weeks
            f["tm_is_very_long"] = 1 if duration > 30 else 0  # >1 month
        except Exception:
            f["tm_duration_days"] = 0
            f["tm_duration_log"] = 0
            f["tm_is_short"] = 0
            f["tm_is_medium"] = 0
            f["tm_is_long"] = 0
            f["tm_is_very_long"] = 0
    else:
        f["tm_duration_days"] = 0
        f["tm_duration_log"] = 0
        f["tm_is_short"] = 0
        f["tm_is_medium"] = 0
        f["tm_is_long"] = 0
        f["tm_is_very_long"] = 0

    # End date features
    if pd.notna(end_date):
        try:
            end_dt = pd.to_datetime(end_date)
            f["tm_end_hour"] = end_dt.hour
            f["tm_end_day_of_week"] = end_dt.weekday()
            f["tm_end_is_weekend"] = 1 if end_dt.weekday() >= 5 else 0
            f["tm_end_is_month_end"] = 1 if end_dt.day >= 28 else 0
            f["tm_end_quarter"] = (end_dt.month - 1) // 3 + 1
        except Exception:
            f["tm_end_hour"] = 0
            f["tm_end_day_of_week"] = 0
            f["tm_end_is_weekend"] = 0
            f["tm_end_is_month_end"] = 0
            f["tm_end_quarter"] = 0
    else:
        f["tm_end_hour"] = 0
        f["tm_end_day_of_week"] = 0
        f["tm_end_is_weekend"] = 0
        f["tm_end_is_month_end"] = 0
        f["tm_end_quarter"] = 0

    return f


# ──────────────────────────────────────────────────────────────
# 3. MARKET STRUCTURAL FEATURES
# ──────────────────────────────────────────────────────────────

def market_features(row) -> dict:
    """Features from market metadata."""
    f = {}

    f["mk_volume"] = float(row.get("volume", 0) or 0)
    f["mk_volume_log"] = math.log10(max(1, f["mk_volume"]))

    yes_price = float(row.get("yes_price", 0.5) or 0.5)
    f["mk_yes_price"] = yes_price
    f["mk_price_extreme"] = 1 if yes_price > 0.9 or yes_price < 0.1 else 0
    f["mk_price_balanced"] = 1 if 0.4 <= yes_price <= 0.6 else 0

    # Category encoding
    cat = str(row.get("category", "")).strip().lower()
    f["mk_has_category"] = 0 if cat in ("", "nan", "none") else 1

    # Slug length (proxy for market complexity)
    slug = str(row.get("slug", ""))
    f["mk_slug_length"] = len(slug)

    return f


# ──────────────────────────────────────────────────────────────
# 4. FRED ECONOMIC DATA
# ──────────────────────────────────────────────────────────────

FRED_SERIES = {
    # Daily
    "DFF": "fed_funds_rate",
    "T10Y2Y": "yield_curve_10y2y",
    "T10Y3M": "yield_curve_10y3m",
    "T10YIE": "breakeven_inflation_10y",
    "T5YIE": "breakeven_inflation_5y",
    "DGS10": "treasury_yield_10y",
    "DGS2": "treasury_yield_2y",
    "DGS30": "treasury_yield_30y",
    "DFII10": "real_rate_10y",
    "DCOILWTICO": "oil_wti",
    "DCOILBRENTEU": "oil_brent",
    "DTWEXBGS": "trade_weighted_usd",
    "DEXUSEU": "usd_eur",
    "DEXJPUS": "usd_jpy",
    "DEXUSUK": "usd_gbp",
    "BAMLH0A0HYM2": "high_yield_spread",
    "BAMLC0A0CM": "ig_spread",
    "TEDRATE": "ted_spread",
    "VIXCLS": "vix_fred",
    # Weekly
    "ICSA": "initial_claims",
    "CCSA": "continued_claims",
    "WM2NS": "m2_money_supply",
    "WALCL": "fed_balance_sheet",
    # Monthly
    "UNRATE": "unemployment_rate",
    "CPIAUCSL": "cpi",
    "CPILFESL": "core_cpi",
    "PPIACO": "ppi",
    "UMCSENT": "consumer_sentiment",
    "HOUST": "housing_starts",
    "PERMIT": "building_permits",
    "RSAFS": "retail_sales",
    "INDPRO": "industrial_production",
    "PCE": "personal_consumption",
    "PSAVERT": "personal_savings_rate",
    "DGORDER": "durable_goods_orders",
    "JTSJOL": "job_openings",
    "PAYEMS": "nonfarm_payrolls",
    "CSUSHPINSA": "case_shiller_home_price",
    "TOTALSA": "auto_sales",
    "BOPGSTB": "trade_balance",
    "BOGZ1FL073164003Q": "household_net_worth",
    "GDP": "gdp",
    "A191RL1Q225SBEA": "gdp_growth_rate",
}

def fetch_fred_series(series_id: str, start: str, end: str) -> pd.Series:
    """Fetch a single FRED series."""
    url = f"https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "observation_start": start,
        "observation_end": end,
    }

    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.warning("FRED %s failed: %s", series_id, e)
        return pd.Series(dtype=float)

    observations = data.get("observations", [])
    if not observations:
        return pd.Series(dtype=float)

    dates = []
    values = []
    for obs in observations:
        try:
            dt = datetime.strptime(obs["date"], "%Y-%m-%d").date()
            val = float(obs["value"])
            dates.append(dt)
            values.append(val)
        except (ValueError, KeyError):
            continue

    return pd.Series(values, index=dates)


def fetch_all_fred(start: str, end: str) -> pd.DataFrame:
    """Fetch all FRED series and combine."""
    result = pd.DataFrame()

    for series_id, name in FRED_SERIES.items():
        log.info("  FRED: %s (%s)", series_id, name)
        series = fetch_fred_series(series_id, start, end)
        if not series.empty:
            result[f"fr_{name}"] = series
            log.info("    Got %d observations", len(series))
        time.sleep(0.5)  # Be nice to FRED API

    # Forward-fill for daily alignment
    if not result.empty:
        # Create daily index
        date_range = pd.date_range(start=start, end=end, freq="D")
        result = result.reindex([d.date() for d in date_range])
        result = result.ffill()

        # Add derived
        if "fr_yield_curve_10y2y" in result.columns:
            result["fr_yield_curve_inverted"] = (result["fr_yield_curve_10y2y"] < 0).astype(int)
        if "fr_fed_funds_rate" in result.columns:
            result["fr_fed_rate_change_30d"] = result["fr_fed_funds_rate"].diff(30)
        if "fr_cpi" in result.columns:
            result["fr_cpi_yoy_change"] = result["fr_cpi"].pct_change(12) * 100  # approximate YoY

    return result


# ──────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────

def main():
    markets_csv = DATA_DIR / "resolved_markets.csv"
    log.info("Loading markets...")
    markets = pd.read_csv(markets_csv, low_memory=False)
    log.info("Loaded %d markets", len(markets))

    # 1. Advanced text features
    log.info("\n=== ADVANCED TEXT FEATURES ===")
    text_rows = []
    for i, row in markets.iterrows():
        feats = extract_advanced_text(row.get("question", ""))
        feats["id"] = row["id"]
        text_rows.append(feats)
        if (i + 1) % 100000 == 0:
            log.info("  Text: %d/%d", i + 1, len(markets))

    text_df = pd.DataFrame(text_rows)
    text_path = FEATURES_DIR / "advanced_text_features.csv"
    text_df.to_csv(text_path, index=False)
    log.info("Advanced text features: %d markets x %d cols -> %s",
             len(text_df), len(text_df.columns) - 1, text_path)

    # 2. Time + market structure features
    log.info("\n=== TIME & MARKET FEATURES ===")
    struct_rows = []
    for i, row in markets.iterrows():
        feats = time_features(row)
        feats.update(market_features(row))
        feats["id"] = row["id"]
        struct_rows.append(feats)
        if (i + 1) % 100000 == 0:
            log.info("  Struct: %d/%d", i + 1, len(markets))

    struct_df = pd.DataFrame(struct_rows)
    struct_path = FEATURES_DIR / "structural_features.csv"
    struct_df.to_csv(struct_path, index=False)
    log.info("Structural features: %d markets x %d cols -> %s",
             len(struct_df), len(struct_df.columns) - 1, struct_path)

    # 3. FRED data
    log.info("\n=== FRED ECONOMIC DATA ===")
    markets["end_date"] = pd.to_datetime(markets["end_date"], errors="coerce")
    valid = markets.dropna(subset=["end_date"])
    valid = valid[(valid["end_date"] >= "2020-01-01") & (valid["end_date"] <= "2026-12-31")]
    dates = sorted(valid["end_date"].dt.date.unique())

    start_str = str(dates[0])
    end_str = str(dates[-1])
    log.info("Fetching FRED data from %s to %s", start_str, end_str)

    fred_df = fetch_all_fred(start_str, end_str)
    if not fred_df.empty:
        fred_path = FEATURES_DIR / "fred_features.csv"
        fred_df.to_csv(fred_path)
        log.info("FRED features: %d dates x %d cols -> %s",
                 len(fred_df), len(fred_df.columns), fred_path)
    else:
        log.warning("No FRED data retrieved")

    # Print stats
    print(f"\nAdvanced text features: {len(text_df.columns) - 1} columns")
    print(f"Structural features: {len(struct_df.columns) - 1} columns")
    print(f"FRED features: {len(fred_df.columns) if not fred_df.empty else 0} columns")
    print(f"Total new features: {len(text_df.columns) - 1 + len(struct_df.columns) - 1 + (len(fred_df.columns) if not fred_df.empty else 0)}")

    # Topic distribution from advanced text
    topic_cols = [c for c in text_df.columns if c.startswith("tx_")]
    bool_cols = [c for c in topic_cols if text_df[c].dtype in ('int64', 'float64') and text_df[c].max() <= 1]
    print("\nAdvanced text boolean distributions:")
    for col in sorted(bool_cols, key=lambda c: -text_df[c].sum()):
        count = int(text_df[col].sum())
        if count > 0:
            pct = count / len(text_df) * 100
            print(f"  {col:30s} {count:>8,} ({pct:.1f}%)")


if __name__ == "__main__":
    main()
