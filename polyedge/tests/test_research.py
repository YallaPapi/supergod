import json
from polyedge.research.ingest import parse_factors_json


def test_parse_factors_json_valid():
    raw = json.dumps({"factors": [
        {"category": "weather", "name": "NYC temp", "value": "92F", "description": "Heat wave"},
        {"category": "financial", "name": "S&P 500", "value": "+1.2%", "description": "Rally"},
    ]})
    factors = parse_factors_json(raw, source="perplexity")
    assert len(factors) == 2
    assert factors[0]["category"] == "weather"
    assert factors[0]["source"] == "perplexity"


def test_parse_factors_json_extracts_from_markdown():
    raw = "Here are the factors:\n```json\n" + json.dumps({"factors": [
        {"category": "sports", "name": "NBA finals", "value": "Lakers won", "description": "Game 7"}
    ]}) + "\n```\nHope that helps!"
    factors = parse_factors_json(raw, source="grok")
    assert len(factors) == 1
    assert factors[0]["source"] == "grok"


def test_parse_factors_json_handles_garbage():
    factors = parse_factors_json("I don't know what you mean", source="grok")
    assert factors == []


def test_parse_factors_with_market_id():
    raw = json.dumps({"factors": [
        {"category": "sentiment", "name": "test", "value": "bullish"}
    ]})
    factors = parse_factors_json(raw, source="perplexity", market_id="mkt123")
    assert factors[0]["market_id"] == "mkt123"


def test_parse_factors_list_format():
    raw = json.dumps([
        {"category": "weather", "name": "rain", "value": "heavy"}
    ])
    factors = parse_factors_json(raw, source="codex")
    assert len(factors) == 1
