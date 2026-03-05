"""Hacker News connector -- top story scores and comment counts."""
import logging
from datetime import date

import requests

from polyedge.data.base_connector import BaseConnector
from polyedge.data.registry import register

log = logging.getLogger(__name__)

_BASE_URL = "https://hacker-news.firebaseio.com/v0"


@register
class HackerNewsConnector(BaseConnector):
    source = "hackernews"
    category = "attention"

    def fetch_date(self, dt: date) -> list[tuple[str, float]]:
        # HN API only returns current state, not historical
        if dt != date.today():
            return []

        try:
            resp = requests.get(f"{_BASE_URL}/topstories.json", timeout=15)
            resp.raise_for_status()
            story_ids = resp.json()[:30]
        except Exception as e:
            log.warning("HN topstories failed: %s", e)
            return []

        scores = []
        total_comments = 0
        for sid in story_ids:
            try:
                resp = requests.get(f"{_BASE_URL}/item/{sid}.json", timeout=10)
                resp.raise_for_status()
                item = resp.json()
                if item is None:
                    continue
                score = item.get("score", 0)
                descendants = item.get("descendants", 0)
                scores.append(score)
                total_comments += descendants
            except Exception:
                continue

        if not scores:
            return []

        return [
            ("hn_top_score", float(max(scores))),
            ("hn_avg_score", round(sum(scores) / len(scores), 2)),
            ("hn_total_comments", float(total_comments)),
            ("hn_stories_over_100", float(sum(1 for s in scores if s > 100))),
            ("hn_stories_over_500", float(sum(1 for s in scores if s > 500))),
        ]
