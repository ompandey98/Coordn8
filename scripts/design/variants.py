#!/usr/bin/env python3
"""Variant treatments for pivotal moments (H4): 2–3 cheap alternatives, judged BLIND.

For the opening, the hero figure and the payoff, the build makes two or three genuinely
different treatments, each as cheap as possible — an instrumentation trace plus ONE contact
sheet — and the blind frame reviewer ranks them. The best passing one ships; the others stay
on disk as alternates.

    variants.py make   <decision> --build <dir> --spec spec.json [--seed N] [--grid 2]
    variants.py rank   <decision> --build <dir> [--verdicts verdicts.json] [--reviewer module.py]
    variants.py status --build <dir>

spec.json:
    {"sentence": "the exact spoken line(s) this moment carries",   (default: the trace's lines)
     "treatments": [{"idea": "...", "technique": "...", "trace": "path/trace.json"}
                    | {"idea": "...", "scene": "path/scene.js", "kit": "kit.json"?}, ...]}   2 or 3

Layout written under <dir>/variants/<decision>/ (pass the build's _build folder as --build):
    v1/ v2/ v3/          trace.json · sheet.svg (+ sheet.png when Pillow is present) · gates.json
                         · treatment.json (idea, technique, role once ranked)
    sentence.txt         the exact sentence(s) spoken over the moment
    review/              the BLIND brief: candidate_A.png … (copies of the sheets only), rank_brief.md
                         and a label map the reviewer is never pointed at. Built by the frame
                         reviewer's own prepare step (scripts/review/frame_review.py) when it exists,
                         otherwise in the same shape here; labels are in an order unrelated to v1/v2/v3.
    builder_notes.json   ideas and techniques (never copied into review/)
    ranked.json          the reviewer's order with a reason per treatment, the shipped one,
                         the alternates, and how blind the review was

The bar is never lowered: every treatment runs the outcome gates on its trace (semantics S1–S8,
invariants C1–C8, density); a failing one cannot ship, and `rank` refuses to declare a winner
unless at least two treatments pass. Treatments that are near-copies of each other (their
visible geometry overlaps by >= 85 %, a Jaccard measure on the traces) are reported — three
takes on one idea are not three ideas.

The reviewer API is `rank(variants_dir) -> ranked list` (scripts/review/frame_review.py; another
file exposing the same function may be given with --reviewer or $COORDN8_VARIANT_REVIEWER). The
coordn8-frame-reviewer agent reads ONLY review/rank_brief.md and the candidate sheets and writes
{"ranking": [{"label": "B", "shows_what_is_said": "match", "reason": "..."}, ...]} to
review/ranking.json (or pass it with --verdicts). Until it has, `rank` is SKIPPED with that
instruction — a ranking is never made up. A candidate the reviewer says `contradicts` its
sentence never ships.
Exit: 0 PASS · 1 REVIEW · 2 SKIPPED · 3 ERROR. Python 3.9+; Pillow optional.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional
from xml.sax.saxutils import escape

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from lib.common import EXIT_ERROR, EXIT_PASS, EXIT_REVIEW, EXIT_SKIPPED, emit  # noqa: E402

PKG_ROOT = Path(__file__).resolve().parents[2]
RUNTIME = PKG_ROOT / "scripts" / "render" / "scene_runtime.js"
GATES = (("semantics", "check_semantics.py", []), ("invariants", "check_invariants.py", []),
         ("density", "check_density.py", ["trace"]))
LABELS = "ABC"
NEAR_DUPLICATE = 0.85
MIN_REASON_WORDS = 4
TILE_W, TILE_H, COLS = 480, 270, 3
VISIBLE = 0.18


# ------------------------------------------------------------------ helpers
def _dir(build: Path, decision: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", decision).strip("-") or "decision"
    return Path(build) / "variants" / safe


def _write(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def _read(p: Path):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def sample_frames(trace: dict, k: int = 6) -> List[dict]:
    """Frames at 87 % through every spoken line (entrances settled), plus the last frame."""
    frames = trace.get("frames") or []
    if not frames:
        return []
    want = []
    for ln in trace.get("lines") or []:
        try:
            s, e = float(ln["start"]), float(ln["end"])
        except (KeyError, TypeError, ValueError):
            continue
        want.append((s + 0.87 * (e - s), ln.get("text") or ln.get("name") or ""))
    want.append((float(frames[-1].get("t") or 0), "last frame"))
    out, seen = [], set()
    for t, label in want[: k]:
        fr = min(frames, key=lambda f: abs(float(f.get("t") or 0) - t))
        key = id(fr)
        if key in seen:
            continue
        seen.add(key)
        out.append({"t": float(fr.get("t") or 0), "label": str(label), "frame": fr})
    return out


def signature(trace: dict) -> set:
    """Coarse visible geometry (10 x 10 cells) across the sampled frames."""
    sig = set()
    for s in sample_frames(trace):
        fr = s["frame"]
        for b in fr.get("boxes") or []:
            if (b.get("alpha") or 0) <= VISIBLE:
                continue
            sig.add((b.get("kind"), round(b["x"] / 192), round(b["y"] / 108), round(b["w"] / 192),
                     round(b["h"] / 108)))
        for c in fr.get("connectors") or []:
            if (c.get("alpha") or 0) > VISIBLE:
                sig.add(("connector", c.get("from"), c.get("to")))
    return sig


def jaccard(a: set, b: set) -> float:
    return len(a & b) / float(len(a | b)) if (a or b) else 1.0


# ------------------------------------------------------------------ contact sheet
def _colour(b: dict, default: str) -> str:
    c = b.get("fill") or b.get("color") or b.get("stroke")
    return c if isinstance(c, str) and re.match(r"^#[0-9a-fA-F]{3,8}$", c) else default


def sheet_svg(trace: dict, title: str = "") -> str:
    tiles = sample_frames(trace)
    W, H = float(trace.get("width") or 1920), float(trace.get("height") or 1080)
    sx, sy = TILE_W / W, TILE_H / H
    rows = max(1, (len(tiles) + COLS - 1) // COLS)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{COLS * (TILE_W + 16) + 16}" '
           f'height="{rows * (TILE_H + 44) + 40}" font-family="sans-serif">',
           '<rect width="100%" height="100%" fill="#f4f2ec"/>',
           f'<text x="16" y="24" font-size="16" fill="#222">{escape(title)}</text>']
    for i, tile in enumerate(tiles):
        ox, oy = 16 + (i % COLS) * (TILE_W + 16), 40 + (i // COLS) * (TILE_H + 44)
        out.append(f'<g transform="translate({ox},{oy})"><rect width="{TILE_W}" height="{TILE_H}" '
                   f'fill="#ffffff" stroke="#999"/>')
        fr = tile["frame"]
        for c in fr.get("connectors") or []:
            a = float(c.get("alpha") or 0)
            pts = " ".join(f"{p['x'] * sx:.1f},{p['y'] * sy:.1f}" for p in c.get("points") or [])
            if pts and a > 0:
                out.append(f'<polyline points="{pts}" fill="none" stroke="#333" stroke-width="1.5" '
                           f'opacity="{a:.2f}"/>')
        for b in fr.get("boxes") or []:
            a = float(b.get("alpha") or 0)
            if a <= 0:
                continue
            x, y, w, h = b["x"] * sx, b["y"] * sy, b["w"] * sx, b["h"] * sy
            kind = b.get("kind")
            if kind == "text":
                size = max(6.0, h * 0.8)
                out.append(f'<text x="{x:.1f}" y="{y + h * 0.8:.1f}" font-size="{size:.1f}" fill="#111" '
                           f'opacity="{a:.2f}">{escape(str(b.get("text") or "")[:60])}</text>')
            elif kind == "mark":
                out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
                           f'fill="{_colour(b, "#7a7aa0")}" opacity="{a * 0.8:.2f}"/>')
            else:
                out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="3" '
                           f'fill="none" stroke="{"#2d5aa6" if kind == "node" else "#666"}" opacity="{a:.2f}"/>')
        out.append(f'<text x="0" y="{TILE_H + 18}" font-size="13" fill="#333">t={tile["t"]:.2f}s · '
                   f'{escape(tile["label"][:70])}</text></g>')
    out.append("</svg>")
    return "\n".join(out)


def sheet_png(trace: dict, path: Path) -> bool:
    try:
        from PIL import Image, ImageDraw  # type: ignore
    except Exception:  # noqa: BLE001 - Pillow is optional
        return False
    tiles = sample_frames(trace)
    W, H = float(trace.get("width") or 1920), float(trace.get("height") or 1080)
    sx, sy = TILE_W / W, TILE_H / H
    rows = max(1, (len(tiles) + COLS - 1) // COLS)
    img = Image.new("RGB", (COLS * (TILE_W + 16) + 16, rows * (TILE_H + 44) + 24), (244, 242, 236))
    d = ImageDraw.Draw(img)
    for i, tile in enumerate(tiles):
        ox, oy = 16 + (i % COLS) * (TILE_W + 16), 16 + (i // COLS) * (TILE_H + 44)
        d.rectangle([ox, oy, ox + TILE_W, oy + TILE_H], fill=(255, 255, 255), outline=(150, 150, 150))
        fr = tile["frame"]
        for c in fr.get("connectors") or []:
            pts = [(ox + p["x"] * sx, oy + p["y"] * sy) for p in c.get("points") or []]
            if len(pts) >= 2 and (c.get("alpha") or 0) > VISIBLE:
                d.line(pts, fill=(50, 50, 50), width=2)
        for b in fr.get("boxes") or []:
            a = float(b.get("alpha") or 0)
            if a <= VISIBLE:
                continue
            x0, y0 = ox + b["x"] * sx, oy + b["y"] * sy
            x1, y1 = x0 + b["w"] * sx, y0 + b["h"] * sy
            if b.get("kind") == "text":
                d.text((x0, y0), str(b.get("text") or "")[:60], fill=(17, 17, 17))
            elif b.get("kind") == "mark":
                d.rectangle([x0, y0, x1, y1], fill=(122, 122, 160))
            else:
                d.rectangle([x0, y0, x1, y1], outline=(45, 90, 166) if b.get("kind") == "node" else (100, 100, 100))
        d.text((ox, oy + TILE_H + 6), f"t={tile['t']:.2f}s  {tile['label'][:70]}", fill=(51, 51, 51))
    img.save(str(path))
    return True


# ------------------------------------------------------------------ gates
def run_gates(trace_path: Path, scene_src: Optional[Path]) -> Dict[str, dict]:
    res = {}
    for name, script, pre in GATES:
        args = [sys.executable, str(PKG_ROOT / "scripts" / "gates" / script)] + pre + [str(trace_path)]
        if scene_src and name != "density":
            args += ["--scene-src", str(scene_src)]
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=120)
            line = next((ln for ln in r.stdout.splitlines() if ln.startswith("{")), "{}")
            g = json.loads(line)
            res[name] = {"status": g.get("status", "ERROR"), "exit": r.returncode,
                         "summary": str(g.get("summary", ""))[:300]}
        except (OSError, ValueError, subprocess.TimeoutExpired) as e:
            res[name] = {"status": "ERROR", "exit": 3, "summary": f"{type(e).__name__}: {e}"}
    return res


def _trace_from_scene(scene: Path, out: Path, kit: Optional[str], grid: int) -> Optional[str]:
    node = shutil.which("node")
    if not node:
        return "node is not on PATH, so a scene cannot be traced here (give a trace file instead)"
    args = [node, str(RUNTIME), "trace", str(scene), "--out", str(out), "--grid", str(grid)]
    if kit:
        args += ["--kit", kit]
    r = subprocess.run(args, capture_output=True, text=True, timeout=180)
    if r.returncode != 0 or not out.exists():
        return f"tracing {scene.name} failed: {(r.stderr or r.stdout)[-300:]}"
    return None


# ------------------------------------------------------------------ make
def make(decision: str, build: Path, spec_path: Path, seed: Optional[int] = None, grid: int = 2) -> dict:
    spec = _read(spec_path)
    if not isinstance(spec, dict) or not isinstance(spec.get("treatments"), list):
        return {"gate": "variants", "status": "ERROR", "summary": f"{spec_path} has no treatments list"}
    treatments = [t for t in spec["treatments"] if isinstance(t, dict)]
    if not 2 <= len(treatments) <= 3:
        return {"gate": "variants", "status": "ERROR",
                "summary": f"a pivotal moment gets 2 or 3 treatments, not {len(treatments)}"}
    base = _dir(build, decision)
    if base.exists():
        shutil.rmtree(base)
    if seed is None:
        seed = int(hashlib.sha256(f"{decision}:{time.time_ns()}".encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    labels = list(LABELS[: len(treatments)])
    rng.shuffle(labels)
    notes = {"schema": 1, "decision": decision, "seed": seed, "treatments": {}}
    findings, sentence, sigs = [], spec.get("sentence"), {}
    for i, t in enumerate(treatments):
        vid = f"v{i + 1}"
        vdir = base / vid
        vdir.mkdir(parents=True, exist_ok=True)
        tr_path = vdir / "trace.json"
        scene = None
        if t.get("trace"):
            src = (spec_path.parent / t["trace"]) if not Path(t["trace"]).is_absolute() else Path(t["trace"])
            if not src.exists():
                return {"gate": "variants", "status": "ERROR", "summary": f"{vid}: no trace at {src}"}
            shutil.copyfile(src, tr_path)
        elif t.get("scene"):
            scene = (spec_path.parent / t["scene"]) if not Path(t["scene"]).is_absolute() else Path(t["scene"])
            err = _trace_from_scene(scene, tr_path, t.get("kit"), grid)
            if err:
                return {"gate": "variants", "status": "SKIPPED" if "node is not" in err else "ERROR",
                        "summary": f"{vid}: {err}"}
        else:
            return {"gate": "variants", "status": "ERROR", "summary": f"{vid}: needs a trace or a scene"}
        trace = _read(tr_path) or {}
        if not sentence:
            sentence = " ".join(str(ln.get("text") or "") for ln in trace.get("lines") or []).strip()
        title = "Treatment"
        (vdir / "sheet.svg").write_text(sheet_svg(trace, title), encoding="utf-8")
        has_png = sheet_png(trace, vdir / "sheet.png")
        gates = run_gates(tr_path, scene)
        passed = all(g["status"] == "PASS" for g in gates.values())
        _write(vdir / "gates.json", {"gates": gates, "pass": passed})
        _write(vdir / "treatment.json", {"variant": vid, "idea": t.get("idea"), "technique": t.get("technique"),
                                         "source": t.get("trace") or t.get("scene"), "gates_pass": passed,
                                         "role": "unranked"})
        notes["treatments"][vid] = {"idea": t.get("idea"), "technique": t.get("technique"),
                                    "gates_pass": passed, "sheet_png": has_png}
        sigs[vid] = signature(trace)
        if not passed:
            findings.append({"check": "treatment-fails-gates", "variant": vid,
                             "detail": "; ".join(f"{k}={v['status']}" for k, v in gates.items())})
    vids = sorted(sigs)
    for a in range(len(vids)):
        for b in range(a + 1, len(vids)):
            j = jaccard(sigs[vids[a]], sigs[vids[b]])
            if j >= NEAR_DUPLICATE:
                findings.append({"check": "near-duplicate-treatments", "variants": [vids[a], vids[b]],
                                 "overlap": round(j, 2),
                                 "detail": f"{vids[a]} and {vids[b]} share {j:.0%} of their visible geometry — "
                                           f"one idea twice is not two treatments"})
    _write(base / "builder_notes.json", notes)
    (base / "sentence.txt").write_text((sentence or "").strip() + "\n", encoding="utf-8")
    blind_info = prepare_blind(base, labels)
    if blind_info.get("status") != "PASS":
        findings.append({"check": "blind-brief", "detail": blind_info.get("summary", "")})
    passing = [v for v, n in notes["treatments"].items() if n["gates_pass"]]
    status = "REVIEW" if findings else "PASS"
    return {"gate": "variants", "status": status, "decision": decision, "dir": str(base), "seed": seed,
            "treatments": len(treatments), "passing": passing, "findings": findings,
            "blind_brief": blind_info.get("brief"), "blind_by": blind_info.get("by"),
            "summary": (f"{len(treatments)} treatment(s) for '{decision}', {len(passing)} pass the outcome gates; "
                        f"blind brief {blind_info.get('brief')}" + (f"; {len(findings)} finding(s)" if findings else ""))}


def _frame_review():
    p = PKG_ROOT / "scripts" / "review" / "frame_review.py"
    if not p.is_file():
        return None
    spec = importlib.util.spec_from_file_location("c8_frame_review", str(p))
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        return None
    return mod


def prepare_blind(base: Path, labels: List[str]) -> dict:
    """The blind brief. The frame reviewer's own prepare step when it exists, else the same shape here."""
    fr = _frame_review()
    if fr is not None and callable(getattr(fr, "prepare_variants", None)):
        try:
            r = fr.prepare_variants(base)
            return {"status": r.get("status"), "brief": r.get("brief"), "by": "scripts/review/frame_review.py",
                    "summary": r.get("summary", "")}
        except Exception as e:  # noqa: BLE001
            return {"status": "ERROR", "summary": f"frame_review.prepare_variants failed: {type(e).__name__}: {e}"}
    review = base / "review"
    review.mkdir(parents=True, exist_ok=True)
    vids = sorted(d.name for d in base.iterdir() if d.is_dir() and d.name.startswith("v"))
    lab = {v: labels[i] for i, v in enumerate(vids)}
    sentence = (base / "sentence.txt").read_text(encoding="utf-8").strip() if (base / "sentence.txt").exists() else ""
    lines = ["# Variant ranking — blind brief for `coordn8-frame-reviewer`", "",
             "Each candidate is a different treatment of the SAME moment. You do not know which one the builder "
             "prefers, and the labels are in no meaningful order. Read only these sheets:", ""]
    for v in sorted(vids, key=lambda x: lab[x]):
        src = base / v / "sheet.png"
        if not src.exists():
            src = base / v / "sheet.svg"
        dst = review / f"candidate_{lab[v]}{src.suffix}"
        shutil.copyfile(src, dst)
        lines.append(f"- candidate `{lab[v]}`: `{dst}`")
    lines += ["", "The exact sentence(s) spoken over this moment:", "", f"> {sentence or '(none supplied)'}", "",
              "Rank them best first. For each give the reason (what it shows better or worse, specifically) and "
              "`shows_what_is_said`: match | partial | contradicts.", "",
              '`{"ranking": [{"label": "B", "shows_what_is_said": "match", "reason": "..."}, ...]}`', ""]
    (review / "rank_brief.md").write_text("\n".join(lines), encoding="utf-8")
    (review / ".label_map.json").write_text(json.dumps({"labels": lab}, indent=1) + "\n", encoding="utf-8")
    return {"status": "PASS", "brief": str(review / "rank_brief.md"), "by": "variants.py (frame reviewer absent)"}


