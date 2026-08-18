"""Command-line interface for the local RAG script.

Usage:
    python cli.py index <file_path>
    python cli.py search "<question>"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rag.chunk import chunk_text
from rag.embed import embed_text, embed_texts
from rag.extract import extract_text
from rag.search import rank_chunks
from rag.store import load_index, save_index

DEFAULT_INDEX_PATH = Path(__file__).parent / "data" / "index.json"


def cmd_index(args: argparse.Namespace) -> None:
    text = extract_text(args.file_path)
    chunks = chunk_text(text)
    if not chunks:
        raise ValueError(f"No text content found to index in {args.file_path}")

    print(f"Extracted {len(text)} characters, split into {len(chunks)} chunks. Embedding...")
    vectors = embed_texts([c["text"] for c in chunks])

    records = [
        {
            "id": chunk["id"],
            "text": chunk["text"],
            "embedding": vector,
            "source": Path(args.file_path).name,
        }
        for chunk, vector in zip(chunks, vectors)
    ]

    index_path = Path(args.index_path)
    save_index(index_path, records)
    print(f"Saved index with {len(records)} chunks to {index_path}")


def cmd_search(args: argparse.Namespace) -> None:
    records = load_index(args.index_path)
    query_vector = embed_text(args.question)
    results = rank_chunks(query_vector, records, top_n=args.top_n)

    if not results:
        print("No results found.")
        return

    for rank, result in enumerate(results, start=1):
        print(f"\n#{rank}  score={result['score']:.4f}  source={result['source']}")
        print(result["text"])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="Build a local embedding index over a document and search it.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser(
        "index", help="Extract, chunk, embed, and save the index for a document."
    )
    index_parser.add_argument("file_path", help="Path to a .txt or .pdf file.")
    index_parser.add_argument(
        "--index-path",
        dest="index_path",
        default=str(DEFAULT_INDEX_PATH),
        help=f"Where to save the index JSON file (default: {DEFAULT_INDEX_PATH}).",
    )
    index_parser.set_defaults(func=cmd_index)

    search_parser = subparsers.add_parser(
        "search", help="Search a previously built index for the most relevant chunks."
    )
    search_parser.add_argument("question", help="Natural-language question.")
    search_parser.add_argument(
        "--top-n", dest="top_n", type=int, default=3, help="Number of results to return (default: 3)."
    )
    search_parser.add_argument(
        "--index-path",
        dest="index_path",
        default=str(DEFAULT_INDEX_PATH),
        help=f"Path to the index JSON file (default: {DEFAULT_INDEX_PATH}).",
    )
    search_parser.set_defaults(func=cmd_search)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
