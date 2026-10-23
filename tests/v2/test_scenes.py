#!/usr/bin/env python3
"""Scene catalog + scene runtime + determinism (E7, E10, A3 runtime half, E2 runtime half, H1).

Every component in data/scene_types.json is exercised through the REAL runtime
(`node scripts/render/scene_runtime.js trace`) and its trace is judged by the REAL gates
(check_semantics.py S1–S8, check_invariants.py C1–C8, check_density.py) — no gate is
re-implemented here. Hermetic: temp dirs only, HOME/COORDN8_HOME/CLAUDE_HOME sandboxed.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import PY, ROOT, Suite, run, sandbox_env, tmpdir  # noqa: E402

s = Suite("scenes")
NODE = shutil.which("node")
RT = ROOT / "scripts" / "render" / "scene_runtime.js"
COMP = ROOT / "scripts" / "render" / "scene_components.js"
CATALOG = ROOT / "data" / "scene_types.json"
FIX = ROOT / "tests" / "v2" / "fixtures" / "scenes"
KITS = ROOT / "data" / "style_kits"
REAL_HOME = os.path.expanduser("~")

BRIEF_SHAPES = {   # the content shapes the requirement names, by component id
    "matrix2x2", "balance", "ledger", "route", "splitMorph", "stateRing", "funnel", "failCard",
    "timeline", "layerStack", "fanIn", "fanOut", "pipeline", "comparisonTable", "bigNumber",
    "evidenceCard", "codeSpotlight", "zoneMap", "treeReveal", "sequence", "gauge", "heatmap",
    "checklist", "network", "zoomDetail", "causeChain"}


def gate_json(r) -> dict:
    try:
        return json.loads(r.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return {"status": "UNPARSEABLE", "raw": (r.stdout + r.stderr)[-400:]}


def node(args, env=None, timeout=120):
    e = dict(os.environ)
    if env:
        e.update(env)
    return subprocess.run([NODE, *map(str, args)], capture_output=True, text=True, env=e, timeout=timeout)


def trace(scene: Path, out: Path, *extra, env=None):
    return node([RT, "trace", scene, "--out", out, *extra], env=env)


def node_eval(code: str, tmp: Path, name: str, env=None):
    f = tmp / f"_{name}.js"
    f.write_text(code, encoding="utf-8")
    return node([f], env=env)


if not NODE:
    s.check("scene runtime checks SKIPPED — node not on PATH (runtime guarantees UNVERIFIED here)", True, "node missing")
    sys.exit(s.finish())

cat = json.loads(CATALOG.read_text(encoding="utf-8"))
types = cat.get("types") or []
ids = [t.get("id") for t in types]

with tmpdir() as td:
    T = Path(td)
    ENV = sandbox_env(T)

    # ================================================================== catalog (E7)
    s.check("scene_types.json parses and lists at least 25 content shapes", len(types) >= 25, len(types))
    fields = ("id", "shape", "use_when", "not_this", "component", "required_props", "budget")
    s.check("every catalog entry has id, shape, use_when, not_this, component, required_props and budget",
            all(all(t.get(k) not in (None, "", []) for k in fields) for t in types),
            [t.get("id") for t in types if not all(t.get(k) not in (None, "", []) for k in fields)])
    s.check("catalog ids are unique", len(set(ids)) == len(ids), ids)
    s.check("every not_this pointer names another catalog entry",
            all(n.get("use") in ids and n.get("use") != t["id"] for t in types for n in t["not_this"]),
            [(t["id"], n.get("use")) for t in types for n in t["not_this"] if n.get("use") not in ids])
    s.check("all 26 content shapes the requirement names have a catalog entry",
            BRIEF_SHAPES <= set(ids), sorted(BRIEF_SHAPES - set(ids)))
    s.check("the catalog states it is a starting point and how to invent a new form",
            "STARTING POINT" in cat.get("about", "") and "invented" in json.dumps(cat.get("invented_form", {})))

    probe = node_eval(
        "const S=require(process.argv[2]);const cat=require(process.argv[3]);const out={};"
        "for(const t of cat.types){const fn=S[t.component];const r={fn:typeof fn==='function'};"
        "try{fn({kitValue:()=>undefined},{});r.err='no throw';}catch(e){r.err=String(e.message)}out[t.id]=r;}"
        "console.log(JSON.stringify({names:S.names(),out}));".replace("process.argv[2]", json.dumps(str(COMP)))
        .replace("process.argv[3]", json.dumps(str(CATALOG))), T, "catalog_probe")
    pr = json.loads(probe.stdout) if probe.returncode == 0 else {"names": [], "out": {}}
    s.check("every catalog entry maps to an existing component function in scene_components.js",
            all(pr["out"].get(i, {}).get("fn") for i in ids) and len(pr["out"]) == len(ids),
            {i: pr["out"].get(i) for i in ids if not pr["out"].get(i, {}).get("fn")} or probe.stderr[-300:])
    mism = {}
    for t in types:
        m = re.search(r"\(required: ([^)]*)\)", pr["out"].get(t["id"], {}).get("err", ""))
        enforced = set(x.strip() for x in m.group(1).split(",")) if m else None
        if enforced != set(t["required_props"]):
            mism[t["id"]] = {"catalog": t["required_props"], "enforced": sorted(enforced or [])}
    s.check("each component enforces exactly the required_props its catalog entry lists", not mism, mism)
    s.check("scene_components.js exports a function for every catalog entry and nothing unlisted",
            set(pr["names"]) == set(ids), sorted(set(pr["names"]) ^ set(ids)))

    # ============================================ every component through the REAL gates
    kit_json = {}
    for kid in ("crayon-box", "ops-console"):
        out = T / f"kit_{kid}.json"
        r = run("scripts/design/kit_export.py", ["runtime", KITS / f"{kid}.json", "--out", out], env=ENV)
        if r.returncode == 0:
            kit_json[kid] = out

    def judge(name: str, kit: Path | None, tag: str) -> dict:
        src = FIX / f"{name}.js"
        out = T / f"tr_{tag}_{name}.json"
        tr = trace(src, out, *(["--kit", kit] if kit else []), env=ENV)
        if tr.returncode != 0:
            return {"trace": tr.stderr[-300:]}
        res = {}
        for gate, args in (("semantics", [out, "--scene-src", src]), ("invariants", [out, "--scene-src", src]),
                           ("density", ["trace", out])):
            r = run(f"scripts/gates/check_{gate}.py", args, env=ENV)
            g = gate_json(r)
            res[gate] = (r.returncode, g.get("counts"), g.get("findings"))
        res["trace_path"] = out
        return res

    results = {i: judge(i, None, "dark") for i in ids}
    for i in ids:
        r = results[i]
        ok_t = "trace" not in r
        s.check(f"component {i}: fixture passes S1-S8 on the real check_semantics gate",
                ok_t and r["semantics"][0] == 0, r.get("trace") or r.get("semantics"))
        s.check(f"component {i}: fixture passes C1-C8 on the real check_invariants gate",
                ok_t and r["invariants"][0] == 0, r.get("trace") or r.get("invariants"))
    bad = [i for i in ids if "trace" in results[i] or results[i]["semantics"][0] or results[i]["invariants"][0]]
    s.check(f"all {len(ids)} component fixtures pass S1-S8 and C1-C8 on the real gates", not bad and len(ids) >= 25, bad)
    bad_den = [i for i in ids if "trace" in results[i] or results[i]["density"][0]]
    s.check("every component fixture also passes the density gate (no 540px void under narration)", not bad_den,
            {i: results[i].get("density") for i in bad_den})

    if "crayon-box" in kit_json:
        lres = {i: judge(i, kit_json["crayon-box"], "light") for i in ids}
        lbad = [i for i in ids if "trace" in lres[i] or any(lres[i][g][0] for g in ("semantics", "invariants", "density"))]
        s.check("every component fixture still passes S1-S8, C1-C8 and density under a light style kit", not lbad,
                {i: lres[i] for i in lbad[:4]})
    else:
        s.check("light-kit re-run SKIPPED: kit export failed", False, "kit_export.py runtime failed")

    traces = {i: json.loads(results[i]["trace_path"].read_text()) for i in ids if "trace" not in results[i]}
    s.check("each fixture trace records the catalog form it drew (declareForm)",
            all(any(f.get("kind") == "catalog" and f.get("name") == i for f in traces[i].get("forms", [])) for i in traces),
            [i for i in traces if not any(f.get("name") == i for f in traces[i].get("forms", []))])
    marks_ok = all(b.get("meaning") for tr in traces.values() for fr in tr["frames"] for b in fr["boxes"]
                   if b.get("kind") == "mark")
    kinds = set(b.get("kind") for tr in traces.values() for fr in tr["frames"] for b in fr["boxes"])
    s.check("components draw only through instrumented primitives, and every mark records what it explains",
            marks_ok and kinds <= {"card", "text", "chip", "node", "shot", "mark"} and {"card", "text", "node", "mark"} <= kinds,
            sorted(kinds))
    conns = [c for tr in traces.values() for fr in tr["frames"] for c in fr["connectors"]]
    assets = [a for tr in traces.values() for fr in tr["frames"] for a in fr["assets"]]
    s.check("every connector a component draws declares from/to and every asset declares subjectId",
            conns and assets and all(c.get("from") and c.get("to") for c in conns) and all(a.get("subjectId") for a in assets),
            (len(conns), len(assets)))

    # a seeded defect drawn WITH a component is caught (the gates really see component output)
    wrong = T / "wrong_subject.js"
    wrong.write_text((FIX / "evidenceCard.js").read_text().replace("subjectId: 'timeout', srcW", "subjectId: 'another-case', srcW")
                     .replace("require('./_util.js')", f"require({json.dumps(str(FIX / '_util.js'))})"), encoding="utf-8")
    tw = T / "tr_wrong.json"
    trw = trace(wrong, tw, env=ENV)
    g = gate_json(run("scripts/gates/check_semantics.py", [tw], env=ENV)) if trw.returncode == 0 else {}
    s.check("a component asset about the wrong subject is caught by S5 (the gates see what components draw)",
            (g.get("counts") or {}).get("S5", 0) > 0, g.get("counts") or trw.stderr[-200:])

    # timing must be named; budgets throw unless explicitly opted out
    pe = node_eval(
        f"const C=require({json.dumps(str(RT))});const S=require({json.dumps(str(COMP))});const r={{}};"
        "const tryit=(k,f)=>{try{C.buildTrace({name:k,script:[{name:'a',start:0,end:2}],grid:1,draw:f});r[k]='ok'}catch(e){r[k]=e.message}};"
        "tryit('numeric',(z)=>S.funnel(z,{enter:3,stages:[{label:'a',value:2},{label:'b',value:1}]}));"
        "tryit('unknown',(z)=>S.funnel(z,{enter:'nope',stages:[{label:'a',value:2},{label:'b',value:1}]}));"
        "const many=Array.from({length:9},(_,i)=>({label:'s'+i,value:9-i}));"
        "tryit('over',(z)=>S.funnel(z,{enter:'a',stages:many}));"
        "tryit('optin',(z)=>S.funnel(z,{enter:'a',stages:many,overBudget:'the reviewer asked for every step'}));"
        "console.log(JSON.stringify(r));", T, "timing_budget")
    tb = json.loads(pe.stdout) if pe.returncode == 0 else {}
    s.check("a component given a numeric time instead of a named beat THROWS", "never" in tb.get("numeric", "") and "accepted" in tb.get("numeric", ""), tb)
    s.check("a component keyed to a renamed/deleted beat THROWS with the valid names", "unknown beat name" in tb.get("unknown", ""), tb)
    s.check("over budget THROWS with the split-into-overview-and-detail fix", "Split it into an overview" in tb.get("over", ""), tb)
    s.check("overBudget:'<reason>' is the explicit opt-in that lets an over-budget scene draw", tb.get("optin") == "ok", tb)

    # ===================================================== A3 — the runtime reads the active kit
    ka, kb = kit_json.get("crayon-box"), kit_json.get("ops-console")
    if ka and kb:
        KA, KB = json.loads(ka.read_text()), json.loads(kb.read_text())
        fx = FIX / "funnel.js"
        ta, tb2 = T / "kitA.json", T / "kitB.json"
        trace(fx, ta, "--kit", ka, env=ENV)
        trace(fx, tb2, "--kit", kb, env=ENV)
        A_, B_ = json.loads(ta.read_text()), json.loads(tb2.read_text())

        def colours(tr):
            return {(b.get("kind"), b.get(k)) for fr in tr["frames"] for b in fr["boxes"] for k in ("fill", "color", "stroke") if b.get(k)}

        def sizes(tr):
            return sorted({b.get("size") for fr in tr["frames"] for b in fr["boxes"] if b.get("kind") == "text"})

        def alpha_at(tr, t):
            fr = min(tr["frames"], key=lambda f: abs(f["t"] - t))
            return [round(b["alpha"], 4) for b in fr["boxes"] if b.get("kind") == "card"]

        s.check("the trace records which kit drew it", A_.get("kit", {}).get("id") == "crayon-box" and B_.get("kit", {}).get("id") == "ops-console",
                (A_.get("kit"), B_.get("kit")))
        s.check("two kits give different recorded colours from the same scene code (no code edit)",
                colours(A_) != colours(B_) and ("text", KA["text"]["color"]) in colours(A_) and ("text", KB["text"]["color"]) in colours(B_),
                (sorted(colours(A_))[:4], sorted(colours(B_))[:4]))
        line1 = A_["lines"][1]
        t_mid = line1["start"] + 0.2
        s.check("two kits give different recorded motion at the same t (entrance timing and easing from the kit)",
                alpha_at(A_, t_mid) != alpha_at(B_, t_mid), (alpha_at(A_, t_mid), alpha_at(B_, t_mid)))
        s.check("two kits give a different type scale in the recorded text", sizes(A_) != sizes(B_), (sizes(A_), sizes(B_)))

        # default: window.__C8_KIT__ set by a kit script, no setKit() call anywhere
        js = T / "kit_global.js"
        r = run("scripts/design/kit_export.py", ["runtime", KITS / "chalk-talk.json", "--js", "--out", js], env=ENV)
        dp = node_eval(
            "global.window={};" f"require({json.dumps(str(js))});"
            f"const C=require({json.dumps(str(RT))});const F=require({json.dumps(str(FIX / 'ledger.js'))});"
            "C.setWords(F.words);const tr=C.buildTrace({name:'g',script:F.script,draw:F.draw,grid:2});"
            "const t=tr.frames[tr.frames.length-1].boxes.find(b=>b.kind==='text');"
            "console.log(JSON.stringify({kit:tr.kit,color:t.color,want:window.__C8_KIT__.text.color}));", T, "kit_default")
        dj = json.loads(dp.stdout) if dp.returncode == 0 else {}
        s.check("the runtime reads window.__C8_KIT__ by default (a kit script loaded, no setKit call)",
                dj.get("kit", {}).get("id") == "chalk-talk" and dj.get("color") == dj.get("want"), dj or dp.stderr[-300:])
    else:
        s.check("kit switching checks SKIPPED: kit export failed", False, kit_json)

    hand = T / "hand_kit.json"
    hand.write_text(json.dumps({"kit": "harbour-fog-hand", "name": "Hand-made", "mode": "light",
                                "colors": {"paper": "#eef2f3", "ink": "#1b2a33", "muted": "#4f6069", "accent": "#a5480f",
                                           "positive": "#2d6e45", "negative": "#a3303f", "warning": "#835a00"},
                                "text": {"color": "#1b2a33", "font": "Georgia, serif", "sizes": {"body": 32, "h1": 50}},
                                "card": {"fill": "rgba(27,42,51,0.07)", "pad": 26}, "chip": {"fill": "#dde4e7", "color": "#1b2a33"},
                                "motion": {"fadeIn": 0.5, "lead": 0.1, "stagger_s": 0.1}}), encoding="utf-8")
    th = T / "hand.json"
    rh = trace(FIX / "checklist.js", th, "--kit", hand, env=ENV)
    H = json.loads(th.read_text()) if rh.returncode == 0 else {"frames": []}
    hc = {b.get("fill") for fr in H["frames"] for b in fr["boxes"] if b.get("kind") == "card"}
    s.check("a hand-defined kit that is in no catalog drives the runtime (its colours are recorded)",
            H.get("kit", {}).get("id") == "harbour-fog-hand" and "rgba(27,42,51,0.07)" in hc, (H.get("kit"), sorted(hc)[:3]))
    t0 = T / "nokit.json"
    trace(FIX / "checklist.js", t0, env=ENV)
    N = json.loads(t0.read_text())
    s.check("with no kit loaded the runtime keeps its previous defaults (trace kit is null)",
            N.get("kit") is None and any(b.get("color") == "#f2f4f8" for fr in N["frames"] for b in fr["boxes"] if b.get("kind") == "text"),
            N.get("kit"))

    # ====================================================== E2 — atLine / atWord THROW
    F_words = json.loads(node_eval(f"console.log(JSON.stringify(require({json.dumps(str(FIX / 'bigNumber.js'))}).words))",
                                   T, "words_dump").stdout)
    wfile = T / "words.json"
    wfile.write_text(json.dumps(F_words), encoding="utf-8")
    ok_tr = trace(FIX / "bigNumber.js", T / "bn_ok.json", "--words", wfile, env=ENV)
    s.check("a scene keyed to a spoken word traces when the word is in the aligned line", ok_tr.returncode == 0, ok_tr.stderr[-300:])
    cut = json.loads(json.dumps(F_words))
    rev = next(l for l in cut["lines"] if l["name"] == "reveal")
    rev["words"] = [w for w in rev["words"] if w["w"].lower().strip(",.") != "thousand"]
    rev["text"] = rev["text"].replace("thousand ", "")
    wcut = T / "words_cut.json"
    wcut.write_text(json.dumps(cut), encoding="utf-8")
    rc = trace(FIX / "bigNumber.js", T / "bn_cut.json", "--words", wcut, env=ENV)
    s.check("removing a word from a line makes the scene THROW at atWord (no silent mistiming)",
            rc.returncode != 0 and "NOT in that line" in rc.stderr and "'thousand'" in rc.stderr, rc.stderr[-300:])
    ren = json.loads(json.dumps(F_words))
    next(l for l in ren["lines"] if l["name"] == "reveal")["name"] = "reveal-renamed"
    wren = T / "words_ren.json"
    wren.write_text(json.dumps(ren), encoding="utf-8")
    rr = trace(FIX / "bigNumber.js", T / "bn_ren.json", "--words", wren, env=ENV)
    s.check("a line missing from the word timings makes atWord/atLine THROW with the valid line names",
            rr.returncode != 0 and "unknown line 'reveal'" in rr.stderr and "reveal-renamed" in rr.stderr, rr.stderr[-300:])

    wp = node_eval(
        f"const C=require({json.dumps(str(RT))});const W=require({json.dumps(str(wfile))});const r={{}};"
        "const t=(k,f)=>{try{r[k]=f()}catch(e){r[k]='THROW '+e.message}};"
        "t('word',()=>C.atWord('reveal','thousand',1,W));t('line',()=>C.atLine('setup',W).words.length);"
        "t('missingLine',()=>C.atLine('no-such-line',W));t('occ',()=>C.atWord('reveal','thousand',2,W));"
        "t('phrase',()=>C.atWord('reveal','twelve thousand',1,W));"
        "const W2=JSON.parse(JSON.stringify(W));W2.lines[1].words=W2.lines[1].words.filter(w=>w.w!=='per');"
        "t('inText',()=>C.atWord('reveal','per',1,W2));"
        "const W3=JSON.parse(JSON.stringify(W));W3.lines[0].words[0].start=NaN;t('nan',()=>C.setWords(W3));"
        "t('fromWords',()=>C.buildTrace({name:'fw',words:W,grid:1,draw:(z)=>{z.chip({text:'x',x:10,y:400,alpha:z.wordAlpha('reveal','second')})}}).lines.map(l=>l.name));"
        "console.log(JSON.stringify(r));", T, "words_probe")
    wr = json.loads(wp.stdout) if wp.returncode == 0 else {}
    rev_w = next(l for l in F_words["lines"] if l["name"] == "reveal")["words"]
    th_w = next(w for w in rev_w if w["w"].lower().strip(",.") == "thousand")
    s.check("atWord returns the aligner's own word timing (start and end), never an estimate",
            isinstance(wr.get("word"), dict) and wr["word"]["start"] == th_w["start"] and wr["word"]["end"] == th_w["end"], wr.get("word"))
    s.check("atLine of a line that is not in the timings THROWS", str(wr.get("missingLine", "")).startswith("THROW") and "unknown line" in wr["missingLine"], wr.get("missingLine"))
    s.check("asking for an occurrence beyond the count THROWS", "only 1 occurrence" in str(wr.get("occ")), wr.get("occ"))
    s.check("a multi-word phrase resolves from its first word's start to its last word's end",
            isinstance(wr.get("phrase"), dict) and wr["phrase"]["start"] < wr["phrase"]["end"] and wr["phrase"]["end"] == th_w["end"], wr.get("phrase"))
    s.check("a word that is in the line text but has no timing is named as an alignment gap",
            "aligner produced no timing" in str(wr.get("inText")), wr.get("inText"))
    s.check("word timings with a non-finite time are rejected when loaded", "THROW" in str(wr.get("nan")) and "non-finite" in str(wr.get("nan")), wr.get("nan"))
    s.check("a scene can take its script straight from the word timings (one source for names and times)",
            wr.get("fromWords") == [l["name"] for l in F_words["lines"]], wr.get("fromWords"))

    # ==================================================== E10 — seeded randomness
    rp = "const C=require(%s);const r=C.random(%s);console.log(JSON.stringify([r(),r(),r(),r.int(1,6),r.pick(['a','b','c'])]));"
    a1 = node(["-e", rp % (json.dumps(str(RT)), "1234")]).stdout.strip()
    a2 = node(["-e", rp % (json.dumps(str(RT)), "1234")]).stdout.strip()
    a3 = node(["-e", rp % (json.dumps(str(RT)), "1235")]).stdout.strip()
    s1 = node(["-e", rp % (json.dumps(str(RT)), "'layout-seed'")]).stdout.strip()
    s2 = node(["-e", rp % (json.dumps(str(RT)), "'layout-seed'")]).stdout.strip()
    s.check("C8.random(seed) reproduces the same sequence in separate processes, and a different seed differs",
            a1 and a1 == a2 and a1 != a3, (a1, a3))
    s.check("a string seed is hashed deterministically (same string, same draw)", s1 and s1 == s2 and s1 != a1, (s1, s2))
    nt = traces.get("network", {})
    s.check("every seed a scene uses is recorded in its trace (network layout seed)",
            any(r_.get("seed") == 11 and r_.get("label") == "network-layout" and r_.get("maxDraws", 0) > 0 for r_ in nt.get("random", [])),
            nt.get("random"))
    rn = node(["-e", f"const C=require({json.dumps(str(RT))});try{{C.random()}}catch(e){{console.log(e.message)}}"]).stdout
    s.check("random() without a seed THROWS", "needs a seed" in rn, rn)

    # ==================================================== E10 — clock guard + determinism gate
    st = node([RT, "selftest"])
    so = st.stdout
    s.check("scene_runtime.js selftest passes (pre-existing and new guarantees)", st.returncode == 0, so[-400:])
    for needle in ("out-of-range index THROWS (no clamp)", "unknown beat name THROWS", "NaN alpha THROWS at draw",
                   "bounded text stays inside its card", "connector without from/to THROWS", "asset without subjectId THROWS"):
        s.check(f"pre-existing runtime guarantee still asserted: {needle}", f"ok   {needle}" in so, so[-200:])
    for needle in ("Math.random during a frame draw THROWS", "Date.now during a frame draw THROWS",
                   "performance.now during a frame draw THROWS", "new Date() during a frame draw THROWS",
                   "the clock guard is released after the trace", "a SWALLOWED clock read is still recorded in the trace",
                   "the same t rendered twice gives an identical frame"):
        s.check(f"runtime guard asserted: {needle}", f"ok   {needle}" in so, so[-200:])

    neg_draw, neg_load = FIX / "neg_random_in_draw.js", FIX / "neg_random_at_load.js"
    neg_css, clean_html = FIX / "neg_css_animation.html", FIX / "clean_page.html"
    det = lambda *a: gate_json(run("scripts/gates/check_determinism.py", list(a), env=ENV, timeout=300))  # noqa: E731
    g1 = det(neg_draw)
    s.check("a scene using Math.random is caught STATICALLY (file and line named)",
            g1.get("status") == "REVIEW" and any(f["rule"] == "clock.math_random" and f["line"] == 10 for f in g1["static"]["findings"]),
            g1.get("static"))
    g2 = det(neg_draw, "--dynamic")
    s.check("a scene using Math.random is caught DYNAMICALLY (the clock guard throws during the draw)",
            any(f["rule"] == "dynamic.clock_during_draw" for f in g2.get("dynamic", {}).get("findings", [])), g2.get("dynamic"))
    g3 = det(neg_load, "--dynamic")
    s.check("randomness read at module load (outside the draw) is caught by the two-process trace comparison",
            any(f["rule"] == "dynamic.cross_run_differs" for f in g3.get("dynamic", {}).get("findings", [])), g3.get("dynamic"))
    g4 = det(neg_css)
    rules4 = {f["rule"] for f in g4.get("static", {}).get("findings", [])}
    s.check("a CSS-animation scene is caught (@keyframes, animation: and transition: all named)",
            {"css.keyframes", "css.animation", "css.transition"} <= rules4, sorted(rules4))
    benv = dict(ENV)
    if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        for cand in (Path(REAL_HOME) / "Library" / "Caches" / "ms-playwright", Path(REAL_HOME) / ".cache" / "ms-playwright"):
            if cand.is_dir():
                benv["PLAYWRIGHT_BROWSERS_PATH"] = str(cand)
                break
    us = subprocess.run([PY, "-c", "import site;print(site.getusersitepackages())"], capture_output=True, text=True).stdout.strip()
    if us and Path(us).is_dir():
        benv["PYTHONPATH"] = os.pathsep.join(x for x in (os.environ.get("PYTHONPATH"), us) if x)
    g5 = gate_json(run("scripts/gates/check_determinism.py", [neg_css, clean_html, "--dynamic"], env=benv, timeout=300))
    dyn5 = g5.get("dynamic", {})
    if dyn5.get("skipped"):
        s.check("headless-browser determinism check SKIPPED here (static scan still catches CSS animation)", True, dyn5.get("skipped"))
    else:
        css_dyn = [f for f in dyn5.get("findings", []) if f["file"].endswith("neg_css_animation.html")]
        clean_dyn = [f for f in dyn5.get("findings", []) if f["file"].endswith("clean_page.html")]
        s.check("a CSS-animated page is caught in headless Chrome (running animations / same t renders differently)",
                any(f["rule"] in ("dynamic.running_animations", "dynamic.same_t_differs") for f in css_dyn), dyn5)
        s.check("a clean HTML scene renders identically at the same t in headless Chrome (no false positive)",
                not clean_dyn, clean_dyn)
    g6 = det(*[FIX / f"{i}.js" for i in ids], FIX / "invented_queue_lane.js", "--dynamic")
    s.check("every component fixture passes the determinism gate: static scan clean and the same t traced twice is identical",
            g6.get("status") == "PASS" and g6.get("dynamic_scenes") == len(ids) + 1, (g6.get("counts"), g6.get("summary")))
    rep = traces.get("pipeline", {})
    tr2 = T / "pipeline_again.json"
    trace(FIX / "pipeline.js", tr2, env=ENV)
    s.check("the same scene traced in two separate runs gives a byte-identical trace",
            tr2.exists() and results["pipeline"]["trace_path"].read_text() == tr2.read_text(), "")
    edge = T / "edge_cases.js"
    edge.write_text("// SRC: test\n// Math.random() in a comment is not a finding\n"
                    "const when = new Date(0);  // a literal date is deterministic\n"
                    "const url = 'http://example.invalid/x';\n"
                    "const j = Date.now();  // c8-allow: build stamp written once into the sidecar, never drawn\n",
                    encoding="utf-8")
    g7 = det(edge)
    s.check("comments are stripped first and a literal new Date(0) is not flagged (no false positives)",
            g7.get("status") == "PASS", g7.get("static"))
    s.check("a deliberate exception is reported as WAIVED with its reason, never silently dropped",
            any(w["rule"] == "clock.date_now" and "build stamp" in w.get("waiver", "") for w in g7.get("static", {}).get("waived", [])),
            g7.get("static"))

    # ============================================================ H1 — freedom doctrine
    gate_src = {p.name: p.read_text(encoding="utf-8") for p in (ROOT / "scripts" / "gates").glob("check_*.py")}
    refs = [n for n, t in gate_src.items() if re.search(r"scene_types|scene_components", t)]
    s.check("no gate references the scene catalog or the component library (outcomes only)", not refs and "check_determinism.py" in gate_src, refs)
    inv = FIX / "invented_queue_lane.js"
    ti = T / "invented.json"
    rti = trace(inv, ti, env=ENV)
    inv_res = {}
    if rti.returncode == 0:
        for gate, args in (("semantics", [ti, "--scene-src", inv]), ("invariants", [ti, "--scene-src", inv]),
                           ("density", ["trace", ti]), ("determinism", [inv, "--dynamic"])):
            inv_res[gate] = run(f"scripts/gates/check_{gate}.py", args, env=ENV, timeout=300).returncode
    s.check("an invented scene form (not in the catalog) passes every gate: S1-S8, C1-C8, density and determinism",
            inv_res and all(v == 0 for v in inv_res.values()), inv_res or rti.stderr[-300:])
    I = json.loads(ti.read_text()) if ti.exists() else {}
    s.check("the invented form is recorded in the trace as invented, with its reason",
            any(f.get("kind") == "invented" and f.get("name") == "queue-lane" and f.get("reason") for f in I.get("forms", [])),
            I.get("forms"))
    s.check("the invented form's name is not a catalog id (it really is outside the catalog)", "queue-lane" not in ids, "")
    nr = node(["-e", f"const C=require({json.dumps(str(RT))});try{{C.buildTrace({{name:'x',script:[{{name:'a',start:0,end:1}}],grid:1,"
                     "draw:(z)=>z.declareForm({invented:'mystery'})})}catch(e){console.log(e.message)}"]).stdout
    s.check("declaring an invented form without a reason THROWS (inventions are recorded, not anonymous)", "needs a reason" in nr, nr)

    # ===================================================== the shipped example still works
    ex = T / "example.json"
    rex = trace(ROOT / "examples" / "example_scene.js", ex, env=ENV)
    exs = run("scripts/gates/check_semantics.py", [ex, "--scene-src", ROOT / "examples" / "example_scene.js"], env=ENV).returncode if rex.returncode == 0 else 9
    exi = run("scripts/gates/check_invariants.py", [ex], env=ENV).returncode if rex.returncode == 0 else 9
    s.check("the shipped example scene traces through the new trace CLI and still passes S1-S8 and C1-C8",
            rex.returncode == 0 and exs == 0 and exi == 0, (rex.stderr[-200:], exs, exi))

sys.exit(s.finish())
