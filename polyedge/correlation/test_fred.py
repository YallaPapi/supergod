"""Quick FRED API test."""
import requests
import time

import os
key = os.environ.get("FRED_API_KEY", "")
series_ids = ["DFF", "T10Y2Y", "UNRATE", "CPIAUCSL", "VIXCLS"]

for sid in series_ids:
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": sid,
        "api_key": key,
        "file_type": "json",
        "observation_start": "2024-01-01",
        "observation_end": "2024-01-10",
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        data = r.json()
        obs = data.get("observations", [])
        print(f"{sid}: status={r.status_code} obs={len(obs)}")
        if obs:
            print(f"  sample: {obs[0]}")
        if "error_message" in data:
            print(f"  ERROR: {data['error_message']}")
    except Exception as e:
        print(f"{sid}: FAILED - {e}")
    time.sleep(0.3)
