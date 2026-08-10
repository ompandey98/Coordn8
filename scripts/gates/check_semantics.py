#!/usr/bin/env python3
"""
Coordn8 gate — SEMANTIC GATES S1–S8 (the checks geometry cannot do).

THE DIAGNOSIS THIS IMPLEMENTS
-----------------------------
Every pre-existing Coordn8 check measures GEOMETRY and PRESENCE. Every complaint a
human makes is about MEANING. A frame can be geometrically perfect and semantically
wrong, and the suite calls it PASS — that is how a build shipped ten real defects
with a fully green board.

Geometry answers: do two boxes overlap? is a panel too small? is the frame empty?
It cannot answer: does this frame show what the voice is saying? did this section
lose the visual it used to have? does this arrow point the right way? is this the
right evidence? is this text spilling out of its own container?

These gates close that gap, reading the INSTRUMENTATION TRACE emitted by
`scripts/render/scene_runtime.js` (per frame: boxes with kind/x/y/w/h/alpha,
connector polylines with from/to/arrowAt, asset draws with path/crop/subjectId).

  S1  non-finite alpha    an element with NaN/undefined alpha renders NOTHING,
                          silently — no error, no warning, no log. This is what
                          left an entire section blank while the narration
                          described its contents out loud.
  S2  raw boundary index  a raw B[i]-style index that can exceed the built array
                          (the source of the S1 NaN). Static scan; comments stripped.
  S3  child-escape        a text box crossing the bounds of the container it is
                          drawn INSIDE. The overlap gate compares two SEPARATE
                          boxes, so a child escaping its own parent is invisible.
  S4  text-vs-connector   a visible text box sitting on a drawn connector segment.
                          A connector is not a box, so the overlap gate cannot see it.
  S5  asset<->subject     a beat's subjectId != the subjectId of an asset it draws:
                          a real, legible, well-cropped panel about the WRONG example.
  S6  flow-direction      the drawn path's endpoints don't match the declared
                          from->to, or the arrowhead is at the wrong end. No
                          geometry gate has any concept of direction.
  S7  section-presence    a DECLARED section renders with no content of its own
                          (per-section, not per-frame) — "this whole section is empty".
  S8  previous-cut diff   a section's ink drops sharply vs the last approved cut
                          with no instruction to change it — an unrequested regression.
                          (S7+S8 are the pair that catches an emptied section on the FIRST run.)

ADVISORY (same protocol as every Coordn8 gate): a finding is a REVIEW signal, not a
hard veto — EXCEPT that S1/S6/S5 findings are the classes that shipped visible,
credibility-destroying defects, so they are reported as BLOCKING in the summary.

Usage:
    python3 scripts/gates/check_semantics.py <trace.json> [more_traces.json ...]
    python3 scripts/gates/check_semantics.py trace.json --scene-src video/scene1.js
    python3 scripts/gates/check_semantics.py trace.json --prev-trace prev/trace.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.common import (EXIT_ERROR, EXIT_PASS, EXIT_REVIEW,  # noqa: E402
                        EXIT_SKIPPED, emit, load_json)

VISIBLE = 0.25          # alpha above which an element is "clearly visible"
INK = 0.18              # alpha above which an element counts as content/ink
ESCAPE_MARGIN = 4.0     # px of tolerance before a child counts as escaping
CLEARANCE = 3.0         # px clearance required between text and a connector
S8_DROP = 0.40          # a section losing >40% of its ink vs the approved cut


# ---------------------------------------------------------------------------
# S2 — static scan of the scene SOURCE for raw boundary indexing.
# ---------------------------------------------------------------------------
def strip_comments(src: str) -> str:
    """Comments must go first: prose about `B[9]` otherwise creates false hits."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(re.sub(r"//.*$", "", ln) for ln in src.split("\n"))


def s2_raw_index(source: str, n_lines: int) -> list[str]:
    """Raw B[i]/bounds[i] indexing that can run past the boundary array.

    The boundary array is built as `n_lines` starts + 1 trailing end, so valid
    indices are 0..n_lines. Anything above that yields undefined -> NaN timing ->
    a silently invisible element."""
    hits = []
    src = strip_comments(source)
    for m in re.finditer(r"\b(?:B|bounds|BOUNDS|beats)\[\s*(\d+)\s*\]", src):
        i = int(m.group(1))
        if i > n_lines:
            hits.append(f"{m.group(0)} exceeds the boundary array "
                        f"(valid 0..{n_lines} for {n_lines} narration lines) — "
                        f"out-of-range indexing yields NaN timing and a silently "
                        f"invisible element")
    # Also flag helper calls with a literal index past the line count (I1's sibling).
    for m in re.finditer(r"\b(?:beat|beatAlpha|beatProgress|lineIn|lineStart|lineEnd)"
                         r"\(\s*(\d+)\s*[,)]", src):
        i = int(m.group(1))
        if i >= n_lines:
            hits.append(f"{m.group(0)}… index {i} is out of range "
                        f"(valid 0..{n_lines - 1}) — prefer a NAMED beat")
    return hits


