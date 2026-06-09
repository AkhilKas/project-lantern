"""Tests for E2 text extraction (HTML/iXBRL path)."""

from __future__ import annotations

from pathlib import Path

from lantern.parse.text import FilingText, _IxbrlTextExtractor, extract, find_primary_documents

# ---------------------------------------------------------------------------
# _IxbrlTextExtractor unit tests
# ---------------------------------------------------------------------------


def _parse(html: str) -> str:
    p = _IxbrlTextExtractor()
    p.feed(html)
    return p.get_text()


def test_plain_html_extracts_text() -> None:
    html = "<html><body><p>Hello world</p></body></html>"
    assert "Hello world" in _parse(html)


def test_script_and_style_are_skipped() -> None:
    html = "<html><body><script>var x=1;</script><style>.c{color:red}</style><p>Visible</p></body></html>"
    text = _parse(html)
    assert "var x" not in text
    assert "color:red" not in text
    assert "Visible" in text


def test_hidden_div_is_skipped() -> None:
    html = (
        "<html><body>"
        '<div style="display:none"><p>Hidden XBRL</p></div>'
        "<p>Visible text</p>"
        "</body></html>"
    )
    text = _parse(html)
    assert "Hidden XBRL" not in text
    assert "Visible text" in text


def test_nested_hidden_div_does_not_leak() -> None:
    html = (
        "<html><body>"
        '<div style="display:none">'
        "  <div><div><p>Deep hidden</p></div></div>"
        "</div>"
        "<p>After</p>"
        "</body></html>"
    )
    text = _parse(html)
    assert "Deep hidden" not in text
    assert "After" in text


def test_xbrl_context_tags_are_skipped() -> None:
    html = (
        "<html><body>"
        "<xbrli:context id='c1'><xbrli:entity>0001</xbrli:entity></xbrli:context>"
        "<xbrldi:explicitMember>value</xbrldi:explicitMember>"
        "<p>Report text</p>"
        "</body></html>"
    )
    text = _parse(html)
    assert "0001" not in text
    assert "value" not in text
    assert "Report text" in text


def test_ixbrl_inline_tags_keep_their_text() -> None:
    # ix:nonfraction and ix:nonnumeric wrap visible values — text must be preserved
    html = (
        "<html><body>"
        '<p>Revenue: <ix:nonfraction name="us-gaap:Revenue">394328</ix:nonfraction></p>'
        "</body></html>"
    )
    text = _parse(html)
    assert "394328" in text
    assert "Revenue" in text


def test_ix_header_hidden_section_skipped() -> None:
    html = (
        "<html><body>"
        "<ix:header><ix:hidden><ix:nonNumeric>hidden-value</ix:nonNumeric></ix:hidden></ix:header>"
        "<p>Public</p>"
        "</body></html>"
    )
    text = _parse(html)
    assert "hidden-value" not in text
    assert "Public" in text


def test_head_tag_content_skipped() -> None:
    html = "<html><head><title>SEC Filing</title></head><body><p>Body text</p></body></html>"
    text = _parse(html)
    assert "SEC Filing" not in text
    assert "Body text" in text


# ---------------------------------------------------------------------------
# extract() routing
# ---------------------------------------------------------------------------


def test_extract_html_returns_filing_text(tmp_path: Path) -> None:
    from lantern.config import TextExtractionConfig

    cfg = TextExtractionConfig()
    html_file = tmp_path / "primary-document.html"
    html_file.write_text("<html><body><p>Apple Inc.</p></body></html>")

    result = extract(html_file, cfg)

    assert isinstance(result, FilingText)
    assert len(result.pages) == 1
    assert result.pages[0].method == "html"
    assert "Apple Inc." in result.full_text


def test_extract_unknown_suffix_falls_back_to_html(tmp_path: Path) -> None:
    from lantern.config import TextExtractionConfig

    cfg = TextExtractionConfig()
    f = tmp_path / "filing.xhtml"
    f.write_text("<html><body><p>XHTML content</p></body></html>")

    result = extract(f, cfg)
    assert "XHTML content" in result.full_text


# ---------------------------------------------------------------------------
# find_primary_documents()
# ---------------------------------------------------------------------------


def test_find_primary_documents(tmp_path: Path) -> None:
    base = tmp_path / "sec-edgar-filings" / "AAPL" / "10-K" / "0000001-25-000001"
    base.mkdir(parents=True)
    doc = base / "primary-document.html"
    doc.write_text("<html><body><p>test</p></body></html>")

    results = find_primary_documents(tmp_path)

    assert len(results) == 1
    ticker, form, accession, path = results[0]
    assert ticker == "AAPL"
    assert form == "10-K"
    assert accession == "0000001-25-000001"
    assert path == doc


def test_find_primary_documents_empty_dir(tmp_path: Path) -> None:
    results = find_primary_documents(tmp_path)
    assert results == []
