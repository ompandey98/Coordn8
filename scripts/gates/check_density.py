#!/usr/bin/env python3
"""
Coordn8 gate — DENSITY (DEN): turns "this looks low-effort" into a NUMBER.

WHY THIS GATE EXISTS
--------------------
Every other gate is about correctness (overlap, sync, legibility, presence) and all of
them PASS on a frame whose bottom half is completely empty. A real build shipped scenes
with 540px — half the frame height — at literally 0.0% ink for whole beats, and one beat
at 1.8% mean ink. The reviewer's verdict was that it looked machine-generated and
effortless. "Looks low-effort" had never been measurable, so it was never caught.

WHAT IT MEASURES
----------------
Ink coverage per horizontal band, per spoken line. A band is DEAD when almost none of
its pixels differ from the background floor. A beat FAILS when >= MAX_DEAD_RUN+1
consecutive content bands are dead (~540px of void at 1080p).

It also prints a MEAN-INK TABLE that RANKS scenes/beats. That ranking matters
independently of pass/fail: the weakest-ranked beat is almost always the next thing a
reviewer complains about, so the table is a to-do list, not just a verdict.

TWO INPUT MODES (so this runs with or without a renderer)
--------------------------------------------------------
  frames  — measure real rendered PNG/PPM frames (the honest, pixel-true mode).
            Uses PIL+numpy when available; falls back to a dependency-free pure-Python
            PNG/PPM reader (slower, still exact) so a machine without PIL still gets a
            real measurement instead of a skip.
  trace   — measure the instrumentation trace from scene_runtime.js: band coverage is
            computed from recorded box geometry x alpha. This needs NO renderer at all
            and catches the same "half the frame is empty" defect at authoring time —
            BEFORE spending minutes on a render. It is an approximation of ink (it
            counts declared boxes, not pixels) and is labelled as such in the output.

ADVISORY: findings are REVIEW signals, never a silent veto.

Usage:
    python3 scripts/gates/check_density.py trace _build/trace_s1.json
    python3 scripts/gates/check_density.py frames _build/frames/s1 --beatmap _build/beatmap.json
"""
from __future__ import annotations

import argparse
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.common import (EXIT_ERROR, EXIT_PASS, EXIT_REVIEW,  # noqa: E402
                        EXIT_SKIPPED, emit, load_json)

BAND_PX = 180           # band height at 1080p
CAPTION_BAND_PX = 180   # the bottom band is the burned-caption region; excluded
DEAD_PCT = 1.0          # a band with <1% ink is dead
MAX_DEAD_RUN = 2        # 3+ consecutive dead content bands = fail (~540px of void)
SAMPLE_AT = 0.85        # measure 85% through a line (entrances settled)