# ---------------------------------------------------------------------------
# geometry helpers
# ---------------------------------------------------------------------------
def _escapes(child: dict, parent: dict, m: float = ESCAPE_MARGIN) -> float:
    """How far `child` sticks out of `parent` (0 when fully contained)."""
    return max(
        parent["x"] - child["x"],
        child["x"] + child["w"] - (parent["x"] + parent["w"]),
        parent["y"] - child["y"],
        child["y"] + child["h"] - (parent["y"] + parent["h"]),
    ) - m


def _seg_box_distance(p1: dict, p2: dict, box: dict) -> float:
    """Min distance between segment p1->p2 and an axis-aligned box (0 = crosses)."""
    # Sample the segment; exact segment/AABB distance is overkill for a gate whose
    # job is to flag "the text is sitting on the line".
    steps = 24
    best = float("inf")
    bx0, by0 = box["x"], box["y"]
    bx1, by1 = box["x"] + box["w"], box["y"] + box["h"]
    for i in range(steps + 1):
        u = i / steps
        x = p1["x"] + (p2["x"] - p1["x"]) * u
        y = p1["y"] + (p2["y"] - p1["y"]) * u
        dx = max(bx0 - x, 0.0, x - bx1)
        dy = max(by0 - y, 0.0, y - by1)
        best = min(best, (dx * dx + dy * dy) ** 0.5)
        if best == 0.0:
            return 0.0
    return best


def _center(b: dict) -> tuple[float, float]:
    return b["x"] + b["w"] / 2, b["y"] + b["h"] / 2


def _near(p: dict, box: dict, tol: float = 0.0) -> bool:
    """Is point p on/inside box (with tolerance)? Used for endpoint matching."""
    return (box["x"] - tol <= p["x"] <= box["x"] + box["w"] + tol and
            box["y"] - tol <= p["y"] <= box["y"] + box["h"] + tol)


