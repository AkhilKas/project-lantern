"""E4: Layout detection for SEC filings.

HTML path: CSS-heuristic classifier. iXBRL HTML from Workiva uses no semantic
heading tags (h1-h6, p, ul). All layout is expressed via inline CSS on <div>
and <span> elements. We classify each div block by the dominant font-size and
font-weight of its span content:

    font-size >= 11pt                  → title  (large document headings)
    font-size >= 9pt AND bold (700)    → title  (section headings e.g. "Item 1.")
    font-size <= 8pt                   → footnote
    everything else                    → text
    <table> elements                   → table

PDF path: LayoutParser with EfficientDet backend (optional, [layout] extra).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

RegionType = Literal["title", "text", "table", "figure", "footnote", "unknown"]
Method = Literal["html-css", "layoutparser"]

_FULL_SKIP: frozenset[str] = frozenset({"script", "style", "head", "ix:header", "ix:hidden"})
_XBRL_NS_SKIP: frozenset[str] = frozenset({"xbrli", "xbrldi", "link", "xlink"})


@dataclass
class LayoutRegion:
    region_index: int
    region_type: RegionType
    text: str
    page_num: int = 1
    method: Method = "html-css"
    bbox: tuple[float, float, float, float] | None = None


@dataclass
class FilingLayout:
    source: Path
    regions: list[LayoutRegion] = field(default_factory=list)


# ---------------------------------------------------------------------------
# CSS-heuristic HTML extractor
# ---------------------------------------------------------------------------


def _classify(max_size: float, is_bold: bool) -> RegionType:
    if max_size >= 11:
        return "title"
    if max_size >= 9 and is_bold:
        return "title"
    if 0 < max_size <= 8:
        return "footnote"
    return "text"


class _HtmlLayoutExtractor(HTMLParser):
    """Walk iXBRL HTML and classify div-level text blocks by CSS heuristics."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip: int = 0
        self._div_depth: int = 0
        self._hide_at: int = -1
        self._table_depth: int = 0
        # stack of div frames: each holds {text, max_size, is_bold}
        self._div_stack: list[dict] = []
        # current span style
        self._span_size: float = 0.0
        self._span_bold: bool = False
        self._raw_regions: list[tuple[RegionType, str]] = []

    def _hidden(self) -> bool:
        return self._skip > 0 or self._hide_at != -1

    @staticmethod
    def _ns(tag: str) -> str:
        return tag.split(":")[0] if ":" in tag else ""

    @staticmethod
    def _is_display_none(attrs: list[tuple[str, str | None]]) -> bool:
        for name, val in attrs:
            if name == "style" and val and re.search(r"display\s*:\s*none", val, re.IGNORECASE):
                return True
        return False

    @staticmethod
    def _parse_span_style(attrs: list[tuple[str, str | None]]) -> tuple[float, bool]:
        style = ""
        for name, val in attrs:
            if name == "style" and val:
                style = val
        m = re.search(r"font-size:([\d.]+)pt", style)
        size = float(m.group(1)) if m else 0.0
        bold = bool(re.search(r"font-weight\s*:\s*(700|bold)", style))
        return size, bold

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        ns = self._ns(tag)
        if tag in _FULL_SKIP or ns in _XBRL_NS_SKIP:
            self._skip += 1
            return
        if self._skip > 0:
            return

        if tag == "div":
            self._div_depth += 1
            if self._hide_at == -1 and self._is_display_none(attrs):
                self._hide_at = self._div_depth
        if self._hidden():
            return

        if tag == "div":
            self._div_stack.append({"text": [], "max_size": 0.0, "is_bold": False})
        elif tag == "table":
            self._table_depth += 1
        elif tag == "span":
            self._span_size, self._span_bold = self._parse_span_style(attrs)

    def handle_endtag(self, tag: str) -> None:
        ns = self._ns(tag)
        if tag in _FULL_SKIP or ns in _XBRL_NS_SKIP:
            if self._skip > 0:
                self._skip -= 1
            return

        if tag == "div":
            if self._hide_at != -1 and self._div_depth == self._hide_at:
                self._hide_at = -1
            self._div_depth = max(0, self._div_depth - 1)
            if self._div_stack and not self._hidden():
                frame = self._div_stack.pop()
                text = " ".join(frame["text"]).strip()
                if text:
                    region_type = _classify(frame["max_size"], frame["is_bold"])
                    self._raw_regions.append((region_type, text))
            elif self._div_stack:
                self._div_stack.pop()
            return

        if self._hidden():
            return

        if tag == "table":
            self._table_depth = max(0, self._table_depth - 1)
        elif tag == "span":
            self._span_size = 0.0
            self._span_bold = False

    def handle_data(self, data: str) -> None:
        if self._hidden():
            return
        text = data.strip()
        if not text:
            return
        if self._table_depth > 0:
            # collect table text into the top div frame
            if self._div_stack:
                frame = self._div_stack[-1]
                frame["text"].append(text)
                # table region type set explicitly on close — track separately
            return
        if self._div_stack:
            frame = self._div_stack[-1]
            frame["text"].append(text)
            if self._span_size > frame["max_size"]:
                frame["max_size"] = self._span_size
            if self._span_bold:
                frame["is_bold"] = True

    def regions(self) -> list[tuple[RegionType, str]]:
        return self._raw_regions


