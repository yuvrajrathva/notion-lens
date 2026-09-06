import asyncio
import re

import httpx

from config import settings

# Nemotron-super is a reasoning-capable model that may emit its chain-of-thought
# inline as <think>...</think> depending on prompting; strip it defensively so a
# leaked reasoning trace never reaches the user-facing answer.
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

# Observed live: NVIDIA's hosted "super" tier model intermittently returns 503
# "Service temporarily overloaded" even for trivial prompts, recovering within a
# couple seconds — worth a couple of quick retries before surfacing an error.
_RETRYABLE_STATUS_CODES = {502, 503, 504}
_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = 1.5


class GenerationError(Exception):
    pass


async def generate_answer(
    client: httpx.AsyncClient, messages: list[dict], temperature: float = 0.2, max_tokens: int = 1024
) -> str:
    """Calls the configured NVIDIA chat completions model and returns the
    assistant's answer text, with any leaked reasoning trace stripped."""
    headers = {
        "Authorization": f"Bearer {settings.GENERATION_MODEL_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": settings.GENERATION_MODEL_NAME,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }

    resp = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = await client.post(settings.GENERATION_API_URL, json=body, headers=headers, timeout=60)
        except httpx.HTTPError as exc:
            raise GenerationError(f"Network error calling generation API: {exc}") from exc

        if resp.status_code == 200:
            break
        if resp.status_code in _RETRYABLE_STATUS_CODES and attempt < _MAX_ATTEMPTS:
            await asyncio.sleep(_RETRY_BACKOFF_SECONDS * attempt)
            continue
        raise GenerationError(f"Generation API returned {resp.status_code}: {resp.text}")

    data = resp.json()
    choices = data.get("choices", [])
    if not choices:
        raise GenerationError("Generation API returned no choices")

    content = choices[0].get("message", {}).get("content", "")
    if not content:
        raise GenerationError("Generation API returned an empty message")

    return _THINK_BLOCK_RE.sub("", content).strip()
