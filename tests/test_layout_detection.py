"""E4 layout detection tests — written before implementation (Red-Green TDD)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lantern.parse.layout import (
    FilingLayout,
    extract_html_layout,
    extract_layout,
    write_layout_jsonl,
)

# ---------------------------------------------------------------------------
# CSS-heuristic classification
# ---------------------------------------------------------------------------


def test_large_bold_span_is_title() -> None:
    html = '<div><span style="font-size:13pt;font-weight:700">FORM 10-K</span></div>'
    regions = extract_html_layout(html)
    assert any(r.region_type == "title" and "FORM 10-K" in r.text for r in regions)


def test_medium_bold_span_is_title() -> None:
    # 11pt and above are large-enough headings even without larger font
    html = '<div><span style="font-size:11pt;font-weight:700">Part I</span></div>'
    regions = extract_html_layout(html)
    assert any(r.region_type == "title" and "Part I" in r.text for r in regions)


def test_small_bold_section_header_is_title() -> None:
    # 9pt bold used for section headings throughout 10-K / 10-Q
    html = '<div><span style="font-size:9pt;font-weight:700">Item 1. Business</span></div>'
    regions = extract_html_layout(html)
    assert any(r.region_type == "title" and "Item 1" in r.text for r in regions)


def test_normal_weight_span_is_text() -> None:
    html = '<div><span style="font-size:9pt;font-weight:400">Apple designs consumer electronics.</span></div>'
    regions = extract_html_layout(html)
    assert any(r.region_type == "text" and "Apple" in r.text for r in regions)


def test_small_font_span_is_footnote() -> None:
    html = '<div><span style="font-size:8pt;font-weight:400">See Note 1 to the financial statements.</span></div>'
    regions = extract_html_layout(html)
    assert any(r.region_type == "footnote" for r in regions)


def test_table_element_is_table_region() -> None:
    html = "<table><tr><th>Revenue</th><td>394B</td></tr></table>"
    regions = extract_html_layout(html)
    assert any(r.region_type == "table" for r in regions)


def test_table_text_captured_in_table_region() -> None:
    html = "<table><tr><td>Net income</td><td>93,736</td></tr></table>"
    regions = extract_html_layout(html)
    table_regions = [r for r in regions if r.region_type == "table"]
    assert table_regions
    assert "Net income" in table_regions[0].text or "93,736" in table_regions[0].text


# ---------------------------------------------------------------------------
# Hidden / XBRL content exclusion
# ---------------------------------------------------------------------------


def test_hidden_div_content_excluded() -> None:
    html = (
        '<div style="display:none"><span style="font-size:13pt;font-weight:700">Hidden</span></div>'
        '<div><span style="font-size:13pt;font-weight:700">Visible</span></div>'
    )
    regions = extract_html_layout(html)
    assert not any("Hidden" in r.text for r in regions)
    assert any("Visible" in r.text for r in regions)


def test_xbrl_context_tags_excluded() -> None:
    html = (
        "<xbrli:context><xbrli:entity>APPLE</xbrli:entity></xbrli:context>"
        '<div><span style="font-size:9pt;font-weight:400">Visible text</span></div>'
    )
    regions = extract_html_layout(html)
    assert not any("APPLE" in r.text for r in regions)
    assert any("Visible text" in r.text for r in regions)


def test_ix_header_excluded() -> None:
    html = (
        "<ix:header><ix:hidden>"
        '<ix:nonNumeric name="dei:DocumentType">10-K</ix:nonNumeric>'
        "</ix:hidden></ix:header>"
        '<div><span style="font-size:9pt">Public content</span></div>'
    )
    regions = extract_html_layout(html)
    texts = " ".join(r.text for r in regions)
    assert "Public content" in texts


# ---------------------------------------------------------------------------
# Structural invariants
# ---------------------------------------------------------------------------


def test_empty_divs_filtered() -> None:
    html = "<div></div>" '<div><span style="font-size:9pt">Real content</span></div>'
    regions = extract_html_layout(html)
    assert all(r.text.strip() for r in regions)


def test_region_index_is_sequential() -> None:
    html = (
        '<div><span style="font-size:13pt;font-weight:700">Title</span></div>'
        '<div><span style="font-size:9pt">Body</span></div>'
        '<div><span style="font-size:8pt">Note</span></div>'
    )
    regions = extract_html_layout(html)
    assert [r.region_index for r in regions] == list(range(len(regions)))


def test_regions_preserve_document_order() -> None:
    html = (
        '<div><span style="font-size:17pt;font-weight:700">Alpha</span></div>'
        '<div><span style="font-size:9pt">Beta</span></div>'
        '<div><span style="font-size:11pt;font-weight:700">Gamma</span></div>'
    )
    regions = extract_html_layout(html)
    texts = [r.text for r in regions]
    assert texts.index("Alpha") < texts.index("Beta") < texts.index("Gamma")


def test_all_regions_have_html_css_method() -> None:
    html = (
        '<div><span style="font-size:13pt;font-weight:700">Heading</span></div>'
        '<div><span style="font-size:9pt">Body</span></div>'
    )
    regions = extract_html_layout(html)
    assert all(r.method == "html-css" for r in regions)


def test_all_regions_page_num_is_one_for_html() -> None:
    html = '<div><span style="font-size:9pt">Content</span></div>'
    regions = extract_html_layout(html)
    assert all(r.page_num == 1 for r in regions)


# ---------------------------------------------------------------------------
# extract_layout() routing
# ---------------------------------------------------------------------------


def test_extract_layout_html_returns_filing_layout(tmp_path: Path) -> None:
    f = tmp_path / "primary-document.html"
    f.write_text(
        '<div><span style="font-size:13pt;font-weight:700">Apple Inc.</span></div>'
        '<div><span style="font-size:9pt">We design electronics.</span></div>'
    )
    result = extract_layout(f)
    assert isinstance(result, FilingLayout)
    assert result.source == f
    assert any(r.region_type == "title" for r in result.regions)
    assert any(r.region_type == "text" for r in result.regions)


def test_extract_layout_pdf_without_layoutparser_returns_empty(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import importlib
    import logging

    if importlib.util.find_spec("layoutparser") is not None:
        pytest.skip("layoutparser installed — skipping missing-dep test")

    f = tmp_path / "filing.pdf"
    f.write_bytes(b"%PDF-1.4 fake")

    with caplog.at_level(logging.WARNING, logger="lantern.parse.layout"):
        result = extract_layout(f)

    assert result.regions == []
    assert any("layoutparser" in m.lower() for m in caplog.messages)


# ---------------------------------------------------------------------------
# write_layout_jsonl()
# ---------------------------------------------------------------------------


def test_write_layout_jsonl_creates_file(tmp_path: Path) -> None:
    f = tmp_path / "primary-document.html"
    f.write_text('<div><span style="font-size:9pt">Content</span></div>')
    filing = extract_layout(f)
    out = write_layout_jsonl(filing, tmp_path / "interim", "AAPL", "10-K", "acc-001")
    assert out.exists()
    assert out.suffix == ".jsonl"


def test_write_layout_jsonl_valid_json_lines(tmp_path: Path) -> None:
    f = tmp_path / "primary-document.html"
    f.write_text(
        '<div><span style="font-size:13pt;font-weight:700">Title</span></div>'
        '<div><span style="font-size:9pt">Body text here.</span></div>'
    )
    filing = extract_layout(f)
    out = write_layout_jsonl(filing, tmp_path / "interim", "MSFT", "10-Q", "acc-002")

    lines = out.read_text().splitlines()
    assert len(lines) >= 2
    first = json.loads(lines[0])
    assert "region_index" in first
    assert "region_type" in first
    assert "text" in first
    assert "method" in first
    assert first["region_type"] in {"title", "text", "table", "footnote", "figure", "unknown"}


def test_write_layout_jsonl_output_path(tmp_path: Path) -> None:
    f = tmp_path / "primary-document.html"
    f.write_text('<div><span style="font-size:9pt">Content</span></div>')
    filing = extract_layout(f)
    out = write_layout_jsonl(filing, tmp_path / "interim", "XOM", "10-K", "acc-xyz")
    assert out == tmp_path / "interim" / "XOM" / "10-K" / "acc-xyz_layout.jsonl"