# ------------------------------------------------------------------ rank
def _load_reviewer(path: Optional[str]):
    cands = [path or os.environ.get("COORDN8_VARIANT_REVIEWER") or None,
             str(PKG_ROOT / "scripts" / "review" / "frame_review.py")]
    for c in cands:
        if not c or not Path(c).is_file():
            continue
        spec = importlib.util.spec_from_file_location(f"c8_reviewer_{abs(hash(c))}", c)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
        except Exception as e:  # noqa: BLE001
            return None, f"{c} could not be loaded: {type(e).__name__}: {e}"
        if callable(getattr(mod, "rank", None)):
            return mod.rank, c
        if c != cands[-1]:
            return None, f"{c} has no rank(variants_dir) function"
    return None, ("the blind reviewer API is absent (no rank(variants_dir) in scripts/review/frame_review.py); "
                  "run the coordn8-frame-reviewer agent on review/rank_brief.md and pass its answer with --verdicts")


def _normalise(raw) -> List[dict]:
    if isinstance(raw, dict):
        raw = raw.get("ranking") or raw.get("ranked") or []
    items = []
    for i, r in enumerate(raw or []):
        if isinstance(r, str):
            items.append({"label": r, "reason": "", "order": i})
        elif isinstance(r, dict):
            lab = r.get("variant") or r.get("label") or r.get("id") or r.get("name") or r.get("sheet")
            items.append({"label": str(lab or ""), "reason": str(r.get("reason") or r.get("why") or ""),
                          "order": r.get("rank", i),
                          "contradicts": bool(r.get("disqualified")) or
                          str(r.get("shows_what_is_said") or "").lower() == "contradicts"})
    items.sort(key=lambda x: (x["order"] if isinstance(x["order"], (int, float)) else 99))
    return items


