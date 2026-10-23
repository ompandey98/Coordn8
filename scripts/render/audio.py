#!/usr/bin/env python3
"""Coordn8 — local MUSIC, SOUND EFFECTS, DUCKING, LOUDNESS and STEMS (E4).

Everything here is synthesized from code on this machine. No music or effect file is
downloaded, no hosted service is called. Sound beyond the narration is OFF BY DEFAULT:
it is switched on only by a flag in the storyboard or the design system (see enabled()).

What it makes
-------------
* Music: plucked-string synthesis (Karplus-Strong, block-vectorised), bell, triangle bass,
  kick / snap / hat / shaker, Schroeder reverb, arranged by SECTIONS. Catalog styles:
  light · groove · bright · tense · break · end. A section may be INVENTED
  ({"at": s, "invented": name, "why": ..., "params": {...}}) — styles are a menu.
  DETERMINISTIC: every random draw comes from random.Random(seed); the same seed gives a
  byte-identical WAV on the same backend. Bar lines can be placed on a key word
  (align_downbeat_to), and the true downbeat times are returned for the beat grid.
* Sound effects: 16 synthesized effects (data/sfx.json) plus invented recipes.
* Ducking: a sidechain keyed by the narration's RMS envelope (10 ms frames, fast attack,
  slow release, a short look-ahead so the first syllable is never masked), keyed RELATIVE
  to the narration's own level so it does not depend on how loud the voice was recorded.
  The duck is MEASURED in dB on the rendered bed (dry vs ducked) and reported.
* Loudness: two-pass ffmpeg loudnorm (I = -16 LUFS, TP <= -1 dBTP, LRA 11): pass 1
  measures, pass 2 normalises with the MEASURED JSON in linear mode. If the gain would
  push the true peak over the ceiling, a look-ahead true-peak limiter (4x oversampled
  peak estimate) is applied first, as ONE gain envelope to every stem, so the stems still
  sum to the mix. The written file is then measured again, independently (ebur128).
* Stems: voice.wav, music.wav, sfx.wav and mix.wav (48 kHz stereo, 16-bit); the residual
  between the sum of the stems and the mix is measured and reported.

Backends: numpy when importable; otherwise a small pure-Python vector type with the same
operations (slower; COORDN8_AUDIO_PURE=1 forces it). Python 3.9, stdlib + optional numpy.

CLI
---
    audio.py music  --seconds 12 --sections '[[0,"light"],[4,"groove"]]' --seed 7 --out m.wav
    audio.py sfx    --name pop --out pop.wav          (--all --out-dir DIR)
    audio.py list-sfx
    audio.py mix    --voice v.wav --out-dir DIR [--music-sections ...] [--sfx-events JSON]
    audio.py build  --voice v.wav --out-dir DIR [--storyboard sb.json] [--design ds.json]
                    [--words words.json]     (OFF unless a flag enables it)
    audio.py measure FILE                    (integrated loudness + true peak, ebur128)
"""
from __future__ import annotations

import array
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG_ROOT = HERE.parents[1]
SFX_CATALOG = PKG_ROOT / "data" / "sfx.json"

SR = 48000
TARGET_I = -16.0
TARGET_TP = -1.0
TARGET_LRA = 11.0
TP_MARGIN_DB = 0.5          # aim the limiter this far under the ceiling
REF_SR = 44100.0            # the delay lengths below were tuned at this rate

try:
    if os.environ.get("COORDN8_AUDIO_PURE") == "1":
        raise ImportError("pure backend forced")
    import numpy as _np
except Exception:  # pragma: no cover - exercised by the pure-backend test
    _np = None

BACKEND = "numpy" if _np is not None else "pure"


# =========================================================================== backend
class PV:
    """Minimal pure-Python float vector (the numpy subset this module uses)."""
    __slots__ = ("d",)

    def __init__(self, d=()):
        self.d = d if type(d) is list else [float(x) for x in d]

    def __len__(self):
        return len(self.d)

    def __iter__(self):
        return iter(self.d)

    def __getitem__(self, k):
        if isinstance(k, slice):
            return PV(self.d[k])
        return self.d[k]

    def __setitem__(self, k, v):
        if isinstance(k, slice):
            a, b, _s = k.indices(len(self.d))
            n = max(0, b - a)
            if isinstance(v, PV):
                v = v.d
            elif not isinstance(v, list):
                v = [float(v)] * n
            self.d[a:b] = v
        else:
            self.d[k] = float(v)

    def __add__(self, o):
        if isinstance(o, PV):
            return PV([a + b for a, b in zip(self.d, o.d)])
        return PV([a + o for a in self.d])
    __radd__ = __add__

    def __sub__(self, o):
        if isinstance(o, PV):
            return PV([a - b for a, b in zip(self.d, o.d)])
        return PV([a - o for a in self.d])

    def __rsub__(self, o):
        return PV([o - a for a in self.d])

    def __mul__(self, o):
        if isinstance(o, PV):
            return PV([a * b for a, b in zip(self.d, o.d)])
        return PV([a * o for a in self.d])
    __rmul__ = __mul__

    def __truediv__(self, o):
        if isinstance(o, PV):
            return PV([a / b for a, b in zip(self.d, o.d)])
        return PV([a / o for a in self.d])

    def __rtruediv__(self, o):
        return PV([o / a for a in self.d])

    def __neg__(self):
        return PV([-a for a in self.d])

    def __pow__(self, p):
        return PV([a ** p for a in self.d])

    def __abs__(self):
        return PV([abs(a) for a in self.d])


class _Pure:
    name = "pure"

    @staticmethod
    def zeros(n):
        return PV([0.0] * int(n))

    @staticmethod
    def arange(n):
        return PV([float(i) for i in range(int(n))])

    @staticmethod
    def array(x):
        return x if isinstance(x, PV) else PV([float(v) for v in x])

    @staticmethod
    def tolist(v):
        return list(v.d) if isinstance(v, PV) else list(v)

    @staticmethod
    def _map(f, v):
        return PV([f(x) for x in v.d]) if isinstance(v, PV) else f(v)

    sin = staticmethod(lambda v: _Pure._map(math.sin, v))
    cos = staticmethod(lambda v: _Pure._map(math.cos, v))
    exp = staticmethod(lambda v: _Pure._map(math.exp, v))
    tanh = staticmethod(lambda v: _Pure._map(math.tanh, v))
    floor = staticmethod(lambda v: _Pure._map(math.floor, v))
    sqrt = staticmethod(lambda v: _Pure._map(math.sqrt, v))
    sign = staticmethod(lambda v: _Pure._map(lambda x: 1.0 if x > 0 else (-1.0 if x < 0 else 0.0), v))

    @staticmethod
    def cumsum(v):
        out, acc = [], 0.0
        for x in v.d:
            acc += x
            out.append(acc)
        return PV(out)

    @staticmethod
    def minimum(v, c):
        if isinstance(c, PV):
            return PV([a if a < b else b for a, b in zip(v.d, c.d)])
        return PV([a if a < c else c for a in v.d])

    @staticmethod
    def maximum(v, c):
        if isinstance(c, PV):
            return PV([a if a > b else b for a, b in zip(v.d, c.d)])
        return PV([a if a > c else c for a in v.d])

    @staticmethod
    def vsum(v):
        return math.fsum(v.d)

    @staticmethod
    def dot(a, b):
        return math.fsum(x * y for x, y in zip(a.d, b.d))

    @staticmethod
    def maxabs(v):
        return max((abs(x) for x in v.d), default=0.0)

    @staticmethod
    def concat(vs):
        out = []
        for v in vs:
            out.extend(v.d)
        return PV(out)

    @staticmethod
    def copy(v):
        return PV(list(v.d))


class _Numpy:
    name = "numpy"
    zeros = staticmethod(lambda n: _np.zeros(int(n)))
    arange = staticmethod(lambda n: _np.arange(int(n), dtype=float))
    array = staticmethod(lambda x: _np.asarray(x, dtype=float))
    tolist = staticmethod(lambda v: v.tolist())
    sin = staticmethod(lambda v: _np.sin(v))
    cos = staticmethod(lambda v: _np.cos(v))
    exp = staticmethod(lambda v: _np.exp(v))
    tanh = staticmethod(lambda v: _np.tanh(v))
    floor = staticmethod(lambda v: _np.floor(v))
    sqrt = staticmethod(lambda v: _np.sqrt(v))
    sign = staticmethod(lambda v: _np.sign(v))
    cumsum = staticmethod(lambda v: _np.cumsum(v))
    minimum = staticmethod(lambda v, c: _np.minimum(v, c))
    maximum = staticmethod(lambda v, c: _np.maximum(v, c))
    vsum = staticmethod(lambda v: float(_np.sum(v)))
    dot = staticmethod(lambda a, b: float(_np.dot(a, b)))
    maxabs = staticmethod(lambda v: float(_np.max(_np.abs(v))) if len(v) else 0.0)
    concat = staticmethod(lambda vs: _np.concatenate(list(vs)) if vs else _np.zeros(0))
    copy = staticmethod(lambda v: _np.array(v, dtype=float))


