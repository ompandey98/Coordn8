#!/usr/bin/env python3
"""Freedom suite (H1, H4, H5): catalogs are menus, never fences — proven three ways.

H1  check_freedom statically audits every gate that exists (glob) and must PASS on the real gates
    and FAIL seeded gates that reject an option for not being listed in a catalog. An
    invented-everything fixture build (invented palette, kit, diagram form, scene form, caption
    style and page component, each with its reason) is run through every outcome gate that
    applies, and each gate's result is a check of its own.
H4  three cheap treatments (trace + one contact sheet each) are made for one pivotal moment; the
    blind reviewer API ranks them; ranked.json keeps the order and the reasons; the best passing
    one ships and the others stay as alternates. The bar is never lowered.
H5  inventions are logged to <COORDN8_HOME>/library/inventions.jsonl; a second passing use in a
    different build triggers the promotion offer exactly once; promote writes only under
    <COORDN8_HOME>/library.
Package data/ files are hashed before and after every section and must be byte-identical.
Hermetic: temp dirs and a sandboxed COORDN8_HOME / CLAUDE_HOME / HOME; no network.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import PY, ROOT, Suite, run, sandbox_env, tmpdir  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
FX = ROOT / "tests" / "v2" / "fixtures" / "freedom"
SCENES = ROOT / "tests" / "v2" / "fixtures" / "scenes"
DATA = ROOT / "data"
NODE = shutil.which("node")

s = Suite("freedom")
REAL_HOME = os.path.expanduser("~")     # captured before sandboxing; used only to READ a cached browser
try:
    import site
    USER_SITE = site.getusersitepackages()  # where --user Pillow / playwright live; read-only
except Exception:  # noqa: BLE001
    USER_SITE = ""


def senv(tmp: Path) -> dict:
    """sandbox_env plus read-only access to the user's site-packages and cached browser."""
    e = sandbox_env(tmp)
    e["PYTHONDONTWRITEBYTECODE"] = "1"
    if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        for cand in (Path(REAL_HOME) / "Library" / "Caches" / "ms-playwright", Path(REAL_HOME) / ".cache" / "ms-playwright"):
            if cand.is_dir():
                e["PLAYWRIGHT_BROWSERS_PATH"] = str(cand)
                break
    if USER_SITE and Path(USER_SITE).is_dir():
        e["PYTHONPATH"] = os.pathsep.join(x for x in (os.environ.get("PYTHONPATH"), USER_SITE) if x)
    return e



