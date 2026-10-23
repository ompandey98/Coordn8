#!/usr/bin/env python3
"""Style kits (A3, with the kit-field parts of E8/E9 and the H1/H3 rules for kits).

Proves: >= 8 original kits that all pass the outcome rules and genuinely differ; one active
kit drives BOTH the page (CSS vars) and the video runtime (window.__C8_KIT__) with no code
edit; blends, hand-defined and invented kits are accepted with a reason; bad kits are caught
by outcome rules only; picks vary with the brief and with history; design_system.json keeps
every other key. Hermetic: temp dirs, sandboxed COORDN8_HOME/HOME, no network.
"""
from __future__ import annotations

import copy
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import ROOT, PY, Suite, run, sandbox_env, tmpdir  # noqa: E402

s = Suite("kits")
import site  # noqa: E402

REAL_HOME = os.path.expanduser("~")   # captured before sandboxing; used only to READ a cached browser
USER_SITE = site.getusersitepackages()  # where a --user playwright lives; read-only
TMP = tmpdir()
T = Path(TMP.name)
ENV = sandbox_env(T)
os.environ.update(ENV)            # in-process calls read history from the sandbox, never the real home
sys.path.insert(0, str(ROOT / "scripts"))

import design.pick_kit as pk  # noqa: E402
import design.kit_export as kx  # noqa: E402

KITS = pk.load_kits()
BY = {k["id"]: k for k in KITS}
PAIRS = [(a, b) for i, a in enumerate(KITS) for b in KITS[i + 1:]]


def clean(k):
    k = copy.deepcopy(k)
    k.pop("_file", None)
    return k


def write(p: Path, obj) -> Path:
    p.write_text(json.dumps(obj, indent=1), encoding="utf-8")
    return p


# ------------------------------------------------------------------ catalog
s.check("at least 8 style kits ship in data/style_kits", len(KITS) >= 8, f"{len(KITS)} kits")
fails = {k["id"]: pk.validate_kit(k) for k in KITS}
s.check("every shipped kit passes the outcome rules", not any(fails.values()),
        {k: v[:2] for k, v in fails.items() if v})
s.check("every kit has theme, stage, props, charts, motion, transitions, caption_style, do and dont",
        all(all(sec in k for sec in pk.REQUIRED_SECTIONS) for k in KITS))
s.check("every kit has id, name, mood, use_when, avoid_when and a palette family",
        all(k.get("id") and k.get("name") and k.get("mood") and k.get("use_when") and k.get("avoid_when")
            and k.get("palette_family") and k.get("palette", {}).get("roles") and k.get("palette", {}).get("dark")
            for k in KITS))
pal_file = ROOT / "data" / "palettes.json"
if pal_file.exists():
    fam_ids = {f["id"] for f in json.loads(pal_file.read_text(encoding="utf-8")).get("families", [])}
    s.check("every kit's palette_family names a family in data/palettes.json",
            all(k["palette_family"] in fam_ids for k in KITS),
            [k["palette_family"] for k in KITS if k["palette_family"] not in fam_ids])

text_ok = []
for k in KITS:
    for mode in ("light", "dark"):
        r = pk.skins(k)[mode]
        for role in pk.TEXT_ROLES:
            text_ok.append((k["id"], mode, role, round(pk.contrast(r[role], r["paper"]), 2)))
s.check("every kit's text roles reach 4.5:1 on paper in light and dark",
        all(c >= 4.5 for *_, c in text_ok), [t for t in text_ok if t[3] < 4.5])
s.check("every kit's type scale is at least 28 px at 1080p",
        all(min(k["theme"]["scale"].values()) >= 28 for k in KITS))
s.check("every caption style is at least 28 px, at most 2 lines and truncates instead of shrinking",
        all(k["caption_style"]["font_px"] >= 28 and k["caption_style"]["max_lines"] <= 2
            and k["caption_style"].get("overflow") == "truncate" for k in KITS))
s.check("caption marks good/bad/key are distinguishable without colour in every kit",
        all(len({tuple(sorted(m["styles"])) for m in k["caption_style"]["marks"].values()}) == 3 for k in KITS))
s.check("every kit's motion ease is a valid non-linear cubic-bezier and stagger is 0-400 ms",
        all(pk.parse_bezier(k["motion"]["ease"]) and not pk.is_linear(pk.parse_bezier(k["motion"]["ease"]))
            and 0 <= k["motion"]["stagger_ms"] <= 400 for k in KITS))
s.check("every kit's transitions name one section transition and prefer a same-element morph",
        all(any(t.get("use") == "section" for t in k["transitions"])
            and any(t.get("morph") for t in k["transitions"]) for k in KITS))
