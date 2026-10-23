#!/usr/bin/env python3
"""
Coordn8 — CONTACT SHEETS for the automatic frame review (Stage V, requirement E6).

WHY THIS FILE EXISTS
--------------------
A reviewer can only judge a frame it can see. Handing a vision reviewer a whole video is
impossible and handing it every frame is wasteful, so the review looks at a SMALL, exact
set of frames chosen where defects actually live:

  * 85-90 % through every spoken line — entrances settled, the beat at full strength
    (the Stage V sampling point, `stage_v.SAMPLE_AT`).
  * 4 frames before every cut — by then everything the scene promised must be on screen.
    A frame sampled at 3/4 of a shot misses the classic defect where an element only
    lights up after the scene has already cut away.
  * -16 / -3 / +2 frames around every key-reveal hit — the frame should read
    "darkening -> held still -> already released".

Every time is computed from the BEAT MAP (sentence start/end in seconds) and converted
to an exact frame index; the frame is decoded from the final mp4 straight into memory
(ffmpeg -> stdout pipe -> Pillow) and composed into one labelled sheet per scene. No
frame is ever written to disk: the only images this module writes are the sheets.

When a previous cut exists, the matching frame of that cut sits beside the new one, so
the reviewer can answer "what used to be here?" — the only question that surfaces a
section which silently lost its visual.

Usage (library use is the normal path; see scripts/review/frame_review.py):
    python3 scripts/render/contact_sheet.py times --mp4 OUT.mp4 --beatmap _build/beatmap.json
    python3 scripts/render/contact_sheet.py build --mp4 OUT.mp4 --beatmap _build/beatmap.json \
        --out-dir _build/review [--storyboard _build/storyboard.json] [--prev-mp4 P.mp4 \
        --prev-beatmap P.json]

Exit codes: 0 ok, 2 skipped (no ffmpeg / no Pillow), 3 error.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

try:  # Pillow composes the sheets; without it the module degrades to SKIPPED.
    from PIL import Image, ImageDraw, ImageFont  # type: ignore
    HAVE_PIL = True
except Exception:  # noqa: BLE001
    HAVE_PIL = False

SAMPLE_AT = 0.875                 # 85-90 % through a spoken line (Stage V)
LINE_WINDOW = (0.85, 0.90)
CUT_OFFSET = -4                   # frames before each cut
HIT_OFFSETS = (-16, -3, 2)        # frames around each hit
MAX_ROWS_PER_SHEET = 6            # a taller sheet is split into pages, never shrunk
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".ppm", ".webp", ".bmp", ".tif", ".tiff"}

try:
    from align import strip_markup as _strip_markup  # type: ignore
except Exception:  # noqa: BLE001
    _strip_markup = None


def display_text(text: str) -> str:
    """The sentence as the viewer hears/reads it: script markup removed, whitespace
    collapsed. (`speech` is a TTS-only form and is never the sentence.)"""
    s = str(text or "")
    if _strip_markup is not None:
        try:
            return _strip_markup(s)
        except Exception:  # noqa: BLE001
            pass
    s = re.sub(r"[⟦⟧]", "", s)
    return re.sub(r"\s+", " ", s).strip()


# --------------------------------------------------------------------------- media
def have_ffmpeg() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def probe(mp4: Path) -> dict:
    """fps, frame count, size and duration of the video stream (measured, not assumed)."""
    cp = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,r_frame_rate,avg_frame_rate,nb_frames:format=duration",
         "-of", "json", str(mp4)], capture_output=True, text=True, timeout=60)
    if cp.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {mp4}: {cp.stderr.strip()[:200]}")
    data = json.loads(cp.stdout or "{}")
    st = (data.get("streams") or [{}])[0]

    def _rate(r: str) -> float:
        try:
            a, b = str(r).split("/")
            return float(a) / float(b) if float(b) else 0.0
        except Exception:  # noqa: BLE001
            return 0.0
    fps = _rate(st.get("r_frame_rate", "0/1")) or _rate(st.get("avg_frame_rate", "0/1"))
    dur = float((data.get("format") or {}).get("duration") or 0.0)
    try:
        n = int(st.get("nb_frames"))
    except (TypeError, ValueError):
        n = int(round(dur * fps)) if fps else 0
    return {"fps": fps, "frames": n, "width": int(st.get("width") or 0),
            "height": int(st.get("height") or 0), "duration": dur}


def grab(mp4: Path, frame: int, fps: float, width: int | None = None):
    """Decode exactly frame `frame` into memory. Nothing touches the disk.

    Accurate input seeking drops every frame whose timestamp is below the target, so
    aiming a quarter-frame BEFORE frame N returns frame N exactly (never N-1 or N+1).
    """
    ts = max(0.0, (frame - 0.25) / fps) if frame > 0 else 0.0
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{ts:.6f}", "-i", str(mp4), "-frames:v", "1"]
    if width:
        cmd += ["-vf", f"scale={int(width)}:-2:flags=area"]
    cmd += ["-f", "image2pipe", "-vcodec", "png", "-"]
    cp = subprocess.run(cmd, capture_output=True, timeout=120)
    if cp.returncode != 0 or not cp.stdout:
        raise RuntimeError(f"could not decode frame {frame} of {mp4}: "
                           f"{cp.stderr.decode('utf-8', 'replace').strip()[:200]}")
    img = Image.open(io.BytesIO(cp.stdout))
    img.load()
    return img.convert("RGB"), len(cp.stdout)


# --------------------------------------------------------------------------- planning
def _sentences(beatmap: dict) -> list[dict]:
    out = []
    for i, s in enumerate(beatmap.get("sentences") or beatmap.get("lines") or []):
        text = display_text(s.get("text", ""))
        if not text:
            continue
        idx = s.get("idx", i + 1)
        out.append({"idx": idx, "name": s.get("name") or s.get("id") or f"line{idx}",
                    "text": text, "start": float(s.get("start", 0.0)),
                    "end": float(s.get("end", s.get("start", 0.0))),
                    "scene": s.get("scene")})
    return out


def _scene_spans(sents: list[dict], beatmap: dict, storyboard: dict | None,
                 duration: float) -> tuple[list[dict], str]:
    """Scene spans [start, end) in seconds; a cut sits at every scene end.

    Priority: beat-map `scenes` with times > storyboard `sentence_ids` > a `scene`
    field on each sentence > one scene for the whole video."""
    if beatmap.get("scenes"):
        spans = []
        for k, sc in enumerate(beatmap["scenes"]):
            spans.append({"id": str(sc.get("id") or f"s{k + 1}"),
                          "start": float(sc.get("start", 0.0)),
                          "end": float(sc.get("end", duration))})
        return spans, "beatmap.scenes"

    groups: list[tuple[str, list[dict]]] = []
    source = "single"
    if storyboard and storyboard.get("scenes"):
        by_idx = {str(s["idx"]): s for s in sents}
        for k, sc in enumerate(storyboard["scenes"]):
            members = [by_idx[str(i)] for i in (sc.get("sentence_ids") or []) if str(i) in by_idx]
            if members:
                groups.append((str(sc.get("id") or f"s{k + 1}"), members))
        if groups:
            source = "storyboard.sentence_ids"
    if not groups and any(s.get("scene") for s in sents):
        order: dict[str, list[dict]] = {}
        for s in sents:
            order.setdefault(str(s.get("scene") or "s1"), []).append(s)
        groups = list(order.items())
        source = "sentence.scene"
    if not groups:
        return [{"id": "all", "start": 0.0, "end": duration}], "single"

    groups.sort(key=lambda g: min(s["start"] for s in g[1]))
    spans = []
    for k, (sid, members) in enumerate(groups):
        start = 0.0 if k == 0 else min(s["start"] for s in members)
        end = (min(s["start"] for s in groups[k + 1][1]) if k + 1 < len(groups) else duration)
        spans.append({"id": sid, "start": start, "end": end})
    return spans, source


def _hit_times(hits: list, words: dict | None, sents: list[dict], fps: float) -> tuple[list, list]:
    """Resolve hits to seconds. A word-anchored hit whose word is absent is an ERROR
    (the same rule as atWord in the scene runtime: a miss throws, never guesses)."""
    out, errors = [], []
    wl = {}
    for ln in (words or {}).get("lines", []) or []:
        wl[str(ln.get("name"))] = ln
    by_name = {str(s["name"]): s for s in sents}
    by_idx = {str(s["idx"]): s for s in sents}
    for k, h in enumerate(hits or []):
        name = str(h.get("name") or h.get("id") or f"hit{k + 1}")
        if h.get("frame") is not None:
            out.append({"name": name, "t": int(h["frame"]) / fps, "frame": int(h["frame"]),
                        "scene": h.get("scene")})
            continue
        if h.get("t") is not None:
            out.append({"name": name, "t": float(h["t"]), "scene": h.get("scene")})
            continue
        line = str(h.get("line", ""))
        word = str(h.get("word", "")).strip().lower()
        ln = wl.get(line)
        if ln is None:
            sent = by_name.get(line) or by_idx.get(line)
            if sent is not None and sent.get("name") in wl:
                ln = wl[sent["name"]]
        if ln is None or not word:
            errors.append(f"hit {name!r}: line {line!r} has no word timings — cannot place it")
            continue
        hitw = [w for w in ln.get("words", []) if str(w.get("w", "")).strip(".,;:!?").lower() == word]
        if not hitw:
            errors.append(f"hit {name!r}: word {word!r} is not in line {line!r} — refusing to guess")
            continue
        out.append({"name": name, "t": float(hitw[0]["start"]), "scene": h.get("scene")})
    return out, errors


def plan(beatmap: dict, *, fps: float, frames: int, duration: float,
         storyboard: dict | None = None, hits: list | None = None,
         words: dict | None = None) -> dict:
    """The exact frames to review, grouped by scene. Pure function — no media access."""
    if fps <= 0:
        raise ValueError("fps must be positive")
    last = max(0, frames - 1)
    sents = _sentences(beatmap)
    spans, span_src = _scene_spans(sents, beatmap, storyboard, duration)
    hit_list = list(hits if hits is not None else (beatmap.get("hits") or []))
    resolved, errors = _hit_times(hit_list, words, sents, fps)

    def clamp(f: int) -> int:
        return min(last, max(0, f))

    def scene_of(t: float) -> int:
        for k, sp in enumerate(spans):
            if sp["start"] <= t < sp["end"]:
                return k
        return len(spans) - 1 if t >= spans[-1]["start"] else 0

    scenes = [{"id": sp["id"], "start": round(sp["start"], 4), "end": round(sp["end"], 4),
               "lines": [], "cut": None, "hits": []} for sp in spans]
    for s in sents:
        d = max(0.0, s["end"] - s["start"])
        t = s["start"] + d * SAMPLE_AT
        f = int(round(t * fps))
        lo = s["start"] + d * LINE_WINDOW[0]
        hi = s["start"] + d * LINE_WINDOW[1]
        if d > 0 and not (lo <= f / fps <= hi):         # rounding left the window: snap in
            f = int(math.ceil(lo * fps - 1e-9))
        f = clamp(f)
        k = scene_of(s["start"] + d / 2.0)
        scenes[k]["lines"].append({
            "kind": "line", "line_idx": s["idx"], "line_name": s["name"], "sentence": s["text"],
            "start": s["start"], "end": s["end"], "frame": f, "t": round(f / fps, 4),
            "fraction": round((f / fps - s["start"]) / d, 4) if d else None,
            "cell": f"{scenes[k]['id']}/L{s['idx']}"})
    for k, sp in enumerate(spans):
        end_f = int(round(sp["end"] * fps))
        start_f = int(round(sp["start"] * fps))
        f = clamp(max(start_f, end_f + CUT_OFFSET))
        scenes[k]["cut"] = {"kind": "cut", "cut_at": round(sp["end"], 4), "cut_frame": end_f,
                            "offset": CUT_OFFSET, "frame": f, "t": round(f / fps, 4),
                            "cell": f"{sp['id']}/cut"}
    for h in resolved:
        hf = int(h["frame"]) if h.get("frame") is not None else int(round(h["t"] * fps))
        if h.get("scene") is not None and any(s["id"] == str(h["scene"]) for s in scenes):
            k = next(i for i, s in enumerate(scenes) if s["id"] == str(h["scene"]))
        else:
            k = scene_of(hf / fps)
        for off in HIT_OFFSETS:
            f = clamp(hf + off)
            scenes[k]["hits"].append({"kind": "hit", "hit": h["name"], "hit_frame": hf,
                                      "offset": off, "frame": f, "t": round(f / fps, 4),
                                      "cell": f"{scenes[k]['id']}/{h['name']}{off:+d}"})
    return {"fps": fps, "frames": frames, "duration": duration, "scene_source": span_src,
            "scenes": scenes, "errors": errors,
            "rules": {"line": f"{int(LINE_WINDOW[0] * 100)}-{int(LINE_WINDOW[1] * 100)}% through "
                              f"every spoken line (target {SAMPLE_AT})",
                      "cut": f"{-CUT_OFFSET} frames before every cut",
                      "hit": "/".join(f"{o:+d}" for o in HIT_OFFSETS) + " frames around every hit"}}


def pair_previous(the_plan: dict, prev_beatmap: dict | None, prev_fps: float,
                  prev_frames: int) -> None:
    """Attach the previous cut's matching frame to every line (by line name, then idx)."""
    if not prev_beatmap:
        return
    psents = _sentences(prev_beatmap)
    by_name = {str(s["name"]): s for s in psents}
    by_idx = {str(s["idx"]): s for s in psents}
    for sc in the_plan["scenes"]:
        for ln in sc["lines"]:
            p = by_name.get(str(ln["line_name"])) or by_idx.get(str(ln["line_idx"]))
            if p is None:
                ln["prev"] = None
                ln["prev_status"] = "new line — no previous frame"
                continue
            d = max(0.0, p["end"] - p["start"])
            f = min(max(0, prev_frames - 1), max(0, int(round((p["start"] + d * SAMPLE_AT) * prev_fps))))
            ln["prev"] = {"frame": f, "t": round(f / prev_fps, 4), "line_name": p["name"],
                          "matched_by": "name" if str(ln["line_name"]) in by_name else "idx",
                          "cell": ln["cell"] + "/prev"}
            ln["prev_status"] = "paired"


