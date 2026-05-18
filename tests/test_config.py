"""Sanity tests for the config loader. These run in CI without any heavy deps."""

from __future__ import annotations

from pathlib import Path

from lantern.config import load_config


def test_default_params_loads() -> None:
    """params.yaml should parse cleanly and produce a valid config."""
    cfg = load_config()
    assert len(cfg.edgar.companies) >= 1
    assert all(c.ticker.isupper() for c in cfg.edgar.companies)
    assert cfg.edgar.user_agent_email  # non-empty
    assert all(spec.form in {"10-K", "10-Q"} for spec in cfg.edgar.filings)


def test_paths_are_absolute() -> None:
    """Path config should be resolved to absolute paths after loading."""
    cfg = load_config()
    for p in [cfg.paths.raw, cfg.paths.interim, cfg.paths.parsed, cfg.paths.ground_truth]:
        assert isinstance(p, Path)
        assert p.is_absolute(), f"{p} should be absolute"


def test_thresholds_have_sensible_defaults() -> None:
    cfg = load_config()
    assert cfg.text_extraction.ocr_char_threshold > 0
    assert cfg.text_extraction.ocr_dpi >= 150
    assert set(cfg.tables.camelot_flavors) <= {"lattice", "stream"}
