#!/usr/bin/env python3
"""
Coordn8 gate — FACTS TRACE (both deliverables).

Every claim/number that appears in a deliverable must trace back to the memory
palace (`_research/project_map.md`) with a file:line reference. This gate checks
a claims manifest against the project map and flags claims whose reference does
not resolve to a real file:line in the repo.

ADVISORY: a flagged claim is a lead — the agent confirms whether the reference
is wrong, the file moved, or the claim is unsupported. It never rewrites claims.

Data contract — claims.json (default shape; deviate if needed):
{
  "repo_root": ".",
  "claims": [
    {"id": "c1", "text": "Handles 5 refund outcomes",
     "ref": "src/engine.py:120", "kind": "number"},
    {"id": "c2", "text": "Routes by channel", "ref": "src/router.py:42"}
  ]
}

Usage:
    python3 scripts/gates/facts_trace.py claims.json --repo .
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.common import (EXIT_ERROR, EXIT_PASS, EXIT_REVIEW, EXIT_SKIPPED,  # noqa: E402
                        emit, load_json)

REF_RE = re.compile(r"^(?P<file>.+?):(?P<line>\d+)(?:-(?P<end>\d+))?$")


def resolve_ref(repo: Path, ref: str) -> tuple[bool, str]:
    m = REF_RE.match(ref.strip())
    if not m:
        # allow a bare file reference (no line)
        p = repo / ref.strip()
        if p.exists():
            return True, "file exists (no line given)"
        return False, "reference is not 'file:line' and file does not exist"
    fpath = repo / m.group("file")
    if not fpath.exists():
        return False, f"file not found: {m.group('file')}"
    try:
        n_lines = sum(1 for _ in fpath.open("r", encoding="utf-8", errors="replace"))
    except Exception as e:  # noqa: BLE001
        return False, f"could not read file: {e}"
    line = int(m.group("line"))
    end = int(m.group("end")) if m.group("end") else line
    if line < 1 or end > n_lines:
        return False, f"line {line}-{end} out of range (file has {n_lines} lines)"
    return True, f"resolves ({n_lines} lines)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("claims")
    ap.add_argument("--repo", default=".")
    args = ap.parse_args()

    cpath = Path(args.claims)
    if not cpath.exists():
        emit({"gate": "facts_trace", "status": "SKIPPED",
              "summary": f"no claims manifest at {cpath} yet"})
        return EXIT_SKIPPED
    try:
        data = load_json(cpath)
    except Exception as e:
        emit({"gate": "facts_trace", "status": "ERROR",
              "summary": f"bad claims manifest: {e}"})
        return EXIT_ERROR

    repo = Path(data.get("repo_root", args.repo)).resolve()
    claims = data.get("claims", [])
    if not claims:
        emit({"gate": "facts_trace", "status": "SKIPPED",
              "summary": "no claims[] to check"})
        return EXIT_SKIPPED

    findings = []
    checked = []
    for c in claims:
        ref = c.get("ref", "")
        if not ref:
            findings.append({"id": c.get("id"), "text": c.get("text", "")[:80],
                             "problem": "claim has no file:line reference"})
            continue
        ok, why = resolve_ref(repo, ref)
        checked.append({"id": c.get("id"), "ref": ref, "ok": ok, "why": why})
        if not ok:
            findings.append({"id": c.get("id"), "text": c.get("text", "")[:80],
                             "ref": ref, "problem": why})

    emit({
        "gate": "facts_trace",
        "status": "REVIEW" if findings else "PASS",
        "repo": str(repo),
        "claims_checked": len(claims),
        "unresolved": len(findings),
        "detail": checked,
        "findings": findings,
        "summary": (f"{len(findings)}/{len(claims)} claim reference(s) do not "
                    f"resolve — review" if findings
                    else f"all {len(claims)} claim references resolve to real "
                         f"file:line"),
        "advisory_note": "Resolving to a real line proves the reference exists, "
                         "NOT that the line actually supports the claim. Spot-"
                         "check load-bearing claims by reading the code.",
    })
    return EXIT_REVIEW if findings else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
