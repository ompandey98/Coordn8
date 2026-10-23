#!/usr/bin/env python3
"""tests/v2 — interactive page template, component library and accessible glossary (C1, C2, A3 page half).

Builds fixture pages from templates/page with scripts/render/build_page.py into a temp dir, then
proves the result with the existing gates (dom_smoke, content_ban, visual_density) and the new
browser gate scripts/gates/check_page_a11y.py — including seeded defects the gate must catch.
HOME / COORDN8_HOME / CLAUDE_HOME are sandboxed; only the cached browser is read from the real home.
"""
from __future__ import annotations

import base64
import copy
import json
import os
import re
import shutil
import site
import subprocess
import sys
from pathlib import Path

REAL_HOME = os.path.expanduser("~")     # captured before sandboxing; used only to READ a cached browser
USER_SITE = site.getusersitepackages()  # where a --user playwright lives; read-only

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import PY, ROOT, Suite, run, sandbox_env, tmpdir  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
from design.kit_export import parse_css_vars  # noqa: E402

s = Suite("page")
FIX = ROOT / "tests" / "v2" / "fixtures" / "page"
KITS = ROOT / "data" / "style_kits"
TPL = ROOT / "templates" / "page"

_td = tmpdir()
T = Path(_td.name)
ENV = sandbox_env(T)
ENV["PYTHONDONTWRITEBYTECODE"] = "1"
if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
    for cand in (Path(REAL_HOME) / "Library" / "Caches" / "ms-playwright", Path(REAL_HOME) / ".cache" / "ms-playwright"):
        if cand.is_dir():
            ENV["PLAYWRIGHT_BROWSERS_PATH"] = str(cand)
            break
if USER_SITE and Path(USER_SITE).is_dir():
    ENV["PYTHONPATH"] = os.pathsep.join(x for x in (os.environ.get("PYTHONPATH"), USER_SITE) if x)


def last_json(text: str) -> dict:
    for ln in reversed((text or "").strip().splitlines()):
        ln = ln.strip()
        if ln.startswith("{"):
            try:
                return json.loads(ln)
            except ValueError:
                continue
    return {}


def build(content: dict, out: Path, kit: Path = KITS / "quiet-ward.json", extra=()) -> tuple:
    out.parent.mkdir(parents=True, exist_ok=True)
    cfile = out.parent / (out.stem + ".content.json")
    cfile.write_text(json.dumps(content), encoding="utf-8")
    if (FIX / "flow.svg").exists() and not (out.parent / "flow.svg").exists():
        shutil.copy(FIX / "flow.svg", out.parent / "flow.svg")
    r = run("scripts/render/build_page.py", [cfile, "--design-system", kit, "--out", out, *extra], env=ENV, timeout=120)
    return r, last_json(r.stdout)


JOBS: dict = {}


def a11y_start(name: str, page: Path, only: str | None = None, shots: Path | None = None):
    """Start the browser gate in the background so the pages are checked in parallel."""
    jp = page.with_suffix(f".{name}.a11y.json")
    args = [PY, str(ROOT / "scripts/gates/check_page_a11y.py"), str(page), "--json", str(jp)]
    if only:
        args += ["--only", only]
    if shots:
        args += ["--shots", str(shots)]
    log = open(page.with_suffix(f".{name}.log"), "w")
    JOBS[name] = (subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, env={**os.environ, **ENV}), jp, log)


def a11y_wait(name: str) -> tuple:
    proc, jp, log = JOBS[name]
    try:
        rc = proc.wait(timeout=170)
    except subprocess.TimeoutExpired:
        proc.kill()
        rc = 124
    log.close()
    res = json.loads(jp.read_text(encoding="utf-8")) if jp.exists() else {}
    return rc, res


def chk(res: dict, name: str):
    c = (res.get("checks") or {}).get(name) or {}
    return bool(c.get("ok")), json.dumps(c.get("detail"))[:380]


BASE = json.loads((FIX / "content.json").read_text(encoding="utf-8"))
CUSTOM = json.loads((FIX / "custom_section.json").read_text(encoding="utf-8"))
HOSTILE = json.loads((FIX / "custom_bad.json").read_text(encoding="utf-8"))

# ------------------------------------------------------------------ template files
components = {p.stem for p in (TPL / "components").glob("*.html")}
need = {"toc", "hero", "section", "flow", "stepper", "table", "claims", "callout", "chip", "term", "video",
        "footer", "custom"}
s.check("template ships shell, page.css, page.js, README and one file per documented component",
        all((TPL / f).exists() for f in ("shell.html", "page.css", "page.js", "README.md")) and need <= components,
        sorted(need - components))
readme = (TPL / "README.md").read_text(encoding="utf-8") if (TPL / "README.md").exists() else ""
s.check("README documents every component and the content schema keys",
        all(k in readme for k in ("flow", "stepper", "outcomes", "claims", "callout", "chip", "glossary",
                                  "video", "custom_html", "summary", "what", "why", "how", "snapshot",
                                  "build_date", "--design-system")), "missing keys in README")

# ------------------------------------------------------------------ main fixture build
OUT = T / "site"
OUT.mkdir()
ffmpeg = shutil.which("ffmpeg")
if ffmpeg:
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=160x90:d=2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(OUT / "thumbs.mp4")], capture_output=True, timeout=60)
(OUT / "thumbs.vtt").write_text("WEBVTT\n\n00:00.000 --> 00:01.500\nOne request, start to finish\n", encoding="utf-8")
PAGE = OUT / "thumbs.html"
PLAN = OUT / "page_plan.json"
r, summ = build(BASE, PAGE, extra=["--plan-out", PLAN, "--claims-out", OUT / "claims.json"])
s.check("fixture page builds from the template (build_page exits 0)", r.returncode == 0 and PAGE.exists(),
        (r.returncode, r.stdout[-300:], r.stderr[-300:]))
H = PAGE.read_text(encoding="utf-8") if PAGE.exists() else ""