# ---------------------------------------------------------------------------
# the gates
# ---------------------------------------------------------------------------
def run_gates(trace: dict, source: str | None, prev: dict | None) -> dict:
    scene = trace.get("scene", "?")
    frames = trace.get("frames") or []
    lines = trace.get("lines") or []
    declared = trace.get("declaredSections") or []

    f = {k: [] for k in ("S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8")}

    # ---- S2 (static, on the source) ----
    if source is not None:
        f["S2"] = s2_raw_index(source, len(lines))

    # Per-frame node geometry for S6 (nodes are recorded boxes with an id).
    for fr in frames:
        t = fr.get("t", 0.0)
        boxes = fr.get("boxes") or []
        nodes = {b["id"]: b for b in boxes if b.get("id")}

        # ---- S1: non-finite alpha -> the element draws NOTHING, silently ----
        for b in boxes:
            a = b.get("alpha")
            if a is None or not isinstance(a, (int, float)) or a != a:  # NaN check
                f["S1"].append(f"t={t:.2f}s {b.get('kind')} alpha={a!r} "
                               f"text={str(b.get('text'))[:36]!r} — renders nothing, no error")

        vis_text = [b for b in boxes
                    if b.get("kind") == "text" and (b.get("alpha") or 0) > VISIBLE]

        # ---- S3: a child escaping the container it is drawn inside ----
        by_id = {b.get("id"): b for b in boxes if b.get("id")}
        for tx in vis_text:
            pid = tx.get("parent")
            if not pid:
                continue
            parent = by_id.get(pid)
            if not parent:
                continue
            over = _escapes(tx, parent)
            if over > 0:
                f["S3"].append(
                    f"t={t:.2f}s text escapes its own container '{pid}' by {over:.0f}px "
                    f"(text[{tx['x']:.0f},{tx['y']:.0f} {tx['w']:.0f}x{tx['h']:.0f}] vs "
                    f"card[{parent['x']:.0f},{parent['y']:.0f} "
                    f"{parent['w']:.0f}x{parent['h']:.0f}]) "
                    f"{'— UNBOUNDED opt-in was used' if tx.get('unbounded') else ''}")

        # ---- S4: visible text sitting on a drawn connector ----
        # A node's OWN label legitimately lives inside that node, and a connector
        # touches its endpoint nodes by definition — so a label belonging to either
        # endpoint is excluded. What we are hunting is unrelated text (a stat tile, a
        # number, a caption) landing on top of a line passing through its band.
        for cn in (fr.get("connectors") or []):
            if (cn.get("alpha") or 0) <= VISIBLE:
                continue
            pts = cn.get("points") or []
            endpoint_ids = {cn.get("from"), cn.get("to")}
            for tx in vis_text:
                if tx.get("parent") in endpoint_ids:
                    continue  # this node's own label
                hit = None
                for i in range(len(pts) - 1):
                    d = _seg_box_distance(pts[i], pts[i + 1], tx)
                    if d < CLEARANCE:
                        hit = d
                        break
                if hit is not None:
                    f["S4"].append(
                        f"t={t:.2f}s text {str(tx.get('text'))[:28]!r} sits on connector "
                        f"{cn.get('from')}->{cn.get('to')} (clearance {hit:.1f}px < {CLEARANCE})")

        # ---- S5: the asset's subject must be the beat's subject ----
        speaking = fr.get("speaking")
        beat_subject = (speaking or {}).get("subjectId")
        for a in (fr.get("assets") or []):
            if (a.get("alpha") or 0) <= VISIBLE:
                continue
            asub = a.get("subjectId")
            if beat_subject and asub and asub != beat_subject:
                f["S5"].append(
                    f"t={t:.2f}s asset '{a.get('path')}' shows subject '{asub}' while the "
                    f"spoken line (beat {speaking.get('name') or speaking.get('idx')}) is about "
                    f"'{beat_subject}' — a confident visual contradicting the voice")

        # ---- S6: direction — endpoints and arrowhead vs the declaration ----
        for cn in (fr.get("connectors") or []):
            if (cn.get("alpha") or 0) <= VISIBLE:
                continue
            src_node, dst_node = nodes.get(cn.get("from")), nodes.get(cn.get("to"))
            pts = cn.get("points") or []
            if not (src_node and dst_node and len(pts) >= 2):
                continue
            first, last = pts[0], pts[-1]
            tol = 24.0
            forward = _near(first, src_node, tol) and _near(last, dst_node, tol)
            backward = _near(first, dst_node, tol) and _near(last, src_node, tol)
            if backward and not forward:
                f["S6"].append(
                    f"t={t:.2f}s connector declares {cn.get('from')}->{cn.get('to')} but the drawn "
                    f"path runs {cn.get('to')}->{cn.get('from')} — the animation contradicts its "
                    f"own arrow and the sentence describing the direction")
            elif not forward and not backward:
                f["S6"].append(
                    f"t={t:.2f}s connector {cn.get('from')}->{cn.get('to')} endpoints match neither "
                    f"node (first=[{first['x']:.0f},{first['y']:.0f}], "
                    f"last=[{last['x']:.0f},{last['y']:.0f}])")
            if cn.get("arrowAt") not in ("to", "from"):
                f["S6"].append(f"t={t:.2f}s connector {cn.get('from')}->{cn.get('to')} has "
                               f"arrowAt={cn.get('arrowAt')!r} (expected 'to' or 'from')")
            elif cn.get("arrowAt") == "from" and forward:
                f["S6"].append(
                    f"t={t:.2f}s connector {cn.get('from')}->{cn.get('to')} draws its arrowhead at "
                    f"the 'from' end while flowing forward — arrowhead points backwards")

    # ---- S7: every DECLARED section must actually render content of its own ----
    # Per-section over the whole scene, not per-frame: a section that never draws
    # anything anywhere is the "this whole section is empty" defect.
    ink_by_section: dict[str, float] = {}
    draws_by_section: dict[str, int] = {}
    for fr in frames:
        for s in (fr.get("sections") or []):
            ink_by_section[s["name"]] = max(ink_by_section.get(s["name"], 0.0),
                                            float(s.get("inkArea") or 0))
            draws_by_section[s["name"]] = (draws_by_section.get(s["name"], 0)
                                           + int(s.get("drawCount") or 0))
    for name in declared:
        if draws_by_section.get(name, 0) == 0 or ink_by_section.get(name, 0.0) <= 0.0:
            f["S7"].append(
                f"section '{name}' is DECLARED but never renders content of its own "
                f"(draws={draws_by_section.get(name, 0)}, ink={ink_by_section.get(name, 0.0):.0f}) "
                f"— the narration will describe an empty region")

    # ---- S8: unrequested regression vs the previous approved cut ----
    if prev:
        prev_ink: dict[str, float] = {}
        for fr in (prev.get("frames") or []):
            for s in (fr.get("sections") or []):
                prev_ink[s["name"]] = max(prev_ink.get(s["name"], 0.0),
                                          float(s.get("inkArea") or 0))
        for name, before in prev_ink.items():
            if before <= 0:
                continue
            now = ink_by_section.get(name, 0.0)
            if now < before * (1.0 - S8_DROP):
                drop = (1.0 - now / before) * 100.0
                f["S8"].append(
                    f"section '{name}' lost {drop:.0f}% of its ink vs the previous cut "
                    f"({before:.0f} -> {now:.0f}) with no instruction to change it — "
                    f"ask 'what used to be here?'")
        for name in prev_ink:
            if name not in ink_by_section and name not in declared:
                f["S8"].append(f"section '{name}' existed in the previous cut and is now GONE")

    return {"scene": scene, "findings": f}


