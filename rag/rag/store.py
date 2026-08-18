"""Local JSON storage for chunk records (text + embedding + source)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

Record = dict[str, Any]


def save_index(path: str | Path, records: list[Record]) -> None:
    """Write chunk records (with embeddings) to a local JSON file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def load_index(path: str | Path) -> list[Record]:
    """Load previously saved chunk records from a local JSON file."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Index file not found: {path}. Run 'python cli.py index <file_path>' first."
        )
    with path.open("r", encoding="utf-8") as f:
        records: list[Record] = json.load(f)
    if not records:
        raise ValueError(f"Index file is empty: {path}")
    return records
