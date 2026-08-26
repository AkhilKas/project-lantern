"""E5: Docling unified conversion pipeline.

Docling (IBM Research) is an integrated document conversion library that
produces layout-aware structured output (text, headings, tables, lists,
figures) in a single pass from HTML or PDF. It serves as a "buy-adjacent"
counterpart to the hand-rolled E2 (text) + E3 (tables) + E4 (layout) stack —
one pipeline emitting comparable JSONL blocks plus native Markdown, so E7
(storage-format comparison) and E10 (evaluation) can diff pipelines head-to-
head.

Docling is heavy (~500MB with models) and lives behind the [docling] extra.
When the package isn't installed, `extract_docling` logs a warning and
returns an empty document — the same graceful-fallback pattern used for
`layoutparser` in E4.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

BlockType = Literal["title", "text", "table", "list", "figure", "footnote", "unknown"]

_LABEL_MAP: dict[str, BlockType] = {
    "section_header": "title",
    "title": "title",
    "page_header": "title",
    "text": "text",
    "paragraph": "text",
    "caption": "text",
    "table": "table",
    "list_item": "list",
    "picture": "figure",
    "footnote": "footnote",
    "page_footer": "footnote",
}


@dataclass
class DoclingBlock:
    block_index: int
    block_type: BlockType
    text: str
    page_num: int = 1
    method: Literal["docling"] = "docling"


@dataclass
class DoclingDocument:
    source: Path
    blocks: list[DoclingBlock] = field(default_factory=list)
    markdown: str = ""


def _map_label(label: str) -> BlockType:
    return _LABEL_MAP.get(label.lower(), "unknown")


# ---------------------------------------------------------------------------
# Docling conversion
# ---------------------------------------------------------------------------


def _table_text(item: object) -> str:
    """Join table cell text into a single string for a table block.

    Docling table items expose cells via `.data.table_cells` (each cell has
    `.text`). Fall back to `.text` if the structured API isn't available.
    """
    data = getattr(item, "data", None)
    cells = getattr(data, "table_cells", None) if data is not None else None
    if cells:
        parts = [getattr(c, "text", "") or "" for c in cells]
        joined = " ".join(p.strip() for p in parts if p and p.strip())
        if joined:
            return joined
    return (getattr(item, "text", "") or "").strip()


def _page_num(item: object) -> int:
    prov = getattr(item, "prov", None)
    if prov:
        first = prov[0]
        return int(getattr(first, "page_no", 1) or 1)
    return 1


def _iter_blocks(doc: object) -> list[DoclingBlock]:
    """Walk a docling document and produce DoclingBlocks in document order."""
    blocks: list[DoclingBlock] = []
    idx = 0
    try:
        items = doc.iterate_items()  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover - defensive
        return blocks

    for entry in items:
        item = entry[0] if isinstance(entry, tuple) else entry
        raw_label = getattr(item, "label", "") or ""
        label = str(raw_label).split(".")[-1]  # DocItemLabel.TEXT -> "TEXT"
        block_type = _map_label(label)
        text = _table_text(item) if block_type == "table" else (getattr(item, "text", "") or "")
        text = text.strip()
        if not text:
            continue
        blocks.append(
            DoclingBlock(
                block_index=idx,
                block_type=block_type,
                text=text,
                page_num=_page_num(item),
            )
        )
        idx += 1
    return blocks


def _convert_via_docling(path: Path) -> tuple[str, list[DoclingBlock]]:
    try:
        from docling.document_converter import DocumentConverter  # type: ignore[import]
    except ImportError:
        log.warning("Docling extraction requires docling — pip install 'lantern[docling]'")
        return "", []

    try:
        converter = DocumentConverter()
        result = converter.convert(source=path)
        doc = result.document
        markdown = doc.export_to_markdown() or ""
        blocks = _iter_blocks(doc)
        return markdown, blocks
    except Exception as exc:
        log.warning("docling failed on %s: %s", path.name, exc)
        return "", []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_docling(path: Path) -> DoclingDocument:
    """Convert a filing (HTML or PDF) via Docling into a unified document."""
    markdown, blocks = _convert_via_docling(path)
    log.info("Docling produced %d block(s) for %s", len(blocks), path.name)
    return DoclingDocument(source=path, blocks=blocks, markdown=markdown)


def write_docling_jsonl(
    document: DoclingDocument,
    interim_dir: Path,
    ticker: str,
    form: str,
    accession: str,
) -> Path:
    """Write one JSON line per block to data/interim/<ticker>/<form>/<accession>_docling.jsonl."""
    out = interim_dir / ticker / form / f"{accession}_docling.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for b in document.blocks:
            f.write(
                json.dumps(
                    {
                        "block_index": b.block_index,
                        "block_type": b.block_type,
                        "text": b.text,
                        "page_num": b.page_num,
                        "method": b.method,
                    }
                )
                + "\n"
            )
    return out


def write_docling_markdown(
    document: DoclingDocument,
    interim_dir: Path,
    ticker: str,
    form: str,
    accession: str,
) -> Path:
    """Write the Docling native Markdown to data/interim/<ticker>/<form>/<accession>_docling.md."""
    out = interim_dir / ticker / form / f"{accession}_docling.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(document.markdown, encoding="utf-8")
    return out
