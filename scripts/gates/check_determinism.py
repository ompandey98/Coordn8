#!/usr/bin/env python3
"""
Coordn8 gate — DETERMINISM (advisory): a frame must be a PURE FUNCTION OF t.

WHY THIS GATE EXISTS
--------------------
The render is crash-safe and parallel only because any frame can be re-rendered in
isolation and come out identical (PLAYBOOK 2.5 / R.3). A scene that reads the clock, uses
unseeded randomness, or lets CSS / timers / requestAnimationFrame drive its motion breaks
that silently: a re-rendered frame differs from its neighbours, a parallel capture
jitters, and two renders of the same build disagree. Nothing else measures it.

TWO CHECKS
----------
  static   scans scene sources (.js/.mjs/.ts, .html/.htm, .css) with comments stripped
           first, for: Math.random(), Date.now(), new Date() / Date() / new Date(<expr>),
           performance.now(), requestAnimationFrame, setTimeout/setInterval, CSS @keyframes,
           animation:/transition: declarations (in CSS, <style>, style="" and JS), and the
           Web Animations API (el.animate([...])). A deliberate exception is waived per line
           with a reason: `// c8-allow: <reason>` — reported as waived, never silently.
  dynamic  (--dynamic) traces each scene MODULE through the real runtime
           (`node scripts/render/scene_runtime.js trace <scene.js> --repeat 2`), which:
             - renders every sampled t twice in-process and compares the frames;
             - runs the draw under the clock guard, so a clock read or Math.random during a
               draw throws (and is logged even if the scene swallows the throw);
           then traces it again in a FRESH process and compares the two traces byte for
           byte — that catches randomness or clock reads at module load, outside the draw.
           For .html scenes, when a headless browser is available, it loads the page twice
           at the same t (window.renderAt(t) when present) and compares the screenshots,
           and reports any running CSS/Web animation from document.getAnimations().

The gate judges OUTCOMES only. It never reads the scene catalog: an invented scene form is
held to exactly the same rule as a catalog component (freedom doctrine).

ADVISORY: findings are REVIEW signals (exit 1), never a silent veto. Exit 0 PASS,
2 SKIPPED (nothing to scan), 3 ERROR.

Usage:
    python3 scripts/gates/check_determinism.py video/scenes/            # static only
    python3 scripts/gates/check_determinism.py s1.js s2.html --dynamic [--kit kit.json]
        [--words words.json] [--t 0.5,2.0] [--grid 5]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.common import EXIT_ERROR, EXIT_PASS, EXIT_REVIEW, EXIT_SKIPPED, emit  # noqa: E402

RUNTIME = Path(__file__).resolve().parents[1] / "render" / "scene_runtime.js"
SCENE_EXT = {".js", ".mjs", ".cjs", ".ts", ".html", ".htm", ".css"}
WAIVER = re.compile(r"c8-allow:\s*(\S.*)$")

# (rule id, regex, applies-to, message)
_LIT = r"\s*(?:-?\d[\d_.eE+-]*|'[^'\n]*'|\"[^\"\n]*\")\s*(?:,\s*(?:-?\d[\d_.eE+-]*|'[^'\n]*'|\"[^\"\n]*\")\s*)*"
RULES = [
    ("clock.math_random", re.compile(r"\bMath\.random\s*\("), "code",
     "Math.random() — unseeded randomness; use C8.random(seed), which is recorded in the trace"),
    ("clock.date_now", re.compile(r"\bDate\.now\s*\("), "code",
     "Date.now() reads the wall clock; time must come from t, a named beat or atWord()"),
    ("clock.new_date", re.compile(r"\bnew\s+Date\s*\(\s*\)|(?<![\w.$])Date\s*\(\s*\)"), "code",
     "new Date() / Date() reads the wall clock"),
    ("clock.new_date_expr", re.compile(r"\bnew\s+Date\s*\((?!" + _LIT + r"\))(?!\s*\))[^)]*\)"), "code",
     "new Date(<expression>) — only a literal date is deterministic; check the expression is not clock-derived"),
    ("clock.performance_now", re.compile(r"\bperformance\.now\s*\("), "code",
     "performance.now() reads the clock"),
    ("timing.request_animation_frame", re.compile(r"\brequestAnimationFrame\s*\("), "code",
     "requestAnimationFrame drives motion by the display clock; render from t via renderAt(t)"),
    ("timing.timer", re.compile(r"\bset(?:Timeout|Interval)\s*\("), "code",
     "setTimeout/setInterval used in a scene — animation must be a function of t, not of elapsed time"),
    ("css.keyframes", re.compile(r"@(?:-webkit-)?keyframes\b"), "any",
     "CSS @keyframes animate on the browser clock, not on t"),
    ("css.animation", re.compile(r"(?<![\w-])animation(?:-name|-duration|-play-state|-iteration-count|-delay)?\s*:\s*(?!\s*(?:none|initial|unset)\b)"),
     "any", "CSS animation declaration — runs on the browser clock, not on t"),
    ("css.transition", re.compile(r"(?<![\w-])transition(?:-property|-duration|-delay)?\s*:\s*(?!\s*(?:none|initial|unset|0s?)\b)"),
     "any", "CSS transition — interpolates on the browser clock between frames"),
    ("css.animation_js", re.compile(r"\.style\.(?:animation|transition|webkitAnimation)\w*\s*=(?!=)"), "code",
     "a CSS animation/transition set from JavaScript"),
    ("web_animations", re.compile(r"\.animate\s*\(\s*[\[{]"), "code",
     "the Web Animations API (element.animate) runs on the document timeline, not on t"),
]


def _blank(m: re.Match) -> str:
    """Replace a comment with spaces, keeping newlines so line numbers survive."""
    return re.sub(r"[^\n]", " ", m.group(0))


def strip_comments(text: str, kind: str) -> str:
    out = re.sub(r"/\*.*?\*/", _blank, text, flags=re.S)
    if kind == "html":
        out = re.sub(r"<!--.*?-->", _blank, out, flags=re.S)
    if kind in ("code", "html"):
        # `//` comments, but not the `//` inside a URL (http://…) or a regex/string start
        out = re.sub(r"(?<![:'\"\w\\])//[^\n]*", _blank, out)
    return out


def file_kind(p: Path) -> str:
    ext = p.suffix.lower()
    if ext in (".html", ".htm"):
        return "html"
    if ext == ".css":
        return "css"
    return "code"


def scan_static(paths: list[Path]) -> tuple[list[dict], list[dict], int]:
    findings: list[dict] = []
    waived: list[dict] = []
    files = 0
    for p in paths:
        try:
            raw = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        files += 1
        kind = file_kind(p)
        clean = strip_comments(raw, kind)
        raw_lines = raw.split("\n")
        for rule, rx, scope, msg in RULES:
            if scope == "code" and kind == "css":
                continue
            for m in rx.finditer(clean):
                line = clean.count("\n", 0, m.start()) + 1
                src_line = raw_lines[line - 1] if line - 1 < len(raw_lines) else ""
                rec = {"file": str(p), "line": line, "rule": rule, "match": m.group(0).strip()[:60],
                       "message": msg}
                w = WAIVER.search(src_line)
                if w:
                    rec["waiver"] = w.group(1).strip()
                    waived.append(rec)
                else:
                    findings.append(rec)
    return findings, waived, files


def expand(inputs: list[str]) -> list[Path]:
    out: list[Path] = []
    for s in inputs:
        p = Path(s)
        if p.is_dir():
            out += sorted(q for q in p.rglob("*") if q.is_file() and q.suffix.lower() in SCENE_EXT
                          and "node_modules" not in q.parts and q.name != "scene_runtime.js")
        elif p.is_file():
            out.append(p)
    return out


def is_scene_module(p: Path) -> bool:
    if p.suffix.lower() not in (".js", ".cjs"):
        return False
    try:
        txt = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return "module.exports" in txt and "draw" in txt


def _trace(node: str, scene: Path, out: Path, a: argparse.Namespace) -> subprocess.CompletedProcess:
    cmd = [node, str(RUNTIME), "trace", str(scene), "--out", str(out), "--repeat", "2"]
    if a.kit:
        cmd += ["--kit", a.kit]
    if a.words:
        cmd += ["--words", a.words]
    if a.t:
        cmd += ["--at", a.t]
    if a.grid:
        cmd += ["--grid", str(a.grid)]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=a.timeout)


def dynamic_js(node: str, scene: Path, a: argparse.Namespace, tmp: Path) -> list[dict]:
    f: list[dict] = []
    o1, o2 = tmp / f"{scene.stem}.1.json", tmp / f"{scene.stem}.2.json"
    r1 = _trace(node, scene, o1, a)
    if r1.returncode != 0:
        err = (r1.stderr or r1.stdout).strip().splitlines()
        msg = err[-1] if err else f"exit {r1.returncode}"
        rule = "dynamic.clock_during_draw" if "nondeterministic call" in msg else "dynamic.trace_failed"
        f.append({"file": str(scene), "rule": rule, "message": msg[:400]})
        return f
    t1 = o1.read_text(encoding="utf-8")
    tr = json.loads(t1)
    if tr.get("nondeterminism"):
        calls = sorted({v.get("call") for v in tr["nondeterminism"]})
        f.append({"file": str(scene), "rule": "dynamic.clock_during_draw",
                  "message": f"clock/random read during draw (swallowed by the scene): {', '.join(calls)}"})
    rep = tr.get("repeat") or {}
    if rep and not rep.get("identical", True):
        f.append({"file": str(scene), "rule": "dynamic.same_t_differs",
                  "message": f"the same t rendered twice in one run gave different frames at t={rep.get('mismatches')[:6]}"})
    if tr.get("clockGuard") is False:
        f.append({"file": str(scene), "rule": "dynamic.guard_disabled",
                  "message": "the scene disabled the clock guard (setClockGuard(false)) — its frames are unverified"})
    r2 = _trace(node, scene, o2, a)
    if r2.returncode == 0:
        t2 = o2.read_text(encoding="utf-8")
        if t1 != t2:
            h1, h2 = hashlib.sha256(t1.encode()).hexdigest()[:12], hashlib.sha256(t2.encode()).hexdigest()[:12]
            f.append({"file": str(scene), "rule": "dynamic.cross_run_differs",
                      "message": f"two fresh traces of the same scene differ ({h1} vs {h2}) — something outside "
                                 f"the draw (module load, a global) reads the clock or unseeded randomness"})
    else:
        f.append({"file": str(scene), "rule": "dynamic.trace_failed",
                  "message": "second trace failed: " + (r2.stderr or "").strip()[-300:]})
    return f


_HTML_PROBE = r"""
import hashlib, json, sys
from pathlib import Path
from playwright.sync_api import sync_playwright
scene, ts = Path(sys.argv[1]).resolve(), [float(x) for x in sys.argv[2].split(',')]
out = {"shots": {}, "animations": 0}
with sync_playwright() as p:
    b = p.chromium.launch()
    for rep in range(2):
        for t in ts:
            pg = b.new_page(viewport={"width": 1920, "height": 1080})
            pg.goto(scene.as_uri() + f"?t={t:.4f}")
            try:
                pg.wait_for_function("() => document.title === 'READY' || window.__ready === true", timeout=1500)
            except Exception:
                pg.wait_for_timeout(250)
            pg.evaluate("(t) => { if (window.renderAt) window.renderAt(t); else if (window.renderFrame) window.renderFrame(t); }", t)
            n = pg.evaluate("() => (document.getAnimations ? document.getAnimations().length : 0)")
            out["animations"] = max(out["animations"], n)
            pg.wait_for_timeout(120 + 160 * rep)
            out["shots"].setdefault(str(t), []).append(hashlib.sha256(pg.screenshot()).hexdigest()[:16])
            pg.close()
    b.close()