def main() -> int:
    ap = argparse.ArgumentParser(description="Coordn8 semantic gates S1-S8.")
    ap.add_argument("traces", nargs="+", help="instrumentation trace JSON file(s)")
    ap.add_argument("--scene-src", action="append", default=[],
                    help="scene source file(s) for the S2 static scan")
    ap.add_argument("--prev-trace", default=None,
                    help="previous approved cut's trace, for the S8 regression diff")
    args = ap.parse_args()

    traces = [Path(p) for p in args.traces]
    missing = [str(p) for p in traces if not p.exists()]
    if missing:
        emit({"gate": "semantics", "status": "SKIPPED",
              "summary": f"missing trace(s): {', '.join(missing)} — run the scene runtime "
                         f"with instrumentation first (scene_runtime.js buildTrace)"})
        return EXIT_SKIPPED

    prev = None
    if args.prev_trace and Path(args.prev_trace).exists():
        try:
            prev = load_json(Path(args.prev_trace))
        except Exception as e:  # noqa: BLE001
            emit({"gate": "semantics", "status": "ERROR",
                  "summary": f"could not read --prev-trace: {type(e).__name__}: {e}"})
            return EXIT_ERROR

    sources = []
    for p in args.scene_src:
        pp = Path(p)
        if pp.exists():
            sources.append(pp.read_text(encoding="utf-8"))
    source_blob = "\n".join(sources) if sources else None

    per_scene, totals = [], {k: 0 for k in ("S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8")}
    for tp in traces:
        try:
            trace = load_json(tp)
        except Exception as e:  # noqa: BLE001
            emit({"gate": "semantics", "status": "ERROR",
                  "summary": f"could not parse {tp}: {type(e).__name__}: {e}"})
            return EXIT_ERROR
        res = run_gates(trace, source_blob, prev)
        for k, v in res["findings"].items():
            totals[k] += len(v)
        per_scene.append(res)

    # S1/S5/S6 are the classes that shipped visible, credibility-destroying defects.
    blocking = totals["S1"] + totals["S5"] + totals["S6"]
    n = sum(totals.values())

    emit({
        "gate": "semantics",
        "status": "REVIEW" if n else "PASS",
        "scenes": [r["scene"] for r in per_scene],
        "counts": totals,
        "blocking_count": blocking,
        "findings": {r["scene"]: {k: v for k, v in r["findings"].items() if v}
                     for r in per_scene if any(r["findings"].values())},
        # Claim-discipline rule 2 (F.7): a gate result is stated together with the fact
        # that passing is NOT evidence of quality — every time, not only when clean.
        # "Ten gates pass" was true while ten real defects shipped.
        "gate_pass_is_not_quality": (
            "These gates measure MEANING-adjacent structure, not quality. Passing them "
            "means 'not provably broken in these specific mechanical ways' — it is NOT "
            "evidence the video is good. Only Stage V (blind frame-vs-sentence review) "
            "or a human can say that."),
        "gate_meanings": {
            "S1": "non-finite alpha — element renders nothing, silently",
            "S2": "raw boundary index past the array (source of S1)",
            "S3": "text escaping the container it is drawn inside",
            "S4": "text sitting on a drawn connector",
            "S5": "asset shows a different subject than the beat is about",
            "S6": "drawn direction/arrowhead contradicts the declared from->to",
            "S7": "a declared section renders no content of its own",
            "S8": "a section lost ink vs the previous approved cut",
        },
        "summary": (
            f"{n} semantic finding(s) across {len(per_scene)} scene(s) "
            f"({blocking} in the BLOCKING classes S1/S5/S6 — silently-invisible element, "
            f"wrong-subject asset, wrong direction). These measure MEANING, not geometry."
            if n else
            f"no semantic findings across {len(per_scene)} scene(s). NOTE: passing these "
            f"gates means 'not provably broken in these specific mechanical ways' — it is "
            f"NOT evidence the video is good. Only Stage V (blind frame-vs-sentence review) "
            f"or a human can say that."),
    })
    return EXIT_REVIEW if n else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
