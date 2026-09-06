import os
from pathlib import Path

from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=ENV_PATH)


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            f"Set it in {ENV_PATH}."
        )
    return value


class Settings:
    NOTION_CLIENT_ID = _require("NOTION_CLIENT_ID")
    NOTION_CLIENT_SECRET = _require("NOTION_CLIENT_SECRET")
    NOTION_REDIRECT_URI = os.environ.get(
        "NOTION_REDIRECT_URI", "http://localhost:8000/auth/notion/callback"
    )
    SESSION_SECRET = _require("SESSION_SECRET")

    DATABASE_URL = _require("DATABASE_URL")

    EMBEDDER_MODEL_API_KEY = _require("EMBEDDER_MODEL_API_KEY")
    EMBEDDER_MODEL_NAME = "nvidia/nemotron-3-embed-1b"
    EMBEDDER_API_URL = "https://integrate.api.nvidia.com/v1/embeddings"
    EMBEDDING_DIM = 2048

    GENERATION_MODEL_API_KEY = _require("GENERATION_MODEL_API_KEY")
    # nvidia/llama-3.3-nemotron-super-49b-v1.5 reached end-of-life on NVIDIA's API
    # (2026-08-26) and returns HTTP 410; nemotron-3-super-120b-a12b is its direct
    # successor in the current model catalog (same "super" reasoning tier).
    GENERATION_MODEL_NAME = "nvidia/nemotron-3-super-120b-a12b"
    GENERATION_API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
    TOP_K_CHUNKS = 5

    AUTO_SYNC_INTERVAL_HOURS = 24
    AUTO_SYNC_ERROR_COOLDOWN_HOURS = 1
    AUTO_SYNC_SWEEP_INTERVAL_MINUTES = 30


settings = Settings()