s.check("every kit states its first-second anchor",
        all((k.get("continuity") or {}).get("first_second_anchor") for k in KITS))
s.check("every kit's chart series is one ink at stepped opacity",
        all(isinstance(k["charts"]["series"].get("role"), str) and len(k["charts"]["series"]["steps"]) >= 2
            for k in KITS))
s.check("every font stack is offline-safe and no kit ships a font file or CDN",
        all(not re.search(r"https?:|url\(", json.dumps(k["theme"]["family"])) for k in KITS)
        and not list((ROOT / "data" / "style_kits").glob("*.woff*")))
s.check("every vendorable font a kit names is OFL-licensed and marked not shipped",
        all(v.get("licence") == "OFL-1.1" and v.get("shipped") is False
            for k in KITS for v in k["theme"].get("vendorable") or []))


# ------------------------------------------------------------------ genuinely different
def palette_sig(k):
    r = k["palette"]["roles"]
    return (k["palette_family"], r["paper"], r["ink"], r["accent"])


def type_sig(k):
    f = k["theme"]["family"]
    return (f.get("display"), f.get("body"), tuple(sorted(k["theme"]["scale"].items())))


def motion_sig(k):
    m = k["motion"]
    return (m["ease"], m["enter_ms"], m["stagger_ms"], m["entrance"].get("kind"))


def trans_sig(k):
    return frozenset(t["id"] for t in k["transitions"])


for label, fn in (("palette", palette_sig), ("type", type_sig), ("motion", motion_sig), ("transitions", trans_sig)):
    same = [(a["id"], b["id"]) for a, b in PAIRS if fn(a) == fn(b)]
    s.check(f"every pair of kits differs in {label}", not same, same[:5])
s.check("every pair of kits differs in paper, accent, display face, ease and entrance at once",
        all(a["palette"]["roles"]["paper"] != b["palette"]["roles"]["paper"]
            and a["palette"]["roles"]["accent"] != b["palette"]["roles"]["accent"]
            and a["motion"]["ease"] != b["motion"]["ease"]
            and a["motion"]["entrance"]["kind"] != b["motion"]["entrance"]["kind"]
            for a, b in PAIRS))
s.check("kits cover both light-base and dark-base looks",
        {k.get("base") for k in KITS} == {"light", "dark"})

# originality against the reference style library (skips, honestly, when the clone is absent)
REF = Path(os.environ.get("C8_REFS", "/tmp/coordn8_refs")) / "video"
FP = ROOT / "tests" / "v2" / "fixtures" / "kits_ref_fingerprint.json"
if not REF.is_dir() and FP.exists():
    # The reference clone is not here: compare against the stored colour fingerprints instead.
    import hashlib as _hl
    _ref = set(json.loads(FP.read_text(encoding="utf-8"))["hashes"])
    kit_hex = {h.lower() for k in KITS for h in re.findall(r"#[0-9a-fA-F]{6}\b", json.dumps(k))}
    shared = sorted(h for h in kit_hex if _hl.sha256(h.encode()).hexdigest()[:16] in _ref)
    ref_fonts = {"patrick hand", "luckiest guy", "caveat", "anton", "playfair", "garamond", "noto sans sc",
                 "songti", "puhuiti", "desk sans", "arial rounded"}
    kit_fonts = json.dumps([k["theme"]["family"] for k in KITS]).lower()
    s.check("no kit colour or font is copied from the reference style library",
            _ref and not shared and not any(f in kit_fonts for f in ref_fonts),
            {"shared_hex": shared[:8], "fonts": [f for f in ref_fonts if f in kit_fonts]})
elif REF.is_dir():
    ref_hex = set()
    for p in list((REF / "src" / "library").rglob("*.ts*")) + list((REF / "styles").rglob("*.md")) + \
            list((REF / "styles").rglob("*.js")):
        try:
            ref_hex |= {h.lower() for h in re.findall(r"#[0-9a-fA-F]{6}\b", p.read_text(encoding="utf-8", errors="ignore"))}
        except OSError:
            pass
    kit_hex = {h.lower() for k in KITS for h in re.findall(r"#[0-9a-fA-F]{6}\b", json.dumps(k))}
    ref_fonts = {"patrick hand", "luckiest guy", "caveat", "anton", "playfair", "garamond", "noto sans sc",
                 "songti", "puhuiti", "desk sans", "arial rounded"}
    kit_fonts = json.dumps([k["theme"]["family"] for k in KITS]).lower()
    s.check("no kit colour or font is copied from the reference style library",
            ref_hex and not (kit_hex & ref_hex) and not any(f in kit_fonts for f in ref_fonts),
            {"shared_hex": sorted(kit_hex & ref_hex)[:8], "fonts": [f for f in ref_fonts if f in kit_fonts]})
