#!/usr/bin/env python3
"""
Coordn8 gate — DIAGRAM COMPLEXITY BUDGET (B2). Advisory.

A diagram over budget stops being read. Global caps per figure: <= 9 nodes, <= 12 edges,
<= 2 accents, <= 2 callouts. A type may override them (data/diagram_types.json `budget`:
sequence = 5 lifelines, scatter = 30 points, ...). Over budget -> REVIEW with the advice
"split into overview + detail" and a PROPOSED split, computed by clustering the figure's own
graph by connectivity (each detail figure within the cap, an overview with one node per cluster).

Freedom (H1): this gate judges an OUTCOME (how much is on one figure). It never asks whether a
type is listed anywhere: an invented form, or any type the catalog has no entry for, simply gets
the global caps. A zoned, high-detail (faithful) redraw may hold up to 24 nodes / 32 edges.

Input (any of):
  <build>/diagram_plan.json or a plan file   {"figures": [{id, type, invented, nodes, edges, accents,
                                               callouts, kept?, edges_kept?, detail?, zones?}, ...]}
  a single figure JSON                       {nodes, edges, accents, callouts, type?}
     nodes / edges may be counts or lists ({"nodes": ["a","b"], "edges": [["a","b"]]}).
  a build directory                          (reads diagram_plan.json in it)

Usage:
  python3 scripts/gates/check_diagram_budget.py <plan.json|figure.json|build-dir> [--figure ID]
         [--catalog PATH]
Exit: 0 PASS · 1 REVIEW · 2 SKIPPED (no input) · 3 ERROR (unreadable input)
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.common import EXIT_ERROR, EXIT_PASS, EXIT_REVIEW, EXIT_SKIPPED, emit  # noqa: E402

PKG_ROOT = Path(__file__).resolve().parents[2]
CATALOG = PKG_ROOT / "data" / "diagram_types.json"
GLOBAL = {"nodes": 9, "edges": 12, "accents": 2, "callouts": 2}
FAITHFUL = {"detail": "high", "min_zones": 2, "nodes": 24, "edges": 32}
ADVICE = "split into overview + detail"


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def load_overrides(path=None) -> tuple:
    """Per-type budgets keyed by type id, plus the global caps. Missing catalog -> globals only."""
    p = Path(path) if path else CATALOG
    try:
        cat = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(GLOBAL), {}, dict(FAITHFUL)
    g = dict(GLOBAL)
    g.update({k: v for k, v in (cat.get("global_budget") or {}).items() if _num(v)})
    faithful = dict(FAITHFUL)
    faithful.update(cat.get("faithful_exemption") or {})
    per = {t["id"]: t.get("budget", {}) for t in cat.get("types", []) if isinstance(t, dict) and "id" in t}
    return g, per, faithful


def caps_for(fig: dict, g: dict, per: dict, faithful: dict) -> tuple:
    """Caps that apply to this figure, and where they came from. A type with no catalog entry gets
    the global caps — catalog absence is never itself a finding."""
    caps = dict(g)
    source = "global"
    inv = fig.get("invented")
    tid = fig.get("type")
    if not inv and isinstance(tid, str) and tid in per:
        caps.update({k: v for k, v in per[tid].items() if _num(v)})
        source = f"type:{tid}"
    elif inv:
        source = "global (invented form)"
        if isinstance(inv, dict) and isinstance(inv.get("budget"), dict):
            caps.update({k: v for k, v in inv["budget"].items() if _num(v)})
            source = "invented form's declared budget"
    zones = fig.get("zones")
    nz = len(zones) if isinstance(zones, list) else (zones if _num(zones) else 0)
    if str(fig.get("detail", "")).lower() == faithful.get("detail", "high") and nz >= faithful.get("min_zones", 2):
        caps["nodes"] = max(caps["nodes"], faithful.get("nodes", 24))
        caps["edges"] = max(caps["edges"], faithful.get("edges", 32))
        source += " + faithful zoned exemption"
    return caps, source


def graph_of(fig: dict):
    """(node_ids or None, edge pairs or None, n_nodes, n_edges)."""
    nodes = fig.get("kept") if isinstance(fig.get("kept"), list) else None
    if nodes is None and isinstance(fig.get("nodes"), list):
        nodes = [n["id"] if isinstance(n, dict) else n for n in fig["nodes"]]
    edges = None
    raw = fig.get("edges_kept") if isinstance(fig.get("edges_kept"), list) else (
        fig.get("edges") if isinstance(fig.get("edges"), list) else None)
    if raw is not None:
        edges = []
        for e in raw:
            if isinstance(e, dict):
                a, b = e.get("from"), e.get("to")
            elif isinstance(e, (list, tuple)) and len(e) >= 2:
                a, b = e[0], e[1]
            else:
                continue
            edges.append((str(a), str(b)))
    if nodes is not None:
        nodes = [str(n) for n in nodes]
    n_nodes = len(nodes) if nodes is not None else (int(fig["nodes"]) if _num(fig.get("nodes")) else 0)
    n_edges = len(edges) if edges is not None else (int(fig["edges"]) if _num(fig.get("edges")) else 0)
    if nodes is not None and edges is not None:
        seen = set(nodes)
        for a, b in edges:
            for x in (a, b):
                if x not in seen:
                    nodes.append(x)
                    seen.add(x)
        n_nodes = len(nodes)
    return nodes, edges, n_nodes, n_edges


def propose_split(nodes, edges, n_nodes: int, cap: int, edge_cap: int) -> dict:
    """Cluster by connectivity: grow each cluster from the best-connected unassigned node,
    adding the neighbour with the most links into the cluster, until the cap. Deterministic."""
    cap = max(2, int(cap))
    if not nodes:
        k = max(2, int(math.ceil(n_nodes / float(cap))))
        size = int(math.ceil(n_nodes / float(k)))
        return {"method": "count-only (no graph given)", "overview": {"nodes": k},
                "details": [{"nodes": min(size, n_nodes - i * size)} for i in range(k)],
                "note": f"{n_nodes} nodes -> an overview of {k} groups + {k} detail figures of about {size}"}
    order = {n: i for i, n in enumerate(nodes)}
    adj = {n: set() for n in nodes}
    for a, b in edges or []:
        if a in adj and b in adj and a != b:
            adj[a].add(b)
            adj[b].add(a)
    unassigned = list(nodes)
    clusters = []
    while unassigned:
        pool = set(unassigned)
        seed = max(unassigned, key=lambda n: (len(adj[n] & pool), -order[n]))
        cl = [seed]
        inside = {seed}
        while len(cl) < cap:
            frontier = [n for n in unassigned if n not in inside and adj[n] & inside]
            if not frontier:
                break
            best = max(frontier, key=lambda n: (len(adj[n] & inside), -order[n]))
            cl.append(best)
            inside.add(best)
        clusters.append(sorted(cl, key=lambda n: order[n]))
        unassigned = [n for n in unassigned if n not in inside]
    # fold singletons into a neighbouring cluster with room
    merged = []
    for cl in clusters:
        if len(cl) == 1 and merged:
            host = next((m for m in merged if len(m) < cap and adj[cl[0]] & set(m)), None)
            if host is not None:
                host.append(cl[0])
                continue
        merged.append(cl)
    clusters = merged
    where = {n: i for i, cl in enumerate(clusters) for n in cl}
    cross = {}
    inner = [0] * len(clusters)
    for a, b in edges or []:
        if a not in where or b not in where:
            continue
        ia, ib = where[a], where[b]
        if ia == ib:
            inner[ia] += 1
        else:
            key = (min(ia, ib), max(ia, ib))
            cross[key] = cross.get(key, 0) + 1
    details = [{"id": f"detail-{i + 1}", "nodes": cl, "node_count": len(cl), "edge_count": inner[i],
                "within_budget": len(cl) <= cap and inner[i] <= edge_cap} for i, cl in enumerate(clusters)]
    overview = {"nodes": [f"group-{i + 1}" for i in range(len(clusters))],
                "edges": [{"from": f"group-{a + 1}", "to": f"group-{b + 1}", "links": c}
                          for (a, b), c in sorted(cross.items())]}
    overview["within_budget"] = len(clusters) <= cap and len(overview["edges"]) <= edge_cap
    return {"method": "connectivity clusters", "overview": overview, "details": details,
            "note": f"{len(nodes)} nodes -> overview of {len(clusters)} groups + {len(clusters)} detail figures"}


def check_figure(fig: dict, g: dict, per: dict, faithful: dict) -> dict:
    caps, source = caps_for(fig, g, per, faithful)
    nodes, edges, n_nodes, n_edges = graph_of(fig)
    counts = {"nodes": n_nodes, "edges": n_edges,
              "accents": int(fig.get("accents") or 0) if _num(fig.get("accents") or 0) else 0,
              "callouts": int(fig.get("callouts") or 0) if _num(fig.get("callouts") or 0) else 0}
    over = {k: [counts[k], caps[k]] for k in ("nodes", "edges", "accents", "callouts") if counts[k] > caps[k]}
    for k, cap in caps.items():  # extra per-type limits the figure reports (lanes, lifelines...)
        if k in counts or not _num(cap) or not _num(fig.get(k)):
            continue
        if fig[k] > cap:
            over[k] = [fig[k], cap]
    res = {"id": fig.get("id", "?"), "type": fig.get("type"), "invented": bool(fig.get("invented")),
           "caps": caps, "caps_from": source, "counts": counts, "over": over}
    if "nodes" in over or "edges" in over:
        res["advice"] = ADVICE
        res["proposed_split"] = propose_split(nodes, edges, n_nodes, caps["nodes"], caps["edges"])
    if "accents" in over:
        res["accent_advice"] = f"keep accent on at most {caps['accents']} focal elements; pick what is focal"
    if "callouts" in over:
        res["callout_advice"] = f"keep at most {caps['callouts']} callouts; move the rest to the caption"
    return res


def figures_from(doc) -> list:
    if isinstance(doc, dict) and isinstance(doc.get("figures"), list):
        return [f for f in doc["figures"] if isinstance(f, dict)]
    if isinstance(doc, dict):
        return [doc]
    if isinstance(doc, list):
        return [f for f in doc if isinstance(f, dict)]
    return []


def _arg(argv, name):
    if name in argv:
        i = argv.index(name)
        return argv[i + 1] if i + 1 < len(argv) else None
    return None


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return EXIT_ERROR
    src = Path(argv[0])
    if src.is_dir():
        src = src / "diagram_plan.json"
    if not src.exists():
        emit({"gate": "diagram_budget", "status": "SKIPPED", "summary": f"no plan or figure at {src}"})
        return EXIT_SKIPPED
    try:
        doc = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        emit({"gate": "diagram_budget", "status": "ERROR", "summary": f"cannot read {src}: {e}"})
        return EXIT_ERROR
    g, per, faithful = load_overrides(_arg(argv, "--catalog"))
    figs = figures_from(doc)
    only = _arg(argv, "--figure")
    if only:
        figs = [f for f in figs if f.get("id") == only]
    if not figs:
        emit({"gate": "diagram_budget", "status": "SKIPPED", "summary": "no figures to check"})
        return EXIT_SKIPPED
    results = [check_figure(f, g, per, faithful) for f in figs]
    bad = [r for r in results if r["over"]]
    emit({"gate": "diagram_budget", "status": "REVIEW" if bad else "PASS",
          "summary": (f"{len(bad)}/{len(results)} figure(s) over budget — {ADVICE}" if bad
                      else f"{len(results)} figure(s) within budget"),
          "measures": "node/edge/accent/callout counts per figure against caps; says nothing about quality",
          "figures": results})
    return EXIT_REVIEW if bad else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
