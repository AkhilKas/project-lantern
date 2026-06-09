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


if __name__ == "__main__":
    main(obj={})
    sys.exit(0)
