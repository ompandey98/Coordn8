#!/usr/bin/env python3
"""
Coordn8 gate — TREE-SHAPE (webpage).

The single most common way the interactive page goes wrong is linearizing a
branching engine: rendering a decision tree as a straight chain of boxes. This
gate reads the flow graphs and flags any flow whose branching is too thin for
the number of distinct outcomes it is supposed to produce.

ADVISORY: exit 1 (REVIEW) means "look at these flows" — it is a lead, not a
verdict. If the helper misjudges THIS project's real shape, the agent writes a
project-specific check (see PLAYBOOK.md 1.2). It never forces a linear chain to
ship, and it never overrides a genuinely-correct flow.

Data contract — flows.json (a DEFAULT shape; deviate if the project needs it):
{
  "flows": [
    {
      "id": "unit-1",
      "title": "Refund eligibility",
      "expected_outcomes": 5,          # distinct terminal results per project_map
      "nodes": [
        {"id": "n1", "kind": "branch",   "label": "Is the order within 30 days?"},
        {"id": "n2", "kind": "terminal", "label": "Approve refund + code R1"},
        ...
      ],
      "edges": [
        {"from": "n1", "to": "n2", "when": "within 30 days"},
        ...
      ]
    }
  ]
}

node.kind in {"branch", "terminal", "process", "start"}.

Usage:
    python3 scripts/gates/tree_shape.py path/to/flows.json
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.common import (EXIT_ERROR, EXIT_PASS, EXIT_REVIEW, EXIT_SKIPPED,  # noqa: E402
                        emit, load_json)


def analyze_flow(flow: dict) -> dict:
    fid = flow.get("id", "?")
    title = flow.get("title", fid)
    nodes = flow.get("nodes", [])
    edges = flow.get("edges", [])
    expected = int(flow.get("expected_outcomes", 0))

    branch_nodes = [n for n in nodes if n.get("kind") == "branch"]
    terminal_nodes = [n for n in nodes if n.get("kind") == "terminal"]
    n_branch = len(branch_nodes)
    n_term = len(terminal_nodes)

    findings = []

    # 1) Terminals should cover the distinct outcomes the flow can produce.
    if expected and n_term < expected:
        findings.append(
            f"only {n_term} terminal node(s) for {expected} expected distinct "
            f"outcomes — outcomes are missing or collapsed."
        )

    # 2) A branching engine needs branch nodes. With >=2 outcomes and 0 branches
    #    it is almost certainly a linear chain.
    if expected >= 2 and n_branch == 0:
        findings.append(
            "flow has multiple outcomes but ZERO branch nodes — this is a linear "
            "chain, not a decision tree."
        )

    # 3) Every branch must have >=2 labeled outgoing edges (a real fork).
    out_by_node: dict[str, list] = {}
    for e in edges:
        out_by_node.setdefault(e.get("from"), []).append(e)
    for b in branch_nodes:
        outs = out_by_node.get(b.get("id"), [])
        if len(outs) < 2:
            findings.append(
                f"branch node '{b.get('label', b.get('id'))}' has "
                f"{len(outs)} exit(s); a fork needs >=2."
            )
        unlabeled = [e for e in outs if not str(e.get("when", "")).strip()]
        if unlabeled:
            findings.append(
                f"branch node '{b.get('label', b.get('id'))}' has "
                f"{len(unlabeled)} unlabeled exit(s); every exit needs a plain-"
                f"words 'when' condition."
            )

    # 4) Heuristic: a healthy tree has roughly (terminals - 1) internal forks.
    #    Far fewer branches than terminals-1 suggests hidden collapsing.
    if n_term >= 3 and n_branch < (n_term - 1) / 2:
        findings.append(
            f"{n_branch} branch node(s) for {n_term} terminals looks too flat — "
            f"verify no forks were collapsed."
        )

    return {
        "id": fid,
        "title": title,
        "branch_nodes": n_branch,
        "terminal_nodes": n_term,
        "expected_outcomes": expected,
        "findings": findings,
        "ok": not findings,
    }


def main() -> int:
    if len(sys.argv) < 2:
        emit({"gate": "tree_shape", "status": "ERROR",
              "summary": "usage: tree_shape.py <flows.json>"})
        return EXIT_ERROR
    path = Path(sys.argv[1])
    if not path.exists():
        emit({"gate": "tree_shape", "status": "SKIPPED",
              "summary": f"no flows file at {path} — nothing to check yet"})
        return EXIT_SKIPPED
    try:
        data = load_json(path)
    except Exception as e:
        emit({"gate": "tree_shape", "status": "ERROR",
              "summary": f"could not parse {path}: {e}"})
        return EXIT_ERROR

    flows = data.get("flows", [])
    if not flows:
        emit({"gate": "tree_shape", "status": "SKIPPED",
              "summary": "flows.json has no flows[] — nothing to check"})
        return EXIT_SKIPPED

    results = [analyze_flow(f) for f in flows]
    bad = [r for r in results if not r["ok"]]

    emit({
        "gate": "tree_shape",
        "status": "REVIEW" if bad else "PASS",
        "flows_checked": len(results),
        "flows_flagged": len(bad),
        "results": results,
        "summary": (
            f"{len(bad)}/{len(results)} flow(s) need review (possible linear "
            f"chains / missing outcomes)" if bad
            else f"all {len(results)} flow(s) look like real decision trees"
        ),
        "advisory_note": "REVIEW is a lead, not a hard fail. Re-check flagged "
                         "flows against the code; fix the diagram data or the "
                         "gate's expectation, not by hiding branches.",
    })
    return EXIT_REVIEW if bad else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
