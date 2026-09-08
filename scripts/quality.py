#!/usr/bin/env python3
"""
Coordn8 — QUALITY OBSERVABILITY: measured, per-scene, per-dimension output quality,
assessed in PARALLEL as scenes land.

WHY THIS EXISTS
---------------
The pipeline already had gates, but they were **terminal pass/fail over the whole
build**. That leaves three blind spots that repeatedly cost real review cycles:

  1. NO MAGNITUDE. "voice sync: FAIL" does not say whether a visual is 40 ms or 2 s
     off its sentence — and those need completely different fixes. A bare verdict is
     not an observation.
  2. NO PER-SCENE VIEW. One weak scene hides inside a build-level PASS, so nobody
     knows *which* scene to fix or which is trending worse.
  3. NOT MEASURED UNTIL THE END. Quality was judged after everything was rendered, so
     a systematic defect cost a full re-render instead of being caught on scene 1.

This module measures six dimensions from artifacts that already exist (the
instrumentation trace + the beat map), scores each 0-100 with the WORST OFFENDER
named, and fans the work out across the adaptive pool so the whole assessment is
parallel and fast:

  alignment   safe-area compliance, shared edges, grid snapping — "does it look laid
              out, or scattered?"
  overlap     partially-overlapping panels and colliding legible text
  voice_sync  per spoken line, the DRIFT in milliseconds between the sentence and
              when its visual actually arrives; lag is a defect, a small lead is craft
  density     ink per horizontal band per spoken line — the measured "low effort" look
  legibility  smallest rendered type and smallest asset scale
  motion      moving elements under narration; a static hold while speaking is a defect

Scores are a MEASUREMENT SUMMARY, not a quality certificate. A perfect 100 means
"nothing measurable is wrong in these six ways" — it never means the video is good.
Only Stage V (blind frame-vs-sentence review) or a human can say that, and this module
says so in its own output so a green scorecard can never be mistaken for a verdict.

Usage:
    python3 scripts/quality.py <build>                     # scorecard for a build
    python3 scripts/quality.py <build> --json
    python3 scripts/quality.py --trace t.json --beatmap b.json
    python3 scripts/quality.py <build> --prev <prev_build>  # trend vs the last cut
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "render"))
from render.parallel import run_pool  # noqa: E402
from render.telemetry import Run  # noqa: E402

# ---- thresholds: every one is a MEASURED limit with a stated reason ----------
SAFE_PCT = 0.05           # 5% safe area on every edge (broadcast convention)
VISIBLE = 0.25            # alpha above which an element is legible
INK = 0.18
MIN_FONT_PX = 28.0        # body/caption floor at 1080p
MIN_TITLE_PX = 44.0
MIN_PANEL_SCALE = 0.42    # below this, real UI text in a screenshot is unreadable
BAND_PX = 180
CAPTION_BAND_PX = 180
DEAD_BAND_PCT = 1.0
MAX_DEAD_RUN = 2
GRID_PX = 4               # elements should snap to a small grid, not float freely
LEAD_OK_MS = 150.0        # a visual may settle up to ~150ms BEFORE its key word
LAG_WARN_MS = 250.0       # arriving later than this reads as lag
ALIGN_EDGE_TOL = 2.0      # px tolerance when deciding two edges are "shared"

DIMENSIONS = ("alignment", "overlap", "voice_sync", "density", "legibility", "motion")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _vis(boxes, a=VISIBLE):
    return [b for b in boxes if (b.get("alpha") or 0) > a]


def _inter(a, b) -> float:
    ix, iy = max(a["x"], b["x"]), max(a["y"], b["y"])
    ax = min(a["x"] + a["w"], b["x"] + b["w"])
    ay = min(a["y"] + a["h"], b["y"] + b["h"])
    w, h = ax - ix, ay - iy
    return w * h if (w > 0 and h > 0) else 0.0


def _area(b) -> float:
    return max(0.0, b["w"]) * max(0.0, b["h"])


def _contains(outer, inner, m=6.0) -> bool:
    return (inner["x"] >= outer["x"] - m and inner["y"] >= outer["y"] - m
            and inner["x"] + inner["w"] <= outer["x"] + outer["w"] + m
            and inner["y"] + inner["h"] <= outer["y"] + outer["h"] + m)


def _score(bad: int, sampled: int, *, per_hit: float = 12.0) -> float:
    """Turn a defect count into a 0-100 score. Bounded, monotonic, and explainable:
    each distinct offending item costs a fixed amount, so a score maps back to a
    countable reality rather than being an opaque number."""
    if sampled <= 0:
        return 100.0
    return max(0.0, round(100.0 - per_hit * bad, 1))


def _frame_ink(frame: dict, width: int, height: int) -> float:
    """Declared-area ink for one frame, 0..1 of the content region."""
    nb = max(1, (height - CAPTION_BAND_PX) // BAND_PX)
    content_h = nb * BAND_PX
    total = 0.0
    for b in _vis(frame.get("boxes") or [], INK):
        y0, y1 = max(0.0, b["y"]), min(float(content_h), b["y"] + b["h"])
        if y1 > y0:
            total += max(0.0, b["w"]) * (y1 - y0) * min(1.0, b.get("alpha") or 0)
    return total / float(width * content_h or 1)


def _bands(frame: dict, width: int, height: int) -> list[float]:
    nb = max(1, (height - CAPTION_BAND_PX) // BAND_PX)
    cover = [0.0] * nb
    for b in _vis(frame.get("boxes") or [], INK):
        for i in range(nb):
            by0, by1 = i * BAND_PX, (i + 1) * BAND_PX
            oy = min(b["y"] + b["h"], by1) - max(b["y"], by0)
            if oy > 0:
                cover[i] += max(0.0, b["w"]) * oy * min(1.0, b.get("alpha") or 0)
    band_area = width * BAND_PX
    return [round(min(100.0, 100.0 * c / band_area), 1) for c in cover]


# ---------------------------------------------------------------------------
# the six measured dimensions
# ---------------------------------------------------------------------------
def dim_alignment(trace: dict) -> dict:
    """Safe-area compliance, shared edges, grid snapping.

    "Ragged" is a real complaint and it is measurable: elements that share no edge
    with anything else and sit off-grid read as scattered even when nothing overlaps.
    """
    W, H = trace.get("width", 1920), trace.get("height", 1080)
    mx, my = W * SAFE_PCT, H * SAFE_PCT
    out_of_safe, off_grid, unaligned, sampled = [], 0, 0, 0

    for fr in trace.get("frames") or []:
        boxes = _vis(fr.get("boxes") or [])
        if not boxes:
            continue
        sampled += 1
        lefts, tops = {}, {}
        for b in boxes:
            if (b["x"] < mx - 0.5 or b["y"] < my - 0.5
                    or b["x"] + b["w"] > W - mx + 0.5
                    or b["y"] + b["h"] > H - my + 0.5):
                out_of_safe.append(
                    f"t={fr.get('t')}s {b.get('kind')} "
                    f"[{b['x']:.0f},{b['y']:.0f} {b['w']:.0f}x{b['h']:.0f}] "
                    f"crosses the {SAFE_PCT * 100:.0f}% safe area")
            if abs(b["x"] % GRID_PX) > 0.51 or abs(b["y"] % GRID_PX) > 0.51:
                off_grid += 1
            lefts.setdefault(round(b["x"] / ALIGN_EDGE_TOL), []).append(b)
            tops.setdefault(round(b["y"] / ALIGN_EDGE_TOL), []).append(b)
        # An element sharing NO left and NO top edge with any sibling is floating.
        for b in boxes:
            if (len(lefts.get(round(b["x"] / ALIGN_EDGE_TOL), [])) < 2
                    and len(tops.get(round(b["y"] / ALIGN_EDGE_TOL), [])) < 2):
                unaligned += 1

    # De-duplicate the safe-area report: the same element across many frames is one
    # authoring defect, not hundreds.
    uniq = sorted({f.split("] ")[0].split("s ", 1)[-1] for f in out_of_safe})
    bad = len(uniq)
    return {
        "dimension": "alignment", "sampled_frames": sampled,
        "score": _score(bad, sampled, per_hit=20.0),
        "metrics": {"safe_area_violations": bad,
                    "off_grid_draws": off_grid,
                    "floating_elements": unaligned,
                    "grid_px": GRID_PX, "safe_pct": SAFE_PCT},
        "worst": uniq[:3],
        "findings": [f"{u} — nothing may cross the safe area" for u in uniq[:6]],
    }


def dim_overlap(trace: dict) -> dict:
    """Partially-overlapping visible panels, and colliding legible text."""
    panel_hits, text_hits, sampled = [], [], 0
    for fr in trace.get("frames") or []:
        boxes = fr.get("boxes") or []
        sampled += 1
        panels = [b for b in _vis(boxes) if b.get("kind") in ("card", "shot")]
        for i in range(len(panels)):
            for j in range(i + 1, len(panels)):
                a, b = panels[i], panels[j]
                ia = _inter(a, b)
                if ia <= 0 or _contains(a, b) or _contains(b, a):
                    continue
                sm = min(_area(a), _area(b))
                if sm > 0 and ia / sm > 0.15:
                    panel_hits.append(f"t={fr.get('t')}s panels overlap "
                                      f"{ia / sm * 100:.0f}%")
        texts = [b for b in _vis(boxes) if b.get("kind") == "text"]
        for i in range(len(texts)):
            for j in range(i + 1, len(texts)):
                a, b = texts[i], texts[j]
                ia = _inter(a, b)
                if ia <= 0:
                    continue
                sm = min(_area(a), _area(b))
                if sm > 0 and ia / sm > 0.18:
                    text_hits.append(
                        f"t={fr.get('t')}s text collision "
                        f"{str(a.get('text'))[:18]!r} x {str(b.get('text'))[:18]!r}")
    bad = len(set(panel_hits)) + len(set(text_hits))
    return {
        "dimension": "overlap", "sampled_frames": sampled,
        "score": _score(bad, sampled, per_hit=10.0),
        "metrics": {"panel_overlap_frames": len(panel_hits),
                    "text_collision_frames": len(text_hits)},
        "worst": (sorted(set(panel_hits))[:2] + sorted(set(text_hits))[:2]),
        "findings": (sorted(set(panel_hits))[:4] + sorted(set(text_hits))[:4]),
    }


def dim_voice_sync(trace: dict) -> dict:
    """Per spoken line, the DRIFT between the sentence and its visual arriving.

    This is the measurement the old pass/fail check lacked. A visual arriving 40 ms
    late and one arriving 2 s late are both "out of sync" to a boolean gate but need
    entirely different fixes, so the magnitude IS the finding. A small LEAD is good
    craft (the element is settling as the word lands); LAG always reads as broken.
    """
    W, H = trace.get("width", 1920), trace.get("height", 1080)
    frames = trace.get("frames") or []
    lines = trace.get("lines") or []
    if not frames or not lines:
        return {"dimension": "voice_sync", "sampled_frames": 0, "score": 100.0,
                "metrics": {}, "worst": [],
                "findings": ["no lines/frames in the trace — voice sync UNMEASURED "
                             "(this is not a pass)"], "unmeasured": True}

    # Detect the arrival of a NEW visual, not a rise in aggregate ink. Aggregate ink
    # is too coarse: on a frame that already holds four full-width cards, a chip
    # entering barely moves the total, so an ink threshold reports "nothing appeared"
    # when something plainly did. Element identity is the honest signal.
    def sig_set(f):
        """IDENTITY only — deliberately excluding position.

        If position were part of the signature, an element that merely MOVES would
        look like a brand-new element on every frame, and a travelling dot would be
        reported as "a new visual appeared" before its own beat. Movement is measured
        by the motion dimension; this set answers only "which things are on screen".
        """
        out = set()
        for b in _vis(f.get("boxes") or [], INK):
            # Stable AUTHORED identity only. Not text (a label may legitimately tick a
            # value every frame) and not position (that is motion).
            out.add((b.get("kind"), b.get("id")))
        for a in f.get("assets") or []:
            if (a.get("alpha") or 0) > INK:
                out.add(("asset", a.get("path")))
        for c in f.get("connectors") or []:
            if (c.get("alpha") or 0) > INK:
                out.add(("conn", c.get("from"), c.get("to")))
        return out

    def vis_count(f):
        """How many things are visible. Immune to motion AND to changing labels, so a
        rise here is unambiguously 'more content arrived'."""
        return (len(_vis(f.get("boxes") or [], INK))
                + sum(1 for a in (f.get("assets") or [])
                      if (a.get("alpha") or 0) > INK)
                + sum(1 for c in (f.get("connectors") or [])
                      if (c.get("alpha") or 0) > INK))

    by_t = {round(float(f.get("t") or 0), 3): f for f in frames}
    ts = sorted(by_t)
    sigs = {t: sig_set(by_t[t]) for t in ts}
    counts = {t: vis_count(by_t[t]) for t in ts}
    drifts, findings = [], []

    for ln in lines:
        start = float(ln.get("start") or 0)
        end = float(ln.get("end") or start)
        pre = [t for t in ts if t < start - 0.02]
        base = sigs[pre[-1]] if pre else set()
        window = [t for t in ts if start - 0.6 <= t <= end]
        base_n = counts[pre[-1]] if pre else 0
        entry = None
        for t in window:
            # Either a newly-identified element, or simply MORE on screen than before
            # this line began. Both mean this beat's visual has arrived.
            if (sigs[t] - base) or counts[t] > base_n:
                entry = t
                break
        if entry is None:
            findings.append(
                f"line {ln.get('idx')} ({ln.get('name') or '?'}): NO new visual "
                f"appears while it is spoken — the narration describes something the "
                f"frame never shows")
            drifts.append(None)
            continue
        drift_ms = (entry - start) * 1000.0
        drifts.append(drift_ms)
        if drift_ms > LAG_WARN_MS:
            findings.append(
                f"line {ln.get('idx')} ({ln.get('name') or '?'}): visual arrives "
                f"{drift_ms:.0f}ms AFTER the sentence starts — lag reads as broken "
                f"(target: land on the key word, up to ~{LEAD_OK_MS:.0f}ms early)")
        elif drift_ms < -LEAD_OK_MS - 400:
            findings.append(
                f"line {ln.get('idx')} ({ln.get('name') or '?'}): visual appears "
                f"{-drift_ms:.0f}ms BEFORE its sentence — a chart on screen before it "
                f"is mentioned reads as a glitch")

    real = [d for d in drifts if d is not None]
    missing = sum(1 for d in drifts if d is None)
    on_beat = sum(1 for d in real if -LEAD_OK_MS - 400 <= d <= LAG_WARN_MS)
    return {
        "dimension": "voice_sync", "sampled_frames": len(frames),
        "score": _score(len(findings), len(lines), per_hit=15.0),
        "metrics": {
            "lines": len(lines),
            "on_beat": on_beat,
            "on_beat_pct": round(100.0 * on_beat / len(lines), 1) if lines else None,
            "median_drift_ms": (round(sorted(real)[len(real) // 2], 1) if real else None),
            "max_lag_ms": (round(max(real), 1) if real else None),
            "max_lead_ms": (round(-min(real), 1) if real and min(real) < 0 else 0.0),
            "lines_with_no_visual": missing,
            "lag_warn_ms": LAG_WARN_MS, "lead_ok_ms": LEAD_OK_MS,
        },
        "worst": findings[:3],
        "findings": findings[:8],
    }


def dim_density(trace: dict) -> dict:
    """Ink per band per spoken line — the measured 'low effort' look."""
    W, H = trace.get("width", 1920), trace.get("height", 1080)
    frames = trace.get("frames") or []
    lines = trace.get("lines") or []
    rows, findings = [], []
    for ln in lines:
        t_at = float(ln.get("start") or 0) + (
            float(ln.get("end") or 0) - float(ln.get("start") or 0)) * 0.85
        best = min(frames, key=lambda f: abs(float(f.get("t") or 0) - t_at),
                   default=None)
        if best is None:
            continue
        bands = _bands(best, W, H)
        run = best_run = 0
        for v in bands:
            run = run + 1 if v < DEAD_BAND_PCT else 0
            best_run = max(best_run, run)
        mean = round(sum(bands) / len(bands), 1) if bands else 0.0
        rows.append({"line": ln.get("idx"), "beat": ln.get("name"),
                     "mean_ink": mean, "bands": bands, "dead_run": best_run})
        if best_run > MAX_DEAD_RUN:
            findings.append(
                f"line {ln.get('idx')} ({ln.get('name') or '?'}): "
                f"{best_run} consecutive dead bands "
                f"(~{best_run * BAND_PX}px empty) while it is spoken")
    rows.sort(key=lambda r: r["mean_ink"])
    return {
        "dimension": "density", "sampled_frames": len(rows),
        "score": _score(len(findings), max(1, len(rows)), per_hit=15.0),
        "metrics": {"beats": len(rows),
                    "weakest_mean_ink": rows[0]["mean_ink"] if rows else None,
                    "median_mean_ink": (rows[len(rows) // 2]["mean_ink"]
                                        if rows else None)},
        "worst": [f"line {r['line']} ({r['beat']}) mean ink {r['mean_ink']}%"
                  for r in rows[:3]],
        "findings": findings[:6],
    }


def dim_legibility(trace: dict) -> dict:
    """Smallest rendered type and smallest asset scale.

    Findings are de-duplicated by the underlying DEFECT (this string at this size),
    not by frame: one undersized caption is one authoring fix, and reporting it 300
    times per scene buries every other finding.
    """
    min_font, min_scale, sampled = None, None, 0
    small_text: dict[tuple, int] = {}
    small_panels: dict[tuple, int] = {}
    for fr in trace.get("frames") or []:
        sampled += 1
        for b in _vis(fr.get("boxes") or []):
            if b.get("kind") == "text" and b.get("size") is not None:
                sz = float(b["size"])
                min_font = sz if min_font is None else min(min_font, sz)
                if sz < MIN_FONT_PX:
                    key = (round(sz, 1), str(b.get("text"))[:40])
                    small_text[key] = small_text.get(key, 0) + 1
        for a in fr.get("assets") or []:
            if (a.get("alpha") or 0) > 0.30 and a.get("scale") is not None:
                sc = float(a["scale"])
                min_scale = sc if min_scale is None else min(min_scale, sc)
                if sc < MIN_PANEL_SCALE:
                    key = (round(sc, 2), str(a.get("path")))
                    small_panels[key] = small_panels.get(key, 0) + 1

    findings = [
        f"text at {sz:.0f}px is under the {MIN_FONT_PX:.0f}px floor "
        f"({n} frame(s)): {txt!r}"
        for (sz, txt), n in sorted(small_text.items())
    ] + [
        f"panel '{path}' renders at {sc:.2f}x, under the {MIN_PANEL_SCALE}x floor "
        f"({n} frame(s)) — real UI text becomes unreadable"
        for (sc, path), n in sorted(small_panels.items())
    ]
    bad = len(small_text) + len(small_panels)
    return {
        "dimension": "legibility", "sampled_frames": sampled,
        "score": _score(bad, max(1, sampled), per_hit=25.0),
        "metrics": {"min_font_px": min_font, "font_floor_px": MIN_FONT_PX,
                    "min_panel_scale": min_scale,
                    "panel_scale_floor": MIN_PANEL_SCALE,
                    "distinct_undersized_text": len(small_text),
                    "distinct_undersized_panels": len(small_panels)},
        "worst": findings[:3], "findings": findings[:6],
    }


def dim_motion(trace: dict) -> dict:
    """Moving elements under narration. A static hold while speaking is a defect."""
    frames = trace.get("frames") or []
    lines = trace.get("lines") or []
    findings, static_lines = [], 0
    for ln in lines:
        start, end = float(ln.get("start") or 0), float(ln.get("end") or 0)
        win = [f for f in frames if start <= float(f.get("t") or 0) <= end]
        if len(win) < 2:
            continue
        sigs = []
        for f in win:
            sigs.append(tuple(sorted(
                (b.get("kind"), round(b["x"], 1), round(b["y"], 1),
                 round(b.get("alpha") or 0, 2))
                for b in _vis(f.get("boxes") or [], INK))))
        if len(set(sigs)) <= 1:
            static_lines += 1
            findings.append(
                f"line {ln.get('idx')} ({ln.get('name') or '?'}): the frame is "
                f"COMPLETELY STATIC for the whole sentence — nothing enters, moves or "
                f"builds while the voice is speaking")
    return {
        "dimension": "motion", "sampled_frames": len(frames),
        "score": _score(static_lines, max(1, len(lines)), per_hit=20.0),
        "metrics": {"lines": len(lines), "static_lines": static_lines,
                    "moving_lines": max(0, len(lines) - static_lines)},
        "worst": findings[:3], "findings": findings[:6],
    }


ASSESSORS = {
    "alignment": dim_alignment, "overlap": dim_overlap,
    "voice_sync": dim_voice_sync, "density": dim_density,
    "legibility": dim_legibility, "motion": dim_motion,
}


# ---------------------------------------------------------------------------
# the parallel fleet
# ---------------------------------------------------------------------------
def assess_build(traces: list[Path], *, dimensions=DIMENSIONS,
                 telemetry_dir: Path | None = None) -> dict:
    """Assess every (scene x dimension) IN PARALLEL on the adaptive pool.

    Six dimensions across N scenes is 6N independent measurements, so it fans out
    perfectly. Running them serially is the difference between a scorecard you consult
    constantly and one you avoid — and a check you avoid is a check that does not
    protect you.
    """
    loaded: dict[str, dict] = {}
    for p in traces:
        try:
            t = json.loads(p.read_text(encoding="utf-8"))
            loaded[t.get("scene") or p.stem] = t
        except Exception as e:  # noqa: BLE001
            loaded[p.stem] = {"__error__": f"{type(e).__name__}: {e}"}

    jobs = [(scene, dim) for scene in loaded for dim in dimensions]

    def work(job):
        scene, dim = job
        tr = loaded[scene]
        if "__error__" in tr:
            return {"scene": scene, "dimension": dim, "score": 0.0,
                    "findings": [f"trace unreadable: {tr['__error__']}"],
                    "metrics": {}, "worst": []}
        res = ASSESSORS[dim](tr)
        res["scene"] = scene
        return res

    telem = None
    if telemetry_dir is not None:
        telem = Run("quality", telemetry_dir, echo=False,
                    meta={"scenes": len(loaded), "dimensions": list(dimensions),
                          "measurements": len(jobs)})
    pool = run_pool(jobs, work, kind="cpu", per_worker_mb=200,
                    log=lambda m: None)
    results = [r for _job, r in pool.done]

    by_scene: dict[str, dict] = {}
    for r in results:
        by_scene.setdefault(r["scene"], {})[r["dimension"]] = r

    scenes = []
    for scene, dims in sorted(by_scene.items()):
        scores = [d["score"] for d in dims.values()]
        weakest = min(dims.values(), key=lambda d: d["score"]) if dims else None
        scenes.append({
            "scene": scene,
            "score": round(sum(scores) / len(scores), 1) if scores else None,
            "weakest_dimension": weakest["dimension"] if weakest else None,
            "dimensions": {k: {"score": v["score"], "metrics": v["metrics"],
                               "worst": v["worst"], "findings": v["findings"]}
                           for k, v in sorted(dims.items())},
        })

    all_scores = [s["score"] for s in scenes if s["score"] is not None]
    findings = [f"{s['scene']}/{d}: {f}"
                for s in scenes for d, v in s["dimensions"].items()
                for f in v["findings"]]
    dim_avg = {}
    for d in dimensions:
        vals = [s["dimensions"][d]["score"] for s in scenes if d in s["dimensions"]]
        if vals:
            dim_avg[d] = round(sum(vals) / len(vals), 1)

    out = {
        "scenes": scenes,
        "scene_count": len(scenes),
        "measurements": len(results),
        "peak_workers": pool.peak_workers,
        "build_score": round(sum(all_scores) / len(all_scores), 1) if all_scores else None,
        "dimension_averages": dim_avg,
        "weakest_scene": (min(scenes, key=lambda s: s["score"] or 101)["scene"]
                          if all_scores else None),
        "weakest_dimension": (min(dim_avg, key=dim_avg.get) if dim_avg else None),
        "findings": findings,
        "not_a_quality_certificate": (
            "These are MEASUREMENTS of six mechanical dimensions. A high score means "
            "'nothing measurable is wrong in these ways' — it is NOT evidence the video "
            "is good. Only Stage V (a blind frame-vs-sentence review) or a human can "
            "say that."),
    }
    if telem:
        telem.finish("ok", build_score=out["build_score"],
                     dimension_averages=dim_avg, findings=len(findings))
        out["telemetry"] = str(telem.summary_path)
    return out


def add_trend(current: dict, prev_path: Path) -> dict:
    """Compare against the previous cut, per scene and per dimension.

    A regression is invisible without a baseline: "scene 4 dropped 22 points on
    density since the last cut" is actionable in a way an absolute number is not.
    """
    try:
        prev = json.loads(prev_path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        current["trend"] = {"error": f"could not read {prev_path}: {e}"}
        return current
    prev_scenes = {s["scene"]: s for s in prev.get("scenes", [])}
    deltas, regressions = [], []
    for s in current.get("scenes", []):
        p = prev_scenes.get(s["scene"])
        if not p or p.get("score") is None or s.get("score") is None:
            continue
        d = round(s["score"] - p["score"], 1)
        deltas.append({"scene": s["scene"], "delta": d,
                       "from": p["score"], "to": s["score"]})
        if d <= -5.0:
            regressions.append(
                f"{s['scene']}: quality score fell {abs(d)} points "
                f"({p['score']} -> {s['score']}) since the previous cut")
        for dim, v in s.get("dimensions", {}).items():
            pv = (p.get("dimensions") or {}).get(dim)
            if pv and pv.get("score") is not None:
                dd = round(v["score"] - pv["score"], 1)
                if dd <= -10.0:
                    regressions.append(
                        f"{s['scene']}/{dim}: dropped {abs(dd)} points "
                        f"({pv['score']} -> {v['score']}) — an unrequested regression")
    pb, cb = prev.get("build_score"), current.get("build_score")
    current["trend"] = {
        "build_score_from": pb, "build_score_to": cb,
        "build_delta": (round(cb - pb, 1) if (pb is not None and cb is not None)
                        else None),
        "per_scene": deltas, "regressions": regressions,
    }
    current["findings"] = current.get("findings", []) + regressions
    return current


def find_traces(build: Path) -> list[Path]:
    hits = sorted(p for p in build.glob("_build/trace*.json") if p.is_file())
    hits += sorted(p for p in build.glob("_build/traces/*.json") if p.is_file())
    hits += sorted(p for p in build.glob("trace*.json") if p.is_file())
    seen, out = set(), []
    for h in hits:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Coordn8 quality observability.")
    ap.add_argument("build", nargs="?", default=None)
    ap.add_argument("--trace", action="append", default=[])
    ap.add_argument("--beatmap", default=None,
                    help="unused when the trace already carries lines[]")
    ap.add_argument("--prev", default=None,
                    help="a previous quality.json (or build) to trend against")
    ap.add_argument("--dimensions", default=",".join(DIMENSIONS))
    ap.add_argument("--out", default=None, help="write the scorecard JSON here")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    traces = [Path(p) for p in args.trace]
    build = Path(args.build) if args.build else None
    if build and not traces:
        traces = find_traces(build)
    traces = [p for p in traces if p.exists()]
    if not traces:
        print(json.dumps({
            "status": "SKIPPED",
            "summary": "no instrumentation traces found — quality is UNMEASURED, which "
                       "is not a pass. Build a trace with scene_runtime.js buildTrace."},
            indent=2))
        return 2

    dims = tuple(d.strip() for d in args.dimensions.split(",")
                 if d.strip() in ASSESSORS)
    telem_dir = (build / "_telemetry") if build else None
    report = assess_build(traces, dimensions=dims or DIMENSIONS,
                          telemetry_dir=telem_dir)

    if args.prev:
        prev = Path(args.prev)
        if prev.is_dir():
            prev = prev / "_build" / "quality.json"
        report = add_trend(report, prev)

    out_path = Path(args.out) if args.out else (
        build / "_build" / "quality.json" if build else None)
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        report["written_to"] = str(out_path)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("Coordn8 quality scorecard")
        print("=" * 78)
        print(f"  build score      {report['build_score']}/100   "
              f"({report['scene_count']} scene(s), {report['measurements']} "
              f"measurements, {report['peak_workers']} parallel workers)")
        print(f"  weakest scene    {report['weakest_scene']}")
        print(f"  weakest aspect   {report['weakest_dimension']}")
        print("-" * 78)
        hdr = "  scene".ljust(22) + "score  " + "  ".join(
            d[:9].ljust(9) for d in (dims or DIMENSIONS))
        print(hdr)
        for s in report["scenes"]:
            row = f"  {s['scene'][:20]:<20}{str(s['score']):>5}  "
            row += "  ".join(
                f"{s['dimensions'][d]['score']:>9.0f}" if d in s["dimensions"] else
                " " * 9 for d in (dims or DIMENSIONS))
            print(row)
        if report.get("trend"):
            t = report["trend"]
            print("-" * 78)
            print(f"  trend vs previous cut: {t.get('build_score_from')} -> "
                  f"{t.get('build_score_to')} (delta {t.get('build_delta')})")
            for r in t.get("regressions", [])[:5]:
                print(f"    REGRESSION {r}")
        print("-" * 78)
        if report["findings"]:
            print(f"  {len(report['findings'])} finding(s); first few:")
            for f in report["findings"][:8]:
                print(f"    - {f[:150]}")
        else:
            print("  no findings across the measured dimensions")
        print("=" * 78)
        print("  NOTE: " + report["not_a_quality_certificate"])

    # One-line stderr summary, matching the gate protocol so the gate runner and the
    # orchestrator can surface this alongside every other check.
    n = len(report["findings"])
    sys.stderr.write(
        "[coordn8:quality] {}: build score {}/100 across {} scene(s); "
        "weakest scene {} ({}); {} finding(s). A score is a MEASUREMENT, never a "
        "quality certificate.\n".format(
            "REVIEW" if n else "PASS", report["build_score"], report["scene_count"],
            report["weakest_scene"], report["weakest_dimension"], n))
    return 1 if report["findings"] else 0


if __name__ == "__main__":
    sys.exit(main())
