#!/usr/bin/env python3
"""Run every tests/v2 suite (in parallel) and collect exact pass/fail check names.

    python3 tests/v2/_runall.py [--out DIR] [--only a,b] [--skip a,b]
Writes DIR/<suite>.json per suite and DIR/_all.json = {suite: {"passed": [...], "failed": [...], "rc": n}}.
Used by tests/run_tests.py, tests/audit_v2.py and tests/v2/test_coverage_matrix.py.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
LATE = {"test_coverage_matrix.py"}  # consumes the others' results


def suites(only=None, skip=None):
    out = []
    for p in sorted(HERE.glob("test_*.py")):
        stem = p.stem[len("test_"):]
        if only and stem not in only:
            continue
        if skip and stem in skip:
            continue
        out.append(p)
    return out


def run_one(p: Path, outdir: Path, env: dict) -> tuple:
    j = outdir / f"{p.stem}.json"
    t0 = time.time()
    try:
        r = subprocess.run([sys.executable, str(p), "--json", str(j)], capture_output=True,
                           text=True, env=env, timeout=1800)
        rc, log = r.returncode, r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        rc, log = 124, "TIMEOUT 1800s"
    res = {"passed": [], "failed": [], "rc": rc, "secs": round(time.time() - t0, 1)}
    if j.exists():
        try:
            d = json.loads(j.read_text(encoding="utf-8"))
            res["passed"], res["failed"] = d.get("passed", []), d.get("failed", [])
        except ValueError:
            pass
    if rc != 0 and not res["failed"]:
        res["failed"] = [{"name": f"{p.stem} ran cleanly", "detail": log[-600:]}]
    (outdir / f"{p.stem}.log").write_text(log, encoding="utf-8")
    return p.stem, res


def run_all(outdir=None, only=None, skip=None, workers=None) -> dict:
    outdir = Path(outdir or tempfile.mkdtemp(prefix="c8v2res-"))
    outdir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["C8_V2_RESULTS"] = str(outdir)
    first = [p for p in suites(only, skip) if p.name not in LATE]
    late = [p for p in suites(only, skip) if p.name in LATE]
    workers = workers or max(2, min(6, (os.cpu_count() or 4) // 2))
    allres = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for stem, res in ex.map(lambda p: run_one(p, outdir, env), first):
            allres[stem] = res
    (outdir / "_all.json").write_text(json.dumps(allres, indent=1), encoding="utf-8")
    for p in late:
        stem, res = run_one(p, outdir, env)
        allres[stem] = res
    (outdir / "_all.json").write_text(json.dumps(allres, indent=1), encoding="utf-8")
    return allres


def load_or_run() -> dict:
    """Reuse results from C8_V2_RESULTS when a parent run already produced them."""
    d = os.environ.get("C8_V2_RESULTS")
    if d and (Path(d) / "_all.json").exists():
        return json.loads((Path(d) / "_all.json").read_text(encoding="utf-8"))
    return run_all()


def passed_names(allres: dict) -> set:
    s = set()
    for stem, r in allres.items():
        for n in r.get("passed", []):
            s.add(f"{stem}::{n}")
    return s


if __name__ == "__main__":
    a = sys.argv[1:]
    out = a[a.index("--out") + 1] if "--out" in a else None
    only = set(a[a.index("--only") + 1].split(",")) if "--only" in a else None
    skip = set(a[a.index("--skip") + 1].split(",")) if "--skip" in a else None
    res = run_all(out, only, skip)
    tp = sum(len(r["passed"]) for r in res.values())
    tf = sum(len(r["failed"]) for r in res.values())
    for k, r in res.items():
        print(f"{k:32s} {len(r['passed']):4d} ok {len(r['failed']):3d} fail  {r['secs']}s")
    print(f"RESULT: {tp} passed, {tf} failed")
    sys.exit(1 if tf else 0)
