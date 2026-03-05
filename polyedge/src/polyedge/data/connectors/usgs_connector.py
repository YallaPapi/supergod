"""USGS Earthquake connector -- seismic activity features."""
import logging
from datetime import date, timedelta

import requests

from polyedge.data.base_connector import BaseConnector
from polyedge.data.registry import register

log = logging.getLogger(__name__)

_API_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"


def _query_earthquakes(start: date, end: date, min_mag: float = 2.5) -> list[float]:
    """Query USGS and return list of magnitudes."""
    params = {
        "format": "geojson",
        "starttime": start.isoformat(),
        "endtime": end.isoformat(),
        "minmagnitude": min_mag,
    }
    try:
        resp = requests.get(_API_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.warning("USGS query failed (%s to %s): %s", start, end, e)
        return []

    magnitudes = []
    for feature in data.get("features", []):
        mag = feature.get("properties", {}).get("mag")
        if mag is not None:
            magnitudes.append(float(mag))
    return magnitudes


@register
class USGSConnector(BaseConnector):
    source = "usgs"
    category = "geophysical"

    def fetch_date(self, dt: date) -> list[tuple[str, float]]:
        features = []

        # 24-hour window
        mags_24h = _query_earthquakes(dt, dt + timedelta(days=1))
        features.append(("earthquake_count_24h", float(len(mags_24h))))
        if mags_24h:
            features.append(("max_magnitude_24h", max(mags_24h)))
            features.append(("avg_magnitude_24h", round(sum(mags_24h) / len(mags_24h), 2)))
        else:
            features.append(("max_magnitude_24h", 0.0))
            features.append(("avg_magnitude_24h", 0.0))

        # 7-day window
        mags_7d = _query_earthquakes(dt - timedelta(days=6), dt + timedelta(days=1))
        features.append(("earthquake_count_7d", float(len(mags_7d))))
        if mags_7d:
            features.append(("max_magnitude_7d", max(mags_7d)))

        # 30-day window
        mags_30d = _query_earthquakes(dt - timedelta(days=29), dt + timedelta(days=1))
        features.append(("earthquake_count_30d", float(len(mags_30d))))

        return features
