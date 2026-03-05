"""Tests for the pluggable API connector framework."""
import os
from datetime import date, timedelta

import pytest

from polyedge.data.base_connector import BaseConnector
from polyedge.data import registry


# --------------- Test connector subclasses ---------------

class FakeGoodConnector(BaseConnector):
    source = "fake_good"
    category = "financial"

    def fetch_date(self, dt: date) -> list[tuple[str, float]]:
        return [("price", 100.0), ("volume", 5000.0)]


class FakeBadConnector(BaseConnector):
    source = "fake_bad"
    category = "sentiment"

    def fetch_date(self, dt: date) -> list[tuple[str, float]]:
        raise RuntimeError("API exploded")


class FakeKeyConnector(BaseConnector):
    source = "fake_key"
    category = "financial"
    requires_key = True
    key_env_var = "FAKE_CONNECTOR_KEY"

    def fetch_date(self, dt: date) -> list[tuple[str, float]]:
        return [("secret_metric", 42.0)]


# --------------- Fixtures ---------------

@pytest.fixture(autouse=True)
def _clear_registry():
    """Clear the global registry before and after each test."""
    registry._CONNECTORS.clear()
    yield
    registry._CONNECTORS.clear()


# --------------- Tests ---------------

def test_base_connector_requires_implementation():
    """Instantiating BaseConnector and calling fetch_date raises NotImplementedError."""
    base = BaseConnector()
    with pytest.raises(NotImplementedError, match="BaseConnector must implement fetch_date"):
        base.fetch_date(date(2026, 1, 1))


def test_register_decorator():
    """A class decorated with @register appears in get_all_connectors()."""
    @registry.register
    class MyConnector(BaseConnector):
        source = "my_src"
        category = "test"

        def fetch_date(self, dt):
            return []

    connectors = registry.get_all_connectors()
    assert len(connectors) == 1
    assert connectors[0].source == "my_src"


def test_fetch_range_default():
    """fetch_range calls fetch_date for each day in the range."""
    connector = FakeGoodConnector()
    start = date(2026, 1, 1)
    end = date(2026, 1, 3)

    results = connector.fetch_range(start, end)

    # 3 days * 2 features each = 6 results
    assert len(results) == 6

    # Verify each day is represented
    dates_in_results = sorted(set(r[0] for r in results))
    assert dates_in_results == [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)]

    # Verify tuple structure (date, name, value)
    for dt, name, value in results:
        assert isinstance(dt, date)
        assert name in ("price", "volume")
        assert isinstance(value, float)


def test_is_available_no_key_needed():
    """Connector with requires_key=False is always available."""
    connector = FakeGoodConnector()
    assert connector.requires_key is False
    assert connector.is_available() is True


def test_is_available_key_missing(monkeypatch):
    """Connector with requires_key=True and no env var is NOT available."""
    monkeypatch.delenv("FAKE_CONNECTOR_KEY", raising=False)
    connector = FakeKeyConnector()
    assert connector.is_available() is False


def test_is_available_key_present(monkeypatch):
    """Connector with requires_key=True and env var set IS available."""
    monkeypatch.setenv("FAKE_CONNECTOR_KEY", "abc123")
    connector = FakeKeyConnector()
    assert connector.is_available() is True


def test_fetch_all_for_date_skips_failures():
    """A connector that raises is skipped; others still return data."""
    registry._CONNECTORS.append(FakeGoodConnector())
    registry._CONNECTORS.append(FakeBadConnector())

    results = registry.fetch_all_for_date(date(2026, 3, 1))

    # Only the good connector's data should be present
    assert len(results) == 2
    sources = [r[0] for r in results]
    assert all(s == "fake_good" for s in sources)

    # Verify tuple structure (source, category, name, value)
    for source, category, name, value in results:
        assert source == "fake_good"
        assert category == "financial"
        assert name in ("price", "volume")


def test_get_connectors_by_category():
    """Filtering by category returns only matching connectors."""
    registry._CONNECTORS.append(FakeGoodConnector())   # financial
    registry._CONNECTORS.append(FakeBadConnector())     # sentiment

    financial = registry.get_connectors_by_category("financial")
    assert len(financial) == 1
    assert financial[0].source == "fake_good"

    sentiment = registry.get_connectors_by_category("sentiment")
    assert len(sentiment) == 1
    assert sentiment[0].source == "fake_bad"

    empty = registry.get_connectors_by_category("nonexistent")
    assert len(empty) == 0