def _echoes(reason: str, idea: str, n: int = 5) -> bool:
    w = re.findall(r"[a-z0-9]+", (idea or "").lower())
    text = " ".join(re.findall(r"[a-z0-9]+", reason.lower()))
    return any(" ".join(w[i:i + n]) in text for i in range(0, max(0, len(w) - n + 1))) if len(w) >= n else False


def rank(decision: str, build: Path, verdicts: Optional[Path] = None, reviewer: Optional[str] = None) -> dict:
    base = _dir(build, decision)
    notes = _read(base / "builder_notes.json")
    if not isinstance(notes, dict):
        return {"gate": "variants", "status": "ERROR", "summary": f"no treatments made for '{decision}' in {base}"}
    review = base / "review"
    fn, source = _load_reviewer(reviewer)
    raw = None
    if fn is not None:
        try:
            raw = fn(str(base), str(verdicts)) if verdicts else fn(str(base))
        except TypeError:
            raw = fn(str(base))
        except ValueError as e:
            return {"gate": "variants", "status": "REVIEW", "summary": f"the reviewer's ranking is malformed: {e}"}
        except Exception as e:  # noqa: BLE001
            return {"gate": "variants", "status": "ERROR", "summary": f"reviewer failed: {type(e).__name__}: {e}"}
    elif verdicts:
        raw, source = _read(verdicts), str(verdicts)
        if raw is None:
            return {"gate": "variants", "status": "ERROR", "summary": f"cannot read verdicts {verdicts}"}
    else:
        return {"gate": "variants", "status": "SKIPPED", "summary": source}
    if not raw:
        return {"gate": "variants", "status": "SKIPPED",
                "summary": f"waiting for the blind reviewer: dispatch coordn8-frame-reviewer on "
                           f"{review / 'rank_brief.md'} and save its answer as {review / 'ranking.json'}"}
    items = _normalise(raw)
    lm = _read(review / ".label_map.json") or {}
    lab2v = {lab: v for v, lab in (lm.get("labels") or {}).items()}
    v_ids = set(lab2v.values())
    findings, ranked, seen = [], [], set()
    for it in items:
        lab = re.sub(r"\.(png|svg)$", "", it["label"].strip(), flags=re.I)
        vid = lab2v.get(lab.upper()) or (lab if lab in v_ids else None)
        if vid is None:
            findings.append({"check": "unknown-label", "detail": f"the reviewer ranked '{it['label']}', which is "
                                                                 f"not one of {sorted(lab2v)}"})
            continue
        if vid in seen:
            findings.append({"check": "ranked-twice", "detail": f"{lab} appears twice in the ranking"})
            continue
        seen.add(vid)
        reason = it["reason"].strip()
        if it.get("contradicts"):
            notes["treatments"][vid]["gates_pass"] = False
            findings.append({"check": "contradicts", "detail": f"{lab}: the reviewer says the frame contradicts "
                                                               f"its sentence — it cannot ship"})
        if len(reason.split()) < MIN_REASON_WORDS:
            findings.append({"check": "reason-missing", "detail": f"{lab}: a ranking needs a reason in words "
                                                                  f"(got {reason!r})"})
        if _echoes(reason, notes["treatments"][vid].get("idea") or ""):
            findings.append({"check": "not-blind", "detail": f"{lab}: the reason repeats the builder's own notes — "
                                                             f"the reviewer must see only the sheets and sentence"})
        ranked.append({"rank": len(ranked) + 1, "variant": vid,
                       "label": next((k for k, v in lab2v.items() if v == vid), vid), "reason": reason,
                       "gates_pass": bool(notes["treatments"][vid].get("gates_pass"))})
    passing = [v for v, n in notes["treatments"].items() if n.get("gates_pass")]
    missing = [v for v in passing if v not in seen]
    if missing:
        findings.append({"check": "unranked", "detail": f"passing treatment(s) {missing} were not ranked"})
    shipped = next((r["variant"] for r in ranked if r["gates_pass"]), None)
    if len(passing) < 2:
        findings.append({"check": "bar-not-lowered",
                         "detail": f"only {len(passing)} treatment(s) pass the outcome gates; make another passing "
                                   f"treatment before choosing — a winner among failures is not a winner"})
        shipped = None
    alternates = [r["variant"] for r in ranked if r["variant"] != shipped] + \
        [v for v in sorted(notes["treatments"]) if v not in seen]
    blocking = {"unknown-label", "ranked-twice", "reason-missing", "unranked", "bar-not-lowered", "not-blind"}
    status = "REVIEW" if any(f["check"] in blocking for f in findings) else "PASS"
    if status != "PASS":
        shipped = None
        alternates = sorted(notes["treatments"])
    out = {"schema": 1, "decision": decision, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "status": status,
           "ranked": ranked, "shipped": shipped, "alternates": alternates,
           "reviewer": {"source": source, "blind": True, "saw": str(review),
                        "saw_files": sorted(p.name for p in review.iterdir() if not p.name.startswith("."))
                        if review.is_dir() else []},
           "findings": findings}
    _write(base / "ranked.json", out)
    for v in notes["treatments"]:
        tp = base / v / "treatment.json"
        t = _read(tp) or {}
        t["role"] = "shipped" if v == shipped else ("alternate" if shipped else "unranked")
        _write(tp, t)
    return {"gate": "variants", "status": status, "decision": decision, "ranked_json": str(base / "ranked.json"),
            "shipped": shipped, "alternates": alternates, "findings": findings,
            "summary": (f"'{decision}': {shipped} ships, {len(alternates)} alternate(s) kept, reasons from {source}"
                        if status == "PASS" else
                        f"'{decision}': no winner — " + "; ".join(f["detail"] for f in findings[:3]))}


