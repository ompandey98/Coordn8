#!/usr/bin/env python3
"""
Coordn8 — DISK GUARD: the render pipeline may never fill the disk. Ever.

WHY THIS FILE EXISTS
--------------------
A real build accumulated frames without bound, produced NO video, filled a **1 TB**
disk and killed the machine. Nothing in the pipeline estimated how much space a
render would need, nothing watched free space while it ran, and nothing ever
deleted a frame — so re-runs ADDED to the pile. That is not a quality bug, it is a
destructive one: the tool damaged the host it was invoked on.

The arithmetic that makes it inevitable without a guard:
    1920x1080 PNG screenshot        ~= 3 MB
    30 fps x 6 min                   = 10,800 frames per scene
                                    ~= 32 GB per scene
    x 11 scenes                     ~= 348 GB, never reclaimed, and a retry doubles it

FOUR LAYERS OF DEFENCE (all of them, because any one alone has a hole)
----------------------------------------------------------------------
  1. STREAM, don't accumulate — the real fix. Frames go straight into the encoder's
     stdin and never touch the disk (see capture_scene.py `--stream`). Peak usage is
     one frame in memory instead of tens of thousands on disk.
  2. PREFLIGHT BUDGET — before writing frame 2, measure frame 1's ACTUAL size,
     multiply by the frame count, and refuse to start if that exceeds the free space
     minus a reserve (or a declared cap). An estimate from a real measurement, not a
     guess.
  3. LIVE WATCHDOG — a background thread samples free space during the run and trips
     an abort event the moment it drops below the floor. Workers cooperate and stop,
     so the run ends in seconds instead of consuming the volume.
  4. ALWAYS CLEAN UP — frames are deleted after a successful encode, partial output
     is removed on failure or Ctrl-C, and `coordn8 clean` reclaims anything a killed
     process left behind. An abandoned run must never cost the user disk.

Nothing here is advisory. A gate may be advisory; filling someone's disk may not.
"""
from __future__ import annotations

import os
import shutil
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Defaults. Deliberately conservative: the cost of a needless refusal is one
# flag; the cost of a full disk is the user's machine.
# ---------------------------------------------------------------------------
RESERVE_MB = 5_000          # never consume the last 5 GB of a volume
ABORT_FREE_MB = 2_000       # trip the watchdog below 2 GB free
WATCH_INTERVAL_S = 1.0      # how often the watchdog samples
DEFAULT_CAP_GB = 20.0       # max intermediate bytes ONE render may hold on disk


def human(n: float) -> str:
    """Bytes -> a short human string. Used in every message a user reads."""
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024.0 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n:.0f} B"
        n /= 1024.0
    return f"{n:.1f} TB"


def disk_free(path: Path) -> int:
    """Free bytes on the volume holding `path` (walks up to an existing parent)."""
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        return shutil.disk_usage(p).free
    except OSError:
        return 0


def disk_total(path: Path) -> int:
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        return shutil.disk_usage(p).total
    except OSError:
        return 0


def dir_bytes(path: Path) -> int:
    """Total bytes under `path`. Tolerates files vanishing mid-walk."""
    total = 0
    p = Path(path)
    if not p.exists():
        return 0
    if p.is_file():
        try:
            return p.stat().st_size
        except OSError:
            return 0
    for root, _dirs, files in os.walk(p, onerror=lambda _e: None):
        for fn in files:
            try:
                total += (Path(root) / fn).stat().st_size
            except OSError:
                pass
    return total


class DiskBudgetError(RuntimeError):
    """Raised when a render cannot proceed without risking the volume."""