def data_hash() -> str:
    h = hashlib.sha256()
    for p in sorted(DATA.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(DATA)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def gate_json(r) -> dict:
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return {}


DATA0 = data_hash()

# ====================================================================== H1 — the static audit
SEEDS = {
    "fence_palette_not_in": ("FAIL", '''import json, sys
from pathlib import Path
CATALOG = Path(__file__).resolve().parents[2] / "data" / "palettes.json"
def main():
    ds = json.load(open(sys.argv[1]))
    ids = {f["id"] for f in json.loads(CATALOG.read_text())["families"]}
    findings = []
    fam = ds["palette"]["family"]
    if fam not in ids:
        findings.append({"check": "unknown-palette", "detail": fam})
    print(json.dumps({"status": "REVIEW" if findings else "PASS"}))
    return 1 if findings else 0
'''),
    "fence_else_branch_exit": ("FAIL", '''import json
EXIT_PASS, EXIT_REVIEW = 0, 1
def main(fam):
    cat = json.load(open("data/palettes.json"))
    known = [f["id"] for f in cat["families"]]
    if fam in known:
        return EXIT_PASS
    else:
        return EXIT_REVIEW
'''),
    "fence_next_is_none": ("FAIL", '''import json
def check(fig):
    types = json.load(open("data/diagram_types.json"))["types"]
    problems = []
    entry = next((t for t in types if t["id"] == fig["type"]), None)
    if entry is None:
        problems.append("type is not in the catalog")
    return problems
'''),
    "fence_helper_predicate": ("FAIL", '''import json
def load():
    return json.load(open("data/scene_types.json"))
def known(x, cat):
    return x in {t["id"] for t in cat["types"]}
def main(scene):
    errors = []
    if not known(scene, load()):
        errors.append("scene type not listed")
    return errors
'''),
    "fence_comprehension": ("FAIL", '''import json
from pathlib import Path
TYPES = {t["id"] for t in json.loads((Path("data") / "diagram_types.json").read_text())["types"]}
def main(plan):
    bad = [f["id"] for f in plan["figures"] if f["type"] not in TYPES]
    return {"status": "REVIEW" if bad else "PASS", "findings": bad}
'''),
    "fence_requires_declared_invented": ("FAIL", '''import json
def main(figs):
    ids = {t["id"] for t in json.load(open("data/diagram_types.json"))["types"]}
    findings = []
    for f in figs:
        if f["type"] not in ids and not f.get("invented"):
            findings.append(f["id"])
    return findings
'''),
    "fence_kit_folder_assert": ("FAIL", '''from pathlib import Path
KITS = Path(__file__).parent / "data" / "style_kits"
def main(kit):
    known = [p.stem for p in KITS.glob("*.json")]
    assert kit in known, "unknown kit"
    return 0
'''),
    "fence_caption_get": ("FAIL", '''import json
def main(style_id):
    styles = {s["id"]: s for s in json.load(open("data/caption_styles.json"))["styles"]}
    issues = []
    if not styles.get(style_id):
        issues.append("caption style is not a catalog style")
    return issues
'''),
    "clean_budget_defaults": ("PASS", '''import json
GLOBAL = {"nodes": 9}
def main(fig):
    per = {t["id"]: t.get("budget", {}) for t in json.load(open("data/diagram_types.json"))["types"]}
    caps = dict(GLOBAL)
    if fig.get("type") in per:
        caps.update(per[fig["type"]])
    findings = []
    if fig["nodes"] > caps["nodes"]:
        findings.append("over budget")
    return findings
'''),
    "clean_records_source_only": ("PASS", '''import json
def main(ds):
    ids = {f["id"] for f in json.load(open("data/palettes.json"))["families"]}
    fam = ds["palette"]["family"]
    source = "catalog" if fam in ids else "invented"
    findings = []
    if ds.get("contrast", 5) < 4.5:
        findings.append("contrast")
    return {"source": source, "findings": findings, "status": "REVIEW" if findings else "PASS"}
'''),
    "clean_word_lexicon": ("PASS", '''import json
def main(words):
    rules = json.load(open("data/language_rules.json"))
    lex = set(rules.get("signal_words", []))
    findings = []
    for w in words:
        if w in lex:
            findings.append(w)
    return findings
'''),
}
HELPER = '''def check_known(x, ids):
    findings = []
    if x not in ids:
        findings.append("not listed")
    return findings
'''
VIA_HELPER = '''import json
from fencehelp import check_known
def main(fam):
    ids = [f["id"] for f in json.load(open("data/palettes.json"))["families"]]
    return check_known(fam, ids)
'''

r = run("scripts/gates/check_freedom.py", timeout=180)
real = gate_json(r)
gate_files = sorted(p for p in (ROOT / "scripts" / "gates").glob("*.py"))
review_files = sorted(p for p in (ROOT / "scripts" / "review").glob("*.py") if p.name != "__init__.py") \
    if (ROOT / "scripts" / "review").is_dir() else []
audited = {g["gate"] for g in real.get("gates", [])}
s.check("check_freedom PASSES on the real gates: no gate fails a value for not being in a catalog",
        r.returncode == 0 and real.get("status") == "PASS",
        [(g["gate"], g["why"]) for g in real.get("gates", []) if g["verdict"] != "clean"] or r.stderr[-300:])
s.check("check_freedom audits every gate that exists at run time (glob of scripts/gates and scripts/review)",
        all(str(p.relative_to(ROOT)) in audited for p in gate_files + review_files)
        and len(audited) >= len(gate_files), (len(audited), len(gate_files)))
s.check("check_freedom reports, for every gate, why it is clean (no catalog / values only / defaults-only lines)",
        real.get("gates") and all(g.get("why") and g["verdict"] == "clean" for g in real["gates"]),
        [g["gate"] for g in real.get("gates", []) if not g.get("why")])
s.check("check_freedom names the catalogs a gate reads and its membership tests (diagram budget reads its "
        "type budgets only to choose defaults)",
        any(g["gate"].endswith("check_diagram_budget.py") and "diagram_types.json" in g["catalogs_read"]
            and all(m["use"] == "defaults only" for m in g["membership_tests"]) for g in real.get("gates", [])),
        [g for g in real.get("gates", []) if g["gate"].endswith("check_diagram_budget.py")])
s.check("check_freedom separates option catalogs (menus) from reference data, and the palette, kit, diagram, "
        "scene and caption catalogs are all taint sources",
        {"palettes.json", "diagram_types.json", "scene_types.json", "caption_styles.json", "style_kits",
         "page/components"} <= set(real.get("catalog_sources", [])) and isinstance(real.get("reference_data"), dict),
        real.get("catalog_sources"))

with tmpdir() as td:
    seeddir = Path(td) / "gates"
    seeddir.mkdir()
    for name, (_, src) in SEEDS.items():
        (seeddir / f"{name}.py").write_text(src, encoding="utf-8")
    (seeddir / "fencehelp.py").write_text(HELPER, encoding="utf-8")
    (seeddir / "fence_via_helper.py").write_text(VIA_HELPER, encoding="utf-8")
    rs = run("scripts/gates/check_freedom.py", [seeddir], timeout=120)
    sj = gate_json(rs)
    by = {Path(g["gate"]).stem: g for g in sj.get("gates", [])}
    pal = by.get("fence_palette_not_in", {})
    s.check("check_freedom FAILS a seeded gate that rejects a palette because its id is not in palettes.json",
            rs.returncode == 1 and sj.get("status") == "FAIL" and pal.get("verdict") == "FAIL"
            and "palettes.json" in pal.get("catalogs_read", []) and "fam not in ids" in pal.get("why", ""),
            pal or rs.stdout[-300:])
    s.check("check_freedom points at the exact line of the fence and what the not-listed branch does",
            any(f["line"] == 9 and "findings" in f["fence"] for f in pal.get("fences", [])), pal.get("fences"))
    for name, (want, _) in SEEDS.items():
        if want != "FAIL" or name == "fence_palette_not_in":
            continue
        g = by.get(name, {})
        s.check(f"check_freedom FAILS a seeded fence: {name.replace('fence_', '').replace('_', ' ')}",
                g.get("verdict") == "FAIL", g)
    g = by.get("fence_via_helper", {})
    s.check("check_freedom FAILS a gate that hands a catalog to an imported helper which fences on it",
            g.get("verdict") == "FAIL" and "helper" in g.get("why", ""), g)
    cleans = [n for n, (w, _) in SEEDS.items() if w == "PASS"]
    s.check("check_freedom keeps gates clean that read a catalog for budgets, record catalog-vs-invented as a "
            "note, or measure text with a word list (no false fence)",
            all(by.get(n, {}).get("verdict") == "clean" for n in cleans),
            {n: by.get(n, {}).get("why") for n in cleans})
    s.check("check_freedom keeps a helper clean when it is never handed a catalog",
            by.get("fencehelp", {}).get("verdict") == "clean", by.get("fencehelp"))
    (seeddir / "broken.py").write_text("def x(:\n", encoding="utf-8")
    rb = run("scripts/gates/check_freedom.py", [seeddir / "broken.py"], timeout=60)
    s.check("an unparseable gate is reported as ERROR (exit 3), never silently passed",
            rb.returncode == 3 and gate_json(rb).get("status") == "ERROR", rb.stdout[-200:])
s.check("package data/ files are byte-identical after the static audit", data_hash() == DATA0, "data/ changed")

# ====================================================================== H1 — the invented fixture
ds = json.loads((FX / "design_system.json").read_text(encoding="utf-8"))
plan = json.loads((FX / "diagram_plan.json").read_text(encoding="utf-8"))
content = json.loads((FX / "page_content.json").read_text(encoding="utf-8"))
scene_src = (FX / "invented_tide_ladder.js").read_text(encoding="utf-8")
cat_pal = {f["id"] for f in json.loads((DATA / "palettes.json").read_text())["families"]}
cat_kits = {p.stem for p in (DATA / "style_kits").glob("*.json")}
cat_diag = {t["id"] for t in json.loads((DATA / "diagram_types.json").read_text())["types"]}
cat_scene = {t["id"] for t in json.loads((DATA / "scene_types.json").read_text())["types"]}
cat_cap = {t["id"] for t in json.loads((DATA / "caption_styles.json").read_text())["styles"]}
cat_comp = {p.stem for p in (ROOT / "templates" / "page" / "components").glob("*.html")}
fig = plan["figures"][0]
sec = content["sections"][0]
inv = {
    "palette": (ds["palette"]["family"], ds["palette"].get("reason"), cat_pal),
    "kit": (ds["kit"], ds["kit_spec"].get("reason"), cat_kits),
    "diagram form": (fig["invented"]["name"], fig["invented"].get("why"), cat_diag),
    "scene form": ("tide-ladder", "reason: 'retries whose waits" if "invented: 'tide-ladder'" in scene_src
                   and "reason:" in scene_src else "", cat_scene),
    "caption style": (ds["caption_style"]["id"], ds["caption_style"].get("reason"), cat_cap),
    "page component": (sec["custom_kind"], sec.get("custom_explains"), cat_comp),
}
for what, (name, reason, catalog) in inv.items():
    bare = str(name).split(":", 1)[-1]
    s.check(f"fixture: the {what} '{name}' is invented (in no catalog) and carries a recorded reason",
            name and bare not in catalog and str(name) not in catalog and reason, (name, reason))

from design.pick_kit import validate_kit  # noqa: E402
sys.path.insert(0, str(ROOT / "scripts" / "render"))
import caption_marks  # noqa: E402
results = {}

r = run("scripts/gates/check_palette.py", [FX / "design_system.json", "--html", FX / "figure.html"])
results["check_palette"] = gate_json(r).get("status")
s.check("invented fixture: check_palette PASSES (invented palette, both skins, and the invented figure's paint)",
        r.returncode == 0 and results["check_palette"] == "PASS", gate_json(r).get("summary"))
errs = validate_kit(ds["kit_spec"])
results["kit outcome rules (pick_kit.validate_kit)"] = "PASS" if not errs else "REVIEW"
s.check("invented fixture: the invented kit passes the kit outcome rules (validate_kit takes no catalog)",
        not errs, errs)
cerrs = caption_marks.validate_style(ds["caption_style"])
results["caption style outcome rules (caption_marks.validate_style)"] = "PASS" if not cerrs else "REVIEW"
s.check("invented fixture: the invented caption style passes the caption outcome rules", not cerrs, cerrs)
r = run("scripts/gates/check_caption_marks.py", [FX / "captions.json", "--kit", FX / "design_system.json"])
results["check_caption_marks"] = gate_json(r).get("status")
s.check("invented fixture: check_caption_marks PASSES with the invented design system",
        r.returncode == 0 and results["check_caption_marks"] == "PASS", gate_json(r).get("summary"))

with tmpdir() as td:
    T = Path(td)
    shutil.copyfile(FX / "diagram_plan.json", T / "diagram_plan.json")
    r = run("scripts/gates/check_diagram_budget.py", [T])
    g = gate_json(r)
    results["check_diagram_budget"] = g.get("status")
    s.check("invented fixture: check_diagram_budget PASSES and gives the invented form the global caps",
            r.returncode == 0 and g.get("status") == "PASS"
            and g.get("figures", [{}])[0].get("caps_from", "").startswith("global (invented form)"), g)
    rb = run("scripts/render/build_page.py", [FX / "page_content.json", "--design-system", FX / "design_system.json",
                                              "--out", T / "page.html", "--plan-out", T / "page_plan.json"],
             timeout=120)
    bj = gate_json(rb)
    results["build_page (kit outcome rules on build)"] = "PASS" if rb.returncode == 0 else "REVIEW"
    s.check("invented fixture: the page builds with the invented kit and palette (build_page refuses kits that "
            "fail outcome rules)", rb.returncode == 0 and bj.get("kit") == ds["kit"]
            and bj.get("palette_family") == ds["palette"]["family"], rb.stdout[-300:] or rb.stderr[-300:])
    r = run("scripts/gates/check_diagram_plan.py", [T])
    g = gate_json(r)
    results["check_diagram_plan"] = g.get("status")
    s.check("invented fixture: check_diagram_plan PASSES on the drawn invented figure (found by data-figure in "
            "the built page)", r.returncode == 0 and g.get("status") == "PASS" and "fig-tide-ladder" in g.get("drawn", []),
            g)
    r = run("scripts/gates/visual_density.py", [T / "page_plan.json"])
    results["visual_density (page plan)"] = gate_json(r).get("status")
    s.check("invented fixture: visual_density PASSES on the page plan with the invented page component",
            r.returncode == 0 and results["visual_density (page plan)"] == "PASS", gate_json(r).get("summary"))
    ra = subprocess.run([PY, str(ROOT / "scripts/gates/check_page_a11y.py"), str(T / "page.html"),
                         "--json", str(T / "a11y.json")], capture_output=True, text=True, timeout=170,
                        env=dict(os.environ, **senv(T / "sb")))
    aj = json.loads((T / "a11y.json").read_text()) if (T / "a11y.json").exists() else gate_json(ra)
    results["check_page_a11y"] = aj.get("status")
    if ra.returncode == 2:
        s.check("invented fixture: check_page_a11y SKIPPED — no headless browser here (live page checks "
                "UNVERIFIED on this machine)", True, aj.get("summary"))
    else:
        s.check("invented fixture: check_page_a11y PASSES (contrast both skins, focus, affordance, phone, "
                "tooltips, print) on the invented page", ra.returncode == 0 and aj.get("status") == "PASS",
                aj.get("summary"))
    if NODE:
        tr = T / "trace.json"
        rt = subprocess.run([NODE, str(ROOT / "scripts/render/scene_runtime.js"), "trace",
                             str(FX / "invented_tide_ladder.js"), "--out", str(tr)],
                            capture_output=True, text=True, timeout=120)
        trace = json.loads(tr.read_text()) if tr.exists() else {}
        s.check("invented fixture: the runtime records the scene form as INVENTED with its reason",
                any(f.get("kind") == "invented" and f.get("name") == "tide-ladder" and f.get("reason")
                    for f in trace.get("forms", [])), rt.stderr[-300:] or trace.get("forms"))
        for gname, args in (("check_semantics", [tr, "--scene-src", FX / "invented_tide_ladder.js"]),
                            ("check_invariants", [tr, "--scene-src", FX / "invented_tide_ladder.js"]),
                            ("check_density", ["trace", tr]),
                            ("check_determinism", [FX / "invented_tide_ladder.js", "--dynamic"])):
            r = run(f"scripts/gates/{gname}.py", args, timeout=150)
            results[gname] = gate_json(r).get("status")
            s.check(f"invented fixture: {gname} PASSES on the invented scene", r.returncode == 0
                    and results[gname] == "PASS", gate_json(r).get("summary") or r.stderr[-300:])
        TRACE_TEXT = tr.read_text() if tr.exists() else ""
    else:
        TRACE_TEXT = ""
        s.check("invented fixture: scene gates SKIPPED — node not on PATH (S1-S8/C1-C8 on the invented scene "
                "UNVERIFIED here)", True, "node missing")
    # negative control: the same gates still catch a BAD invented option
    bad = copy.deepcopy(ds)
    bad["palette"]["family"] = "invented:unreadable"
    bad["palette"]["roles"]["ink"] = bad["palette"]["roles"]["paper"]
    (T / "bad_ds.json").write_text(json.dumps(bad), encoding="utf-8")
    r = run("scripts/gates/check_palette.py", [T / "bad_ds.json"])
    s.check("control: an invented palette with unreadable ink is still caught by check_palette (outcomes are "
            "judged, not waved through)", r.returncode == 1 and "role-contrast" in json.dumps(gate_json(r)),
            gate_json(r).get("summary"))
    bplan = copy.deepcopy(plan)
    bplan["figures"][0]["invented"].pop("why")
    bplan["figures"][0]["nodes"] = 30
    (T / "bad_plan.json").write_text(json.dumps(bplan), encoding="utf-8")
    r1 = run("scripts/gates/check_diagram_plan.py", [T / "bad_plan.json"])
    r2 = run("scripts/gates/check_diagram_budget.py", [T / "bad_plan.json"])
    s.check("control: an invented diagram form with no reason, and one over budget, are still caught",
            r1.returncode == 1 and r2.returncode == 1, (gate_json(r1).get("summary"), gate_json(r2).get("summary")))
FIXTURE_GATES = dict(results)
s.check("invented fixture: every outcome gate that applies was run and passed (list in the report)",
        FIXTURE_GATES and all(v in ("PASS",) for k, v in FIXTURE_GATES.items() if k != "check_page_a11y")
        and FIXTURE_GATES.get("check_page_a11y") in ("PASS", "SKIPPED"), FIXTURE_GATES)
print("  fixture gate results:", json.dumps(FIXTURE_GATES))
s.check("package data/ files are byte-identical after the invented-fixture gates", data_hash() == DATA0, "data/ changed")

# ====================================================================== H4 — variant treatments
STUB = '''import json
from pathlib import Path
def rank(variants_dir, ranking=None):
    review = Path(variants_dir) / "review"
    labels = json.loads((review / ".label_map.json").read_text())["labels"]
    files = sorted(p.name for p in review.iterdir() if not p.name.startswith("."))
    order = sorted(labels.values())
    return [{"label": lab, "reason": f"candidate {lab} shows the sentence with every label legible, rank {i + 1}"}
            for i, lab in enumerate(order)]
'''
STUB_NOREASON = '''import json
from pathlib import Path
def rank(variants_dir, ranking=None):
    labels = json.loads((Path(variants_dir) / "review" / ".label_map.json").read_text())["labels"]
    return [{"label": lab, "reason": ""} for lab in sorted(labels.values())]
'''
if NODE:
    with tmpdir() as td:
        T = Path(td)
        env = senv(T)
        build = T / "proj" / "Coordn8" / "_build"
        build.mkdir(parents=True)
        spec = {"sentence": "Each failed call climbs one rung of the ladder.",
                "treatments": [
                    {"idea": "retries as a ladder whose waits rise like a tide", "technique": "analogy-by-structure",
                     "scene": str(FX / "invented_tide_ladder.js")},
                    {"idea": "the four tries laid out along a timeline", "technique": "constraint-then-relax",
                     "scene": str(SCENES / "timeline.js")},
                    {"idea": "one big number for the longest wait", "technique": "negative-space",
                     "scene": str(SCENES / "bigNumber.js")}]}
        (T / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
        rm = run("scripts/design/variants.py", ["make", "opening", "--build", build, "--spec", T / "spec.json",
                                                 "--seed", "4471"], env=env, timeout=240)
        mj = gate_json(rm)
        vd = build / "variants" / "opening"
        vs = sorted(d for d in vd.glob("v*") if d.is_dir())
        s.check("variants make: three treatments created, each = one trace + one contact sheet (svg, and png "
                "for the reviewer)",
                len(vs) == 3 and all((d / "trace.json").exists() and (d / "sheet.svg").exists()
                                     and (d / "sheet.png").exists() for d in vs), mj.get("summary") or rm.stderr[-300:])
        gates_ok = [json.loads((d / "gates.json").read_text()) for d in vs if (d / "gates.json").exists()]
        s.check("variants make: every treatment ran the outcome gates (semantics, invariants, density) on its trace",
                len(gates_ok) == 3 and all(set(g["gates"]) == {"semantics", "invariants", "density"} for g in gates_ok)
                and all(g["pass"] for g in gates_ok), gates_ok)
        review = vd / "review"
        brief_text = " ".join(p.read_text(errors="replace") for p in review.glob("*.md")) if review.is_dir() else ""
        shown = sorted(p.name for p in review.iterdir() if not p.name.startswith(".")) if review.is_dir() else []
        leaked = [t["idea"] for t in spec["treatments"] if t["idea"] in brief_text] + \
                 [t["technique"] for t in spec["treatments"] if t["technique"] in brief_text]
        s.check("variants: the blind brief holds only the candidate sheets and the exact sentence — no idea, "
                "technique or v1/v2/v3 name",
                spec["sentence"] in brief_text and not leaked and "v1" not in brief_text
                and all(n.startswith("candidate_") or n.endswith(".md") for n in shown), (shown, leaked))
        r0 = run("scripts/design/variants.py", ["rank", "opening", "--build", build], env=env, timeout=60)
        j0 = gate_json(r0)
        s.check("variants rank before any reviewer answer is SKIPPED with the instruction — a ranking is never "
                "made up", r0.returncode == 2 and j0.get("status") == "SKIPPED" and not (vd / "ranked.json").exists(),
                j0.get("summary"))
        lm = json.loads((review / ".label_map.json").read_text())["labels"] if (review / ".label_map.json").exists() else {}
        order = sorted(lm, key=lambda v: lm[v])  # reviewer's choice: label order
        ranking = {"ranking": [{"label": lm[v], "shows_what_is_said": "match",
                                "reason": f"candidate {lm[v]} reads clearly against the sentence, ranked {i + 1}"}
                               for i, v in enumerate(order)]}
        (review / "ranking.json").write_text(json.dumps(ranking), encoding="utf-8")
        rr = run("scripts/design/variants.py", ["rank", "opening", "--build", build], env=env, timeout=60)
        rj = gate_json(rr)
        ranked = json.loads((vd / "ranked.json").read_text()) if (vd / "ranked.json").exists() else {}
        api = (ROOT / "scripts" / "review" / "frame_review.py").exists()
        s.check("variants rank through the blind reviewer API: ranked.json holds a ranked order with the "
                "reviewer's reason for every treatment",
                rr.returncode == 0 and len(ranked.get("ranked", [])) == 3
                and [x["rank"] for x in ranked["ranked"]] == [1, 2, 3]
                and all(len(x["reason"].split()) >= 4 for x in ranked["ranked"])
                and ("frame_review" in ranked.get("reviewer", {}).get("source", "") if api else True),
                rj.get("summary") or rr.stderr[-300:])
        s.check("variants: the best passing treatment ships and the other two are kept on disk as alternates",
                ranked.get("shipped") == order[0] and sorted(ranked.get("alternates", [])) == sorted(order[1:])
                and all((vd / v / "trace.json").exists() for v in order)
                and json.loads((vd / order[1] / "treatment.json").read_text())["role"] == "alternate"
                and json.loads((vd / order[0] / "treatment.json").read_text())["role"] == "shipped", ranked)
        # contradicts never ships
        ranking["ranking"][0]["shows_what_is_said"] = "contradicts"
        (review / "ranking.json").write_text(json.dumps(ranking), encoding="utf-8")
        run("scripts/design/variants.py", ["rank", "opening", "--build", build], env=env, timeout=60)
        rk2 = json.loads((vd / "ranked.json").read_text())
        s.check("variants: a candidate the reviewer says contradicts its sentence never ships",
                rk2.get("shipped") not in (None, order[0]) and order[0] in rk2.get("alternates", []), rk2.get("shipped"))
        # a stub reviewer exposing the same API (any host can plug its own)
        (T / "stub.py").write_text(STUB, encoding="utf-8")
        (T / "stub_noreason.py").write_text(STUB_NOREASON, encoding="utf-8")
        rs1 = run("scripts/design/variants.py", ["rank", "opening", "--build", build, "--reviewer", T / "stub.py"],
                  env=env, timeout=60)
        rk3 = json.loads((vd / "ranked.json").read_text())
        s.check("variants rank accepts any reviewer exposing rank(variants_dir) -> ranked list",
                rs1.returncode == 0 and rk3.get("shipped") and "stub.py" in rk3["reviewer"]["source"], rk3.get("summary"))
        rs2 = run("scripts/design/variants.py", ["rank", "opening", "--build", build, "--reviewer",
                                                  T / "stub_noreason.py"], env=env, timeout=60)
        rk4 = json.loads((vd / "ranked.json").read_text())
        s.check("variants: a ranking without reasons is refused (REVIEW, nothing ships)",
                rs2.returncode == 1 and rk4.get("shipped") is None
                and any(f["check"] == "reason-missing" for f in rk4.get("findings", [])), rk4.get("findings"))
        # near-duplicates and a failing treatment: the bar is not lowered
        tr0 = vs[0] / "trace.json"
        badtr = json.loads(tr0.read_text())
        for fr in badtr["frames"]:
            for b in fr["boxes"]:
                b["alpha"] = None
        (T / "bad_trace.json").write_text(json.dumps(badtr), encoding="utf-8")
        spec2 = {"sentence": "Each failed call climbs one rung of the ladder.",
                 "treatments": [{"idea": "a", "trace": str(tr0)}, {"idea": "b", "trace": str(tr0)},
                                {"idea": "c", "trace": str(T / "bad_trace.json")}]}
        (T / "spec2.json").write_text(json.dumps(spec2), encoding="utf-8")
        rm2 = run("scripts/design/variants.py", ["make", "payoff", "--build", build, "--spec", T / "spec2.json",
                                                  "--seed", "7"], env=env, timeout=240)
        m2 = gate_json(rm2)
        checks2 = {f["check"] for f in m2.get("findings", [])}
        s.check("variants: two near-identical treatments are reported (one idea twice is not two treatments)",
                "near-duplicate-treatments" in checks2, m2.get("findings"))
        s.check("variants: a treatment that fails its outcome gates is flagged and can never ship",
                "treatment-fails-gates" in checks2 and len(m2.get("passing", [])) == 2, m2.get("findings"))
        vd2 = build / "variants" / "payoff"
        lm2 = json.loads((vd2 / "review" / ".label_map.json").read_text())["labels"]
        bad_v = "v3"
        rk = {"ranking": [{"label": lm2[bad_v], "shows_what_is_said": "match",
                           "reason": "this one looks the most striking of the three sheets"}] +
                         [{"label": lm2[v], "shows_what_is_said": "match",
                           "reason": "clear and legible against the sentence"} for v in ("v1", "v2")]}
        (vd2 / "review" / "ranking.json").write_text(json.dumps(rk), encoding="utf-8")
        run("scripts/design/variants.py", ["rank", "payoff", "--build", build], env=env, timeout=60)
        rk5 = json.loads((vd2 / "ranked.json").read_text())
        s.check("variants: even ranked first, a gate-failing treatment does not ship — the best PASSING one does",
                rk5.get("shipped") in ("v1", "v2") and "v3" in rk5.get("alternates", []), rk5.get("shipped"))
        st = gate_json(run("scripts/design/variants.py", ["status", "--build", build], env=env))
        s.check("variants status lists each pivotal decision with its shipped treatment and alternates",
                {d["decision"] for d in st.get("decisions", [])} == {"opening", "payoff"}, st)
else:
    s.check("variant treatments SKIPPED — node not on PATH, so scenes cannot be traced here (H4 UNVERIFIED)",
            True, "node missing")
s.check("package data/ files are byte-identical after the variant tests", data_hash() == DATA0, "data/ changed")

# ====================================================================== H5 — invention ledger
with tmpdir() as td:
    T = Path(td)
    env = senv(T)
    home = Path(env["COORDN8_HOME"])

    def mk_build(name: str, palette_family=None, rename_kit=None) -> Path:
        b = T / name / "Coordn8" / "_build"
        b.mkdir(parents=True)
        d = copy.deepcopy(ds)
        if palette_family:
            d["palette"]["family"] = palette_family
        (b / "design_system.json").write_text(json.dumps(d), encoding="utf-8")
        shutil.copyfile(FX / "diagram_plan.json", b / "diagram_plan.json")
        shutil.copyfile(FX / "page_content.json", b / "page_content.json")
        if TRACE_TEXT:
            (b / "trace_tide_ladder.json").write_text(TRACE_TEXT, encoding="utf-8")
        return b

    GATES_OK = {"palette": "PASS", "diagram_plan": "PASS", "diagram_budget": "PASS", "semantics": "PASS",
                "invariants": "PASS", "density": "PASS", "page_a11y": "PASS", "check_caption_marks": "PASS",
                "visual_density": "PASS"}
    gfile = T / "gates_ok.json"
    gfile.write_text(json.dumps({"gates": GATES_OK}), encoding="utf-8")

    def record(b: Path, gates=gfile, bid=None):
        args = ["record", b, "--gate-results", gates] + (["--build-id", bid] if bid else [])
        r = run("scripts/design/inventions.py", args, env=env, timeout=60)
        return r, gate_json(r)

    def events():
        p = home / "library" / "inventions.jsonl"
        return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []

    bA = mk_build("projA")
    rA, jA = record(bA)
    kinds = {i["kind"] for i in jA.get("inventions", [])}
    want = {"palette", "kit", "diagram", "caption_style", "page_component"} | ({"scene"} if TRACE_TEXT else set())
    s.check("inventions: first use logged — palette, kit, diagram form, scene, caption style and page component "
            "each recorded with where it was used and its gate results",
            rA.returncode == 0 and want <= kinds and len([e for e in events() if e["event"] == "use"]) == len(kinds)
            and all(e["where"] and e["gates"] and e["passed"] for e in events() if e["event"] == "use"),
            (kinds, jA.get("summary"), rA.stderr[-200:]))
    s.check("inventions: the ledger lives at <COORDN8_HOME>/library/inventions.jsonl",
            (home / "library" / "inventions.jsonl").exists() and Path(jA.get("ledger", "")).resolve()
            == (home / "library" / "inventions.jsonl").resolve(), jA.get("ledger"))
    s.check("inventions: the first use makes no promotion offer", not jA.get("offers")
            and not [e for e in events() if e["event"] == "offer"], jA.get("offers"))
    n_before = len(events())
    rA2, jA2 = record(bA)
    s.check("inventions: recording the same build again adds no duplicate use and no offer",
            len(events()) == n_before and not jA2.get("offers"), len(events()) - n_before)
    bB = mk_build("projB")
    rB, jB = record(bB)
    offers = [e for e in events() if e["event"] == "offer"]
    s.check("inventions: a second passing use in a DIFFERENT build triggers the promotion offer — printed and "
            "recorded — for every invention",
            {o["kind"] for o in jB.get("offers", [])} == kinds and "PROMOTION OFFER" in rB.stderr
            and {o["kind"] for o in offers} == kinds, (jB.get("offers"), rB.stderr[-300:]))
    bC = mk_build("projC")
    rC, jC = record(bC)
    offers_after = [e for e in events() if e["event"] == "offer"]
    s.check("inventions: the promotion offer is made exactly once (a third build adds no second offer)",
            not jC.get("offers") and len(offers_after) == len(offers) == len(kinds) and "PROMOTION OFFER" not in rC.stderr,
            (len(offers_after), jC.get("offers")))
    # failing gates / missing reason never count
    bF = mk_build("projF")
    gbad = T / "gates_bad.json"
    gbad.write_text(json.dumps({"gates": dict(GATES_OK, palette="REVIEW")}), encoding="utf-8")
    rF, jF = record(bF, gates=gbad)
    pal_use = next((i for i in jF.get("inventions", []) if i["kind"] == "palette"), {})
    s.check("inventions: a use whose applicable gate failed is logged as not passed and names the failing gate",
            pal_use.get("passed") is False and "palette=REVIEW" in pal_use.get("verdict", ""), pal_use)
    s.check("inventions: a failing gate of another kind does not taint an unrelated invention (diagram still passes)",
            any(i["kind"] == "diagram" and i["passed"] for i in jF.get("inventions", [])), jF.get("inventions"))
    bN = T / "projN" / "Coordn8" / "_build"
    bN.mkdir(parents=True)
    (bN / "inventions.json").write_text(json.dumps({"inventions": [
        {"kind": "page_component", "name": "invented:no-reason-panel", "definition": {"x": 1}}]}), encoding="utf-8")
    rN, jN = record(bN)
    s.check("inventions: an invention with no recorded reason never passes (invent, and say why)",
            jN.get("inventions") and jN["inventions"][0]["passed"] is False
            and "no reason" in jN["inventions"][0]["verdict"], jN.get("inventions"))
    # same invention under a new name: fingerprint
    env2 = senv(T / "fp")
    home2 = Path(env2["COORDN8_HOME"])
    bX, bY = mk_build("projX", "invented:orchid-one"), mk_build("projY", "invented:orchid-renamed")
    for b in (bX, bY):
        ry = run("scripts/design/inventions.py", ["record", b, "--gate", "palette=PASS"], env=env2, timeout=60)
    jy = gate_json(ry)
    s.check("inventions: the same invention under a new name is recognised by its fingerprint and still offered",
            any(o["kind"] == "palette" for o in jy.get("offers", [])), jy.get("offers"))
    # promote
    snap = {str(p.relative_to(T)) for p in T.rglob("*") if p.is_file()}
    rp0 = run("scripts/design/inventions.py", ["promote", "page_component", "no-reason-panel"], env=env, timeout=60)
    s.check("inventions: promote refuses an invention that was never offered (unless --force)",
            rp0.returncode == 1 and gate_json(rp0).get("status") == "REFUSED", rp0.stdout[-200:])
    rp = run("scripts/design/inventions.py", ["promote", "palette", "orchid-parchment"], env=env, timeout=60)
    rp2 = run("scripts/design/inventions.py", ["promote", "diagram", "tide-ladder"], env=env, timeout=60)
    rp3 = run("scripts/design/inventions.py", ["promote", "palette", "orchid-parchment"], env=env, timeout=60)
    new = sorted({str(p.relative_to(T)) for p in T.rglob("*") if p.is_file()} - snap)
    lib_rel = str((home / "library").relative_to(T))
    overlay = home / "library" / "catalog" / "palette.json"
    ov = json.loads(overlay.read_text()) if overlay.exists() else {}
    s.check("inventions: promote copies the invention into the LOCAL catalog overlay under "
            "<COORDN8_HOME>/library/catalog/",
            rp.returncode == 0 and rp2.returncode == 0 and rp3.returncode == 0
            and ov.get("entries", {}).get("orchid-parchment", {}).get("definition", {}).get("roles")
            == ds["palette"]["roles"] and (home / "library" / "catalog" / "diagram.json").exists(),
            (rp.stdout[-200:], ov.keys()))
    s.check("inventions: promote writes ONLY under <COORDN8_HOME>/library (overlay, its backups and the ledger)",
            new and all(n.startswith(lib_rel + os.sep) for n in new), new)
    s.check("inventions: re-promoting keeps a backup of the previous overlay inside the library",
            any(".backups" in n for n in new), new)
    s.check("inventions: promotion is recorded in the ledger", any(e["event"] == "promoted" for e in events()),
            [e["event"] for e in events()][-5:])
    s.check("inventions never edit the package data/ directory (byte-identical)", data_hash() == DATA0, "data/ changed")

s.check("package data/ files are byte-identical after every freedom test", data_hash() == DATA0, "data/ changed")
sys.exit(s.finish())