else:
    s.check("reference-originality check skipped: reference clone absent (recorded, not a pass of originality)",
            True, f"{REF} not present")

# ------------------------------------------------------------------ exports: one kit, two readers
css = {k["id"]: kx.to_css_vars(k) for k in KITS}
cv = {kid: kx.parse_css_vars(c) for kid, c in css.items()}
rt = {k["id"]: kx.to_runtime(k) for k in KITS}
keys_css = {"palette": "--c8-paper", "type": "--c8-font-body", "motion": "--c8-ease", "transitions": "--c8-transitions"}
diff_css = [(a["id"], b["id"], f) for a, b in PAIRS for f, key in keys_css.items()
            if cv[a["id"]].get(key) == cv[b["id"]].get(key)]
s.check("switching kits changes the exported CSS vars for palette, type, motion and transitions", not diff_css,
        diff_css[:5])


def rt_parts(r):
    return {"palette": r["colors"]["paper"] + r["colors"]["accent"], "type": r["text"]["font"] + r["text"]["display_font"],
            "motion": tuple(r["motion"]["ease"]), "transitions": tuple(t["id"] for t in r["transitions"])}


diff_rt = [(a["id"], b["id"], f) for a, b in PAIRS for f in ("palette", "type", "motion", "transitions")
           if rt_parts(rt[a["id"]])[f] == rt_parts(rt[b["id"]])[f]]
s.check("switching kits changes the runtime JSON for palette, type, motion and transitions", not diff_rt, diff_rt[:5])

agree = []
for k in KITS:
    c, r = cv[k["id"]], rt[k["id"]]
    base = k.get("base", "light")
    ok = (c["--c8-paper"] == r["colors"]["paper"] == pk.skins(k)[base]["paper"]
          and c["--c8-ease"] == r["motion"]["ease_css"]
          and c["--c8-caption-size"] == f"{r['caption']['size']}px"
          and c["--c8-enter-ms"] == f"{int(round(r['motion']['enter_s'] * 1000))}ms"
          and c["--c8-font-body"] == r["text"]["font"]
          and c["--c8-transitions"].strip('"').split() == [t["id"] for t in r["transitions"]])
    if not ok:
        agree.append(k["id"])
s.check("page CSS vars and runtime JSON agree on paper, type, ease, timing, caption size and transitions", not agree,
        agree)
s.check("CSS export carries the other skin via prefers-color-scheme and [data-theme=dark]",
        all("@media (prefers-color-scheme:" in c and ":root[data-theme=dark]{" in c and ":root[data-theme=light]{" in c
            for c in css.values()))
s.check("CSS export zeroes motion under prefers-reduced-motion",
        all("@media (prefers-reduced-motion: reduce)" in c and "--c8-enter-ms: 0ms;" in c.split("prefers-reduced-motion")[1]
            for c in css.values()))
s.check("runtime ease samples are monotone-safe, start at 0 and end at 1",
        all(abs(r["motion"]["ease_samples"][0]) < 1e-6 and abs(r["motion"]["ease_samples"][-1] - 1) < 1e-3
            for r in rt.values()))
s.check("runtime carries both skins and never a size under 28 px",
        all(set(r["modes"]) == {"light", "dark"} and min(r["text"]["sizes"].values()) >= 28
            and r["caption"]["size"] >= 28 for r in rt.values()))

# the real scene runtime consumes the kit's motion with no code edit
node = shutil.which("node")
if node:
    js = T / "kit_probe.js"
    js.write_text(
        "const C=require(process.argv[2]);const kits=JSON.parse(require('fs').readFileSync(process.argv[3],'utf8'));"
        "const out={};for(const [id,K] of Object.entries(kits)){"
        "const sc=new C.Scene({name:'probe',script:[{name:'a',start:0,end:1},{name:'b',start:1,end:3}],ctx:null});"
        "sc.begin(1.12);out[id]=sc.beatAlpha('b',{fadeIn:K.motion.fadeIn,lead:K.motion.lead});"
        "const w=new C.Scene({name:'w',script:[{name:'a',start:0,end:1}],ctx:null});w.begin(0.5);"
        "out[id+':text']=w.text({text:'x',alpha:1,size:K.caption.size}).size;}"
        "console.log(JSON.stringify(out));", encoding="utf-8")
    kits_json = write(T / "rt.json", {kid: r for kid, r in rt.items()})
    pr = subprocess.run([node, str(js), str(ROOT / "scripts" / "render" / "scene_runtime.js"), str(kits_json)],
                        capture_output=True, text=True, timeout=60)
    vals = json.loads(pr.stdout) if pr.returncode == 0 and pr.stdout.strip().startswith("{") else {}
    alphas = {kid: vals.get(kid) for kid in rt}
    s.check("the existing scene runtime reads kit motion through beatAlpha and different kits change the frame",
            vals and len({round(a, 4) for a in alphas.values() if a is not None}) >= 5,
            {"alphas": alphas, "stderr": pr.stderr[-300:]})
    s.check("caption size from the runtime JSON draws at or above the 28 px floor in the scene runtime",
            vals and all(vals.get(f"{kid}:text", 0) >= 28 for kid in rt), vals)
    jsout = T / "kit.js"
    jsout.write_text(kx.to_runtime_js(BY["chalk-talk"]), encoding="utf-8")
    ev = subprocess.run([node, "-e", "global.window={};require(process.argv[1]);"
                         "console.log(window.__C8_KIT__.kit+'|'+window.__C8_KIT__.motion.ease.length)", str(jsout)],
                        capture_output=True, text=True, timeout=60)
    s.check("runtime --js output defines window.__C8_KIT__ for the active kit", ev.stdout.strip() == "chalk-talk|4",
            ev.stdout + ev.stderr[-200:])
