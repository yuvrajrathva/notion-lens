import httpx

from config import settings

# The NIM embeddings endpoint accepts a batch of inputs per request; keep batches
# modest to stay comfortably under its request size / token limits.
BATCH_SIZE = 32


class EmbeddingError(Exception):
    pass


async def embed_texts(
    client: httpx.AsyncClient, texts: list[str], input_type: str = "passage"
) -> list[list[float]]:
    """Embeds a list of texts with nvidia/nemotron-3-embed-1b, batching requests.
    Returns one 2048-dim vector per input text, in the same order."""
    if not texts:
        return []

    headers = {
        "Authorization": f"Bearer {settings.EMBEDDER_MODEL_API_KEY}",
        "Content-Type": "application/json",
    }

    embeddings: list[list[float]] = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i : i + BATCH_SIZE]
        body = {
            "input": batch,
            "model": settings.EMBEDDER_MODEL_NAME,
            "input_type": input_type,
            "encoding_format": "float",
            "truncate": "END",
        }

        try:
            resp = await client.post(settings.EMBEDDER_API_URL, json=body, headers=headers, timeout=60)
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"Network error calling embeddings API: {exc}") from exc

        if resp.status_code != 200:
            raise EmbeddingError(f"Embeddings API returned {resp.status_code}: {resp.text}")

        data = resp.json().get("data", [])
        if len(data) != len(batch):
            raise EmbeddingError(
                f"Embeddings API returned {len(data)} vectors for a batch of {len(batch)} inputs"
            )

        # The API echoes back an `index` per item; sort by it to guarantee order.
        data.sort(key=lambda item: item.get("index", 0))
        for item in data:
            vector = item["embedding"]
            if len(vector) != settings.EMBEDDING_DIM:
                raise EmbeddingError(
                    f"Expected {settings.EMBEDDING_DIM}-dim embedding, got {len(vector)}"
                )
            embeddings.append(vector)

    return embeddings
