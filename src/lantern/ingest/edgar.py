"""Download SEC filings and their XBRL attachments via sec-edgar-downloader.

The SEC's EDGAR system requires a descriptive User-Agent string identifying
the requester so they can throttle abusive crawlers. We construct that from
the user_agent_name and user_agent_email values in params.yaml.

References:
    https://sec-edgar-downloader.readthedocs.io/en/latest/
    https://www.sec.gov/os/accessing-edgar-data
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from sec_edgar_downloader import Downloader

from lantern.config import EdgarConfig, LanternConfig

log = logging.getLogger(__name__)


@dataclass
class DownloadResult:
    ticker: str
    form: str
    downloaded: int
    target_dir: Path


def _make_downloader(cfg: EdgarConfig, target_dir: Path) -> Downloader:
    """Build a Downloader pointing at target_dir, with a polite User-Agent."""
    target_dir.mkdir(parents=True, exist_ok=True)
    # The Downloader signature is (company_name, email_address, download_folder).
    return Downloader(cfg.user_agent_name, cfg.user_agent_email, str(target_dir))


def download_filings(cfg: LanternConfig) -> list[DownloadResult]:
    """Download each (company, form) pair as configured.

    sec-edgar-downloader lays files out under:
        <target>/sec-edgar-filings/<TICKER>/<FORM>/<ACCESSION>/...

    Each filing folder contains the primary document plus, when requested,
    the XBRL instance and supporting files.
    """
    results: list[DownloadResult] = []
    dl = _make_downloader(cfg.edgar, cfg.paths.raw)

    for company in cfg.edgar.companies:
        for spec in cfg.edgar.filings:
            log.info("Downloading %s for %s (limit=%d)", spec.form, company.ticker, spec.limit)
            count = dl.get(
                spec.form,
                company.ticker,
                limit=spec.limit,
                download_details=cfg.edgar.include_xbrl,
            )
            results.append(
                DownloadResult(
                    ticker=company.ticker,
                    form=spec.form,
                    downloaded=count,
                    target_dir=cfg.paths.raw / "sec-edgar-filings" / company.ticker / spec.form,
                )
            )
            log.info("  -> %d filing(s) saved", count)

    return results


def summarize(results: list[DownloadResult]) -> str:
    """Human-readable summary, useful for CLI output and CI smoke tests."""
    lines = ["Downloaded filings:"]
    total = 0
    for r in results:
        lines.append(f"  {r.ticker:6s} {r.form:5s}  count={r.downloaded}  -> {r.target_dir}")
        total += r.downloaded
    lines.append(f"Total filings: {total}")
    return "\n".join(lines)
