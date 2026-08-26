"""Lantern CLI - thin click wrapper around the pipeline modules.

Run `lantern --help` to see commands. Each command corresponds to a
DVC stage so the same logic is invokable both manually and via `dvc repro`.
"""

from __future__ import annotations

import logging
import sys

import click
from rich.logging import RichHandler

from lantern.config import load_config
from lantern.ingest.edgar import download_filings, summarize
from lantern.parse.docling import extract_docling, write_docling_jsonl, write_docling_markdown
from lantern.parse.layout import extract_layout, write_layout_jsonl
from lantern.parse.tables import extract_tables, write_tables_jsonl
from lantern.parse.text import extract, find_primary_documents, write_interim


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
    )


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging.")
@click.pass_context
def main(ctx: click.Context, verbose: bool) -> None:
    """Project LANTERN: SEC filing ingestion and parsing pipeline."""
    _setup_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["cfg"] = load_config()


@main.command("download")
@click.pass_context
def download_cmd(ctx: click.Context) -> None:
    """Download filings and XBRL attachments listed in params.yaml."""
    cfg = ctx.obj["cfg"]
    results = download_filings(cfg)
    click.echo(summarize(results))


@main.command("info")
@click.pass_context
def info_cmd(ctx: click.Context) -> None:
    """Print the active configuration (debug aid)."""
    cfg = ctx.obj["cfg"]
    click.echo(cfg.model_dump_json(indent=2))


@main.command("parse")
@click.option("--ticker", default=None, help="Only parse filings for this ticker.")
@click.option("--form", default=None, help="Only parse this form type (10-K or 10-Q).")
@click.pass_context
def parse_cmd(ctx: click.Context, ticker: str | None, form: str | None) -> None:
    """Extract text from downloaded filings into data/interim."""
    cfg = ctx.obj["cfg"]
    docs = find_primary_documents(cfg.paths.raw)
    if ticker:
        docs = [(t, f, a, p) for t, f, a, p in docs if t.upper() == ticker.upper()]
    if form:
        docs = [(t, f, a, p) for t, f, a, p in docs if f.upper() == form.upper()]

    if not docs:
        click.echo("No primary documents found. Run `lantern download` first.")
        return

    written: list[str] = []
    for t, f, accession, path in docs:
        filing = extract(path, cfg.text_extraction)
        out = write_interim(filing, cfg.paths.interim, t, f, accession)
        char_total = sum(p.char_count for p in filing.pages)
        written.append(f"  {t:6s} {f:5s}  chars={char_total:>8,}  -> {out}")

    click.echo("Parsed filings:")
    click.echo("\n".join(written))
    click.echo(f"Total: {len(written)} filing(s)")


@main.command("extract-tables")
@click.option("--ticker", default=None, help="Only process filings for this ticker.")
@click.option("--form", default=None, help="Only process this form type (10-K or 10-Q).")
@click.pass_context
def extract_tables_cmd(ctx: click.Context, ticker: str | None, form: str | None) -> None:
    """Extract tables from downloaded filings into data/interim as JSONL."""
    cfg = ctx.obj["cfg"]
    docs = find_primary_documents(cfg.paths.raw)
    if ticker:
        docs = [(t, f, a, p) for t, f, a, p in docs if t.upper() == ticker.upper()]
    if form:
        docs = [(t, f, a, p) for t, f, a, p in docs if f.upper() == form.upper()]

    if not docs:
        click.echo("No primary documents found. Run `lantern download` first.")
        return

    written: list[str] = []
    for t, f, accession, path in docs:
        filing = extract_tables(path, cfg.tables)
        out = write_tables_jsonl(filing, cfg.paths.interim, t, f, accession)
        written.append(f"  {t:6s} {f:5s}  tables={len(filing.tables):>3}  -> {out}")

    click.echo("Extracted tables:")
    click.echo("\n".join(written))
    click.echo(f"Total: {len(written)} filing(s)")


@main.command("detect-layout")
@click.option("--ticker", default=None, help="Only process filings for this ticker.")
@click.option("--form", default=None, help="Only process this form type (10-K or 10-Q).")
@click.pass_context
def detect_layout_cmd(ctx: click.Context, ticker: str | None, form: str | None) -> None:
    """Detect layout regions in downloaded filings and write to data/interim as JSONL."""
    cfg = ctx.obj["cfg"]
    docs = find_primary_documents(cfg.paths.raw)
    if ticker:
        docs = [(t, f, a, p) for t, f, a, p in docs if t.upper() == ticker.upper()]
    if form:
        docs = [(t, f, a, p) for t, f, a, p in docs if f.upper() == form.upper()]

    if not docs:
        click.echo("No primary documents found. Run `lantern download` first.")
        return

    written: list[str] = []
    for t, f, accession, path in docs:
        filing = extract_layout(path)
        out = write_layout_jsonl(filing, cfg.paths.interim, t, f, accession)
        counts = {}
        for r in filing.regions:
            counts[r.region_type] = counts.get(r.region_type, 0) + 1
        summary = " ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        written.append(f"  {t:6s} {f:5s}  {summary}  -> {out.name}")

    click.echo("Layout detection results:")
    click.echo("\n".join(written))
    click.echo(f"Total: {len(written)} filing(s)")


@main.command("docling")
@click.option("--ticker", default=None, help="Only process filings for this ticker.")
@click.option("--form", default=None, help="Only process this form type (10-K or 10-Q).")
@click.pass_context
def docling_cmd(ctx: click.Context, ticker: str | None, form: str | None) -> None:
    """Run the Docling unified pipeline on downloaded filings.

    Emits per-filing JSONL (block-level) and Markdown to data/interim.
    """
    cfg = ctx.obj["cfg"]
    docs = find_primary_documents(cfg.paths.raw)
    if ticker:
        docs = [(t, f, a, p) for t, f, a, p in docs if t.upper() == ticker.upper()]
    if form:
        docs = [(t, f, a, p) for t, f, a, p in docs if f.upper() == form.upper()]

    if not docs:
        click.echo("No primary documents found. Run `lantern download` first.")
        return

    written: list[str] = []
    for t, f, accession, path in docs:
        document = extract_docling(path)
        jsonl_out = write_docling_jsonl(document, cfg.paths.interim, t, f, accession)
        md_out = write_docling_markdown(document, cfg.paths.interim, t, f, accession)
        counts: dict[str, int] = {}
        for b in document.blocks:
            counts[b.block_type] = counts.get(b.block_type, 0) + 1
        summary = " ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "empty"
        written.append(f"  {t:6s} {f:5s}  {summary}  -> {jsonl_out.name}, {md_out.name}")

    click.echo("Docling pipeline results:")
    click.echo("\n".join(written))
    click.echo(f"Total: {len(written)} filing(s)")


if __name__ == "__main__":
    main(obj={})
    sys.exit(0)