# --------------------------------------------------------------------------- composing
def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # older Pillow
        return ImageFont.load_default()


def _rows_for_scene(sc: dict, with_prev: bool) -> list[list[dict]]:
    rows: list[list[dict]] = []
    for ln in sc["lines"]:
        row = [{"src": "now", **ln}]
        if with_prev:
            row.append({"src": "prev", **ln})
        rows.append(row)
    events = []
    if sc.get("cut"):
        events.append({"src": "now", **sc["cut"]})
    rows_events = [events] if events else []
    hits = sc.get("hits") or []
    for i in range(0, len(hits), len(HIT_OFFSETS)):
        rows_events.append([{"src": "now", **h} for h in hits[i:i + len(HIT_OFFSETS)]])
    return rows + rows_events


def _label(c: dict) -> str:
    if c["kind"] == "line":
        if c["src"] == "prev":
            p = c.get("prev")
            return (f"L{c['line_idx']} BEFORE (previous cut) f{p['frame']}" if p
                    else f"L{c['line_idx']} BEFORE: new line, nothing to compare")
        pct = f"{c['fraction'] * 100:.1f}%" if c.get("fraction") is not None else "-"
        return f"L{c['line_idx']} NOW {pct} t={c['t']:.3f}s f{c['frame']}"
    if c["kind"] == "cut":
        return f"CUT{c['offset']:+d}f t={c['t']:.3f}s f{c['frame']}"
    return f"{c['hit']} {c['offset']:+d}f t={c['t']:.3f}s f{c['frame']}"


