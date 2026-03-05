"""Wikipedia connector -- top page views from Wikimedia API."""
import logging
from datetime import date

import requests

from polyedge.data.base_connector import BaseConnector
from polyedge.data.registry import register

log = logging.getLogger(__name__)

_API_URL = "https://wikimedia.org/api/rest_v1/metrics/pageviews/top/en.wikipedia/all-access"


@register
class WikipediaConnector(BaseConnector):
    source = "wikipedia"
    category = "attention"

    def fetch_date(self, dt: date) -> list[tuple[str, float]]:
        year = dt.strftime("%Y")
        month = dt.strftime("%m")
        day = dt.strftime("%d")
        url = f"{_API_URL}/{year}/{month}/{day}"

        try:
            headers = {"User-Agent": "polyedge/1.0"}
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log.warning("Wikipedia pageviews failed for %s: %s", dt, e)
            return []

        articles = []
        for item in data.get("items", []):
            for article in item.get("articles", []):
                views = article.get("views", 0)
                articles.append(views)

        # Sort descending, take top 10
        articles.sort(reverse=True)
        top10 = articles[:10]

        features = []
        for i, views in enumerate(top10, 1):
            features.append((f"wiki_top{i}_views", float(views)))

        if top10:
            features.append(("wiki_total_top10_views", float(sum(top10))))

        return features
