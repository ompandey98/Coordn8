#!/usr/bin/env python3
"""Coordn8 benchmark runner — the current package vs the 1.5.0 baseline on six fixed fixtures.

    python3 bench/run.py [--fixture N[,N...]] [--version v2|v1.5|both] [--render short|none]
                         [--report PATH] [--results-out PATH] [--baseline TGZ] [--keep]

For every fixture (bench/fixtures/0N-*/ with its brief in bench/briefs/0N-*.md) it runs ONE
deterministic toolchain pass and scores the result:

  audience inference -> palette director -> style kit -> diagram plan for the fixture's main flow
  -> layout -> page build (templates/page) with a claims ledger generated from the fixture's real
  path:line facts -> two short scenes (scene runtime) -> instrumentation trace -> scores
  -> optional short render (2 scenes, 6-10 s in total) through capture_scene with its disk guard;
     frames are reclaimed by the encoder and the work directory is removed at the end.

Scores per fixture and version (every field carries a tag):
  quality      six measured dimensions (scripts/quality.py) over the scene traces
  palette      palette gate (design system + the page + the scene's painted text)
  diagram      plan entries with >= 2 written rejections, plan gate, budget gate
  language     share of page + narration sentences with no plain-language finding
  claims       share of claims that resolve to a real path:line or are marked inferred
  a11y         page accessibility gate checks passed + DOM smoke
  render       frames per second, peak disk (disk guard), frames left on disk, wall time

Tags: `measured` · `not-available` (the capability does not exist in that version — never a
fabricated zero) · `skipped` (not requested, or a tool is missing here) · `error`.

The 1.5.0 numbers come from running the 1.5.0 tree's OWN tools (extracted from the baseline
tarball into the temporary work directory) on the SAME inputs. Inputs the 1.5.0 tree cannot
produce itself (it has no palette, kit, diagram planner, layout engine or page builder) are
prepared once by the current toolchain, and the score that depends on producing them is reported
`not-available` for 1.5.0.

Isolation: HOME, COORDN8_HOME and CLAUDE_HOME point into the work directory for every tool run.
Nothing is sent to any hosted service; no model is invoked. Python 3.9, standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import site
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

BENCH = Path(__file__).resolve().parent
PKG = BENCH.parent
FIXTURES = BENCH / "fixtures"
BRIEFS = BENCH / "briefs"
sys.path.insert(0, str(BENCH))
import score  # noqa: E402

DEFAULT_BASELINE = Path(os.environ.get(
    "COORDN8_BASELINE_TGZ",
    str(PKG.parent / "Coordn8_v2_handoff" / "_baseline" / "coordn8_v1.5.0_tree_20261022_2121.tgz")))
REFS_DIR = Path(os.environ.get("COORDN8_REFS_DIR", "/tmp/coordn8_refs"))

FIXTURE_MAX_FILES = 40
FIXTURE_MAX_BYTES = 200 * 1024
FPS = 24
SCENE_RENDER_MIN_S, SCENE_RENDER_MAX_S = 3.0, 5.0
W, H = 1920, 1080
SAFE_X, SAFE_Y = 96, 54
SECONDS_PER_WORD = 0.36
LINE_GAP_S = 0.25


# ============================================================================ small helpers
def tag(value, t: str = "measured", note: str = "") -> dict:
    d = {"value": value, "tag": t}
    if note:
        d["note"] = note
    return d


def na(note: str) -> dict:
    return tag(None, "not-available", note)


def skipped(note: str) -> dict:
    return tag(None, "skipped", note)


def err(note: str) -> dict:
    return tag(None, "error", note[:300])


def last_json(text: str):
    """The last line of stdout that parses as a JSON object (gates print one)."""
    for ln in reversed((text or "").strip().splitlines()):
        ln = ln.strip()
        if ln.startswith("{"):
            try:
                return json.loads(ln)
            except ValueError:
                continue
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def fixture_dirs() -> list:
    return sorted(p for p in FIXTURES.iterdir() if p.is_dir() and re.match(r"^\d\d-", p.name))


def fixture_stats(d: Path) -> dict:
    files = [p for p in d.rglob("*") if p.is_file()]
    return {"files": len(files), "bytes": sum(p.stat().st_size for p in files)}


def brief_for(d: Path) -> Path:
    hits = sorted(BRIEFS.glob(d.name[:3] + "*.md"))
    if not hits:
        raise FileNotFoundError(f"no brief for fixture {d.name} in {BRIEFS}")
    return hits[0]


def find_line(path: Path, needle: str):
    try:
        for i, ln in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if needle in ln:
                return i
    except OSError:
        return None
    return None


def words(s: str) -> int:
    return len(re.findall(r"\S+", s))


# ============================================================================ the sandbox
class Sandbox:
    """A temporary work directory; every tool runs with HOME/COORDN8_HOME/CLAUDE_HOME inside it."""

    def __init__(self, keep: bool = False):
        self.root = Path(tempfile.mkdtemp(prefix="c8bench-"))
        self.keep = keep
        real_home = Path(os.path.expanduser("~"))
        self.env = dict(os.environ)
        for k in ("home", "coordn8_home", "claude_home"):
            (self.root / "_env" / k).mkdir(parents=True, exist_ok=True)
        self.env.update({"HOME": str(self.root / "_env" / "home"),
                         "COORDN8_HOME": str(self.root / "_env" / "coordn8_home"),
                         "CLAUDE_HOME": str(self.root / "_env" / "claude_home"),
                         "COORDN8_SANDBOX": "1", "PYTHONDONTWRITEBYTECODE": "1"})
        # Read-only reuse of already-installed tools under the real home: the headless browser
        # cache and the user's Python packages. Nothing is written there.
        pw = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or str(real_home / "Library" / "Caches" / "ms-playwright")
        if Path(pw).exists():
            self.env["PLAYWRIGHT_BROWSERS_PATH"] = pw
        ub = getattr(site, "USER_BASE", None)
        if ub and Path(ub).exists():
            self.env["PYTHONUSERBASE"] = ub
        # Branded desktop Chrome, run under a temporary HOME, writes its screenshot and then
        # exits non-zero at teardown, which the capture probe reads as a failure. The headless
        # shell that ships with the browser cache exits cleanly, so put it first on PATH.
        self.browser_note = ""
        shells = sorted(Path(pw).glob("chromium_headless_shell-*/*/chrome-headless-shell")) if Path(pw).exists() else []
        if shells:
            shim = self.root / "_env" / "bin"
            shim.mkdir(parents=True, exist_ok=True)
            sh = shim / "google-chrome"     # a wrapper, not a symlink: the binary finds its data beside itself
            sh.write_text('#!/bin/sh\nexec "%s" "$@"\n' % shells[-1], encoding="utf-8")
            sh.chmod(0o755)
            self.env["PATH"] = str(shim) + os.pathsep + self.env.get("PATH", "")
            self.browser_note = "cold-launch browser: the headless shell from the local browser cache"

    def run(self, argv, *, timeout=300, cwd=None) -> subprocess.CompletedProcess:
        try:
            return subprocess.run([str(a) for a in argv], capture_output=True, text=True, env=self.env,
                                  timeout=timeout, cwd=str(cwd) if cwd else None)
        except subprocess.TimeoutExpired as e:
            return subprocess.CompletedProcess(e.cmd, 124, e.stdout or "", f"TIMEOUT {timeout}s")
        except OSError as e:
            return subprocess.CompletedProcess(argv, 127, "", f"{type(e).__name__}: {e}")

    def py(self, tree: Path, script: str, *args, timeout=300, cwd=None):
        return self.run([sys.executable, tree / script, *args], timeout=timeout, cwd=cwd)

    def cleanup(self) -> None:
        if not self.keep:
            shutil.rmtree(self.root, ignore_errors=True)


def extract_baseline(sb: Sandbox, tgz: Path):
    if not tgz.exists():
        return None, f"baseline tarball not found at {tgz.name}"
    dest = sb.root / "baseline-1.5.0"
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tgz, "r:gz") as tf:
        safe = [m for m in tf.getmembers()
                if not (m.name.startswith("/") or ".." in Path(m.name).parts) and (m.isfile() or m.isdir())]
        tf.extractall(dest, members=safe)
    tops = [p for p in dest.iterdir() if p.is_dir()]
    root = dest
    if len(tops) == 1 and not (dest / "scripts").exists():
        root = tops[0]
    ver = (root / "VERSION").read_text(encoding="utf-8").strip() if (root / "VERSION").exists() else "?"
    if not (root / "scripts").exists():
        return None, "baseline tarball has no scripts/ tree"
    return root, ver


def frames_on_disk(root: Path) -> int:
    return sum(1 for p in root.rglob("frame_*") if p.is_file())


# ============================================================================ inputs
def load_fixture(d: Path) -> dict:
    spec = json.loads((d / "BENCH.json").read_text(encoding="utf-8"))
    spec["_dir"] = d
    spec["_brief_path"] = brief_for(d)
    spec["_brief"] = spec["_brief_path"].read_text(encoding="utf-8")
    return spec


def claims_for(spec: dict) -> list:
    """The claims ledger: every flow step and stated fact -> its real path:line, plus the
    fixture's honest inferences. A reference whose anchor text is not found stays unmarked
    (and costs coverage) — it is never guessed."""
    d = spec["_dir"]
    out, n = [], 0
    for it in spec["flow"]["items"]:
        n += 1
        ref = it.get("ref") or {}
        line = find_line(d / ref.get("path", ""), ref.get("match", "")) if ref else None
        c = {"id": f"c{n}", "text": f"{it['label']}: {it['desc']}"}
        if line:
            c["ref"] = f"{ref['path']}:{line}"
            c["status"] = "read"
        out.append(c)
    for f in spec.get("facts") or []:
        n += 1
        line = find_line(d / f["path"], f["match"])
        c = {"id": f"c{n}", "text": f["text"]}
        if line:
            c["ref"] = f"{f['path']}:{line}"
            c["status"] = "read"
        out.append(c)
    for t in spec.get("inferred") or []:
        n += 1
        out.append({"id": f"c{n}", "text": t, "inferred": True, "status": "inferred"})
    return out


def timed_script(lines: list) -> list:
    t, out = 0.2, []
    for ln in lines:
        dur = max(1.6, round(SECONDS_PER_WORD * words(ln["text"]), 2))
        out.append({"name": ln["name"], "text": ln["text"], "start": round(t, 3), "end": round(t + dur, 3),
                    "subjectId": "main-flow"})
        t += dur + LINE_GAP_S
    return out


def wrap(s: str, n: int = 84) -> list:
    out, cur = [], ""
    for w_ in s.split():
        if cur and len(cur) + 1 + len(w_) > n:
            out.append(cur)
            cur = w_
        else:
            cur = f"{cur} {w_}".strip()
    if cur:
        out.append(cur)
    return out[:2]


# ============================================================================ the scenes
def fit_layout(layouts: list) -> tuple:
    """Pick the layout (LR or TB) that is drawn largest inside the diagram band; never upscale."""
    best = None
    for name, lay in layouts:
        bw, bh = lay["bbox"]["w"], lay["bbox"]["h"]
        s = min(1.0, (W - 2 * SAFE_X) / max(1, bw), 360.0 / max(1, bh))
        if best is None or s > best[2]:
            best = (name, lay, s)
    return best


def scene_specs(spec: dict, layout: dict, scale: float, kit: dict) -> list:
    """Two scenes as plain data. Scene 1 builds the main flow step by step with a token moving
    along the connectors; scene 2 states the fixture's outcome lines as chips with a marker."""
    col = kit.get("colors") or {}
    node_k = kit.get("node") or {}
    ink, paper = col.get("ink", "#111111"), col.get("paper", "#ffffff")
    accent, muted = col.get("accent", "#0a6"), col.get("muted", "#555555")
    bw, bh = layout["bbox"]["w"] * scale, layout["bbox"]["h"] * scale
    x0 = SAFE_X + ((W - 2 * SAFE_X) - bw) / 2.0
    y0 = 200 + (360 - bh) / 2.0

    def P(p):
        return {"x": round(x0 + p["x"] * scale, 1), "y": round(y0 + p["y"] * scale, 1)}

    nodes = [{"id": n["id"], "label": n["label"], "x": round(x0 + n["x"] * scale, 1),
              "y": round(y0 + n["y"] * scale, 1), "w": round(n["w"] * scale, 1), "h": round(n["h"] * scale, 1)}
             for n in layout["nodes"]]
    edges = [{"from": e["from"], "to": e["to"], "points": [P(p) for p in e["points"]]} for e in layout["edges"]]
    items = {it["id"]: it for it in spec["flow"]["items"]}
    sc = spec["scenes"]
    s1_lines, s2_lines = timed_script(sc[0]["lines"]), timed_script(sc[1]["lines"])
    show, first_line = {}, {}
    for raw, ln in zip(sc[0]["lines"], s1_lines):
        for nid in raw.get("show") or []:
            first_line.setdefault(nid, ln["name"])
        show[ln["name"]] = raw.get("show") or []
    for n in nodes:
        n["beat"] = first_line.get(n["id"], s1_lines[0]["name"])
    s1 = {"id": sc[0]["id"], "title": sc[0]["title"], "script": s1_lines, "nodes": nodes, "edges": edges,
          "desc": [{"beat": ln["name"], "until": (s1_lines[i + 1]["name"] if i + 1 < len(s1_lines) else None),
                    "lines": wrap(" ".join(items[n]["desc"] for n in show[ln["name"]][:1] if n in items)
                                  or ln["text"])} for i, ln in enumerate(s1_lines)],
          "token": [{"beat": ln["name"], "path": [p for e in edges if e["to"] in show[ln["name"]]
                                                  for p in e["points"]] or
                     [{"x": nodes[0]["x"], "y": nodes[0]["y"] + nodes[0]["h"] + 24},
                      {"x": nodes[0]["x"] + nodes[0]["w"], "y": nodes[0]["y"] + nodes[0]["h"] + 24}]}
                    for ln in s1_lines]}
    chips, cx = [], SAFE_X + 40
    for raw, ln in zip(sc[1]["lines"], s2_lines):
        for c in raw.get("chips") or []:
            wch = round(len(c) * 30 * 0.56 + 40, 1)
            if cx + wch > W - SAFE_X - 40:
                cx = SAFE_X + 40
            chips.append({"text": c, "beat": ln["name"], "x": round(cx, 1), "y": 330.0})
            cx += wch + 32
    s2 = {"id": sc[1]["id"], "title": sc[1]["title"], "script": s2_lines, "chips": chips,
          "cards": [{"id": f"say-{i}", "beat": ln["name"],
                     "until": (s2_lines[i + 1]["name"] if i + 1 < len(s2_lines) else None),
                     "lines": wrap(ln["text"])} for i, ln in enumerate(s2_lines)]}
    style = {"ink": ink, "paper": paper, "accent": accent, "muted": muted,
             "node_fill": node_k.get("fill") or "rgba(0,0,0,0.06)",
             "card_fill": (kit.get("card") or {}).get("fill") or "rgba(0,0,0,0.05)"}
    s1["style"] = s2["style"] = style
    return [s1, s2]


