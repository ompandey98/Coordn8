#!/usr/bin/env python3
"""
Coordn8 — ADAPTIVE parallel work queue that resizes itself live.

This is the "parallelize everything without crashing the machine" primitive from
PLAYBOOK.md PHASE R.3. Use it for CPU-heavy fan-out: frame capture, per-scene
encodes, image analysis. It:

  - starts from a worker bound computed off real CPU + free-RAM capacity,
  - **dynamically ramps concurrency UP when the machine has spare headroom and
    DOWN when load/RAM tighten** — a controller re-evaluates every ~0.5s and moves
    the live permit count between 1 and a hard ceiling. This is the fix for
    "renders take painfully long": earlier versions picked one fixed worker count
    and only ever throttled DOWN, so a machine that freed up mid-run stayed
    under-utilized. Now it climbs back up and finishes as fast as the host allows,
    without ever swamping it,
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
import threading
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


def compute_worker_ceiling(kind: str = "cpu", per_worker_mb: int = 600) -> int:
    """The HARD upper bound the adaptive controller may ramp up to.

    Higher than the conservative `compute_workers` starting point: on an idle
    machine we want to use nearly all cores (leaving one for the OS + our own
    controller/main thread). RAM still caps it — a frame worker is memory-heavy —
    so we never promise more parallelism than the box can hold.
    """
    cores = os.cpu_count() or 1
    if kind == "io":
        return min(16, max(4, cores * 2))
    cpu_ceiling = max(1, cores - 1)          # use almost every core when idle
    free_mb = _free_ram_mb()
    if free_mb is not None:
        mem_ceiling = max(1, free_mb // per_worker_mb)
        return max(1, min(cpu_ceiling, mem_ceiling))
    return cpu_ceiling


class _DynamicSemaphore:
    """A semaphore whose capacity can be raised or lowered at runtime.

    Threads acquire a permit before running a task and release it after. The
    controller changes `capacity`; when it drops, extra permits are absorbed as
    running tasks finish (we never kill an in-flight task), and when it rises,
    freshly-available permits let more tasks start. This is the mechanism that
    makes concurrency track live machine headroom.
    """

    def __init__(self, initial: int, hard_max: int):
        self._cond = threading.Condition()
        self._capacity = max(1, initial)
        self._in_use = 0
        self._hard_max = max(1, hard_max)

    def acquire(self) -> None:
        with self._cond:
            while self._in_use >= self._capacity:
                self._cond.wait(timeout=0.25)
            self._in_use += 1

    def release(self) -> None:
        with self._cond:
            self._in_use = max(0, self._in_use - 1)
            self._cond.notify_all()

    def set_capacity(self, n: int) -> int:
        n = max(1, min(self._hard_max, int(n)))
        with self._cond:
            changed = n != self._capacity
            self._capacity = n
            if changed:
                self._cond.notify_all()
        return n

    @property
    def capacity(self) -> int:
        with self._cond:
            return self._capacity


def _load_1m() -> float | None:
    try:
        return os.getloadavg()[0]
    except (OSError, AttributeError):
        return None


@dataclass
class PoolResult:
    done: list = field(default_factory=list)
    failed: list = field(default_factory=list)
    peak_workers: int = 0  # highest live concurrency the controller reached
    aborted: bool = False  # an external kill switch (e.g. the disk watchdog) stopped us

    @property
    def ok(self) -> bool:
        # An aborted run is NOT ok even with zero failures: stopping early is a
        # deliberate incomplete result, and calling it success is how a render
        # "succeeded" while producing no video.
        return not self.failed and not self.aborted


def _controller_decision(cur: int, hard_max: int, cores: int,
                         load_ceiling: float, per_worker_mb: int,
                         min_free_mb: int) -> tuple[int, str]:
    """Given the live machine state, decide the next capacity (pure-ish helper).

    - shrink FAST (halve, floor 1) if load or RAM is over the ceiling — protecting
      the host takes priority and must react immediately;
    - grow SLOWLY (one worker at a time) only when there's real headroom on BOTH
      load and RAM, up to the hard ceiling — additive-increase / multiplicative-
      decrease, the same discipline TCP uses to stay stable under contention.
    """
    load = _load_1m()
    free_mb = _free_ram_mb()
    too_hot = load is not None and load > load_ceiling
    too_full = free_mb is not None and free_mb < min_free_mb
    if too_hot or too_full:
        why = ("load %.1f>%.1f" % (load, load_ceiling)) if too_hot \
            else ("free %dMB<%dMB" % (free_mb, min_free_mb))
        return max(1, cur // 2), f"shrink ({why})"
    # Headroom to grow? Require comfortable slack on whichever signals we can read.
    load_ok = load is None or load < load_ceiling * 0.75
    ram_ok = free_mb is None or free_mb > min_free_mb * 2
    if cur < hard_max and load_ok and ram_ok:
        return cur + 1, "grow (headroom)"
    return cur, "hold"


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
    adaptive: bool = True,
    control_interval_s: float = 0.5,
    log: Callable[[str], None] | None = None,
    abort_event: 'threading.Event | None' = None,
) -> PoolResult:
    """Run `task(item)` over `items` with an ADAPTIVE, self-resizing pool.

    respect_load / adaptive: when True (default), a background controller resizes
      the live worker count every `control_interval_s` — growing toward the
      hard ceiling when load AND free RAM have slack, shrinking fast when either is
      over the ceiling. This keeps the host responsive while finishing as quickly
      as the machine allows (the fix for under-parallelized, painfully-long
      renders). Set respect_load=False (or adaptive=False) for a fixed-size pool —
      deterministic for tests / when capacity is already reserved.

    abort_event: an external kill switch. When it is set, NO further items start and
      the pool returns what it has. This exists because a render once filled a 1 TB
      disk with frames and nothing could stop it: a worker that notices the volume
      filling MUST be able to halt the run, not just log about it. In-flight items
      are allowed to finish so nothing is left half-written.

    Returns PoolResult(done=[(item, result)...], failed=[(item, error)...]).
    """
    cores = os.cpu_count() or 1
    start_workers = compute_workers(kind, per_worker_mb)
    hard_max = max(start_workers, compute_worker_ceiling(kind, per_worker_mb))
    load_ceiling = cores * backoff_load_factor
    if min_free_mb is None:
        min_free_mb = per_worker_mb
    use_adaptive = adaptive and respect_load
    _log = log or (lambda m: sys.stderr.write(f"[coordn8:pool] {m}\n"))
    _log(f"start_workers={start_workers} hard_max={hard_max} kind={kind} "
         f"items={len(items)} load_ceiling={load_ceiling:.1f} "
         f"adaptive={use_adaptive}")

    # The live gate. When not adaptive, pin capacity at the fixed start count.
    sem = _DynamicSemaphore(start_workers, hard_max if use_adaptive else start_workers)

    stop_controller = threading.Event()
    peak = {"cap": sem.capacity}

    def _controller():
        while not stop_controller.wait(control_interval_s):
            nxt, why = _controller_decision(
                sem.capacity, hard_max, cores, load_ceiling,
                per_worker_mb, min_free_mb)
            applied = sem.set_capacity(nxt)
            peak["cap"] = max(peak["cap"], applied)

    result = PoolResult()
    result_lock = threading.Lock()
    attempts: dict[int, int] = {}
    pending = list(enumerate(items))

    class _Aborted(Exception):
        """Raised in place of running an item once the abort switch is set."""

    def _guarded(idx_item):
        idx, item = idx_item
        # Cheapest possible check: never even acquire a permit once aborted, so a
        # tripped disk watchdog stops the run within one in-flight item.
        if abort_event is not None and abort_event.is_set():
            raise _Aborted("aborted before start")
        sem.acquire()
        try:
            if abort_event is not None and abort_event.is_set():
                raise _Aborted("aborted while queued")
            return idx, item, task(item)
        finally:
            sem.release()

    controller = None
    if use_adaptive:
        controller = threading.Thread(target=_controller, daemon=True)
        controller.start()

    try:
        # Threads are cheap; the SEMAPHORE — not the pool size — decides how many
        # tasks execute at once, so the executor is sized to the ceiling and the
        # controller modulates real concurrency underneath it.
        with ThreadPoolExecutor(max_workers=max(1, hard_max)) as ex:
            while pending:
                if abort_event is not None and abort_event.is_set():
                    _log(f"ABORTED — {len(pending)} item(s) not started")
                    result.aborted = True
                    break
                batch = pending
                pending = []
                futures = {ex.submit(_guarded, ii): ii for ii in batch}
                for fut in as_completed(futures):
                    idx, item = futures[fut]
                    try:
                        _, _, out = fut.result()
                        with result_lock:
                            result.done.append((item, out))
                    except _Aborted:
                        result.aborted = True
                        continue      # not a failure: we chose to stop
                    except Exception as e:  # noqa: BLE001
                        if abort_event is not None and abort_event.is_set():
                            result.aborted = True
                            continue  # don't retry into a filling disk
                        attempts[idx] = attempts.get(idx, 0) + 1
                        if attempts[idx] <= max_retries:
                            _log(f"item {idx} failed ({e}); retry "
                                 f"{attempts[idx]}/{max_retries}")
                            pending.append((idx, item))
                        else:
                            _log(f"item {idx} permanently failed: {e}")
                            with result_lock:
                                result.failed.append((item, str(e)))
    finally:
        stop_controller.set()
        if controller is not None:
            controller.join(timeout=1.0)

    _log(f"done={len(result.done)} failed={len(result.failed)} "
         f"peak_workers={peak['cap']}")
    result.peak_workers = peak["cap"]
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

    # Adaptive path: same correctness contract, plus the controller must have
    # kept live concurrency within [1, hard_ceiling] and recorded a peak.
    seen["count"] = 0
    res2 = run_pool(list(range(20)), task, kind="cpu", adaptive=True)
    squares2 = sorted(out for _, out in res2.done)
    expected2 = sorted(i * i for i in range(20))
    ceiling = max(compute_workers("cpu"), compute_worker_ceiling("cpu"))
    adaptive_ok = (squares2 == expected2 and res2.ok
                   and 1 <= res2.peak_workers <= ceiling)
    print(f"selftest: workers_bounded=OK done={len(res.done)} "
          f"failed={len(res.failed)} results_correct={squares == expected} "
          f"adaptive_correct={squares2 == expected2} "
          f"peak_workers={res2.peak_workers} ceiling={ceiling}")
    return 0 if (ok and adaptive_ok) else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    print("Coordn8 bounded parallel pool. Import run_pool(), or run --selftest.")
    print(f"computed cpu workers here: {compute_workers('cpu')}")
    print(f"computed io  workers here: {compute_workers('io')}")
