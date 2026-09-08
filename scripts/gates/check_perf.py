#!/usr/bin/env python3
"""
Coordn8 gate — PERFORMANCE / RUN HEALTH. Was the render a sane speed, and did it
treat the host decently?

WHY THIS GATE EXISTS
--------------------
Speed has failed in BOTH directions on this pipeline, and neither was visible:

  * Too slow: renders crawled while the machine sat mostly idle, because the naive
    path cold-launched a browser per frame (measured 5.4s/frame) and nothing ever
    reported achieved throughput.
  * Too aggressive: that same naive path, run in parallel, drove the 1-minute load to
    37 on 14 cores — hammering the host while still being slow.

So "fast" is not the goal and neither is "gentle": the goal is *both*, and this gate
is the check that names which one broke. It reads the telemetry a render already
writes (`_telemetry/*.summary.json`) and reports one of:

    ok             acceptable rate, host not abused
    slow           below the throughput floor, WITH the binding constraint named
    throttled      barely used the available workers while resources were free
                   (the "needlessly slow" case)
    host-pressure  ran hard enough to hurt the machine (sustained load over ~1.5x
                   cores, RAM nearly gone, or disk nearly full)

It also flags the single highest-value speed fix when it applies: a run that used the
cold-launch engine when a persistent browser was available.

ADVISORY for the speed verdicts. The RESOURCE-EXHAUSTION conditions are reported as
CRITICAL — a gate may be relaxed about taste, never about damaging a host.

Usage:
    python3 scripts/gates/check_perf.py path/to/Coordn8
    python3 scripts/gates/check_perf.py path/to/_telemetry/capture-s1.summary.json
    python3 scripts/gates/check_perf.py <build> --min-rate 1.0
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.common import (EXIT_ERROR, EXIT_PASS, EXIT_REVIEW,  # noqa: E402
                        EXIT_SKIPPED, emit)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "render"))
from diskguard import human  # noqa: E402
from telemetry import fmt_duration  # noqa: E402

DEFAULT_MIN_RATE = 0.5     # frames/s; below this a full scene takes hours


def find_summaries(target: Path) -> list[Path]:
    if target.is_file() and target.suffix == ".json":
        return [target]
    if not target.exists():
        return []
    hits = sorted(target.rglob("*_telemetry/*.summary.json"))
    hits += sorted(target.rglob("_telemetry/*.summary.json"))
    hits += sorted(target.glob("*.summary.json"))
    # de-dup, preserve order
    seen, out = set(), []
    for h in hits:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


def assess(summary: dict, min_rate: float) -> dict:
    """Turn one run's telemetry into a status + concrete findings."""
    name = summary.get("run", "?")
    prog = summary.get("progress") or {}
    res = summary.get("resources") or {}
    v = summary.get("verdict") or {}
    findings: list[str] = []
    critical = False

    rate = prog.get("overall_rate_per_s") or prog.get("rate_per_s")
    cores = res.get("cores")
    load = (res.get("load_1m") or {})
    ram = (res.get("free_ram_mb") or {})
    disk = (res.get("free_disk") or {})

    status = v.get("status") or "unknown"

    # The telemetry already computed a verdict; re-derive the important parts here so
    # this gate is not merely echoing a claim it cannot check (a gate that trusts the
    # thing it is auditing is theatre).
    if rate is not None and rate < min_rate:
        status = "slow"
        findings.append(
            f"{name}: measured {rate:.2f} items/s, below the {min_rate}/s floor "
            f"(elapsed {fmt_duration(prog.get('elapsed_s'))} for "
            f"{prog.get('done')}/{prog.get('total')}).")
    if cores and load.get("max") is not None and load["max"] > cores * 1.5:
        critical = True
        findings.append(
            f"{name}: 1-minute load peaked at {load['max']:.1f} on {cores} cores "
            f"(over the {cores * 1.5:.1f} back-off ceiling) — the run was abusing the "
            f"host. Reduce concurrency, or use the persistent-browser engine so one "
            f"process does the work of many.")
    if ram.get("min") is not None and ram["min"] < 512:
        critical = True
        findings.append(f"{name}: available RAM fell to {ram['min']} MB — swap risk.")
    if disk.get("min") is not None and disk["min"] < 2 * 1024 ** 3:
        critical = True
        findings.append(f"{name}: free disk fell to {human(disk['min'])} — at the "
                        f"abort floor.")

    # The single biggest speed lever, when it was left on the table.
    engine = summary.get("engine") or (summary.get("meta") or {}).get("engine")
    if engine == "cold-chrome-pool":
        findings.append(
            f"{name}: used the COLD-LAUNCH engine (a new browser per frame). A "
            f"persistent browser is dramatically faster on the same hardware and uses "
            f"one process instead of many. Install it: pip install playwright && "
            f"python3 -m playwright install chromium")

    if status == "throttled":
        findings.append(
            f"{name}: {v.get('summary', 'the pool under-used available capacity')}")

    return {"run": name, "status": status, "rate": rate, "critical": critical,
            "elapsed": fmt_duration(prog.get("elapsed_s")),
            "peak_workers": v.get("peak_workers"),
            "findings": findings}