B = _Numpy if _np is not None else _Pure


# =========================================================================== DSP primitives
def secs(n) -> int:
    return int(round(float(n) * SR))


def taxis(n):
    return B.arange(n) * (1.0 / SR)


def mtof(m: float) -> float:
    return 440.0 * 2.0 ** ((m - 69) / 12.0)


def noise(rng: random.Random, n: int):
    r = rng.random
    return B.array([r() * 2.0 - 1.0 for _ in range(int(n))])


def mix_into(dst, src, at_s: float, gain: float = 1.0) -> None:
    off = int(round(at_s * SR))
    a, b = max(0, off), min(len(dst), off + len(src))
    if b > a:
        dst[a:b] = dst[a:b] + src[a - off:b - off] * gain


def rms(v) -> float:
    n = len(v)
    return math.sqrt(B.dot(v, v) / n) if n else 0.0


def db(x: float) -> float:
    return 20.0 * math.log10(x) if x > 1e-12 else -240.0


def normalize_peak(v, target: float = 0.9):
    p = B.maxabs(v)
    return v * (target / p) if p > 0 else v


def soft_clip(v, drive: float = 1.2):
    return B.tanh(v * drive) * (1.0 / math.tanh(drive))


def biquad(v, kind: str, freq: float, q: float = 0.707):
    """RBJ biquad (lp | hp | bp), sequential, on a list."""
    w0 = 2 * math.pi * freq / SR
    alpha = math.sin(w0) / (2 * q)
    c = math.cos(w0)
    if kind == "lp":
        b0, b1, b2 = (1 - c) / 2, 1 - c, (1 - c) / 2
    elif kind == "hp":
        b0, b1, b2 = (1 + c) / 2, -(1 + c), (1 + c) / 2
    else:
        b0, b1, b2 = alpha, 0.0, -alpha
    a0, a1, a2 = 1 + alpha, -2 * c, 1 - alpha
    b0, b1, b2, a1, a2 = b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0
    x1 = x2 = y1 = y2 = 0.0
    out = []
    ap = out.append
    for x in B.tolist(v):
        y = b0 * x + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1, y2, y1 = x1, x, y1, y
        ap(y)
    return B.array(out)


def sweep_filter(v, kind: str, f_of_p, q: float, block: int = 64):
    """Time-varying biquad: coefficients rebuilt every `block` samples from f_of_p(progress)."""
    xs = B.tolist(v)
    n = len(xs)
    out = []
    x1 = x2 = y1 = y2 = 0.0
    for s in range(0, n, block):
        f = max(20.0, min(SR * 0.45, f_of_p(s / max(1, n))))
        w0 = 2 * math.pi * f / SR
        alpha = math.sin(w0) / (2 * q)
        c = math.cos(w0)
        if kind == "lp":
            b0, b1, b2 = (1 - c) / 2, 1 - c, (1 - c) / 2
        elif kind == "hp":
            b0, b1, b2 = (1 + c) / 2, -(1 + c), (1 + c) / 2
        else:
            b0, b1, b2 = alpha, 0.0, -alpha
        a0 = 1 + alpha
        b0, b1, b2, a1, a2 = b0 / a0, b1 / a0, b2 / a0, -2 * c / a0, (1 - alpha) / a0
        for x in xs[s:s + block]:
            y = b0 * x + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
            x2, x1, y2, y1 = x1, x, y1, y
            out.append(y)
    return B.array(out)


def onepole_lp(v, cut: float):
    a = 1 - math.exp(-2 * math.pi * cut / SR)
    y = 0.0
    out = []
    for x in B.tolist(v):
        y += a * (x - y)
        out.append(y)
    return B.array(out)


def _comb(x, d: int, decay: float):
    """Feedback comb with in-loop damping (6-tap FIR approximation of a one-pole
    0.6/0.4 lowpass), computed block-by-block (each block depends only on earlier ones)."""
    n = len(x)
    y = B.zeros(n)
    lp = B.zeros(n)
    taps = [0.6 * 0.4 ** j for j in range(6)]
    for k0 in range(d, n, d):
        k1 = min(k0 + d, n)
        y[k0:k1] = x[k0 - d:k1 - d] + lp[k0 - d:k1 - d] * decay
        acc = y[k0:k1] * taps[0]
        for j in range(1, 6):
            acc = acc + y[k0 - j:k1 - j] * taps[j]
        lp[k0:k1] = acc
    return y


def _allpass(x, d: int, g: float = 0.5):
    n = len(x)
    v = B.copy(x)
    for k0 in range(d, n, d):
        k1 = min(k0 + d, n)
        v[k0:k1] = x[k0:k1] + v[k0 - d:k1 - d] * g
    out = v * (-g)
    out[d:] = out[d:] + v[:n - d]
    return out


def reverb(v, mix: float = 0.22, decay: float = 0.78):
    """Schroeder reverb: four damped combs in parallel, two allpasses in series."""
    n = len(v)
    acc = B.zeros(n)
    for L in (1557, 1617, 1491, 1422):
        acc = acc + _comb(v, int(round(L * SR / REF_SR)), decay)
    acc = acc * 0.25
    for L in (225, 556):
        acc = _allpass(acc, int(round(L * SR / REF_SR)))
    return v * (1 - mix) + acc * (mix * 2.2)


# =========================================================================== instruments
def _interp(v, ratio: float, n: int):
    """Linear-interpolation resample: output sample i reads input position i * ratio."""
    if _np is not None and not isinstance(v, PV):
        xp = _np.arange(len(v), dtype=float)
        return _np.interp(_np.arange(n, dtype=float) * ratio, xp, v)
    xs = B.tolist(v)
    last = len(xs) - 1
    out = []
    for i in range(n):
        x = i * ratio
        j = int(x)
        if j >= last:
            out.append(xs[last])
            continue
        f = x - j
        out.append(xs[j] + (xs[j + 1] - xs[j]) * f)
    return PV(out)


def pluck(rng, freq: float, dur: float = 1.2, bright: float = 0.5):
    """Karplus-Strong plucked string, EXACTLY in tune. The loop's two-point average delays by
    half a sample, so the period is L - 0.5 samples; an integer L can only approximate
    SR / f (tens of cents sharp or flat at high notes). Instead the string is synthesized at
    its own rate SR' = f * (L - 0.5) (L = ceil(SR / f + 0.5), so SR' >= SR) and resampled to
    SR by linear interpolation. Computed in chunks of L - 1 samples (block-vectorised)."""
    n = secs(dur)
    L = max(3, int(math.ceil(SR / freq + 0.5)))
    sr_s = freq * (L - 0.5)
    ns = int(math.ceil(dur * sr_s)) + 2
    ring = biquad(noise(rng, L), "lp", (1500 + bright * 4000) * SR / sr_s)
    out = B.zeros(ns)
    m = min(L, ns)
    out[0:m] = ring[0:m]
    c = 0.5 * 0.9965
    s = L
    while s < ns:
        e = min(s + L - 1, ns)
        out[s:e] = (out[s - L:e - L] + out[s - L + 1:e - L + 1]) * c
        s = e
    out = _interp(out, sr_s / SR, n)
    i = B.arange(n)
    env = B.minimum(i * (1.0 / (0.002 * SR)), 1.0) * B.minimum((n - i) * (1.0 / (0.08 * SR)), 1.0)
    return out * env


def bell(freq: float, dur: float = 1.0):
    n = secs(dur)
    t = taxis(n)
    v = B.zeros(n)
    for mlt, amp in ((1, 1.0), (2.76, 0.35), (5.4, 0.12)):
        v = v + B.sin(t * (2 * math.pi * freq * mlt)) * B.exp(t * -(3 + mlt * 2)) * amp
    return v * B.minimum(B.arange(n) * (1 / 40.0), 1.0)


def bass(freq: float, dur: float = 0.45):
    n = secs(dur)
    i = B.arange(n)
    ph = i * (freq / SR)
    tri = abs(ph - B.floor(ph + 0.5)) * 4 - 1
    env = B.minimum(i * (1 / 200.0), 1.0) * B.exp(i * (-3.5 / SR)) * B.minimum((n - i) * (1 / 400.0), 1.0)
    return (tri * 0.8 + B.sin(ph * (2 * math.pi)) * 0.4) * env