s.check("built page inlines its CSS and JS: no stylesheet link, no external script, one inline script",
        "<link" not in H and not re.search(r"<script[^>]*\ssrc=", H)
        and len(re.findall(r"<script>", H)) == 1 and "c8-page-css" in H and "c8-kit-vars" in H)
s.check("built page has zero external URLs (grep for http(s):// and protocol-relative src/href/url())",
        not re.search(r"https?://", H) and not re.search(r"""(?:src|href)\s*=\s*["']//""", H)
        and not re.search(r"url\(\s*['\"]?//", H), re.findall(r"https?://\S{0,40}", H)[:3])
toc_links = re.findall(r'<nav class="c8-toc"[\s\S]*?</nav>', H)
toc_hrefs = re.findall(r'href="#([^"]+)"', toc_links[0]) if toc_links else []
sec_ids = re.findall(r'<section id="([^"]+)" class="c8-section" data-c8-section', H)
s.check("sidebar contents: one link per section, including the claims ledger, in page order",
        toc_hrefs == sec_ids and len(sec_ids) == len(BASE["sections"]) + 1 and sec_ids[-1] == "claims",
        (toc_hrefs, sec_ids))
s.check("a one-line summary is rendered for every section",
        len(re.findall(r'class="c8-summary" data-c8-summary>[^<\s]', H)) + len(re.findall(r'class="c8-summary" data-c8-summary><', H))
        == len(sec_ids) and H.count("data-c8-summary") == len(sec_ids))
how_blocks = re.findall(r'<details class="c8-how" data-c8-block="how">', H)
s.check("What and Why are shown open; How is folded in a closed native <details>",
        H.count('data-c8-block="what"') == 3 and H.count('data-c8-block="why"') == 3 and len(how_blocks) == 2
        and "<details class=\"c8-how\" data-c8-block=\"how\" open" not in H)
svg_nodes = set(re.findall(r'data-node-id="([^"]+)"', H))
step_hl = set(" ".join(re.findall(r'data-highlight="([^"]*)"', H)).split())
s.check("decision-tree flow: inline SVG with role=img and a label; every stepper highlight names a flow node",
        re.search(r'<svg[^>]*role="img"[^>]*aria-label="What happens to one request"', H) is not None
        and "xmlns" not in H and step_hl and step_hl <= svg_nodes, (sorted(step_hl - svg_nodes)))
s.check("outcomes table: column headers scoped, every row header and cell labelled for phone reflow",
        H.count('<th scope="col">') >= 4 + 3 and len(re.findall(r'<td data-label="[^"]+">', H)) >= 9
        and len(re.findall(r'<th scope="row" data-label="Outcome">', H)) == 3)
claims_blob = re.search(r'<script type="application/json" id="c8-claims">(.*?)</script>', H, re.S)
cj = json.loads(claims_blob.group(1)) if claims_blob else {}
s.check("claims ledger: every row marked read or inferred, tally counts the inferred, claims JSON embedded",
        H.count('data-claim-status="read"') == 4 and H.count('data-claim-status="inferred"') == 1
        and "5 claims: 4 read in the code, 1 inferred." in H and cj.get("read") == 4 and cj.get("inferred") == 1
        and json.loads((OUT / "claims.json").read_text())["claims"][4]["status"] == "inferred", cj)
bad_claims = copy.deepcopy(BASE)
bad_claims["claims"].append({"claim": "An unsupported statement."})
rb, sb = build(bad_claims, T / "bad_claims" / "p.html")
s.check("claims ledger refuses a claim marked neither read nor inferred (build exits 3, names the claim)",
        rb.returncode == 3 and "neither read nor inferred" in sb.get("summary", ""), sb)
rb2 = copy.deepcopy(BASE)
rb2["claims"][0] = {"claim": "Read but pointing nowhere.", "status": "read"}
rr2, sb2 = build(rb2, T / "bad_claims2" / "p.html")
s.check("claims ledger refuses a claim marked read with no path:line", rr2.returncode == 3 and "no path:line" in sb2.get("summary", ""), sb2)
s.check("note and warning callouts carry a text label and an accessible name, not colour alone",
        re.search(r'<aside class="c8-callout" data-kind="warning" aria-label="Watch out">', H) is not None
        and re.search(r'<aside class="c8-callout" data-kind="note" aria-label="Good to know">', H) is not None
        and '<p class="c8-callout-label" aria-hidden="true">Watch out</p>' in H)
s.check("path:line chips link to a relative source path when a base is given",
        '<a class="c8-chip" href="../src/router.py#L18" data-ref="router.py:18"><code>router.py:18</code></a>' in H)
nolink = copy.deepcopy(BASE)
nolink.pop("source_link_base")
rn, _ = build(nolink, T / "nolink" / "p.html")
HN = (T / "nolink" / "p.html").read_text(encoding="utf-8") if rn.returncode == 0 else ""
s.check("without a source base a chip is plain code, not a link-looking control",
        '<span class="c8-chip" data-ref="router.py:18"><code>router.py:18</code></span>' in HN and 'a class="c8-chip"' not in HN)
vtt_data = re.search(r'<track kind="captions" src="data:text/vtt;base64,([^"]+)"', H)
s.check("video tab: relative sibling mp4, captions embedded as a data URL (loads from file://), speeds 0.75x to 2x",
        'src="thumbs.mp4"' in H and vtt_data is not None
        and "One request" in base64.b64decode(vtt_data.group(1)).decode() and summ.get("captions_embedded") is True
        and re.findall(r'data-c8-speed="([\d.]+)"', H) == ["0.75", "1", "1.25", "1.5", "2"])
nov = copy.deepcopy(BASE)
nov.pop("video")
rnv, _ = build(nov, T / "novideo" / "p.html")
HV = (T / "novideo" / "p.html").read_text(encoding="utf-8") if rnv.returncode == 0 else ""
s.check("the video tab is optional: without a video there is no tab, no player and the page still builds",
        rnv.returncode == 0 and "<video" not in HV and 'role="tablist"' not in HV and "data-c8-section" in HV)
