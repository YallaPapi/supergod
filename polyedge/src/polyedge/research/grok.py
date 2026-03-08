import logging

import httpx
from polyedge.db import settings

log = logging.getLogger(__name__)

GROK_URL = "https://api.x.ai/v1/chat/completions"

# Shared client to reuse connections (avoids ConnectError from too many sockets)
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=90,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
    return _client


async def query_grok(prompt: str, model: str = "grok-3-mini") -> str:
    client = _get_client()
    resp = await client.post(
        GROK_URL,
        headers={"Authorization": f"Bearer {settings.grok_api_key}"},
        json={"model": model, "messages": [{"role": "user", "content": prompt}]},
    )
    if resp.status_code != 200:
        body = resp.text[:300]
        raise RuntimeError(f"Grok API HTTP {resp.status_code}: {body}")
    return resp.json()["choices"][0]["message"]["content"]