def status(build: Path) -> dict:
    root = Path(build) / "variants"
    rows = []
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        if not d.is_dir():
            continue
        r = _read(d / "ranked.json") or {}
        rows.append({"decision": d.name, "treatments": len([x for x in d.glob("v*") if x.is_dir()]),
                     "ranked": bool(r), "shipped": r.get("shipped"), "alternates": r.get("alternates", [])})
    return {"gate": "variants", "status": "PASS" if rows else "SKIPPED", "decisions": rows,
            "summary": f"{len(rows)} pivotal decision(s) with treatments under {root}"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Coordn8 variant treatments (H4)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make")
    m.add_argument("decision")
    m.add_argument("--build", required=True)
    m.add_argument("--spec", required=True)
    m.add_argument("--seed", type=int)
    m.add_argument("--grid", type=int, default=2)
    r = sub.add_parser("rank")
    r.add_argument("decision")
    r.add_argument("--build", required=True)
    r.add_argument("--verdicts")
    r.add_argument("--reviewer")
    s = sub.add_parser("status")
    s.add_argument("--build", required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "make":
            res = make(a.decision, Path(a.build), Path(a.spec), a.seed, a.grid)
        elif a.cmd == "rank":
            res = rank(a.decision, Path(a.build), Path(a.verdicts) if a.verdicts else None, a.reviewer)
        else:
            res = status(Path(a.build))
    except (OSError, ValueError) as e:
        res = {"gate": "variants", "status": "ERROR", "summary": f"{type(e).__name__}: {e}"}
    emit(res)
    return {"PASS": EXIT_PASS, "REVIEW": EXIT_REVIEW, "SKIPPED": EXIT_SKIPPED}.get(res.get("status"), EXIT_ERROR)


if __name__ == "__main__":
    sys.exit(main())