class _TableTextCollector(HTMLParser):
    """Lightweight parser: collect visible text from a <table> subtree."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._buf: list[str] = []
        self._skip: int = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        ns = tag.split(":")[0] if ":" in tag else ""
        if tag in _FULL_SKIP or ns in _XBRL_NS_SKIP:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        ns = tag.split(":")[0] if ":" in tag else ""
        if (tag in _FULL_SKIP or ns in _XBRL_NS_SKIP) and self._skip > 0:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._skip == 0 and data.strip():
            self._buf.append(data.strip())

    def text(self) -> str:
        return " ".join(self._buf)


def _split_tables(html: str) -> list[tuple[bool, str]]:
    """Split HTML into segments: (is_table, content)."""
    segments: list[tuple[bool, str]] = []
    pos = 0
    for m in re.finditer(r"<table\b", html, re.IGNORECASE):
        if m.start() > pos:
            segments.append((False, html[pos : m.start()]))
        # find matching </table>
        depth = 1
        search = m.end()
        while depth > 0 and search < len(html):
            open_m = re.search(r"<table\b", html[search:], re.IGNORECASE)
            close_m = re.search(r"</table\b", html[search:], re.IGNORECASE)
            if close_m is None:
                break
            if open_m and open_m.start() < close_m.start():
                depth += 1
                search += open_m.end()
            else:
                depth -= 1
                search += close_m.end()
        segments.append((True, html[m.start() : search]))
        pos = search
    if pos < len(html):
        segments.append((False, html[pos:]))
    return segments


def extract_html_layout(html: str) -> list[LayoutRegion]:
    """Extract layout regions from iXBRL HTML using CSS heuristics."""
    raw: list[tuple[RegionType, str]] = []

    for is_table, chunk in _split_tables(html):
        if is_table:
            collector = _TableTextCollector()
            collector.feed(chunk)
            text = collector.text()
            if text:
                raw.append(("table", text))
        else:
            extractor = _HtmlLayoutExtractor()
            extractor.feed(chunk)
            raw.extend(extractor.regions())

    return [
        LayoutRegion(region_index=i, region_type=rtype, text=text)
        for i, (rtype, text) in enumerate(raw)
        if text.strip()
    ]


# ---------------------------------------------------------------------------
# PDF path (LayoutParser)
# ---------------------------------------------------------------------------


def _extract_pdf_layout(path: Path) -> list[LayoutRegion]:
    try:
        import layoutparser as lp  # type: ignore[import]
    except ImportError:
        log.warning("PDF layout detection requires layoutparser — pip install 'lantern[layout]'")
        return []

    try:
        import pdfplumber

        regions: list[LayoutRegion] = []
        idx = 0
        model = lp.Detectron2LayoutModel(
            "lp://PubLayNet/faster_rcnn_R_50_FPN_3x/config",
            extra_config=["MODEL.ROI_HEADS.SCORE_THRESH_TEST", 0.5],
            label_map={0: "Text", 1: "Title", 2: "List", 3: "Table", 4: "Figure"},
        )
        with pdfplumber.open(path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                img = page.to_image(resolution=150).original
                layout = model.detect(img)
                for block in layout:
                    label = block.type.lower()
                    region_type: RegionType = (
                        label
                        if label
                        in {  # type: ignore[assignment]
                            "title",
                            "text",
                            "table",
                            "figure",
                            "footnote",
                        }
                        else "unknown"
                    )
                    text = block.text or ""
                    regions.append(
                        LayoutRegion(
                            region_index=idx,
                            region_type=region_type,
                            text=text,
                            page_num=page_num,
                            method="layoutparser",
                            bbox=tuple(block.coordinates),  # type: ignore[arg-type]
                        )
                    )
                    idx += 1
        return regions
    except Exception as exc:
        log.warning("layoutparser failed on %s: %s", path.name, exc)
        return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_layout(path: Path) -> FilingLayout:
    """Extract layout regions from a filing (HTML/iXBRL or PDF)."""
    suffix = path.suffix.lower()
    if suffix in (".html", ".htm"):
        html = path.read_text(encoding="utf-8", errors="replace")
        regions = extract_html_layout(html)
    elif suffix == ".pdf":
        regions = _extract_pdf_layout(path)
    else:
        log.warning("Unknown suffix %s for %s, trying HTML layout", suffix, path.name)
        html = path.read_text(encoding="utf-8", errors="replace")
        regions = extract_html_layout(html)
    log.info("Detected %d layout region(s) in %s", len(regions), path.name)
    return FilingLayout(source=path, regions=regions)


def write_layout_jsonl(
    filing: FilingLayout,
    interim_dir: Path,
    ticker: str,
    form: str,
    accession: str,
) -> Path:
    """Write one JSON line per region to data/interim/<ticker>/<form>/<accession>_layout.jsonl."""
    out = interim_dir / ticker / form / f"{accession}_layout.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for r in filing.regions:
            f.write(
                json.dumps(
                    {
                        "region_index": r.region_index,
                        "region_type": r.region_type,
                        "text": r.text,
                        "page_num": r.page_num,
                        "method": r.method,
                    }
                )
                + "\n"
            )
    return out
