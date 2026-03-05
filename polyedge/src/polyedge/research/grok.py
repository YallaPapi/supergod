import httpx
from polyedge.db import settings

GROK_URL = "https://api.x.ai/v1/chat/completions"


async def query_grok(prompt: str, model: str = "grok-3-mini") -> str:
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            GROK_URL,
            headers={"Authorization": f"Bearer {settings.grok_api_key}"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}]},
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
