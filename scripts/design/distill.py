#!/usr/bin/env python3
"""Style distillation (requirement A4): a reference video / image / folder -> a DRAFT style kit.

    python3 scripts/design/distill.py <video|image|folder> [--out kit.json] [--sheet sheet.png]
                                      [--name NAME] [--seed N]

Rule: MATCH THE STYLE, NEVER COPY THE CONTENT. What is measured and kept: colour roles, the
type scale, motion energy, cut rhythm. What is never kept: the reference's frames, words,
logos, characters, footage or file path — the kit records only a content hash of each source,
and the contact sheet is a private working aid written only where --sheet points.

Measured, in order (every number lands in the kit's `distilled` section):
  1. cut points — 10 fps greyscale 160x90: histogram difference + mean pixel difference, a
     cut where both together exceed --cut and the score is a local maximum (>= 0.4 s apart);
     the shot table, average shot length and cuts per minute follow;
  2. motion per second — mean absolute frame difference (0..1) per second, and per shot;
  3. keyframes — one per shot (long shots every 4 s), at most 24, into a contact sheet
     (4 columns, time label per cell, the distilled palette as a strip at the bottom);
  4. palette — k-means with k = 8 in OKLab (seeded k-means++, so the same input always gives
     the same clusters), then CIEDE2000 de-duplication: clusters closer than 5 are merged;
  5. accent — a SEPARATE k-means pass over saturated mid-lightness pixels only (OKLCH chroma
     >= 0.06, 0.30 <= L <= 0.85), so a small accent is not drowned by a large background. The
     accent is guaranteed to differ from the background family by CIEDE2000 >= 20; when no
     measured colour does, one is synthesised and the kit says so;
  6. type sizes — text-like row bands on each keyframe (many ink/paper transitions per row),
     band height scaled to 1080p, then clustered into title / body / caption estimates;
  7. draft kit — the nearest shipped kit (same skin, closest motion and accent hue) is the
     STARTING POINT; palette (completed for both skins and contrast-checked through the brand
     palette builder), type scale, motion signature and transitions are replaced by the
     measured ones; the kit is then judged by the kit outcome rules and the palette gate.

Dependencies: ffmpeg/ffprobe for video (and for images when present); numpy speeds up the
frame maths (all sums are exact integers, so the numpy and pure-Python paths give the SAME
kit); without ffmpeg a PNG/PPM image is still read by a pure-Python decoder (Pillow is used
for other formats when present) and a video is SKIPPED with the reason.
Exit codes: 0 PASS, 1 REVIEW (kit produced with findings), 2 SKIPPED, 3 ERROR. Python 3.9+.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import random
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from lib.common import EXIT_ERROR, EXIT_PASS, EXIT_REVIEW, EXIT_SKIPPED, emit  # noqa: E402
from design import brand as brandlib  # noqa: E402
from design import pick_kit as pk  # noqa: E402
from design.color import delta_e, hue_distance, oklch_hex, rgb_to_oklab, rgb_to_oklch, to_hex  # noqa: E402

GATE = "distill"
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".gif"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".ppm", ".pnm"}
K = 8
ACCENT_K = 4
DEDUP_DE = 5.0
BACKGROUND_FAMILY_DE = 12.0
ACCENT_MIN_DE = 20.0
ACCENT_MIN_C = 0.06
ACCENT_L = (0.30, 0.85)
SAMPLE_CAP = 8000
CUT_THRESHOLD = 0.30
GRAY_W, GRAY_H, GRAY_FPS = 160, 90, 10
PAL_W, PAL_H = 96, 54
KEY_W, KEY_H = 640, 360
CELL_W, CELL_H = 240, 135
MAX_KEYFRAMES = 24
MAX_SOURCES = 24
TEXT_SPAN_EM = 0.92          # ascender-to-descender band of a text line, as a share of the em
DEFAULT_SEED = 8


# ======================================================================  capabilities
def numpy_mod():
    if os.environ.get("COORDN8_NO_NUMPY"):
        return None
    try:
        import numpy  # type: ignore
        return numpy
    except Exception:
        return None


def ffmpeg_bins() -> Optional[Tuple[str, str]]:
    if os.environ.get("COORDN8_NO_FFMPEG"):
        return None
    f, p = shutil.which("ffmpeg"), shutil.which("ffprobe")
    return (f, p) if f and p else None


def _run(args: List[str]) -> bytes:
    r = subprocess.run(args, capture_output=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "replace")[-400:])
    return r.stdout


def probe(path: Path, ff) -> dict:
    out = _run([ff[1], "-v", "error", "-show_entries",
                "format=duration:stream=codec_type,width,height,r_frame_rate,nb_frames", "-of", "json", str(path)])
    d = json.loads(out.decode("utf-8"))
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), None)
    if v is None:
        raise RuntimeError("no video stream")
    num, den = (v.get("r_frame_rate") or "30/1").split("/")
    fps = int(num) / max(1, int(den))
    dur = float((d.get("format") or {}).get("duration") or 0.0)
    return {"duration": round(dur, 3), "width": int(v["width"]), "height": int(v["height"]), "fps": round(fps, 3)}


def raw_frames(path: Path, ff, vf: str, pix: str, extra: Sequence[str] = (), out: Sequence[str] = ()) -> bytes:
    return _run([ff[0], "-v", "error", *extra, "-i", str(path), "-vf", vf, *out,
                 "-f", "rawvideo", "-pix_fmt", pix, "-"])


# ======================================================================  pure-Python image I/O
def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if pa <= pb and pa <= pc else (b if pb <= pc else c)


def read_png(data: bytes) -> Tuple[int, int, bytes]:
    """8-bit, non-interlaced PNG (grey, grey+alpha, RGB, RGBA, palette) -> (w, h, rgb bytes)."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat, plte = 8, [], b""
    w = h = depth = ctype = inter = None
    while pos < len(data):
        ln, typ = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + ln]
        pos += 12 + ln
        if typ == b"IHDR":
            w, h, depth, ctype, _, _, inter = struct.unpack(">IIBBBBB", body)
        elif typ == b"PLTE":
            plte = body
        elif typ == b"IDAT":
            idat.append(body)
        elif typ == b"IEND":
            break
    if depth != 8 or inter != 0 or ctype not in (0, 2, 3, 4, 6):
        raise ValueError(f"unsupported PNG (bit depth {depth}, colour type {ctype}, interlace {inter})")
    bpp = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    raw = zlib.decompress(b"".join(idat))
    stride = w * bpp
    out = bytearray()
    prev = bytearray(stride)
    i = 0
    for _ in range(h):
        f = raw[i]
        line = bytearray(raw[i + 1:i + 1 + stride])
        i += 1 + stride
        for x in range(stride):
            a = line[x - bpp] if x >= bpp else 0
            b = prev[x]
            c = prev[x - bpp] if x >= bpp else 0
            if f == 1:
                line[x] = (line[x] + a) & 255
            elif f == 2:
                line[x] = (line[x] + b) & 255
            elif f == 3:
                line[x] = (line[x] + ((a + b) >> 1)) & 255
            elif f == 4:
                line[x] = (line[x] + _paeth(a, b, c)) & 255
        prev = line
        if ctype == 2:
            out += line
        elif ctype == 6:
            for x in range(0, stride, 4):
                out += line[x:x + 3]
        elif ctype == 0:
            for v in line:
                out += bytes((v, v, v))
        elif ctype == 4:
            for x in range(0, stride, 2):
                out += bytes((line[x],) * 3)
        else:
            for v in line:
                out += plte[v * 3:v * 3 + 3]
    return w, h, bytes(out)


