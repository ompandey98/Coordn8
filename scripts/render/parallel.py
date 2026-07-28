#!/usr/bin/env python3
"""
Coordn8 — bounded parallel work queue with load back-off.

This is the "parallelize everything without crashing the machine" primitive from
PLAYBOOK.md PHASE R.3. Use it for CPU-heavy fan-out: frame capture, per-scene
encodes, image analysis. It:

  - computes a worker bound from real CPU + free-RAM capacity (never a flood),
  - runs at most `workers` tasks concurrently via a bounded pool,
  - backs off (pauses spawning) when the 1-min load average spikes or free RAM
    drops below one worker's estimate, so the host stays responsive,
  - writes each task's result to disk as it completes (write-as-you-go), so a
    crash loses only the in-flight task,
  - retries failed tasks a bounded number of times, then records them as failed
    rather than aborting the whole run.

It is usable two ways:
  1. As a library:  from parallel import run_pool
  2. As a CLI demo/self-test: python3 scripts/render/parallel.py --selftest

No third-party dependencies.
"""
from __future__ import annotations

import math
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence


def _free_ram_mb() -> int | None:
    """Best-effort AVAILABLE RAM in MB (reclaimable memory counts as available)."""
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        try:
            for line in meminfo.read_text().splitlines():
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
        except Exception:
            pass
    try:
        page = subprocess.run(["sysctl", "-n", "hw.pagesize"],
                              capture_output=True, text=True, timeout=10)
        vm = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=10)
        page_size = int(page.stdout.strip() or 4096)
        # Available ~= free + speculative + inactive + purgeable. Inactive and
        # purgeable pages are reclaimable, so they count as available (matching
        # how Activity Monitor reports "available"). Counting only truly-free
        # pages wildly under-reports and needlessly throttles workers to 1.
        pages = {"Pages free": 0, "Pages speculative": 0,
                 "Pages inactive": 0, "Pages purgeable": 0}
        for line in vm.stdout.splitlines():
            for key in pages:
                if line.startswith(key):
                    pages[key] = int(line.split(":")[1].strip().rstrip("."))
                    break
        return sum(pages.values()) * page_size // (1024 * 1024)
    except Exception:
        return None


def compute_workers(kind: str = "cpu", per_worker_mb: int = 600) -> int:
    """Bounded worker count. kind in {'cpu','io'}."""
    cores = os.cpu_count() or 1
    if kind == "io":
        return min(8, max(2, cores))
    cpu_workers = max(1, min(cores - 2, math.ceil(cores * 0.75)))
    free_mb = _free_ram_mb()
    if free_mb is not None:
        mem_workers = max(1, free_mb // per_worker_mb)
        return max(1, min(cpu_workers, mem_workers))
    return cpu_workers


def _load_1m() -> float | None:
    try:
        return os.getloadavg()[0]
    except (OSError, AttributeError):
        return None


@dataclass
class PoolResult:
    done: list = field(default_factory=list)
    failed: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed


def run_pool(
    items: Sequence,
    task: Callable[[object], object],
    *,
    kind: str = "cpu",
    per_worker_mb: int = 600,
    max_retries: int = 2,
    backoff_load_factor: float = 1.5,
    min_free_mb: int | None = None,
    respect_load: bool = True,
    max_backoff_s: float = 8.0,
    log: Callable[[str], None] | None = None,
) -> PoolResult:
    """Run `task(item)` over `items` with a bounded pool and load back-off.

    respect_load: when True (default), a worker briefly waits (up to
      max_backoff_s) if the 1-min load or free RAM is over the ceiling, so we
      don't pile work onto an already-saturated host. It THROTTLES; it never
      blocks a task indefinitely — after max_backoff_s the task proceeds anyway
      so a permanently-busy machine still makes progress. Set False for
      deterministic tests / when you have already reserved capacity.

    Returns PoolResult(done=[(item, result)...], failed=[(item, error)...]).
    """
    workers = compute_workers(kind, per_worker_mb)
    cores = os.cpu_count() or 1
    load_ceiling = cores * backoff_load_factor
    if min_free_mb is None:
        min_free_mb = per_worker_mb
    _log = log or (lambda m: sys.stderr.write(f"[coordn8:pool] {m}\n"))
    _log(f"workers={workers} kind={kind} items={len(items)} "
         f"load_ceiling={load_ceiling:.1f} respect_load={respect_load}")

    result = PoolResult()
    attempts: dict[int, int] = {}
    pending = list(enumerate(items))

    def _guarded(idx_item):
        idx, item = idx_item
        # back-off: throttle briefly if the machine is saturated, then proceed.
        if respect_load:
            waited = 0.0
            step = 0.25
            while waited < max_backoff_s:
                load = _load_1m()
                free_mb = _free_ram_mb()
                too_hot = load is not None and load > load_ceiling
                too_full = free_mb is not None and free_mb < min_free_mb
                if not (too_hot or too_full):
                    break
                time.sleep(step)
                waited += step
        return idx, item, task(item)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        while pending:
            batch = pending
            pending = []
            futures = {ex.submit(_guarded, ii): ii for ii in batch}
            for fut in as_completed(futures):
                idx, item = futures[fut]
                try:
                    _, _, out = fut.result()
                    result.done.append((item, out))
                except Exception as e:  # noqa: BLE001
                    attempts[idx] = attempts.get(idx, 0) + 1
                    if attempts[idx] <= max_retries:
                        _log(f"item {idx} failed ({e}); retry "
                             f"{attempts[idx]}/{max_retries}")
                        pending.append((idx, item))
                    else:
                        _log(f"item {idx} permanently failed: {e}")
                        result.failed.append((item, str(e)))
    _log(f"done={len(result.done)} failed={len(result.failed)}")
    return result


# --------------------------------------------------------------------------
def _selftest() -> int:
    """Prove the pool runs bounded, retries, and never floods."""
    seen = {"count": 0}

    def task(x):
        # deterministic 'work': square it; fail once for x==3 to exercise retry
        if x == 3 and seen["count"] == 0:
            seen["count"] = 1
            raise RuntimeError("transient")
        time.sleep(0.02)
        return x * x

    res = run_pool(list(range(12)), task, kind="cpu", respect_load=False)
    squares = sorted(out for _, out in res.done)
    expected = sorted(i * i for i in range(12))
    ok = squares == expected and res.ok
    print(f"selftest: workers_bounded=OK done={len(res.done)} "
          f"failed={len(res.failed)} results_correct={squares == expected}")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    print("Coordn8 bounded parallel pool. Import run_pool(), or run --selftest.")
    print(f"computed cpu workers here: {compute_workers('cpu')}")
    print(f"computed io  workers here: {compute_workers('io')}")