print(json.dumps(out))
"""


def dynamic_html(scene: Path, a: argparse.Namespace, tmp: Path) -> tuple[list[dict], str | None]:
    try:
        import playwright.sync_api  # noqa: F401
    except Exception:  # noqa: BLE001
        return [], "playwright not importable — HTML scenes checked statically only"
    probe = tmp / "_html_probe.py"
    probe.write_text(_HTML_PROBE, encoding="utf-8")
    ts = a.t or "0.5,1.5"
    try:
        r = subprocess.run([sys.executable, str(probe), str(scene), ts], capture_output=True, text=True,
                           timeout=a.timeout)
    except subprocess.TimeoutExpired:
        return [], "headless browser probe timed out"
    if r.returncode != 0 or not r.stdout.strip():
        return [], "headless browser unavailable: " + (r.stderr or "").strip()[-200:]
    res = json.loads(r.stdout.strip().splitlines()[-1])
    f: list[dict] = []
    if res.get("animations"):
        f.append({"file": str(scene), "rule": "dynamic.running_animations",
                  "message": f"{res['animations']} CSS/Web animation(s) running on the document timeline"})
    for t, hs in res.get("shots", {}).items():
        if len(set(hs)) > 1:
            f.append({"file": str(scene), "rule": "dynamic.same_t_differs",
                      "message": f"two renders of t={t} produced different pixels"})
    return f, None


def main() -> int:
    ap = argparse.ArgumentParser(description="Coordn8 determinism gate (advisory).")
    ap.add_argument("paths", nargs="+", help="scene files or directories")
    ap.add_argument("--dynamic", action="store_true", help="also trace scene modules twice / render HTML twice")
    ap.add_argument("--kit", default=None, help="runtime kit JSON for the dynamic trace")
    ap.add_argument("--words", default=None, help="word timings JSON for the dynamic trace")
    ap.add_argument("--t", default=None, help="comma-separated t values to sample (default: the scene's grid)")
    ap.add_argument("--grid", type=float, default=None, help="samples per second for the dynamic trace")
    ap.add_argument("--timeout", type=int, default=120)
    a = ap.parse_args()

    files = expand(a.paths)
    if not files:
        emit({"gate": "determinism", "status": "SKIPPED", "summary": "no scene sources found to scan"})
        return EXIT_SKIPPED

    try:
        s_find, s_waived, n_files = scan_static(files)
    except Exception as e:  # noqa: BLE001
        emit({"gate": "determinism", "status": "ERROR", "summary": f"static scan failed: {type(e).__name__}: {e}"})
        return EXIT_ERROR

    d_find: list[dict] = []
    d_skips: list[str] = []
    d_scenes = 0
    if a.dynamic:
        node = shutil.which("node")
        with tempfile.TemporaryDirectory(prefix="c8det-") as td:
            tmp = Path(td)
            for p in files:
                if is_scene_module(p):
                    if not node:
                        d_skips.append(f"{p.name}: node not on PATH — dynamic trace SKIPPED")
                        continue
                    d_scenes += 1
                    try:
                        d_find += dynamic_js(node, p, a, tmp)
                    except subprocess.TimeoutExpired:
                        d_find.append({"file": str(p), "rule": "dynamic.trace_failed", "message": "trace timed out"})
                elif p.suffix.lower() in (".html", ".htm"):
                    got, skip = dynamic_html(p, a, tmp)
                    if skip:
                        d_skips.append(f"{p.name}: {skip}")
                    else:
                        d_scenes += 1
                        d_find += got

    n = len(s_find) + len(d_find)
    counts: dict[str, int] = {}
    for r in s_find + d_find:
        counts[r["rule"]] = counts.get(r["rule"], 0) + 1
    emit({
        "gate": "determinism",
        "status": "REVIEW" if n else "PASS",
        "files_scanned": n_files,
        "dynamic_scenes": d_scenes,
        "counts": counts,
        "static": {"findings": s_find, "waived": s_waived},
        "dynamic": {"enabled": bool(a.dynamic), "findings": d_find, "skipped": d_skips},
        "gate_pass_is_not_quality": (
            "Determinism is necessary for a crash-safe, parallel, reproducible render. Passing says "
            "nothing about whether the scene is good — only Stage V or a human can say that."),
        "summary": (
            f"{n} nondeterminism finding(s): " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
            + (f" ({len(s_waived)} waived with a reason)" if s_waived else "")
            if n else
            f"no clock reads, unseeded randomness or clock-driven animation in {n_files} file(s)"
            + (f"; {d_scenes} scene(s) traced twice with identical output" if a.dynamic else "")
            + (f"; {len(d_skips)} dynamic check(s) SKIPPED" if d_skips else "")
            + (f"; {len(s_waived)} waived with a reason" if s_waived else "")),
    })
    return EXIT_REVIEW if n else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
