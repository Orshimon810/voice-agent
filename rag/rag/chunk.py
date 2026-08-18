"""Split extracted text into overlapping chunks suitable for embedding."""

from __future__ import annotations

import re

DEFAULT_CHUNK_SIZE = 500
DEFAULT_OVERLAP = 50

_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def chunk_text(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[dict[str, int | str]]:
    """Split text into overlapping chunks, preferring paragraph/sentence
    boundaries over mid-word splits.

    Returns a list of {"id": int, "text": str} dicts.
    """
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")
    if not text or not text.strip():
        return []

    units = _build_units(text, chunk_size, overlap)
    chunks = _pack_units(units, chunk_size, overlap)
    return [{"id": i, "text": chunk} for i, chunk in enumerate(chunks)]


def _build_units(text: str, chunk_size: int, overlap: int) -> list[tuple[str, bool]]:
    """Break text into (piece, is_hard_split) tuples no larger than
    chunk_size, splitting on paragraph boundaries first, then sentence
    boundaries, then hard character splits as a last resort. Hard-split
    pieces already carry their own overlap and are marked so the packing
    step emits them as-is instead of re-merging them at word boundaries
    that don't exist within them."""
    units: list[tuple[str, bool]] = []
    paragraphs = [p.strip() for p in _PARAGRAPH_SPLIT_RE.split(text) if p.strip()]
    for paragraph in paragraphs:
        if len(paragraph) <= chunk_size:
            units.append((paragraph, False))
            continue
        for sentence in _SENTENCE_SPLIT_RE.split(paragraph):
            sentence = sentence.strip()
            if not sentence:
                continue
            if len(sentence) <= chunk_size:
                units.append((sentence, False))
            else:
                units.extend((piece, True) for piece in _hard_split(sentence, chunk_size, overlap))
    return units


def _hard_split(text: str, chunk_size: int, overlap: int) -> list[str]:
    step = chunk_size - overlap
    return [text[i : i + chunk_size] for i in range(0, len(text), step)]


def _pack_units(units: list[tuple[str, bool]], chunk_size: int, overlap: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for unit, is_hard_split in units:
        if is_hard_split:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(unit)
            continue

        candidate = f"{current} {unit}".strip() if current else unit
        if len(candidate) <= chunk_size:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = f"{_overlap_tail(current, overlap)} {unit}".strip()
        else:
            current = unit

    if current:
        chunks.append(current)
    return chunks


def _overlap_tail(text: str, overlap: int) -> str:
    """Return up to the last `overlap` characters of text, trimmed to
    start at a word boundary so chunks don't begin mid-word."""
    tail = text[-overlap:]
    space_idx = tail.find(" ")
    if space_idx != -1:
        tail = tail[space_idx + 1 :]
    return tail
