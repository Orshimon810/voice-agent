"""Text extraction from .txt and .pdf files."""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader

SUPPORTED_EXTENSIONS = (".txt", ".pdf")


class UnsupportedFileTypeError(ValueError):
    """Raised when the given file's extension is not supported."""


def extract_text(file_path: str | Path) -> str:
    """Extract full text from a .txt or .pdf file.

    Raises FileNotFoundError, UnsupportedFileTypeError, or ValueError (on
    empty/unreadable content).
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if not path.is_file():
        raise ValueError(f"Not a file: {path}")

    suffix = path.suffix.lower()
    if suffix == ".txt":
        text = _extract_txt(path)
    elif suffix == ".pdf":
        text = _extract_pdf(path)
    else:
        supported = ", ".join(SUPPORTED_EXTENSIONS)
        raise UnsupportedFileTypeError(
            f"Unsupported file extension '{suffix}' for {path}. "
            f"Supported extensions: {supported}"
        )

    if not text.strip():
        raise ValueError(f"No extractable text found in file: {path}")
    return text


def _extract_txt(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _extract_pdf(path: Path) -> str:
    reader = PdfReader(str(path))
    pages_text = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(pages_text)