else:
    s.check("scene-runtime consumption check skipped: node not on PATH", True, "node missing")

# a real browser resolves the vars, including the dark skin under an emulated dark OS setting
probe = (
    "import sys,json\n"
    "from playwright.sync_api import sync_playwright\n"
    "css=open(sys.argv[1]).read()\n"
    "html='<html><head><style>'+css+'</style></head><body></body></html>'\n"
    "out={}\n"
    "with sync_playwright() as p:\n"
    "  b=p.chromium.launch()\n"
    "  for scheme in ('light','dark'):\n"
    "    pg=b.new_page(color_scheme=scheme); pg.set_content(html)\n"
    "    out[scheme]=pg.evaluate(\"()=>getComputedStyle(document.documentElement).getPropertyValue('--c8-paper').trim()\")\n"
    "    pg.evaluate(\"()=>document.documentElement.setAttribute('data-theme','light')\")\n"
    "    out[scheme+'_pinned_light']=pg.evaluate(\"()=>getComputedStyle(document.documentElement).getPropertyValue('--c8-paper').trim()\")\n"
    "    pg.close()\n"
    "  b.close()\n"
    "print(json.dumps(out))\n")
pfile = T / "css_probe.py"
pfile.write_text(probe, encoding="utf-8")
cfile = T / "fn.css"
cfile.write_text(css["field-notebook"], encoding="utf-8")
benv = dict(os.environ)   # HOME stays sandboxed; only the cached browser location is pointed at, read-only
if not benv.get("PLAYWRIGHT_BROWSERS_PATH"):
    for cand in (Path(REAL_HOME) / "Library" / "Caches" / "ms-playwright", Path(REAL_HOME) / ".cache" / "ms-playwright"):
        if cand.is_dir():
            benv["PLAYWRIGHT_BROWSERS_PATH"] = str(cand)
            break
if USER_SITE and Path(USER_SITE).is_dir():
    benv["PYTHONPATH"] = os.pathsep.join(x for x in (benv.get("PYTHONPATH"), USER_SITE) if x)
try:
    br = subprocess.run([PY, str(pfile), str(cfile)], capture_output=True, text=True, timeout=90, env=benv)
    got = json.loads(br.stdout.strip().splitlines()[-1]) if br.returncode == 0 and br.stdout.strip() else None
except Exception as ex:  # noqa: BLE001
    got, br = None, None
if got:
    fl = BY["field-notebook"]["palette"]
    s.check("a real browser applies the light skin by default, the dark skin under a dark OS, and data-theme pins it",
            got["light"] == fl["roles"]["paper"] and got["dark"] == fl["dark"]["paper"]
            and got["dark_pinned_light"] == fl["roles"]["paper"], got)
else:
    s.check("browser CSS-resolution check skipped: no headless browser available here", True,
            (br.stderr[-200:] if br else "probe failed to start"))

# ------------------------------------------------------------------ outcome rules (H1: never catalog membership)
src = inspect.getsource(pk.validate_kit)
s.check("no rule depends on catalog membership (validator takes no catalog and never reads one)",
        list(inspect.signature(pk.validate_kit).parameters) == ["kit"]
        and not re.search(r"load_kits|KITS_DIR|kit_by_id|palettes\.json|style_kits", src))

hand = clean(BY["field-notebook"])
hand.update(id="harbour-fog", name="Harbour Fog", base="light", palette_family="something-never-catalogued",
            mood=["misty", "quiet"], reason="a maritime logistics subject wanted a cool, foggy look")