def main() -> int:
    ap = argparse.ArgumentParser(description="Coordn8 performance / run-health gate.")
    ap.add_argument("target", help="a build folder or a telemetry summary JSON")
    ap.add_argument("--min-rate", type=float, default=DEFAULT_MIN_RATE)
    args = ap.parse_args()

    target = Path(args.target)
    summaries = find_summaries(target)
    if not summaries:
        emit({"gate": "perf", "status": "SKIPPED",
              "summary": f"no telemetry summaries under {target} — nothing has been "
                         f"rendered yet, so there is no run health to assess. (A render "
                         f"writes _telemetry/<run>.summary.json as it goes.)"})
        return EXIT_SKIPPED

    runs, findings, critical = [], [], False
    skipped_non_render: list[str] = []
    for p in summaries:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            emit({"gate": "perf", "status": "ERROR",
                  "summary": f"could not parse {p}: {type(e).__name__}: {e}"})
            return EXIT_ERROR
        # Only RENDER runs have throughput to judge. Other telemetry (e.g. the
        # quality assessment itself) has no frame count, and reporting it as a run
        # with an "unknown" rate is noise that buries the real signal.
        if not (data.get("progress") or {}).get("total"):
            skipped_non_render.append(data.get("run", str(p.name)))
            continue
        a = assess(data, args.min_rate)
        runs.append(a)
        findings += a["findings"]
        critical = critical or a["critical"]

    if not runs:
        emit({"gate": "perf", "status": "SKIPPED",
              "summary": f"found {len(summaries)} telemetry file(s) but none is a render "
                         f"with a frame count — no throughput to assess yet"})
        return EXIT_SKIPPED
    slow = [r for r in runs if r["status"] in ("slow", "throttled")]
    # Built outside the f-string: nesting same-type quotes inside one is a syntax
    # error before Python 3.12, and this package supports 3.8+.
    rate_list = ", ".join(
        "{}={:.1f}/s".format(r["run"], r["rate"]) for r in runs if r["rate"])
    emit({
        "gate": "perf",
        "status": "REVIEW" if findings else "PASS",
        "critical": critical,
        "runs_assessed": len(runs),
        "skipped_non_render": skipped_non_render,
        "runs": runs,
        "slow_runs": [r["run"] for r in slow],
        "limits": {"min_rate_per_s": args.min_rate,
                   "load_backoff_ceiling": "1.5 x cores",
                   "ram_floor_mb": 512, "disk_floor": "2 GB"},
        "findings": findings,
        "meaning": ("Both directions are failures: 'slow' means the run crawled, "
                    "'host-pressure' means it hurt the machine. A healthy run is fast "
                    "AND gentle, so this names which one broke rather than reporting a "
                    "bare number."),
        "summary": (
            f"{len(findings)} performance finding(s) across {len(runs)} run(s)"
            + (" — CRITICAL: the host was under real pressure" if critical else "")
            if findings else
            f"{len(runs)} run(s) all within the throughput floor and gentle on the "
            f"host ({rate_list})"),
    })
    return EXIT_REVIEW if findings else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
