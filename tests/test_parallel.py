"""Parallel job-runner tests — written before implementation (Red-Green TDD).

The real ProcessPoolExecutor path is exercised with picklable builtins rather
than functions defined in this module: under the "spawn" start method (the
default on macOS) a child re-imports the module owning the worker, and the
`tests` package is not guaranteed to be importable from a fresh interpreter.
"""

from __future__ import annotations

import os
import pickle

import pytest

from lantern.parse.parallel import resolve_jobs, run_jobs

# ---------------------------------------------------------------------------
# resolve_jobs
# ---------------------------------------------------------------------------


def test_resolve_jobs_one_is_one() -> None:
    assert resolve_jobs(1) == 1


def test_resolve_jobs_zero_means_all_cores() -> None:
    assert resolve_jobs(0) == (os.cpu_count() or 1)


def test_resolve_jobs_explicit_count_preserved() -> None:
    assert resolve_jobs(4) == 4


def test_resolve_jobs_negative_clamps_to_one() -> None:
    assert resolve_jobs(-5) == 1


# ---------------------------------------------------------------------------
# run_jobs — serial path
# ---------------------------------------------------------------------------


def test_run_jobs_serial_preserves_order() -> None:
    assert run_jobs(str.upper, ["a", "b", "c"], jobs=1) == ["A", "B", "C"]


def test_run_jobs_serial_runs_in_process() -> None:
    """jobs=1 must not pickle, so a local closure is a valid worker."""
    seen: list[int] = []

    def worker(x: int) -> int:
        seen.append(x)
        return x * 2

    assert run_jobs(worker, [1, 2, 3], jobs=1) == [2, 4, 6]
    assert seen == [1, 2, 3]


def test_run_jobs_empty_input() -> None:
    assert run_jobs(str.upper, [], jobs=4) == []


def test_run_jobs_single_item_stays_in_process() -> None:
    """One item is not worth a pool, so a closure must still work."""

    def worker(x: str) -> str:
        return x + "!"

    assert run_jobs(worker, ["only"], jobs=8) == ["only!"]


# ---------------------------------------------------------------------------
# run_jobs — parallel path
# ---------------------------------------------------------------------------


def test_run_jobs_parallel_preserves_order() -> None:
    items = ["d", "c", "b", "a"]
    assert run_jobs(str.upper, items, jobs=2) == ["D", "C", "B", "A"]


def test_run_jobs_parallel_matches_serial_result() -> None:
    items = [str(i) for i in range(8)]
    assert run_jobs(str.upper, items, jobs=4) == run_jobs(str.upper, items, jobs=1)


def test_run_jobs_parallel_caps_workers_at_item_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """Asking for more workers than items must not spawn idle processes."""
    from lantern.parse import parallel as par

    recorded: dict[str, int] = {}
    real = par.ProcessPoolExecutor

    def spy(max_workers: int, **kw: object) -> object:
        recorded["max_workers"] = max_workers
        return real(max_workers=max_workers, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(par, "ProcessPoolExecutor", spy)
    run_jobs(str.upper, ["a", "b"], jobs=16)
    assert recorded["max_workers"] == 2


# ---------------------------------------------------------------------------
# Config must survive pickling to reach worker processes
# ---------------------------------------------------------------------------


def test_config_is_picklable() -> None:
    from lantern.config import load_config

    cfg = load_config()
    restored = pickle.loads(pickle.dumps(cfg))
    assert restored.paths.interim == cfg.paths.interim
    assert restored.text_extraction.ocr_char_threshold == cfg.text_extraction.ocr_char_threshold


# ---------------------------------------------------------------------------
# CLI wiring — every parse command takes --jobs
# ---------------------------------------------------------------------------


PARSE_COMMANDS = ["parse", "extract-tables", "detect-layout", "docling"]


@pytest.mark.parametrize("cmd", PARSE_COMMANDS)
def test_cli_command_has_jobs_option(cmd: str) -> None:
    from lantern.cli import main

    params = {p.name for p in main.commands[cmd].params}
    assert "jobs" in params


@pytest.mark.parametrize("cmd", PARSE_COMMANDS)
def test_cli_jobs_flag_is_accepted(cmd: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from click.testing import CliRunner

    from lantern import cli as cli_mod

    monkeypatch.setattr(cli_mod, "find_primary_documents", lambda _: [])
    result = CliRunner().invoke(cli_mod.main, [cmd, "--jobs", "4"])
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize("cmd", PARSE_COMMANDS)
def test_cli_jobs_short_flag_is_accepted(cmd: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from click.testing import CliRunner

    from lantern import cli as cli_mod

    monkeypatch.setattr(cli_mod, "find_primary_documents", lambda _: [])
    result = CliRunner().invoke(cli_mod.main, [cmd, "-j", "2"])
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize("cmd", PARSE_COMMANDS)
def test_cli_jobs_defaults_to_serial(cmd: str) -> None:
    from lantern.cli import main

    jobs = next(p for p in main.commands[cmd].params if p.name == "jobs")
    assert jobs.default == 1
