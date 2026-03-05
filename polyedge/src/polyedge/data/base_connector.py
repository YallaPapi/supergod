"""Base class for all API data connectors."""
import logging
from datetime import date, timedelta
from typing import Optional

log = logging.getLogger(__name__)


class BaseConnector:
    """Base class for API connectors that fetch daily features.

    Each connector represents one data source (yfinance, FRED, etc.)
    and returns (feature_name, value) pairs for a given date.

    Subclasses MUST set:
        source: str - API source identifier (e.g., "yfinance")
        category: str - Feature category (e.g., "financial")

    Subclasses MUST implement:
        fetch_date(dt) -> list of (name, value) tuples

    Subclasses MAY override:
        fetch_range(start, end) -> list of (date, name, value) tuples
            Default implementation calls fetch_date for each day.
            Override for APIs that support bulk historical queries.
    """
    source: str = ""
    category: str = ""
    requires_key: bool = False
    key_env_var: str = ""

    def fetch_date(self, dt: date) -> list[tuple[str, float]]:
        """Fetch features for a single date.

        Returns list of (feature_name, value) tuples.
        Feature names should be lowercase_with_underscores.
        Values must be numeric (float).
        """
        raise NotImplementedError(f"{self.__class__.__name__} must implement fetch_date()")

    def fetch_range(self, start: date, end: date) -> list[tuple[date, str, float]]:
        """Fetch features for a date range.

        Returns list of (date, feature_name, value) tuples.

        Default: calls fetch_date() per day. Override for bulk APIs.
        """
        results = []
        dt = start
        while dt <= end:
            try:
                for name, value in self.fetch_date(dt):
                    results.append((dt, name, value))
            except Exception as e:
                log.warning("%s failed for %s: %s", self.source, dt, e)
            dt += timedelta(days=1)
        return results

    def is_available(self) -> bool:
        """Check if this connector can run (API key present if required, etc.)."""
        if not self.requires_key:
            return True
        import os
        key = os.environ.get(self.key_env_var, "")
        return bool(key)

    def get_api_key(self) -> str:
        """Get the API key from environment."""
        import os
        return os.environ.get(self.key_env_var, "")

    def __repr__(self):
        return f"<{self.__class__.__name__} source={self.source} category={self.category}>"