SCENE_JS = r"""
(function () {
  const SPEC = __SPEC__;
  const st = SPEC.style;
  function lerpPath(pts, f) {
    if (!pts.length) return {x: 0, y: 0};
    if (pts.length === 1) return pts[0];
    let total = 0; const seg = [];
    for (let i = 1; i < pts.length; i++) { const d = Math.hypot(pts[i].x - pts[i-1].x, pts[i].y - pts[i-1].y); seg.push(d); total += d; }
    let goal = Math.max(0, Math.min(1, f)) * total;
    for (let i = 1; i < pts.length; i++) {
      if (goal <= seg[i-1] || i === pts.length - 1) { const k = seg[i-1] ? Math.min(1, goal / seg[i-1]) : 0;
        return {x: pts[i-1].x + (pts[i].x - pts[i-1].x) * k, y: pts[i-1].y + (pts[i].y - pts[i-1].y) * k}; }
      goal -= seg[i-1];
    }
    return pts[pts.length - 1];
  }
  function clamp01(x) { return x < 0 ? 0 : (x > 1 ? 1 : x); }
  // A text card that hands the screen to the next one: it stays until the next line starts,
  // leaves in 0.1 s, and the next card enters in the following 0.15 s, so two cards are never
  // legible at the same time. The first card of a scene enters with its line.
  function handOff(s, beat, until, first) {
    const ln = s.beat(beat);
    const t0 = first ? ln.start - 0.1 : ln.start + 0.1;
    let a = clamp01((s.t - t0) / 0.15);
    if (until) { const nx = s.beat(until); a = Math.min(a, clamp01(1 - (s.t - nx.start) / 0.1)); }
    return a;
  }
  function draw(z) {
    z.text({text: SPEC.title, x: 96, y: 70, size: 46, weight: 700, alpha: 1, color: st.ink});
    if (SPEC.nodes) {
      z.section('flow', (s) => {
        for (const n of SPEC.nodes) s.node({id: n.id, x: n.x, y: n.y, w: n.w, h: n.h, text: n.label,
          alpha: s.beatAlpha(n.beat), fill: st.node_fill, color: st.ink});
        for (const e of SPEC.edges) {
          const nb = SPEC.nodes.find(n => n.id === e.to);
          s.connector({from: e.from, to: e.to, points: e.points, alpha: s.beatAlpha(nb ? nb.beat : SPEC.script[0].name),
            color: st.muted});
        }
        for (const tk of SPEC.token) {
          const ln = s.beat(tk.beat);
          const f = (s.t - ln.start) / Math.max(0.1, ln.end - ln.start);
          const a = (s.t >= ln.start - 0.05 && s.t <= ln.end) ? 1 : 0;
          const p = lerpPath(tk.path, f);
          s.card({id: 'token-' + tk.beat, x: Math.round(p.x - 12), y: Math.round(p.y - 12), w: 24, h: 24,
                  alpha: a, fill: st.accent, radius: 12});
        }
      });
      z.section('step-detail', (s) => {
        for (const d of SPEC.desc) {
          const a = handOff(s, d.beat, d.until, d === SPEC.desc[0]);
          s.card({id: 'desc-' + d.beat, x: 96, y: 600, w: 1728, h: 240, alpha: a, pad: 36, fill: st.card_fill}, (k) => {
            d.lines.forEach((tx, i) => k.text({text: tx, size: 34, y: 640 + i * 56, color: st.ink}));
          });
        }
      });
    } else {
      z.section('outcomes', (s) => {
        for (const c of SPEC.chips) s.chip({text: c.text, x: c.x, y: c.y, size: 30, alpha: s.beatAlpha(c.beat),
                                            fill: st.card_fill, color: st.ink});
        const last = SPEC.script[SPEC.script.length - 1];
        const f = Math.max(0, Math.min(1, (s.t - SPEC.script[0].start) / Math.max(0.1, last.end - SPEC.script[0].start)));
        s.card({id: 'progress-track', x: 96, y: 470, w: 1728, h: 12, alpha: 1, fill: st.card_fill});
        s.card({id: 'progress-marker', x: Math.round(96 + f * 1700), y: 430, w: 28, h: 28, alpha: 1, fill: st.accent, radius: 14});
      });
      z.section('say', (s) => {
        for (const c of SPEC.cards) {
          const a = handOff(s, c.beat, c.until, c === SPEC.cards[0]);
          s.card({id: c.id, x: 96, y: 600, w: 1728, h: 240, alpha: a, pad: 36, fill: st.card_fill}, (k) => {
            c.lines.forEach((tx, i) => k.text({text: tx, size: 34, y: 640 + i * 56, color: st.ink}));
          });
        }
      });
    }
  }
  const api = {SCRIPT: SPEC.script, draw: draw, name: SPEC.id};
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (typeof window !== 'undefined') window.__C8_BENCH_SCENE = api;
})();
"""

