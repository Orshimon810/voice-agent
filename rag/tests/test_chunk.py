from rag.chunk import chunk_text


def _make_sentences(count: int) -> list[str]:
    return [
        f"This is sentence number {i} in the test document, added for chunk boundary testing{i}."
        for i in range(1, count + 1)
    ]


def test_chunk_empty_text_returns_empty_list() -> None:
    assert chunk_text("") == []
    assert chunk_text("   \n  ") == []


def test_chunk_short_text_returns_single_chunk() -> None:
    text = "Just one short paragraph that easily fits in a single chunk."

    chunks = chunk_text(text, chunk_size=500, overlap=50)

    assert len(chunks) == 1
    assert chunks[0] == {"id": 0, "text": text}


def test_chunk_ids_are_sequential() -> None:
    text = " ".join(_make_sentences(10))

    chunks = chunk_text(text, chunk_size=200, overlap=40)

    assert [c["id"] for c in chunks] == list(range(len(chunks)))


def test_chunk_respects_size_target() -> None:
    text = " ".join(_make_sentences(10))
    chunk_size, overlap = 200, 40

    chunks = chunk_text(text, chunk_size=chunk_size, overlap=overlap)

    assert len(chunks) > 1
    for chunk in chunks:
        # A chunk may briefly exceed chunk_size by at most one overlap tail,
        # but should never balloon far past the target.
        assert len(chunk["text"]) <= chunk_size + overlap


def test_chunk_does_not_split_sentences_that_fit() -> None:
    sentences = _make_sentences(10)
    text = " ".join(sentences)

    chunks = chunk_text(text, chunk_size=200, overlap=40)
    combined = " ".join(c["text"] for c in chunks)

    for sentence in sentences:
        assert sentence in combined


def test_chunk_overlap_shares_content_between_consecutive_chunks() -> None:
    text = " ".join(_make_sentences(10))
    overlap = 40

    chunks = chunk_text(text, chunk_size=200, overlap=overlap)

    assert len(chunks) > 1
    for prev_chunk, next_chunk in zip(chunks, chunks[1:]):
        prev_text, next_text = prev_chunk["text"], next_chunk["text"]
        last_word = prev_text.split()[-1]
        assert last_word in next_text.split()[: overlap // 4 + 5]


def test_chunk_word_boundaries_preserved_for_normal_text() -> None:
    text = " ".join(_make_sentences(6))
    original_words = set(text.split())

    chunks = chunk_text(text, chunk_size=150, overlap=30)

    for chunk in chunks:
        for word in chunk["text"].split():
            assert word in original_words


def test_chunk_hard_splits_a_single_oversized_word() -> None:
    long_word = "x" * 1200
    chunk_size, overlap = 500, 50

    chunks = chunk_text(long_word, chunk_size=chunk_size, overlap=overlap)

    assert len(chunks) > 1
    for chunk in chunks[:-1]:
        assert len(chunk["text"]) == chunk_size
    reconstructed = "".join(c["text"][0 if i == 0 else overlap :] for i, c in enumerate(chunks))
    assert reconstructed == long_word


def test_chunk_raises_when_overlap_not_smaller_than_chunk_size() -> None:
    try:
        chunk_text("some text", chunk_size=100, overlap=100)
        assert False, "expected ValueError"
    except ValueError:
        pass
