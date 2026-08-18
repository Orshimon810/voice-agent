import math

import pytest

import rag.search as search_module
from rag.search import cosine_similarity, rank_chunks, search


def test_cosine_similarity_identical_vectors_is_one() -> None:
    assert cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal_vectors_is_zero() -> None:
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_similarity_opposite_vectors_is_negative_one() -> None:
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_cosine_similarity_zero_vector_returns_zero() -> None:
    assert cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0


def _fake_records() -> list[dict]:
    # 2D "embeddings" placed at known angles so ranking is predictable.
    return [
        {"id": 0, "text": "far off topic", "source": "doc.txt", "embedding": [0.0, 1.0]},
        {"id": 1, "text": "exact match", "source": "doc.txt", "embedding": [1.0, 0.0]},
        {"id": 2, "text": "somewhat related", "source": "doc.txt", "embedding": [0.9, 0.1]},
    ]


def test_rank_chunks_orders_by_similarity_descending() -> None:
    query_vector = [1.0, 0.0]

    results = rank_chunks(query_vector, _fake_records(), top_n=3)

    assert [r["id"] for r in results] == [1, 2, 0]
    assert results[0]["score"] >= results[1]["score"] >= results[2]["score"]
    assert results[0]["score"] == pytest.approx(1.0)


def test_rank_chunks_respects_top_n() -> None:
    results = rank_chunks([1.0, 0.0], _fake_records(), top_n=2)

    assert len(results) == 2
    assert [r["id"] for r in results] == [1, 2]


def test_rank_chunks_excludes_embedding_from_results() -> None:
    results = rank_chunks([1.0, 0.0], _fake_records(), top_n=1)

    assert "embedding" not in results[0]
    assert results[0]["text"] == "exact match"
    assert results[0]["source"] == "doc.txt"


def test_rank_chunks_handles_empty_records() -> None:
    assert rank_chunks([1.0, 0.0], [], top_n=3) == []


def test_search_embeds_query_and_ranks_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(search_module, "embed_text", lambda text: [1.0, 0.0])

    results = search("does not matter", _fake_records(), top_n=1)

    assert results[0]["id"] == 1
    assert results[0]["score"] == pytest.approx(1.0)