def compose(the_plan: dict, mp4: Path, out_dir: Path, *, cell_width: int, src_w: int,
            src_h: int, prev_mp4: Path | None = None, prev_fps: float | None = None,
            fmt: str = "jpg", workers: int = 4, budget_check=None) -> list[dict]:
    """Decode every planned frame into memory, compose one sheet per scene (paged when
    tall), write ONLY the sheets, and return their records (path, hash, cell boxes)."""
    if not HAVE_PIL:
        raise RuntimeError("Pillow is not installed — contact sheets need it")
    fps = float(the_plan["fps"])
    with_prev = prev_mp4 is not None
    cell_h = int(round(src_h * cell_width / float(src_w))) if src_w else cell_width * 9 // 16
    cell_h += cell_h % 2
    band = max(22, cell_width // 22)
    font = _font(max(12, band - 8))

    jobs: dict[tuple[str, int], None] = {}
    for sc in the_plan["scenes"]:
        for row in _rows_for_scene(sc, with_prev):
            for c in row:
                if c["src"] == "prev":
                    if c.get("prev"):
                        jobs[("prev", c["prev"]["frame"])] = None
                else:
                    jobs[("now", c["frame"])] = None

    def _do(key):
        src, f = key
        if src == "prev":
            return key, grab(prev_mp4, f, float(prev_fps or fps), cell_width)
        return key, grab(mp4, f, fps, cell_width)

    keys = list(jobs)
    images: dict = {}
    first_bytes = None
    if keys and budget_check is not None:           # measure ONE real frame, then budget
        k0, (img0, nb0) = _do(keys[0])
        images[k0] = img0
        first_bytes = nb0
        budget_check(first_bytes, len(keys))
        keys = keys[1:]
    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as ex:
        for key, (img, _nb) in ex.map(_do, keys):
            images[key] = img

    out_dir.mkdir(parents=True, exist_ok=True)
    sheets = []
    for sc in the_plan["scenes"]:
        rows = _rows_for_scene(sc, with_prev)
        if not rows:
            continue
        pages = [rows[i:i + MAX_ROWS_PER_SHEET] for i in range(0, len(rows), MAX_ROWS_PER_SHEET)]
        for p, prow in enumerate(pages):
            cols = max(len(r) for r in prow)
            W = cols * cell_width + (cols + 1) * 6
            H = len(prow) * (cell_h + band) + (len(prow) + 1) * 6 + band
            sheet = Image.new("RGB", (W, H), (18, 18, 22))
            dr = ImageDraw.Draw(sheet)
            title = (f"scene {sc['id']}  {sc['start']:.2f}-{sc['end']:.2f}s"
                     + (f"  page {p + 1}/{len(pages)}" if len(pages) > 1 else "")
                     + f"  cell scale {cell_width / float(src_w or cell_width):.3f} of 1:1")
            dr.text((8, 4), title, fill=(240, 240, 240), font=font)
            cells = []
            y = band + 6
            for row in prow:
                x = 6
                for c in row:
                    dr.rectangle([x, y, x + cell_width - 1, y + band - 1], fill=(40, 40, 48))
                    dr.text((x + 6, y + 3), _label(c), fill=(255, 214, 90), font=font)
                    iy = y + band
                    if c["src"] == "prev" and not c.get("prev"):
                        dr.rectangle([x, iy, x + cell_width - 1, iy + cell_h - 1], fill=(60, 30, 30))
                        img_box = None
                    else:
                        key = ("prev", c["prev"]["frame"]) if c["src"] == "prev" else ("now", c["frame"])
                        im = images[key]
                        if im.size != (cell_width, cell_h):
                            im = im.resize((cell_width, cell_h))
                        sheet.paste(im, (x, iy))
                        img_box = [x, iy, cell_width, cell_h]
                    cell_id = c["cell"] + ("/prev" if c["src"] == "prev" and not c["cell"].endswith("/prev") else "")
                    cells.append({"cell": cell_id, "kind": c["kind"], "src": c["src"],
                                  "label": _label(c), "box": img_box,
                                  "frame": (c["prev"]["frame"] if c["src"] == "prev" and c.get("prev")
                                            else (None if c["src"] == "prev" else c["frame"]))})
                    x += cell_width + 6
                y += cell_h + band + 6
            name = f"sheet_{_safe(sc['id'])}" + (f"_p{p + 1}" if len(pages) > 1 else "") + f".{fmt}"
            path = out_dir / name
            if fmt == "png":
                sheet.save(path, "PNG", optimize=True)
            else:
                sheet.save(path, "JPEG", quality=90)
            sheets.append({"scene": sc["id"], "page": p + 1, "path": str(path),
                           "sha256": sha256(path), "width": W, "height": H,
                           "cell_width": cell_width, "cell_height": cell_h,
                           "scale": round(cell_width / float(src_w or cell_width), 4),
                           "cells": cells})
    images.clear()
    return sheets


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(s))[:60] or "scene"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def default_cell_width(src_w: int) -> int:
    """Half of 1:1 for HD sources (so two columns fit a 1920 sheet), 1:1 for small ones."""
    if src_w <= 640:
        return src_w
    return max(480, min(960, int(round(src_w * 0.5))))


