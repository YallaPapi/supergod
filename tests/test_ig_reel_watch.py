from supergod.tools.ig_reel_watch import (
    _extract_adspower_cdp,
    extract_views,
    parse_compact_count,
    _target_reels_url,
)


def test_parse_compact_count():
    assert parse_compact_count("1,234") == 1234
    assert parse_compact_count("2.5k") == 2500
    assert parse_compact_count("3m") == 3_000_000
    assert parse_compact_count("4.1b") == 4_100_000_000
    assert parse_compact_count("x") is None


def test_extract_views():
    views, src = extract_views(
        [
            "random text",
            "12,345 views",
            "something else",
        ]
    )
    assert views == 12345
    assert "views" in src.lower()


def test_target_reels_url():
    assert _target_reels_url("https://www.instagram.com/myprofile") == "https://www.instagram.com/myprofile/reels/"
    assert _target_reels_url("https://www.instagram.com/myprofile/") == "https://www.instagram.com/myprofile/reels/"


def test_extract_adspower_cdp():
    payload = {"ws": {"puppeteer": "ws://127.0.0.1:9222/devtools/browser/abc"}}
    assert _extract_adspower_cdp(payload) == "ws://127.0.0.1:9222/devtools/browser/abc"
    payload2 = {"debug_port": 9223}
    assert _extract_adspower_cdp(payload2) == "http://127.0.0.1:9223"
