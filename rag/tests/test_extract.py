from pathlib import Path

import pytest

from pdf_fixture import build_minimal_pdf
from rag.extract import UnsupportedFileTypeError, extract_text


def test_extract_txt_reads_utf8_content(tmp_path: Path) -> None:
    file_path = tmp_path / "sample.txt"
    file_path.write_text("שלום עולם\nHello world", encoding="utf-8")

    text = extract_text(file_path)

    assert "שלום עולם" in text
    assert "Hello world" in text


def test_extract_txt_on_real_sample_document() -> None:
    sample_path = Path(__file__).parent.parent / "data" / "chok_tivi_aviation_services_law.txt"

    text = extract_text(sample_path)

    assert len(text) > 0
    assert "חוק שירותי תעופה" in text


def test_extract_pdf_reads_text(tmp_path: Path) -> None:
    file_path = tmp_path / "sample.pdf"
    file_path.write_bytes(build_minimal_pdf("Hello World"))

    text = extract_text(file_path)

    assert "Hello World" in text


def test_extract_missing_file_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        extract_text(tmp_path / "does_not_exist.txt")


def test_extract_empty_txt_raises_value_error(tmp_path: Path) -> None:
    file_path = tmp_path / "empty.txt"
    file_path.write_text("   \n  ", encoding="utf-8")

    with pytest.raises(ValueError):
        extract_text(file_path)


def test_extract_unsupported_extension_raises(tmp_path: Path) -> None:
    file_path = tmp_path / "sample.docx"
    file_path.write_text("content", encoding="utf-8")

    with pytest.raises(UnsupportedFileTypeError):
        extract_text(file_path)
