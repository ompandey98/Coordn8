#!/usr/bin/env python3
"""
Coordn8 gate — CONTINUITY (E9), advisory.

Reads a scene plan (and, optionally, instrumentation traces from scene_runtime.js) and judges
two outcomes a viewer feels:

  1. A CUT where a MORPH was possible. Consecutive scenes that share element ids (the same
     object persists) but hard-cut between them make the object jump; a same-element morph
     lets the eye follow it. Flagged unless the plan states a `cut_reason` (a deliberate jump
     cut is a legitimate choice — say why, and it becomes a note).
  2. An EMPTY FIRST SECOND. Nothing clearly visible (alpha >= 0.5, a non-trivial area) at any
     time t <= 1.0 s of the video. The opening frame should already hold the anchor — the title
     or the persistent object.

Notes only — never findings:
  * a transition id that is not in the active kit's vocabulary (kits are menus, never fences);
  * scenes that share ids but use a non-cut, non-morph transition (a dissolve/push could carry
    them as a morph);
  * a morph declared between scenes that share nothing (it will read as a dissolve);
  * a first second that could not be measured from the inputs (said plainly, never a pass).

Plan schema (defaults; extra keys are ignored):
  {"scenes": [
     {"id": "intro", "start": 0.0, "end": 4.0,
      "elements": ["title", {"id": "cache", "visible_from": 0.0, "w": 400, "h": 200}],
      "trace": "optional/path/to/trace.json"},
     {"id": "how", "transition": "morph" | {"id": "page-push"}, "cut_reason": "optional", ...}],
   "first_second": [{"t": 0.0, "elements": [{"id", "alpha", "w", "h", "kind"}]}],   # optional
   "anchor": {"id": "title", "enter": 0.0, "fadeIn": 0.0}}                          # optional
  `transition` on scene i is the transition INTO scene i from scene i-1.

Exit: 0 PASS, 1 REVIEW (findings), 2 SKIPPED (no input), 3 ERROR (unreadable input).

Usage:
    python3 scripts/gates/check_continuity.py _build/scene_plan.json [--kit design_system.json|kit.json]
    python3 scripts/gates/check_continuity.py plan.json --trace s1.trace.json --trace s2.trace.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
from lib.common import EXIT_ERROR, EXIT_PASS, EXIT_REVIEW, EXIT_SKIPPED, emit  # noqa: E402

GATE = "check_continuity"
FIRST_SECOND = 1.0
VISIBLE_ALPHA = 0.5
MIN_AREA_FRAC = 0.0025            # 0.25 % of the frame: a title line, not a stray hairline
IGNORE_KINDS = {"background", "caption-scrim", "scrim", "vignette"}
KINDS = ("morph", "dissolve", "cut", "push", "wipe", "dip", "zoom")


def kind_of(entry) -> str:
    """Same classification as scripts/render/transitions.js kindOf()."""
    if isinstance(entry, str):
        entry = {"id": entry}
    e = entry or {}
    if e.get("kind") in KINDS:
        return e["kind"]
    tid = str(e.get("id") or "").lower()
    if tid in KINDS:
        return tid
    if e.get("morph"):
        return "morph"
    secs = e.get("s") if isinstance(e.get("s"), (int, float)) else (
        e["ms"] / 1000.0 if isinstance(e.get("ms"), (int, float)) else None)
    if e.get("on_beat") or secs == 0 or re.search(r"(^|\b|-)cut\b|^cut", tid):
        return "cut"
    if e.get("mask") or re.search(r"wipe|iris", tid):
        return "wipe"
    if e.get("move") or re.search(r"push|slide", tid):
        return "push"
    if e.get("blend") == "via" or e.get("via_role") or e.get("via") or re.search(r"through|dip", tid):
        return "dip"
    if re.search(r"zoom", tid) or (isinstance(e.get("scale_from"), (int, float)) and
                                   isinstance(e.get("scale_to"), (int, float)) and
                                   e["scale_from"] != e["scale_to"]):
        return "zoom"
    if e.get("blend") == "crossfade" or re.search(r"dissolve|fade", tid):
        return "dissolve"
    if "morph" in tid:
        return "morph"
    return "dissolve"


def kit_vocabulary(path) -> dict:
    """{transition id: kind} from a kit JSON, a design_system.json or a runtime export."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    kit = data.get("kit_spec") if isinstance(data.get("kit_spec"), dict) else data
    return {str(t.get("id")): kind_of(t) for t in (kit.get("transitions") or []) if isinstance(t, dict)}


