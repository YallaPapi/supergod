"""CoinGecko connector -- crypto prices, market caps, volumes."""
import logging
import time
from datetime import date

import requests

from polyedge.data.base_connector import BaseConnector
from polyedge.data.registry import register

log = logging.getLogger(__name__)

_BASE_URL = "https://api.coingecko.com/api/v3"

_COINS = [
    "bitcoin", "ethereum", "solana", "ripple", "dogecoin",
    "cardano", "polkadot", "chainlink", "avalanche-2", "polygon-pos",
]

# Short prefix for feature names
_COIN_PREFIX = {
    "bitcoin": "btc",
    "ethereum": "eth",
    "solana": "sol",
    "ripple": "xrp",
    "dogecoin": "doge",
    "cardano": "ada",
    "polkadot": "dot",
    "chainlink": "link",
    "avalanche-2": "avax",
    "polygon-pos": "matic",
}


@register
class CoinGeckoConnector(BaseConnector):
    source = "coingecko"
    category = "crypto"

    def fetch_date(self, dt: date) -> list[tuple[str, float]]:
        features = []
        date_str = dt.strftime("%d-%m-%Y")

        for coin_id in _COINS:
            prefix = _COIN_PREFIX[coin_id]
            url = f"{_BASE_URL}/coins/{coin_id}/history?date={date_str}"
            try:
                resp = requests.get(url, timeout=15)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                log.warning("CoinGecko %s failed: %s", coin_id, e)
                time.sleep(1)
                continue

            md = data.get("market_data", {})
            if not md:
                time.sleep(1)
                continue

            price = _safe_get(md, "current_price", "usd")
            mcap = _safe_get(md, "market_cap", "usd")
            vol = _safe_get(md, "total_volume", "usd")

            if price is not None:
                features.append((f"{prefix}_price_usd", price))
            if mcap is not None:
                features.append((f"{prefix}_market_cap_usd", mcap))
            if vol is not None:
                features.append((f"{prefix}_volume_usd", vol))

            time.sleep(1)  # rate limit

        # Total crypto market cap from /global
        try:
            resp = requests.get(f"{_BASE_URL}/global", timeout=15)
            resp.raise_for_status()
            gdata = resp.json().get("data", {})
            total_mcap = gdata.get("total_market_cap", {}).get("usd")
            if total_mcap is not None:
                features.append(("crypto_total_market_cap_usd", float(total_mcap)))
        except Exception as e:
            log.warning("CoinGecko /global failed: %s", e)

        return features


def _safe_get(md: dict, field: str, currency: str):
    """Safely extract a nested numeric value."""
    try:
        val = md[field][currency]
        return float(val)
    except (KeyError, TypeError, ValueError):
        return None