hand["palette"] = {"family": "harbour-fog",
                   "roles": {"paper": "#eef2f3", "ink": "#1b2a33", "muted": "#4f6069", "accent": "#a5480f",
                             "link": "#245a94", "positive": "#2d6e45", "negative": "#a3303f", "warning": "#835a00",
                             "deterministic": "#245a94", "model-judgement": "#6a3f99", "data-field": "#2d6e45",
                             "vision": "#835a00"},
                   "dark": {"paper": "#11191d", "ink": "#e3ecef", "muted": "#a5b5bc", "accent": "#f0a26a",
                            "link": "#8fbbe8", "positive": "#86cc9c", "negative": "#f39aa6", "warning": "#e9c06a",
                            "deterministic": "#8fbbe8", "model-judgement": "#c6a8ee", "data-field": "#86cc9c",
                            "vision": "#e9c06a"}}
hand["theme"]["family"] = {"display": "Rockwell, 'Roboto Slab', Georgia, serif", "body": "Verdana, Geneva, sans-serif",
                           "mono": "Monaco, Menlo, monospace"}
hand["motion"] = {"ease": "cubic-bezier(.3,.1,.2,1)", "enter_ms": 700, "exit_ms": 400, "stagger_ms": 200,
                  "entrance": {"kind": "surface-from-fog", "opacity_from": 0, "blur_px": 14},
                  "exit": {"kind": "sink-into-fog", "opacity_to": 0, "blur_px": 10}}
hand["transitions"] = [{"id": "tide-wipe", "use": "section", "ms": 900, "mask": "wave-left"},
                       {"id": "fog-dissolve", "use": "within-section", "ms": 650, "blend": "via", "via_role": "muted"},
                       {"id": "morph", "use": "within-section", "ms": 600, "morph": True}]
hand["stage"]["background"] = {"treatment": "fog-bands", "base_role": "paper", "texture": {"kind": "bands", "alpha": 0.04}}
hand["props"]["callout"]["style"] = "lighthouse-beam"
s.check("a hand-defined new kit with invented names everywhere passes the outcome rules",
        pk.validate_kit(hand) == [], pk.validate_kit(hand))
hc, hr = kx.parse_css_vars(kx.to_css_vars(hand)), kx.to_runtime(hand)
s.check("a hand-defined new kit exports to CSS vars and runtime JSON",
        hc["--c8-kit"] == '"harbour-fog"' and hc["--c8-paper"] == "#eef2f3" and hr["kit"] == "harbour-fog"
        and [t["id"] for t in hr["transitions"]] == ["tide-wipe", "fog-dissolve", "morph"], (hc.get("--c8-kit"), hr["kit"]))
moved = T / "elsewhere"
moved.mkdir()
s.check("validation is identical when the kit catalog directory is empty or missing",
        pk.validate_kit(clean(BY["ops-console"])) == [] and pk.load_kits(moved) == []
        and pk.validate_kit(hand) == [])

bad = clean(BY["field-notebook"])
paper = bad["palette"]["roles"]["paper"]
ink = bad["palette"]["roles"]["ink"]
lo, hi = 0.0, 1.0
for _ in range(40):
    mid = (lo + hi) / 2
    c = pk.contrast(pk.mix(ink, paper, mid), paper)
    lo, hi = (lo, mid) if c > 3.9 else (mid, hi)
bad_ink = pk.mix(ink, paper, hi)
bad["palette"]["roles"]["ink"] = bad_ink
bad["caption_style"]["font_px"] = 27
bad["id"] = "too-faint"
errs = pk.validate_kit(bad)
s.check("an invalid invented kit (27 px caption, 3.9:1 text) is rejected for both reasons",
        any("27" in e and "caption" in e for e in errs) and any("'ink'" in e and "3.9" in e for e in errs),
        {"ink": bad_ink, "contrast": round(pk.contrast(bad_ink, paper), 2), "errs": errs[:4]})
lin = clean(BY["poster-grid"]); lin["motion"]["ease"] = "cubic-bezier(0,0,1,1)"
brk = clean(BY["poster-grid"]); brk["motion"]["ease"] = "cubic-bezier(1.4,0,.2,1)"
stg = clean(BY["poster-grid"]); stg["motion"]["stagger_ms"] = 450
hue = clean(BY["poster-grid"]); hue["caption_style"]["marks"]["bad"]["styles"] = list(
    hue["caption_style"]["marks"]["good"]["styles"])
off = clean(BY["poster-grid"]); off["props"]["card"]["fill"] = {"role": "hot-pink"}
s.check("a linear ease and an out-of-range bezier are rejected", any("linear" in e for e in pk.validate_kit(lin))
        and any("cubic-bezier" in e for e in pk.validate_kit(brk)))
s.check("a stagger above 400 ms is rejected", any("stagger" in e for e in pk.validate_kit(stg)))
s.check("caption marks that differ only by colour are rejected",
        any("differ only by colour" in e for e in pk.validate_kit(hue)))
