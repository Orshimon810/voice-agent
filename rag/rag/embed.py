"""OpenAI embeddings client."""

from __future__ import annotations

import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

EMBEDDING_MODEL = "text-embedding-3-small"
DEFAULT_BATCH_SIZE = 100


class MissingApiKeyError(RuntimeError):
    """Raised when OPENAI_API_KEY is not configured."""


def _get_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise MissingApiKeyError(
            "OPENAI_API_KEY is not set. Add it to a .env file in the rag/ "
            "directory (see .env.example), e.g. OPENAI_API_KEY=sk-..."
        )
    return OpenAI(api_key=api_key)


def embed_texts(texts: list[str], batch_size: int = DEFAULT_BATCH_SIZE) -> list[list[float]]:
    """Embed multiple texts, batching requests to the OpenAI API."""
    if not texts:
        return []

    client = _get_client()
    embeddings: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        try:
            response = client.embeddings.create(model=EMBEDDING_MODEL, input=batch)
        except Exception as exc:
            raise RuntimeError(f"OpenAI embeddings request failed: {exc}") from exc
        embeddings.extend(item.embedding for item in response.data)
    return embeddings


def embed_text(text: str) -> list[float]:
    """Embed a single piece of text (e.g. a search query)."""
    return embed_texts([text])[0]