SCENE_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>scene</title>
<style>html,body{margin:0;padding:0;background:__PAPER__;overflow:hidden}canvas{display:block}</style></head>
<body><canvas id="c" width="1920" height="1080"></canvas>
<script>__KIT__</script>
<script>__RUNTIME__</script>
<script>__SCENE__</script>
<script>
(function(){
  const C = window.Coordn8; const S = window.__C8_BENCH_SCENE;
  const ctx = document.getElementById('c').getContext('2d');
  const scene = new C.Scene({name: S.name, script: new C.Script(S.SCRIPT), ctx: ctx, width: 1920, height: 1080});
  window.renderAt = function (t) { scene.begin(t); S.draw(scene); scene.end(); };
  window.renderAt(0); document.title = 'READY';
})();
</script></body></html>
"""


def write_scene(work: Path, spec: dict, runtime: Path, kit_js: str = "") -> tuple:
    js = SCENE_JS.replace("__SPEC__", json.dumps(spec, ensure_ascii=False, sort_keys=True))
    jsp = work / f"{spec['id']}.scene.js"
    jsp.write_text(js, encoding="utf-8")
    html = (SCENE_HTML.replace("__PAPER__", spec["style"]["paper"]).replace("__KIT__", kit_js)
            .replace("__RUNTIME__", runtime.read_text(encoding="utf-8")).replace("__SCENE__", js))
    hp = work / f"{spec['id']}.scene.html"
    hp.write_text(html, encoding="utf-8")
    return jsp, hp


TRACE_JS = ("const fs=require('fs');const C=require(process.argv[1]);const S=require(process.argv[2]);"
            "const tr=C.buildTrace({name:S.name,script:S.SCRIPT,grid:5,draw:S.draw});"
            "fs.writeFileSync(process.argv[3],JSON.stringify(tr));")


def build_trace(sb: Sandbox, runtime: Path, scene_js: Path, out: Path) -> tuple:
    cp = sb.run(["node", "-e", TRACE_JS, runtime, scene_js, out], timeout=120)
    return cp.returncode == 0 and out.exists(), (cp.stderr or "")[-300:]


def scene_elements(specs: list) -> dict:
    """The painted text of the scenes, for the palette gate's text-contrast and off-palette checks."""
    frames = []
    for s in specs:
        st = s["style"]
        els = [{"id": "title", "kind": "text", "color": st["ink"], "bg": st["paper"], "font_px": 46, "bold": True,
                "text": s["title"]}]
        for n in s.get("nodes") or []:
            els.append({"id": n["id"], "kind": "text", "color": st["ink"], "bg": st["node_fill"], "font_px": 30,
                        "text": n["label"]})
        for c in s.get("chips") or []:
            els.append({"id": c["text"], "kind": "text", "color": st["ink"], "bg": st["card_fill"], "font_px": 30,
                        "text": c["text"]})
        for d in (s.get("desc") or []) + (s.get("cards") or []):
            for i, tx in enumerate(d["lines"]):
                els.append({"id": f"{d.get('beat')}-{i}", "kind": "text", "color": st["ink"], "bg": st["card_fill"],
                            "font_px": 34, "text": tx})
        frames.append({"id": s["id"], "elements": els})
    return {"frames": frames}