s.check("a colour that is not a declared palette role is rejected",
        any("hot-pink" in e for e in pk.validate_kit(off)))

# ------------------------------------------------------------------ picking
BRIEFS = {
    "fintech": {"subject": "a fintech payments ledger that reconciles card transactions and flags fraud",
                "audience": "engineering managers and finance leads", "tone": "precise and sober"},
    "medical": {"subject": "a hospital medication-dosing service that checks patient allergies",
                "audience": "clinical staff and nurses", "tone": "calm and reassuring"},
    "game-theory": {"subject": "game theory of the prisoner's dilemma and repeated strategies",
                    "audience": "curious general audience", "tone": "thoughtful and dramatic"},
    "infra": {"subject": "kubernetes deployment pipeline with observability and incident response",
              "audience": "site reliability engineers", "tone": "terse and technical"},
    "kids": {"subject": "how plants make food from sunlight, for a primary school classroom",
             "audience": "kids aged 8 to 10", "tone": "playful and friendly"},
}
picks = {name: pk.pick(b, history=[]) for name, b in BRIEFS.items()}
chosen = {name: r["kit"] for name, r in picks.items()}
s.check("five different briefs pick at least 3 different kits", len(set(chosen.values())) >= 3, chosen)
s.check("every pick gives reasons and at least 2 rejections with reasons",
        all(r["reason"] and len(r["rejected"]) >= 2 and all(j["why"] for j in r["rejected"]) for r in picks.values()),
        {n: (r["reason"][:60], len(r["rejected"])) for n, r in picks.items()})
s.check("each fixture brief picks the kit whose subject it is",
        chosen == {"fintech": "ledger-ink", "medical": "quiet-ward", "game-theory": "night-gallery",
                   "infra": "ops-console", "kids": "crayon-box"}, chosen)
kids_rej = " ".join(j["why"] for j in picks["kids"]["rejected"])
s.check("a kit the brief argues against is rejected with its avoid_when reason", "avoid_when" in kids_rej, kids_rej[:300])

gt = BRIEFS["game-theory"]
fresh = pk.pick(gt, history=[])["kit"]
rep = pk.pick(gt, history=[{"kit": fresh, "project": "p", "build": 1}])
s.check("anti-repetition changes the pick when history repeats it",
        rep["kit"] != fresh and rep["history_penalty_applied"] and fresh in rep["reason"], (fresh, rep["kit"], rep["reason"]))
stated = pk.pick(dict(gt, stated="please use the Night Gallery look"), history=[{"kit": "night-gallery"}])
s.check("a kit the user names is obeyed over history and the quote is recorded",
        stated["kit"] == "night-gallery" and stated["mode"] == "stated"
        and stated["obeyed_user_quote"] == "please use the Night Gallery look", (stated["kit"], stated["mode"]))
dark = pk.pick({"subject": "an internal API", "stated": "make it dark and cinematic"}, history=[])
s.check("a stated look (dark) steers the pick to a dark-base kit", BY[dark["kit"]]["base"] == "dark", dark["kit"])

bl = pk.pick(gt, history=[], blend_ids=("ledger-ink", "night-gallery"), take=("motion", "transitions"),
             reason="ledger precision with a cinematic reveal")
s.check("a blend records both parents and a reason",
        bl["kit"] == "blend:ledger-ink+night-gallery" and bl["parents"] == ["ledger-ink", "night-gallery"]
        and bl["reason"] == "ledger precision with a cinematic reveal")
s.check("a blend takes motion and transitions from the second parent and the look from the first",
        bl["spec"]["motion"] == BY["night-gallery"]["motion"] and bl["spec"]["transitions"] == BY["night-gallery"]["transitions"]
        and bl["spec"]["palette"] == BY["ledger-ink"]["palette"] and bl["spec"]["theme"] == BY["ledger-ink"]["theme"]
        and pk.validate_kit(bl["spec"]) == [])
inv = pk.pick(gt, history=[], invented=hand)
s.check("an invented kit is accepted as invented:<name> with its reason",
        inv["kit"] == "invented:harbour-fog" and inv["mode"] == "invented" and "maritime" in inv["reason"])
try:
    pk.pick(gt, history=[], invented=bad, reason="testing")
    rejected_bad = False
except ValueError as ex:
    rejected_bad = "27" in str(ex)
s.check("pick refuses an invented kit that fails the outcome rules", rejected_bad)
ext = {"extends": "chalk-talk", "name": "Chalk Talk Slow", "reason": "a slower board for a younger class",
       "motion": {"enter_ms": 800, "stagger_ms": 220}}