s.check("footer carries the snapshot hash and the build date from the content",
        re.search(r'<footer class="c8-footer">\s*<p>Describes the code at snapshot <code>4f9c2e7a1b3d</code> · built 2026-10-22</p>', H) is not None)

# ------------------------------------------------------------------ pages for the browser gate, checked in parallel
cust = copy.deepcopy(BASE)
cust["sections"].insert(2, CUSTOM)
CPAGE = T / "custom" / "thumbs.html"
rcu, scu = build(cust, CPAGE, extra=["--plan-out", T / "custom" / "plan.json"])
P_DARK = T / "kit2" / "thumbs.html"
rk, sk = build(BASE, P_DARK, kit=KITS / "night-gallery.json")
SEEDED = """
<section id="seeded" class="c8-section" data-c8-section aria-labelledby="seeded-t"><h2 id="seeded-t">Seeded</h2>
<p style="color:#c9cfd2">faint text that fails contrast</p>
<span style="cursor:pointer;text-decoration:underline">looks like a link but is not one</span>
<div style="width:900px;height:4px"></div>
<img alt="" src="seeded.png">
<button type="button" tabindex="4">jumps the queue</button>
<button type="button" class="c8-btn">Does nothing</button>
</section></main>"""
SP = T / "seeded" / "s.html"
SP.parent.mkdir()
SP.write_text(H.replace("</main>", SEEDED).replace('src="seeded.png"', 'src="' + "https:" + '//example.invalid/pixel.png"'),
              encoding="utf-8")
NP = T / "seeded" / "nojs.html"
NP.write_text(re.sub(r"<script>\n.*?</script>", "<script></script>", H, flags=re.S), encoding="utf-8")
UP = T / "seeded" / "unlinked.html"
UP.write_text(H.replace('aria-describedby="c8-tip-', 'data-was="c8-tip-'), encoding="utf-8")
SHOTS = T / "shots"
a11y_start("main", PAGE, shots=SHOTS)
a11y_start("custom", CPAGE, only="static,network,contrast,focus,toc,affordance,phone,tooltip,print")
a11y_start("seeded", SP, only="static,network,contrast,focus,affordance,phone")
a11y_start("nojs", NP, only="toc")
a11y_start("unlinked", UP, only="tooltip")
if rk.returncode == 0:
    a11y_start("darkkit", P_DARK, only="contrast,theme")

# W8-F1: glossary terms that are NOT rendered when the page opens (inside stepper steps that are not
# current) and terms that sit only in the first screen (cannot be scrolled down to the bottom edge)
GL_STEP = [{"term": "full-size photo", "definition": "the original image, before any resize."},
           {"term": "stores the copy", "definition": "writes the resized image into the cache for the next request."}]
GL_TOP = [{"term": "answered from memory", "definition": "served from the cache without reading the original."},
          {"term": "surprise a caller", "definition": "take longer than a caller expects."}]
c_step = copy.deepcopy(BASE)
c_step["glossary"] = GL_STEP
TSP = T / "terms" / "steps.html"
r_tsp, _ = build(c_step, TSP)
c_top = copy.deepcopy(BASE)
c_top["glossary"] = GL_TOP
TTP = T / "terms" / "top.html"
r_ttp, _ = build(c_top, TTP)
HTP = TTP.read_text(encoding="utf-8") if r_ttp.returncode == 0 else ""
# seeded: the page never flips its tooltip -> the flip check must still fail on a first-screen term
NFP = T / "terms" / "noflip.html"
NFP.write_text(HTP.replace("if (top + h > vh - MARGIN) {", "if (false) {"), encoding="utf-8")
# seeded: a term held hidden by markup the gate has no way to open -> its checks fail, the gate does not crash
HIP = T / "terms" / "hidden.html"
HIP.write_text(HTP.replace('<span class="c8-term-wrap">', '<span class="c8-term-wrap" style="display:none">', 1)
               if HTP.count('<span class="c8-term-wrap">') >= 2 else HTP, encoding="utf-8")
for _n, _pg in (("tsteps", TSP), ("ttop", TTP), ("tnoflip", NFP), ("thidden", HIP)):
    if _pg.exists():
        a11y_start(_n, _pg, only="tooltip")

# ------------------------------------------------------------------ glossary (C2), static
marks = summ.get("glossary_marked") or []
s.check("glossary: the first use of each term is marked exactly once on the page",
        sorted(m["term"] for m in marks) == sorted(g["term"] for g in BASE["glossary"])
        and H.count('class="c8-term"') == len(BASE["glossary"]), marks)
s.check("glossary: 'cache' is not matched inside 'cached' (the earlier 'cached' stays plain text)",
        "Most thumbnails are cached already" in H and 'data-term="cache"' in H
        and H.index("cached already") < H.index('data-term="cache"')
        and not re.search(r'<button[^>]*data-term="cache"[^>]*>cached<', H))
gl = {"title": "Glossary edge cases", "conclusion": "The Cache of the `cache` kind.", "snapshot": "abc123def456",
      "build_date": "2026-10-22",
      "glossary": [{"term": "cache", "definition": "a store kept close at hand."},
                   {"term": "warm path", "definition": "the path taken when the cache already holds the answer.",
                    "scope": "section"},
                   {"term": "evict", "definition": "to remove an entry to make room."}],
      "sections": [
          {"id": "one", "title": "The cache and the warm path", "summary": "Only `evict` and [a link about evict](#two) mention it here.",
           "what": {"html": "<p>An <code>evict</code> call, <a href=\"#two\">evict link</a>, <span data-no-gloss>evict</span>.</p><h3>evict heading</h3><p>Then the warm path; the WARM PATH again.</p>"}},
          {"id": "two", "title": "Second", "summary": "Now evict appears in prose, and the warm path again in a new section."}]}
rg, sg = build(gl, T / "gloss" / "g.html")
HG = (T / "gloss" / "g.html").read_text(encoding="utf-8") if rg.returncode == 0 else ""
mg = sg.get("glossary_marked") or []
s.check("glossary: case-insensitive match keeps the original case of the text ('Cache' stays 'Cache')",
        any(m["term"] == "cache" and m["text"] == "Cache" for m in mg) and '>Cache</button>' in HG, mg)
