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


if __name__ == "__main__":
    main(obj={})
    sys.exit(0)
