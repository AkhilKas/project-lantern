"""E5 Docling pipeline tests — written before implementation (Red-Green TDD).

Docling is heavy (~500MB with models) and lives behind the [docling] optional
extra. Most tests either exercise the graceful-fallback path or construct
DoclingDocument instances directly to test writers and shape. Real conversion
is exercised only when the `docling` package is importable.
"""

from __future__ import annotations

import importlib.util
import json
import logging
from pathlib import Path

import pytest

from lantern.parse.docling import (
    DoclingBlock,
    DoclingDocument,
    _map_label,
    extract_docling,
    write_docling_jsonl,
    write_docling_markdown,
)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


def test_docling_block_defaults() -> None:
    b = DoclingBlock(block_index=0, block_type="text", text="hello")
    assert b.block_index == 0
    assert b.block_type == "text"
    assert b.text == "hello"
    assert b.page_num == 1
    assert b.method == "docling"


def test_docling_document_defaults() -> None:
    d = DoclingDocument(source=Path("x.html"))
    assert d.source == Path("x.html")
    assert d.blocks == []
    assert d.markdown == ""


# ---------------------------------------------------------------------------
# _map_label — Docling label string → our BlockType
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label,expected",
    [
        ("section_header", "title"),
        ("title", "title"),
        ("page_header", "title"),
        ("text", "text"),
        ("paragraph", "text"),
        ("caption", "text"),
        ("table", "table"),
        ("list_item", "list"),
        ("picture", "figure"),
        ("footnote", "footnote"),
        ("page_footer", "footnote"),
        ("something_novel", "unknown"),
    ],
)
def test_map_label(label: str, expected: str) -> None:
    assert _map_label(label) == expected


def test_map_label_is_case_insensitive() -> None:
    assert _map_label("SECTION_HEADER") == "title"
    assert _map_label("Table") == "table"


# ---------------------------------------------------------------------------
# extract_docling routing + graceful fallback
# ---------------------------------------------------------------------------


def _docling_installed() -> bool:
    return importlib.util.find_spec("docling") is not None


def test_extract_docling_returns_document(tmp_path: Path) -> None:
    f = tmp_path / "primary-document.html"
    f.write_text("<html><body><p>Hello</p></body></html>")
    result = extract_docling(f)
    assert isinstance(result, DoclingDocument)
    assert result.source == f


@pytest.mark.skipif(_docling_installed(), reason="docling installed — skipping missing-dep test")
def test_extract_docling_without_docling_returns_empty(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    f = tmp_path / "primary-document.html"
    f.write_text("<html><body><p>Hello</p></body></html>")

    with caplog.at_level(logging.WARNING, logger="lantern.parse.docling"):
        result = extract_docling(f)

    assert result.blocks == []
    assert result.markdown == ""
    assert any("docling" in m.lower() for m in caplog.messages)


@pytest.mark.skipif(_docling_installed(), reason="docling installed — skipping missing-dep test")
def test_extract_docling_pdf_without_docling_returns_empty(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    f = tmp_path / "filing.pdf"
    f.write_bytes(b"%PDF-1.4 fake")

    with caplog.at_level(logging.WARNING, logger="lantern.parse.docling"):
        result = extract_docling(f)

    assert result.blocks == []
    assert result.markdown == ""


# ---------------------------------------------------------------------------
# write_docling_jsonl
# ---------------------------------------------------------------------------


def _sample_document(source: Path) -> DoclingDocument:
    return DoclingDocument(
        source=source,
        blocks=[
            DoclingBlock(block_index=0, block_type="title", text="Apple Inc."),
            DoclingBlock(block_index=1, block_type="text", text="We design electronics."),
            DoclingBlock(block_index=2, block_type="table", text="Revenue 394B"),
        ],
        markdown="# Apple Inc.\n\nWe design electronics.\n",
    )


def test_write_docling_jsonl_creates_file(tmp_path: Path) -> None:
    doc = _sample_document(tmp_path / "primary-document.html")
    out = write_docling_jsonl(doc, tmp_path / "interim", "AAPL", "10-K", "acc-001")
    assert out.exists()
    assert out.suffix == ".jsonl"


def test_write_docling_jsonl_output_path(tmp_path: Path) -> None:
    doc = _sample_document(tmp_path / "x.html")
    out = write_docling_jsonl(doc, tmp_path / "interim", "XOM", "10-K", "acc-xyz")
    assert out == tmp_path / "interim" / "XOM" / "10-K" / "acc-xyz_docling.jsonl"


def test_write_docling_jsonl_valid_json_lines(tmp_path: Path) -> None:
    doc = _sample_document(tmp_path / "x.html")
    out = write_docling_jsonl(doc, tmp_path / "interim", "MSFT", "10-Q", "acc-002")

    lines = out.read_text().splitlines()
    assert len(lines) == 3
    first = json.loads(lines[0])
    assert first["block_index"] == 0
    assert first["block_type"] == "title"
    assert first["text"] == "Apple Inc."
    assert first["page_num"] == 1
    assert first["method"] == "docling"


def test_write_docling_jsonl_empty_document_writes_empty_file(tmp_path: Path) -> None:
    doc = DoclingDocument(source=tmp_path / "x.html")
    out = write_docling_jsonl(doc, tmp_path / "interim", "JPM", "10-K", "acc-empty")
    assert out.exists()
    assert out.read_text() == ""


# ---------------------------------------------------------------------------
# write_docling_markdown
# ---------------------------------------------------------------------------


def test_write_docling_markdown_creates_file(tmp_path: Path) -> None:
    doc = _sample_document(tmp_path / "primary-document.html")
    out = write_docling_markdown(doc, tmp_path / "interim", "AAPL", "10-K", "acc-001")
    assert out.exists()
    assert out.suffix == ".md"


def test_write_docling_markdown_output_path(tmp_path: Path) -> None:
    doc = _sample_document(tmp_path / "x.html")
    out = write_docling_markdown(doc, tmp_path / "interim", "WMT", "10-Q", "acc-md")
    assert out == tmp_path / "interim" / "WMT" / "10-Q" / "acc-md_docling.md"


def test_write_docling_markdown_writes_markdown(tmp_path: Path) -> None:
    doc = _sample_document(tmp_path / "x.html")
    out = write_docling_markdown(doc, tmp_path / "interim", "AAPL", "10-K", "acc-001")
    assert out.read_text() == "# Apple Inc.\n\nWe design electronics.\n"


def test_write_docling_markdown_empty_document(tmp_path: Path) -> None:
    doc = DoclingDocument(source=tmp_path / "x.html")
    out = write_docling_markdown(doc, tmp_path / "interim", "JPM", "10-K", "acc-empty")
    assert out.exists()
    assert out.read_text() == ""


# ---------------------------------------------------------------------------
# CLI: `lantern docling`
# ---------------------------------------------------------------------------


def test_cli_docling_command_registered() -> None:
    from lantern.cli import main

    assert "docling" in main.commands


def test_cli_docling_no_documents(monkeypatch: pytest.MonkeyPatch) -> None:
    """When no primary documents exist, the command exits cleanly with a message."""
    from click.testing import CliRunner

    from lantern import cli as cli_mod

    monkeypatch.setattr(cli_mod, "find_primary_documents", lambda _: [])

    runner = CliRunner()
    result = runner.invoke(cli_mod.main, ["docling"])
    assert result.exit_code == 0
    assert "No primary documents" in result.output