s.check("glossary: terms inside code, headings, links and data-no-gloss are not marked",
        [m["section"] for m in mg if m["term"] == "evict"] == ["two"]
        and "<code>cache</code>" in HG and "<code>evict</code>" in HG and ">evict heading</h3>" in HG
        and "<h2 id=\"one-title\" tabindex=\"-1\">The cache and the warm path</h2>" in HG, mg)
s.check("glossary: section scope marks a term once per section, page scope once per page",
        [m["section"] for m in mg if m["term"] == "warm path"] == ["one", "two"]
        and [m["section"] for m in mg if m["term"] == "cache"] == ["top"], mg)
s.check("glossary tooltip: a button whose aria-describedby points at a role=tooltip holding the definition",
        all(re.search(r'<button type="button" class="c8-term" data-term="%s"[^>]*aria-describedby="(%s)">[^<]+</button>'
                      r'<span class="c8-tip" role="tooltip" id="\1">' % (m["term"].replace(" ", "-"), m["tip"]), H)
            for m in marks), marks)

# ------------------------------------------------------------------ flows from a graph (layout.js hook)
graph = {"nodes": [{"id": "a", "label": "Start"}, {"id": "b", "label": "Yes end"}, {"id": "c", "label": "No end"}],
         "edges": [{"from": "a", "to": "b", "label": "it holds"}, {"from": "a", "to": "c", "label": "it does not"}]}
gc = {"title": "Graph flow", "conclusion": "Two exits.", "sections": [{"id": "g", "title": "Graph", "summary": "Two exits.",
      "components": [{"type": "flow", "title": "Graph flow", "graph": graph}]}]}
stub = T / "stub_layout.js"
stub.write_text("const fs=require('fs');const g=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));"
                "process.stdout.write('<svg viewBox=\"0 0 100 40\"><g class=\"c8-node\" data-node-id=\"'+g.nodes[0].id+'\">"
                "<rect x=\"1\" y=\"1\" width=\"98\" height=\"38\"/><text x=\"50\" y=\"24\">laid out</text></g></svg>');",
                encoding="utf-8")
if shutil.which("node"):
    rl, sl = build(gc, T / "graph1" / "g.html", extra=["--layout-js", stub])
    HL = (T / "graph1" / "g.html").read_text(encoding="utf-8") if rl.returncode == 0 else ""
    s.check("a flow given as a graph is drawn by the layout script when one is present, with a list alternative",
            rl.returncode == 0 and (sl.get("flows") or [{}])[0].get("mode") == "layout.js" and "laid out" in HL
            and 'class="c8-flow-text"' in HL and "when it holds" in HL, sl.get("flows"))
else:
    s.check("layout-script hook check skipped: node not on PATH", True, "node missing")
rl2, sl2 = build(gc, T / "graph2" / "g.html", extra=["--layout-js", T / "no_such_layout.js"])
HL2 = (T / "graph2" / "g.html").read_text(encoding="utf-8") if rl2.returncode == 0 else ""
s.check("without a layout script a graph flow falls back to an accessible indented list and says so",
        rl2.returncode == 0 and (sl2.get("flows") or [{}])[0].get("mode") == "outline" and 'class="c8-outline"' in HL2
        and any("indented list" in w for w in sl2.get("warnings", [])), sl2.get("warnings"))

# ------------------------------------------------------------------ existing gates on the built page
rs = run("scripts/gates/dom_smoke.py", [PAGE, "--expect", "data-c8-section:4", 'aria-current="true":1',
                                        'role="tooltip":4', "c8-term:4", "data-c8-ready:1"], env=ENV, timeout=120)
ds = last_json(rs.stdout)
s.check("dom_smoke passes on the fixture page (rendered DOM; sections, aria-current, tooltips, stepper ready)",
        rs.returncode == 0 and ds.get("render_mode") == "rendered-dom", (rs.returncode, ds.get("summary"), ds.get("markers")))
rv = run("scripts/gates/visual_density.py", [PLAN, "--kind", "page"], env=ENV, timeout=60)
s.check("the page plan emitted by the build passes visual_density (every section has a visual and an interaction)",
        rv.returncode == 0, last_json(rv.stdout).get("summary"))


def code_blank(html_text: str) -> tuple:
    """Blank <style> and executable <script> bodies (keep line numbers) — the reader-facing projection."""
    lines = html_text.split("\n")
    code_lines = set()
    for m in re.finditer(r"<(style|script)(\s[^>]*)?>(.*?)</\1>", html_text, re.S):
        if m.group(1) == "script" and "application/json" in (m.group(2) or ""):
            continue
        a = html_text.count("\n", 0, m.start(3))
        b = html_text.count("\n", 0, m.end(3))
        code_lines.update(range(a + 1, b + 2))
    out = [("" if (i + 1) in code_lines else ln) for i, ln in enumerate(lines)]
    return "\n".join(out), code_lines


proj, code_lines = code_blank(H)
(OUT / "projection.html").write_text(proj, encoding="utf-8")
rc = run("scripts/gates/content_ban.py", [OUT / "projection.html"], env=ENV, timeout=60)
s.check("content_ban finds nothing in the page's text and markup (everything outside inline style/script code)",
        rc.returncode == 0, [(f["kind"], f["match"]) for f in last_json(rc.stdout).get("findings", [])][:8])
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("c8_cb_page", ROOT / "scripts" / "gates" / "content_ban.py")
_cb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cb)
raw_hits = _cb.line_hits(H, [], set())
stray = [f for f in raw_hits if f["line"] not in code_lines]
kinds: dict = {}
for f in raw_hits:
    kinds[f["kind"]] = kinds.get(f["kind"], 0) + 1
s.check("every content_ban hit on the raw file sits inside inline CSS/JS code (the regex reads var()/calls as acronyms)",
        not stray, {"total": len(raw_hits), "by_kind": kinds, "outside_code": stray[:4]})