def kick():
    n = secs(0.32)
    t = taxis(n)
    ph = B.cumsum((B.exp(t * -28) * 95 + 45) * (1.0 / SR))
    return B.sin(ph * (2 * math.pi)) * B.exp(t * -9)


def snap(rng):
    n = secs(0.18)
    t = taxis(n)
    step = (B.sign(t - 0.011) + 1) * 0.5
    e = B.exp(t * -38) + step * B.exp((t - 0.011) * -30) * 0.6
    return biquad(noise(rng, n) * e, "bp", 1800, 0.9)


def hat(rng, open_: bool = False):
    n = secs(0.2 if open_ else 0.05)
    t = taxis(n)
    return biquad(noise(rng, n) * B.exp(t * -(18 if open_ else 70)), "hp", 7000)


def shaker(rng):
    n = secs(0.09)
    t = taxis(n)
    return biquad(noise(rng, n) * B.sin(t * (math.pi / 0.09)) ** 2, "hp", 5000)


# =========================================================================== arrangement
CHORDS = {
    "C": {"uke": [67, 60, 64, 72], "root": 36}, "Am": {"uke": [69, 60, 64, 69], "root": 33},
    "F": {"uke": [69, 60, 65, 69], "root": 29}, "G": {"uke": [67, 62, 67, 71], "root": 31},
}
MINOR = {
    "Am": {"uke": [69, 60, 64, 69], "root": 33}, "F": {"uke": [69, 60, 65, 69], "root": 29},
    "Dm": {"uke": [69, 62, 65, 69], "root": 26}, "E": {"uke": [68, 64, 68, 71], "root": 28},
}
MAJOR_PROG = ["C", "Am", "F", "G"]
MINOR_PROG = ["Am", "F", "Dm", "E"]
STRUMS = [(0, "D", 1.0), (1, "D", 0.7), (1.5, "U", 0.5), (2.5, "U", 0.5), (3, "D", 0.8), (3.5, "U", 0.5)]
BRIGHT_MOTIFS = [
    [(0, 76), (0.5, 79), (1, 81), (2, 79), (3, 76)], [(0, 72), (1, 74), (1.5, 76), (2, 79), (3.5, 81)],
    [(0, 77), (0.5, 76), (1, 72), (2, 74), (3, 76)], [(0, 79), (1, 81), (2, 84), (3, 79)],
]
# Each catalog style is a parameter set; an invented section supplies its own parameters.
STYLES = {
    "light":  {"strum": "full", "strum_vel": 0.8, "bass": "none", "drums": "shaker", "bells": "none", "minor": False},
    "groove": {"strum": "full", "strum_vel": 1.0, "bass": "walk", "drums": "groove", "bells": "none", "minor": False},
    "bright": {"strum": "full", "strum_vel": 1.0, "bass": "walk", "drums": "groove+", "bells": "motif", "minor": False},
    "tense":  {"strum": "full", "strum_vel": 1.0, "bass": "pulse", "drums": "tense", "bells": "none", "minor": True},
    "break":  {"strum": "sparse", "strum_vel": 0.65, "bass": "none", "drums": "none", "bells": "sparkle", "minor": False},
    "end":    {"strum": "final", "strum_vel": 1.0, "bass": "final", "drums": "final", "bells": "final", "minor": False},
}
PARAM_CHOICES = {"strum": {"full", "sparse", "none", "final"}, "bass": {"walk", "pulse", "none", "final"},
                 "drums": {"shaker", "groove", "groove+", "tense", "none", "final"},
                 "bells": {"none", "motif", "sparkle", "final"}}


def normalize_sections(sections) -> list:
    """[[at, style], ...] or [{"at", "style"} | {"at", "invented", "why", "params"}] ->
    [{"at", "name", "params", "invented"}]. Unknown parameter values fall back to the
    nearest catalog default and are reported in "notes" (a menu, never a fence)."""
    out = []
    for s in sections or [[0, "groove"]]:
        if isinstance(s, (list, tuple)):
            s = {"at": s[0], "style": s[1]}
        at = float(s.get("at", 0))
        if s.get("invented"):
            base = dict(STYLES.get(s.get("base", "groove"), STYLES["groove"]))
            notes = []
            for k, v in (s.get("params") or {}).items():
                if k in PARAM_CHOICES and v not in PARAM_CHOICES[k]:
                    notes.append(f"{k}={v!r} is not a renderable value; kept {base.get(k)!r}")
                    continue
                base[k] = v
            out.append({"at": at, "name": "invented:" + str(s["invented"]), "params": base,
                        "invented": True, "why": s.get("why", ""), "notes": notes})
        else:
            st = str(s.get("style", "groove"))
            if st not in STYLES:
                out.append({"at": at, "name": "invented:" + st, "params": dict(STYLES["groove"]),
                            "invented": True, "why": s.get("why", ""),
                            "notes": [f"style {st!r} has no parameters; rendered with groove parameters"]})
            else:
                out.append({"at": at, "name": st, "params": dict(STYLES[st]), "invented": False})
    out.sort(key=lambda r: r["at"])
    return out


def _style_at(t: float, secs_: list) -> dict:
    cur = secs_[0]
    for s in secs_:
        if t + 1e-9 >= s["at"]:
            cur = s
    return cur


def render_music(seconds: float, sections=None, seed: int = 7, bpm: float = 112.0,
                 align_downbeat_to: float | None = None, fade_out_s: float = 1.5) -> tuple:
    """-> (mono vector at SR, meta). meta["downbeats"] = every bar start inside the track,
    meta["beat_s"] the beat length. With align_downbeat_to=t, a bar line falls on t."""
    rng = random.Random(int(seed))
    secs_ = normalize_sections(sections)
    beat = 60.0 / float(bpm)
    bar = 4 * beat
    n = int(math.ceil(seconds * SR))
    music = B.zeros(n)
    drums = B.zeros(n)
    K, S, Hc, Ho, Sh = kick(), snap(rng), hat(rng), hat(rng, True), shaker(rng)
    offset = 0.0 if align_downbeat_to is None else float(align_downbeat_to) % bar
    k0 = -int(math.ceil(offset / bar)) if offset > 1e-9 else 0
    downbeats = []
    first_music = secs_[0]["at"]
    k = k0
    while True:
        t0 = offset + k * bar
        if t0 >= seconds - 1e-9:
            break
        k += 1
        if t0 + bar <= first_music:
            continue
        sec = _style_at(max(t0, 0.0), secs_)
        p = sec["params"]
        if t0 >= -1e-9:
            downbeats.append(round(t0, 6))
        bar_i = k - 1 - k0
        tense = bool(p.get("minor"))
        name = (MINOR_PROG if tense else MAJOR_PROG)[bar_i % 4]
        chord = MINOR[name] if tense else CHORDS[name]
        vel_scale = float(p.get("strum_vel", 1.0))

        def strum(c_uke, at, vel, dur=1.1):
            for j, m in enumerate(c_uke):
                mix_into(music, pluck(rng, mtof(m), dur, 0.35 + vel * 0.4),
                         at + j * 0.011 + (rng.random() - 0.5) * 0.004, 0.45 * vel)
        if p["strum"] == "final":
            strum(CHORDS["C"]["uke"], t0, 1.0, 2.6)
            mix_into(music, bass(mtof(36), 1.2), t0, 0.5)
            mix_into(drums, K, t0, 0.8)
            mix_into(drums, Ho, t0, 0.3)
            for j, m in enumerate((72, 76, 79, 84)):
                mix_into(music, bell(mtof(m), 1.6), t0 + j * 0.08, 0.1)
            break
        if p["strum"] == "sparse":
            strum(chord["uke"], t0, vel_scale, 1.8)
            strum(chord["uke"], t0 + 2 * beat, 0.45, 1.2)
        elif p["strum"] == "full":
            for b_, d_, v_ in STRUMS:
                notes = chord["uke"] if d_ == "D" else list(reversed(chord["uke"]))
                strum(notes, t0 + b_ * beat, v_ * vel_scale)
        if p["bass"] == "walk":
            mix_into(music, bass(mtof(chord["root"])), t0, 0.5)
            mix_into(music, bass(mtof(chord["root"] + 7), 0.3), t0 + 1.5 * beat, 0.35)
            mix_into(music, bass(mtof(chord["root"])), t0 + 2 * beat, 0.45)
        elif p["bass"] == "pulse":
            for j in range(8):
                mix_into(music, bass(mtof(chord["root"]), 0.22), t0 + j * 0.5 * beat, 0.42)
        dr = p["drums"]
        if dr == "shaker":
            for j in range(8):
                mix_into(drums, Sh, t0 + j * 0.5 * beat, 0.1 if j % 2 else 0.16)
        elif dr in ("groove", "groove+"):
            mix_into(drums, K, t0, 0.9)
            mix_into(drums, K, t0 + 2 * beat, 0.8)
            if dr == "groove+":
                mix_into(drums, K, t0 + 2.5 * beat, 0.5)
            mix_into(drums, S, t0 + beat, 0.55)
            mix_into(drums, S, t0 + 3 * beat, 0.55)
            for j in range(8):
                mix_into(drums, Ho if j == 7 else Hc, t0 + j * 0.5 * beat, 0.12 if j % 2 else 0.2)
        elif dr == "tense":
            mix_into(drums, K, t0, 0.8)
            mix_into(drums, K, t0 + 2 * beat, 0.7)
            for j in range(16):
                mix_into(drums, Hc, t0 + j * 0.25 * beat, 0.06 + (0.08 if j % 4 == 0 else 0))
            if bar_i % 2 == 1:
                for j in range(4):
                    mix_into(drums, S, t0 + 3 * beat + j * 0.25 * beat, 0.2 + j * 0.08)
        if p["bells"] == "motif":
            for b_, m in BRIGHT_MOTIFS[bar_i % 4]:
                mix_into(music, bell(mtof(m), 1.2), t0 + b_ * beat, 0.12)
        elif p["bells"] == "sparkle" and bar_i % 2 == 0:
            for j, m in enumerate((84, 79)):
                mix_into(music, bell(mtof(m), 1.4), t0 + 1 + j * 0.5, 0.09)
    music = biquad(music, "lp", 6000)
    out = reverb(music, 0.24) + reverb(drums, 0.08) * 0.45
    out = soft_clip(out, 1.1)
    if fade_out_s > 0:
        i = B.arange(n)
        fade_n = secs(fade_out_s)
        env = B.minimum((n - i) * (1.0 / max(1, fade_n)), 1.0)
        out = out * env
    meta = {"seconds": round(n / SR, 6), "bpm": float(bpm), "beat_s": beat, "bar_s": bar,
            "offset_s": round(offset, 6), "downbeats": downbeats, "seed": int(seed),
            "sections": [{"at": s["at"], "name": s["name"], "invented": s["invented"],
                          **({"why": s.get("why", ""), "notes": s.get("notes", [])} if s["invented"] else {})}
                         for s in secs_],
            "backend": B.name, "sample_rate": SR}
    return out, meta