# ============================================================================ the page
def page_content(spec: dict, claims: list, svg_rel: str) -> dict:
    f = spec["flow"]
    items = f["items"]
    rows = []
    for it, c in zip(items, claims):
        cell = {"text": c["ref"], "ref": c["ref"]} if c.get("ref") else "not traced"
        rows.append([it["label"], it["desc"], cell])
    return {
        "title": f"How {spec['project']} works",
        "conclusion": spec["conclusion"],
        "glossary": spec.get("glossary") or [],
        "snapshot": "bench-fixture",
        "sections": [
            {"id": "overview", "title": "What it is", "summary": f"{spec['project']}: {spec['subject']}.",
             "what": spec["conclusion"], "why": spec["why"]},
            {"id": "main-flow", "title": f["title"], "summary": f["text"],
             "what": " ".join(it["desc"] for it in items[:2]),
             "components": [
                 {"type": "flow", "id": f["id"], "title": f["title"], "svg_file": svg_rel,
                  "caption": "Each box is one step. The arrows show the order of the steps."},
                 {"type": "stepper", "title": "Walk through the steps",
                  "steps": [{"title": it["label"], "body": it["desc"], "highlight": [it["id"]]} for it in items]}]},
            {"id": "steps", "title": "Where each step lives", "summary": "Each step and the line that implements it.",
             "components": [{"type": "outcomes", "caption": "Steps and their source lines",
                             "columns": ["Step", "What it does", "Source"], "rows": rows}]},
        ],
        "claims": [{"id": c["id"], "claim": c["text"], **({"ref": c["ref"]} if c.get("ref") else {}),
                    "status": "inferred" if c.get("inferred") else ("read" if c.get("ref") else "")}
                   for c in claims],
    }