# ------------------------------------------------------------------ browser gate on the fixture page
ra, res = a11y_wait("main")
live = bool(res.get("live_checks_ran"))
if not live:
    s.check("a11y gate live checks skipped honestly: no headless browser here", ra == 2, res.get("summary"))
else:
    s.check("a11y gate: text contrast >= 4.5:1 (3:1 large) in the light skin, computed styles", *chk(res, "contrast_light"))
    s.check("a11y gate: text contrast >= 4.5:1 (3:1 large) in the dark skin, computed styles", *chk(res, "contrast_dark"))
    s.check("a11y gate: Tab reaches every control in DOM order with no backwards jump on screen", *chk(res, "focus_order"))
    s.check("a11y gate: every focused control shows a ring of >= 2 px at >= 3:1", *chk(res, "focus_visible"))
    s.check("a11y gate: after scrolling, the contents link of the section in view has aria-current=true; "
            "the last section at the page bottom; a click holds 800 ms then follows the scroll", *chk(res, "toc_aria_current"))
    s.check("a11y gate: the page opens offline and requests nothing beyond file:/data:", *chk(res, "no_external_requests"))
    s.check("a11y gate: no uncaught script error", *chk(res, "no_script_errors"))
    s.check("a11y gate: everything that looks clickable is a control, and every control responds", *chk(res, "affordance_honest"))
    s.check("a11y gate: no horizontal page scroll at 320, 390 and 420 px", *chk(res, "phone_no_hscroll"))
    s.check("a11y gate: print media unfolds every How block, shows every step, hides navigation, prints chip targets",
            *chk(res, "print_unfolds"))
    s.check("a11y gate: reduced motion turns smooth scrolling and kit durations off", *chk(res, "reduced_motion"))
    s.check("a11y gate: the theme button flips between the kit's light and dark skins and starts from the OS setting",
            *chk(res, "theme_toggle"))
    s.check("a11y gate: the example stepper answers its buttons and the arrow/Home/End keys", *chk(res, "stepper_keyboard"))
    s.check("a11y gate: the Watch tab shows the player, speed buttons set playbackRate, arrow keys return to Read",
            *chk(res, "video_speed"))
    s.check("C2: keyboard focus opens the tooltip", *chk(res, "tooltip_keyboard_focus_opens"))
    s.check("C2: Escape closes the open tooltip", *chk(res, "tooltip_escape_closes"))
    s.check("C2: mouse hover opens the tooltip and leaving closes it", *chk(res, "tooltip_hover_opens"))
    s.check("C2: an emulated touch tap (hasTouch) opens the tooltip; a tap elsewhere closes it", *chk(res, "tooltip_touch_tap_opens"))
    an_ok, an_d = chk(res, "tooltip_accessible_name")
    names = ((res.get("checks") or {}).get("tooltip_accessible_name") or {}).get("detail") or {}
    defs = {g["term"]: g["definition"] for g in BASE["glossary"]}
    s.check("C2: accessible name is the term and the description is exactly the definition (browser accessibility tree)",
            an_ok and names.get("source") == "accessibility tree"
            and all(n["description"].strip() == defs.get(n["term"], defs.get(n["term"].lower(), "")).strip()
                    for n in names.get("terms", [])), an_d)
    s.check("C2: tooltip elements carry role=tooltip and are linked from their term", *chk(res, "tooltip_role_and_link"))
    s.check("C2: near the bottom edge the tooltip flips above the term and stays on screen", *chk(res, "tooltip_flips_on_screen"))
    s.check("C2: at 320 px every tooltip is clamped inside the viewport", *chk(res, "tooltip_clamped_at_phone_width"))
    s.check("a11y gate overall verdict on the fixture page is PASS (exit 0)", ra == 0 and res.get("status") == "PASS",
            res.get("failed"))
    try:
        from PIL import Image, ImageStat  # type: ignore
        stats = {}
        for n in ("light", "dark", "print", "phone"):
            p = SHOTS / f"{n}.png"
            if p.exists():
                im = Image.open(p).convert("L")
                im.thumbnail((400, 400))
                stats[n] = (round(ImageStat.Stat(im).stddev[0], 1), round(ImageStat.Stat(im).mean[0], 1))
        s.check("light, dark, print-emulated and phone screenshots exist and are not blank (pixel variance)",
                len(stats) == 4 and all(v[0] > 8 for v in stats.values()), stats)
        s.check("the dark screenshot is really dark and the light one light (mean luminance)",
                len(stats) == 4 and stats["dark"][1] < 90 and stats["light"][1] > 160, stats)
    except ImportError:
        sizes = {n: (SHOTS / f"{n}.png").stat().st_size for n in ("light", "dark", "print", "phone") if (SHOTS / f"{n}.png").exists()}
        s.check("light, dark, print-emulated and phone screenshots exist and are not blank (size; Pillow absent)",
                len(sizes) == 4 and all(v > 20000 for v in sizes.values()), sizes)

# ------------------------------------------------------------------ custom section (the template is not a cage)
HC = CPAGE.read_text(encoding="utf-8") if rcu.returncode == 0 else ""
s.check("a page with a custom section builds and keeps its own markup, CSS and inline SVG",
        rcu.returncode == 0 and 'data-c8-custom="full-cache"' in HC and ".fc-grid{" in HC and 'class="fc-slot fc-changed"' in HC
        and 'viewBox="0 0 340 60"' in HC and not scu.get("removed_unsafe"), (scu.get("removed_unsafe"), scu.get("summary")))
rcs = run("scripts/gates/dom_smoke.py", [CPAGE, "--expect", "data-c8-section:5", "data-c8-custom:1"], env=ENV, timeout=120)
s.check("the custom-section page passes dom_smoke", rcs.returncode == 0, last_json(rcs.stdout).get("summary"))
rcv = run("scripts/gates/visual_density.py", [T / "custom" / "plan.json", "--kind", "page"], env=ENV, timeout=60)
s.check("the custom-section page plan passes visual_density", rcv.returncode == 0, last_json(rcv.stdout).get("summary"))
if live:
    rca, cres = a11y_wait("custom")
    s.check("the custom-section page passes every a11y gate check (contrast both skins, focus, aria-current, affordance)",
            rca == 0 and cres.get("status") == "PASS", cres.get("failed"))

