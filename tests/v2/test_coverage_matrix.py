#!/usr/bin/env python3
"""Capability-coverage test: every capability from the internal reference inventory
is present in Coordn8 at parity or better.

Each row of tests/coverage_matrix.json must name: the feature, the files that
implement it (all must exist), the evidence checks (suite::check, all must have
PASSED in this run), and a verdict of `parity` or `better`. `better` additionally
needs `better_evidence` checks that passed and a `why_better` sentence.
The matrix itself carries capabilities only (no project names).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import ROOT, Suite  # noqa: E402
import _runall  # noqa: E402

MATRIX = ROOT / "tests" / "coverage_matrix.json"


def main() -> int:
    s = Suite("coverage_matrix")
    m = json.loads(MATRIX.read_text(encoding="utf-8"))
    rows = m["rows"]
    s.check("the matrix lists every inventoried capability (>= 79 rows)", len(rows) >= 79, str(len(rows)))
    allres = _runall.load_or_run()
    passed = _runall.passed_names(allres)
    bad = []
    counts = {"parity": 0, "better": 0}
    for r in rows:
        why = []
        if r.get("verdict") not in ("parity", "better"):
            why.append(f"verdict={r.get('verdict')}")
        if not r.get("feature"):
            why.append("no feature")
        if not r.get("files"):
            why.append("no files")
        for f in r.get("files", []):
            if not (ROOT / f).exists():
                why.append(f"missing file {f}")
        if not r.get("evidence"):
            why.append("no evidence")
        for e in r.get("evidence", []):
            if e not in passed:
                why.append(f"evidence not passing: {e}")
        if r.get("verdict") == "better":
            if not r.get("better_evidence") or not r.get("why_better"):
                why.append("better without better_evidence/why_better")
            for e in r.get("better_evidence", []):
                if e not in passed:
                    why.append(f"better evidence not passing: {e}")
        if why:
            bad.append(f"{r['id']} ({r['capability'][:40]}): {', '.join(why[:3])}")
        else:
            counts[r["verdict"]] += 1
    s.check("every capability row is implemented and ends at parity or better",
            not bad, f"{len(bad)} rows open: " + " | ".join(bad[:8]))
    print(f"  coverage: {counts['better']} better, {counts['parity']} parity, {len(bad)} open of {len(rows)}")
    s.check("no row is marked worse", all(r.get("verdict") != "worse" for r in rows), "")
    return s.finish()


if __name__ == "__main__":
    sys.exit(main())
