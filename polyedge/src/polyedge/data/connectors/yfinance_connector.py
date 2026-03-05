"""Yahoo Finance connector -- stock/index/commodity prices via yfinance."""
import logging
from datetime import date, timedelta

from polyedge.data.base_connector import BaseConnector
from polyedge.data.registry import register

log = logging.getLogger(__name__)

# Ticker -> feature prefix mapping
_INDICES = {
    "^GSPC": "sp500",
    "^IXIC": "nasdaq",
    "^DJI": "dow",
    "^VIX": "vix",
    "^TNX": "10yr_yield",
}

_COMMODITIES = {
    "GC=F": "gold",
    "CL=F": "oil",
    "DX-Y.NYB": "dxy",
}

_STOCKS = {
    "AAPL": "aapl",
    "MSFT": "msft",
    "NVDA": "nvda",
    "TSLA": "tsla",
    "META": "meta",
    "GOOG": "goog",
    "AMZN": "amzn",
    "JPM": "jpm",
    "V": "visa",
    "MA": "mastercard",
}

_ALL_TICKERS = {**_INDICES, **_COMMODITIES, **_STOCKS}


@register
class YFinanceConnector(BaseConnector):
    source = "yfinance"
    category = "financial"

    def fetch_date(self, dt: date) -> list[tuple[str, float]]:
        import yfinance as yf

        tickers = list(_ALL_TICKERS.keys())
        start = dt
        # Need previous day for pct change calculation
        prev_start = dt - timedelta(days=5)  # go back 5 days to cover weekends
        end = dt + timedelta(days=1)

        try:
            data = yf.download(
                tickers, start=str(prev_start), end=str(end), progress=False
            )
        except Exception as e:
            log.warning("yfinance download failed: %s", e)
            return []

        if data.empty:
            return []

        # Check if dt is a trading day
        close = data["Close"] if "Close" in data.columns.get_level_values(0) else data.get("Close")
        if close is None or close.empty:
            return []

        # Filter to our target date
        target_rows = close.loc[close.index.date == dt] if hasattr(close.index, 'date') else None
        if target_rows is None or target_rows.empty:
            return []  # Not a trading day (weekend/holiday)

        features = []
        for ticker, prefix in _ALL_TICKERS.items():
            col = ticker if ticker in close.columns else None
            if col is None:
                # Single ticker case -- close might be a Series
                if len(_ALL_TICKERS) == 1:
                    col = close.columns[0] if hasattr(close, 'columns') else None
                if col is None:
                    continue

            try:
                price = float(close.loc[close.index.date == dt, col].iloc[0])
            except (IndexError, KeyError, TypeError):
                continue

            if not _is_valid(price):
                continue

            features.append((f"{prefix}_close", price))

            # Percent change from previous day
            try:
                prev_prices = close.loc[close.index.date < dt, col]
                if not prev_prices.empty:
                    prev_close = float(prev_prices.iloc[-1])
                    if prev_close != 0 and _is_valid(prev_close):
                        pct = ((price - prev_close) / prev_close) * 100.0
                        features.append((f"{prefix}_pct_change", round(pct, 4)))
            except (IndexError, KeyError, TypeError):
                pass

        return features

    def fetch_range(self, start: date, end: date) -> list[tuple[date, str, float]]:
        """Override for efficiency -- yfinance handles date ranges natively."""
        import yfinance as yf

        tickers = list(_ALL_TICKERS.keys())
        # Go back a few extra days for pct change on the first day
        dl_start = start - timedelta(days=5)
        dl_end = end + timedelta(days=1)

        try:
            data = yf.download(
                tickers, start=str(dl_start), end=str(dl_end), progress=False
            )
        except Exception as e:
            log.warning("yfinance download failed: %s", e)
            return []

        if data.empty:
            return []

        close = data["Close"] if "Close" in data.columns.get_level_values(0) else data.get("Close")
        if close is None or close.empty:
            return []

        results = []
        dt = start
        while dt <= end:
            target_rows = close.loc[close.index.date == dt]
            if target_rows.empty:
                dt += timedelta(days=1)
                continue

            for ticker, prefix in _ALL_TICKERS.items():
                if ticker not in close.columns:
                    continue
                try:
                    price = float(close.loc[close.index.date == dt, ticker].iloc[0])
                except (IndexError, KeyError, TypeError):
                    continue

                if not _is_valid(price):
                    continue

                results.append((dt, f"{prefix}_close", price))

                try:
                    prev_prices = close.loc[close.index.date < dt, ticker]
                    if not prev_prices.empty:
                        prev_close = float(prev_prices.iloc[-1])
                        if prev_close != 0 and _is_valid(prev_close):
                            pct = ((price - prev_close) / prev_close) * 100.0
                            results.append((dt, f"{prefix}_pct_change", round(pct, 4)))
                except (IndexError, KeyError, TypeError):
                    pass

            dt += timedelta(days=1)

        return results


def _is_valid(v) -> bool:
    """Check that value is a finite number."""
    import math
    try:
        return math.isfinite(float(v))
    except (ValueError, TypeError):
        return False
