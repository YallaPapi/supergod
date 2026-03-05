import httpx
from polyedge.db import settings

PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"


async def query_perplexity(prompt: str, model: str = "sonar") -> str:
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            PERPLEXITY_URL,
            headers={"Authorization": f"Bearer {settings.perplexity_api_key}"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}]},
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
