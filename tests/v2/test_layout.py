#!/usr/bin/env python3
"""v2 suite: deterministic layout engine (B3), connector & geometry gate (B4), diagram export (B7),
freedom doctrine for diagram forms (H1).

Hermetic: temp dirs only, sandboxed HOME, no network. Needs `node` (17+) and the vendored, sha-pinned
layout library (dagre.min.js); when either is missing every engine check is replaced by one
check that records the skip reason.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import ROOT, Suite, run, sandbox_env, tmpdir  # noqa: E402

sys.dont_write_bytecode = True

LAYOUT_JS = ROOT / "scripts" / "render" / "layout.js"
CLI = ROOT / "scripts" / "render" / "layout_cli.js"
GATE = "scripts/gates/check_connectors.py"
EXPORT = "scripts/render/export_diagram.py"
FIX = ROOT / "tests" / "v2" / "fixtures" / "layout"
KITS = ROOT / "data" / "style_kits"
CATALOG = ROOT / "data" / "diagram_types.json"
CACHE_DAGRE = ROOT / "scripts" / "render" / "vendor" / "dagre.min.js"  # bundled, MIT
NODE = shutil.which("node")


def find_dagre():
    for c in (os.environ.get("COORDN8_DAGRE"), ROOT / "_build" / "vendor" / "dagre.min.js", CACHE_DAGRE):
        if c and Path(c).is_file():
            return Path(c)
    return None


def pinned_sha():
    sys.path.insert(0, str(ROOT / "scripts" / "render"))
    import fetch_visual_libs  # type: ignore
    return fetch_visual_libs.LIBS["dagre"]["sha256"]


def node_cli(args, env=None, timeout=60):
    e = dict(os.environ)
    if env:
        e.update(env)
    return subprocess.run([NODE, str(CLI)] + [str(a) for a in args], capture_output=True, text=True, timeout=timeout, env=e)


def gate(paths, extra=(), env=None):
    cp = run(GATE, list(map(str, paths)) + list(extra), env=env)
    try:
        return cp.returncode, json.loads(cp.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return cp.returncode, {"status": "UNPARSED", "stderr": cp.stderr[-400:], "counts": {}}


def lay(graph_path, out_json, extra=(), out_svg=None, env=None):
    args = [graph_path, "--dagre", DAGRE, "--json", out_json] + list(extra)
    if out_svg:
        args += ["--svg", out_svg]
    cp = node_cli(args, env=env)
    return cp, (json.loads(Path(out_json).read_text(encoding="utf-8")) if cp.returncode == 0 else None)


def kit_runtime(kit_id, dst, env):
    cp = run("scripts/design/kit_export.py", ["runtime", str(KITS / f"{kit_id}.json"), "--out", str(dst)], env=env)
    return cp.returncode == 0 and Path(dst).exists()


s = Suite("layout")
DAGRE = find_dagre()

if not NODE or not DAGRE:
    s.check("engine checks skipped with a recorded reason (node or the vendored layout library is missing)", True,
            f"node={NODE!r} dagre={DAGRE!r}")
    # The gate still runs without node: prove the hand-made defect is caught.
    rc, g = gate([FIX / "through_box_before.layout.json"])
    s.check("line through an unrelated box is caught BEFORE (hand-made layout)", g.get("counts", {}).get("K1", 0) > 0, g)
    sys.exit(s.finish())

with tmpdir() as td:
    T = Path(td)
    env = sandbox_env(T)
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    # ---- the vendored library is the pinned one -------------------------------------------------
    s.check("vendored layout library matches its pinned sha256", hashlib.sha256(DAGRE.read_bytes()).hexdigest() == pinned_sha(),
            str(DAGRE))

    # ---- B3: determinism ------------------------------------------------------------------------
    b7 = FIX / "branch7.json"
    cp1, L1 = lay(b7, T / "a1.json", out_svg=T / "a1.svg")
    cp2, L2 = lay(b7, T / "a2.json", out_svg=T / "a2.svg")
    s.check("layout CLI runs on the 7-node branching graph", cp1.returncode == 0 and L1 is not None, cp1.stderr[-300:])
    s.check("same graph in two separate node processes -> byte-identical SVG",
            (T / "a1.svg").read_bytes() == (T / "a2.svg").read_bytes(), "svg differs")
    s.check("same graph in two separate node processes -> byte-identical layout JSON",
            (T / "a1.json").read_bytes() == (T / "a2.json").read_bytes(), "json differs")
    js = (f"const L=require({json.dumps(str(LAYOUT_JS))});const g=JSON.parse(require('fs').readFileSync({json.dumps(str(b7))},'utf8'));"
          f"const o={{dagre:{json.dumps(str(DAGRE))}}};const a=L.toSVG(L.layout(g,o));const b=L.toSVG(L.layout(JSON.parse(JSON.stringify(g)),o));"
          f"process.stdout.write(a===b?'SAME':'DIFF');")
    cp = subprocess.run([NODE, "-e", js], capture_output=True, text=True, timeout=60)
    s.check("same graph twice in one process -> identical SVG", cp.stdout == "SAME", cp.stdout + cp.stderr[-200:])
    svg = (T / "a1.svg").read_text(encoding="utf-8")
    s.check("SVG carries no timestamp and no random id", not re.search(r"\d{4}-\d{2}-\d{2}|Math\.random|uuid", svg)
            and not re.search(r'id="[^"]*\d{6,}', svg), "found a date/random-looking id")

    # ---- B3: the 7-node branching graph obeys every connector rule ------------------------------
    rc, g7 = gate([T / "a1.json"])
    s.check("7-node branching graph: connector gate PASS (all checks)", rc == 0 and g7.get("status") == "PASS", g7)
    nj = g7.get("not_judged", {}).get(str(T / "a1.json"), {})
    s.check("7-node branching graph: orthogonality and hops were judged, not skipped", "K4" not in nj and "K6" not in nj, nj)
    s.check("7-node branching graph: no edge through an unrelated box", g7.get("counts", {}).get("K1") == 0, g7.get("counts"))
    labelled = [e for e in L1["edges"] if e.get("label")]
    s.check("7-node branching graph: every label has an opaque mask 6-10 px off its line",
            len(labelled) >= 4 and all(e["label"]["mask"] and e["label"]["valid"] for e in labelled)
            and g7.get("counts", {}).get("K2") == 0, [e["label"] for e in labelled])
    s.check("SVG draws a mask rect (paper fill) for every label", svg.count('data-c8="mask"') == len(labelled), svg.count('data-c8="mask"'))

    def depth(p, n):
        return min(p["x"] - n["x"], n["x"] + n["w"] - p["x"], p["y"] - n["y"], n["y"] + n["h"] - p["y"])
    nodes = {n["id"]: n for n in L1["nodes"]}
    tips = [abs(depth(e["points"][-1], nodes[e["to"]])) for e in L1["edges"]]
    poly_inside = [max(depth(p, nodes[e["to"]]) for p in e["arrow"]["points"]) for e in L1["edges"]]
    s.check("7-node branching graph: every arrowhead tip lies ON its target border",
            all(t <= 0.05 for t in tips), tips)
    s.check("7-node branching graph: no arrowhead polygon reaches inside its target", all(v <= 0.05 for v in poly_inside), poly_inside)
    diag = [e["id"] for e in L1["edges"] for a, b in zip(e["points"], e["points"][1:])
            if abs(a["x"] - b["x"]) > 0.01 and abs(a["y"] - b["y"]) > 0.01]
    s.check("7-node branching graph: every segment is orthogonal", not diag, diag)

    # attach points at L*k/(N+1)
    ok_ports, bad = True, []
    for nid, n in nodes.items():
        for side in ("top", "bottom"):
            ys = n["y"] if side == "top" else n["y"] + n["h"]
            xs = sorted([e["points"][0]["x"] for e in L1["edges"] if e["from"] == nid and abs(e["points"][0]["y"] - ys) < 0.05] +
                        [e["points"][-1]["x"] for e in L1["edges"] if e["to"] == nid and abs(e["points"][-1]["y"] - ys) < 0.05])
            want = [round(n["x"] + n["w"] * k / (len(xs) + 1), 1) for k in range(1, len(xs) + 1)]
            if xs and [round(x, 1) for x in xs] != want:
                ok_ports = False
                bad.append((nid, side, xs, want))
    s.check("attach points sit at L*k/(N+1) along each side", ok_ports, bad)

    # ---- B3: re-route around a box; hand-made BEFORE vs engine AFTER ----------------------------
    rc, gb = gate([FIX / "through_box_before.layout.json"])
    s.check("line through an unrelated box is caught BEFORE (hand-made layout)", gb.get("counts", {}).get("K1", 0) > 0, gb)
    cp, La = lay(FIX / "through_box.graph.json", T / "after.json")
    rc, ga = gate([T / "after.json"])
    s.check("same graph laid out by the engine: line through an unrelated box ABSENT after",
            cp.returncode == 0 and ga.get("counts", {}).get("K1") == 0 and ga.get("status") == "PASS", ga)
    cp, Ln = lay(FIX / "through_box.graph.json", T / "noreroute.json", extra=["--straight-lanes", "--no-reroute"])
    rc, gn = gate([T / "noreroute.json"])
    s.check("engine with re-routing switched off puts a lane behind a box (gate catches it)",
            gn.get("counts", {}).get("K1", 0) > 0, gn)
    cp, Lr = lay(FIX / "through_box.graph.json", T / "reroute.json", extra=["--straight-lanes"])
    rc, gr = gate([T / "reroute.json"])
    s.check("engine re-routes that lane to a free one and records why",
            gr.get("counts", {}).get("K1") == 0 and any("unrelated box" in r["reason"] for r in Lr["rerouted"]), (gr.get("counts"), Lr["rerouted"]))

    # ---- B4: seeded defects, each caught separately ---------------------------------------------
    base_counts = g7.get("counts", {})

    def seeded(name, mutate, code):
        doc = copy.deepcopy(L1)
        mutate(doc)
        p = T / f"seed_{code}.json"
        p.write_text(json.dumps(doc), encoding="utf-8")
        rc, g = gate([p])
        s.check(f"seeded defect caught: {name}", base_counts.get(code) == 0 and g.get("counts", {}).get(code, 0) > 0
                and rc == 1, g.get("counts"))
        return g

    def unmask(doc):
        e = next(e for e in doc["edges"] if e.get("label"))
        e["label"]["mask"] = False
    seeded("label on a line without a mask", unmask, "K2")

    def bury(doc):
        e = doc["edges"][0]
        n = next(n for n in doc["nodes"] if n["id"] == e["to"])
        tip = e["points"][-1]
        tip["y"] = n["y"] + 12      # 12 px into the target
        e["arrow"]["points"] = [dict(tip), {"x": tip["x"] - 4, "y": tip["y"] - 8}, {"x": tip["x"] + 4, "y": tip["y"] - 8}]
    seeded("arrowhead buried inside its target", bury, "K3")

    def diagonal(doc):
        e = next(e for e in doc["edges"] if len(e["points"]) >= 4)
        e["points"] = [e["points"][0], e["points"][-1]]
    seeded("diagonal segment in an orthogonal layout", diagonal, "K4")

    def parallel(doc):
        e = max(doc["edges"], key=lambda e: max(abs(a["y"] - b["y"]) for a, b in zip(e["points"], e["points"][1:])))
        i = max(range(len(e["points"]) - 1), key=lambda i: abs(e["points"][i]["y"] - e["points"][i + 1]["y"]))
        a, b = e["points"][i], e["points"][i + 1]
        doc["edges"].append({"id": "twin", "from": "zz", "to": "zz", "points": [{"x": a["x"] + 6, "y": a["y"] + 10},
                             {"x": b["x"] + 6, "y": b["y"] - 10}], "arrowAt": "to"})
    seeded("two parallel segments 6 px apart", parallel, "K5")

    # a diagonal in a kit that declares orthogonal routing (trace input carries no routing itself)
    tr = json.loads((FIX / "trace_through_box.json").read_text(encoding="utf-8"))
    tr["frames"][0]["connectors"] = [{"kind": "connector", "from": "a", "to": "b", "alpha": 1, "arrowAt": "to",
                                      "points": [{"x": 200, "y": 120}, {"x": 260, "y": 240}]}]
    (T / "diag_trace.json").write_text(json.dumps(tr), encoding="utf-8")
    rc0, g0 = gate([T / "diag_trace.json"])
    rc1, g1 = gate([T / "diag_trace.json"], ["--kit", str(KITS / "drafting-table.json")])
    rc2, g2c = gate([T / "diag_trace.json"], ["--kit", str(KITS / "chalk-talk.json")])
    s.check("diagonal flagged when the kit declares orthogonal routing", g1.get("counts", {}).get("K4", 0) > 0, g1.get("counts"))
    s.check("diagonal not judged when the kit's routing is curved (outcome follows the kit)",
            g2c.get("counts", {}).get("K4", 0) == 0 and g0.get("counts", {}).get("K4", 0) == 0, (g0.get("counts"), g2c.get("counts")))
    rc, gt = gate([FIX / "trace_through_box.json"])
    s.check("scene trace: connector through an unrelated box and buried arrowhead are caught",
            gt.get("counts", {}).get("K1", 0) > 0 and gt.get("counts", {}).get("K3", 0) > 0, gt.get("counts"))

    # ---- B3/B4: hops, groups, paint order ---------------------------------------------------------
    cp, LG = lay(FIX / "groups.json", T / "groups.json", out_svg=T / "groups.svg")
    rc, gg = gate([T / "groups.json"])
    s.check("grouped graph with a cycle and a self-loop: gate PASS", cp.returncode == 0 and gg.get("status") == "PASS", gg)
    s.check("crossings get hops (grouped graph has >= 1 hop, each drawn as an arc)",
            len(LG["hops"]) >= 1 and (T / "groups.svg").read_text(encoding="utf-8").count(" A5 5 0 0 ") >= len(LG["hops"]), LG["hops"])

    def no_hops(doc):
        doc["hops"] = []
    doc = copy.deepcopy(LG)
    no_hops(doc)
    (T / "nohop.json").write_text(json.dumps(doc), encoding="utf-8")
    rc, gh = gate([T / "nohop.json"])
    s.check("seeded defect caught: crossing with no hop", gg.get("counts", {}).get("K6") == 0 and gh.get("counts", {}).get("K6", 0) > 0,
            gh.get("counts"))
    idx = {(p["kind"], p["id"]): p["index"] for p in LG["paint_order"]}
    groups_ok = all(idx[("group", g["parent"])] < idx[("group", g["id"])] for g in LG["groups"] if g["parent"])
    kids_ok = all(idx[("group", n["group"])] < idx[("node", n["id"])] for n in LG["nodes"] if n["group"])
    s.check("topological paint order: a parent group before its child group, a group before its children (declared child-first)",
            groups_ok and kids_ok and [g["id"] for g in LG["groups"]][:2] == ["platform", "core"], LG["paint_order"])
    kinds = [p["kind"] for p in LG["paint_order"]]
    s.check("paint order runs background -> groups -> edges -> labels -> nodes",
            kinds == sorted(kinds, key=["background", "group", "edge", "label", "node"].index), kinds)
    gsvg = (T / "groups.svg").read_text(encoding="utf-8")
    order_in_svg = [int(x) for x in re.findall(r'data-paint="(\d+)"', gsvg)]
    s.check("SVG elements are emitted in paint order", order_in_svg == sorted(order_in_svg) and len(order_in_svg) == len(LG["paint_order"]),
            order_in_svg[:12])
    doc = copy.deepcopy(LG)
    po = doc["paint_order"]
    gi = next(i for i, p in enumerate(po) if p["kind"] == "group")
    po.append(po.pop(gi))
    (T / "badpaint.json").write_text(json.dumps(doc), encoding="utf-8")
    rc, gp = gate([T / "badpaint.json"])
    s.check("seeded defect caught: a group painted after its children", gp.get("counts", {}).get("K8", 0) > 0, gp.get("counts"))
    cyc = {"nodes": [{"id": "a", "group": "g1"}], "groups": [{"id": "g1", "parent": "g2"}, {"id": "g2", "parent": "g1"}], "edges": []}
    (T / "cyc.json").write_text(json.dumps(cyc), encoding="utf-8")
    cp = node_cli([T / "cyc.json", "--dagre", DAGRE, "--json", T / "cyc_out.json"])
    s.check("a containment cycle is refused with a clear error", cp.returncode == 3 and "containment cycle" in cp.stderr, cp.stderr[-200:])

    cp, LR = lay(FIX / "groups.json", T / "lr.json", extra=["--rankdir", "LR"])
    rc, glr = gate([T / "lr.json"])
    s.check("left-to-right layout of the grouped graph: gate PASS", cp.returncode == 0 and glr.get("status") == "PASS", glr)

    # ---- B3: kit-driven SVG -------------------------------------------------------------------------
    ka, kb = T / "kitA.json", T / "kitB.json"
    if kit_runtime("drafting-table", ka, env) and kit_runtime("night-gallery", kb, env):
        node_cli([T / "a1.json", "--kit", ka, "--svg", T / "ka.svg"])
        node_cli([T / "a1.json", "--kit", kb, "--svg", T / "kb.svg"])
        sa, sb = (T / "ka.svg").read_text(encoding="utf-8"), (T / "kb.svg").read_text(encoding="utf-8")
        cols = lambda t: set(re.findall(r'(?:fill|stroke)="(#[0-9a-fA-F]{6})"', t))  # noqa: E731
        widths = lambda t: set(re.findall(r'stroke-width="([\d.]+)"', t))  # noqa: E731
        kA = json.loads(ka.read_text(encoding="utf-8"))
        s.check("two different kits -> different SVG colours", cols(sa) != cols(sb) and cols(sa) and cols(sb), (cols(sa), cols(sb)))
        s.check("two different kits -> different stroke widths or radii", widths(sa) != widths(sb) or
                set(re.findall(r'rx="([\d.]+)"', sa)) != set(re.findall(r'rx="([\d.]+)"', sb)), (widths(sa), widths(sb)))
        s.check("kit colours come from the kit runtime (paper, node stroke)", kA["colors"]["paper"] in sa and kA["node"]["stroke"] in sa,
                kA["colors"]["paper"])
        s.check("same layout + same kit -> identical SVG bytes",
                node_cli([T / "a1.json", "--kit", ka, "--svg", T / "ka2.svg"]).returncode == 0 and
                (T / "ka2.svg").read_bytes() == (T / "ka.svg").read_bytes(), "differs")
    else:
        s.check("two different kits -> different SVG colours", False, "kit_export runtime failed")
    src = LAYOUT_JS.read_text(encoding="utf-8")
    neutral = re.search(r"const NEUTRAL = \{(.*?)\};", src, re.S)
    hexes = set(re.findall(r"#[0-9a-fA-F]{6}\b", src))
    s.check("layout.js hard-codes no colour beyond its neutral fallback", neutral and hexes <= set(re.findall(r"#[0-9a-fA-F]{6}", neutral.group(1))),
            hexes)

    # ---- H1: an invented diagram form --------------------------------------------------------------
    cat_ids = {t["id"] for t in json.loads(CATALOG.read_text(encoding="utf-8"))["types"]}
    inv = json.loads((FIX / "invented.json").read_text(encoding="utf-8"))
    cp, LI = lay(FIX / "invented.json", T / "inv.json", out_svg=T / "inv.svg")
    rc, gi2 = gate([T / "inv.json"])
    s.check("invented diagram type (not in the catalog) lays out and passes the connector gate",
            inv["type"] not in cat_ids and cp.returncode == 0 and gi2.get("status") == "PASS" and LI["type"] == inv["type"], gi2)
    gate_src = (ROOT / GATE).read_text(encoding="utf-8")
    s.check("connector gate and layout engine never consult the diagram-type catalog",
            not re.search(r"diagram_types|load_catalog|pick_diagram", gate_src) and not re.search(r"diagram_types|catalog\.json", src),
            "catalog reference found")

    # ---- scene compatibility: the engine's connectors satisfy the existing direction gate (S6) ----
    sr = ROOT / "scripts" / "render" / "scene_runtime.js"
    js = (f"const C=require({json.dumps(str(sr))});const L=require({json.dumps(str(LAYOUT_JS))});"
          f"const r=JSON.parse(require('fs').readFileSync({json.dumps(str(T / 'a1.json'))},'utf8'));const sc=L.toScene(r);"
          f"const tr=C.buildTrace({{name:'lay',script:[{{name:'one',text:'x',start:0,end:1}}],duration:1,grid:2,draw:(s)=>{{"
          f"for(const n of sc.nodes)s.node({{id:n.id,x:n.x,y:n.y,w:n.w,h:n.h,alpha:1}});"
          f"for(const c of sc.connectors)s.connector(Object.assign({{alpha:1}},c));}}}});"
          f"process.stdout.write(JSON.stringify(tr));")
    cp = subprocess.run([NODE, "-e", js], capture_output=True, text=True, timeout=60)
    if cp.returncode == 0 and cp.stdout:
        (T / "trace.json").write_text(cp.stdout, encoding="utf-8")
        sem = run("scripts/gates/check_semantics.py", [str(T / "trace.json")], env=env)
        try:
            sj = json.loads(sem.stdout.strip().splitlines()[0])
        except (ValueError, IndexError):
            sj = {"counts": {}}
        rc, gtr = gate([T / "trace.json"])
        s.check("engine connectors drawn through scene_runtime pass the direction gate (S6) and the connector gate",
                sj.get("counts", {}).get("S6", 1) == 0 and gtr.get("counts", {}).get("K1") == 0 and gtr.get("counts", {}).get("K3") == 0,
                (sj.get("counts"), gtr.get("counts")))
    else:
        s.check("engine connectors drawn through scene_runtime pass the direction gate (S6) and the connector gate", False, cp.stderr[-300:])

    # ---- B7: export ----------------------------------------------------------------------------------
    build = T / "build"
    (build).mkdir()
    plan = {"schema": 1, "figures": [{"id": "braid", "type": "braided-relay",
                                      "invented": {"name": "braided-relay", "why": "two relays share one buffer"}}]}
    (build / "diagram_plan.json").write_text(json.dumps(plan), encoding="utf-8")
    eenv = dict(env)
    eenv["COORDN8_DAGRE"] = str(DAGRE)
    real_pw = Path(os.path.expanduser("~")) / "Library" / "Caches" / "ms-playwright"
    if real_pw.exists():
        eenv["PLAYWRIGHT_BROWSERS_PATH"] = str(real_pw)   # read-only use of an installed browser
    cp = run(EXPORT, [str(FIX / "invented.json"), "--build", str(build), "--kit", str(KITS / "field-notebook.json")], env=eenv, timeout=180)
    reg_p = build / "diagrams" / "registry.json"
    reg = json.loads(reg_p.read_text(encoding="utf-8")) if reg_p.exists() else {"diagrams": []}
    ent = next((d for d in reg["diagrams"] if d.get("id") == "braid"), {})
    svg_p = build / "diagrams" / "braid.svg"
    s.check("export writes the SVG and a registry entry {id,type,nodes,edges,svg_sha256,png,source_plan}",
            cp.returncode == 0 and svg_p.exists() and all(k in ent for k in ("id", "type", "nodes", "edges", "svg_sha256", "png", "source_plan")),
            cp.stdout[-300:] + cp.stderr[-300:])
    s.check("registry svg_sha256 is the sha256 of the written SVG",
            svg_p.exists() and ent.get("svg_sha256") == hashlib.sha256(svg_p.read_bytes()).hexdigest(), ent.get("svg_sha256"))
    s.check("registry entry carries the invented type and points at its plan entry",
            ent.get("type") == "braided-relay" and ent.get("source_plan") == "diagram_plan.json#braid"
            and (ent.get("invented") or {}).get("name") == "braided-relay", ent)
    png_p = build / "diagrams" / "braid.png"
    png_ok = bool(ent.get("png")) and png_p.exists() and png_p.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    s.check("export writes a PNG, or records why it was skipped", png_ok or str(ent.get("png_note") or "").startswith("skipped:"),
            ent.get("png_note"))
    if png_ok:
        import struct
        w, h = struct.unpack(">II", png_p.read_bytes()[16:24])
        bb = json.loads((build / "diagrams" / "braid.layout.json").read_text(encoding="utf-8"))["bbox"]
        s.check("PNG is the layout's size at 2x", (w, h) == (bb["w"] * 2, bb["h"] * 2), (w, h, bb))
    s.check("export runs the connector gate and records its result", (ent.get("connector_gate") or {}).get("status") == "PASS", ent.get("connector_gate"))
    sha1 = ent.get("svg_sha256")
    run(EXPORT, [str(FIX / "invented.json"), "--build", str(build), "--kit", str(KITS / "field-notebook.json"), "--no-png"], env=eenv, timeout=180)
    reg2 = json.loads(reg_p.read_text(encoding="utf-8"))
    s.check("re-export replaces its own entry (one per id) with the same SVG hash",
            [d["id"] for d in reg2["diagrams"]].count("braid") == 1 and
            next(d for d in reg2["diagrams"] if d["id"] == "braid")["svg_sha256"] == sha1, reg2)
    cp = run(EXPORT, [str(b7), "--build", str(build), "--no-png"], env=eenv, timeout=180)
    reg3 = json.loads(reg_p.read_text(encoding="utf-8"))
    s.check("a second diagram is appended to the registry", [d["id"] for d in reg3["diagrams"]] == ["braid", "request-routing"],
            [d["id"] for d in reg3["diagrams"]])
    bad = T / "dagre_tampered.js"
    bad.write_bytes(DAGRE.read_bytes() + b"\n//x")
    cp = run(EXPORT, [str(b7), "--build", str(build), "--no-png", "--dagre", str(bad)], env=dict(eenv, COORDN8_DAGRE=""), timeout=120)
    s.check("export refuses a layout library whose sha256 is not the pinned one", cp.returncode == 3 and "pinned" in cp.stdout, cp.stdout[-300:])

    leftovers = [p for p in (ROOT / "tests" / "v2" / "fixtures" / "layout").iterdir() if p.suffix not in (".json",)]
    s.check("suite leaves nothing beside its fixtures", not leftovers, leftovers)

sys.exit(s.finish())