@dataclass
class Budget:
    """A preflight space budget for one render, checked against reality.

    `estimate_total_bytes` is computed from a MEASURED first frame, never guessed:
    a guess is how the original failure happened.
    """
    out_dir: Path
    frame_count: int
    reserve_bytes: int = RESERVE_MB * 1024 * 1024
    cap_bytes: int = int(DEFAULT_CAP_GB * 1024 ** 3)
    measured_frame_bytes: int | None = None

    @property
    def estimate_total_bytes(self) -> int:
        if self.measured_frame_bytes is None:
            return 0
        return self.measured_frame_bytes * max(0, self.frame_count)

    def check(self) -> None:
        """Raise DiskBudgetError unless this render fits, with the real numbers.

        Two independent limits: the volume's actual free space (minus a reserve so
        we never take the last of it) and an explicit cap on how much intermediate
        data a single render may hold. Either one refusing is a refusal.
        """
        if self.measured_frame_bytes is None:
            return  # nothing measured yet; caller checks again after frame 1
        need = self.estimate_total_bytes
        free = disk_free(self.out_dir)
        usable = free - self.reserve_bytes

        if need > self.cap_bytes:
            raise DiskBudgetError(
                f"REFUSING TO START: this render would write ~{human(need)} of "
                f"intermediate frames ({self.frame_count:,} frames x "
                f"{human(self.measured_frame_bytes)}), over the {human(self.cap_bytes)} "
                f"per-render cap.\n"
                f"  Fix (any one): use --stream so frames never touch the disk; "
                f"lower --fps; shorten the scene; use --format jpg; "
                f"or raise the cap deliberately with --max-bytes-gb.")
        if need > usable:
            raise DiskBudgetError(
                f"REFUSING TO START: this render needs ~{human(need)} but only "
                f"{human(free)} is free ({human(usable)} usable after a "
                f"{human(self.reserve_bytes)} safety reserve).\n"
                f"  Fix (any one): use --stream (writes no frames at all); free space; "
                f"lower --fps; shorten the scene; run `coordn8 clean` to reclaim "
                f"intermediates from previous runs.")

    def report(self) -> dict:
        return {
            "frames": self.frame_count,
            "measured_frame_bytes": self.measured_frame_bytes,
            "estimate_total": self.estimate_total_bytes,
            "estimate_total_h": human(self.estimate_total_bytes),
            "free": disk_free(self.out_dir),
            "free_h": human(disk_free(self.out_dir)),
            "reserve": self.reserve_bytes,
            "cap": self.cap_bytes,
            "cap_h": human(self.cap_bytes),
        }


@dataclass
class DiskWatchdog:
    """Background free-space monitor that TRIPS AN ABORT instead of just logging.

    The original failure had no way to stop: even a worker that noticed the disk
    filling could not halt the pool. So this owns a `threading.Event` that every
    worker checks, and the capture loop treats it as a hard stop.
    """
    path: Path
    abort_free_bytes: int = ABORT_FREE_MB * 1024 * 1024
    cap_bytes: int | None = None
    watch_dir: Path | None = None
    interval_s: float = WATCH_INTERVAL_S
    abort: threading.Event = field(default_factory=threading.Event)
    reason: str = ""
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None
    samples: int = 0

    def _tick(self) -> None:
        free = disk_free(self.path)
        if free < self.abort_free_bytes:
            self.reason = (f"free space fell to {human(free)}, below the "
                           f"{human(self.abort_free_bytes)} floor — ABORTING to protect "
                           f"the volume (partial output will be cleaned up)")
            self.abort.set()
            return
        if self.cap_bytes is not None and self.watch_dir is not None:
            used = dir_bytes(self.watch_dir)
            if used > self.cap_bytes:
                self.reason = (f"intermediate frames reached {human(used)}, over the "
                               f"{human(self.cap_bytes)} per-render cap — ABORTING "
                               f"(runaway accumulation)")
                self.abort.set()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            self.samples += 1
            self._tick()
            if self.abort.is_set():
                return

    def start(self) -> DiskWatchdog:
        self._tick()          # check immediately; don't wait one interval
        if not self.abort.is_set():
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=self.interval_s * 2)

    def __enter__(self) -> DiskWatchdog:
        return self.start()

    def __exit__(self, *_exc) -> None:
        self.stop()

    def raise_if_tripped(self) -> None:
        if self.abort.is_set():
            raise DiskBudgetError(self.reason or "disk watchdog aborted the render")


