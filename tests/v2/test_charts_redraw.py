#!/usr/bin/env python3
"""v2 suite: chart honesty (B5) and redraw-never-convert with a fidelity ledger (B6).

Hermetic: temp dirs only, sandboxed HOME, no network. The redraw half needs `node` and the vendored,
MIT-licensed layout library (scripts/render/vendor/dagre.min.js, as tests/v2/test_layout.py finds it);
when either is missing every engine check is replaced by one check that records the skip reason.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import ROOT, Suite, run, sandbox_env, tmpdir  # noqa: E402

sys.dont_write_bytecode = True

GATE = "scripts/gates/check_chart_honesty.py"
REDRAW = "scripts/design/redraw.py"
CH = ROOT / "tests" / "v2" / "fixtures" / "charts"
RD = ROOT / "tests" / "v2" / "fixtures" / "redraw"
NODE = shutil.which("node")


def find_dagre():
    for c in (os.environ.get("COORDN8_DAGRE"), ROOT / "_build" / "vendor" / "dagre.min.js",
              ROOT / "scripts" / "render" / "vendor" / "dagre.min.js"):
        if c and Path(c).is_file():
            return Path(c)
    return None


DAGRE = find_dagre()


def first_json(cp):
    try:
        return json.loads(cp.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return {"status": "UNPARSED", "stderr": cp.stderr[-400:], "findings": [], "charts": []}


def chart(path_or_text, tmp=None, name="c.svg", env=None):
    p = path_or_text
    if tmp is not None:
        p = Path(tmp) / name
        p.write_text(path_or_text, encoding="utf-8")
    cp = run(GATE, [str(p)], env=env)
    return cp.returncode, first_json(cp)


def set_attr(src: str, mark: str, attr: str, value: str) -> str:
    """Change one attribute on the element carrying data-name="mark"."""
    pat = re.compile(r'(<[a-z]+\b[^>]*\bdata-name="%s"[^>]*>)' % re.escape(mark))
    m = pat.search(src)
    assert m, mark
    tag = m.group(1)
    new = re.sub(r'(\s%s=")[^"]*(")' % re.escape(attr), r"\g<1>%s\g<2>" % value, tag, count=1)
    if new == tag:
        new = tag[:-2] + f' {attr}="{value}"/>' if tag.endswith("/>") else tag[:-1] + f' {attr}="{value}">'
    return src.replace(tag, new, 1)


def kinds(res, mark=None):
    return [f["kind"] for f in res.get("findings", []) if mark is None or f["mark"] == mark]


s = Suite("charts_redraw")

with tmpdir() as td:
    tmp = Path(td)
    env = sandbox_env(tmp)
    env["PYTHONDONTWRITEBYTECODE"] = "1"   # keep the interpreter's own bytecode cache out of the sandbox HOME

    # ============================================================ B5 — chart honesty
    clean = {"bar": "bar_clean.svg", "column": "column_clean.svg", "stacked-bar": "stacked_bar_clean.svg",
             "stacked-column": "stacked_column_clean.html", "line": "line_clean.svg", "scatter": "scatter_clean.svg",
             "area": "area_clean.svg", "pie": "pie_clean.svg", "donut": "donut_clean.svg"}
    for fam, fn in clean.items():
        code, res = chart(CH / fn, env=env)
        fams = [c["family"] for c in res.get("charts", [])]
        s.check(f"chart: clean {fam} chart passes (geometry recomputed from its data-* values)",
                code == 0 and res.get("status") == "PASS" and fams == [fam], f"{code} {fams} {res.get('findings')}")
    all_bound = True
    detail = []
    for fam, fn in clean.items():
        _c, res = chart(CH / fn, env=env)
        for c in res.get("charts", []):
            if c["printed_numbers_unbound"] != 0 or c["printed_numbers_bound"] < 1:
                all_bound = False
                detail.append((fn, c["printed_numbers_bound"], c["printed_numbers_unbound"]))
    s.check("chart: every number printed in every clean fixture is also in a data-* attribute", all_bound, detail)

    code, res = chart(CH / "bar_unbound_number.svg", env=env)
    s.check("chart: a printed number with no data-* value behind it is flagged (missing -> flagged)",
            code == 1 and kinds(res) == ["unbound-number"] and res["findings"][0]["mark"] == "52", res.get("findings"))

    code, res = chart(CH / "bar_long20.svg", env=env)
    f = [x for x in res.get("findings", []) if x["kind"] == "length"]
    s.check("chart: a bar drawn 20 % too long is caught",
            code == 1 and len(f) == 1 and f[0]["mark"] == "East" and "+20.0 %" in f[0]["detail"], res.get("findings"))
    fit = (res.get("charts") or [{}])[0].get("fit", {})
    s.check("chart: one outlier bar does not move the fitted scale — the four honest bars still pass",
            fit.get("scale_px_per_unit") == 5.0 and fit.get("outliers") == ["East"]
            and [x["mark"] for x in res.get("findings", [])] == ["East"], (fit, res.get("findings")))

    code, res = chart(CH / "bar_first_long20.svg", env=env)
    s.check("chart: the bad bar is the FIRST mark (the one an anchor-based fit would trust) — only it is flagged",
            code == 1 and [x["mark"] for x in res.get("findings", [])] == ["North"]
            and res["charts"][0]["fit"]["scale_px_per_unit"] == 5.0, res.get("findings"))

    def bars_svg(rows, ticks=""):
        body = "".join(f'<rect data-name="{n}" data-value="{v}" x="100" y="{20 + 40 * i}" width="{w}" height="30"/>'
                       for i, (n, v, w) in enumerate(rows))
        return f'<svg xmlns="http://www.w3.org/2000/svg"><g data-chart="bar">{ticks}{body}</g></svg>'

    code, res = chart(bars_svg([("a", 10, 40), ("b", 20, 96), ("c", 30, 120)]), tmp, "three.svg", env)
    s.check("chart: robust fit isolates a 20 % long bar among only THREE marks (median of ratios)",
            code == 1 and [x["mark"] for x in res.get("findings", [])] == ["b"], res.get("findings"))
    code, res = chart(bars_svg([("a", 10, 40), ("b", 20, 96)]), tmp, "two.svg", env)
    s.check("chart: two marks that disagree are reported as an ambiguous scale (fail closed, never a pass)",
            code == 1 and "ambiguous-scale" in kinds(res), res.get("findings"))
    code, res = chart(bars_svg([("a", 10, 40)]), tmp, "one.svg", env)
    s.check("chart: a single mark with no tick is reported unverifiable, never passed",
            code == 1 and kinds(res) == ["unverifiable"], res.get("findings"))
    tick = '<line data-tick="x" data-value="20" x1="180" x2="180" y1="0" y2="60"/>'
    code1, res1 = chart(bars_svg([("a", 10, 40)], tick), tmp, "one_tick.svg", env)
    code2, res2 = chart(bars_svg([("a", 10, 48)], tick), tmp, "one_tick_bad.svg", env)
    s.check("chart: one mark plus a precise axis tick is verifiable — honest passes, 20 % long is caught",
            code1 == 0 and code2 == 1 and "length" in kinds(res2), (res1.get("findings"), res2.get("findings")))

    src = (CH / "bar_clean.svg").read_text(encoding="utf-8")
    code, res = chart(src.replace('<rect data-name="West" data-value="10"', '<rect data-name="West"'), tmp, "nv.svg", env)
    s.check("chart: a drawn bar with no data-value is flagged as an unbound mark", code == 1 and "unbound-mark" in kinds(res),
            res.get("findings"))
    code, res = chart(src.replace('data-tick="x" data-value="40" x1="320" x2="320"', 'data-tick="x" data-value="40" x1="300" x2="300"'),
                      tmp, "tick.svg", env)
    s.check("chart: a tick drawn where the data's scale does not put its value (relabelled axis) is caught",
            code == 1 and "tick" in kinds(res) and all(k == "tick" for k in kinds(res)), res.get("findings"))
    wrapped = src.replace('<g data-chart="bar"', '<g transform="translate(30, 12)"><g data-chart="bar"').replace("</g>\n</svg>", "</g></g>\n</svg>")
    code, res = chart(wrapped, tmp, "translated.svg", env)
    s.check("chart: a translate() on an ancestor is resolved — the shifted clean chart still passes", code == 0, res.get("findings"))
    code, res = chart(set_attr(src, "South", "transform", "scale(1.2 1)"), tmp, "scaled.svg", env)
    s.check("chart: a scale/rotate transform on a mark is reported (geometry unverifiable), not trusted",
            code == 1 and "transform" in kinds(res, "South"), res.get("findings"))

    col = (CH / "column_clean.svg").read_text(encoding="utf-8")
    code, res = chart(set_attr(col, "Q2", "y", "170"), tmp, "float.svg", env)
    s.check("chart: a column floating off the baseline (not starting at zero) is caught",
            code == 1 and "baseline" in kinds(res, "Q2"), res.get("findings"))
    code, res = chart(col.replace('data-unbound="the year is a heading, not data"', 'data-unbound=""'), tmp, "ub.svg", env)
    s.check("chart: data-unbound without a reason is flagged; with a reason the clean fixture passes",
            code == 1 and "unbound-reason" in kinds(res), res.get("findings"))

    code, res = chart(CH / "pie_bad_angle.svg", env=env)
    s.check("chart: a pie slice whose angle is wrong (8 deg too wide) is caught on that slice only",
            code == 1 and kinds(res, "B") == ["angle"] and not any(k == "angle" for k in kinds(res, "C") + kinds(res, "D")),
            res.get("findings"))
    pie = (CH / "pie_clean.svg").read_text(encoding="utf-8")
    code, res = chart(pie.replace(">25%<", ">30%<"), tmp, "pct.svg", env)
    s.check("chart: a pie percentage label that disagrees with the share recomputed from the data is flagged",
            code == 1 and kinds(res) == ["unbound-number"], res.get("findings"))

    code, res = chart(CH / "line_point_off.svg", env=env)
    s.check("chart: a line point 12 px off is caught on that point only; the other eleven points pass",
            code == 1 and [x["mark"] for x in res.get("findings", [])] == ["series-A[3]"], res.get("findings"))

    code, res = chart(CH / "stacked_bar_gap.svg", env=env)
    s.check("chart: a gap between stacked segments is caught", code == 1 and "stack-gap" in kinds(res), res.get("findings"))
    sb = (CH / "stacked_bar_clean.svg").read_text(encoding="utf-8")
    code, res = chart(sb.replace('data-stack="API" data-total="60"', 'data-stack="API" data-total="70"'), tmp, "tot.svg", env)
    s.check("chart: a stacked total that does not equal the sum of its segments is caught",
            code == 1 and "stack-total" in kinds(res), res.get("findings"))

    code, res = chart(CH / "area_truncated.svg", env=env)
    s.check("chart: an area that closes above zero (truncated baseline) is caught",
            code == 1 and kinds(res) == ["area-baseline"], res.get("findings"))

    sc = (CH / "scatter_clean.svg").read_text(encoding="utf-8")
    code, res = chart(set_attr(sc, "p3", "r", "10"), tmp, "radius.svg", env)
    s.check("chart: a scatter point sized by radius instead of area is caught on that point only",
            code == 1 and [x["mark"] for x in res.get("findings", [])] == ["p3"] and kinds(res) == ["area"], res.get("findings"))
    code, res = chart(set_attr(sc, "p5", "cx", "430"), tmp, "nudge.svg", env)
    s.check("chart: a scatter point nudged sideways is caught; its peers still pass",
            code == 1 and [x["mark"] for x in res.get("findings", [])] == ["p5"], res.get("findings"))

    code, res = chart(CH / "invented_family.svg", env=env)
    s.check("chart: an invented chart family is not failed for being unknown (PASS, with a note)",
            code == 0 and res.get("charts", [{}])[0].get("invented") is True and res.get("notes"), res)
    inv = (CH / "invented_family.svg").read_text(encoding="utf-8")
    code, res = chart(inv.replace(">72%<", ">80%<"), tmp, "inv.svg", env)
    s.check("chart: an invented family's printed numbers are still checked against its data",
            code == 1 and kinds(res) == ["unbound-number"], res.get("findings"))
    code, res = chart('<svg xmlns="http://www.w3.org/2000/svg"><rect x="1" y="1" width="5" height="5"/></svg>', tmp,
                      "plain.svg", env)
    s.check("chart: a file with no data-chart is SKIPPED (exit 2), never reported as a pass",
            code == 2 and res.get("status") == "SKIPPED", res)

    sys.path.insert(0, str(ROOT / "scripts" / "gates"))
    import check_chart_honesty as CHK  # noqa: E402
    r = CHK.robust_ratio([("a", 5.0), ("b", 5.01), ("c", 4.99), ("d", 5.0), ("x", 6.0)])
    s.check("chart: robust ratio fit (median + MAD) rejects one wild ratio and keeps the scale at the honest value",
            r["outliers"] == ["x"] and abs(r["scale"] - 5.0) < 0.011, r)
    aff = CHK.robust_affine([("p%d" % i, i, 100 - 3 * i + (40 if i == 2 else 0)) for i in range(6)])
    s.check("chart: robust position fit (median pairwise slope + MAD of residuals) isolates one bad point",
            aff["outliers"] == ["p2"] and abs(aff["a"] + 3) < 1e-9 and abs(aff["b"] - 100) < 1e-9, aff)

    # ============================================================ B6 — redraw with a fidelity ledger
    if not NODE or not DAGRE:
        s.check("redraw: SKIPPED — " + ("node not found" if not NODE else "vendored layout library (dagre.min.js) not found"),
                True)
    else:
        build = tmp / "build"
        denv = dict(env, COORDN8_DAGRE=str(DAGRE))

        def redraw(src, *extra, b=None, rid=None):
            args = [str(src), "--build", str(b or build)] + (["--id", rid] if rid else []) + list(extra)
            cp = run(REDRAW, args, env=denv)
            res = first_json(cp)
            led = None
            if res.get("ledger") and Path(res["ledger"]).is_file():
                led = json.loads(Path(res["ledger"]).read_text(encoding="utf-8"))
            return cp.returncode, res, led

        fixtures = {"mermaid flowchart": "flowchart.mmd", "mermaid sequence": "sequence.mmd", "mermaid state": "state.mmd",
                    "mermaid class": "class.mmd", "draw.io uncompressed": "architecture.drawio",
                    "draw.io deflate-compressed": "architecture_compressed.drawio", "Excalidraw": "whiteboard.excalidraw"}
        out = {}
        for label, fn in fixtures.items():
            code, res, led = redraw(RD / fn)
            out[fn] = (code, res, led)
            svg_ok = res.get("svg") and Path(res["svg"]).is_file() and Path(res["svg"]).read_text(encoding="utf-8").startswith("<svg")
            s.check(f"redraw: {label} fixture redraws to SVG through the deterministic layout engine",
                    code == 0 and res.get("status") == "PASS" and svg_ok and led and led.get("verified_against_layout") is True,
                    (code, res.get("summary"), res.get("problems")))
            if led:
                ents = led["entries"]
                per = {}
                for e in ents:
                    per.setdefault(e["kind"], {}).setdefault(e["fate"], 0)
                    per[e["kind"]][e["fate"]] += 1
                bal = all(sum(per.get(k, {}).get(fz, 0) for fz in ("kept", "merged", "renamed", "dropped"))
                          == led["source_counts"].get(k, 0) for k in ("node", "container", "edge", "label", "other"))
                tot = sum(led["fates"][fz] for fz in ("kept", "merged", "renamed", "dropped")) == led["source_counts"]["total"] == len(ents)
                s.check(f"redraw: {label} ledger — kept + merged + renamed + dropped == source count, per kind and in total",
                        bal and tot, (per, led["source_counts"]))
                s.check(f"redraw: {label} ledger — every merged, renamed or dropped entry carries a reason",
                        all(e["fate"] == "kept" or str(e.get("reason", "")).strip() for e in ents),
                        [e for e in ents if e["fate"] != "kept" and not e.get("reason")])

        # independent source counts (read straight from the source files, not from the redraw)
        expected = {"flowchart.mmd": {"node": 7, "container": 2, "edge": 8, "label": 12, "other": 4},
                    "sequence.mmd": {"node": 4, "container": 0, "edge": 7, "label": 11, "other": 7},
                    "state.mmd": {"node": 11, "container": 1, "edge": 10, "label": 18, "other": 2},
                    "class.mmd": {"node": 7, "container": 0, "edge": 5, "label": 12, "other": 1}}
        ok, bad = True, []
        for fn, exp in expected.items():
            led = out[fn][2] or {}
            got = {k: (led.get("source_counts") or {}).get(k) for k in exp}
            if got != exp:
                ok, bad = False, bad + [(fn, got, exp)]
        s.check("redraw: Mermaid ledgers account for exactly the elements counted by hand in each source", ok, bad)
        root = ET.parse(str(RD / "architecture.drawio")).getroot()
        page = root.findall("diagram")[0]
        cells = list(page.iter("mxCell"))
        edge_ids = {c.get("id") for c in cells if c.get("edge") == "1"}
        v_cells = [c for c in cells if c.get("vertex") == "1" and c.get("parent") not in edge_ids
                   and "edgeLabel" not in (c.get("style") or "")]
        led = out["architecture.drawio"][2] or {}
        sc_ = led.get("source_counts", {})
        s.check("redraw: draw.io ledger accounts for every vertex and edge cell in the source page",
                sc_.get("node", 0) + sc_.get("container", 0) == len(v_cells) and sc_.get("edge") == len(edge_ids),
                (sc_, len(v_cells), len(edge_ids)))
        doc = json.loads((RD / "whiteboard.excalidraw").read_text(encoding="utf-8"))
        live = [e for e in doc["elements"] if not e.get("isDeleted")]
        holders = {e["id"] for e in live if e["type"] in ("rectangle", "ellipse", "diamond", "image", "arrow", "line")}
        n_nodes = sum(1 for e in live if e["type"] in ("rectangle", "ellipse", "diamond", "image")
                      or (e["type"] == "text" and e.get("containerId") not in holders))
        n_edges = sum(1 for e in live if e["type"] in ("arrow", "line"))
        n_frames = sum(1 for e in live if e["type"] == "frame")
        led = out["whiteboard.excalidraw"][2] or {}
        sc_ = led.get("source_counts", {})
        s.check("redraw: Excalidraw ledger accounts for every shape, frame, arrow/line and standalone text in the scene",
                (sc_.get("node"), sc_.get("container"), sc_.get("edge")) == (n_nodes, n_frames, n_edges),
                (sc_, n_nodes, n_frames, n_edges))

        # both draw.io encodings: identical content
        b1, b2 = tmp / "enc1", tmp / "enc2"
        c1, r1, l1 = redraw(RD / "architecture.drawio", b=b1, rid="arch")
        c2, r2, l2 = redraw(RD / "architecture_compressed.drawio", b=b2, rid="arch")
        same = (c1 == c2 == 0 and Path(r1["svg"]).read_bytes() == Path(r2["svg"]).read_bytes()
                and (b1 / "redraw" / "arch.graph.json").read_bytes() == (b2 / "redraw" / "arch.graph.json").read_bytes()
                and l1["fates"] == l2["fates"] and l2["source"]["encoding"] == "deflate+base64"
                and l1["source"]["encoding"] == "uncompressed")
        s.check("redraw: draw.io compressed and uncompressed encodings decode to the same graph and byte-identical SVG", same,
                (r1.get("summary"), r2.get("summary")))

        # determinism
        det = []
        for fn in fixtures.values():
            ca, ra, _ = redraw(RD / fn, b=tmp / "detA", rid="d")
            cb, rb, _ = redraw(RD / fn, b=tmp / "detB", rid="d")
            det.append(ca == cb == 0 and Path(ra["svg"]).read_bytes() == Path(rb["svg"]).read_bytes()
                       and Path(ra["ledger"]).read_bytes() == Path(rb["ledger"]).read_bytes())
        s.check("redraw: same input twice -> byte-identical SVG and ledger (every format)", all(det), det)

        # ledger validation
        led_path = Path(out["flowchart.mmd"][1]["ledger"])
        good = json.loads(led_path.read_text(encoding="utf-8"))
        cp = run(REDRAW, ["check", str(led_path)], env=denv)
        s.check("redraw: check accepts a balanced ledger (exit 0)", cp.returncode == 0, cp.stdout[-300:])
        bad = json.loads(json.dumps(good))
        victim = next(e for e in bad["entries"] if e["fate"] == "dropped")
        victim.pop("reason", None)
        p = tmp / "noreason.fidelity.json"
        p.write_text(json.dumps(bad), encoding="utf-8")
        cp = run(REDRAW, ["check", str(p)], env=denv)
        s.check("redraw: a dropped element without a reason fails the ledger check (exit 1, named)",
                cp.returncode == 1 and "without a reason" in cp.stdout and victim["id"] in cp.stdout, cp.stdout[-400:])
        bad = json.loads(json.dumps(good))
        bad["entries"] = [e for e in bad["entries"] if e["kind"] != "edge" or e["fate"] != "dropped"]
        p.write_text(json.dumps(bad), encoding="utf-8")
        cp = run(REDRAW, ["check", str(p)], env=denv)
        s.check("redraw: a ledger that silently loses an element (counts no longer balance) fails the check",
                cp.returncode == 1 and "accounts for" in cp.stdout, cp.stdout[-400:])
        ed = tmp / "noreason_edit.json"
        ed.write_text(json.dumps({"drop": [{"id": "legacy", "reason": ""}]}), encoding="utf-8")
        nb = tmp / "refused"
        code, res, led = redraw(RD / "flowchart.mmd", "--edits", str(ed), b=nb)
        s.check("redraw: an edit that drops an element without a reason is refused (exit 3) and nothing is written",
                code == 3 and "reason" in res.get("summary", "") and not (nb / "redraw").exists(), res)

        # edits applied and recorded
        code, res, led = redraw(RD / "flowchart.mmd", "--edits", str(RD / "flowchart.edits.json"), b=tmp / "edits", rid="ed")
        g = json.loads((tmp / "edits" / "redraw" / "ed.graph.json").read_text(encoding="utf-8"))
        ents = {(e["kind"], e["id"]): e for e in (led or {}).get("entries", [])}
        names = {n["id"]: n["label"] for n in g["nodes"]}
        ok = (code == 0 and "legacy" not in names and "mobile" not in names and names.get("web") == "Client apps"
              and names.get("gw") == "Gateway"
              and ents[("node", "legacy")]["fate"] == "dropped" and "degrade ladder" in ents[("node", "legacy")]["reason"]
              and ents[("node", "mobile")]["fate"] == "merged" and ents[("node", "mobile")]["into"] == "web"
              and ents[("node", "gw")]["fate"] == "renamed"
              and sum(1 for e in g["edges"] if (e["from"], e["to"]) == ("web", "gw")) == 1
              and ents[("edge", "e2")]["fate"] == "merged")
        s.check("redraw: drop / merge / rename edits are applied to the drawing and recorded with their reasons", ok,
                (names, [(k, v["fate"]) for k, v in ents.items() if k[0] in ("node", "edge")]))

        # every element the ledger says is drawn is in the layout, and the layout holds nothing else
        okx, why = True, []
        for fn, (code, res, led) in out.items():
            if not res.get("layout"):
                okx = False
                continue
            lay = json.loads(Path(res["layout"]).read_text(encoding="utf-8"))
            ln = {"node:" + n["id"] for n in lay["nodes"]} | {"edge:" + e["id"] for e in lay["edges"]} | \
                 {"group:" + gg["id"] for gg in lay.get("groups", [])}
            claimed = {e["drawn_as"] for e in led["entries"] if e.get("drawn_as") and e["fate"] in ("kept", "renamed")
                       and e["kind"] in ("node", "edge", "container")}
            if claimed != ln:
                okx = False
                why.append((fn, sorted(claimed ^ ln)))
        s.check("redraw: the drawn elements in each ledger are exactly the elements in the layout the engine produced", okx, why)

        svg_dr = Path(out["architecture.drawio"][1]["svg"]).read_text(encoding="utf-8")
        svg_ex = Path(out["whiteboard.excalidraw"][1]["svg"]).read_text(encoding="utf-8")
        s.check("redraw: source palette and coordinates are discarded (no source colour reaches the SVG)",
                "#dae8fc" not in svg_dr.lower() and "#a5d8ff" not in svg_ex.lower() and 'x="200"' not in svg_dr,
                "source colour found")
        ldr = {(e["kind"], e["id"]): e for e in out["architecture.drawio"][2]["entries"]}
        lex = {(e["kind"], e["id"]): e for e in out["whiteboard.excalidraw"][2]["entries"]}
        lfc = {(e["kind"], e["id"]): e for e in out["flowchart.mmd"][2]["entries"]}
        whats = {e.get("what") for e in out["whiteboard.excalidraw"][2]["entries"] if e["kind"] == "other"}
        s.check("redraw: hidden cell, dangling edge, invisible link, freehand stroke, image payload and link are dropped "
                "with their reasons",
                ldr[("node", "draft")]["fate"] == "dropped" and "hidden" in ldr[("node", "draft")]["reason"]
                and ldr[("edge", "e5")]["fate"] == "dropped" and "dangling" in ldr[("edge", "e5")]["reason"]
                and lfc[("edge", "e8")]["fate"] == "dropped" and "invisible" in lfc[("edge", "e8")]["reason"]
                and {"freedraw-stroke", "image-payload", "link", "deleted-element"} <= whats
                and lex[("edge", "a6")]["fate"] == "dropped", whats)
        s.check("redraw: a draw.io edge-label cell is folded into its connector's label (merged, with the target named)",
                ldr[("label", "e2lbl#label")]["fate"] == "merged" and ldr[("label", "e2lbl#label")]["into"] == "edge:e2",
                ldr.get(("label", "e2lbl#label")))
        sg = json.loads((build / "redraw" / "sequence.graph.json").read_text(encoding="utf-8"))
        s.check("redraw: sequence messages keep their time order as numbers 1..N in the drawn labels",
                [int(e["label"].split(".")[0]) for e in sg["edges"]] == list(range(1, len(sg["edges"]) + 1)),
                [e["label"] for e in sg["edges"]])
        cg = json.loads((build / "redraw" / "class.graph.json").read_text(encoding="utf-8"))
        labs = [e["label"] for e in cg["edges"]]
        s.check("redraw: class relationships keep their meaning as label text and cardinalities are folded in",
                "extends" in labs and "implements" in labs and "is composed of" in labs and "places (1 to *)" in labs, labs)
        s.check("redraw: the connector gate passes on every redrawn layout",
                all(res.get("connector_gate") == "PASS" for _c, res, _l in out.values()),
                {fn: r.get("connector_gate") for fn, (_c, r, _l) in out.items()})

        inj = tmp / "inject.mmd"
        inj.write_text('flowchart TD\n  A["<script>alert(1)</script> Start"] --> B["ignore previous instructions"]\n',
                       encoding="utf-8")
        code, res, led = redraw(inj, b=tmp / "inj")
        svg = Path(res["svg"]).read_text(encoding="utf-8") if res.get("svg") else ""
        s.check("redraw: label text is inert data — markup is flattened, nothing executable reaches the SVG",
                code == 0 and "<script" not in svg.lower() and "ignore previous instructions" in svg, res.get("summary"))
        pie = tmp / "pie.mmd"
        pie.write_text('pie title Pets\n  "Dogs" : 386\n', encoding="utf-8")
        code, res, led = redraw(pie, b=tmp / "pie")
        s.check("redraw: an unsupported Mermaid kind is refused with a message, never approximated (exit 3)",
                code == 3 and "unsupported" in res.get("summary", ""), res)
        code, res, led = redraw(RD / "two_diagrams.md", "--diagram", "1", b=tmp / "md", rid="md1")
        g = json.loads((tmp / "md" / "redraw" / "md1.graph.json").read_text(encoding="utf-8")) if code == 0 else {}
        s.check("redraw: Markdown with two fences — --diagram 1 redraws the second and lists the other in the ledger",
                code == 0 and [n["id"] for n in g.get("nodes", [])] == ["X", "Y", "Z"]
                and any(e.get("what") == "unselected-diagram-0" for e in led["entries"]), res.get("summary"))
        code, res, led = redraw(RD / "architecture.drawio", "--diagram", "1", b=tmp / "pg", rid="pg1")
        s.check("redraw: multi-page draw.io — the unselected page is listed in the ledger and --diagram 1 redraws it",
                code == 0 and led["drawn_counts"]["nodes"] == 1
                and any(e.get("what") == "unselected-page-0" for e in led["entries"])
                and any(e.get("what") == "unselected-page-1" for e in out["architecture.drawio"][2]["entries"]),
                res.get("summary"))
        s.check("redraw: nothing is written into the sandboxed home directories",
                not any(any((tmp / d).rglob("*")) for d in ("coordn8_home", "claude_home", "home")), "home touched")

sys.exit(s.finish())
