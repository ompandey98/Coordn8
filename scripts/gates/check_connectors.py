#!/usr/bin/env python3
"""
Coordn8 gate — CONNECTOR & GEOMETRY RULES (B4). Advisory, like every Coordn8 gate.

Reads a layout JSON written by scripts/render/layout.js (layout_cli.js --json) and/or an
instrumentation trace from scripts/render/scene_runtime.js (frames -> boxes + connectors), and
judges the GEOMETRY of every connector against the connector rules:

  K1  through-box     a segment passes behind a node that is not its source or target
                      (or cuts through the interior of its own endpoint box)
  K2  label           a connector label with no opaque mask; a mask closer than 6 px or further
                      than 10 px from its own line; a mask covering another connector; a mask
                      clipped by a node painted after it
  K3  arrowhead       the arrow tip strictly inside its target beyond a 2 px tolerance (buried),
                      or more than 4 px short of the target's border (floating)
  K4  diagonal        a slanted segment where the layout, the kit or --orthogonal says orthogonal
  K5  parallel        two parallel segments of different connectors closer than 12 px over a
                      shared run longer than 4 px (8 px allowed on a box side shorter than 48 px)
  K6  hop             two connectors cross with no hop recorded at the crossing
  K7  shared port     two connectors leave (or two arrive at) one box side closer than 12 px
  K8  paint order     a group painted after one of its children, or edges/labels/nodes out of
                      the order groups -> edges -> labels -> nodes
  K9  containment     a node outside the group it belongs to, or inside a group it does not
  K10 border ride     a segment running along a node's border (it hides behind the node fill)
  K11 corner port     a connector attaching within 8 px of a box corner

It judges geometry only. It never consults a diagram-type catalog: an invented diagram form is
judged exactly like a catalogued one (freedom doctrine).

Usage:
    python3 scripts/gates/check_connectors.py <layout.json|trace.json> [more ...]
           [--kit KIT.json] [--orthogonal] [--require-hops]
Exit: 0 PASS, 1 REVIEW, 2 SKIPPED (no input), 3 ERROR (unreadable input).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.common import (EXIT_ERROR, EXIT_PASS, EXIT_REVIEW,  # noqa: E402
                        EXIT_SKIPPED, emit)

PARALLEL_MIN = 12.0      # parallel segments keep at least this far apart
SMALL_SIDE = 48.0        # ... on a side shorter than this
SMALL_MIN = 8.0          # ... 8 px is allowed
MIN_RUN = 4.0            # shorter shared runs are a touch, not a parallel run
LABEL_GAP_MIN = 6.0
LABEL_GAP_MAX = 10.0
ARROW_BURY_TOL = 2.0     # tip may sit this far inside the target border
ARROW_SHORT_TOL = 4.0    # ... or this far outside it
HOP_TOL = 3.0            # a hop within this distance of a crossing covers it
AXIS_TOL = 0.5
BORDER_TOL = 1.0
CORNER_CLEAR = 8.0
VISIBLE = 0.25           # trace connectors/boxes below this alpha are not judged

CODES = ("K1", "K2", "K3", "K4", "K5", "K6", "K7", "K8", "K9", "K10", "K11")
MEANINGS = {
    "K1": "segment passes behind a box that is not its endpoint",
    "K2": "label unmasked, off its 6-10 px band, covering a line, or clipped by a node",
    "K3": "arrowhead buried inside (or stopping short of) its target",
    "K4": "diagonal segment where routing is declared orthogonal",
    "K5": "parallel segments closer than 12 px",
    "K6": "crossing with no hop",
    "K7": "two connectors sharing one attach point",
    "K8": "paint order breaks groups -> edges -> labels -> nodes",
    "K9": "node outside its group, or inside a group it does not belong to",
    "K10": "segment riding along a box border",
    "K11": "connector attached within 8 px of a box corner",
}


# ----------------------------------------------------------------------------- input model
def _pt(p):
    if isinstance(p, dict):
        return float(p["x"]), float(p["y"])
    return float(p[0]), float(p[1])


def _rect(b) -> dict:
    return {"x": float(b["x"]), "y": float(b["y"]), "w": float(b["w"]), "h": float(b["h"])}


def model_from_layout(doc: dict) -> dict:
    nodes = {str(n["id"]): dict(_rect(n), group=n.get("group")) for n in doc.get("nodes") or []}
    groups = {str(g["id"]): dict(_rect(g), parent=g.get("parent")) for g in doc.get("groups") or []}
    edges = []
    for i, e in enumerate(doc.get("edges") or []):
        lab = e.get("label")
        if isinstance(lab, dict) and all(k in lab for k in ("x", "y", "w", "h")):
            label = dict(_rect(lab), mask=bool(lab.get("mask")), text=lab.get("text"))
        elif isinstance(lab, str) and lab:
            label = {"text": lab, "mask": False, "x": None}
        else:
            label = None
        arrow = e.get("arrow") or {}
        edges.append({
            "id": str(e.get("id", f"e{i}")), "from": str(e.get("from")), "to": str(e.get("to")),
            "points": [_pt(p) for p in e.get("points") or []],
            "arrowAt": e.get("arrowAt", "to"),
            "arrow_points": [_pt(p) for p in arrow.get("points") or []] or None,
            "label": label, "transit": bool(e.get("transit")),
        })
    return {"kind": "layout", "nodes": nodes, "groups": groups, "edges": edges,
            "hops": doc.get("hops") if isinstance(doc.get("hops"), list) else None,
            "paint_order": doc.get("paint_order") if isinstance(doc.get("paint_order"), list) else None,
            "orthogonal": doc.get("orthogonal") is True}


def models_from_trace(doc: dict) -> list:
    """One model per frame; only clearly visible nodes and connectors are judged."""
    out = []
    for fr in doc.get("frames") or []:
        nodes = {}
        for b in fr.get("boxes") or []:
            if b.get("id") and (b.get("alpha") or 0) > VISIBLE and b.get("kind") != "text":
                nodes[str(b["id"])] = dict(_rect(b), group=None)
        edges = []
        for i, c in enumerate(fr.get("connectors") or []):
            if (c.get("alpha") or 0) <= VISIBLE:
                continue
            edges.append({"id": f"{c.get('from')}->{c.get('to')}#{i}", "from": str(c.get("from")),
                          "to": str(c.get("to")), "points": [_pt(p) for p in c.get("points") or []],
                          "arrowAt": c.get("arrowAt", "to"), "arrow_points": None,
                          "label": None, "transit": False})
        out.append({"kind": "trace", "t": fr.get("t", 0.0), "nodes": nodes, "groups": {}, "edges": edges,
                    "hops": None, "paint_order": None, "orthogonal": False})
    return out


def kit_routing(path) -> tuple:
    """(routing, hop_at_crossings) from a style kit, a kit_export runtime JSON or a design_system."""
    try:
        k = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    if isinstance(k.get("kit_spec"), dict):
        k = k["kit_spec"]
    conn = ((k.get("props") or {}).get("connector")) if isinstance(k.get("props"), dict) else None
    if conn is None:
        conn = k.get("connector") if isinstance(k.get("connector"), dict) else {}
    return conn.get("routing"), conn.get("hop_at_crossings")


# ----------------------------------------------------------------------------- geometry
def segs(points):
    return [(points[i], points[i + 1], i) for i in range(len(points) - 1)]


def is_h(a, b):
    return abs(a[1] - b[1]) <= AXIS_TOL


def is_v(a, b):
    return abs(a[0] - b[0]) <= AXIS_TOL


def through_box(a, b, r, inset=1.0) -> bool:
    x0, y0, x1, y1 = r["x"] + inset, r["y"] + inset, r["x"] + r["w"] - inset, r["y"] + r["h"] - inset
    if x1 <= x0 or y1 <= y0:
        return False
    # Liang-Barsky clip of the segment against the open interior
    dx, dy = b[0] - a[0], b[1] - a[1]
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, a[0] - x0), (dx, x1 - a[0]), (-dy, a[1] - y0), (dy, y1 - a[1])):
        if p == 0:
            if q <= 0:
                return False
        else:
            t = q / p
            if p < 0:
                t0 = max(t0, t)
            else:
                t1 = min(t1, t)
    if t0 >= t1:
        return False
    return (t1 - t0) * math.hypot(dx, dy) > 0.5


def rect_seg_dist(r, a, b) -> float:
    if not (is_h(a, b) or is_v(a, b)):
        best = float("inf")
        for i in range(65):
            t = i / 64
            x, y = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
            best = min(best, math.hypot(max(r["x"] - x, 0, x - r["x"] - r["w"]), max(r["y"] - y, 0, y - r["y"] - r["h"])))
        return best
    sx0, sx1 = sorted((a[0], b[0]))
    sy0, sy1 = sorted((a[1], b[1]))
    dx = max(r["x"] - sx1, 0.0, sx0 - (r["x"] + r["w"]))
    dy = max(r["y"] - sy1, 0.0, sy0 - (r["y"] + r["h"]))
    return math.hypot(dx, dy)


def overlap_area(a, b) -> float:
    dx = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
    dy = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
    return dx * dy if dx > 0 and dy > 0 else 0.0


def contained(inner, outer, eps=0.5) -> bool:
    return (inner["x"] >= outer["x"] - eps and inner["y"] >= outer["y"] - eps and
            inner["x"] + inner["w"] <= outer["x"] + outer["w"] + eps and
            inner["y"] + inner["h"] <= outer["y"] + outer["h"] + eps)


def inside_depth(p, r) -> float:
    """>0: how far p lies inside r; <0: how far outside (Chebyshev to the border)."""
    x, y = p
    if r["x"] <= x <= r["x"] + r["w"] and r["y"] <= y <= r["y"] + r["h"]:
        return min(x - r["x"], r["x"] + r["w"] - x, y - r["y"], r["y"] + r["h"] - y)
    return -max(r["x"] - x, x - r["x"] - r["w"], r["y"] - y, y - r["y"] - r["h"], 0.0)


def side_of(p, r):
    d = {"top": abs(p[1] - r["y"]), "bottom": abs(p[1] - r["y"] - r["h"]),
         "left": abs(p[0] - r["x"]), "right": abs(p[0] - r["x"] - r["w"])}
    s = min(d, key=lambda k: (d[k], k))
    return s, (r["w"] if s in ("top", "bottom") else r["h"]), (p[0] if s in ("top", "bottom") else p[1])


def attached(p, r, tol=ARROW_SHORT_TOL) -> bool:
    return abs(inside_depth(p, r)) <= tol


def crossing(s, t):
    """Proper crossing point of two segments (interior to both by > 1 px), else None."""
    (a, b, _), (c, d, _) = s, t
    r = (b[0] - a[0], b[1] - a[1])
    q = (d[0] - c[0], d[1] - c[1])
    den = r[0] * q[1] - r[1] * q[0]
    if abs(den) < 1e-9:
        return None
    u = ((c[0] - a[0]) * q[1] - (c[1] - a[1]) * q[0]) / den
    v = ((c[0] - a[0]) * r[1] - (c[1] - a[1]) * r[0]) / den
    la, lc = math.hypot(*r), math.hypot(*q)
    if la == 0 or lc == 0:
        return None
    if 1.0 / la < u < 1 - 1.0 / la and 1.0 / lc < v < 1 - 1.0 / lc:
        return a[0] + u * r[0], a[1] + u * r[1]
    return None


# ----------------------------------------------------------------------------- the checks
def run_checks(m: dict, orthogonal: bool = False, require_hops: bool = False) -> dict:
    f = {k: [] for k in CODES}
    not_run = {}
    nodes, groups, edges = m["nodes"], m["groups"], m["edges"]
    ortho = orthogonal or m.get("orthogonal")
    for e in edges:
        pts = e["points"]
        if len(pts) < 2:
            f["K3"].append(f"edge {e['id']} has fewer than two points")
            continue
        S = segs(pts)
        # K1 — through an unrelated box (or the interior of its own endpoint boxes)
        for a, b, i in S:
            for nid, r in nodes.items():
                if through_box(a, b, r):
                    if nid in (e["from"], e["to"]):
                        if through_box(a, b, r, inset=ARROW_BURY_TOL + 0.5):
                            f["K1"].append(f"edge {e['id']} segment {i} cuts through the interior of its own endpoint '{nid}'")
                    elif e.get("transit"):
                        continue  # declared, dashed transit past a cross-cutting box (rule exception)
                    else:
                        f["K1"].append(f"edge {e['id']} ({e['from']}->{e['to']}) segment {i} "
                                       f"({a[0]:g},{a[1]:g})->({b[0]:g},{b[1]:g}) passes behind unrelated node '{nid}'")
        # K4 — diagonal where orthogonal is declared
        if ortho:
            for a, b, i in S:
                if not is_h(a, b) and not is_v(a, b):
                    f["K4"].append(f"edge {e['id']} segment {i} ({a[0]:g},{a[1]:g})->({b[0]:g},{b[1]:g}) is diagonal "
                                   f"but routing is orthogonal")
                    break
        # K3 — arrowhead on the target border
        at_to = e.get("arrowAt", "to") != "from"
        tip = pts[-1] if at_to else pts[0]
        target = nodes.get(e["to"] if at_to else e["from"])
        if target:
            dep = inside_depth(tip, target)
            if dep > ARROW_BURY_TOL:
                f["K3"].append(f"edge {e['id']} arrowhead is buried {dep:.1f} px inside its target "
                               f"'{e['to'] if at_to else e['from']}' (tolerance {ARROW_BURY_TOL:g})")
            elif dep < -ARROW_SHORT_TOL:
                f["K3"].append(f"edge {e['id']} arrowhead stops {-dep:.1f} px short of its target's border")
            for ap in e.get("arrow_points") or []:
                if inside_depth(ap, target) > ARROW_BURY_TOL:
                    f["K3"].append(f"edge {e['id']} arrowhead polygon reaches {inside_depth(ap, target):.1f} px inside its target")
                    break
        # K10 / K11 — border rides and corner ports (only on real boxes)
        for a, b, i in S:
            for nid, r in nodes.items():
                if is_h(a, b):
                    lo, hi = sorted((a[0], b[0]))
                    run = min(hi, r["x"] + r["w"]) - max(lo, r["x"])
                    if run > MIN_RUN and min(abs(a[1] - r["y"]), abs(a[1] - r["y"] - r["h"])) <= BORDER_TOL:
                        f["K10"].append(f"edge {e['id']} segment {i} runs along the border of '{nid}'")
                elif is_v(a, b):
                    lo, hi = sorted((a[1], b[1]))
                    run = min(hi, r["y"] + r["h"]) - max(lo, r["y"])
                    if run > MIN_RUN and min(abs(a[0] - r["x"]), abs(a[0] - r["x"] - r["w"])) <= BORDER_TOL:
                        f["K10"].append(f"edge {e['id']} segment {i} runs along the border of '{nid}'")
        for p in (pts[0], pts[-1]):
            for nid, r in nodes.items():
                if not attached(p, r):
                    continue
                for cx in (r["x"], r["x"] + r["w"]):
                    for cy in (r["y"], r["y"] + r["h"]):
                        if max(abs(p[0] - cx), abs(p[1] - cy)) < CORNER_CLEAR:
                            f["K11"].append(f"edge {e['id']} attaches at ({p[0]:g},{p[1]:g}), within "
                                            f"{CORNER_CLEAR:g} px of a corner of '{nid}'")
        # K2 — labels
        lab = e.get("label")
        if lab:
            if not lab.get("mask") or lab.get("x") is None:
                f["K2"].append(f"edge {e['id']} label {str(lab.get('text') or '')[:24]!r} sits on the drawing with no opaque mask")
            if lab.get("x") is not None:
                gap = min(rect_seg_dist(lab, a, b) for a, b, _ in S)
                if gap < LABEL_GAP_MIN - 0.05:
                    f["K2"].append(f"edge {e['id']} label mask is {gap:.1f} px from its own line (< {LABEL_GAP_MIN:g}): it sits on the line")
                elif gap > LABEL_GAP_MAX + 0.05:
                    f["K2"].append(f"edge {e['id']} label mask is {gap:.1f} px from its own line (> {LABEL_GAP_MAX:g}): it no longer reads as this line's label")
                for o in edges:
                    if o is e:
                        continue
                    if any(rect_seg_dist(lab, a, b) < 0.5 for a, b, _ in segs(o["points"])):
                        f["K2"].append(f"edge {e['id']} label mask covers connector {o['id']}")
                for nid, r in nodes.items():
                    if overlap_area(lab, r) > 1.0 and not contained(lab, r):
                        f["K2"].append(f"edge {e['id']} label mask is clipped by node '{nid}' painted after it")
    # K5 — parallel too close; K6 — crossings without a hop; K7 — shared ports
    allsegs = [(e, s) for e in edges for s in segs(e["points"])]

    def ends_on_small_side(e, s):
        a, b, i = s
        for p in ((a,) if i == 0 else ()) + ((b,) if i == len(e["points"]) - 2 else ()):
            for nid in (e["from"], e["to"]):
                r = nodes.get(nid)
                if r and attached(p, r, 1.0) and side_of(p, r)[1] < SMALL_SIDE:
                    return True
        return False

    for x in range(len(allsegs)):
        e1, s1 = allsegs[x]
        for y in range(x + 1, len(allsegs)):
            e2, s2 = allsegs[y]
            if e1 is e2:
                continue
            (a, b, _), (c, d, _) = s1, s2
            for horiz in (True, False):
                if horiz and not (is_h(a, b) and is_h(c, d)):
                    continue
                if not horiz and not (is_v(a, b) and is_v(c, d)):
                    continue
                k = 0 if horiz else 1   # along-axis index
                o = 1 - k
                sep = abs(a[o] - c[o])
                lo = max(min(a[k], b[k]), min(c[k], d[k]))
                hi = min(max(a[k], b[k]), max(c[k], d[k]))
                run = hi - lo
                need = SMALL_MIN if (ends_on_small_side(e1, s1) and ends_on_small_side(e2, s2)) else PARALLEL_MIN
                if run > MIN_RUN and sep < need - 0.05:
                    f["K5"].append(f"edges {e1['id']} and {e2['id']} run parallel {sep:.1f} px apart over {run:.0f} px (< {need:g})")
    crossings = []
    for x in range(len(allsegs)):
        e1, s1 = allsegs[x]
        for y in range(x + 1, len(allsegs)):
            e2, s2 = allsegs[y]
            if e1 is e2:
                continue
            pt = crossing(s1, s2)
            if pt:
                crossings.append((e1["id"], e2["id"], pt))
    hops = m.get("hops")
    if hops is None and not require_hops:
        not_run["K6"] = (f"no hop data in this input ({len(crossings)} crossing(s) seen); pass --require-hops "
                         f"or a kit with hop_at_crossings to judge them")
    else:
        hl = hops or []
        for i1, i2, (px, py) in crossings:
            if not any(math.hypot(float(h["x"]) - px, float(h["y"]) - py) <= HOP_TOL for h in hl):
                f["K6"].append(f"edges {i1} and {i2} cross at ({px:.1f},{py:.1f}) with no hop")
    ports = []
    for e in edges:
        if len(e["points"]) < 2:
            continue
        for p, role in ((e["points"][0], "start"), (e["points"][-1], "end")):
            for nid, r in nodes.items():
                if attached(p, r, 1.0):
                    side, length, along = side_of(p, r)
                    ports.append((nid, side, length, along, role, e["id"]))
    for i in range(len(ports)):
        for j in range(i + 1, len(ports)):
            A, B = ports[i], ports[j]
            if A[0] != B[0] or A[1] != B[1] or A[4] != B[4] or A[5] == B[5]:
                continue
            need = SMALL_MIN if A[2] < SMALL_SIDE else PARALLEL_MIN
            if abs(A[3] - B[3]) < need - 0.05:
                f["K7"].append(f"edges {A[5]} and {B[5]} attach {abs(A[3] - B[3]):.1f} px apart on the {A[1]} side of '{A[0]}' (< {need:g})")
    # K8 — paint order
    po = m.get("paint_order")
    if po is None:
        not_run["K8"] = "no paint order in this input"
    else:
        idx = {}
        for i, p in enumerate(po):
            idx.setdefault((p.get("kind"), str(p.get("id"))), i)
        for gid, g in groups.items():
            gi = idx.get(("group", gid))
            if gi is None:
                f["K8"].append(f"group '{gid}' is never painted")
                continue
            for nid, n in nodes.items():
                if n.get("group") == gid and idx.get(("node", nid), -1) < gi:
                    f["K8"].append(f"node '{nid}' is painted before its group '{gid}'")
            for cid, c in groups.items():
                if c.get("parent") == gid and idx.get(("group", cid), -1) < gi:
                    f["K8"].append(f"group '{cid}' is painted before its parent group '{gid}'")
        layers = {k: [i for i, p in enumerate(po) if p.get("kind") == k] for k in ("group", "edge", "label", "node")}
        order = ["group", "edge", "label", "node"]
        for a_i in range(len(order)):
            for b_i in range(a_i + 1, len(order)):
                A, B = layers[order[a_i]], layers[order[b_i]]
                if A and B and max(A) > min(B):
                    f["K8"].append(f"a {order[a_i]} is painted after a {order[b_i]} (order must be groups -> edges -> labels -> nodes)")
    # K9 — containment
    if groups:
        def ancestors(gid):
            seen = []
            while gid and gid not in seen:
                seen.append(gid)
                gid = (groups.get(gid) or {}).get("parent")
            return seen
        for nid, n in nodes.items():
            mine = ancestors(n.get("group")) if n.get("group") else []
            for gid in mine:
                if gid in groups and not contained(n, groups[gid]):
                    f["K9"].append(f"node '{nid}' lies outside its group '{gid}'")
            for gid, g in groups.items():
                if gid not in mine and overlap_area(n, g) > 1.0:
                    f["K9"].append(f"node '{nid}' is drawn inside group '{gid}' it does not belong to")
        for gid, g in groups.items():
            p = g.get("parent")
            if p in groups and not contained(g, groups[p]):
                f["K9"].append(f"group '{gid}' lies outside its parent group '{p}'")
    else:
        not_run["K9"] = "no groups in this input"
    if not ortho:
        not_run["K4"] = "routing is not declared orthogonal here (layout, kit or --orthogonal)"
    if m["kind"] == "trace":
        not_run.setdefault("K2", "scene traces carry no connector labels or masks (text on lines is S4 in check_semantics.py)")
        not_run.setdefault("K8", "scene traces carry no paint order")
    return {"findings": {k: v for k, v in f.items()}, "not_run": not_run, "crossings": len(crossings)}


def judge_file(path: Path, orthogonal: bool, require_hops: bool) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(doc, dict) and isinstance(doc.get("frames"), list):
        merged = {k: [] for k in CODES}
        seen = set()
        not_run = {}
        for m in models_from_trace(doc):
            r = run_checks(m, orthogonal=orthogonal, require_hops=require_hops)
            not_run.update(r["not_run"])
            for k, v in r["findings"].items():
                for msg in v:
                    if (k, msg) in seen:
                        continue
                    seen.add((k, msg))
                    merged[k].append(f"t={float(m['t']):.2f}s {msg}")
        return {"source": str(path), "kind": "trace", "findings": merged, "not_run": not_run}
    if isinstance(doc, dict) and isinstance(doc.get("edges"), list) and isinstance(doc.get("nodes"), list):
        r = run_checks(model_from_layout(doc), orthogonal=orthogonal, require_hops=require_hops)
        return {"source": str(path), "kind": "layout", "findings": r["findings"], "not_run": r["not_run"],
                "crossings": r["crossings"]}
    raise ValueError("not a layout JSON (nodes+edges) or a scene trace (frames)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Coordn8 connector & geometry gate (advisory).")
    ap.add_argument("inputs", nargs="+", help="layout JSON (layout_cli.js --json) and/or scene trace JSON")
    ap.add_argument("--kit", help="style kit / kit_export runtime / design_system JSON (routing, hop_at_crossings)")
    ap.add_argument("--orthogonal", action="store_true", help="judge diagonals even if the input does not declare it")
    ap.add_argument("--require-hops", action="store_true", help="judge crossings even without hop data")
    a = ap.parse_args(argv)
    ortho, hops = a.orthogonal, a.require_hops
    if a.kit:
        routing, hop_flag = kit_routing(a.kit)
        ortho = ortho or routing == "orthogonal"
        hops = hops or bool(hop_flag)
    paths = [Path(p) for p in a.inputs]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        emit({"gate": "connectors", "status": "SKIPPED",
              "summary": f"missing input(s): {', '.join(missing)} — write a layout with layout_cli.js --json "
                         f"or a scene trace with scene_runtime.js first"})
        return EXIT_SKIPPED
    results = []
    for p in paths:
        try:
            results.append(judge_file(p, ortho, hops))
        except (OSError, ValueError, KeyError, TypeError) as ex:
            emit({"gate": "connectors", "status": "ERROR", "summary": f"could not read {p}: {type(ex).__name__}: {ex}"})
            return EXIT_ERROR
    counts = {k: sum(len(r["findings"][k]) for r in results) for k in CODES}
    n = sum(counts.values())
    emit({
        "gate": "connectors",
        "status": "REVIEW" if n else "PASS",
        "counts": counts,
        "findings": {r["source"]: {k: v for k, v in r["findings"].items() if v} for r in results if any(r["findings"].values())},
        "not_judged": {r["source"]: r["not_run"] for r in results if r["not_run"]},
        "gate_meanings": MEANINGS,
        "judges": "geometry only — never catalog membership",
        "gate_pass_is_not_quality": ("Passing means no connector breaks these geometric rules; it does not mean "
                                     "the diagram explains its subject well."),
        "summary": (f"{n} connector finding(s) across {len(results)} input(s)" if n else
                    f"no connector findings across {len(results)} input(s) (geometry only; not a quality claim)"),
    })
    return EXIT_REVIEW if n else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
