"""Open-Meteo connector -- historical weather data for major cities."""
import logging
from datetime import date

import requests

from polyedge.data.base_connector import BaseConnector
from polyedge.data.registry import register

log = logging.getLogger(__name__)

_API_URL = "https://archive-api.open-meteo.com/v1/archive"

_CITIES = {
    "nyc": (40.71, -74.01),
    "la": (34.05, -118.24),
    "chicago": (41.88, -87.63),
    "london": (51.51, -0.13),
    "tokyo": (35.68, 139.69),
}

_DAILY_VARS = "temperature_2m_max,temperature_2m_min,precipitation_sum,windspeed_10m_max"

_VAR_MAP = {
    "temperature_2m_max": "temp_max",
    "temperature_2m_min": "temp_min",
    "precipitation_sum": "precipitation",
    "windspeed_10m_max": "windspeed",
}


@register
class OpenMeteoConnector(BaseConnector):
    source = "open_meteo"
    category = "weather"

    def fetch_date(self, dt: date) -> list[tuple[str, float]]:
        features = []
        date_str = dt.isoformat()
        all_temps = []
        all_precip = []

        for city, (lat, lon) in _CITIES.items():
            params = {
                "latitude": lat,
                "longitude": lon,
                "start_date": date_str,
                "end_date": date_str,
                "daily": _DAILY_VARS,
                "timezone": "auto",
            }
            try:
                resp = requests.get(_API_URL, params=params, timeout=15)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                log.warning("Open-Meteo %s failed: %s", city, e)
                continue

            daily = data.get("daily", {})
            for api_var, feat_suffix in _VAR_MAP.items():
                values = daily.get(api_var, [])
                if values and values[0] is not None:
                    val = float(values[0])
                    features.append((f"{city}_{feat_suffix}", val))

                    if feat_suffix in ("temp_max", "temp_min"):
                        all_temps.append(val)
                    if feat_suffix == "precipitation":
                        all_precip.append(val)

        # Aggregates
        if all_temps:
            features.append(("avg_temp_all_cities", round(sum(all_temps) / len(all_temps), 2)))
        if all_precip:
            features.append(("max_precip_all_cities", max(all_precip)))

        return features