# ---------------------------------------------------------------------------
# Dependency-free image reading. PIL+numpy are used when present (fast path);
# otherwise we decode PNG ourselves so the gate still measures real pixels.
# ---------------------------------------------------------------------------
def _read_png_gray(path: Path) -> tuple[int, int, list[int]] | None:
    """Minimal PNG -> (w, h, grayscale rows flattened). Handles the 8-bit
    truecolor/gray/alpha forms ffmpeg and Chrome emit. Returns None if unsupported."""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    pos, w, h, bitdepth, color = 8, 0, 0, 8, 6
    idat = bytearray()
    while pos + 8 <= len(data):
        ln = struct.unpack(">I", data[pos:pos + 4])[0]
        typ = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + ln]
        if typ == b"IHDR":
            w, h, bitdepth, color = struct.unpack(">IIBB", chunk[:10])
        elif typ == b"IDAT":
            idat += chunk
        elif typ == b"IEND":
            break
        pos += 12 + ln
    if not w or not h or bitdepth != 8:
        return None
    nch = {0: 1, 2: 3, 4: 2, 6: 4}.get(color)
    if nch is None:
        return None
    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error:
        return None
    stride = w * nch
    out: list[int] = []
    prev = bytearray(stride)
    i = 0
    for _y in range(h):
        if i >= len(raw):
            break
        ft = raw[i]; i += 1
        line = bytearray(raw[i:i + stride]); i += stride
        if len(line) < stride:
            break
        # PNG filter reconstruction
        if ft == 1:
            for x in range(nch, stride):
                line[x] = (line[x] + line[x - nch]) & 0xFF
        elif ft == 2:
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 0xFF
        elif ft == 3:
            for x in range(stride):
                a = line[x - nch] if x >= nch else 0
                line[x] = (line[x] + ((a + prev[x]) >> 1)) & 0xFF
        elif ft == 4:
            for x in range(stride):
                a = line[x - nch] if x >= nch else 0
                b = prev[x]
                c = prev[x - nch] if x >= nch else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[x] = (line[x] + pr) & 0xFF
        # to luma
        if nch == 1:
            out.extend(line)
        elif nch == 2:
            out.extend(line[0::2])
        else:
            for x in range(0, stride, nch):
                r, g, b = line[x], line[x + 1], line[x + 2]
                out.append((r * 299 + g * 587 + b * 114) // 1000)
        prev = line
    return (w, h, out) if len(out) >= w else None


def _band_ink_from_pixels(w: int, h: int, gray: list[int]) -> list[float]:
    """Ink % per band, using a local background floor (robust to any theme)."""
    if not gray or w == 0:
        return []
    srt = sorted(gray[::max(1, len(gray) // 20000)])  # sampled percentile
    floor = srt[int(len(srt) * 0.12)] if srt else 0
    thr = floor + 18
    nb = max(1, (h - CAPTION_BAND_PX) // BAND_PX)
    bands = []
    for b in range(nb):
        y0, y1 = b * BAND_PX, min(h, (b + 1) * BAND_PX)
        tot = ink = 0
        for y in range(y0, y1, 2):          # every other row is plenty
            row = y * w
            for x in range(0, w, 2):
                tot += 1
                if gray[row + x] > thr:
                    ink += 1
        bands.append(round(100.0 * ink / tot, 1) if tot else 0.0)
    return bands


def _band_ink_from_trace_frame(frame: dict, width: int, height: int) -> list[float]:
    """Approximate per-band ink from recorded box geometry x alpha.

    NOT pixel-true — it counts declared area, so a large mostly-transparent panel
    reads as more ink than it paints. It is still decisive for the defect this gate
    exists to catch (a band with NOTHING declared in it is unambiguously dead), and
    it runs in milliseconds at authoring time instead of minutes after a render.
    """
    nb = max(1, (height - CAPTION_BAND_PX) // BAND_PX)
    cover = [0.0] * nb
    for b in (frame.get("boxes") or []):
        a = b.get("alpha") or 0
        if a <= 0.08:
            continue
        y0, y1 = b["y"], b["y"] + b["h"]
        for i in range(nb):
            by0, by1 = i * BAND_PX, (i + 1) * BAND_PX
            oy = min(y1, by1) - max(y0, by0)
            if oy > 0:
                cover[i] += max(0.0, b["w"]) * oy * min(1.0, a)
    for cn in (frame.get("connectors") or []):
        a = cn.get("alpha") or 0
        pts = cn.get("points") or []
        if a <= 0.08 or len(pts) < 2:
            continue
        for j in range(len(pts) - 1):
            p, q = pts[j], pts[j + 1]
            for i in range(nb):
                by0, by1 = i * BAND_PX, (i + 1) * BAND_PX
                if min(p["y"], q["y"]) <= by1 and max(p["y"], q["y"]) >= by0:
                    cover[i] += abs(q["x"] - p["x"]) * 3.0 * a + 40.0 * a
    band_area = width * BAND_PX
    return [round(min(100.0, 100.0 * c / band_area), 1) for c in cover]


def worst_dead_run(bands: list[float]) -> int:
    run = best = 0
    for v in bands:
        run = run + 1 if v < DEAD_PCT else 0
        best = max(best, run)
    return best


def main() -> int:
    ap = argparse.ArgumentParser(description="Coordn8 density gate (DEN).")
    sub = ap.add_subparsers(dest="mode", required=True)

    t = sub.add_parser("trace", help="measure the instrumentation trace (no renderer needed)")
    t.add_argument("traces", nargs="+")

    fr = sub.add_parser("frames", help="measure real rendered frames (pixel-true)")
    fr.add_argument("dir")
    fr.add_argument("--beatmap", default=None)
    fr.add_argument("--fps", type=float, default=30.0)

    args = ap.parse_args()
    rows, fails = [], []

    if args.mode == "trace":
        traces = [Path(p) for p in args.traces]
        have = [p for p in traces if p.exists()]
        if not have:
            emit({"gate": "density", "status": "SKIPPED",
                  "summary": "no trace files found — build one with scene_runtime.js buildTrace"})
            return EXIT_SKIPPED
        for tp in have:
            try:
                tr = load_json(tp)
            except Exception as e:  # noqa: BLE001
                emit({"gate": "density", "status": "ERROR",
                      "summary": f"could not parse {tp}: {type(e).__name__}: {e}"})
                return EXIT_ERROR
            w, h = tr.get("width", 1920), tr.get("height", 1080)
            scene = tr.get("scene", tp.stem)
            frames = tr.get("frames") or []
            for ln in (tr.get("lines") or []):
                t_at = ln["start"] + (ln["end"] - ln["start"]) * SAMPLE_AT
                best = min(frames, key=lambda f: abs(f.get("t", 0) - t_at), default=None)
                if best is None:
                    continue
                bands = _band_ink_from_trace_frame(best, w, h)
                run = worst_dead_run(bands)
                mean = round(sum(bands) / len(bands), 1) if bands else 0.0
                row = {"scene": scene, "line": ln["idx"], "beat": ln.get("name"),
                       "t": round(t_at, 2), "bands": bands, "mean_ink": mean,
                       "dead_run": run, "measure": "trace-approx"}
                rows.append(row)
                if run > MAX_DEAD_RUN:
                    fails.append(row)
    else:
        d = Path(args.dir)
        pngs = sorted(list(d.glob("*.png")) + list(d.glob("*.ppm"))) if d.is_dir() else []
        if not pngs:
            emit({"gate": "density", "status": "SKIPPED",
                  "summary": f"no frames in {d} — video tier not reached, or render not run yet"})
            return EXIT_SKIPPED
        # sample the frames matching each spoken line, else spread across the sequence
        picks: list[tuple[Path, dict]] = []
        if args.beatmap and Path(args.beatmap).exists():
            bm = load_json(Path(args.beatmap))
            for s in (bm.get("sentences") or []):
                t_at = float(s["start"]) + (float(s["end"]) - float(s["start"])) * SAMPLE_AT
                idx = min(len(pngs) - 1, max(0, int(round(t_at * args.fps))))
                picks.append((pngs[idx], {"line": s.get("idx"), "t": round(t_at, 2)}))
        else:
            for k in range(0, len(pngs), max(1, len(pngs) // 12)):
                picks.append((pngs[k], {"line": None, "t": round(k / args.fps, 2)}))

        used_pil = False
        try:
            from PIL import Image  # noqa: F401
            import numpy as np  # noqa: F401
            used_pil = True
        except Exception:  # noqa: BLE001
            used_pil = False

        for path, meta in picks:
            bands = []
            if used_pil:
                from PIL import Image
                import numpy as np
                im = Image.open(path).convert("L")
                a = np.asarray(im, dtype=float)
                h, w = a.shape
                thr = float(np.percentile(a, 12)) + 18
                nb = max(1, (h - CAPTION_BAND_PX) // BAND_PX)
                bands = [round(float((a[i * BAND_PX:(i + 1) * BAND_PX] > thr).mean()) * 100, 1)
                         for i in range(nb)]
            else:
                got = _read_png_gray(path)
                if got:
                    w, h, gray = got
                    bands = _band_ink_from_pixels(w, h, gray)
            if not bands:
                continue
            run = worst_dead_run(bands)
            mean = round(sum(bands) / len(bands), 1)
            row = {"scene": d.name, "line": meta["line"], "t": meta["t"],
                   "frame": path.name, "bands": bands, "mean_ink": mean,
                   "dead_run": run,
                   "measure": "pixels(PIL)" if used_pil else "pixels(pure-python)"}
            rows.append(row)
            if run > MAX_DEAD_RUN:
                fails.append(row)

    if not rows:
        emit({"gate": "density", "status": "SKIPPED",
              "summary": "nothing measurable (no readable frames/trace lines)"})
        return EXIT_SKIPPED

    ranked = sorted(rows, key=lambda r: r["mean_ink"])[:8]
    emit({
        "gate": "density",
        "status": "REVIEW" if fails else "PASS",
        "measured": len(rows),
        "mode": args.mode,
        "limits": {"band_px": BAND_PX, "dead_band_pct": DEAD_PCT,
                   "max_dead_run": MAX_DEAD_RUN, "sampled_at_pct_through_line": SAMPLE_AT},
        "weakest_beats_ranked": [
            {"scene": r["scene"], "line": r["line"], "beat": r.get("beat"),
             "mean_ink": r["mean_ink"], "bands": r["bands"]} for r in ranked],
        "findings": [
            f"{r['scene']} line {r['line']} (t={r['t']}s): {r['dead_run']} consecutive dead bands "
            f"(~{r['dead_run'] * BAND_PX}px of void) while a line is spoken; bands={r['bands']}"
            for r in fails],
        "summary": (
            f"{len(fails)} beat(s) leave {(MAX_DEAD_RUN + 1) * BAND_PX}px+ of the frame empty "
            f"under live narration — the 'machine-generated / effortless' look, now measured"
            if fails else
            f"no beat leaves {(MAX_DEAD_RUN + 1) * BAND_PX}px+ of the frame empty across "
            f"{len(rows)} sampled beat(s). The weakest-ink beats are ranked above — the lowest "
            f"one is usually the next thing a reviewer names, whether or not it failed."),
    })
    return EXIT_REVIEW if fails else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
