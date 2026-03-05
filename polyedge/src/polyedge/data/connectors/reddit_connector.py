"""Reddit connector -- subreddit activity scores via JSON API."""
import logging
from datetime import date

import requests

from polyedge.data.base_connector import BaseConnector
from polyedge.data.registry import register

log = logging.getLogger(__name__)

_SUBREDDITS = ["politics", "cryptocurrency", "wallstreetbets", "sports", "technology"]
_HEADERS = {"User-Agent": "polyedge/1.0"}


@register
class RedditConnector(BaseConnector):
    source = "reddit"
    category = "attention"

    def fetch_date(self, dt: date) -> list[tuple[str, float]]:
        # Reddit JSON API only returns current state, not historical
        if dt != date.today():
            return []

        features = []
        for sub in _SUBREDDITS:
            url = f"https://www.reddit.com/r/{sub}/hot.json?limit=25"
            try:
                resp = requests.get(url, headers=_HEADERS, timeout=15)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                log.warning("Reddit r/%s failed: %s", sub, e)
                continue

            posts = data.get("data", {}).get("children", [])
            scores = []
            total_comments = 0
            for post in posts:
                pdata = post.get("data", {})
                scores.append(pdata.get("score", 0))
                total_comments += pdata.get("num_comments", 0)

            if scores:
                avg_score = round(sum(scores) / len(scores), 2)
                features.append((f"reddit_{sub}_avg_score", float(avg_score)))
                features.append((f"reddit_{sub}_total_comments", float(total_comments)))

        return features
