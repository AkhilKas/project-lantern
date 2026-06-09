"""E2: Text extraction from SEC filings (iXBRL HTML and PDF).

HTML path: stdlib HTMLParser that skips iXBRL/XBRL metadata sections and
hidden divs, keeping all visible text content.

PDF path: pdfplumber page-by-page, with optional Tesseract OCR fallback for
pages whose character count falls below params.yaml ocr_char_threshold.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal

from lantern.config import TextExtractionConfig

log = logging.getLogger(__name__)

Method = Literal["html", "pdfplumber", "ocr"]


@dataclass
class PageText:
    page_num: int
    text: str
    method: Method
    char_count: int


@dataclass
class FilingText:
    source: Path
    pages: list[PageText]

    @property
    def full_text(self) -> str:
        return "\n\n".join(p.text for p in self.pages if p.text)


# ---------------------------------------------------------------------------
# HTML / iXBRL extractor
# ---------------------------------------------------------------------------


class _IxbrlTextExtractor(HTMLParser):
    """Extract visible text from iXBRL HTML, skipping XBRL metadata."""

    _FULL_SKIP: frozenset[str] = frozenset(
        {
            "script",
            "style",
            "head",
            "ix:header",
            "ix:hidden",
            "ix:resources",
        }
    )
    _XBRL_NS_SKIP: frozenset[str] = frozenset({"xbrli", "xbrldi", "link", "xlink"})
    _BLOCK: frozenset[str] = frozenset(
        {
            "p",
            "div",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "li",
            "ul",
            "ol",
            "section",
            "article",
            "blockquote",
        }
    )
    _ROW: frozenset[str] = frozenset({"tr"})
    _CELL: frozenset[str] = frozenset({"td", "th"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._buf: list[str] = []
        self._skip: int = 0
        self._div_depth: int = 0
        self._hide_at: int = -1  # div_depth when we entered a display:none subtree

    @staticmethod
    def _is_display_none(attrs: list[tuple[str, str | None]]) -> bool:
        for name, val in attrs:
            if name == "style" and val and re.search(r"display\s*:\s*none", val, re.IGNORECASE):
                return True
        return False

    @staticmethod
    def _ns(tag: str) -> str:
        return tag.split(":")[0] if ":" in tag else ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        ns = self._ns(tag)
        if tag in self._FULL_SKIP or ns in self._XBRL_NS_SKIP:
            self._skip += 1
            return
        if self._skip > 0:
            return

        if tag == "div":
            self._div_depth += 1
            if self._hide_at == -1 and self._is_display_none(attrs):
                self._hide_at = self._div_depth
        if self._hide_at != -1:
            return

        if tag in self._BLOCK or tag in self._ROW:
            self._buf.append("\n")
        elif tag in self._CELL:
            self._buf.append("\t")
        elif tag == "br":
            self._buf.append("\n")

    def handle_endtag(self, tag: str) -> None:
        ns = self._ns(tag)
        if tag in self._FULL_SKIP or ns in self._XBRL_NS_SKIP:
            if self._skip > 0:
                self._skip -= 1
            return
        if tag == "div":
            if self._hide_at != -1 and self._div_depth == self._hide_at:
                self._hide_at = -1
            self._div_depth = max(0, self._div_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._skip == 0 and self._hide_at == -1:
            self._buf.append(data)

    def get_text(self) -> str:
        raw = "".join(self._buf)
        lines = [" ".join(line.split()) for line in raw.split("\n")]
        return "\n".join(line for line in lines if line)


def _extract_html(path: Path) -> list[PageText]:
    html = path.read_text(encoding="utf-8", errors="replace")
    extractor = _IxbrlTextExtractor()
    extractor.feed(html)
    text = extractor.get_text()
    char_count = len(text.replace(" ", "").replace("\n", ""))
    return [PageText(page_num=1, text=text, method="html", char_count=char_count)]


# ---------------------------------------------------------------------------
# PDF extractor
# ---------------------------------------------------------------------------


def _ocr_page(page: object, cfg: TextExtractionConfig) -> str:
    try:
        import pytesseract  # type: ignore[import]
    except ImportError:
        log.warning("OCR requested but pytesseract not installed — pip install 'lantern[ocr]'")
        return ""
    img = page.to_image(resolution=cfg.ocr_dpi).original  # type: ignore[attr-defined]
    return pytesseract.image_to_string(img, lang=cfg.ocr_lang)


def _extract_pdf(path: Path, cfg: TextExtractionConfig) -> list[PageText]:
    import pdfplumber  # already in base deps

    pages: list[PageText] = []
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            non_ws = len(re.sub(r"\s", "", text))
            if non_ws >= cfg.ocr_char_threshold:
                pages.append(
                    PageText(page_num=i, text=text, method="pdfplumber", char_count=non_ws)
                )
            else:
                log.debug("Page %d has %d chars, falling back to OCR", i, non_ws)
                ocr_text = _ocr_page(page, cfg)
                pages.append(
                    PageText(
                        page_num=i,
                        text=ocr_text,
                        method="ocr",
                        char_count=len(re.sub(r"\s", "", ocr_text)),
                    )
                )
    return pages


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract(path: Path, cfg: TextExtractionConfig) -> FilingText:
    """Extract text from a filing (HTML/iXBRL or PDF), returning a FilingText."""
    suffix = path.suffix.lower()
    if suffix in (".html", ".htm"):
        pages = _extract_html(path)
    elif suffix == ".pdf":
        pages = _extract_pdf(path, cfg)
    else:
        log.warning("Unknown suffix %s for %s, trying HTML extraction", suffix, path.name)
        pages = _extract_html(path)
    log.info(
        "Extracted %d page(s) via %s from %s",
        len(pages),
        pages[0].method if pages else "n/a",
        path.name,
    )
    return FilingText(source=path, pages=pages)


def find_primary_documents(
    raw_dir: Path,
) -> list[tuple[str, str, str, Path]]:
    """Return (ticker, form, accession, path) for each primary document found."""
    results: list[tuple[str, str, str, Path]] = []
    base = raw_dir / "sec-edgar-filings"
    if not base.exists():
        return results
    for ticker_dir in sorted(base.iterdir()):
        if not ticker_dir.is_dir():
            continue
        for form_dir in sorted(ticker_dir.iterdir()):
            if not form_dir.is_dir():
                continue
            for accession_dir in sorted(form_dir.iterdir()):
                if not accession_dir.is_dir():
                    continue
                for candidate in (
                    "primary-document.html",
                    "primary-document.htm",
                    "primary-document.pdf",
                ):
                    doc = accession_dir / candidate
                    if doc.exists():
                        results.append((ticker_dir.name, form_dir.name, accession_dir.name, doc))
                        break
    return results


def write_interim(
    filing: FilingText, interim_dir: Path, ticker: str, form: str, accession: str
) -> Path:
    """Write extracted text to data/interim/<ticker>/<form>/<accession>.txt."""
    out = interim_dir / ticker / form / f"{accession}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(filing.full_text, encoding="utf-8")
    return out