# =========================================================================== sound effects
def _buf(sec):
    return B.zeros(secs(sec))


def _sfx_click(rng):
    n = secs(0.06)
    t = taxis(n)
    return (B.sin(t * (2 * math.pi * 1900)) * 0.6 + noise(rng, n) * 0.4) * B.exp(t * -140)


def _sfx_tick(rng):
    n = secs(0.03)
    t = taxis(n)
    return biquad(noise(rng, n) * B.exp(t * -260), "bp", 3600, 4) * 7


def _sfx_pop(rng):
    n = secs(0.14)
    t = taxis(n)
    ph = B.cumsum((B.exp(t * -45) * 900 + 320) * (1.0 / SR))
    return B.sin(ph * (2 * math.pi)) * B.exp(t * -32) * B.minimum(B.arange(n) * (1 / 30.0), 1.0)


def _sfx_whoosh(rng):
    n = secs(0.5)
    p = B.arange(n) * (1.0 / n)
    env = B.sin(p * math.pi)
    env = env * B.sqrt(env)  # ^1.5
    y = sweep_filter(noise(rng, n), "bp", lambda q: 400 + math.sin(q * math.pi) * 2600, 2.0)
    return y * env


def _sfx_ding(rng):
    out = _buf(1.6)
    mix_into(out, bell(mtof(88), 1.4), 0, 0.7)
    mix_into(out, bell(mtof(93), 1.4), 0.12, 0.8)
    return out


def _sfx_chime(rng):
    out = _buf(1.4)
    for k, m in enumerate((72, 76, 79, 84, 88, 91, 96)):
        mix_into(out, bell(mtof(m), 0.9), k * 0.055, 0.35 - k * 0.02)
    return out


def _sfx_error(rng):
    out = _buf(0.6)
    for at, f in ((0.0, 180), (0.2, 140)):
        n = secs(0.17)
        t = taxis(n)
        sq = B.sign(B.sin(t * (2 * math.pi * f))) * 0.6 + B.sin(t * (2 * math.pi * f * 2)) * 0.2
        env = B.minimum(t * 200, 1.0) * B.minimum((0.17 - t) * 60, 1.0)
        mix_into(out, biquad(sq * B.maximum(env, 0.0), "lp", 2200), at, 1.0)
    return out


def _sfx_stamp(rng):
    n = secs(0.4)
    t = taxis(n)
    ph = B.cumsum((B.exp(t * -40) * 90 + 60) * (1.0 / SR))
    v = B.sin(ph * (2 * math.pi)) * B.exp(t * -14) + noise(rng, n) * B.exp(t * -45) * 0.5
    return biquad(v, "lp", 3000)


def _sfx_paper(rng):
    n = secs(0.35)
    p = B.arange(n) * (1.0 / n)
    y = sweep_filter(noise(rng, n), "bp", lambda q: 900 + q * 3500, 1.2)
    return y * B.sin(p * math.pi) * (B.sin(p * 40) * 0.4 + 0.6)


def _sfx_type(rng):
    out = _buf(0.9)
    at = 0.0
    while at < 0.8:
        n = secs(0.03)
        t = taxis(n)
        f = 2500 + rng.random() * 2500
        c = (noise(rng, n) * 0.7 + B.sin(t * (2 * math.pi * f)) * 0.3) * B.exp(t * -260)
        mix_into(out, biquad(c, "hp", 1500), at, 0.5 + rng.random() * 0.5)
        at += 0.045 + rng.random() * 0.06
    return out


def _sfx_question(rng):
    out = _buf(0.55)
    for at, f0, f1 in ((0.0, 520, 560), (0.16, 600, 900)):
        n = secs(0.28)
        t = taxis(n)
        ph = B.cumsum((t * ((f1 - f0) / 0.28) + f0) * (1.0 / SR))
        v = (B.sin(ph * (2 * math.pi)) + B.sin(ph * (4 * math.pi)) * 0.25) * B.minimum(t * 80, 1.0) * B.exp(t * -7)
        mix_into(out, v, at, 0.8)
    return out


def _sfx_riser(rng):
    n = secs(2.0)
    t = taxis(n)
    gate = (B.sign(1.9 - t) + 1) * 0.5
    nz = onepole_lp(biquad(noise(rng, n), "hp", 300), 6000) * ((t * 0.5) ** 2.2) * 0.7
    tone = B.sin((t * 220 + t * t * 110) * (2 * math.pi)) * ((t * 0.5) ** 2) * 0.35
    return (nz + tone) * gate


def _sfx_impact(rng):
    n = secs(1.6)
    t = taxis(n)
    tone = B.sin((t * 70 - t * t * 12) * (2 * math.pi)) * B.exp(t * -3.2) * 0.9
    rumble = onepole_lp(noise(rng, n), 180) * B.maximum(1.0 - t * (1 / 0.9), 0.0) * 1.8
    return tone + rumble


def _sfx_chapter(rng):
    n = secs(0.5)
    t = taxis(n)
    return B.sin(t * (2 * math.pi * 110)) * B.exp(t * -12) * 0.8 + B.sin(t * (2 * math.pi * 1760)) * B.exp(t * -20) * 0.25


def _sfx_rewind(rng):
    n = secs(1.3)
    t = taxis(n)
    down = B.maximum(1.0 - t * (1 / 1.3), 0.0)
    tone = B.sin((t * 900 - t * t * 280) * (2 * math.pi)) * (B.sin(t * (2 * math.pi * 22)) * 0.4 + 0.6) * \
        B.minimum(t * 12, 1.0) * down * 0.5
    hiss = biquad(noise(rng, n) * 0.2, "hp", 2000) * down
    return tone + hiss


def _sfx_clink(rng):
    n = secs(0.45)
    t = taxis(n)
    v = (B.sin(t * (2 * math.pi * 2380)) * 0.55 + B.sin(t * (2 * math.pi * 3710)) * 0.35 +
         B.sin(t * (2 * math.pi * 5220)) * 0.25) * B.exp(t * -28)
    return v + B.sin(t * (2 * math.pi * 1460)) * B.exp(t * -60) * 0.2


SFX = {"click": _sfx_click, "tick": _sfx_tick, "pop": _sfx_pop, "whoosh": _sfx_whoosh, "ding": _sfx_ding,
       "chime": _sfx_chime, "error": _sfx_error, "stamp": _sfx_stamp, "paper": _sfx_paper, "type": _sfx_type,
       "question": _sfx_question, "riser": _sfx_riser, "impact": _sfx_impact, "chapter": _sfx_chapter,
       "rewind": _sfx_rewind, "clink": _sfx_clink}


