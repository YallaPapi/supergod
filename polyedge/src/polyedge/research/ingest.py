import json
import re
import logging

log = logging.getLogger(__name__)


def parse_factors_json(text: str, source: str, market_id: str | None = None) -> list[dict]:
    text = text.strip()
    for attempt_text in [text, _extract_fenced(text)]:
        if not attempt_text:
            continue
        try:
            data = json.loads(attempt_text)
            if isinstance(data, dict) and "factors" in data:
                return _normalize(data["factors"], source, market_id)
            if isinstance(data, list):
                return _normalize(data, source, market_id)
        except json.JSONDecodeError:
            continue
    match = re.search(r'\{[^{}]*"factors"\s*:\s*\[.*?\]\s*\}', text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group())
            return _normalize(data.get("factors", []), source, market_id)
        except json.JSONDecodeError:
            pass
    log.warning("Could not parse factors from %s response (%d chars)", source, len(text))
    return []


def _extract_fenced(text: str) -> str | None:
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    return match.group(1).strip() if match else None


def _normalize(items: list, source: str, market_id: str | None) -> list[dict]:
    factors = []
    for item in items:
        if not isinstance(item, dict):
            continue
        factors.append({
            "market_id": market_id,
            "category": item.get("category", "unknown"),
            "subcategory": item.get("subcategory", ""),
            "name": item.get("name", "unnamed"),
            "value": str(item.get("value", "")),
            "source": source,
            "confidence": float(item.get("confidence", 0.5)),
        })
    return factors
