"""E3 table extraction tests — written before implementation (Red-Green TDD)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lantern.parse.tables import (
    FilingTables,
    extract_html_tables,
    extract_tables,
    write_tables_jsonl,
)

# ---------------------------------------------------------------------------
# _HtmlTableExtractor / extract_html_tables unit tests
# ---------------------------------------------------------------------------


def test_simple_table_with_headers() -> None:
    html = """
    <table>
      <tr><th>Name</th><th>Value</th></tr>
      <tr><td>Revenue</td><td>394328</td></tr>
    </table>
    """
    tables = extract_html_tables(html)
    assert len(tables) == 1
    assert tables[0].rows == [["Name", "Value"], ["Revenue", "394328"]]
    assert tables[0].method == "html"
    assert tables[0].table_index == 0


def test_multiple_tables_returned() -> None:
    html = """
    <table><tr><td>Table A</td></tr></table>
    <table><tr><td>Table B</td></tr></table>
    """
    tables = extract_html_tables(html)
    assert len(tables) == 2
    assert tables[0].rows[0][0] == "Table A"
    assert tables[1].rows[0][0] == "Table B"
    assert tables[0].table_index == 0
    assert tables[1].table_index == 1


def test_table_index_is_sequential() -> None:
    html = "".join(f"<table><tr><td>T{i}</td></tr></table>" for i in range(4))
    tables = extract_html_tables(html)
    assert [t.table_index for t in tables] == [0, 1, 2, 3]


def test_nested_span_text_is_flattened() -> None:
    html = """
    <table>
      <tr><td><span>Hello</span> <b>World</b></td></tr>
    </table>
    """
    tables = extract_html_tables(html)
    cell = tables[0].rows[0][0]
    assert "Hello" in cell
    assert "World" in cell


def test_layout_table_with_no_text_excluded() -> None:
    # Spacer tables used for visual layout — no readable content
    html = """
    <table>
      <tr><td style="width:1.0%"/><td style="width:99%"/></tr>
    </table>
    <table>
      <tr><td>Real data</td></tr>
    </table>
    """
    tables = extract_html_tables(html)
    assert len(tables) == 1
    assert tables[0].rows[0][0] == "Real data"


def test_table_inside_hidden_div_skipped() -> None:
    html = """
    <div style="display:none">
      <table><tr><td>Secret</td></tr></table>
    </div>
    <table><tr><td>Visible</td></tr></table>
    """
    tables = extract_html_tables(html)
    assert len(tables) == 1
    assert tables[0].rows[0][0] == "Visible"


def test_xbrl_tags_inside_cell_do_not_leak_metadata() -> None:
    html = """
    <table>
      <tr>
        <td><ix:nonfraction name="us-gaap:Revenue">394328</ix:nonfraction></td>
        <td><xbrli:context id="c1">ctx</xbrli:context></td>
      </tr>
    </table>
    """
    tables = extract_html_tables(html)
    assert len(tables) == 1
    row = tables[0].rows[0]
    assert row[0] == "394328"
    assert row[1] == ""  # xbrli context tag text is stripped


def test_multirow_table_preserves_order() -> None:
    html = """
    <table>
      <tr><td>R1C1</td><td>R1C2</td></tr>
      <tr><td>R2C1</td><td>R2C2</td></tr>
      <tr><td>R3C1</td><td>R3C2</td></tr>
    </table>
    """
    tables = extract_html_tables(html)
    assert tables[0].rows == [
        ["R1C1", "R1C2"],
        ["R2C1", "R2C2"],
        ["R3C1", "R3C2"],
    ]


def test_nested_table_extracted_separately() -> None:
    html = """
    <table>
      <tr><td>outer
        <table><tr><td>inner</td></tr></table>
      </td></tr>
    </table>
    """
    tables = extract_html_tables(html)
    texts = [t.rows[0][0] for t in tables]
    assert any("inner" in t for t in texts)


# ---------------------------------------------------------------------------
# extract_tables() routing
# ---------------------------------------------------------------------------


def test_extract_tables_html_file(tmp_path: Path) -> None:
    from lantern.config import TablesConfig

    cfg = TablesConfig(camelot_flavors=["lattice", "stream"])
    f = tmp_path / "primary-document.html"
    f.write_text(
        "<table><tr><th>Item</th><th>Amount</th></tr><tr><td>Cash</td><td>100</td></tr></table>"
    )

    result = extract_tables(f, cfg)

    assert isinstance(result, FilingTables)
    assert result.source == f
    assert len(result.tables) == 1
    assert result.tables[0].method == "html"


def test_extract_tables_pdf_without_camelot_returns_empty(tmp_path: Path) -> None:
    """When camelot is not installed, PDF path should warn and return empty tables."""
    pytest.importorskip("camelot", reason="camelot not installed — skipping PDF path test")

    from lantern.config import TablesConfig

    cfg = TablesConfig(camelot_flavors=["lattice", "stream"])
    f = tmp_path / "filing.pdf"
    f.write_bytes(b"%PDF-1.4 fake")  # not a real PDF, just tests routing

    result = extract_tables(f, cfg)
    assert isinstance(result, FilingTables)


def test_extract_tables_no_camelot_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """PDF extraction without camelot installed should log a warning, not raise."""
    import importlib

    camelot_present = importlib.util.find_spec("camelot") is not None
    if camelot_present:
        pytest.skip("camelot is installed — this test checks the missing-camelot path")

    import logging

    from lantern.config import TablesConfig

    cfg = TablesConfig(camelot_flavors=["lattice", "stream"])
    f = tmp_path / "filing.pdf"
    f.write_bytes(b"%PDF-1.4 fake")

    with caplog.at_level(logging.WARNING, logger="lantern.parse.tables"):
        result = extract_tables(f, cfg)

    assert result.tables == []
    assert any("camelot" in m.lower() for m in caplog.messages)


# ---------------------------------------------------------------------------
# write_tables_jsonl()
# ---------------------------------------------------------------------------


def test_write_tables_jsonl_creates_file(tmp_path: Path) -> None:
    from lantern.config import TablesConfig

    cfg = TablesConfig(camelot_flavors=["lattice", "stream"])
    src = tmp_path / "primary-document.html"
    src.write_text("<table><tr><th>Col</th></tr><tr><td>Val</td></tr></table>")

    filing = extract_tables(src, cfg)
    out = write_tables_jsonl(filing, tmp_path / "interim", "AAPL", "10-K", "0000001-25-000001")

    assert out.exists()
    assert out.suffix == ".jsonl"


def test_write_tables_jsonl_valid_json_lines(tmp_path: Path) -> None:
    from lantern.config import TablesConfig

    cfg = TablesConfig(camelot_flavors=["lattice", "stream"])
    src = tmp_path / "primary-document.html"
    src.write_text(
        "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>"
        "<table><tr><td>X</td></tr></table>"
    )

    filing = extract_tables(src, cfg)
    out = write_tables_jsonl(filing, tmp_path / "interim", "AAPL", "10-K", "acc-001")

    lines = out.read_text().splitlines()
    assert len(lines) == 2  # one line per table
    first = json.loads(lines[0])
    assert first["table_index"] == 0
    assert first["method"] == "html"
    assert first["rows"] == [["A", "B"], ["1", "2"]]


def test_write_tables_jsonl_output_path(tmp_path: Path) -> None:
    from lantern.config import TablesConfig

    cfg = TablesConfig(camelot_flavors=["lattice", "stream"])
    src = tmp_path / "primary-document.html"
    src.write_text("<table><tr><td>data</td></tr></table>")

    filing = extract_tables(src, cfg)
    out = write_tables_jsonl(filing, tmp_path / "interim", "XOM", "10-Q", "acc-xyz")

    assert out == tmp_path / "interim" / "XOM" / "10-Q" / "acc-xyz_tables.jsonl"
