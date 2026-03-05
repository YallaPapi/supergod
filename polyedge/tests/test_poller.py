from polyedge.poller import parse_market

SAMPLE_MARKET = {
    "id": "12345",
    "question": "Will Bitcoin hit $100k by July 2026?",
    "slug": "will-bitcoin-hit-100k",
    "category": "Crypto",
    "description": "Market on BTC price target",
    "endDate": "2026-07-01T00:00:00Z",
    "outcomePrices": ["0.65", "0.35"],
    "volume": "500000",
    "liquidity": "50000",
    "active": True,
    "closed": False,
    "clobTokenIds": ["tok_yes", "tok_no"],
    "volume24hr": "12000",
}


def test_parse_market():
    m = parse_market(SAMPLE_MARKET)
    assert m["id"] == "12345"
    assert m["yes_price"] == 0.65
    assert m["no_price"] == 0.35
    assert m["active"] is True
    assert m["resolved"] is False


def test_parse_resolved_market():
    resolved = {**SAMPLE_MARKET, "closed": True, "active": False}
    m = parse_market(resolved)
    assert m["resolved"] is True
    assert m["active"] is False


def test_parse_market_missing_prices():
    raw = {**SAMPLE_MARKET, "outcomePrices": []}
    m = parse_market(raw)
    assert m["yes_price"] == 0.5


def test_parse_market_no_end_date():
    raw = {**SAMPLE_MARKET}
    del raw["endDate"]
    m = parse_market(raw)
    assert m["end_date"] is None
