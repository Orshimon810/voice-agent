"""Cosine-similarity search over stored chunk embeddings."""

from __future__ import annotations

from typing import Any

import numpy as np

from rag.embed import embed_text
from rag.store import Record

DEFAULT_TOP_N = 3


def cosine_similarity(a: list[float], b: list[float]) -> float:
    va = np.asarray(a, dtype=float)
    vb = np.asarray(b, dtype=float)
    denom = np.linalg.norm(va) * np.linalg.norm(vb)
    if denom == 0:
        return 0.0
    return float(np.dot(va, vb) / denom)


def rank_chunks(
    query_vector: list[float],
    records: list[Record],
    top_n: int = DEFAULT_TOP_N,
) -> list[dict[str, Any]]:
    """Rank stored records by cosine similarity to a query vector.

    Pure ranking logic with no network calls, so it can be unit tested
    with fake embedding vectors.
    """
    scored = [
        {**{k: v for k, v in record.items() if k != "embedding"}, "score": cosine_similarity(query_vector, record["embedding"])}
        for record in records
    ]
    scored.sort(key=lambda r: r["score"], reverse=True)
    return scored[:top_n]


def search(query: str, records: list[Record], top_n: int = DEFAULT_TOP_N) -> list[dict[str, Any]]:
    """Embed a natural-language query and return its top-N matching chunks."""
    query_vector = embed_text(query)
    return rank_chunks(query_vector, records, top_n)