# ============================================================================ the passes
def prepare_v2(sb: Sandbox, spec: dict, work: Path, tree: Path) -> dict:
    """The current toolchain pass. Returns the artefacts and the per-step outputs it measured."""
    out: dict = {"steps": {}}
    work.mkdir(parents=True, exist_ok=True)
    build = work / "build"
    build.mkdir(exist_ok=True)
    (work / "brief.json").write_text(json.dumps({"brief": spec["_brief"], "subject": spec["subject"]}), encoding="utf-8")
    cp = sb.py(tree, "scripts/design/audience.py", work / "brief.json", "--json")
    try:
        aud = json.loads(cp.stdout)
    except ValueError:
        aud = last_json(cp.stdout) or {}
    out["audience"] = aud
    role = (aud or {}).get("role_label") or (aud or {}).get("role") or "engineer"
    pbrief = {"subject": spec["subject"], "audience": role, "tone": spec.get("tone", ""), "project": spec["project"]}
    (work / "palette_brief.json").write_text(json.dumps(pbrief), encoding="utf-8")
    cp = sb.py(tree, "scripts/design/palette.py", work / "palette_brief.json", "--build", build, "--seed", "7",
               "--no-history", "--json")
    out["steps"]["palette"] = cp.returncode
    pal = json.loads(cp.stdout) if cp.returncode == 0 else {}
    out["palette"] = {"family": pal.get("family"), "rejected": len(pal.get("rejected") or [])}
    cp = sb.py(tree, "scripts/design/pick_kit.py", work / "palette_brief.json", "--build", build,
               "--palette", build / "design_system.json")
    out["steps"]["kit"] = cp.returncode
    ds = build / "design_system.json"
    out["kit"] = (json.loads(ds.read_text(encoding="utf-8")).get("kit") if ds.exists() else None)
    cp = sb.py(tree, "scripts/design/kit_export.py", "runtime", ds, "--out", build / "kit_runtime.json")
    out["steps"]["kit_export"] = cp.returncode
    kit = json.loads((build / "kit_runtime.json").read_text(encoding="utf-8")) if cp.returncode == 0 else {}
    cp = sb.py(tree, "scripts/design/kit_export.py", "runtime", ds, "--js")
    kit_js = cp.stdout if cp.returncode == 0 else ""
    f = spec["flow"]
    desc = {"id": f["id"], "text": f["text"],
            "items": [{k: v for k, v in it.items() if k in ("id", "label", "kind")} for it in f["items"]],
            "relations": f["relations"], "audience": "mixed", "detail": "medium"}
    (work / "figure_desc.json").write_text(json.dumps(desc), encoding="utf-8")
    cp = sb.py(tree, "scripts/design/pick_diagram.py", work / "figure_desc.json", "--build", build, "--json")
    out["steps"]["pick_diagram"] = cp.returncode
    lays = []
    for rd in ("LR", "TB"):
        lj = build / f"layout_{rd}.json"
        cp = sb.run(["node", tree / "scripts/render/layout_cli.js", build / "diagram_plan.json", "--figure", f["id"],
                     "--rankdir", rd, "--kit", build / "kit_runtime.json", "--json", lj,
                     "--svg", build / f"{f['id']}_{rd}.svg"], timeout=120)
        out["steps"][f"layout_{rd}"] = cp.returncode
        if cp.returncode == 0 and lj.exists():
            lays.append((rd, json.loads(lj.read_text(encoding="utf-8"))))
    if not lays:
        raise RuntimeError("layout_cli produced no layout")
    rd, lay, scale = fit_layout(lays)
    out["layout"] = {"rankdir": rd, "scale": round(scale, 3), "nodes": len(lay["nodes"]),
                     "edges": len(lay["edges"]), "warnings": len(lay.get("warnings") or []),
                     "rerouted": len(lay.get("rerouted") or [])}
    shutil.copy(build / f"{f['id']}_{rd}.svg", build / f"{f['id']}.svg")
    claims = claims_for(spec)
    (build / "claims_manifest.json").write_text(json.dumps({"repo_root": str(spec["_dir"]), "claims": claims},
                                                           indent=1), encoding="utf-8")
    content = page_content(spec, claims, f"build/{f['id']}.svg")
    (work / "content.json").write_text(json.dumps(content, indent=1, ensure_ascii=False), encoding="utf-8")
    specs = scene_specs(spec, lay, scale, kit)
    out.update({"build": build, "content": work / "content.json", "claims": claims, "specs": specs,
                "kit_js": kit_js, "kit_rt": kit, "design_system": ds})
    narr = {"scenes": [{"id": s["id"], "lines": [{"text": ln["text"]} for ln in s["script"]]} for s in specs]}
    (work / "narration.json").write_text(json.dumps(narr, indent=1), encoding="utf-8")
    (build / "figures_manifest.json").write_text(json.dumps({"figures": [f["id"]]}), encoding="utf-8")
    return out


def score_quality(sb: Sandbox, tree: Path, traces: list, work: Path) -> dict:
    cp = sb.py(tree, "scripts/quality.py", *sum((["--trace", t] for t in traces), []), "--json",
               "--out", work / "quality.json", timeout=300)
    q = json.loads((work / "quality.json").read_text(encoding="utf-8")) if (work / "quality.json").exists() else None
    if not q:
        return err(f"quality.py exit {cp.returncode}: {(cp.stderr or '')[-200:]}")
    return tag({"build_score": q.get("build_score"), "dimensions": q.get("dimension_averages"),
                "findings": len(q.get("findings") or []), "weakest": q.get("weakest_dimension")})