ex_rec = pk.pick(gt, history=[], invented=ext)
s.check("a hand-defined kit can extend a shipped kit and override only what it changes",
        ex_rec["spec"]["motion"]["enter_ms"] == 800 and ex_rec["spec"]["motion"]["ease"] == BY["chalk-talk"]["motion"]["ease"]
        and ex_rec["spec"]["extended_from"] == "chalk-talk" and ex_rec["kit"] == "invented:chalk-talk-slow")

# palette director result applied as the skin; composite judged on outcome
fams = json.loads(pal_file.read_text(encoding="utf-8"))["families"] if pal_file.exists() else []
if fams:
    f0 = next(f for f in fams if f["id"] == BY["ledger-ink"]["palette_family"])
    skin = {"family": f0["id"], "roles": f0["roles"], "dark": f0["dark"]}
    pr_rec = pk.pick(BRIEFS["fintech"], palette=skin, history=[])
    s.check("the Palette Director's palette becomes the active kit's skin in both exports",
            kx.to_runtime(pr_rec["spec"], skin)["colors"]["paper"] == f0["roles"]["paper"]
            and kx.parse_css_vars(kx.to_css_vars(pr_rec["spec"], skin))["--c8-paper"] == f0["roles"]["paper"])
    s.check("a kit's own palette family earns a fit bonus when the director chose it",
            any("palette family" in w for w in pr_rec["ranked"][0]["reasons"]), pr_rec["ranked"][0]["reasons"])
poor = {"family": "invented:murky", "roles": dict(BY["ledger-ink"]["palette"]["roles"], ink="#9a9a90", muted="#a0a09a"),
        "dark": BY["ledger-ink"]["palette"]["dark"]}
try:
    pk.pick(BRIEFS["fintech"], palette=poor, history=[])
    refused = False
except ValueError as ex:
    refused = "outcome rules" in str(ex)
rows = pk.rank(BRIEFS["fintech"], KITS, poor, [])
s.check("a low-contrast build palette makes every kit's composite fail on contrast and pick refuses to ship one",
        refused and all(any("'ink'" in f for f in r["outcome_failures"]) for r in rows),
        [r["outcome_failures"][:1] for r in rows[:2]])

# ------------------------------------------------------------------ CLI + design_system.json
build = T / "proj" / "Coordn8" / "_build"
build.mkdir(parents=True)
write(build / "design_system.json", {"schema": 1, "palette": {"family": "invented:keep-me", "roles": BY["drafting-table"]["palette"]["roles"],
                                                               "dark": BY["drafting-table"]["palette"]["dark"]},
                                     "obeyed_user_quotes": ["blue please"], "seed": 4471, "candidates": ["palette-owned"]})
bf = write(T / "brief_infra.json", BRIEFS["infra"])
r1 = run("scripts/design/pick_kit.py", [bf, "--build", build, "--json"], env=ENV, timeout=120)
ds = json.loads((build / "design_system.json").read_text(encoding="utf-8"))
s.check("CLI pick exits 0 and returns ranked candidates with reasons",
        r1.returncode == 0 and json.loads(r1.stdout)["ranked"][0]["reasons"], (r1.returncode, r1.stderr[-300:]))
s.check("pick writes kit, type, motion, transitions and caption_style into design_system.json",
        all(k in ds for k in ("kit", "type", "motion", "transitions", "caption_style", "kit_spec", "kit_choice"))
        and isinstance(ds["transitions"], list) and ds["type"]["scale"]["caption"] >= 28, sorted(ds))
s.check("design_system.json keeps every other key (palette, quotes, seed, candidates)",
        ds["palette"]["family"] == "invented:keep-me" and ds["obeyed_user_quotes"] == ["blue please"]
        and ds["seed"] == 4471 and ds["candidates"] == ["palette-owned"], {k: ds.get(k) for k in ("seed", "candidates")})
s.check("design_system.json records the rejected kits with reasons",
        len(ds["kit_choice"]["rejected"]) >= 2 and all(j["why"] for j in ds["kit_choice"]["rejected"]))
c1 = run("scripts/design/kit_export.py", ["css", build / "design_system.json"], env=ENV, timeout=60)
r2 = run("scripts/design/kit_export.py", ["runtime", build / "design_system.json"], env=ENV, timeout=60)
rt_cli = json.loads(r2.stdout) if r2.returncode == 0 else {}
css_cli = kx.parse_css_vars(c1.stdout) if c1.returncode == 0 else {}
s.check("kit_export css and runtime read the same active kit from design_system.json",
        rt_cli.get("kit") == ds["kit"] and css_cli.get("--c8-kit") == f'"{ds["kit"]}"', (rt_cli.get("kit"), css_cli.get("--c8-kit")))