# ------------------------------------------------------------------ hostile custom html is neutralised
hos = copy.deepcopy(BASE)
hos["sections"].append(HOSTILE)
HPAGE = T / "hostile" / "thumbs.html"
rh, sh = build(hos, HPAGE)
HH = HPAGE.read_text(encoding="utf-8") if rh.returncode == 0 else ""
seg = HH[HH.find('data-c8-custom="hostile"'):HH.find('data-c8-custom="hostile"') + 3000] if HH else ""
s.check("custom_html sanitiser removes script, event handlers, javascript:, iframes, forms, objects and @import",
        rh.returncode == 0 and "<script>window.__pwned" not in HH and "onclick" not in seg and "javascript:" not in HH
        and "<iframe" not in seg and "<form" not in seg and "<object" not in seg and "@import" not in HH
        and "Plain text stays." in seg and len(sh.get("removed_unsafe", [])) >= 8, sh.get("removed_unsafe"))
s.check("a page built with hostile custom HTML still has zero external URLs",
        rh.returncode == 0 and not re.search(r"https?://|//example", HH), re.findall(r".{20}example.{20}", HH)[:3])

# ------------------------------------------------------------------ seeded defects the gate must catch
if live:
    _, sres = a11y_wait("seeded")
    s.check("a11y gate catches seeded low-contrast text", not chk(sres, "contrast_light")[0], chk(sres, "contrast_light")[1])
    aff = ((sres.get("checks") or {}).get("affordance_honest") or {}).get("detail") or {}
    s.check("a11y gate catches a clickable-looking element that is not a control",
            any("looks like a link" in x.get("text", "") for x in aff.get("looks_clickable_but_not_a_control", [])), aff)
    s.check("a11y gate catches horizontal scroll at phone width", not chk(sres, "phone_no_hscroll")[0])
    s.check("a11y gate catches an external request (and the static scan flags the reference)",
            not chk(sres, "no_external_requests")[0] and not chk(sres, "static_no_external_refs")[0], chk(sres, "no_external_requests")[1])
    s.check("a11y gate catches a positive tabindex that breaks DOM focus order", not chk(sres, "focus_order")[0], chk(sres, "focus_order")[1])
    s.check("a seeded page is REVIEW, not PASS (exit 1)", sres.get("status") == "REVIEW")

    _, nres = a11y_wait("nojs")
    s.check("a11y gate catches a page whose contents tracking is gone (no aria-current)", not chk(nres, "toc_aria_current")[0])
    _, ures = a11y_wait("unlinked")
    s.check("a11y gate catches glossary terms whose tooltip is not linked by aria-describedby",
            ures.get("status") != "PASS", ures.get("failed"))
    ires = sres
    idet = ((ires.get("checks") or {}).get("affordance_honest") or {}).get("detail") or {}
    s.check("a11y gate catches a button that does nothing when clicked",
            any(d.get("control") == "Does nothing" for d in idet.get("controls_that_do_nothing", [])), idet.get("controls_that_do_nothing"))

# ------------------------------------------------------------------ A3 page half: the page reads the active kit
HK = P_DARK.read_text(encoding="utf-8") if rk.returncode == 0 else ""


def kit_vars(text: str) -> dict:
    m = re.search(r'<style id="c8-kit-vars">(.*?)</style>', text, re.S)
    return parse_css_vars(m.group(1)) if m else {}


va, vb = kit_vars(H), kit_vars(HK)
qw = json.loads((KITS / "quiet-ward.json").read_text())
ng = json.loads((KITS / "night-gallery.json").read_text())
diff = [k for k in ("--c8-paper", "--c8-ink", "--c8-accent", "--c8-font-body", "--c8-page-size-body", "--c8-ease")
        if va.get(k) != vb.get(k)]
s.check("A3: two kits give different --c8-* vars in the built page (paper, ink, accent, font, size, easing) with no code edit",
        rk.returncode == 0 and va.get("--c8-paper") == qw["palette"]["roles"]["paper"]
        and vb.get("--c8-paper") == ng["palette"]["dark"]["paper"] and len(diff) >= 5, diff)
pcss_a = re.search(r'<style id="c8-page-css">(.*?)</style>', H, re.S)
pcss_b = re.search(r'<style id="c8-page-css">(.*?)</style>', HK, re.S)
s.check("A3: the template CSS and JS are byte-identical across kits — only the kit block changes",
        pcss_a and pcss_b and pcss_a.group(1) == pcss_b.group(1)
        and re.search(r"<script>\n(.*?)</script>", H, re.S).group(1) == re.search(r"<script>\n(.*?)</script>", HK, re.S).group(1))
if live:
    _, kres = a11y_wait("darkkit")
    s.check("A3: a dark-base kit's page passes text contrast in both skins and its theme toggle works",
            kres.get("status") == "PASS", kres.get("failed"))
    probe = (
        "import sys,json\nfrom playwright.sync_api import sync_playwright\nout={}\n"
        "with sync_playwright() as p:\n  b=p.chromium.launch()\n"
        "  for f in sys.argv[1:]:\n    for sc in ('light','dark'):\n"
        "      pg=b.new_page(color_scheme=sc); pg.goto('file://'+f)\n"
        "      out[f+'|'+sc]=pg.evaluate(\"()=>getComputedStyle(document.documentElement).backgroundColor\"); pg.close()\n"
        "  b.close()\nprint(json.dumps(out))\n")
    pf = T / "bgprobe.py"
    pf.write_text(probe, encoding="utf-8")
    bp_ = subprocess.run([PY, str(pf), str(PAGE), str(P_DARK)], capture_output=True, text=True, env={**os.environ, **ENV}, timeout=90)
    bg = last_json(bp_.stdout)

    def rgb(hx):
        hx = hx.lstrip("#")
        return "rgb(%d, %d, %d)" % tuple(int(hx[i:i + 2], 16) for i in (0, 2, 4))
    s.check("A3: in a real browser each page paints its own kit's paper, light under a light OS and dark under a dark OS",
            bg.get(f"{PAGE}|light") == rgb(qw["palette"]["roles"]["paper"]) and bg.get(f"{PAGE}|dark") == rgb(qw["palette"]["dark"]["paper"])
            and bg.get(f"{P_DARK}|light") == rgb(ng["palette"]["roles"]["paper"]) and bg.get(f"{P_DARK}|dark") == rgb(ng["palette"]["dark"]["paper"]),
            bg)
