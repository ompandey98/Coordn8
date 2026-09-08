#!/usr/bin/env python3
"""
Coordn8 — TELEMETRY: observability for a long render, and a verdict on whether it
ran at a sane speed.

WHY THIS FILE EXISTS
--------------------
Two real incidents, opposite in shape, same root cause — nobody could see what the
render was doing:

  * A render accumulated frames until a 1 TB disk was full and the machine died. It
    was never visible that it was consuming ~30 GB per scene.
  * Renders were "painfully slow" while the machine sat mostly idle, because the
    worker pool had throttled down and nothing reported achieved throughput.

Both are observability failures before they are anything else. A pipeline that can
run for an hour MUST answer, continuously and cheaply:

    how far along am I · how fast am I going · when will I finish ·
    what is this costing the host (CPU, RAM, disk) · and is that rate ACCEPTABLE

This module provides that, with **zero third-party dependencies**:

  Run        an append-only JSONL event stream + a summary JSON on disk, so a crashed
             or killed run still leaves a readable record (the same write-as-you-go
             discipline the render itself uses).
  Sampler    a background thread recording a TIME SERIES of load, available RAM and
             free disk — a trend, not a single instantaneous reading, because the
             interesting failures are gradual.
  Progress   done/total, an EWMA rate, and an ETA, printed on a throttle so a long
             run is legible without flooding the terminal.
  verdict()  the live quality assessment: is the measured rate above the floor, and
             if it is slow, WHICH signal explains it (load, RAM, disk, or a throttled
             pool). "Too slow" and "too aggressive" are both defects, and this names
             which one is happening rather than leaving the user to guess.

Everything degrades quietly: if a probe is unavailable on this OS the field is None
and the rest still works. Telemetry must never be the thing that breaks a render.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections import deque
from typing import Callable
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parallel import _free_ram_mb, _load_1m  # noqa: E402
from diskguard import disk_free, dir_bytes, human  # noqa: E402

# Sampling and reporting cadence. Cheap enough to leave on always: one sample is a
# couple of syscalls, and being able to see the run is worth far more than that.
SAMPLE_INTERVAL_S = 2.0
REPORT_INTERVAL_S = 5.0
EWMA_ALPHA = 0.3          # recent rate matters more than the long-run average

# Throughput floors. Deliberately generous — the goal is to catch "something is
# badly wrong / we are crawling", not to police a fast machine.
MIN_FRAMES_PER_S = 0.5    # below this, a 10k-frame scene would take > 5 hours
SLOW_UTILISATION = 0.35   # using under ~35% of the worker ceiling while idle = slow


def _now() -> float:
    return time.monotonic()


def fmt_duration(seconds: float | None) -> str:
    """Seconds -> '1h 12m 30s'. None-safe, because an unknown ETA is normal early on."""
    if seconds is None or seconds != seconds or seconds < 0:
        return "unknown"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


@dataclass
class Sample:
    t: float
    load_1m: float | None
    free_ram_mb: int | None
    free_disk_bytes: int | None
    watched_bytes: int | None = None

    def as_dict(self) -> dict:
        return {"t": round(self.t, 2), "load_1m": self.load_1m,
                "free_ram_mb": self.free_ram_mb,
                "free_disk_bytes": self.free_disk_bytes,
                "watched_bytes": self.watched_bytes}


@dataclass
class ResourceSampler:
    """Background time-series recorder. A trend, not a snapshot."""
    disk_path: Path | None = None
    watch_dir: Path | None = None
    interval_s: float = SAMPLE_INTERVAL_S
    samples: list[Sample] = field(default_factory=list)
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _take(self) -> Sample:
        s = Sample(
            t=_now(),
            load_1m=_load_1m(),
            free_ram_mb=_free_ram_mb(),
            free_disk_bytes=disk_free(self.disk_path) if self.disk_path else None,
            watched_bytes=dir_bytes(self.watch_dir) if self.watch_dir else None,
        )
        with self._lock:
            self.samples.append(s)
        return s

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            try:
                self._take()
            except Exception:  # noqa: BLE001 — telemetry never breaks the run
                pass

    def start(self) -> ResourceSampler:
        self._take()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> ResourceSampler:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=self.interval_s * 2)
        try:
            self._take()          # a final reading, so the series brackets the run
        except Exception:  # noqa: BLE001
            pass
        return self

    def __enter__(self) -> ResourceSampler:
        return self.start()

    def __exit__(self, *_exc) -> None:
        self.stop()

    def series(self) -> list[dict]:
        with self._lock:
            return [s.as_dict() for s in self.samples]

    def summary(self) -> dict:
        with self._lock:
            xs = list(self.samples)
        if not xs:
            return {}

        def stat(vals):
            vals = [v for v in vals if v is not None]
            if not vals:
                return None
            return {"min": min(vals), "max": max(vals),
                    "mean": round(sum(vals) / len(vals), 2), "last": vals[-1]}

        cores = os.cpu_count() or 1
        load = stat([s.load_1m for s in xs])
        return {
            "samples": len(xs),
            "cores": cores,
            "load_1m": load,
            "load_per_core": (round(load["mean"] / cores, 2) if load else None),
            "free_ram_mb": stat([s.free_ram_mb for s in xs]),
            "free_disk": stat([s.free_disk_bytes for s in xs]),
            "free_disk_h": (human(min(v for v in
                                      (s.free_disk_bytes for s in xs) if v is not None))
                            if any(s.free_disk_bytes is not None for s in xs) else None),
            "watched_peak": (max((s.watched_bytes or 0) for s in xs) or None),
            "watched_peak_h": human(max((s.watched_bytes or 0) for s in xs))
            if any(s.watched_bytes for s in xs) else None,
        }


@dataclass
class Progress:
    """done/total with an EWMA rate and an ETA, printed on a throttle.

    The EWMA matters: a plain average hides the pool ramping up or a machine getting
    busy, which is exactly the signal a user needs while waiting.
    """
    total: int
    label: str = "items"
    report_interval_s: float = REPORT_INTERVAL_S
    # Annotated so dataclass treats these as real fields (an un-annotated class
    # attribute is silently NOT a field, which makes the constructor reject it).
    log: "Callable[[str], None] | None" = None
    done: int = 0
    started: float = field(default_factory=_now)
    _last_report: float = 0.0
    _last_t: float = field(default_factory=_now)
    _rate_ewma: float | None = None
    _recent: deque = field(default_factory=lambda: deque(maxlen=64))
    _lock: threading.Lock = field(default_factory=threading.Lock)
    # Optional callable returning a short live suffix (e.g. workers + disk used).
    extra: "Callable[[], str] | None" = None

    def __post_init__(self):
        if self.log is None:
            self.log = lambda m: sys.stderr.write(m + "\n")

    def tick(self, n: int = 1) -> None:
        with self._lock:
            now = _now()
            dt = now - self._last_t
            self._last_t = now
            self.done += n
            if dt > 0:
                inst = n / dt
                self._recent.append(inst)
                self._rate_ewma = (inst if self._rate_ewma is None
                                   else EWMA_ALPHA * inst + (1 - EWMA_ALPHA) * self._rate_ewma)
            due = (now - self._last_report) >= self.report_interval_s
            if due:
                self._last_report = now
        if due:
            self.report()

    @property
    def rate(self) -> float | None:
        """Best available rate estimate ({label}/s)."""
        with self._lock:
            if self._rate_ewma is not None:
                return self._rate_ewma
            el = _now() - self.started
            return (self.done / el) if el > 0 and self.done else None

    @property
    def overall_rate(self) -> float | None:
        el = _now() - self.started
        return (self.done / el) if el > 0 and self.done else None

    @property
    def eta_s(self) -> float | None:
        r = self.rate
        if not r or r <= 0:
            return None
        return max(0.0, (self.total - self.done) / r)

    def report(self) -> None:
        pct = (100.0 * self.done / self.total) if self.total else 0.0
        r = self.rate
        suffix = ""
        if self.extra:
            try:
                suffix = " | " + self.extra()
            except Exception:  # noqa: BLE001
                suffix = ""
        self.log(f"[coordn8:progress] {self.done}/{self.total} {self.label} "
                 f"({pct:.0f}%) | {r:.1f}/s" if r else
                 f"[coordn8:progress] {self.done}/{self.total} {self.label} ({pct:.0f}%)")
        # Second line keeps the first scannable while still giving ETA + cost.
        self.log(f"[coordn8:progress]   elapsed {fmt_duration(_now() - self.started)} "
                 f"| ETA {fmt_duration(self.eta_s)}{suffix}")

    def summary(self) -> dict:
        return {"total": self.total, "done": self.done, "label": self.label,
                "elapsed_s": round(_now() - self.started, 2),
                "rate_per_s": round(self.rate, 3) if self.rate else None,
                "overall_rate_per_s": round(self.overall_rate, 3) if self.overall_rate else None,
                "eta_s": round(self.eta_s, 1) if self.eta_s else None}


def verdict(progress: Progress, sampler: ResourceSampler, *,
            peak_workers: int | None = None, worker_ceiling: int | None = None,
            min_rate: float = MIN_FRAMES_PER_S) -> dict:
    """LIVE QUALITY ASSESSMENT of the run itself: was this a sane speed, and if not, why?

    Reports one of:
      ok            — rate above the floor and the host was not abused
      slow          — below the floor; the `because` field names the binding constraint
      throttled     — the pool used a small fraction of its ceiling while resources
                      were free, i.e. we were needlessly slow (the "painfully slow
                      while the machine idles" complaint)
      host-pressure — we ran hard enough to hurt the machine (sustained load over
                      ~1.5x cores, or RAM/disk nearly exhausted)

    "Too slow" and "too aggressive" are BOTH failures. Naming which one is happening
    is the whole point — a bare number leaves the user guessing.
    """
    s = sampler.summary()
    cores = s.get("cores") or (os.cpu_count() or 1)
    rate = progress.rate or progress.overall_rate
    load = (s.get("load_1m") or {}).get("mean")
    load_max = (s.get("load_1m") or {}).get("max")
    ram_min = (s.get("free_ram_mb") or {}).get("min")
    disk_min = (s.get("free_disk") or {}).get("min")

    reasons: list[str] = []
    status = "ok"

    host_pressure = []
    if load_max is not None and load_max > cores * 1.5:
        host_pressure.append(
            f"1-minute load peaked at {load_max:.1f} on {cores} cores "
            f"(over the {cores * 1.5:.1f} ceiling) — the pool should have backed off")
    if ram_min is not None and ram_min < 512:
        host_pressure.append(f"available RAM fell to {ram_min} MB — swap risk")
    if disk_min is not None and disk_min < 2 * 1024 ** 3:
        host_pressure.append(f"free disk fell to {human(disk_min)}")
    if host_pressure:
        status = "host-pressure"
        reasons += host_pressure

    if rate is not None and rate < min_rate:
        status = "slow"
        because = []
        if load is not None and load > cores:
            because.append(f"the machine was busy (mean load {load:.1f} vs {cores} cores) — "
                           f"other work was competing, so the pool correctly throttled")
        if ram_min is not None and ram_min < 1500:
            because.append(f"memory was tight (min {ram_min} MB available), capping workers")
        if disk_min is not None and disk_min < 5 * 1024 ** 3:
            because.append(f"the volume was nearly full ({human(disk_min)} free)")
        if (peak_workers and worker_ceiling
                and peak_workers < max(1, worker_ceiling * SLOW_UTILISATION)):
            because.append(f"the pool only reached {peak_workers} of {worker_ceiling} "
                           f"possible workers")
        if not because:
            because.append("no resource constraint explains it — suspect the per-item "
                           "cost itself (e.g. a slow page, a large viewport, or a "
                           "settle delay set too high)")
        reasons += [f"measured {rate:.2f}/s, below the {min_rate}/s floor. Because: "
                    + "; ".join(because)]
    elif (peak_workers and worker_ceiling
          and peak_workers < max(1, worker_ceiling * SLOW_UTILISATION)
          and (load is None or load < cores * 0.75)
          and (ram_min is None or ram_min > 2000)):
        status = "throttled"
        reasons.append(
            f"the pool used only {peak_workers} of {worker_ceiling} available workers "
            f"while the machine had headroom (mean load "
            f"{load if load is not None else 'n/a'} on {cores} cores, "
            f"{ram_min if ram_min is not None else 'n/a'} MB RAM free). "
            f"This is the 'needlessly slow' case — raise concurrency or check what "
            f"serialised the work.")

    return {
        "status": status,
        "rate_per_s": round(rate, 3) if rate else None,
        "min_rate_per_s": min_rate,
        "peak_workers": peak_workers,
        "worker_ceiling": worker_ceiling,
        "elapsed_s": round(_now() - progress.started, 2),
        "eta_s": round(progress.eta_s, 1) if progress.eta_s else None,
        "resources": s,
        "reasons": reasons,
        "summary": (
            "ran at an acceptable rate without stressing the host"
            if status == "ok" else "; ".join(reasons)[:600]),
    }


class Run:
    """One observable run: a JSONL event stream + a summary, both written as we go.

    Write-as-you-go is deliberate: a run that is killed (or that kills the machine)
    must still leave a readable record of what it was doing and what it cost. A log
    that only appears on clean exit is useless for exactly the failures that matter.
    """

    def __init__(self, name: str, out_dir: Path | None = None, *,
                 echo: bool = True, meta: dict | None = None):
        self.name = name
        self.started_wall = time.time()
        self.started = _now()
        self.echo = echo
        self.dir = Path(out_dir) if out_dir else Path(".coordn8-telemetry")
        self.events_path = self.dir / f"{name}.events.jsonl"
        self.summary_path = self.dir / f"{name}.summary.json"
        self._lock = threading.Lock()
        self._stages: list[dict] = []
        self._ok = True
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._fh = self.events_path.open("a", encoding="utf-8")
        except OSError:
            self._fh = None       # telemetry must never break a render
        self.event("run.start", meta=meta or {}, wall=self.started_wall)

    def event(self, kind: str, **fields) -> None:
        rec = {"t": round(_now() - self.started, 3), "kind": kind, **fields}
        line = json.dumps(rec, ensure_ascii=False, default=str)
        with self._lock:
            if self._fh:
                try:
                    self._fh.write(line + "\n")
                    self._fh.flush()      # flush every event: a killed run still has it
                except OSError:
                    pass
        if self.echo and kind.endswith((".error", ".abort", ".refuse")):
            sys.stderr.write(f"[coordn8:telemetry] {kind}: "
                             f"{fields.get('reason') or fields}\n")

    def stage(self, name: str):
        """Context manager timing one stage and recording its outcome."""
        run = self

        class _Stage:
            def __enter__(self_inner):
                self_inner.t0 = _now()
                run.event("stage.start", stage=name)
                return self_inner

            def __exit__(self_inner, exc_type, exc, _tb):
                dt = round(_now() - self_inner.t0, 3)
                rec = {"stage": name, "seconds": dt,
                       "status": "error" if exc_type else "ok"}
                if exc_type:
                    rec["error"] = f"{exc_type.__name__}: {exc}"
                    run._ok = False
                run._stages.append(rec)
                run.event("stage.end", **rec)
                return False
        return _Stage()

    def finish(self, status: str = "ok", **extra) -> dict:
        total = round(_now() - self.started, 2)
        summary = {
            "run": self.name,
            "status": status if self._ok else "error",
            "started_wall": self.started_wall,
            "total_seconds": total,
            "total_h": fmt_duration(total),
            "stages": self._stages,
            "slowest_stage": (max(self._stages, key=lambda s: s["seconds"])
                              if self._stages else None),
            **extra,
        }
        self.event("run.end", **{k: v for k, v in summary.items() if k != "stages"})
        try:
            self.summary_path.write_text(
                json.dumps(summary, indent=2, ensure_ascii=False, default=str) + "\n",
                encoding="utf-8")
        except OSError:
            pass
        with self._lock:
            if self._fh:
                try:
                    self._fh.close()
                except OSError:
                    pass
                self._fh = None
        return summary

    def __enter__(self) -> Run:
        return self

    def __exit__(self, exc_type, exc, _tb):
        if exc_type:
            self.event("run.error", reason=f"{exc_type.__name__}: {exc}")
            self.finish("error")
        return False


if __name__ == "__main__":
    # Self-test: exercise every piece without needing a render.
    import argparse
    import tempfile
    ap = argparse.ArgumentParser(description="Coordn8 telemetry self-test.")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    d = Path(tempfile.mkdtemp())
    with Run("selftest", d, echo=False, meta={"why": "selftest"}) as run:
        with run.stage("warmup"):
            time.sleep(0.05)
        with ResourceSampler(disk_path=d, watch_dir=d, interval_s=0.05) as smp:
            p = Progress(total=20, label="frames", report_interval_s=0.05,
                         log=lambda m: None)
            for _ in range(20):
                time.sleep(0.01)
                p.tick()
            time.sleep(0.12)
        v = verdict(p, smp, peak_workers=1, worker_ceiling=8, min_rate=0.5)
        s = run.finish("ok", progress=p.summary(), verdict=v,
                       resources=smp.summary())
    ok = True
    checks = [
        ("events file written", run.events_path.exists()),
        ("summary file written", run.summary_path.exists()),
        ("events are valid JSONL",
         all(json.loads(l) for l in run.events_path.read_text().splitlines() if l.strip())),
        ("stage timing recorded", any(x["stage"] == "warmup" for x in s["stages"])),
        ("progress reached total", p.done == 20),
        ("rate computed", (p.rate or 0) > 0),
        ("resource series collected", len(smp.samples) >= 2),
        ("verdict has a status", v["status"] in
         ("ok", "slow", "throttled", "host-pressure")),
        ("verdict explains a non-ok status",
         v["status"] == "ok" or bool(v["reasons"])),
    ]
    for name, cond in checks:
        print(("  ok   " if cond else "  FAIL ") + name)
        ok = ok and bool(cond)
    print(f"\nRESULT: {'all telemetry self-checks passed' if ok else 'FAILURES'}")
    sys.exit(0 if ok else 1)
