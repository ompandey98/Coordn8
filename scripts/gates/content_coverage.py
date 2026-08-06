#!/usr/bin/env python3
"""
Coordn8 gate — CONTENT COVERAGE (the completeness backstop).

Content's counterpart to the visual gates. The visual gates (visual_density,
layout_probe) stop a deliverable that is *sparse or ugly*; THIS gate stops a
deliverable that is *incomplete* — one that silently dropped something the user
asked for, or a core object the code demands be covered (PLAYBOOK 0.4b / 0.7a).

It reads the requirement-coverage ledger `_research/requirements.md` — a markdown
table the agent builds at Phase 0 with one row per distinct ask / requirement /
core object:

  | id | requirement | in_scope | importance | status | where_covered | notes |

and flags, as LEADS the agent clears:
  - any IN-SCOPE `must`/`should` row whose status is not `covered`
    (an OPEN/partial/missing in-scope requirement blocks "done"),
  - any `covered` row with NO `where_covered` anchor (a claim of coverage that
    points nowhere is the exact "confident it's all there" failure — 0.4b rule 5),
  - `deferred`/`out-of-scope` rows, surfaced as an FYI so a silent dumping-ground
    can't hide there (each SHOULD carry a reason; the agent confirms it was a real
    user deferral logged in decisions.md, not "couldn't fit it").

ADVISORY (see lib.common): a regex/parser cannot know the deliverable truly
teaches a row — only that the ledger says so with an anchor. Exit 1 (REVIEW) =
"these in-scope items look uncovered — go confirm each is actually taught."
The agent (and the coverage/completeness critic, R.1) still reads the finished
narration/page against this ledger; a PASS here is necessary, never sufficient.

Column names are matched leniently (case/space/underscore-insensitive, common
aliases accepted) so a hand-authored ledger isn't rejected on a header typo.

Usage:
    python3 scripts/gates/content_coverage.py Coordn8/_research/requirements.md
    python3 scripts/gates/content_coverage.py _research/requirements.md --json
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.common import (EXIT_ERROR, EXIT_PASS, EXIT_REVIEW, EXIT_SKIPPED,  # noqa: E402
                        emit)

# Canonical column -> the header spellings we accept for it. Matching is done on
# a normalized header (lowercased, non-alphanumerics stripped), so "In Scope?",
# "in_scope", and "inscope" all collapse to the same key.
COLUMN_ALIASES = {
    "id": {"id", "no", "num", "ref"},
    "requirement": {"requirement", "requirements", "ask", "item", "what",
                    "description", "need"},
    "in_scope": {"inscope", "scope", "inscopeq", "scoped"},
    "importance": {"importance", "priority", "prio", "weight", "severity"},
    "status": {"status", "state", "coverage", "covered"},
    "where_covered": {"wherecovered", "where", "location", "coveredin",
                      "section", "scenesection", "anchor", "sectionscene"},
    "notes": {"notes", "note", "comment", "comments", "reason", "why"},
}

# status values that count as "this requirement is genuinely handled"
STATUS_COVERED = {"covered", "done", "complete", "completed", "yes", "y",
                  "full", "fully"}
STATUS_DEFERRED = {"deferred", "deferredbyuser", "deferred-by-user", "later",
                   "postponed", "punted"}
STATUS_OUT = {"outofscope", "out-of-scope", "oos", "n/a", "na", "excluded"}
STATUS_PARTIAL = {"partial", "partially", "inprogress", "in-progress", "wip",
                  "started"}
STATUS_OPEN = {"open", "todo", "missing", "uncovered", "none", "", "-", "pending"}

IMPORTANCE_MUST = {"must", "required", "critical", "p0", "high", "mustcover"}
IMPORTANCE_SHOULD = {"should", "important", "p1", "medium", "med"}
IMPORTANCE_EDGE = {"edge", "nice", "nicetohave", "optional", "p2", "low",
                   "peripheral"}

TRUEY = {"yes", "y", "true", "1", "in", "inscope", "✓", "x"}
FALSEY = {"no", "n", "false", "0", "out", "outofscope", ""}


def _norm(s: str) -> str:
    """Lowercase + strip everything but a-z0-9 for tolerant header/value matching."""
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _split_row(line: str) -> list[str]:
    """Split a markdown table row into trimmed cells (drop the outer pipes)."""
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def _is_separator(cells: list[str]) -> bool:
    """A markdown header separator row: every cell is like ---, :--, --:."""
    return bool(cells) and all(re.fullmatch(r":?-{2,}:?", c.strip() or "-")
                               for c in cells)


def _map_headers(header_cells: list[str]) -> dict[str, int]:
    """Map canonical column name -> its index in this table (lenient)."""
    idx: dict[str, int] = {}
    for i, h in enumerate(header_cells):
        nh = _norm(h)
        for canon, aliases in COLUMN_ALIASES.items():
            if canon in idx:
                continue
            if nh == canon or nh in aliases:
                idx[canon] = i
                break
    return idx


def parse_ledger(text: str):
    """Find the FIRST markdown table that has a 'requirement' column and parse
    its data rows into dicts keyed by canonical column name."""
    lines = text.splitlines()
    # locate a header row followed by a separator row
    for i in range(len(lines) - 1):
        if "|" not in lines[i]:
            continue
        header = _split_row(lines[i])
        nxt = _split_row(lines[i + 1])
        if not _is_separator(nxt):
            continue
        cols = _map_headers(header)
        if "requirement" not in cols:
            continue  # not the ledger table; keep scanning
        rows = []
        for line in lines[i + 2:]:
            if "|" not in line:
                if line.strip() == "":
                    continue  # allow blank lines inside/after; stop on next non-table? keep simple: skip
                else:
                    break  # table ended at a non-table, non-blank line
            cells = _split_row(line)
            if _is_separator(cells):
                continue
            if not any(c.strip() for c in cells):
                continue
            row = {}
            for canon, ci in cols.items():
                row[canon] = cells[ci].strip() if ci < len(cells) else ""
            rows.append(row)
        return cols, rows
    return {}, []


def _truthy_scope(val: str) -> bool:
    n = _norm(val)
    if n in {_norm(x) for x in FALSEY}:
        return False
    # default: a row with no explicit scope marker is treated as IN scope
    # (safer for completeness — an unmarked ask is assumed to matter).
    return True


def classify_status(val: str) -> str:
    n = _norm(val)
    if n in {_norm(x) for x in STATUS_COVERED}:
        return "covered"
    if n in {_norm(x) for x in STATUS_DEFERRED}:
        return "deferred"
    if n in {_norm(x) for x in STATUS_OUT}:
        return "out"
    if n in {_norm(x) for x in STATUS_PARTIAL}:
        return "partial"
    return "open"  # includes explicit OPEN and anything unrecognized/blank


def classify_importance(val: str) -> str:
    n = _norm(val)
    if n in {_norm(x) for x in IMPORTANCE_EDGE}:
        return "edge"
    if n in {_norm(x) for x in IMPORTANCE_SHOULD}:
        return "should"
    if n in {_norm(x) for x in IMPORTANCE_MUST}:
        return "must"
    return "must"  # unmarked importance defaults to MUST (fail-safe toward coverage)


def evaluate(rows: list[dict]):
    """Return (findings, stats). findings are leads the agent clears."""
    findings = []
    stats = {"total": len(rows), "covered": 0, "partial": 0, "open": 0,
             "deferred": 0, "out": 0, "in_scope_must_should": 0,
             "in_scope_uncovered": 0, "covered_no_anchor": 0}

    for r in rows:
        rid = r.get("id", "") or r.get("requirement", "")[:32]
        req = r.get("requirement", "")
        in_scope = _truthy_scope(r.get("in_scope", ""))
        status = classify_status(r.get("status", ""))
        importance = classify_importance(r.get("importance", ""))
        where = r.get("where_covered", "").strip()

        stats[status] = stats.get(status, 0) + 1

        # a covered row must point somewhere — an anchorless "covered" is the
        # exact confident-but-empty claim 0.4b rule 5 warns about.
        if status == "covered" and not where:
            stats["covered_no_anchor"] += 1
            findings.append({
                "id": rid, "kind": "covered_without_anchor",
                "importance": importance, "in_scope": in_scope,
                "requirement": req[:120],
                "why": "status=covered but where_covered is empty — a coverage "
                       "claim that points to no section/scene is unverifiable",
            })

        if in_scope and importance in ("must", "should"):
            stats["in_scope_must_should"] += 1
            if status in ("open", "partial"):
                stats["in_scope_uncovered"] += 1
                findings.append({
                    "id": rid, "kind": "in_scope_uncovered",
                    "importance": importance, "status": status,
                    "requirement": req[:120],
                    "why": f"in-scope {importance} requirement is '{status}', "
                           f"not 'covered' — blocks done until covered or the "
                           f"user explicitly defers it (logged in decisions.md)",
                })

        # surface deferrals/out-of-scope as FYI so nothing hides there silently
        if status in ("deferred", "out") and in_scope:
            note = r.get("notes", "").strip()
            if not note:
                findings.append({
                    "id": rid, "kind": "deferral_without_reason",
                    "status": status, "requirement": req[:120],
                    "why": f"row is '{status}' but has no reason in notes — a "
                           f"deferral/out-of-scope MUST cite a logged user "
                           f"decision, never be a place to quietly drop content",
                })

    return findings, stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ledger", help="path to _research/requirements.md")
    ap.add_argument("--json", action="store_true",
                    help="(default output is already JSON; kept for symmetry)")
    args = ap.parse_args()

    path = Path(args.ledger)
    if not path.exists():
        emit({"gate": "content_coverage", "status": "SKIPPED",
              "summary": f"no ledger at {path} — build _research/requirements.md "
                         f"(PLAYBOOK 0.7a) so coverage can be verified"})
        return EXIT_SKIPPED

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        emit({"gate": "content_coverage", "status": "ERROR",
              "summary": f"cannot read {path}: {e}"})
        return EXIT_ERROR

    cols, rows = parse_ledger(text)
    if not rows:
        emit({"gate": "content_coverage", "status": "ERROR",
              "summary": "no requirement table found — expected a markdown table "
                         "with at least a 'requirement' column (see 0.7a). "
                         "Found columns: " + (", ".join(cols) or "none")})
        return EXIT_ERROR

    findings, stats = evaluate(rows)
    blocking = [f for f in findings if f["kind"] == "in_scope_uncovered"]

    emit({
        "gate": "content_coverage",
        "status": "REVIEW" if findings else "PASS",
        "ledger": str(path),
        "columns_found": list(cols),
        "stats": stats,
        "blocking_in_scope_gaps": len(blocking),
        "findings": findings[:200],
        "truncated": len(findings) > 200,
        "summary": (
            f"{stats['in_scope_uncovered']} in-scope must/should requirement(s) "
            f"NOT covered, {stats['covered_no_anchor']} covered-without-anchor, "
            f"across {stats['total']} ledger rows — review each"
            if findings else
            f"all {stats['in_scope_must_should']} in-scope must/should "
            f"requirement(s) covered with anchors ({stats['total']} rows total)"
        ),
        "advisory_note": "A PASS means the LEDGER says everything in-scope is "
                         "covered with an anchor — you still read the finished "
                         "narration/page against it (coverage critic, R.1). A "
                         "ledger that lies passes; your eyes are the real gate.",
    })
    return EXIT_REVIEW if findings else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