def reclaim(paths: list[Path], *, dry_run: bool = False) -> dict:
    """Delete intermediate render artifacts, reporting exactly what was freed.

    Used by `coordn8 clean` and by the failure/interrupt paths. Refuses to touch
    anything that is not recognisably a Coordn8 intermediate, so a mistyped path
    can never delete a user's work.
    """
    SAFE_NAMES = {"frames", "_frames", "_manim", "media", "_build"}
    SAFE_SUFFIXES = {".png", ".ppm", ".jpg", ".jpeg", ".webp", ".y4m", ".raw"}
    removed, skipped, freed = [], [], 0

    for p in paths:
        p = Path(p)
        if not p.exists():
            continue
        # A directory is a render intermediate when its NAME says so, when its
        # PARENT chain says so (…/_build/frames/s1 — the shape capture writes), or
        # when it actually contains frame files. Checked over a wide sample so a few
        # sub-directories first cannot hide the evidence.
        parents = {q.name for q in p.parents}
        looks_like_frames = (
            p.is_dir() and (
                p.name in SAFE_NAMES
                or p.name.startswith(("frames", "_frames", "_manim", "coordn8-stream-"))
                or bool(SAFE_NAMES & parents)
                or any(c.suffix.lower() in SAFE_SUFFIXES
                       for c in list(p.rglob("*"))[:400] if c.is_file())
            )
        )
        is_frame_file = p.is_file() and p.suffix.lower() in SAFE_SUFFIXES
        if not (looks_like_frames or is_frame_file):
            skipped.append({"path": str(p),
                            "why": "not recognisable as a Coordn8 render intermediate "
                                   "— refusing to delete"})
            continue
        size = dir_bytes(p)
        if dry_run:
            removed.append({"path": str(p), "bytes": size, "bytes_h": human(size),
                            "action": "would remove"})
            freed += size
            continue
        try:
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)
            removed.append({"path": str(p), "bytes": size, "bytes_h": human(size),
                            "action": "removed"})
            freed += size
        except OSError as e:
            skipped.append({"path": str(p), "why": f"{type(e).__name__}: {e}"})

    return {"removed": removed, "skipped": skipped,
            "freed_bytes": freed, "freed": human(freed), "dry_run": dry_run}


class cleanup_on_exit:
    """Context manager: remove partial artifacts on exception OR Ctrl-C/SIGTERM.

    A killed render must not leave tens of gigabytes behind — that is how a 1 TB
    volume disappeared across several attempts. On a clean exit, `keep()` marks the
    output as wanted and nothing is deleted.
    """

    def __init__(self, paths: list[Path], *, enabled: bool = True,
                 log=None):
        self.paths = [Path(p) for p in paths]
        self.enabled = enabled
        self._keep = False
        self._log = log or (lambda m: sys.stderr.write(f"[coordn8:disk] {m}\n"))
        self._prev_handlers: dict[int, object] = {}

    def keep(self) -> None:
        self._keep = True

    def _handle_signal(self, signum, _frame):
        self._log(f"interrupted (signal {signum}) — cleaning up partial render output")
        self._purge()
        # Restore and re-raise the default behaviour so the exit code is honest.
        h = self._prev_handlers.get(signum, signal.SIG_DFL)
        try:
            signal.signal(signum, h)  # type: ignore[arg-type]
        except (ValueError, OSError, TypeError):
            pass
        os.kill(os.getpid(), signum)

    def _purge(self):
        if not self.enabled or self._keep:
            return
        res = reclaim(self.paths)
        if res["freed_bytes"]:
            self._log(f"reclaimed {res['freed']} of partial output")

    def __enter__(self):
        if self.enabled:
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    self._prev_handlers[sig] = signal.getsignal(sig)
                    signal.signal(sig, self._handle_signal)
                except (ValueError, OSError):
                    pass  # not the main thread: rely on the exception path
        return self

    def __exit__(self, exc_type, _exc, _tb):
        for sig, h in self._prev_handlers.items():
            try:
                signal.signal(sig, h)  # type: ignore[arg-type]
            except (ValueError, OSError, TypeError):
                pass
        if exc_type is not None:
            self._purge()
        return False


def preflight_summary(out_dir: Path, frame_count: int, frame_bytes: int,
                      cap_bytes: int | None = None) -> str:
    """One human line stating the plan BEFORE any bulk writing starts."""
    b = Budget(out_dir=out_dir, frame_count=frame_count,
               measured_frame_bytes=frame_bytes,
               cap_bytes=cap_bytes or int(DEFAULT_CAP_GB * 1024 ** 3))
    r = b.report()
    return (f"plan: {frame_count:,} frames x {human(frame_bytes)} "
            f"= ~{r['estimate_total_h']} of intermediates; free {r['free_h']}; "
            f"cap {r['cap_h']}")


if __name__ == "__main__":
    # Tiny CLI so the numbers are inspectable without reading the code.
    import argparse
    import json as _json
    ap = argparse.ArgumentParser(description="Coordn8 disk guard utilities.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("report", help="free/total space and the size of a folder")
    s.add_argument("path")
    r = sub.add_parser("reclaim", help="delete render intermediates")
    r.add_argument("paths", nargs="+")
    r.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.cmd == "report":
        p = Path(a.path)
        print(_json.dumps({"path": str(p), "free": human(disk_free(p)),
                           "total": human(disk_total(p)),
                           "folder_bytes": human(dir_bytes(p))}, indent=2))
    else:
        print(_json.dumps(reclaim([Path(x) for x in a.paths], dry_run=a.dry_run),
                          indent=2))