def render_scenes(sb: Sandbox, tree: Path, htmls: list, durations: list, work: Path) -> dict:
    """Capture each scene to frames under the disk guard, encode (the encoder reclaims the frames)."""
    total_frames, cap_s, peak, ok = 0, 0.0, 0, True
    notes = []
    for hp, dur in zip(htmls, durations):
        rs = max(SCENE_RENDER_MIN_S, min(SCENE_RENDER_MAX_S, dur))
        fdir = work / "frames" / hp.stem.replace(".scene", "")
        t0 = time.monotonic()
        cp = sb.py(tree, "scripts/render/capture_scene.py", "capture", "--scene", hp, "--duration", f"{rs:.2f}",
                   "--fps", FPS, "--out", fdir, "--format", "jpg", "--max-bytes-gb", "2", timeout=600)
        dt = time.monotonic() - t0
        tel = sorted((fdir.parent / "_telemetry").glob(f"capture-{hp.stem}*.summary.json"))
        summ = json.loads(tel[-1].read_text(encoding="utf-8")) if tel else {}
        n = sum(1 for p in fdir.glob("frame_*") if p.is_file()) if fdir.exists() else 0
        on_disk = int(summ.get("bytes_on_disk") or 0)
        wpeak = int(((summ.get("resources") or {}).get("watched_peak")) or 0)
        peak = max(peak, on_disk, wpeak)
        if cp.returncode != 0 or n == 0:
            ok = False
            notes.append(f"{hp.stem}: capture exit {cp.returncode}: {(cp.stderr or '')[-160:]}")
            shutil.rmtree(fdir, ignore_errors=True)
            continue
        total_frames += n
        cap_s += dt
        cp = sb.py(tree, "scripts/render/capture_scene.py", "encode", "--frames", fdir, "--fps", FPS,
                   "--out", work / f"{hp.stem}.mp4", timeout=600)
        if cp.returncode != 0:
            ok = False
            notes.append(f"{hp.stem}: encode exit {cp.returncode}")
        shutil.rmtree(fdir, ignore_errors=True)   # belt and braces: the encoder already reclaimed them
    left = frames_on_disk(work)
    res = {"frames": total_frames, "render_seconds": round(total_frames / FPS, 2),
           "fps": round(total_frames / cap_s, 2) if cap_s > 0 else None,
           "peak_disk_bytes": peak, "frames_left": left, "ok": ok}
    return {"fps": tag(res["fps"]) if res["fps"] else err("; ".join(notes) or "no frames captured"),
            "peak_disk": tag(peak) if peak else err("; ".join(notes) or "no disk measurement"),
            "frames_left": tag(left), "render": tag(res, note="; ".join(notes))}


def run_v2(sb: Sandbox, spec: dict, work: Path, render: str) -> dict:
    t0 = time.monotonic()
    r: dict = {}
    tree = PKG
    try:
        prep = prepare_v2(sb, spec, work, tree)
    except Exception as e:  # noqa: BLE001
        msg = f"toolchain pass failed: {type(e).__name__}: {e}"
        r = {k: err(msg) for k in score.FIELDS}
        r["frames_left"] = tag(frames_on_disk(work))
        r["wall_s"] = tag(round(time.monotonic() - t0, 2))
        return r
    build = prep["build"]
    aud = prep["audience"]
    (work / "scene_specs.json").write_text(json.dumps(prep["specs"]), encoding="utf-8")
    r["audience"] = tag({"role": aud.get("role"), "level": aud.get("level"),
                         "confidence": aud.get("confidence")})
    # scenes -> traces -> quality
    runtime = tree / "scripts/render/scene_runtime.js"
    traces, htmls, durs = [], [], []
    for s in prep["specs"]:
        jsp, hp = write_scene(work, s, runtime, prep["kit_js"])
        tp = work / f"trace_{s['id']}.json"
        ok, why = build_trace(sb, runtime, jsp, tp)
        if ok:
            traces.append(tp)
        else:
            r.setdefault("_notes", []).append(f"trace {s['id']}: {why}")
        htmls.append(hp)
        durs.append(s["script"][-1]["end"] + 0.3)
    r["quality"] = score_quality(sb, tree, traces, work) if traces else err("no trace was built")
    # palette gate: design system + the page + the painted scene text
    (work / "scene_elements.json").write_text(json.dumps(scene_elements(prep["specs"])), encoding="utf-8")
    page = work / "page.html"
    cp = sb.py(tree, "scripts/render/build_page.py", prep["content"], "--design-system", prep["design_system"],
               "--out", page, "--plan-out", build / "page_plan.json", "--claims-out", build / "page_claims.json")
    bp = last_json(cp.stdout) or {}
    built = cp.returncode == 0 and page.exists()
    cp = sb.py(tree, "scripts/gates/check_palette.py", prep["design_system"], "--elements",
               work / "scene_elements.json")
    pg = last_json(cp.stdout) or {}
    pp = {}
    if built:
        cp2 = sb.py(tree, "scripts/gates/check_palette.py", prep["design_system"], "--html", page)
        pp = last_json(cp2.stdout) or {}
    r["palette"] = tag({"status": pg.get("status"), "family": prep["palette"]["family"],
                        "kit": prep["kit"], "findings": len(pg.get("findings") or []),
                        "min_text_contrast": ((pg.get("measured") or {}).get("light") or {}).get("min_text_contrast"),
                        "candidates_rejected": prep["palette"]["rejected"],
                        "page_status": pp.get("status"), "page_counts": pp.get("counts") or {}},
                       note=("palette gate on the built page: " + ", ".join(
                           f"{k} {v}" for k, v in sorted((pp.get("counts") or {}).items()))
                             if pp.get("counts") else "")) if pg else err(cp.stderr[-200:])
    # diagram plan quality
    plan = json.loads((build / "diagram_plan.json").read_text(encoding="utf-8"))
    figs = plan.get("figures") or []
    with_rej = sum(1 for fg in figs if len([x for x in fg.get("rejected") or [] if x.get("why")]) >= 2)
    cp1 = sb.py(tree, "scripts/gates/check_diagram_plan.py", build / "diagram_plan.json", build / "figures_manifest.json")
    cp2 = sb.py(tree, "scripts/gates/check_diagram_budget.py", build / "diagram_plan.json")
    g1, g2 = last_json(cp1.stdout) or {}, last_json(cp2.stdout) or {}
    r["diagram"] = tag({"figures": len(figs), "with_2_rejections": with_rej,
                        "type": figs[0].get("type") if figs else None,
                        "plan_gate": g1.get("status"), "budget_gate": g2.get("status"),
                        "layout": prep["layout"]})
    # language
    cp = sb.py(tree, "scripts/gates/check_language.py", prep["content"], work / "narration.json")
    lg = last_json(cp.stdout) or {}
    n_s = int(lg.get("sentences_checked") or 0)
    bad = {(f_.get("where"), f_.get("sentence")) for f_ in lg.get("findings") or []}
    r["language"] = (tag({"pass_rate": round(100.0 * (n_s - len(bad)) / n_s, 1) if n_s else None,
                          "sentences": n_s, "sentences_with_findings": len(bad),
                          "notes": len(lg.get("notes") or []), "status": lg.get("status")})
                     if n_s else err(f"language gate: {lg.get('summary') or cp.stderr[-200:]}"))
    # claims
    r["claims"] = claims_score(sb, tree, build / "claims_manifest.json", spec["_dir"],
                               page if built else None, build / "page_claims.json")
    # page accessibility
    if not built:
        r["a11y"] = err(f"page build failed: {(cp.stderr or '')[-200:]}")
    else:
        cpa = sb.py(tree, "scripts/gates/check_page_a11y.py", page, "--json", work / "a11y.json", timeout=300)
        a = json.loads((work / "a11y.json").read_text(encoding="utf-8")) if (work / "a11y.json").exists() else {}
        if not a:
            a = last_json(cpa.stdout) or {}
        cp = sb.py(tree, "scripts/gates/dom_smoke.py", page, "--expect", "data-c8-flow:1", "data-c8-stepper:1",
                   "c8-claims:1", "<section:3")
        dm = last_json(cp.stdout) or {}
        checks = a.get("checks") or {}
        if a.get("live_checks_ran"):
            r["a11y"] = tag({"passed": sum(1 for v in checks.values() if v.get("ok")), "checks": len(checks),
                             "failed": a.get("failed") or [], "dom_smoke": dm.get("status"),
                             "page_bytes": page.stat().st_size, "page_warnings": len(bp.get("warnings") or [])})
        elif a.get("status") == "SKIPPED":
            r["a11y"] = skipped(f"live checks did not run: {a.get('note') or a.get('summary') or ''}"[:200])
        else:
            r["a11y"] = err(f"page a11y gate exit {cpa.returncode}: {a.get('summary') or (cpa.stderr or '')[-200:]}")
    # render
    if render == "short":
        r.update(render_scenes(sb, tree, htmls, durs, work))
    else:
        for k in ("fps", "peak_disk"):
            r[k] = skipped("render not requested (--render none)")
        r["frames_left"] = tag(frames_on_disk(work))
    r["wall_s"] = tag(round(time.monotonic() - t0, 2))
    r["_traces"] = [str(t) for t in traces]
    return r