def sfx_catalog() -> dict:
    try:
        return json.loads(SFX_CATALOG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema": 1, "effects": []}


def _recipe(rng, layers: list):
    dur = max(float(L.get("at", 0)) + float(L.get("dur", 0.3)) for L in layers) if layers else 0.1
    out = _buf(dur)
    for L in layers:
        kind = L.get("kind", "tone")
        d = float(L.get("dur", 0.3))
        n = secs(d)
        t = taxis(n)
        env = B.exp(t * -float(L.get("decay", 10)))
        if kind == "tone":
            f0, f1 = float(L.get("f0", 880)), float(L.get("f1", L.get("f0", 880)))
            ratio = f1 / f0 if f0 > 0 else 1.0
            fr = B.exp(t * (math.log(ratio) / max(d, 1e-6))) * f0 if ratio != 1.0 else B.zeros(n) + f0
            v = B.sin(B.cumsum(fr * (1.0 / SR)) * (2 * math.pi)) * env
        elif kind == "noise":
            v = noise(rng, n) * env
            filt = L.get("filter")
            if filt in ("lp", "hp", "bp"):
                v = biquad(v, filt, float(L.get("freq", 2000)), float(L.get("q", 0.9)))
        elif kind == "pluck":
            v = pluck(rng, float(L.get("f0", 440)), d)
        elif kind == "bell":
            v = bell(float(L.get("f0", 880)), d)
        else:
            raise ValueError(f"recipe layer kind {kind!r}: use tone | noise | pluck | bell")
        mix_into(out, v * B.minimum(B.arange(n) * (1 / 24.0), 1.0), float(L.get("at", 0)), float(L.get("gain", 1.0)))
    return out


def render_sfx(spec, seed: int | None = None):
    """spec = catalog id or {"invented", "why", "recipe": [layers]}. -> (vector, meta).
    Peak-normalised to 0.9 (about -0.9 dBFS); placed later with a relative gain."""
    if isinstance(spec, dict) and spec.get("invented"):
        name = "invented:" + str(spec["invented"])
        s = seed if seed is not None else 5000 + sum(ord(c) for c in name)
        v = _recipe(random.Random(s), spec.get("recipe") or [])
        meta = {"name": name, "invented": True, "why": spec.get("why", ""), "seed": s}
    else:
        name = str(spec)
        if name not in SFX:
            raise KeyError(f"unknown effect {name!r}; listed: {sorted(SFX)} — or pass "
                           f"{{'invented': ..., 'why': ..., 'recipe': [...]}}")
        s = seed if seed is not None else 1000 + sorted(SFX).index(name)
        v = SFX[name](random.Random(s))
        meta = {"name": name, "invented": False, "seed": s}
    v = normalize_peak(v, 0.9)
    meta.update({"seconds": round(len(v) / SR, 4), "sample_rate": SR, "backend": B.name})
    return v, meta


# --------------------------------------------------------------------------- spectral profile
PROFILE_BANDS = [62.5 * 2 ** (k / 3.0) for k in range(24)]      # 62.5 Hz .. ~12.7 kHz, third-octaves


def spectral_profile(v) -> dict:
    """Third-octave band levels (dB re the loudest band) + a 10-slice envelope (dB re peak)
    + duration. Used to prove effects are DISTINCT, not just differently named."""
    xs = B.tolist(v)
    n = len(xs)
    bands = []
    if _np is not None and n:
        x = _np.asarray(xs)
        nfft = 1 << max(10, (n - 1).bit_length())
        spec = _np.abs(_np.fft.rfft(x * _np.hanning(n), nfft)) ** 2
        fr = _np.fft.rfftfreq(nfft, 1.0 / SR)
        for fc in PROFILE_BANDS:
            lo, hi = fc / 2 ** (1 / 6.0), fc * 2 ** (1 / 6.0)
            m = (fr >= lo) & (fr < hi)
            bands.append(float(spec[m].sum()) if m.any() else 0.0)
    else:  # Goertzel at three frequencies per band, over the first 1.5 s (pure-Python fallback)
        sub = xs[: int(1.5 * SR)]
        for fc in PROFILE_BANDS:
            e = 0.0
            for f in (fc * 2 ** (-1 / 9.0), fc, fc * 2 ** (1 / 9.0)):
                w = 2 * math.cos(2 * math.pi * f / SR)
                s1 = s2 = 0.0
                for x in sub:
                    s1, s2 = x + w * s1 - s2, s1
                e += s1 * s1 + s2 * s2 - w * s1 * s2
            bands.append(e)
    top = max(bands) if bands and max(bands) > 0 else 1.0
    bands_db = [max(-60.0, 10 * math.log10(b / top)) if b > 0 else -60.0 for b in bands]
    env = []
    sl = max(1, n // 10)
    peak = max((abs(x) for x in xs), default=1.0) or 1.0
    for k in range(10):
        seg = xs[k * sl:(k + 1) * sl] or [0.0]
        r = math.sqrt(sum(x * x for x in seg) / len(seg))
        env.append(max(-60.0, db(r / peak)))
    return {"bands_db": bands_db, "env_db": env, "seconds": n / SR}


def profile_distance(a: dict, b: dict) -> float:
    """Mean absolute band difference (dB) + mean envelope difference (dB) / 2
    + 6 dB per doubling of duration."""
    bd = sum(abs(x - y) for x, y in zip(a["bands_db"], b["bands_db"])) / len(a["bands_db"])
    ed = sum(abs(x - y) for x, y in zip(a["env_db"], b["env_db"])) / len(a["env_db"])
    dd = 6.0 * abs(math.log2(max(a["seconds"], 1e-3) / max(b["seconds"], 1e-3)))
    return round(bd + ed / 2 + dd, 3)


# =========================================================================== wav i/o
def quantize16(v) -> bytes:
    if _np is not None and not isinstance(v, PV):
        q = _np.round(_np.clip(_np.asarray(v, dtype=float), -1.0, 1.0) * 32767.0).astype("<i2")
        return q.tobytes()
    a = array.array("h", [int(round(max(-1.0, min(1.0, x)) * 32767.0)) for x in B.tolist(v)])
    if sys.byteorder == "big":
        a.byteswap()
    return a.tobytes()


def write_wav(path, v, channels: int = 1, sr: int = SR) -> str:
    """16-bit PCM. channels=2 writes the mono vector to both channels (dual mono)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = quantize16(v)
    if channels == 2:
        if _np is not None:
            m = _np.frombuffer(raw, dtype="<i2")
            raw = _np.repeat(m, 2).astype("<i2").tobytes()
        else:
            out = bytearray()
            for i in range(0, len(raw), 2):
                out += raw[i:i + 2] * 2
            raw = bytes(out)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(raw)
    return str(path)


def _ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def _resample(xs, src: int, dst: int):
    """Mono resample: ffmpeg (mono in, mono out — no channel remix) or linear."""
    if src == dst:
        return B.array(xs)
    ff = _ffmpeg()
    if ff:
        r = subprocess.run([ff, "-v", "error", "-f", "f32le", "-ar", str(src), "-ac", "1", "-i", "pipe:0",
                            "-ar", str(dst), "-f", "f32le", "pipe:1"], input=_f32_bytes(B.array(xs)),
                           capture_output=True, timeout=300)
        if r.returncode == 0:
            return _from_f32(r.stdout, 1)
    xs = B.tolist(xs)
    m = int(len(xs) * dst / src)
    step = src / dst
    last = len(xs) - 1
    ys = []
    for i in range(m):
        x = i * step
        j = int(x)
        f = x - j
        k = j + 1 if j < last else last
        ys.append(xs[j] + (xs[k] - xs[j]) * f)
    return B.array(ys)


def read_audio(path, sr: int = SR):
    """Audio file -> mono float vector at `sr`. Channels are AVERAGED here (never left to a
    downmix matrix, which can sum dual-mono to +6 dB). 16-bit WAV is read directly; any
    other file is decoded by ffmpeg at its own channel count."""
    try:
        with wave.open(str(path), "rb") as w:
            ch, sw, fr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            raw = w.readframes(n)
        if sw != 2:
            raise wave.Error("not 16-bit")
        if _np is not None:
            a = _np.frombuffer(raw, dtype="<i2").astype(float) / 32768.0
            x = a.reshape(-1, ch).mean(axis=1) if ch > 1 else a
        else:
            a = array.array("h")
            a.frombytes(raw)
            if sys.byteorder == "big":
                a.byteswap()
            x = PV([sum(a[i:i + ch]) / (ch * 32768.0) for i in range(0, len(a), ch)])
        return _resample(x, fr, sr)
    except (wave.Error, EOFError):
        pass
    ff = _ffmpeg()
    if not ff:
        raise ValueError(f"{path}: not a 16-bit WAV and ffmpeg is not available to decode it")
    pr = subprocess.run([shutil.which("ffprobe") or "ffprobe", "-v", "error", "-select_streams", "a:0",
                         "-show_entries", "stream=channels", "-of", "csv=p=0", str(path)],
                        capture_output=True, text=True, timeout=60)
    ch = int((pr.stdout or "1").strip().split()[0] or 1) if pr.returncode == 0 else 1
    r = subprocess.run([ff, "-v", "error", "-i", str(path), "-ar", str(sr), "-f", "f32le", "-"],
                       capture_output=True, timeout=300)
    if r.returncode != 0:
        raise ValueError(f"{path}: ffmpeg could not decode it")
    if _np is not None:
        a = _np.frombuffer(r.stdout[: len(r.stdout) - len(r.stdout) % (4 * ch)], dtype="<f4").astype(float)
        return a.reshape(-1, ch).mean(axis=1) if ch > 1 else a
    v = _from_f32(r.stdout, 1)
    return PV([sum(v.d[i:i + ch]) / ch for i in range(0, len(v.d), ch)]) if ch > 1 else v


def _f32_bytes(v) -> bytes:
    if _np is not None and not isinstance(v, PV):
        return _np.asarray(v, dtype="<f4").tobytes()
    a = array.array("f", B.tolist(v))
    if sys.byteorder == "big":
        a.byteswap()
    return a.tobytes()


def _from_f32(raw: bytes, channels: int = 1):
    if _np is not None:
        x = _np.frombuffer(raw, dtype="<f4").astype(float)
        return x[0::channels] if channels > 1 else x
    a = array.array("f")
    a.frombytes(raw[: len(raw) - len(raw) % 4])
    if sys.byteorder == "big":
        a.byteswap()
    return PV([float(x) for x in a[0::channels]])


# =========================================================================== loudness
_JSON_RE = re.compile(r"\{\s*\"input_i\".*?\}", re.S)


def _parse_loudnorm(stderr: str) -> dict:
    m = _JSON_RE.search(stderr or "")
    if not m:
        raise RuntimeError("loudnorm printed no measurement JSON: " + (stderr or "")[-300:])
    return json.loads(m.group(0))


DUAL_MONO = "pan=stereo|c0=c0|c1=c0"


def loudnorm_measure(v) -> dict:
    """Pass 1: ffmpeg loudnorm measures the (dual-mono stereo) signal; returns its JSON."""
    ff = _ffmpeg()
    if not ff:
        raise RuntimeError("ffmpeg is required for loudness measurement")
    af = f"{DUAL_MONO},loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}:print_format=json"
    r = subprocess.run([ff, "-hide_banner", "-nostats", "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", "pipe:0",
                        "-af", af, "-f", "null", "-"], input=_f32_bytes(v), capture_output=True, timeout=600)
    return _parse_loudnorm(r.stderr.decode("utf-8", "replace"))


def loudnorm_apply(v, measured: dict) -> tuple:
    """Pass 2: normalise with the MEASURED values (linear mode). -> (mono vector, JSON)."""
    ff = _ffmpeg()
    af = (f"{DUAL_MONO},loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}"
          f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
          f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
          f":offset={measured['target_offset']}:linear=true:print_format=json")
    r = subprocess.run([ff, "-hide_banner", "-nostats", "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", "pipe:0",
                        "-af", af, "-ar", str(SR), "-f", "f32le", "pipe:1"],
                       input=_f32_bytes(v), capture_output=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError("loudnorm pass 2 failed: " + r.stderr.decode("utf-8", "replace")[-300:])
    return _from_f32(r.stdout, 2), _parse_loudnorm(r.stderr.decode("utf-8", "replace"))


_EBU_I = re.compile(r"Integrated loudness:\s*\n\s*I:\s*(-?[\d.]+|-inf)\s*LUFS")
_EBU_TP = re.compile(r"True peak:\s*\n\s*Peak:\s*(-?[\d.]+|-inf)\s*dBFS")


def measure_file(path) -> dict:
    """Independent measurement of a written file: ebur128 integrated loudness and true peak."""
    ff = _ffmpeg()
    if not ff:
        return {"status": "skipped", "reason": "ffmpeg not found"}
    r = subprocess.run([ff, "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true:framelog=quiet",
                        "-f", "null", "-"], capture_output=True, text=True, timeout=600)
    err = r.stderr or ""
    mi, mt = _EBU_I.search(err), _EBU_TP.search(err)
    if not mi or not mt:
        return {"status": "error", "reason": "ebur128 summary not found: " + err[-300:]}
    f = lambda s: float("-inf") if s == "-inf" else float(s)  # noqa: E731
    return {"status": "ok", "integrated_lufs": f(mi.group(1)), "true_peak_dbtp": f(mt.group(1)),
            "method": "ffmpeg ebur128 (EBU R128 / ITU-R BS.1770, 4x oversampled true peak)"}


def true_peak_estimate(v) -> list:
    """Per-sample true-peak estimate: max |x| over a 4x band-limited upsampling (numpy FFT);
    the pure backend uses |x| (the caller adds margin)."""
    if _np is None or isinstance(v, PV):
        return [abs(x) for x in B.tolist(v)]
    x = _np.asarray(v, dtype=float)
    n = len(x)
    if n == 0:
        return []
    X = _np.fft.rfft(x)
    up = _np.fft.irfft(X, 4 * n) * 4.0
    tp = _np.abs(up[: 4 * n]).reshape(n, 4).max(axis=1)
    return _np.maximum(tp, _np.abs(x))


def limiter_gain(v, ceiling: float, lookahead_s: float = 0.003, release_s: float = 0.08):
    """Look-ahead peak limiter as a GAIN ENVELOPE (applied identically to every stem so the
    stems keep summing to the mix). min-filter (radius W) -> box blur (radius W/2) ->
    slow release. Guarantees gain <= ceiling / true-peak at every sample."""
    tp = true_peak_estimate(v)
    n = len(tp)
    W = max(1, int(lookahead_s * SR))
    h = max(1, W // 2)
    if _np is not None and not isinstance(v, PV) and n:
        t = _np.asarray(tp, dtype=float)
        raw = _np.where(t > ceiling, ceiling / _np.maximum(t, 1e-12), 1.0)
        pad = _np.concatenate([_np.ones(W), raw, _np.ones(W)])
        mins = _np.lib.stride_tricks.sliding_window_view(pad, 2 * W + 1).min(axis=1)
        pref = _np.concatenate([[0.0], _np.cumsum(mins)])
        idx = _np.arange(n)
        lo, hi = _np.maximum(0, idx - h), _np.minimum(n, idx + h + 1)
        box = ((pref[hi] - pref[lo]) / (hi - lo)).tolist()
    else:
        raw = [1.0 if (p <= ceiling or p <= 0) else ceiling / p for p in (tp.tolist() if hasattr(tp, "tolist") else tp)]
        # sliding minimum (monotone deque), radius W
        from collections import deque
        mins = [1.0] * n
        dq = deque()
        for i in range(n + W):
            if i < n:
                while dq and raw[dq[-1]] >= raw[i]:
                    dq.pop()
                dq.append(i)
            c = i - W
            if c >= 0:
                while dq and dq[0] < c - W:
                    dq.popleft()
                mins[c] = raw[dq[0]]
        pref = [0.0]
        for x in mins:
            pref.append(pref[-1] + x)
        box = [(pref[min(n, i + h + 1)] - pref[max(0, i - h)]) / (min(n, i + h + 1) - max(0, i - h)) for i in range(n)]
    rel = 1.0 / max(1, int(release_s * SR))
    g, out = 1.0, []
    for x in box:
        g = min(x, g + (1.0 - g) * rel * 4)
        out.append(g)
    return B.array(out)


# =========================================================================== ducking
HOP_S = 0.01


def voice_envelope(voice) -> dict:
    """Narration RMS envelope in dB per 10 ms frame, with fast attack / slow release,
    plus the narration's ACTIVE level (95th percentile frame) that keys the duck."""
    xs = B.tolist(voice)
    hop = int(SR * HOP_S)
    nfr = len(xs) // hop + (1 if len(xs) % hop else 0)
    raw = []
    for k in range(nfr):
        fr = xs[k * hop:(k + 1) * hop]
        e = sum(x * x for x in fr) / max(1, len(fr))
        raw.append(10 * math.log10(e + 1e-12))
    srt = sorted(raw)
    p95 = srt[int(0.95 * (len(srt) - 1))] if srt else -120.0
    sm, env = -120.0, []
    for d in raw:
        sm += (d - sm) * (0.5 if d > sm else 0.035)
        env.append(sm)
    return {"raw_db": raw, "env_db": env, "active_db": p95, "hop_s": HOP_S}


def duck_curve(envd: dict, n: int, duck_db: float = 12.0, knee_db: float = 15.0,
               range_db: float = 30.0, lookahead_s: float = 0.08) -> tuple:
    """Per-sample gain for the music: full duck (−duck_db) when the voice envelope is within
    knee_db of its active level, none below active − range_db, linear in between. The
    curve looks ahead so the bed is already down when the first syllable starts.
    -> (gain vector, per-frame duck dB list)."""
    env = envd["env_db"]
    hi = envd["active_db"] - knee_db
    lo = envd["active_db"] - range_db
    la = max(0, int(round(lookahead_s / HOP_S)))
    amt = []
    for k in range(len(env)):
        e = max(env[k:k + la + 1]) if la else env[k]
        x = (e - lo) / max(1e-6, hi - lo)
        amt.append(-duck_db * (0.0 if x <= 0 else (1.0 if x >= 1 else x)))
    hop = int(SR * HOP_S)
    gains = [10 ** (a / 20.0) for a in amt] or [1.0]
    # linear interpolation between frame centres, per sample
    out = []
    for k in range(len(gains)):
        g0 = gains[k]
        g1 = gains[k + 1] if k + 1 < len(gains) else g0
        step = (g1 - g0) / hop
        out.extend(g0 + step * j for j in range(hop))
    out = out[:n] + [gains[-1]] * max(0, n - len(out))
    return B.array(out), amt


def voiced_mask(envd: dict, below_active_db: float = 20.0) -> list:
    """Frames where the narration is speaking (raw level within 20 dB of its active level)."""
    thr = envd["active_db"] - below_active_db
    return [d > thr for d in envd["raw_db"]]


def measure_duck(dry, ducked, mask: list) -> dict:
    """MEASURED duck: music RMS in the voiced frames without vs with ducking (dB)."""
    hop = int(SR * HOP_S)
    a = B.tolist(dry)
    b = B.tolist(ducked)
    ea = eb = 0.0
    cnt = 0
    for k, on in enumerate(mask):
        if not on:
            continue
        s, e = k * hop, min(len(a), (k + 1) * hop)
        ea += sum(x * x for x in a[s:e])
        eb += sum(x * x for x in b[s:e])
        cnt += e - s
    if cnt == 0 or eb <= 0:
        return {"voiced_s": 0.0, "duck_db": None}
    return {"voiced_s": round(cnt / SR, 3), "duck_db": round(10 * math.log10(ea / eb), 2),
            "music_dry_dbfs": round(10 * math.log10(ea / cnt + 1e-20), 2),
            "music_ducked_dbfs": round(10 * math.log10(eb / cnt + 1e-20), 2)}


# =========================================================================== mix
def _active_rms(v, rel_db: float = 30.0) -> float:
    """RMS over the frames within rel_db of the loudest 10 ms frame (the 'speaking' level)."""
    xs = B.tolist(v)
    hop = int(SR * HOP_S)
    frs = []
    for k in range(0, len(xs), hop):
        fr = xs[k:k + hop]
        frs.append(sum(x * x for x in fr) / max(1, len(fr)))
    if not frs or max(frs) <= 0:
        return 0.0
    thr = max(frs) * 10 ** (-rel_db / 10)
    act = [e for e in frs if e >= thr]
    return math.sqrt(sum(act) / len(act))


def mix(voice, out_dir, *, music=None, music_meta=None, sfx_events=None, music_rel_db: float = -16.0,
        duck_db: float = 12.0, lookahead_s: float = 0.08, write: bool = True) -> dict:
    """voice: mono vector at SR. music: mono vector (or None). sfx_events: [{"name" | invented
    spec, "at", "gain_db"?}]. Writes stems + mix (48 kHz stereo 16-bit) and returns the
    measured report. Loudness: measure -> (limit) -> loudnorm linear with the measured JSON
    -> measure the written file again."""
    out_dir = Path(out_dir)
    n = len(voice)
    voice = B.array(voice)
    v_act = _active_rms(voice) or 1e-4
    report = {"backend": B.name, "sample_rate": SR, "seconds": round(n / SR, 3),
              "voice_active_dbfs": round(db(v_act), 2), "targets": {"I": TARGET_I, "TP": TARGET_TP}}
    music_out = B.zeros(n)
    duck = {"enabled": False}
    if music is not None:
        m = B.array(music)
        m = m[:n] if len(m) >= n else B.concat([m, B.zeros(n - len(m))])
        m_act = rms(m) or 1e-9
        m = m * (v_act * 10 ** (music_rel_db / 20.0) / m_act)
        envd = voice_envelope(voice)
        g, _amt = duck_curve(envd, n, duck_db=duck_db, lookahead_s=lookahead_s)
        ducked = m * g
        mask = voiced_mask(envd)
        duck = {"enabled": True, "requested_db": duck_db, "music_rel_db": music_rel_db,
                "lookahead_s": lookahead_s, **measure_duck(m, ducked, mask)}
        music_out = ducked
    report["duck"] = duck
    sfx_out = B.zeros(n)
    placed = []
    for ev in sfx_events or []:
        spec = ev.get("invented") and ev or ev.get("name")
        v, meta = render_sfx(spec if not isinstance(spec, dict) else spec, ev.get("seed"))
        rel = float(ev.get("gain_db", _default_gain(meta["name"])))
        s_act = _active_rms(v) or 1e-9
        gain = v_act * 10 ** (rel / 20.0) / s_act
        mix_into(sfx_out, v, float(ev["at"]), gain)
        placed.append({"name": meta["name"], "at": float(ev["at"]), "end": round(float(ev["at"]) + len(v) / SR, 4),
                       "gain_db": rel, "invented": meta["invented"]})
    report["sfx"] = placed
    stems = {"voice": voice, "music": music_out, "sfx": sfx_out}
    mix0 = voice + music_out + sfx_out
    # ---- pass 1: measure
    p1 = loudnorm_measure(mix0)
    report["loudnorm_pass1"] = p1
    if not math.isfinite(float(p1["input_i"])):
        raise ValueError("the mix is silent: integrated loudness is -inf, nothing to normalise")
    lim = {"applied": False}
    margin = TP_MARGIN_DB + (1.0 if _np is None else 0.0)
    want_tp = TARGET_TP - margin
    raw_stems = dict(stems)
    raw_mix = mix0
    ceil_db = None
    for it in range(8):
        gain_db = TARGET_I - float(p1["input_i"])
        pred_tp = float(p1["input_tp"]) + gain_db
        if pred_tp <= want_tp + 0.05:
            break
        # lower the ceiling by the remaining shortfall; always limit the ORIGINAL stems
        ceil_db = (want_tp - gain_db) if ceil_db is None else ceil_db - (pred_tp - want_tp)
        g = limiter_gain(raw_mix, 10 ** (ceil_db / 20.0))
        stems = {k: v * g for k, v in raw_stems.items()}
        mix0 = stems["voice"] + stems["music"] + stems["sfx"]
        lim = {"applied": True, "iterations": it + 1, "ceiling_dbfs_pre_gain": round(ceil_db, 2),
               "min_gain_db": round(db(min(B.tolist(g))), 2)}
        p1 = loudnorm_measure(mix0)
        report["loudnorm_pass1_after_limit"] = p1
    report["limiter"] = lim
    # ---- pass 2: normalise with the measured JSON
    out, p2 = loudnorm_apply(mix0, p1)
    out = out[:n] if len(out) >= n else B.concat([out, B.zeros(n - len(out))])
    report["loudnorm_pass2"] = p2
    report["stems_exact"] = p2.get("normalization_type") == "linear"
    g_lin = B.dot(out, mix0) / max(1e-20, B.dot(mix0, mix0))
    report["linear_gain_db"] = round(db(g_lin), 3)
    stems = {k: s * g_lin for k, s in stems.items()}
    resid = out - (stems["voice"] + stems["music"] + stems["sfx"])
    report["stem_sum_residual_db"] = round(db(rms(resid) / max(1e-12, rms(out))), 2)
    if write:
        out_dir.mkdir(parents=True, exist_ok=True)
        paths = {"mix": write_wav(out_dir / "mix.wav", out, 2)}
        sd = out_dir / "stems"
        for k, s in stems.items():
            if k == "music" and music is None:
                continue
            if k == "sfx" and not placed:
                continue
            paths[k] = write_wav(sd / f"{k}.wav", s, 2)
        report["files"] = paths
        mm = measure_file(paths["mix"])
        report["measured"] = mm
        if mm.get("status") == "ok":
            report["targets_met"] = (abs(mm["integrated_lufs"] - TARGET_I) <= 1.0
                                     and mm["true_peak_dbtp"] <= TARGET_TP)
    if music_meta:
        report["music"] = {k: music_meta[k] for k in ("bpm", "beat_s", "bar_s", "offset_s", "downbeats", "seed",
                                                      "sections") if k in music_meta}
    return report


def _default_gain(name: str) -> float:
    for e in sfx_catalog().get("effects", []):
        if e.get("id") == name:
            return float(e.get("default_gain_db", -10))
    return -10.0


# =========================================================================== switch + build
def _flag(x):
    if x is None:
        return None
    if isinstance(x, dict):
        return x.get("enabled", True) is not False
    if isinstance(x, (list, tuple)):
        return len(x) > 0
    return bool(x)


def enabled(storyboard: dict | None = None, design_system: dict | None = None) -> dict:
    """Sound beyond the narration is OFF unless a flag turns it on:
    storyboard.audio / design_system.audio = true | {"enabled": bool, "music": bool|{...},
    "sfx": bool|[events]}. The storyboard wins over the design system.
    -> {"music": bool, "sfx": bool, "source": where the decision came from}."""
    res = {"music": False, "sfx": False, "source": "default (off)"}
    for name, doc in (("design_system", design_system), ("storyboard", storyboard)):
        a = doc.get("audio") if isinstance(doc, dict) else None
        if isinstance(a, bool):
            res.update({"music": a, "sfx": a, "source": f"{name}.audio"})
            continue
        if not isinstance(a, dict):
            continue
        on = a.get("enabled")
        if on is not None:
            res.update({"music": bool(on), "sfx": bool(on), "source": f"{name}.audio.enabled"})
        for k in ("music", "sfx"):
            f = _flag(a.get(k))
            if f is not None:
                res[k] = f and on is not False
                res["source"] = f"{name}.audio.{k}"
    return res


def _norm_word(s: str) -> str:
    return re.sub(r"^[^\w]+|[^\w]+$", "", str(s).replace("⟦", "").replace("⟧", "").lower())


def resolve_time(ev: dict, words: dict | None) -> float:
    """An event time: {"at": s} or {"line": name, "word": w, "occurrence"?: 1, "offset"?: s}.
    A word that is not in the line RAISES (the same rule as the scene runtime)."""
    if "at" in ev:
        return float(ev["at"])
    if not words:
        raise ValueError(f"event {ev} is keyed to a word but no word timings were given")
    ln = next((x for x in words.get("lines", []) if x.get("name") == ev.get("line")), None)
    if ln is None:
        raise ValueError(f"unknown line {ev.get('line')!r} in the word timings")
    key = _norm_word(ev.get("word", ""))
    hits = [w for w in ln.get("words", []) if _norm_word(w.get("w", "")) == key]
    occ = int(ev.get("occurrence", 1))
    if len(hits) < occ:
        raise ValueError(f"word {ev.get('word')!r} is NOT in line {ev.get('line')!r}")
    return float(hits[occ - 1]["start"]) + float(ev.get("offset", 0.0))


def load_json_or_js(path) -> dict:
    txt = Path(path).read_text(encoding="utf-8").strip()
    if txt.startswith("window."):
        txt = txt.split("=", 1)[1].strip().rstrip(";").strip()
    return json.loads(txt)


def build(voice_path, out_dir, storyboard: dict | None = None, design_system: dict | None = None,
          words: dict | None = None) -> dict:
    """The pipeline entry. OFF by default: the narration is returned untouched as the track
    and nothing else is written except audio_plan.json."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    sw = enabled(storyboard, design_system)
    plan = {"schema": 1, "switch": sw, "voice": str(voice_path)}
    if not (sw["music"] or sw["sfx"]):
        plan.update({"enabled": False, "track": str(voice_path), "music": None, "sfx": [],
                     "note": "sound beyond the narration is off by default; set storyboard.audio or "
                             "design_system.audio to enable music and/or effects"})
        (out_dir / "audio_plan.json").write_text(json.dumps(plan, indent=1), encoding="utf-8")
        return plan
    a = {}
    for doc in (design_system, storyboard):
        if isinstance(doc, dict) and isinstance(doc.get("audio"), dict):
            a.update(doc["audio"])
    voice = read_audio(voice_path)
    seconds = len(voice) / SR
    music = meta = None
    if sw["music"]:
        mc = a.get("music") if isinstance(a.get("music"), dict) else {}
        align_t = mc.get("align_downbeat_to")
        if isinstance(align_t, dict):
            align_t = resolve_time(align_t, words)
        music, meta = render_music(seconds, mc.get("sections"), seed=int(mc.get("seed", 7)),
                                   bpm=float(mc.get("bpm", 112)), align_downbeat_to=align_t)
    events = []
    if sw["sfx"]:
        for ev in (a.get("sfx") if isinstance(a.get("sfx"), list) else []):
            e = dict(ev)
            e["at"] = resolve_time(ev, words)
            events.append(e)
    rep = mix(voice, out_dir, music=music, music_meta=meta, sfx_events=events,
              music_rel_db=float((a.get("music") or {}).get("level_db", -16) if isinstance(a.get("music"), dict) else -16),
              duck_db=float(a.get("duck_db", 12)))
    plan.update({"enabled": True, "track": rep["files"]["mix"], "report": rep})
    (out_dir / "audio_plan.json").write_text(json.dumps(plan, indent=1), encoding="utf-8")
    return plan


# =========================================================================== CLI
def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Coordn8 local music, effects, ducking, loudness, stems.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("music")
    m.add_argument("--seconds", type=float, required=True)
    m.add_argument("--sections", default='[[0, "groove"]]')
    m.add_argument("--seed", type=int, default=7)
    m.add_argument("--bpm", type=float, default=112.0)
    m.add_argument("--align-downbeat-to", type=float, default=None)
    m.add_argument("--out", required=True)
    s = sub.add_parser("sfx")
    s.add_argument("--name")
    s.add_argument("--all", action="store_true")
    s.add_argument("--out")
    s.add_argument("--out-dir")
    sub.add_parser("list-sfx")
    x = sub.add_parser("mix")
    x.add_argument("--voice", required=True)
    x.add_argument("--out-dir", required=True)
    x.add_argument("--music-sections", default=None)
    x.add_argument("--seed", type=int, default=7)
    x.add_argument("--bpm", type=float, default=112.0)
    x.add_argument("--sfx-events", default=None, help="JSON list of {name, at, gain_db?}")
    x.add_argument("--duck-db", type=float, default=12.0)
    b = sub.add_parser("build")
    b.add_argument("--voice", required=True)
    b.add_argument("--out-dir", required=True)
    b.add_argument("--storyboard")
    b.add_argument("--design")
    b.add_argument("--words")
    me = sub.add_parser("measure")
    me.add_argument("file")
    a = ap.parse_args(argv)
    if a.cmd == "music":
        v, meta = render_music(a.seconds, json.loads(a.sections), seed=a.seed, bpm=a.bpm,
                               align_downbeat_to=a.align_downbeat_to)
        write_wav(a.out, v)
        print(json.dumps(meta))
    elif a.cmd == "sfx":
        names = sorted(SFX) if a.all else [a.name]
        for nm in names:
            v, meta = render_sfx(nm)
            p = Path(a.out_dir) / f"{nm}.wav" if a.out_dir else Path(a.out)
            write_wav(p, v)
            print(json.dumps({**meta, "path": str(p)}))
    elif a.cmd == "list-sfx":
        print(json.dumps({"backend": B.name, "effects": sorted(SFX)}))
    elif a.cmd == "mix":
        voice = read_audio(a.voice)
        music = meta = None
        if a.music_sections:
            music, meta = render_music(len(voice) / SR, json.loads(a.music_sections), seed=a.seed, bpm=a.bpm)
        ev = json.loads(a.sfx_events) if a.sfx_events else []
        print(json.dumps(mix(voice, a.out_dir, music=music, music_meta=meta, sfx_events=ev, duck_db=a.duck_db),
                         indent=1))
    elif a.cmd == "build":
        sb = load_json_or_js(a.storyboard) if a.storyboard else None
        ds = load_json_or_js(a.design) if a.design else None
        wd = load_json_or_js(a.words) if a.words else None
        print(json.dumps(build(a.voice, a.out_dir, sb, ds, wd), indent=1))
    elif a.cmd == "measure":
        print(json.dumps(measure_file(a.file)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
