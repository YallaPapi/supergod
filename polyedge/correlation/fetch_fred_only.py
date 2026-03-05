"""Fetch all FRED series and save to CSV."""
import requests
import time
import logging
from datetime import datetime
from pathlib import Path
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

import os
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")
DATA_DIR = Path(r"C:\polyedge\data\features")

FRED_SERIES = {
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
    "VIXCLS": "vix_fred",
    "ICSA": "initial_claims",
    "CCSA": "continued_claims",
    "WM2NS": "m2_money_supply",
    "WALCL": "fed_balance_sheet",
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
    "GDP": "gdp",
    "A191RL1Q225SBEA": "gdp_growth_rate",
}

start = "2020-01-01"
end = "2026-03-05"
result = pd.DataFrame()

for series_id, name in FRED_SERIES.items():
    url = "https://api.stlouisfed.org/fred/series/observations"
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
        observations = data.get("observations", [])

        dates, values = [], []
        for obs in observations:
            try:
                val = float(obs["value"])
                dt = datetime.strptime(obs["date"], "%Y-%m-%d").date()
                dates.append(dt)
                values.append(val)
            except (ValueError, KeyError):
                continue

        if dates:
            result[f"fr_{name}"] = pd.Series(values, index=dates)
            log.info("%s (%s): %d obs", series_id, name, len(dates))
        else:
            log.warning("%s (%s): no valid observations", series_id, name)
    except Exception as e:
        log.error("%s (%s): FAILED - %s", series_id, name, e)

    time.sleep(0.3)

log.info("Raw FRED: %d series, %d dates", len(result.columns), len(result))

# Create daily index and forward-fill
date_range = pd.date_range(start=start, end=end, freq="D")
result = result.reindex([d.date() for d in date_range])
result = result.ffill()

# Derived features
if "fr_yield_curve_10y2y" in result.columns:
    result["fr_yield_curve_inverted"] = (result["fr_yield_curve_10y2y"] < 0).astype(float)
if "fr_fed_funds_rate" in result.columns:
    result["fr_fed_rate_change_30d"] = result["fr_fed_funds_rate"].diff(30)
if "fr_cpi" in result.columns:
    result["fr_cpi_mom"] = result["fr_cpi"].pct_change() * 100
if "fr_unemployment_rate" in result.columns:
    result["fr_unemployment_change"] = result["fr_unemployment_rate"].diff()
if "fr_initial_claims" in result.columns:
    result["fr_claims_4wk_avg"] = result["fr_initial_claims"].rolling(28).mean()
if "fr_nonfarm_payrolls" in result.columns:
    result["fr_payrolls_change"] = result["fr_nonfarm_payrolls"].diff()

output_path = DATA_DIR / "fred_features.csv"
result.to_csv(output_path)
log.info("FRED saved: %d dates x %d features -> %s (%.1f MB)",
         len(result), len(result.columns), output_path,
         output_path.stat().st_size / 1024 / 1024)

print(f"\nFRED: {len(result)} dates x {len(result.columns)} features")
print("Columns:")
for c in sorted(result.columns):
    non_null = result[c].notna().sum()
    print(f"  {c:45s} {non_null:>5d} non-null")