def claims_score(sb: Sandbox, tree: Path, manifest: Path, repo: Path, page, page_claims: Path) -> dict:
    cp = sb.py(tree, "scripts/gates/facts_trace.py", manifest, "--repo", repo)
    ft = last_json(cp.stdout) or {}
    total = int(ft.get("claims_checked") or 0)
    if not total:
        return err(f"facts_trace: {ft.get('summary') or cp.stderr[-200:]}")
    if "inferred" in ft:      # the current gate counts read + inferred
        covered = int(ft.get("read") or 0) + int(ft.get("inferred") or 0)
        inferred_known = True
    else:                     # the 1.5.0 gate has no inferred mark: anything unresolved is unresolved
        covered = total - int(ft.get("unresolved") or 0)
        inferred_known = False
    v = {"coverage_pct": round(100.0 * covered / total, 1), "claims": total, "covered": covered,
         "inferred_mark_understood": inferred_known, "status": ft.get("status")}
    if page is not None and (tree / "scripts/gates/check_claims.py").exists():
        cp = sb.py(tree, "scripts/gates/check_claims.py", "verify", page, page_claims, "--repo", repo)
        v["page_ledger"] = (last_json(cp.stdout) or {}).get("status")
    return tag(v)


def run_v15(sb: Sandbox, spec: dict, work: Path, render: str, tree, v2_prep_dir: Path, v2_res: dict) -> dict:
    """The 1.5.0 tree's own tools on the SAME inputs; absent capabilities are not-available."""
    t0 = time.monotonic()
    r: dict = {}
    if tree is None:
        return {k: skipped("1.5.0 baseline not available here") for k in score.FIELDS}
    work.mkdir(parents=True, exist_ok=True)
    r["audience"] = na("1.5.0 has no audience inference")
    r["palette"] = na("1.5.0 has no palette director, style kit or palette gate")
    r["diagram"] = na("1.5.0 has no diagram planner, budget gate or layout engine")
    r["language"] = na("1.5.0 has no plain-language gate")
    r["a11y"] = na("1.5.0 has no page builder or page accessibility gate (the page was hand-written)")
    q_note = ("1.5.0 cannot compose a scene from a fixture (no kit, planner or layout); "
              "its quality scorer is unchanged in 2.0")
    parity = None
    if v2_res.get("_traces") and (tree / "scripts/quality.py").exists():
        qs = score_quality(sb, tree, [Path(t) for t in v2_res["_traces"]], work)
        if qs.get("tag") == "measured":
            mine = ((v2_res.get("quality") or {}).get("value") or {}).get("build_score")
            parity = qs["value"]["build_score"] == mine
            q_note += f"; scorer parity on the same trace: {'identical' if parity else 'DIFFERENT'}"
    r["quality"] = na(q_note)
    manifest = v2_prep_dir / "build" / "claims_manifest.json"
    r["claims"] = (claims_score(sb, tree, manifest, spec["_dir"], None, Path("-")) if manifest.exists()
                   else skipped("no claims manifest prepared"))
    if render == "short":
        runtime = tree / "scripts/render/scene_runtime.js"
        htmls, durs = [], []
        sp = v2_prep_dir / "scene_specs.json"
        if not sp.exists():
            r["fps"] = r["peak_disk"] = skipped("no scene input was prepared")
            r["frames_left"] = tag(frames_on_disk(work))
            r["wall_s"] = tag(round(time.monotonic() - t0, 2))
            return r
        specs = json.loads(sp.read_text(encoding="utf-8"))
        for s in specs:
            _jsp, hp = write_scene(work, s, runtime, "")
            htmls.append(hp)
            durs.append(s["script"][-1]["end"] + 0.3)
        r.update(render_scenes(sb, tree, htmls, durs, work))
    else:
        for k in ("fps", "peak_disk"):
            r[k] = skipped("render not requested (--render none)")
        r["frames_left"] = tag(frames_on_disk(work))
    r["wall_s"] = tag(round(time.monotonic() - t0, 2))
    return r


# ============================================================================ coverage + head-to-head
def coverage_summary() -> dict:
    p = PKG / "tests" / "coverage_matrix.json"
    try:
        rows = json.loads(p.read_text(encoding="utf-8")).get("rows") or []
    except (OSError, ValueError):
        return {"available": False}
    by_group: dict = {}
    for row in rows:
        g = row.get("group") or "other"
        v = str(row.get("verdict") or "missing")
        by_group.setdefault(g, {}).setdefault(v, 0)
        by_group[g][v] += 1
    totals: dict = {}
    for row in rows:
        v = str(row.get("verdict") or "missing")
        totals[v] = totals.get(v, 0) + 1
    return {"available": True, "rows": len(rows), "totals": totals, "by_group": by_group}


