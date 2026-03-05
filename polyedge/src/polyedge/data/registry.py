"""Connector registry -- discovers and manages all available connectors."""
import importlib
import logging
import pkgutil
from datetime import date

from polyedge.data.base_connector import BaseConnector

log = logging.getLogger(__name__)

# Global registry
_CONNECTORS: list[BaseConnector] = []


def register(connector_class):
    """Decorator to register a connector class."""
    instance = connector_class()
    _CONNECTORS.append(instance)
    return connector_class


def get_all_connectors() -> list[BaseConnector]:
    """Return all registered connectors."""
    return list(_CONNECTORS)


def get_available_connectors() -> list[BaseConnector]:
    """Return connectors that have their API keys (or don't need them)."""
    return [c for c in _CONNECTORS if c.is_available()]


def get_connectors_by_category(category: str) -> list[BaseConnector]:
    """Return connectors for a specific category."""
    return [c for c in _CONNECTORS if c.category == category]


def fetch_all_for_date(dt: date) -> list[tuple[str, str, str, float]]:
    """Run all available connectors for one date.

    Returns list of (source, category, feature_name, value) tuples.
    Skips connectors that fail or are unavailable.
    """
    results = []
    for connector in get_available_connectors():
        try:
            features = connector.fetch_date(dt)
            for name, value in features:
                results.append((connector.source, connector.category, name, value))
        except Exception as e:
            log.warning("Connector %s failed for %s: %s", connector.source, dt, e)
    return results


def discover_connectors():
    """Auto-discover connector modules in the connectors package.

    Imports all modules in polyedge.data.connectors, which triggers
    their @register decorators.
    """
    try:
        import polyedge.data.connectors as pkg
        for importer, modname, ispkg in pkgutil.iter_modules(pkg.__path__):
            try:
                importlib.import_module(f"polyedge.data.connectors.{modname}")
            except Exception as e:
                log.warning("Failed to load connector %s: %s", modname, e)
    except Exception as e:
        log.warning("Failed to discover connectors: %s", e)
