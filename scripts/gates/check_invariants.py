#!/usr/bin/env python3
"""
Coordn8 gate — INVARIANT GATES C1–C8 (necessary, never sufficient).

These are the deterministic frame-level invariants. They are NOT a quality
measure — passing every one of them means "not provably broken in these eight
mechanical ways", nothing more. A build once reported ten green gates while ten
real defects shipped, so this gate states that limit in its own output and defers
the quality question to Stage V (blind frame-vs-sentence review) and to a human.

Each check exists because the corresponding defect shipped at least once with a
fully green board:

  C1 OVERLAP      Two clearly-visible CONTENT panels PARTIALLY overlap. Full
                  containment is legal (deliberate nesting); partial overlap is
                  the "one visual on top of another" defect. Also flags two
                  legible text boxes colliding — the floor is legibility (0.25),
                  not opacity: a chip label dimmed to 0.32 is still plainly
                  readable, and requiring BOTH texts to be bright blinded an
                  earlier version of this check to exactly that case.
  C2 ASSET        A DECLARED asset never actually draws. A scene promises an
                  asset manifest; if a prepared screenshot never appears, the
                  narration describes something the viewer never sees.
  C3 CURSOR       A recorded cursor sits in dead space instead of on the panel
                  it is pointing at, during its spoken line.
  C4 REPETITION   (implemented in check_content.py — n-gram repetition across
                  narration lines; referenced here so the set stays honest.)
  C5 SOURCE       A scene source file carries no provenance comment (`// SRC:`),
                  so a claim on screen cannot be traced back to the code.
  C6 DEAD-FRAME   A SUSTAINED void (> 0.6 s) while a line is being SPOKEN. A
                  short void at a line boundary is a deliberate cross-fade and
                  must NOT be flagged — measuring instants instead of duration
                  makes this gate noisy, and a noisy gate gets ignored, which is
                  how a real 1-second void shipped. Silence is never flagged:
                  a hold with just the title between sentences is breathing room.
  C7 LEGIBILITY   An asset panel rendered below ~0.42x of its source width, at
                  which real UI text becomes unreadable. On-screen glyph size is
                  governed by the crop's WIDTH, not its height — cropping a text
                  block shorter does NOT enlarge its glyphs. If a panel cannot
                  reach the floor at any width that fits the frame, stop cropping:
                  keep the panel for structure and re-draw the load-bearing line
                  as real rendered text at full size.
  C8 CROSS-CONTEXT  An asset belonging to one subsystem appears inside another
                  subsystem's scene — showing one system's product while the
                  narration describes a different one. Factually wrong, and
                  invisible to every geometry check, because it is a question of
                  WHICH SYSTEM an asset belongs to.

ADVISORY: findings are REVIEW signals, never a silent veto.

Usage:
    python3 scripts/gates/check_invariants.py <trace.json> [more.json ...]
    python3 scripts/gates/check_invariants.py trace.json --scene-src video/s1.js
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.common import (EXIT_ERROR, EXIT_PASS, EXIT_REVIEW,  # noqa: E402
                        EXIT_SKIPPED, emit, load_json)

VISIBLE = 0.25            # legibility floor: above this, an element is readable
INK = 0.18                # above this, an element counts as content
PANEL_OVERLAP_FRAC = 0.15  # partial overlap of this much of the smaller panel
TEXT_OVERLAP_FRAC = 0.18
CONTAIN_MARGIN = 6.0      # tolerance when deciding "fully contained"
MAX_VOID_S = 0.6          # a void longer than this, under speech, is a defect
MIN_SHOT_SCALE = 0.42     # below this, real UI text in a panel is unreadable
CURSOR_MARGIN = 60.0      # the pointer may travel just off a panel's edge
TITLE_BAND_Y = 200.0      # text above this y is the standing scene title


def _inter(a: dict, b: dict) -> float:
    ix, iy = max(a["x"], b["x"]), max(a["y"], b["y"])
    ax = min(a["x"] + a["w"], b["x"] + b["w"])
    ay = min(a["y"] + a["h"], b["y"] + b["h"])
    w, h = ax - ix, ay - iy
    return w * h if (w > 0 and h > 0) else 0.0


def _area(b: dict) -> float:
    return max(0.0, b["w"]) * max(0.0, b["h"])


def _contains(outer: dict, inner: dict, m: float = CONTAIN_MARGIN) -> bool:
    return (inner["x"] >= outer["x"] - m and inner["y"] >= outer["y"] - m
            and inner["x"] + inner["w"] <= outer["x"] + outer["w"] + m
            and inner["y"] + inner["h"] <= outer["y"] + outer["h"] + m)


def _fmt(b: dict) -> str:
    return (f"{b.get('kind')}[{b['x']:.0f},{b['y']:.0f} "
            f"{b['w']:.0f}x{b['h']:.0f} a={b.get('alpha', 0):.2f}]")


def run(trace: dict, sources: dict[str, str]) -> dict:
    scene = trace.get("scene", "?")
    frames = trace.get("frames") or []
    grid = float(trace.get("grid") or 5)
    step = 1.0 / grid if grid else 0.2
    scene_subsystem = trace.get("subsystem")
    declared_assets = trace.get("declaredAssets") or []

    f = {k: [] for k in ("C1", "C2", "C3", "C5", "C6", "C7", "C8")}
    drawn_assets: set[str] = set()
    void_instants: list[float] = []

    for fr in frames:
        t = float(fr.get("t") or 0.0)
        boxes = fr.get("boxes") or []
        assets = fr.get("assets") or []
        speaking = fr.get("speaking")

        # ---- C1: partial overlap of two visible content panels ----
        panels = [b for b in boxes
                  if b.get("kind") in ("card", "shot") and (b.get("alpha") or 0) > VISIBLE]
        for i in range(len(panels)):
            for j in range(i + 1, len(panels)):
                a, b = panels[i], panels[j]
                ia = _inter(a, b)
                if ia <= 0:
                    continue
                if _contains(a, b) or _contains(b, a):
                    continue                      # deliberate nesting is legal
                smaller = min(_area(a), _area(b))
                if smaller > 0 and ia / smaller > PANEL_OVERLAP_FRAC:
                    f["C1"].append(
                        f"t={t:.2f}s {ia / smaller * 100:.0f}% partial overlap: "
                        f"{_fmt(a)} X {_fmt(b)} — one visual on top of another")
        texts = [b for b in boxes
                 if b.get("kind") == "text" and (b.get("alpha") or 0) > VISIBLE]
        for i in range(len(texts)):
            for j in range(i + 1, len(texts)):
                a, b = texts[i], texts[j]
                ia = _inter(a, b)
                if ia <= 0:
                    continue
                smaller = min(_area(a), _area(b))
                if smaller > 0 and ia / smaller > TEXT_OVERLAP_FRAC:
                    f["C1"].append(
                        f"t={t:.2f}s legible text collision "
                        f"({str(a.get('text'))[:22]!r} X {str(b.get('text'))[:22]!r}) "
                        f"— old text bleeding through / two strings on one spot")

        # ---- C2 / C7 / C8: asset-level invariants ----
        for a in assets:
            if (a.get("alpha") or 0) > INK:
                drawn_assets.add(str(a.get("path")))
            if (a.get("alpha") or 0) > 0.30:
                sc = a.get("scale")
                if isinstance(sc, (int, float)) and sc == sc and sc < MIN_SHOT_SCALE:
                    f["C7"].append(
                        f"t={t:.2f}s panel '{a.get('path')}' renders at {sc:.2f}x of source "
                        f"(floor {MIN_SHOT_SCALE}x) — real UI text becomes unreadable. "
                        f"Scale is governed by WIDTH: cropping shorter will NOT help. "
                        f"Keep the panel for structure and re-draw the key line as real text")
                asub = a.get("subsystem")
                if scene_subsystem and asub and asub != scene_subsystem:
                    f["C8"].append(
                        f"t={t:.2f}s asset '{a.get('path')}' belongs to subsystem "
                        f"'{asub}' but this scene is about '{scene_subsystem}' — showing one "
                        f"system's product while narrating another")

        # ---- C3: cursor must sit on the panel it points at, while speaking ----
        for c in (fr.get("cursors") or []):
            if (c.get("alpha") or 1) <= 0.15:
                continue
            target = c.get("onAsset")
            candidates = [a for a in assets if (a.get("alpha") or 0) > VISIBLE
                          and (target is None or str(a.get("path")) == str(target))]
            if not candidates:
                if target is not None:
                    f["C3"].append(
                        f"t={t:.2f}s cursor points at '{target}' but that asset is not "
                        f"visibly drawn in this frame")
                continue
            on_any = any(
                (a["x"] - CURSOR_MARGIN <= c["x"] <= a["x"] + a["w"] + CURSOR_MARGIN
                 and a["y"] - CURSOR_MARGIN <= c["y"] <= a["y"] + a["h"] + CURSOR_MARGIN)
                for a in candidates)
            if not on_any:
                f["C3"].append(
                    f"t={t:.2f}s cursor at ({c['x']:.0f},{c['y']:.0f}) sits in dead space, "
                    f"off every visible panel — a viewer reads that as a mistake")

        # ---- C6: collect void instants, but ONLY while a line is spoken ----
        # Silence is deliberate breathing room and must never be flagged.
        if speaking:
            vis = [b for b in boxes if (b.get("alpha") or 0) > INK]
            shots = [b for b in vis if b.get("kind") == "shot"]
            chips = [b for b in vis if b.get("kind") == "chip"]
            nodes = [b for b in vis if b.get("kind") == "node"]
            body = [b for b in vis if b.get("kind") == "text"
                    and float(b.get("y") or 0) > TITLE_BAND_Y]
            conns = [c for c in (fr.get("connectors") or [])
                     if (c.get("alpha") or 0) > INK]
            # A bare container card with nothing inside it is itself the defect,
            # so cards alone do not count as content.
            if len(shots) + len(chips) + len(nodes) + len(body) + len(conns) == 0:
                void_instants.append(t)

    # ---- C6: collapse instants into RUNS; only a SUSTAINED void is a defect ----
    runs: list[list[float]] = []
    for t in void_instants:
        if runs and abs(t - runs[-1][-1]) <= step + 1e-6:
            runs[-1].append(t)
        else:
            runs.append([t])
    for r in runs:
        span = (r[-1] - r[0]) + step
        if span > MAX_VOID_S:
            f["C6"].append(
                f"t={r[0]:.2f}s SUSTAINED VOID {span:.1f}s ({len(r)} samples) while a line "
                f"is being spoken — the narration describes something the frame does not show")

    # ---- C2: every DECLARED asset must actually draw somewhere ----
    for a in declared_assets:
        path = a if isinstance(a, str) else str(a.get("path", ""))
        if path and path not in drawn_assets:
            f["C2"].append(
                f"declared asset '{path}' NEVER draws in this scene — it was prepared and "
                f"promised but never appears (check its alpha/gating expression)")

    # ---- C5: provenance comment on the scene source ----
    for name, src in sources.items():
        if not re.search(r"//\s*SRC:|/\*\s*SRC:|#\s*SRC:", src):
            f["C5"].append(
                f"{name}: no `// SRC:` provenance comment — a claim rendered by this scene "
                f"cannot be traced back to the code it came from")

    return {"scene": scene, "findings": f,
            "declared_assets": len(declared_assets), "drawn_assets": len(drawn_assets)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Coordn8 invariant gates C1-C8.")
    ap.add_argument("traces", nargs="+")
    ap.add_argument("--scene-src", action="append", default=[])
    args = ap.parse_args()

    traces = [Path(p) for p in args.traces]
    have = [p for p in traces if p.exists()]
    if not have:
        emit({"gate": "invariants", "status": "SKIPPED",
              "summary": f"missing trace(s): {', '.join(str(p) for p in traces)} — build one "
                         f"with scene_runtime.js buildTrace first"})
        return EXIT_SKIPPED

    sources: dict[str, str] = {}
    for p in args.scene_src:
        pp = Path(p)
        if pp.exists():
            sources[pp.name] = pp.read_text(encoding="utf-8")

    per_scene, totals = [], {k: 0 for k in ("C1", "C2", "C3", "C5", "C6", "C7", "C8")}
    for tp in have:
        try:
            trace = load_json(tp)
        except Exception as e:  # noqa: BLE001
            emit({"gate": "invariants", "status": "ERROR",
                  "summary": f"could not parse {tp}: {type(e).__name__}: {e}"})
            return EXIT_ERROR
        res = run(trace, sources)
        for k, v in res["findings"].items():
            totals[k] += len(v)
        per_scene.append(res)
        sources = {}   # C5 is per-source, only report it once

    n = sum(totals.values())
    emit({
        "gate": "invariants",
        "status": "REVIEW" if n else "PASS",
        "scenes": [r["scene"] for r in per_scene],
        "counts": totals,
        "limits": {"panel_overlap_frac": PANEL_OVERLAP_FRAC,
                   "text_overlap_frac": TEXT_OVERLAP_FRAC,
                   "max_void_seconds": MAX_VOID_S,
                   "min_panel_scale": MIN_SHOT_SCALE,
                   "visible_alpha": VISIBLE},
        "findings": {r["scene"]: {k: v for k, v in r["findings"].items() if v}
                     for r in per_scene if any(r["findings"].values())},
        "gate_meanings": {
            "C1": "two visible content panels partially overlap / legible text collides",
            "C2": "a declared asset never actually draws",
            "C3": "a cursor sits in dead space instead of on its panel",
            "C4": "narration repetition — implemented in check_content.py",
            "C5": "a scene source carries no `// SRC:` provenance comment",
            "C6": "a sustained void (>0.6s) while a line is being spoken",
            "C7": "an asset panel below 0.42x of source — UI text unreadable",
            "C8": "an asset from one subsystem inside another subsystem's scene",
        },
        "gate_pass_is_not_quality": (
            "These are INVARIANTS: necessary, never sufficient. Passing all of them means "
            "'not provably broken in these eight mechanical ways' — it is NOT evidence the "
            "video is good. Only Stage V (blind frame-vs-sentence review) or a human can "
            "say that."),
        "summary": (
            f"{n} invariant finding(s) across {len(per_scene)} scene(s): "
            + ", ".join(f"{k}={v}" for k, v in totals.items() if v)
            if n else
            f"no invariant findings across {len(per_scene)} scene(s). NOTE: these are "
            f"necessary-not-sufficient checks; a green board here is NOT a quality claim."),
    })
    return EXIT_REVIEW if n else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