# What each reference is, described by capability only. The letter-to-source mapping is kept
# outside the package.
REFERENCES = [
    {"ref": "A", "kind": "diagram-drawing agent skill (one HTML + SVG file per diagram)",
     "runs_on_fixture": False,
     "why": "its diagrams are authored by a model following written instructions; nothing generates "
            "a diagram from a repository without a model session, and this bench runs none",
     "crosscheck": "its rendered-page linter (clipping, overflow, runtime errors) is run locally on the "
                   "2.0 page for fixture 01, with network blocked and a temporary HOME"},
    {"ref": "B", "kind": "explanation-writing agent skill",
     "runs_on_fixture": False, "why": "instructions and documents only; there is no executable part"},
    {"ref": "C", "kind": "narrated explainer-video project built on a component video renderer",
     "runs_on_fixture": False,
     "why": "each episode is hand-authored code for one topic, not generated from a repository; its "
            "renderer dependencies are not installed here; voice-over episodes need a voice recording "
            "and music-only episodes need a licensed music track, and neither is shipped"},
    {"ref": "D", "kind": "simple-explanation agent skill",
     "runs_on_fixture": False, "why": "instructions only; there is no executable part"},
    {"ref": "E", "kind": "plain-explanation agent skill with an eval runner",
     "runs_on_fixture": False,
     "why": "the eval runner drives the agent command-line tool (a live model session); the bench "
            "runs no model sessions"},
    {"ref": "F", "kind": "creative-ideation agent skill",
     "runs_on_fixture": False,
     "why": "instructions, reference notes and eval prompts; there is no executable generator"},
]


def crosscheck_ref_a(sb: Sandbox, page) -> dict:
    src = REFS_DIR / "diagram" / "scripts" / "lint-render.py"
    if page is None or not Path(page).exists():
        return {"ran": False, "why": "no 2.0 page was built for fixture 01 in this run"}
    if not src.exists():
        return {"ran": False, "why": "no local copy of reference A was found"}
    copy_dir = sb.root / "refA"
    copy_dir.mkdir(exist_ok=True)
    shutil.copy(src, copy_dir / "lint.py")
    target = copy_dir / "page.html"
    shutil.copy(page, target)
    cp = sb.run([sys.executable, "-I", copy_dir / "lint.py", target], timeout=300, cwd=copy_dir)
    tail = (cp.stdout or "").strip().splitlines()[-1:] or (cp.stderr or "").strip().splitlines()[-1:]
    return {"ran": True, "exit": cp.returncode,
            "verdict": "no findings" if cp.returncode == 0 else ("findings" if cp.returncode == 1 else "did not run"),
            "last_line": re.sub(r"/\S+", "<path>", tail[0])[:160] if tail else ""}


# ============================================================================ main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Coordn8 benchmark: current package vs 1.5.0 on six fixtures")
    ap.add_argument("--fixture", default="all", help="1-6, a comma list, or all")
    ap.add_argument("--version", default="both", choices=("v2", "v1.5", "both"))
    ap.add_argument("--render", default="short", choices=("short", "none"))
    ap.add_argument("--report", default=str(BENCH / "report.md"))
    ap.add_argument("--results-out", default=None, help="also write the raw results JSON here")
    ap.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    ap.add_argument("--no-crosscheck", action="store_true")
    ap.add_argument("--keep", action="store_true", help="keep the work directory (debugging)")
    a = ap.parse_args(argv)

    allf = fixture_dirs()
    if a.fixture == "all":
        chosen = allf
    else:
        want = {int(x) for x in a.fixture.split(",") if x.strip()}
        chosen = [d for d in allf if int(d.name[:2]) in want]
    if not chosen:
        sys.stderr.write("[bench] no fixture selected\n")
        return 3
    t_start = time.monotonic()
    sb = Sandbox(keep=a.keep)
    results = {"schema": 1, "fps": FPS, "render": a.render, "version": a.version,
               "package_version": (PKG / "VERSION").read_text(encoding="utf-8").strip(),
               "fixtures": [], "coverage": coverage_summary(), "references": REFERENCES}
    try:
        base_tree, base_ver = (None, "not requested")
        if a.version in ("v1.5", "both"):
            base_tree, base_ver = extract_baseline(sb, Path(a.baseline))
        results["baseline_version"] = base_ver
        page_for_a = None
        for d in chosen:
            spec = load_fixture(d)
            st = fixture_stats(d)
            row = {"fixture": d.name, "project": spec["project"], "brief": spec["_brief_path"].name,
                   "files": st["files"], "bytes": st["bytes"]}
            sys.stderr.write(f"[bench] {d.name}: ")
            w2 = sb.root / d.name / "v2"
            v2 = run_v2(sb, spec, w2, a.render)
            if a.version in ("v2", "both"):
                row["v2"] = {k: v for k, v in v2.items() if not k.startswith("_")}
                sys.stderr.write("2.0 done ")
            if a.version in ("v1.5", "both"):
                row["v1.5"] = run_v15(sb, spec, sb.root / d.name / "v15", a.render, base_tree, w2, v2)
                sys.stderr.write("1.5 done")
            sys.stderr.write("\n")
            if d.name.startswith("01-") and (w2 / "page.html").exists():
                page_for_a = w2 / "page.html"
            results["fixtures"].append(row)
        if not a.no_crosscheck:
            results["crosscheck_a"] = crosscheck_ref_a(sb, page_for_a)
        results["frames_left_total"] = frames_on_disk(sb.root)
    finally:
        work = sb.root
        sb.cleanup()
    results["work_dir_removed"] = not work.exists()
    results["wall_s_total"] = round(time.monotonic() - t_start, 2)
    report = score.render_report(results)
    Path(a.report).write_text(report, encoding="utf-8")
    if a.results_out:
        Path(a.results_out).write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    sys.stderr.write(score.totals_line(results) + "\n")
    sys.stderr.write(f"[bench] wrote {a.report} in {results['wall_s_total']} s; "
                     f"frames left {results.get('frames_left_total')}; work dir removed {results['work_dir_removed']}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
