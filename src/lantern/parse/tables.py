"""E3: Table extraction from SEC filings (iXBRL HTML and PDF via Camelot).

HTML path: HTMLParser state machine that extracts <table> elements, skipping
display:none sections and XBRL metadata tags. Each table is returned as a
list of rows (list of cell strings).

PDF path: Camelot with lattice or stream flavor, chosen by whether the page
has enough ruling lines. Requires the [tables] extra.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal

from lantern.config import TablesConfig

log = logging.getLogger(__name__)

Method = Literal["html", "camelot-lattice", "camelot-stream"]

_XBRL_NS_SKIP: frozenset[str] = frozenset({"xbrli", "xbrldi", "link", "xlink"})
_FULL_SKIP: frozenset[str] = frozenset({"script", "style", "head", "ix:header", "ix:hidden"})


@dataclass
class ExtractedTable:
    table_index: int
    rows: list[list[str]]
    method: Method
    page_num: int = 1


@dataclass
class FilingTables:
    source: Path
    tables: list[ExtractedTable]


# ---------------------------------------------------------------------------
# HTML table extractor
# ---------------------------------------------------------------------------


class _HtmlTableExtractor(HTMLParser):
    """Walk iXBRL HTML and collect all visible <table> elements."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        # hidden / skip state
        self._skip: int = 0
        self._div_depth: int = 0
        self._hide_at: int = -1
        # table nesting stack — each entry is list[list[str]] (rows of current table)
        self._table_stack: list[list[list[str]]] = []
        # current row and cell buffers
        self._row: list[str] | None = None
        self._cell_buf: list[str] | None = None
        # completed tables
        self._done: list[list[list[str]]] = []

    @staticmethod
    def _is_display_none(attrs: list[tuple[str, str | None]]) -> bool:
        for name, val in attrs:
            if name == "style" and val and re.search(r"display\s*:\s*none", val, re.IGNORECASE):
                return True
        return False

    @staticmethod
    def _ns(tag: str) -> str:
        return tag.split(":")[0] if ":" in tag else ""

    def _hidden(self) -> bool:
        return self._skip > 0 or self._hide_at != -1

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
        if self._hide_at != -1:
            return

        if tag == "table":
            self._table_stack.append([])
        elif tag == "tr" and self._table_stack:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell_buf = []

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
            return
        if self._hidden():
            return

        if tag in ("td", "th") and self._cell_buf is not None:
            text = " ".join(self._cell_buf).strip()
            if self._row is not None:
                self._row.append(text)
            self._cell_buf = None
        elif tag == "tr" and self._row is not None and self._table_stack:
            self._table_stack[-1].append(self._row)
            self._row = None
        elif tag == "table" and self._table_stack:
            finished = self._table_stack.pop()
            self._done.append(finished)

    def handle_data(self, data: str) -> None:
        if self._hidden():
            return
        if self._cell_buf is not None:
            stripped = data.strip()
            if stripped:
                self._cell_buf.append(stripped)

    def tables(self) -> list[list[list[str]]]:
        return self._done


def extract_html_tables(html: str) -> list[ExtractedTable]:
    """Extract all non-empty visible tables from an HTML/iXBRL string."""
    extractor = _HtmlTableExtractor()
    extractor.feed(html)
    result: list[ExtractedTable] = []
    idx = 0
    for raw in extractor.tables():
        # Skip layout tables — all cells empty
        if not any(cell for row in raw for cell in row if cell):
            continue
        result.append(ExtractedTable(table_index=idx, rows=raw, method="html"))
        idx += 1
    return result


# ---------------------------------------------------------------------------
# PDF extractor (Camelot)
# ---------------------------------------------------------------------------


def _camelot_flavor(page_index: int, cfg: TablesConfig) -> str:
    """Choose lattice vs stream based on config (PDF-only, not yet auto-detected)."""
    if "lattice" in cfg.camelot_flavors:
        return "lattice"
    return "stream"


def _extract_pdf_tables(path: Path, cfg: TablesConfig) -> list[ExtractedTable]:
    try:
        import camelot  # type: ignore[import]
    except ImportError:
        log.warning("PDF table extraction requires camelot — pip install 'lantern[tables]'")
        return []

    results: list[ExtractedTable] = []
    idx = 0
    for flavor in cfg.camelot_flavors:
        try:
            tables = camelot.read_pdf(str(path), flavor=flavor, pages="all")
        except Exception as exc:
            log.warning("camelot %s failed on %s: %s", flavor, path.name, exc)
            continue
        for t in tables:
            rows = t.df.values.tolist()
            results.append(
                ExtractedTable(
                    table_index=idx,
                    rows=[[str(c).strip() for c in row] for row in rows],
                    method=f"camelot-{flavor}",  # type: ignore[arg-type]
                    page_num=t.page,
                )
            )
            idx += 1
    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_tables(path: Path, cfg: TablesConfig) -> FilingTables:
    """Extract tables from a filing (HTML/iXBRL or PDF)."""
    suffix = path.suffix.lower()
    if suffix in (".html", ".htm"):
        html = path.read_text(encoding="utf-8", errors="replace")
        tables = extract_html_tables(html)
    elif suffix == ".pdf":
        tables = _extract_pdf_tables(path, cfg)
    else:
        log.warning("Unknown suffix %s for %s, trying HTML table extraction", suffix, path.name)
        html = path.read_text(encoding="utf-8", errors="replace")
        tables = extract_html_tables(html)
    log.info("Extracted %d table(s) from %s", len(tables), path.name)
    return FilingTables(source=path, tables=tables)


def write_tables_jsonl(
    filing: FilingTables,
    interim_dir: Path,
    ticker: str,
    form: str,
    accession: str,
) -> Path:
    """Write one JSON line per table to data/interim/<ticker>/<form>/<accession>_tables.jsonl."""
    out = interim_dir / ticker / form / f"{accession}_tables.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for table in filing.tables:
            f.write(
                json.dumps(
                    {
                        "table_index": table.table_index,
                        "method": table.method,
                        "page_num": table.page_num,
                        "rows": table.rows,
                    }
                )
                + "\n"
            )
    return out
