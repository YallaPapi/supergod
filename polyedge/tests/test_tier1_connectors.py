"""Tests for Tier 1 connectors -- structure and calendar logic (no network)."""
from datetime import date

import pytest

from polyedge.data import registry
from polyedge.data.base_connector import BaseConnector

# Import connector classes directly (triggers @register on first import)
from polyedge.data.connectors.calendar_connector import CalendarConnector
from polyedge.data.connectors.yfinance_connector import YFinanceConnector
from polyedge.data.connectors.coingecko_connector import CoinGeckoConnector
from polyedge.data.connectors.open_meteo_connector import OpenMeteoConnector
from polyedge.data.connectors.usgs_connector import USGSConnector
from polyedge.data.connectors.wikipedia_connector import WikipediaConnector
from polyedge.data.connectors.hackernews_connector import HackerNewsConnector
from polyedge.data.connectors.reddit_connector import RedditConnector

# Save a snapshot of registered connectors after all imports
_REGISTERED = list(registry._CONNECTORS)


# ---- Fixtures ----

@pytest.fixture(autouse=True)
def _restore_registry():
    """Restore registry to the known-good state for each test."""
    registry._CONNECTORS.clear()
    registry._CONNECTORS.extend(_REGISTERED)
    yield
    registry._CONNECTORS.clear()
    registry._CONNECTORS.extend(_REGISTERED)


# ---- 1. All 8 connectors importable and registered ----

def test_all_eight_connectors_registered():
    connectors = registry.get_all_connectors()
    sources = sorted(c.source for c in connectors)
    expected = sorted([
        "calendar", "yfinance", "coingecko", "open_meteo",
        "usgs", "wikipedia", "hackernews", "reddit",
    ])
    assert sources == expected, f"Expected {expected}, got {sources}"


def test_all_connectors_are_base_connector_subclass():
    for c in registry.get_all_connectors():
        assert isinstance(c, BaseConnector), f"{c} is not a BaseConnector"


# ---- 2. Calendar connector with known date: 2025-12-25 ----

def test_calendar_christmas_2025():
    """2025-12-25 is Thursday, Christmas, quarter end month, year-end month."""
    cal = _get_connector("calendar")
    features = dict(cal.fetch_date(date(2025, 12, 25)))

    assert features["day_of_week"] == 3.0  # Thursday
    assert features["day_of_month"] == 25.0
    assert features["month"] == 12.0
    assert features["is_weekend"] == 0.0
    assert features["is_us_holiday"] == 1.0  # Christmas
    assert features["quarter"] == 4.0
    # Not month end (25th != 31st)
    assert features["is_month_end"] == 0.0
    assert features["is_quarter_end"] == 0.0
    assert features["is_year_end"] == 0.0
    assert features["is_leap_year"] == 0.0  # 2025 is not a leap year
    assert features["days_in_month"] == 31.0


def test_calendar_dec31_2025():
    """2025-12-31 is quarter end, year end, month end."""
    cal = _get_connector("calendar")
    features = dict(cal.fetch_date(date(2025, 12, 31)))

    assert features["is_month_end"] == 1.0
    assert features["is_quarter_end"] == 1.0
    assert features["is_year_end"] == 1.0


# ---- 3. Weekend detection ----

def test_calendar_weekend_saturday():
    """2025-12-27 is Saturday."""
    cal = _get_connector("calendar")
    features = dict(cal.fetch_date(date(2025, 12, 27)))
    assert features["is_weekend"] == 1.0
    assert features["day_of_week"] == 5.0  # Saturday


def test_calendar_weekend_sunday():
    """2025-12-28 is Sunday."""
    cal = _get_connector("calendar")
    features = dict(cal.fetch_date(date(2025, 12, 28)))
    assert features["is_weekend"] == 1.0
    assert features["day_of_week"] == 6.0  # Sunday


def test_calendar_weekday():
    """2025-12-29 is Monday."""
    cal = _get_connector("calendar")
    features = dict(cal.fetch_date(date(2025, 12, 29)))
    assert features["is_weekend"] == 0.0
    assert features["day_of_week"] == 0.0  # Monday


# ---- 4. YFinance connector metadata ----

def test_yfinance_metadata():
    yf = _get_connector("yfinance")
    assert yf.source == "yfinance"
    assert yf.category == "financial"
    assert yf.requires_key is False


# ---- 5. Each connector's fetch_date returns list of (str, float) tuples ----

def test_calendar_fetch_date_returns_correct_types():
    cal = _get_connector("calendar")
    results = cal.fetch_date(date(2025, 6, 15))
    assert isinstance(results, list)
    for item in results:
        assert isinstance(item, tuple), f"Expected tuple, got {type(item)}"
        assert len(item) == 2
        name, value = item
        assert isinstance(name, str), f"Name should be str, got {type(name)}"
        assert isinstance(value, float), f"Value should be float, got {type(value)}: {name}={value}"


# ---- 6. Calendar feature count ----

def test_calendar_feature_count():
    cal = _get_connector("calendar")
    results = cal.fetch_date(date(2025, 7, 4))
    # Should have 14 features
    assert len(results) == 14, f"Expected 14 features, got {len(results)}: {[r[0] for r in results]}"


# ---- 7. Calendar: days_until_next_holiday ----

def test_calendar_days_until_holiday_on_holiday():
    cal = _get_connector("calendar")
    features = dict(cal.fetch_date(date(2025, 7, 4)))
    assert features["days_until_next_holiday"] == 0.0  # Today IS the holiday


def test_calendar_days_until_holiday_before():
    cal = _get_connector("calendar")
    features = dict(cal.fetch_date(date(2025, 7, 3)))
    assert features["days_until_next_holiday"] == 1.0  # July 4th is tomorrow


# ---- 8. Connector metadata checks ----

def test_all_connectors_have_source_and_category():
    for c in registry.get_all_connectors():
        assert c.source, f"{c.__class__.__name__} has empty source"
        assert c.category, f"{c.__class__.__name__} has empty category"


def test_all_tier1_connectors_require_no_key():
    for c in registry.get_all_connectors():
        assert c.requires_key is False, f"{c.source} should not require an API key"
        assert c.is_available() is True


# ---- 9. Calendar leap year ----

def test_calendar_leap_year():
    cal = _get_connector("calendar")
    features_2024 = dict(cal.fetch_date(date(2024, 2, 29)))
    assert features_2024["is_leap_year"] == 1.0
    assert features_2024["days_in_month"] == 29.0


def test_calendar_month_start():
    cal = _get_connector("calendar")
    features = dict(cal.fetch_date(date(2025, 3, 1)))
    assert features["is_month_start"] == 1.0


# ---- Helpers ----

def _get_connector(source: str) -> BaseConnector:
    """Get a registered connector by source name."""
    for c in registry.get_all_connectors():
        if c.source == source:
            return c
    raise ValueError(f"Connector '{source}' not found in registry")