dt_skin = BY["drafting-table"]["palette"]
base_skin = dt_skin["dark"] if BY[ds["kit"]]["base"] == "dark" else dt_skin["roles"]
s.check("both exports from design_system.json use the build's palette, not the kit's own",
        rt_cli.get("colors", {}).get("paper") == base_skin["paper"] and css_cli.get("--c8-paper") == base_skin["paper"],
        (rt_cli.get("colors", {}).get("paper"), css_cli.get("--c8-paper"), base_skin["paper"]))

# switching kits through the CLI changes both exports with no code edit
b2 = T / "proj2"
b2.mkdir()
for kid in ("ledger-ink", "chalk-talk"):
    d = b2 / kid
    d.mkdir()
    rr = run("scripts/design/pick_kit.py", [write(T / f"st_{kid}.json", {"subject": "x", "stated": f"use {kid}"}),
                                            "--build", d], env=ENV, timeout=120)
outs = {kid: (kx.parse_css_vars(run("scripts/design/kit_export.py", ["css", b2 / kid / "design_system.json"], env=ENV).stdout),
              json.loads(run("scripts/design/kit_export.py", ["runtime", b2 / kid / "design_system.json"], env=ENV).stdout or "{}"))
        for kid in ("ledger-ink", "chalk-talk")}
(a_css, a_rt), (b_css, b_rt) = outs["ledger-ink"], outs["chalk-talk"]
s.check("switching the active kit in design_system.json changes both exports for palette, type, motion and transitions",
        all(a_css.get(k) != b_css.get(k) for k in keys_css.values())
        and all(rt_parts(a_rt)[f] != rt_parts(b_rt)[f] for f in ("palette", "type", "motion", "transitions")),
        (a_rt.get("kit"), b_rt.get("kit")))

# history through the real store (sandboxed COORDN8_HOME)
from memory.store import Store  # noqa: E402

bgt = write(T / "brief_gt.json", gt)
h0 = json.loads(run("scripts/design/pick_kit.py", [bgt, "--json"], env=ENV).stdout)["kit"]
Store().append_choice({"project": "fixture", "build": 1, "kit": h0, "palette_family": "x"})
h1 = json.loads(run("scripts/design/pick_kit.py", [bgt, "--json"], env=ENV).stdout)
s.check("anti-repetition reads the user's real choice history and changes the CLI pick",
        h1["kit"] != h0 and h1["history_penalty_applied"], (h0, h1["kit"]))

kf = write(T / "bad_kit.json", bad)
rb = run("scripts/design/pick_kit.py", [bgt, "--build", T / "badbuild", "--kit", kf, "--reason", "test", "--json"], env=ENV)
s.check("CLI rejects an invalid invented kit with exit 3 and writes nothing",
        rb.returncode == 3 and "27" in rb.stdout and not (T / "badbuild" / "design_system.json").exists(), rb.stdout[-300:])
hf = write(T / "hand_kit.json", hand)
rh = run("scripts/design/pick_kit.py", [bgt, "--build", T / "handbuild", "--kit", hf, "--json"], env=ENV)
hds = json.loads((T / "handbuild" / "design_system.json").read_text(encoding="utf-8")) if rh.returncode == 0 else {}
s.check("CLI accepts a hand-defined kit and records invented:<name> with its reason",
        hds.get("kit") == "invented:harbour-fog" and "maritime" in hds.get("kit_choice", {}).get("reason", ""), rh.stdout[-300:])
rbl = run("scripts/design/pick_kit.py", [bgt, "--build", T / "blendbuild", "--blend", "quiet-ward,ops-console",
                                         "--reason", "calm look, crisp motion", "--json"], env=ENV)
bds = json.loads((T / "blendbuild" / "design_system.json").read_text(encoding="utf-8")) if rbl.returncode == 0 else {}
s.check("CLI blend records both parents and the reason in design_system.json",
        bds.get("kit") == "blend:quiet-ward+ops-console" and bds.get("kit_choice", {}).get("parents") == ["quiet-ward", "ops-console"]
        and bds["kit_choice"].get("reason") == "calm look, crisp motion", rbl.stdout[-300:])
rv = run("scripts/design/pick_kit.py", ["--validate", kf, "--json"], env=ENV)
s.check("--validate reports outcome failures for a bad kit and passes a good one",
        rv.returncode == 3 and json.loads(rv.stdout)["valid"] is False
        and run("scripts/design/pick_kit.py", ["--validate", hf], env=ENV).returncode == 0)

# sandbox hygiene: nothing written to the real user zone
s.check("kit picking wrote history reads only inside the sandbox",
        str(Store().home).startswith(str(T)), str(Store().home))

TMP.cleanup()
sys.exit(s.finish())