def read_ppm(data: bytes) -> Tuple[int, int, bytes]:
    toks, pos = [], 0
    while len(toks) < 4:
        while data[pos:pos + 1].isspace():
            pos += 1
        if data[pos:pos + 1] == b"#":
            while data[pos:pos + 1] not in (b"\n", b""):
                pos += 1
            continue
        st = pos
        while not data[pos:pos + 1].isspace():
            pos += 1
        toks.append(data[st:pos])
    if toks[0] != b"P6" or int(toks[3]) != 255:
        raise ValueError("only binary 8-bit PPM (P6) is read")
    w, h = int(toks[1]), int(toks[2])
    return w, h, data[pos + 1:pos + 1 + w * h * 3]


def write_png(path: Path, w: int, h: int, rgb: bytes) -> None:
    def chunk(t, b):
        return struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b) & 0xffffffff)
    rows = b"".join(b"\x00" + rgb[y * w * 3:(y + 1) * w * 3] for y in range(h))
    data = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))
    Path(path).write_bytes(data)


def resize_nearest(w: int, h: int, rgb: bytes, nw: int, nh: int) -> bytes:
    out = bytearray(nw * nh * 3)
    for y in range(nh):
        sy = min(h - 1, (y * h) // nh)
        row = sy * w * 3
        for x in range(nw):
            sx = min(w - 1, (x * w) // nw)
            o = (y * nw + x) * 3
            out[o:o + 3] = rgb[row + sx * 3:row + sx * 3 + 3]
    return bytes(out)


def load_image(path: Path, ff) -> Tuple[int, int, bytes, str]:
    """-> (w, h, rgb, decoder) at KEY_W x KEY_H."""
    if ff:
        b = raw_frames(path, ff, f"scale={KEY_W}:{KEY_H}", "rgb24", out=("-frames:v", "1"))
        return KEY_W, KEY_H, b[:KEY_W * KEY_H * 3], "ffmpeg"
    data = path.read_bytes()
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            w, h, rgb = read_png(data)
            dec = "pure-python png"
        elif data[:2] == b"P6":
            w, h, rgb = read_ppm(data)
            dec = "pure-python ppm"
        else:
            raise ValueError("needs ffmpeg or Pillow")
    except ValueError:
        try:
            from PIL import Image  # type: ignore
        except Exception:
            raise ValueError(f"cannot decode {path.suffix} without ffmpeg or Pillow")
        im = Image.open(str(path)).convert("RGB")
        w, h, rgb, dec = im.width, im.height, im.tobytes(), "pillow"
    return KEY_W, KEY_H, resize_nearest(w, h, rgb, KEY_W, KEY_H), dec


# ======================================================================  frame maths (exact)
def frame_diffs(gray: bytes, n: int, w: int, h: int, np) -> Tuple[List[float], List[float]]:
    """Per consecutive pair: (mean |dpixel| / 255, half L1 histogram distance, 32 bins).
    Both computed from integer sums, so numpy and pure Python give identical floats."""
    px = w * h
    pd, hd = [], []
    if n < 2:
        return pd, hd
    if np is not None:
        a = np.frombuffer(gray[:n * px], dtype=np.uint8).reshape(n, px)
        sums = np.abs(a[1:].astype(np.int16) - a[:-1].astype(np.int16)).sum(axis=1, dtype=np.int64)
        bins = (a >> 3).astype(np.int64)
        hist = np.stack([np.bincount(bins[i], minlength=32) for i in range(n)])
        hsum = np.abs(hist[1:] - hist[:-1]).sum(axis=1)
        pd = [int(s) / (px * 255) for s in sums.tolist()]
        hd = [0.5 * int(s) / px for s in hsum.tolist()]
        return pd, hd
    hists = []
    for i in range(n):
        hcount = [0] * 32
        for v in gray[i * px:(i + 1) * px]:
            hcount[v >> 3] += 1
        hists.append(hcount)
    for i in range(n - 1):
        f0 = gray[i * px:(i + 1) * px]
        f1 = gray[(i + 1) * px:(i + 2) * px]
        pd.append(sum(abs(x - y) for x, y in zip(f0, f1)) / (px * 255))
        hd.append(0.5 * sum(abs(x - y) for x, y in zip(hists[i], hists[i + 1])) / px)
    return pd, hd


def detect_cuts(pd: List[float], hd: List[float], fps: float, threshold: float) -> List[float]:
    score = [a + b for a, b in zip(hd, pd)]
    cuts: List[float] = []
    for i, s in enumerate(score):
        local = score[max(0, i - 5):i + 6]
        t = round((i + 1) / fps, 2)
        if s > threshold and s >= max(local) and (not cuts or t - cuts[-1] > 0.4):
            cuts.append(t)
    return cuts


def sample_pixels(rgb: bytes, cap: int) -> List[Tuple[int, int, int]]:
    n = len(rgb) // 3
    if n == 0:
        return []
    m = min(cap, n)
    return [tuple(rgb[(i * n // m) * 3:(i * n // m) * 3 + 3]) for i in range(m)]  # type: ignore


# ======================================================================  k-means in OKLab
_LAB_CACHE: Dict[Tuple[int, int, int], Tuple[float, float, float]] = {}


def oklab(p) -> Tuple[float, float, float]:
    v = _LAB_CACHE.get(p)
    if v is None:
        v = rgb_to_oklab(p)
        _LAB_CACHE[p] = v
    return v


def _d2(a, b) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2


def lab_hex(c) -> str:
    L, a, b = c
    return oklch_hex(L, math.hypot(a, b), math.degrees(math.atan2(b, a)) % 360)


def kmeans(points: List[Tuple[float, float, float]], k: int, seed: int, iters: int = 30) -> List[Tuple[tuple, int]]:
    """Deterministic k-means++ (seeded) -> [(centre, count)] sorted by count desc."""
    n = len(points)
    if n == 0:
        return []
    rng = random.Random(seed)
    centres = [points[rng.randrange(n)]]
    d2 = [_d2(p, centres[0]) for p in points]
    while len(centres) < k:
        tot = sum(d2)
        if tot <= 1e-12:
            break
        r = rng.random() * tot
        acc, idx = 0.0, n - 1
        for i, d in enumerate(d2):
            acc += d
            if acc >= r:
                idx = i
                break
        centres.append(points[idx])
        d2 = [min(d, _d2(p, centres[-1])) for d, p in zip(d2, points)]
    assign = [-1] * n
    for _ in range(iters):
        changed = False
        sums = [[0.0, 0.0, 0.0, 0] for _ in centres]
        for i, p in enumerate(points):
            j = min(range(len(centres)), key=lambda c: _d2(p, centres[c]))
            if j != assign[i]:
                assign[i] = j
                changed = True
            s = sums[j]
            s[0] += p[0]
            s[1] += p[1]
            s[2] += p[2]
            s[3] += 1
        centres = [(s[0] / s[3], s[1] / s[3], s[2] / s[3]) if s[3] else c for s, c in zip(sums, centres)]
        if not changed:
            break
    counts = [0] * len(centres)
    for j in assign:
        counts[j] += 1
    out = [(c, n_) for c, n_ in zip(centres, counts) if n_]
    out.sort(key=lambda cn: (-cn[1], lab_hex(cn[0])))
    return out


def dedupe(clusters: List[Tuple[tuple, int]], limit: float = DEDUP_DE) -> Tuple[List[Tuple[tuple, int]], int]:
    """Merge clusters closer than `limit` CIEDE2000 (weighted mean in OKLab). Returns (clusters, merges)."""
    cl = list(clusters)
    merges = 0
    while True:
        best = None
        for i in range(len(cl)):
            for j in range(i + 1, len(cl)):
                d = delta_e(lab_hex(cl[i][0]), lab_hex(cl[j][0]))
                if d < limit and (best is None or d < best[0]):
                    best = (d, i, j)
        if best is None:
            break
        _, i, j = best
        (a, na), (b, nb) = cl[i], cl[j]
        t = na + nb
        m = tuple((a[q] * na + b[q] * nb) / t for q in range(3))
        cl = [c for x, c in enumerate(cl) if x not in (i, j)] + [(m, t)]
        merges += 1
    cl.sort(key=lambda cn: (-cn[1], lab_hex(cn[0])))
    return cl, merges


# ======================================================================  type sizes
def text_bands(rgb: bytes, w: int, h: int, np) -> List[int]:
    """Heights (rows) of text-like bands: rows with many ink/paper transitions."""
    if np is not None:
        a = np.frombuffer(rgb[:w * h * 3], dtype=np.uint8).reshape(h, w, 3).astype(np.int32)
        lum = (54 * a[:, :, 0] + 183 * a[:, :, 1] + 19 * a[:, :, 2]) >> 8
        hist = np.bincount((lum >> 3).ravel(), minlength=32)
        bg = int(np.argmax(hist)) * 8 + 4
        ink = (np.abs(lum - bg) > 64).astype(np.int32)
        dens = ink.sum(axis=1).tolist()
        trans = np.abs(np.diff(ink, axis=1)).sum(axis=1).tolist()
    else:
        lum = [(54 * rgb[i] + 183 * rgb[i + 1] + 19 * rgb[i + 2]) >> 8 for i in range(0, w * h * 3, 3)]
        hc = [0] * 32
        for v in lum:
            hc[v >> 3] += 1
        bg = max(range(32), key=lambda i: (hc[i], -i)) * 8 + 4
        dens, trans = [], []
        for y in range(h):
            row = [1 if abs(v - bg) > 64 else 0 for v in lum[y * w:(y + 1) * w]]
            dens.append(sum(row))
            trans.append(sum(1 for x in range(1, w) if row[x] != row[x - 1]))
    min_t = max(8, int(w * 0.025))
    texty = [trans[y] >= min_t and 0.01 * w <= dens[y] <= 0.75 * w for y in range(h)]
    bands, y = [], 0
    while y < h:
        if texty[y]:
            st = y
            gap = 0
            while y < h and (texty[y] or gap < 1):
                gap = 0 if texty[y] else gap + 1
                y += 1
            end = y - gap
            if end - st >= 3:
                bands.append(end - st)
        else:
            y += 1
    return bands


def type_estimates(band_rows: List[int], frame_h: int) -> dict:
    px = sorted((round(b * 1080 / frame_h / TEXT_SPAN_EM) for b in band_rows), reverse=True)
    if not px:
        return {"found": False, "why": "no text-like rows in any keyframe"}
    groups: List[List[int]] = []
    for v in px:
        if groups and v >= 0.85 * groups[-1][0]:
            groups[-1].append(v)
        else:
            groups.append([v])
    med = [sorted(g)[len(g) // 2] for g in groups]
    title = med[0]
    if len(groups) == 1:
        body = caption = med[0]
    else:
        bi = 1 + max(range(len(groups) - 1), key=lambda i: (len(groups[i + 1]), -i))
        body = med[bi]
        caption = med[-1] if med[-1] < body else body
    return {"found": True, "bands": len(px), "observed_px_1080p": px[:40],
            "clusters": [{"px": m, "count": len(g)} for m, g in zip(med, groups)],
            "title_px": title, "body_px": body, "caption_px": caption,
            "method": f"text-like row bands (>= 2.5% ink/paper transitions per row), band height scaled to "
                      f"1080p and divided by {TEXT_SPAN_EM} (ascender-to-descender span of a line, in em)"}


def scale_from(est: dict, base_scale: dict) -> Tuple[dict, str]:
    if not est.get("found"):
        return dict(base_scale), "kept the starting kit's scale (no text measured)"
    clamp = lambda v, lo, hi: int(max(lo, min(hi, round(v))))  # noqa: E731
    body = clamp(est["body_px"], 28, 64)
    title = clamp(max(est["title_px"], body * 1.25), max(44, body + 8), 160)
    caption = clamp(min(est["caption_px"], body), 28, body)
    h1 = clamp(body + (title - body) * 0.62, body, title)
    h2 = clamp(body + (title - body) * 0.3, body, h1)
    sc = {"title": title, "h1": h1, "h2": h2, "body": body, "caption": caption, "label": max(28, caption)}
    for k in base_scale:
        sc.setdefault(k, max(28, int(base_scale[k])))
    return sc, "measured (clamped to >= 28 px at 1080p and ordered title >= h1 >= h2 >= body >= caption)"


# ======================================================================  per-source analysis
def analyse_video(path: Path, ff, np, cut: float) -> dict:
    info = probe(path, ff)
    dur = info["duration"]
    gray = raw_frames(path, ff, f"fps={GRAY_FPS},scale={GRAY_W}:{GRAY_H}", "gray")
    n = len(gray) // (GRAY_W * GRAY_H)
    pd, hd = frame_diffs(gray, n, GRAY_W, GRAY_H, np)
    del gray
    cuts = [c for c in detect_cuts(pd, hd, GRAY_FPS, cut) if c < dur - 0.05]
    bounds = [0.0] + cuts + [round(dur, 2)]
    shots = []
    for i in range(len(bounds) - 1):
        a0 = int(bounds[i] * GRAY_FPS)
        a1 = max(a0 + 1, int(bounds[i + 1] * GRAY_FPS) - 1)
        seg = pd[a0:a1]
        shots.append({"i": i + 1, "start": bounds[i], "end": bounds[i + 1],
                      "len": round(bounds[i + 1] - bounds[i], 2),
                      "motion": round(sum(seg) / len(seg), 4) if seg else 0.0})
    per_sec = []
    for s in range(int(math.ceil(dur))):
        seg = pd[s * GRAY_FPS:(s + 1) * GRAY_FPS]
        per_sec.append(round(sum(seg) / len(seg), 4) if seg else 0.0)
    pfps = max(0.5, min(4.0, 48.0 / max(dur, 0.1)))
    prgb = raw_frames(path, ff, f"fps={pfps:.3f},scale={PAL_W}:{PAL_H}", "rgb24")
    times = []
    for sh in shots:
        if sh["len"] > 8:
            t = sh["start"] + 2
            while t < sh["end"] - 1:
                times.append(round(t, 2))
                t += 4
        else:
            times.append(round(sh["start"] + sh["len"] / 2, 2))
    times = times[:MAX_KEYFRAMES]
    keys = []
    for t in times:
        b = raw_frames(path, ff, f"scale={KEY_W}:{KEY_H}", "rgb24", extra=("-ss", f"{t:.3f}"),
                       out=("-frames:v", "1"))[:KEY_W * KEY_H * 3]
        if len(b) == KEY_W * KEY_H * 3:
            keys.append((t, b))
    return {"kind": "video", "info": info, "cuts": cuts, "shots": shots, "motion_per_sec": per_sec,
            "palette_rgb": prgb, "keys": keys}


def analyse_image(path: Path, ff) -> dict:
    w, h, rgb, dec = load_image(path, ff)
    return {"kind": "image", "info": {"width": w, "height": h, "decoder": dec}, "palette_rgb":
            resize_nearest(w, h, rgb, PAL_W, PAL_H), "keys": [(None, rgb)]}


def collect_sources(target: Path) -> List[Path]:
    if target.is_file():
        return [target]
    out = []
    for p in sorted(target.rglob("*")):
        rel = p.relative_to(target)
        if len(rel.parts) > 3 or any(x.startswith(".") for x in rel.parts):
            continue
        if p.is_file() and p.suffix.lower() in VIDEO_EXT | IMAGE_EXT:
            out.append(p)
    return out[:MAX_SOURCES]


def sha16(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


# ======================================================================  contact sheet
_FONT = {"0": "111101101101111", "1": "010110010010111", "2": "111001111100111", "3": "111001111001111",
         "4": "101101111001001", "5": "111100111001111", "6": "111100111101111", "7": "111001001001001",
         "8": "111101111101111", "9": "111101111001111", ".": "000000000000010", "s": "000011110011110",
         "-": "000000111000000", "#": "101111101111101"}


def _blit_text(buf: bytearray, W: int, x0: int, y0: int, text: str, scale: int = 2, col=(255, 255, 255)):
    for ci, ch in enumerate(text):
        g = _FONT.get(ch)
        if not g:
            continue
        for gy in range(5):
            for gx in range(3):
                if g[gy * 3 + gx] == "1":
                    for dy in range(scale):
                        for dx in range(scale):
                            x = x0 + ci * 4 * scale + gx * scale + dx
                            y = y0 + gy * scale + dy
                            o = (y * W + x) * 3
                            if 0 <= o < len(buf) - 2:
                                buf[o:o + 3] = bytes(col)


def contact_sheet(path: Path, keys: List[Tuple[Optional[float], bytes]], palette: List[dict],
                  accent_hex: str) -> dict:
    cols = 4
    rows = max(1, math.ceil(len(keys) / cols))
    strip = 40
    W, H = cols * CELL_W, rows * CELL_H + strip
    buf = bytearray(W * H * 3)
    for i, (t, rgb) in enumerate(keys):
        cell = resize_nearest(KEY_W, KEY_H, rgb, CELL_W, CELL_H)
        cx, cy = (i % cols) * CELL_W, (i // cols) * CELL_H
        for y in range(CELL_H):
            o = ((cy + y) * W + cx) * 3
            buf[o:o + CELL_W * 3] = cell[y * CELL_W * 3:(y + 1) * CELL_W * 3]
        label = f"{t:.1f}s" if t is not None else "-"
        for y in range(cy, cy + 14):
            o = (y * W + cx) * 3
            buf[o:o + 4 * 2 * len(label) * 3 + 12] = bytes(4 * 2 * len(label) * 3 + 12)
        _blit_text(buf, W, cx + 3, cy + 2, label)
    x = 0
    total = sum(p["share"] for p in palette) or 1
    sw = []
    for p in palette:
        wpx = max(2, int(round((W - 60) * p["share"] / total)))
        sw.append((p["hex"], x, min(W - 60, x + wpx)))
        x += wpx
    sw.append((accent_hex, W - 56, W))
    for hx, x0, x1 in sw:
        r, g, b = int(hx[1:3], 16), int(hx[3:5], 16), int(hx[5:7], 16)
        for y in range(rows * CELL_H + 4, H - 4):
            for xx in range(x0, x1):
                o = (y * W + xx) * 3
                buf[o:o + 3] = bytes((r, g, b))
    _blit_text(buf, W, W - 54, rows * CELL_H + 6, "#", 2, (255, 255, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    write_png(path, W, H, bytes(buf))
    return {"path": str(path), "width": W, "height": H, "cells": len(keys), "columns": cols,
            "note": "a private working aid of the reference's look; never ship it or copy its content"}


# ======================================================================  distil
def distil(target: Path, *, seed: int = DEFAULT_SEED, cut: float = CUT_THRESHOLD, name: Optional[str] = None,
           sheet: Optional[Path] = None) -> Tuple[int, dict, Optional[dict]]:
    ff = ffmpeg_bins()
    np = numpy_mod()
    if not target.exists():
        return EXIT_ERROR, {"gate": GATE, "status": "ERROR", "summary": f"no such input: {target}"}, None
    sources = collect_sources(target)
    if not sources:
        return EXIT_SKIPPED, {"gate": GATE, "status": "SKIPPED",
                              "summary": f"no video or image found in {target}"}, None
    analysed, skipped = [], []
    for src in sources:
        kind = "video" if src.suffix.lower() in VIDEO_EXT else "image"
        try:
            if kind == "video":
                if not ff:
                    skipped.append({"source": src.name, "why": "video needs ffmpeg + ffprobe, which are not "
                                                               "available here (install ffmpeg to read it)"})
                    continue
                a = analyse_video(src, ff, np, cut)
            else:
                a = analyse_image(src, ff)
        except (RuntimeError, ValueError, OSError, struct.error, zlib.error) as e:
            skipped.append({"source": src.name, "why": f"unreadable: {str(e)[:200]}"})
            continue
        a["name"] = src.name
        a["sha256_16"] = sha16(src)
        analysed.append(a)
    if not analysed:
        return EXIT_SKIPPED, {"gate": GATE, "status": "SKIPPED", "skipped": skipped,
                              "summary": "nothing could be read: " + "; ".join(s["why"] for s in skipped)}, None

    # ---------------- palette (k-means k=8 + CIEDE2000 de-dup) and the separate accent pass
    per = max(1, SAMPLE_CAP // len(analysed))
    pix: List[tuple] = []
    for a in analysed:
        pix += sample_pixels(a.pop("palette_rgb"), per)
    labs = [oklab(p) for p in pix]
    raw = kmeans(labs, K, seed)
    clusters, merges = dedupe(raw)
    total = sum(n for _, n in clusters)
    palette = []
    for c, n in clusters:
        hx = lab_hex(c)
        L, C, h = rgb_to_oklch(hx)
        palette.append({"hex": hx, "share": round(n / total, 4), "L": round(L, 3), "C": round(C, 4), "h": round(h, 1)})
    paper = palette[0]
    family = [p for p in palette if delta_e(p["hex"], paper["hex"]) < BACKGROUND_FAMILY_DE]
    lch = [(lab[0], math.hypot(lab[1], lab[2])) for lab in labs]
    acc_pts = [lab for lab, (L, C) in zip(labs, lch) if C >= ACCENT_MIN_C and ACCENT_L[0] <= L <= ACCENT_L[1]]
    accent_pass = {"rule": f"OKLCH chroma >= {ACCENT_MIN_C} and {ACCENT_L[0]} <= L <= {ACCENT_L[1]}",
                   "pixels": len(acc_pts), "pixel_share": round(len(acc_pts) / max(1, len(labs)), 4),
                   "candidates": []}
    accent = None
    if len(acc_pts) >= max(20, len(labs) // 500):
        acl, _ = dedupe(kmeans(acc_pts, ACCENT_K, seed + 1))
        at = sum(n for _, n in acl)
        cands = []
        for c, n in acl:
            hx = lab_hex(c)
            de_bg = min(delta_e(hx, p["hex"]) for p in family)
            C = rgb_to_oklch(hx)[1]
            cands.append({"hex": hx, "share_of_accent": round(n / at, 4), "C": round(C, 4),
                          "score": round(n / at * C, 5), "delta_e_to_background_family": round(de_bg, 2),
                          "eligible": de_bg >= ACCENT_MIN_DE})
        cands.sort(key=lambda c: (-c["score"], c["hex"]))
        accent_pass["candidates"] = cands
        ok = [c for c in cands if c["eligible"]]
        if ok:
            accent = {"hex": ok[0]["hex"], "source": "measured (accent pass)"}
    if accent is None:
        chromatic = [p for p in palette if p["C"] >= 0.03 and p not in family]
        hue = chromatic[0]["h"] if chromatic else ((paper["h"] + 180) % 360 if paper["C"] >= 0.02 else 250.0)
        L = 0.55 if paper["L"] > 0.5 else 0.72
        hx = oklch_hex(L, 0.15, hue)
        for _ in range(20):
            if min(delta_e(hx, p["hex"]) for p in family) >= ACCENT_MIN_DE:
                break
            L += -0.03 if paper["L"] > 0.5 else 0.03
            hx = oklch_hex(L, 0.15, hue)
        accent = {"hex": hx, "source": "synthesised",
                  "why": "no saturated mid-lightness colour in the reference differs from its background "
                         f"family by >= {ACCENT_MIN_DE} CIEDE2000; an accent at hue {hue:.0f} was made"}
    base_skin = "dark" if paper["L"] < 0.5 else "light"
    ink = max(palette, key=lambda p: brandlib.contrast(p["hex"], paper["hex"]))
    given = {"paper": paper["hex"], "accent": accent["hex"]}
    mapping_note = {"paper": "largest k-means cluster by area", "accent": accent["source"]}
    if brandlib.contrast(ink["hex"], paper["hex"]) >= 3.0 and delta_e(ink["hex"], accent["hex"]) >= 12:
        given["ink"] = ink["hex"]
        mapping_note["ink"] = "cluster with the highest contrast against the paper"
    pal = brandlib.complete_palette(given, given_skin=base_skin, name="invented:distilled")

    # ---------------- motion and rhythm
    videos = [a for a in analysed if a["kind"] == "video"]
    per_sec = [v for a in videos for v in a["motion_per_sec"]]
    shots = [s for a in videos for s in a["shots"]]
    dur = sum(a["info"]["duration"] for a in videos)
    cuts_total = sum(len(a["cuts"]) for a in videos)
    if per_sec:
        mean_motion = round(sum(per_sec) / len(per_sec), 4)
        energy = "calm" if mean_motion < 0.008 else ("moderate" if mean_motion < 0.03 else "lively")
        avg_shot = round(dur / max(1, len(shots)), 2)
        cpm = round(cuts_total / dur * 60, 2) if dur > 0 else 0.0
    else:
        mean_motion = energy = avg_shot = cpm = None

    # ---------------- type
    bands, kh = [], KEY_H
    keys = [(t, rgb) for a in analysed for (t, rgb) in a["keys"]][:MAX_KEYFRAMES]
    for _, rgb in keys:
        bands += text_bands(rgb, KEY_W, KEY_H, np)
    est = type_estimates(bands, kh)

    # ---------------- start from the nearest shipped kit (a starting point, never a fence)
    kits = pk.load_kits()
    target_enter = {"calm": 640, "moderate": 420, "lively": 260}.get(energy or "", None)
    acc_h = rgb_to_oklch(pal["roles"]["accent"])[2]

    def kscore(k):
        km = (k.get("motion") or {}).get("enter_ms", 400)
        kacc = ((k.get("palette") or {}).get("roles") or {}).get("accent", "#888888")
        return ((0 if k.get("base", "light") == base_skin else 10)
                + (abs(km - target_enter) / 100 if target_enter else 0)
                + hue_distance(acc_h, rgb_to_oklch(kacc)[2]) / 60, k.get("id"))
    start = min(kits, key=kscore) if kits else None
    kit = pk.resolve(start, kits) if start else {}
    kit.pop("_file", None)
    kit.pop("extended_from", None)
    slug = brandlib.slugify(name or (target.stem if target.is_file() else target.name))[:40].strip("-") or "reference"
    kid = f"distilled-{slug}"
    kit.update({"schema": 1, "id": kid, "name": f"Distilled {slug.replace('-', ' ')}", "base": base_skin,
                "palette_family": f"invented:{kid}"})
    kit["mood"] = [m for m in ([energy] if energy else []) + ["distilled"]]
    kit["signals"] = {"for": [], "against": []}
    kit["use_when"] = ["a build asked to look like a given reference (style only)"]
    kit["avoid_when"] = list((start or {}).get("avoid_when") or [])
    kit["palette"] = {"family": f"invented:{kid}", "roles": pal["roles"], "dark": pal["dark"],
                      "note": "distilled from the reference's colours; contrast and separation checked in both skins"}
    theme = kit.setdefault("theme", {})
    theme["scale"], scale_how = scale_from(est, theme.get("scale") or {})
    motion_how = "kept the starting kit's motion (still input: nothing to measure)"
    if energy:
        sig = {"calm": ("cubic-bezier(.4,0,.2,1)", 640, 110),
               "moderate": ("cubic-bezier(.25,.1,.25,1)", 420, 70),
               "lively": ("cubic-bezier(.2,0,0,1)", 260, 40)}[energy]
        mo = kit.setdefault("motion", {})
        mo.update({"ease": sig[0], "enter_ms": sig[1], "exit_ms": int(round(sig[1] * 0.7)), "stagger_ms": sig[2]})
        within = ({"id": "cut-on-beat", "use": "within-section", "ms": 0, "on_beat": True} if avg_shot < 3.0 else
                  {"id": "dissolve", "use": "within-section", "ms": 500, "blend": "crossfade"})
        old = kit.get("transitions") or []
        section = next((t for t in old if t.get("use") == "section"), {"id": "dip", "use": "section", "ms": 400})
        emph = [t for t in old if t.get("use") == "emphasis"][:1]
        kit["transitions"] = [within, section] + emph
        motion_how = (f"{energy} motion (mean frame change {mean_motion}) and {avg_shot}s average shot -> "
                      f"enter {sig[1]} ms, stagger {sig[2]} ms, within-section '{within['id']}'")
    kit["do"] = list((start or {}).get("do") or [])[:3] + [
        "match the reference's colour, type scale and rhythm; tell your own story with them"]
    kit["dont"] = list((start or {}).get("dont") or [])[:3] + [
        "never reuse the reference's words, logos, characters, music or footage — only its style"]
    kit["distilled"] = {
        "rule": "match the style, never copy the content",
        "sources": [{"name_hash": hashlib.sha256(a["name"].encode()).hexdigest()[:12], "sha256_16": a["sha256_16"],
                     "kind": a["kind"], **{k: v for k, v in a["info"].items() if k != "decoder"}} for a in analysed],
        "skipped": skipped,
        "seed": seed, "method": {"palette": f"k-means k={K} in OKLab, seeded k-means++, CIEDE2000 de-dup < {DEDUP_DE}",
                                 "accent": accent_pass["rule"] + f"; separate k={ACCENT_K} pass; must be >= "
                                           f"{ACCENT_MIN_DE} CIEDE2000 from the background family",
                                 "cuts": f"histogram + pixel difference at {GRAY_FPS} fps, threshold {cut}"},
        "palette_clusters": palette, "dedup_merges": merges,
        "background_family": [p["hex"] for p in family],
        "accent": dict(accent, kit_light=pal["roles"]["accent"], kit_dark=pal["dark"]["accent"]),
        "accent_pass": accent_pass,
        "accent_check": {
            "measured_vs_background_family_de": round(min(delta_e(accent["hex"], p["hex"]) for p in family), 2),
            "kit_light_vs_paper_de": pal["accent_vs_paper_de"]["light"],
            "kit_dark_vs_paper_de": pal["accent_vs_paper_de"]["dark"],
            "min_required": ACCENT_MIN_DE},
        "role_mapping": mapping_note, "palette_adjustments": pal["adjustments"],
        "palette_unresolved": pal["unresolved"],
        "rhythm": {"duration_s": round(dur, 2) if videos else None, "cuts": cuts_total,
                   "cut_points": [c for a in videos for c in a["cuts"]], "shots": shots,
                   "avg_shot_s": avg_shot, "cuts_per_minute": cpm, "motion_per_second": per_sec,
                   "mean_motion": mean_motion, "energy": energy},
        "type": est, "type_scale": scale_how, "motion_signature": motion_how,
        "started_from": {"kit": (start or {}).get("id"),
                         "why": "nearest shipped kit by skin, motion and accent hue — a starting point only; "
                                "palette, type scale, motion and transitions were replaced by measured values"},
    }
    errs = pk.validate_kit(kit)
    gate = brandlib.gate_palette(kit["palette"])
    kit["distilled"]["validation"] = {"kit_outcome_rules": errs, "palette_gate": gate["status"],
                                      "palette_findings": gate["findings"]}
    sheet_info = None
    if sheet:
        sheet_info = contact_sheet(Path(sheet), keys, palette, pal["roles"]["accent"])
    ok = not errs and gate["status"] == "PASS" and kit["distilled"]["accent_check"]["measured_vs_background_family_de"] >= ACCENT_MIN_DE
    res = {"gate": GATE, "status": "PASS" if ok else "REVIEW", "kit_id": kid, "base": base_skin,
           "accent": kit["distilled"]["accent"], "accent_check": kit["distilled"]["accent_check"],
           "skipped": skipped, "sheet": sheet_info, "numpy": bool(np), "ffmpeg": bool(ff),
           "validation": kit["distilled"]["validation"],
           "summary": (f"draft kit {kid} ({base_skin}); accent {accent['hex']} "
                       f"({kit['distilled']['accent_check']['measured_vs_background_family_de']} ΔE from the "
                       f"background family); " + ("passes the kit rules and palette gate" if ok else
                                                  f"{len(errs)} kit finding(s), palette gate {gate['status']}"))}
    return (EXIT_PASS if ok else EXIT_REVIEW), res, kit


def serialise(kit: dict) -> str:
    return json.dumps(kit, indent=1, ensure_ascii=False) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Coordn8 style distillation -> draft kit")
    ap.add_argument("input", help="a video, an image, or a folder of them")
    ap.add_argument("--out", help="write the draft kit JSON here (default: print it)")
    ap.add_argument("--sheet", help="write the keyframe contact sheet PNG here")
    ap.add_argument("--name", help="kit name (default: from the input name)")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--cut", type=float, default=CUT_THRESHOLD, help="cut threshold (lower = more sensitive)")
    a = ap.parse_args(argv)
    try:
        code, res, kit = distil(Path(a.input), seed=a.seed, cut=a.cut, name=a.name,
                                sheet=Path(a.sheet) if a.sheet else None)
    except Exception as e:  # report, never a bare traceback
        emit({"gate": GATE, "status": "ERROR", "summary": f"{type(e).__name__}: {e}"})
        return EXIT_ERROR
    if kit is not None:
        if a.out:
            Path(a.out).parent.mkdir(parents=True, exist_ok=True)
            Path(a.out).write_text(serialise(kit), encoding="utf-8")
            res["kit"] = a.out
        else:
            res["kit_doc"] = kit
    emit(res)
    return code


if __name__ == "__main__":
    sys.exit(main())