def image_files(root: Path) -> set:
    out = set()
    for p in Path(root).rglob("*"):
        if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES:
            out.add(str(p.resolve()))
    return out


# --------------------------------------------------------------------------- CLI
def _load(p: str | None):
    if not p:
        return None
    pp = Path(p)
    return json.loads(pp.read_text(encoding="utf-8")) if pp.exists() else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Coordn8 contact sheets for the frame review.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("times", "build"):
        p = sub.add_parser(name)
        p.add_argument("--mp4", required=True)
        p.add_argument("--beatmap", required=True)
        p.add_argument("--storyboard")
        p.add_argument("--hits")
        p.add_argument("--words")
        p.add_argument("--prev-mp4")
        p.add_argument("--prev-beatmap")
        if name == "build":
            p.add_argument("--out-dir", required=True)
            p.add_argument("--cell-width", type=int, default=0)
            p.add_argument("--format", choices=("jpg", "png"), default="jpg")
    a = ap.parse_args(argv)
    if not have_ffmpeg():
        print(json.dumps({"status": "SKIPPED", "summary": "ffmpeg/ffprobe not installed"}))
        return 2
    info = probe(Path(a.mp4))
    hits = _load(a.hits)
    if isinstance(hits, dict):
        hits = hits.get("hits")
    pl = plan(_load(a.beatmap) or {}, fps=info["fps"], frames=info["frames"],
              duration=info["duration"], storyboard=_load(a.storyboard), hits=hits,
              words=_load(a.words))
    prev_mp4 = Path(a.prev_mp4) if a.prev_mp4 else None
    pinfo = probe(prev_mp4) if prev_mp4 else None
    if prev_mp4:
        pair_previous(pl, _load(a.prev_beatmap), pinfo["fps"], pinfo["frames"])
    if a.cmd == "times":
        print(json.dumps(pl, indent=1, ensure_ascii=False))
        return 0
    if not HAVE_PIL:
        print(json.dumps({"status": "SKIPPED", "summary": "Pillow not installed"}))
        return 2
    cw = a.cell_width or default_cell_width(info["width"])
    sheets = compose(pl, Path(a.mp4), Path(a.out_dir), cell_width=cw, src_w=info["width"],
                     src_h=info["height"], prev_mp4=prev_mp4,
                     prev_fps=pinfo["fps"] if pinfo else None, fmt=a.format)
    print(json.dumps({"status": "PASS", "sheets": [s["path"] for s in sheets]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