skin = {"family": "invented:harbour-test", "roles": dict(qw["palette"]["roles"], paper="#fbf8f1"),
        "dark": dict(qw["palette"]["dark"])}
ds_file = T / "ds" / "design_system.json"
ds_file.parent.mkdir()
ds_file.write_text(json.dumps({"schema": 1, "kit": "quiet-ward", "kit_spec": qw, "palette": skin}), encoding="utf-8")
rds, sds = build(BASE, T / "ds" / "thumbs.html", kit=ds_file)
HDS = (T / "ds" / "thumbs.html").read_text(encoding="utf-8") if rds.returncode == 0 else ""
s.check("A3: a design_system.json palette (Palette Director skin) overrides the kit's own palette in the page",
        rds.returncode == 0 and kit_vars(HDS).get("--c8-paper") == "#fbf8f1" and sds.get("palette_family") == "invented:harbour-test",
        (kit_vars(HDS).get("--c8-paper"), sds.get("palette_family")))
ds2 = T / "ds2" / "design_system.json"
ds2.parent.mkdir()
ds2.write_text(json.dumps({"schema": 1, "kit": "ledger-ink"}), encoding="utf-8")
rd2, sd2 = build(BASE, T / "ds2" / "thumbs.html", kit=ds2)
s.check("A3: a design_system.json that only names a kit id builds with that kit", rd2.returncode == 0 and sd2.get("kit") == "ledger-ink", sd2)
badkit = copy.deepcopy(qw)
badkit["palette"]["roles"]["ink"] = badkit["palette"]["roles"]["paper"]
(T / "badkit.json").write_text(json.dumps(badkit), encoding="utf-8")
rbk, sbk = build(BASE, T / "badkit" / "p.html", kit=T / "badkit.json")
s.check("A3: a kit that fails the outcome rules (ink equals paper) is refused, nothing written",
        rbk.returncode == 3 and not (T / "badkit" / "p.html").exists() and "outcome rules" in sbk.get("summary", ""), sbk)
fontkit = copy.deepcopy(qw)
(T / "fonts").mkdir()
(T / "fonts" / "ok.woff2").write_bytes(b"wOF2-test-bytes")
(T / "fonts" / "closed.woff2").write_bytes(b"wOF2-closed")
fontkit["theme"]["vendorable"] = [{"name": "Open Test", "family": "Open Test", "licence": "OFL-1.1", "file": "fonts/ok.woff2"},
                                  {"name": "Closed Test", "licence": "proprietary", "file": "fonts/closed.woff2"}]
(T / "fontkit.json").write_text(json.dumps(fontkit), encoding="utf-8")
rf, sf = build(BASE, T / "fontpage" / "p.html", kit=T / "fontkit.json")
HF = (T / "fontpage" / "p.html").read_text(encoding="utf-8") if rf.returncode == 0 else ""
s.check("a vendored OFL woff2 present on disk is embedded as base64; a non-OFL font is not",
        rf.returncode == 0 and sf.get("fonts_embedded") == 1 and base64.b64encode(b"wOF2-test-bytes").decode() in HF
        and base64.b64encode(b"wOF2-closed").decode() not in HF and any("not OFL" in w for w in sf.get("warnings", [])), sf.get("warnings"))
s.check("with no vendored font the page uses the kit's system font stack, never a font URL",
        "@font-face" not in H and va.get("--c8-font-body") == qw["theme"]["family"]["body"])

