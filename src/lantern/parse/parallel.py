"""Shared helper for running per-filing work serially or across processes.

Every parse stage (E2 text, E3 tables, E4 layout, E5 docling) is a serial loop
over independent filings, and each one is CPU-bound single-threaded — the E5
corpus run measured 97% CPU, i.e. exactly one core busy. Filings share no
state, so the loop parallelises cleanly at the process level.

Threads would not help: the work is Python-level HTML parsing holding the GIL,
not I/O. Note that Docling's own `num_threads` / `AcceleratorOptions` only
apply to its ML model inference on the PDF path; on iXBRL HTML it runs
`SimplePipeline`, which loads no models.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")


def resolve_jobs(jobs: int) -> int:
    """Map a --jobs value to a concrete worker count.

    0 means "one worker per CPU". Anything below 1 is treated as serial.
    """
    if jobs == 0:
        return os.cpu_count() or 1
    return max(1, jobs)


def run_jobs(
    worker: Callable[[T], R],
    items: Sequence[T],
    jobs: int,
) -> list[R]:
    """Apply `worker` to every item, preserving input order.

    A resolved count of 1 (or an input of 0-1 items) runs in-process: no
    pickling, readable tracebacks, and behaviour identical to the pre-parallel
    pipeline. Above that the work fans out across processes, so `worker` must
    be a module-level function and both its argument and return value must be
    picklable.
    """
    worker_count = resolve_jobs(jobs)
    if worker_count == 1 or len(items) <= 1:
        return [worker(item) for item in items]

    with ProcessPoolExecutor(max_workers=min(worker_count, len(items))) as pool:
        return list(pool.map(worker, items))