def _ids(elements) -> list:
    out = []
    for e in elements or []:
        i = e if isinstance(e, str) else (e.get("id") if isinstance(e, dict) else None)
        if i is not None and i not in out:
            out.append(i)
    return out


def _trace_ids(trace: dict) -> list:
    out = []
    for f in trace.get("frames") or []:
        for b in f.get("boxes") or []:
            if b.get("id") is not None and (b.get("alpha") or 0) > 0.18 and b["id"] not in out:
                out.append(b["id"])
    return out


def _visible(el: dict, frame_area: float) -> bool:
    if el.get("kind") in IGNORE_KINDS:
        return False
    a = el.get("alpha", 1.0)
    try:
        a = float(a)
    except (TypeError, ValueError):
        return False
    if a != a or a < VISIBLE_ALPHA:
        return False
    w, h = el.get("w"), el.get("h")
    if isinstance(w, (int, float)) and isinstance(h, (int, float)):
        return w * h >= frame_area * MIN_AREA_FRAC
    return True       # declared without a size: trust the declaration (a title, a node)


def first_second(plan: dict, traces: list, frame_area: float) -> dict:
    """{"measured": bool, "first_visible_t": float|None, "source": str}."""
    # 1) the first scene's own instrumentation trace — the strongest evidence (measured draws)
    first_trace = traces[0] if traces else None
    if first_trace and first_trace.get("frames"):
        offset = float(((plan.get("scenes") or [{}])[0] or {}).get("start", 0.0) or 0.0)
        for f in sorted(first_trace["frames"], key=lambda f: f.get("t", 0)):
            t = offset + float(f.get("t", 0))
            if t > FIRST_SECOND + 1e-9:
                break
            if any(_visible(b, frame_area) for b in f.get("boxes") or []):
                return {"measured": True, "first_visible_t": round(t, 3), "source": "trace"}
        return {"measured": True, "first_visible_t": None, "source": "trace"}
    # 2) sampled frames declared in the plan
    fs = plan.get("first_second")
    if isinstance(fs, list) and fs:
        for f in sorted(fs, key=lambda f: f.get("t", 0)):
            if float(f.get("t", 0)) > FIRST_SECOND + 1e-9:
                break
            if any(_visible(e, frame_area) for e in f.get("elements") or [] if isinstance(e, dict)):
                return {"measured": True, "first_visible_t": round(float(f.get("t", 0)), 3),
                        "source": "plan.first_second"}
        return {"measured": True, "first_visible_t": None, "source": "plan.first_second"}
    # 3) an anchor spec (transitions.js anchorAlpha semantics: fully visible by enter + fadeIn)
    an = plan.get("anchor")
    if isinstance(an, dict):
        enter = float(an.get("enter", 0.0) or 0.0)
        fade = float(an.get("fadeIn", 0.0) or 0.0)
        t = enter + fade
        return {"measured": True, "first_visible_t": round(t, 3) if t <= FIRST_SECOND else None,
                "source": "plan.anchor"}
    # 4) the first scene's declared element entrances
    scenes = plan.get("scenes") or []
    if scenes:
        s0 = scenes[0] or {}
        start = float(s0.get("start", 0.0) or 0.0)
        ents = [e for e in s0.get("elements") or [] if isinstance(e, dict)
                and isinstance(e.get("visible_from"), (int, float))]
        if ents:
            ok = [start + float(e["visible_from"]) for e in ents if _visible(e, frame_area)]
            t = min(ok) if ok else None
            return {"measured": True, "first_visible_t": round(t, 3) if t is not None and t <= FIRST_SECOND
                    else None, "source": "plan.elements.visible_from"}
    return {"measured": False, "first_visible_t": None, "source": None}