# ------------------------------------------------------------------ W8-F1: palette on the built page
COLOUR_LIT = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(|\b(?:hwb|lab|lch|oklab|oklch)\(")
tpl_files = [TPL / "page.css", TPL / "page.js", TPL / "shell.html"] + sorted((TPL / "components").glob("*.html"))
lit_hits = []
for f in tpl_files:
    for i, ln in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
        code_ln = re.sub(r"/\*.*?\*/", "", ln)
        if f.suffix == ".html":
            code_ln = re.sub(r"<!--.*?-->", "", code_ln)
        if COLOUR_LIT.search(code_ln):
            lit_hits.append(f"{f.name}:{i}: {ln.strip()[:90]}")
s.check("W8-F1: the page template paints no literal colour (no hex, rgb(), hsl() or colour function in "
        "page.css, page.js, shell.html or any component) — only kit roles and their derivations", not lit_hits, lit_hits[:6])
fb_hits = [f"{f.name}: {m.group(0)}" for f in tpl_files
           for m in re.finditer(r"var\(\s*--c8-[\w-]+\s*,\s*(?:#|rgb|hsl|[a-z]+\))", f.read_text(encoding="utf-8"))
           if not re.search(r",\s*(?:transparent|currentColor|inherit)\)", m.group(0))]
s.check("W8-F1: no role variable in the template carries a literal colour fallback (var(--c8-ink, #111))",
        not fb_hits, fb_hits[:6])


def palette_gate(design: Path, page: Path) -> dict:
    rr = run("scripts/gates/check_palette.py", [design, "--html", page], env=ENV, timeout=120)
    return dict(last_json(rr.stdout), _rc=rr.returncode)


pg_main = palette_gate(KITS / "quiet-ward.json", PAGE)
s.check("W8-F1: the palette gate on the built fixture page is PASS (no off-palette, rainbow or hue-only-meaning)",
        pg_main.get("_rc") == 0 and pg_main.get("status") == "PASS", json.dumps(pg_main.get("counts")))
if rk.returncode == 0:
    pg_dark = palette_gate(KITS / "night-gallery.json", P_DARK)
    s.check("W8-F1: the palette gate on a dark-base kit's built page is PASS", pg_dark.get("_rc") == 0
            and pg_dark.get("status") == "PASS", json.dumps(pg_dark.get("counts")))
pg_ds = palette_gate(ds_file, T / "ds" / "thumbs.html") if rds.returncode == 0 else {}
s.check("W8-F1: the palette gate on a page built from a design_system.json skin is PASS",
        pg_ds.get("_rc") == 0 and pg_ds.get("status") == "PASS", json.dumps(pg_ds.get("counts")))
HOT = T / "site" / "hot.html"
HOT.write_text(H.replace("</style>\n</head>", ".c8-hot { border: 2px solid #ff00aa; }\n</style>\n</head>", 1), encoding="utf-8")
pg_hot = palette_gate(KITS / "quiet-ward.json", HOT)
s.check("W8-F1 (negative): the same built page with one hard-coded #ff00aa rule added is REVIEW with off-palette",
        "c8-hot" in HOT.read_text(encoding="utf-8") and pg_hot.get("_rc") == 1
        and (pg_hot.get("counts") or {}).get("off-palette", 0) >= 1, json.dumps(pg_hot.get("counts")))
pr_m = re.search(r"@media print\{\s*:root\{(.*?)\}", va and re.search(r'<style id="c8-kit-vars">(.*?)</style>', H, re.S).group(1) or "", re.S)
pr_vars = dict(re.findall(r"(--c8-[\w-]+):\s*([^;]+?)\s*!important;", pr_m.group(1))) if pr_m else {}
s.check("W8-F1: print pins the kit's LIGHT roles (!important) so a dark-skin reader prints dark ink on light paper",
        bool(pr_vars) and pr_vars.get("--c8-paper") == qw["palette"]["roles"]["paper"]
        and pr_vars.get("--c8-ink") == qw["palette"]["roles"]["ink"], {k: pr_vars.get(k) for k in ("--c8-paper", "--c8-ink")})

# ------------------------------------------------------------------ W8-F1: page a11y gate robustness
sys.path.insert(0, str(ROOT / "scripts" / "gates"))
import check_page_a11y as A11Y  # noqa: E402

_chk, _errs = {}, []


def _half(o):
    o["tooltip_present"] = {"ok": True, "detail": "1 term"}
    raise TimeoutError("Locator.scroll_into_view_if_needed: Timeout 5000ms exceeded")


A11Y.run_group(_chk, _errs, "tooltip", _half)
s.check("W8-F1: a probe error inside one check group is reported per check — results measured before it are kept, "
        "every unfinished check of the group fails with the error, and nothing else is touched",
        _chk.get("tooltip_present", {}).get("ok") is True and len(_chk) == len(A11Y.GROUP_CHECKS["tooltip"])
        and all(_chk[n].get("probe_error") and not _chk[n]["ok"] for n in A11Y.GROUP_CHECKS["tooltip"][1:])
        and len(_errs) == 1 and _errs[0]["group"] == "tooltip", _errs)
s.check("W8-F1: the browser gate never waits Playwright's 30 s default on one element (action timeout <= 5 s)",
        A11Y.ACTION_TIMEOUT_MS <= 5000 and "set_default_timeout(ACTION_TIMEOUT_MS)" in (ROOT / "scripts/gates/check_page_a11y.py").read_text(),
        A11Y.ACTION_TIMEOUT_MS)
if live:
    TIP_NAMES = A11Y.GROUP_CHECKS["tooltip"]
    rcs_, tres = a11y_wait("tsteps")
    s.check("W8-F1: glossary terms that sit only in stepper steps not yet shown are revealed through the stepper "
            "and every tooltip check passes (no crash, exit 0)",
            rcs_ == 0 and tres.get("status") == "PASS" and all(n in (tres.get("checks") or {}) for n in TIP_NAMES),
            (rcs_, tres.get("failed"), tres.get("summary")))
    hov = ((tres.get("checks") or {}).get("tooltip_hover_opens") or {}).get("detail") or {}
    s.check("W8-F1: the report says when a term had to be revealed before it was checked",
            bool(hov.get("revealed_first")), hov)
    rct, tt = a11y_wait("ttop")
    fl = ((tt.get("checks") or {}).get("tooltip_flips_on_screen") or {})
    s.check("W8-F1: terms only in the first screen still get a real flip test (the window is shortened so the term "
            "sits at the bottom edge) and it passes", rct == 0 and fl.get("ok") and (fl.get("detail") or {}).get("window_shortened_to"),
            json.dumps(fl)[:300])
    rcn, tn = a11y_wait("tnoflip")
    s.check("W8-F1 (negative): a page whose tooltip never flips above still fails tooltip_flips_on_screen",
            rcn == 1 and not chk(tn, "tooltip_flips_on_screen")[0], chk(tn, "tooltip_flips_on_screen")[1])
    rch, th = a11y_wait("thidden")
    s.check("W8-F1 (negative): a term hidden by markup the gate cannot open fails the tooltip checks it affects, "
            "every tooltip check is still reported, and the gate finishes (exit 1, not a crash) in under 60 s",
            rch == 1 and th.get("status") == "REVIEW" and all(n in (th.get("checks") or {}) for n in TIP_NAMES)
            and (th.get("seconds") or 999) < 60 and not chk(th, "tooltip_clamped_at_phone_width")[0],
            (rch, th.get("failed"), th.get("seconds")))

# ------------------------------------------------------------------ extension hook for later components
s.check("unknown component types without a template or html are refused with a clear message",
        build({"title": "x", "conclusion": "y", "sections": [{"id": "a", "title": "A", "summary": "b",
                                             "components": [{"type": "no-such-widget"}]}]}, T / "unk" / "p.html")[0].returncode == 3)

_rc = s.finish()
_td.cleanup()
sys.exit(_rc)
