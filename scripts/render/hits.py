#!/usr/bin/env python3
"""Coordn8 — the key-reveal HIT and the BEAT GRID (E3). Python half; scripts/render/hits.js
is the scene-side half with the same numbers (the tests compare them frame for frame).

The hit treatment (one key reveal, at most two per video)
---------------------------------------------------------
  pre-darken   from 0.75 s before the release the whole frame eases toward ~94 % dark
  hold         the last 3 frames before the release are FROZEN (the scene is drawn at the
               hold-start time) — a held breath
  release      on the key word's measured start (atWord) — or on a measured downbeat when
               release="downbeat" and one lies within the snap window
  burst        from the release: the darkness lifts fast, a glow peaks and decays, the key
               element scales 1.12 -> 1 and a ring expands behind it (0.5 s)
Every value is a pure function of the frame number, so any frame re-renders alone.

The beat grid
-------------
A bar grid is fixed by TWO MEASURED DOWNBEATS: the bar length is the gap divided by a whole
number of bars (an approximate bar length — from the tempo estimate or given — only chooses
that whole number). Measuring the two downbeats from audio: the first onset of the music
and the strongest "sag then surge" (energy falls, then jumps), each refined to 10 ms.

CLI
---
    hits.py timeline --key 4.2 [--fps 30] [--release word|downbeat --grid grid.json]
    hits.py grid --d0 1.30 --d1 10.53 [--approx-bar 2.3]
    hits.py measure music.wav            (downbeats + grid from the audio)
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

DEFAULTS = {"pre_darken_s": 0.75, "dark_alpha": 0.94, "hold_frames": 3, "burst_s": 0.5,
            "lift_s": 0.18, "scale_from": 1.12}
FRAME_TOLERANCE = 2


def _round_half_up(x: float) -> int:
    """Math.round semantics (the scene side is JavaScript; Python's round() is half-even)."""
    return int(math.floor(x + 0.5))


def frame_of(t: float, fps: float) -> int:
    return _round_half_up(t * fps)


def _smooth(p: float) -> float:
    p = 0.0 if p < 0 else (1.0 if p > 1 else p)
    return p * p * (3 - 2 * p)


def _ease_out(p: float) -> float:
    p = 0.0 if p < 0 else (1.0 if p > 1 else p)
    return 1 - (1 - p) ** 3


# --------------------------------------------------------------------------- beat grid
def beat_grid(d0: float, d1: float, approx_bar_s: float | None = None, bars_between: int | None = None,
              beats_per_bar: int = 4) -> dict:
    """Grid from two measured downbeats d0 < d1. The whole number of bars between them is
    `bars_between`, or round((d1 - d0) / approx_bar_s)."""
    span = float(d1) - float(d0)
    if span <= 0:
        raise ValueError("the second downbeat must come after the first")
    if bars_between is None:
        if not approx_bar_s or approx_bar_s <= 0:
            raise ValueError("give bars_between or an approximate bar length")
        bars_between = max(1, _round_half_up(span / float(approx_bar_s)))
    bar = span / int(bars_between)
    return {"origin_s": float(d0), "bar_s": bar, "beat_s": bar / beats_per_bar, "beats_per_bar": beats_per_bar,
            "bars_between": int(bars_between), "bpm": round(60.0 * beats_per_bar / bar, 3),
            "from_downbeats": [float(d0), float(d1)]}


def grid_beat(grid: dict, k: float) -> float:
    return grid["origin_s"] + k * grid["beat_s"]


def grid_downbeat(grid: dict, k: float) -> float:
    return grid["origin_s"] + k * grid["bar_s"]


def nearest(grid: dict, t: float, downbeat: bool = True) -> float:
    step = grid["bar_s"] if downbeat else grid["beat_s"]
    k = _round_half_up((t - grid["origin_s"]) / step)
    return grid["origin_s"] + k * step


# --------------------------------------------------------------------------- hit timeline
def hit_timeline(key_t: float, fps: float = 30, release: str = "word", grid: dict | None = None,
                 snap_window_s: float = 0.25, **opts) -> dict:
    o = dict(DEFAULTS)
    o.update({k: v for k, v in opts.items() if k in DEFAULTS})
    rel_t, rel_src = float(key_t), "word"
    if release == "downbeat":
        if grid:
            d = nearest(grid, float(key_t), True)
            if abs(d - key_t) <= snap_window_s:
                rel_t, rel_src = d, "downbeat"
            else:
                rel_src = f"word (no downbeat within {snap_window_s}s)"
        else:
            rel_src = "word (no grid given)"
    rf = frame_of(rel_t, fps)
    return {"fps": fps, "key_t": float(key_t), "release_t": rel_t, "release_source": rel_src,
            "dark_start_frame": frame_of(rel_t - o["pre_darken_s"], fps),
            "hold_start_frame": rf - int(o["hold_frames"]),
            "release_frame": rf,
            "lift_end_frame": rf + max(1, _round_half_up(o["lift_s"] * fps)),
            "burst_end_frame": rf + max(1, _round_half_up(o["burst_s"] * fps)),
            "opts": o}


def hit_state(tl: dict, frame: int) -> dict:
    """The hit at one frame: dark (overlay alpha), frozen + scene_frame (draw THIS frame
    while frozen), burst progress, glow, scale of the key element, ring progress."""
    o = tl["opts"]
    ds, hs, rf = tl["dark_start_frame"], tl["hold_start_frame"], tl["release_frame"]
    le, be = tl["lift_end_frame"], tl["burst_end_frame"]
    st = {"frame": frame, "dark": 0.0, "frozen": False, "scene_frame": frame, "burst": 0.0, "glow": 0.0,
          "scale": 1.0, "ring": 0.0, "phase": "idle"}
    if ds <= frame < hs:
        st.update(dark=o["dark_alpha"] * _smooth((frame - ds) / max(1, hs - ds)), phase="pre-darken")
    elif hs <= frame < rf:
        st.update(dark=o["dark_alpha"], frozen=True, scene_frame=hs, phase="hold")
    elif rf <= frame < be:
        p = (frame - rf) / max(1, be - rf)
        lift = (frame - rf) / max(1, le - rf)
        st.update(dark=o["dark_alpha"] * (1 - _ease_out(lift)) if frame < le else 0.0, burst=round(p, 6),
                  glow=round(1 - _ease_out(p), 6), scale=round(o["scale_from"] + (1 - o["scale_from"]) * _ease_out(p), 6),
                  ring=round(_ease_out(p), 6), phase="burst")
    st["dark"] = round(st["dark"], 6)
    return st


def timeline_frames(tl: dict, first: int, last: int) -> list:
    return [hit_state(tl, f) for f in range(first, last + 1)]


# --------------------------------------------------------------------------- measuring downbeats
def _frames_db(xs, sr: int, win_s: float) -> list:
    win = max(1, int(sr * win_s))
    out = []
    for i in range(0, len(xs) - win + 1, win):
        acc = 0.0
        for x in xs[i:i + win]:
            acc += x * x
        out.append(10 * math.log10(acc / win + 1e-18))
    return out


def _as_list(samples):
    return samples.tolist() if hasattr(samples, "tolist") else list(samples)


def estimate_bar(xs, sr: int, bar_lo_s: float = 1.3, bar_hi_s: float = 4.0) -> float | None:
    """Bar length from the autocorrelation of the onset strength (positive log-energy flux,
    5 ms hops), searched over bar lengths (chord changes and patterns repeat per bar). Like
    any tempo estimate it can land on half or double the bar; it is only used to choose the
    WHOLE NUMBER of bars between two measured downbeats, and a hint (the music's own BPM)
    always wins over it."""
    hop = max(1, int(sr * 0.005))
    le = []
    for i in range(0, len(xs) - hop + 1, hop):
        acc = 0.0
        for x in xs[i:i + hop]:
            acc += x * x
        le.append(math.log(acc + 1e-12))
    flux = [max(0.0, le[i + 1] - le[i]) for i in range(len(le) - 1)]
    fps = sr / hop
    lo, hi = int(fps * bar_lo_s), int(fps * bar_hi_s) + 1
    if len(flux) <= hi + 10:
        return None
    mu = sum(flux) / len(flux)
    f = [x - mu for x in flux]
    best, best_lag = -1e30, None
    try:
        import numpy as np
        a = np.asarray(f)
        for lag in range(lo, hi):
            v = float(np.dot(a[:-lag], a[lag:]))
            if v > best:
                best, best_lag = v, lag
    except Exception:
        for lag in range(lo, hi):
            v = sum(f[i] * f[i + lag] for i in range(len(f) - lag))
            if v > best:
                best, best_lag = v, lag
    return best_lag / fps if best_lag else None


def measure_downbeats(samples, sr: int, approx_bar_s: float | None = None, beats_per_bar: int = 4,
                      search_from_s: float = 1.2) -> dict:
    """Two measured downbeats -> grid. d0 = the first onset (10 ms frame within 36 dB of the
    loudest, then the first sample over that level); d1 = the strongest sag-then-surge
    (50 ms frames: the next 150 ms is > 6 dB over the previous 200 ms, plus half of how
    much the energy sagged 0.5-1.0 s before), refined to the biggest 10 ms rise within
    +-80 ms."""
    xs = _as_list(samples)
    e10 = _frames_db(xs, sr, 0.01)
    if not e10:
        raise ValueError("no audio")
    thr = max(e10) - 36
    k = next(i for i, d in enumerate(e10) if d > thr)
    lin = math.sqrt(10 ** (thr / 10))
    s0 = k * int(sr * 0.01)
    j = next((i for i in range(max(0, s0 - int(sr * 0.01)), s0 + int(sr * 0.01)) if abs(xs[i]) > lin), s0)
    d0 = j / sr
    e50 = _frames_db(xs, sr, 0.05)
    cands = []
    lo = max(int(max(search_from_s, d0 + 1.0) * 20), 20)
    for i in range(lo, len(e50) - 3):
        dip = sum(e50[i - 4:i]) / 4
        jump = sum(e50[i:i + 3]) / 3 - dip
        sag = sum(e50[i - 20:i - 10]) / 10 - sum(e50[i - 6:i]) / 6
        if jump > 6:
            cands.append((jump + 0.5 * max(0.0, sag), i / 20.0, jump, sag))
    if not cands:
        raise ValueError("no sag-then-surge downbeat found after the first onset")
    score, t, jump, sag = max(cands)
    a, b = int((t - 0.08) * 100), int((t + 0.08) * 100)
    seg = e10[max(1, a):min(len(e10), b)]
    rises = [(seg[i] - seg[i - 1], i) for i in range(1, len(seg))]
    _r, ri = max(rises)
    d1 = (max(1, a) + ri) / 100.0
    est = None if approx_bar_s else estimate_bar(xs, sr)
    approx = approx_bar_s or est
    grid = beat_grid(d0, d1, approx_bar_s=approx, beats_per_bar=beats_per_bar)
    return {"d0_s": round(d0, 4), "d1_s": round(d1, 4), "approx_bar_s": approx,
            "approx_source": "hint" if approx_bar_s else "autocorrelation estimate (may be half/double)",
            "surge_db": round(jump, 2), "sag_db": round(sag, 2), "grid": grid}


# --------------------------------------------------------------------------- CLI
def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Coordn8 key-reveal hit + beat grid.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("timeline")
    t.add_argument("--key", type=float, required=True)
    t.add_argument("--fps", type=float, default=30)
    t.add_argument("--release", default="word", choices=["word", "downbeat"])
    t.add_argument("--grid")
    g = sub.add_parser("grid")
    g.add_argument("--d0", type=float, required=True)
    g.add_argument("--d1", type=float, required=True)
    g.add_argument("--approx-bar", type=float)
    g.add_argument("--bars-between", type=int)
    m = sub.add_parser("measure")
    m.add_argument("audio")
    m.add_argument("--approx-bar", type=float)
    a = ap.parse_args(argv)
    if a.cmd == "timeline":
        grid = json.loads(Path(a.grid).read_text()) if a.grid else None
        print(json.dumps(hit_timeline(a.key, a.fps, a.release, grid)))
    elif a.cmd == "grid":
        print(json.dumps(beat_grid(a.d0, a.d1, a.approx_bar, a.bars_between)))
    else:
        sys.path.insert(0, str(HERE))
        import audio
        x = audio.read_audio(a.audio)
        print(json.dumps(measure_downbeats(x, audio.SR, a.approx_bar)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