def check(plan: dict, traces: list | None = None, vocab: dict | None = None) -> dict:
    traces = traces or []
    scenes = plan.get("scenes") or []
    W = float(plan.get("width", 1920) or 1920)
    H = float(plan.get("height", 1080) or 1080)
    findings, notes, pairs = [], [], []
    ids, scene_traces = [], []
    for i, sc in enumerate(scenes):
        sc = sc or {}
        own = _ids(sc.get("elements"))
        tr = traces[i] if i < len(traces) else None
        if tr is None and sc.get("trace"):
            try:
                tr = json.loads(Path(sc["trace"]).read_text(encoding="utf-8"))
            except (OSError, ValueError) as ex:
                notes.append(f"scene {sc.get('id', i)}: trace {sc['trace']} unreadable ({type(ex).__name__})")
        for x in (_trace_ids(tr) if tr else []):
            if x not in own:
                own.append(x)
        ids.append(own)
        scene_traces.append(tr)
    for i in range(1, len(scenes)):
        a, b = scenes[i - 1] or {}, scenes[i] or {}
        tr = b.get("transition")
        name = (tr.get("id") or tr.get("kind")) if isinstance(tr, dict) else tr
        shared = [x for x in ids[i] if x in ids[i - 1]]
        label = f"{a.get('id', i - 1)} -> {b.get('id', i)}"
        if tr is None:
            if shared:
                notes.append(f"{label}: no transition declared; the scenes share {shared} — declare one "
                             f"(a morph carries them)")
            pairs.append({"pair": label, "transition": None, "kind": None, "shared": shared})
            continue
        if vocab is not None and name is not None and str(name) in vocab:
            kind = vocab[str(name)]
        else:
            kind = kind_of(tr)
        if vocab is not None and name is not None and str(name) not in vocab and str(name) not in KINDS:
            notes.append(f"{label}: transition {name!r} is not in the active kit's vocabulary "
                         f"({', '.join(sorted(vocab))}) — fine, kits are menus")
        elif vocab is not None and str(name) in KINDS and kind not in set(vocab.values()):
            notes.append(f"{label}: the active kit has no {kind!r} transition in its vocabulary — fine, "
                         f"kits are menus")
        if shared and kind == "cut":
            if b.get("cut_reason"):
                notes.append(f"{label}: deliberate cut over shared {shared} — {b['cut_reason']}")
            else:
                findings.append(
                    f"{label}: the scenes share element id(s) {shared} but CUT — the object jumps; "
                    f"morph it (same-element morph interpolates position/size/colour) or state a "
                    f"cut_reason if the jump is the point")
        elif shared and kind != "morph":
            notes.append(f"{label}: shared {shared} carried by a {kind!r} — a morph would let the eye follow them")
        elif kind == "morph" and not shared:
            notes.append(f"{label}: morph declared but the scenes share no element id — it will read as a dissolve")
        pairs.append({"pair": label, "transition": name, "kind": kind, "shared": shared})
    fs = first_second(plan, [scene_traces[0]] if scene_traces and scene_traces[0] else [], W * H)
    if fs["measured"] and fs["first_visible_t"] is None:
        findings.append(
            f"nothing clearly visible in the first {FIRST_SECOND:g} s (alpha >= {VISIBLE_ALPHA}, area >= "
            f"{MIN_AREA_FRAC * 100:g}% of the frame; source: {fs['source']}) — open on an anchor: the title "
            f"or the persistent object, visible from frame 0 (transitions.js anchorState)")
    elif not fs["measured"]:
        notes.append("first second NOT measured: give a trace of the first scene, plan.first_second samples, "
                     "plan.anchor, or visible_from on the first scene's elements")
    return {"findings": findings, "notes": notes, "pairs": pairs, "first_second": fs,
            "scenes": len(scenes)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Advisory continuity gate (morph-over-cut, first-second anchor).")
    ap.add_argument("plan", help="scene plan JSON (see module docstring)")
    ap.add_argument("--trace", action="append", default=[], help="scene trace JSON, in scene order")
    ap.add_argument("--kit", help="kit JSON / design_system.json / runtime export, for the vocabulary notes")
    a = ap.parse_args(argv)
    p = Path(a.plan)
    if not p.exists():
        emit({"gate": GATE, "status": "SKIPPED", "summary": f"no scene plan at {p} — nothing to check"})
        return EXIT_SKIPPED
    try:
        plan = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(plan, list):
            plan = {"scenes": plan}
        traces = [json.loads(Path(t).read_text(encoding="utf-8")) for t in a.trace]
        vocab = kit_vocabulary(a.kit) if a.kit else None
    except (OSError, ValueError) as ex:
        emit({"gate": GATE, "status": "ERROR", "summary": f"could not read inputs: {type(ex).__name__}: {ex}"})
        return EXIT_ERROR
    if not plan.get("scenes"):
        emit({"gate": GATE, "status": "SKIPPED", "summary": "the plan has no scenes — nothing to check"})
        return EXIT_SKIPPED
    r = check(plan, traces, vocab)
    fs = r["first_second"]
    first = (f"first visible at {fs['first_visible_t']}s" if fs["first_visible_t"] is not None
             else ("first second EMPTY" if fs["measured"] else "first second NOT measured"))
    emit({"gate": GATE, "status": "REVIEW" if r["findings"] else "PASS", **r,
          "summary": (f"{len(r['findings'])} continuity issue(s) over {r['scenes']} scene(s); {first}"
                      if r["findings"] else
                      f"{r['scenes']} scene(s): no cut over a persisting element; {first}")})
    return EXIT_REVIEW if r["findings"] else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
