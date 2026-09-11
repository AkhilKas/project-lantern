"""Lantern CLI - thin click wrapper around the pipeline modules.

Run `lantern --help` to see commands. Each command corresponds to a
DVC stage so the same logic is invokable both manually and via `dvc repro`.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import click
from rich.logging import RichHandler

from lantern.config import LanternConfig, load_config
from lantern.ingest.edgar import download_filings, summarize
from lantern.parse.docling import extract_docling, write_docling_jsonl, write_docling_markdown
from lantern.parse.layout import extract_layout, write_layout_jsonl
from lantern.parse.parallel import run_jobs
from lantern.parse.tables import extract_tables, write_tables_jsonl
from lantern.parse.text import extract, find_primary_documents, write_interim

# One unit of per-filing work: (ticker, form, accession, path, config).
# Passed whole to a worker so the tuple stays picklable for ProcessPoolExecutor.
Job = tuple[str, str, str, Path, LanternConfig]


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
    )


def _jobs_option(fn: click.decorators.FC) -> click.decorators.FC:
    """Shared --jobs/-j option: 1 = serial (default), 0 = one worker per CPU."""
    return click.option(
        "--jobs",
        "-j",
        type=int,
        default=1,
        show_default=True,
        help="Parallel worker processes. 1 = serial, 0 = one per CPU core.",
    )(fn)


def _select_jobs(cfg: LanternConfig, ticker: str | None, form: str | None) -> list[Job]:
    """Find primary documents, apply --ticker/--form filters, pair with config."""
    docs = find_primary_documents(cfg.paths.raw)
    if ticker:
        docs = [(t, f, a, p) for t, f, a, p in docs if t.upper() == ticker.upper()]
    if form:
        docs = [(t, f, a, p) for t, f, a, p in docs if f.upper() == form.upper()]
    return [(t, f, a, p, cfg) for t, f, a, p in docs]


def _emit(header: str, lines: list[str]) -> None:
    click.echo(header)
    click.echo("\n".join(lines))
    click.echo(f"Total: {len(lines)} filing(s)")


def _counts(types: list[str]) -> str:
    counts: dict[str, int] = {}
    for t in types:
        counts[t] = counts.get(t, 0) + 1
    return " ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "empty"


# ---------------------------------------------------------------------------
# Per-filing workers. Module-level so ProcessPoolExecutor can pickle them.
# ---------------------------------------------------------------------------


def _parse_one(job: Job) -> str:
    t, f, accession, path, cfg = job
    filing = extract(path, cfg.text_extraction)
    out = write_interim(filing, cfg.paths.interim, t, f, accession)
    char_total = sum(p.char_count for p in filing.pages)
    return f"  {t:6s} {f:5s}  chars={char_total:>8,}  -> {out}"


def _tables_one(job: Job) -> str:
    t, f, accession, path, cfg = job
    filing = extract_tables(path, cfg.tables)
    out = write_tables_jsonl(filing, cfg.paths.interim, t, f, accession)
    return f"  {t:6s} {f:5s}  tables={len(filing.tables):>3}  -> {out}"


def _layout_one(job: Job) -> str:
    t, f, accession, path, cfg = job
    filing = extract_layout(path)
    out = write_layout_jsonl(filing, cfg.paths.interim, t, f, accession)
    summary = _counts([r.region_type for r in filing.regions])
    return f"  {t:6s} {f:5s}  {summary}  -> {out.name}"


def _docling_one(job: Job) -> str:
    t, f, accession, path, cfg = job
    document = extract_docling(path)
    jsonl_out = write_docling_jsonl(document, cfg.paths.interim, t, f, accession)
    md_out = write_docling_markdown(document, cfg.paths.interim, t, f, accession)
    summary = _counts([b.block_type for b in document.blocks])
    return f"  {t:6s} {f:5s}  {summary}  -> {jsonl_out.name}, {md_out.name}"


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
@_jobs_option
@click.pass_context
def parse_cmd(ctx: click.Context, ticker: str | None, form: str | None, jobs: int) -> None:
    """Extract text from downloaded filings into data/interim."""
    jobs_list = _select_jobs(ctx.obj["cfg"], ticker, form)
    if not jobs_list:
        click.echo("No primary documents found. Run `lantern download` first.")
        return
    _emit("Parsed filings:", run_jobs(_parse_one, jobs_list, jobs))


@main.command("extract-tables")
@click.option("--ticker", default=None, help="Only process filings for this ticker.")
@click.option("--form", default=None, help="Only process this form type (10-K or 10-Q).")
@_jobs_option
@click.pass_context
def extract_tables_cmd(ctx: click.Context, ticker: str | None, form: str | None, jobs: int) -> None:
    """Extract tables from downloaded filings into data/interim as JSONL."""
    jobs_list = _select_jobs(ctx.obj["cfg"], ticker, form)
    if not jobs_list:
        click.echo("No primary documents found. Run `lantern download` first.")
        return
    _emit("Extracted tables:", run_jobs(_tables_one, jobs_list, jobs))


@main.command("detect-layout")
@click.option("--ticker", default=None, help="Only process filings for this ticker.")
@click.option("--form", default=None, help="Only process this form type (10-K or 10-Q).")
@_jobs_option
@click.pass_context
def detect_layout_cmd(ctx: click.Context, ticker: str | None, form: str | None, jobs: int) -> None:
    """Detect layout regions in downloaded filings and write to data/interim as JSONL."""
    jobs_list = _select_jobs(ctx.obj["cfg"], ticker, form)
    if not jobs_list:
        click.echo("No primary documents found. Run `lantern download` first.")
        return
    _emit("Layout detection results:", run_jobs(_layout_one, jobs_list, jobs))


@main.command("docling")
@click.option("--ticker", default=None, help="Only process filings for this ticker.")
@click.option("--form", default=None, help="Only process this form type (10-K or 10-Q).")
@_jobs_option
@click.pass_context
def docling_cmd(ctx: click.Context, ticker: str | None, form: str | None, jobs: int) -> None:
    """Run the Docling unified pipeline on downloaded filings.

    Emits per-filing JSONL (block-level) and Markdown to data/interim.
    """
    jobs_list = _select_jobs(ctx.obj["cfg"], ticker, form)
    if not jobs_list:
        click.echo("No primary documents found. Run `lantern download` first.")
        return
    _emit("Docling pipeline results:", run_jobs(_docling_one, jobs_list, jobs))


if __name__ == "__main__":
    main(obj={})
    sys.exit(0)
