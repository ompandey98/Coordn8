#!/usr/bin/env python3
"""
Coordn8 — SUBTITLES from the beat map (accurate by construction).

The narration text and its exact per-sentence timings ALREADY exist in
`_build/beatmap.json` (built from the TTS speech marks — see PLAYBOOK 2.3). A
subtitle file is just a re-serialization of that same data, so the captions
carry the EXACT spoken words at the EXACT times — never a re-transcription, never
a guess. This is why subtitles are cheap and correct here.

Three subcommands:

  srt    — write a standards-compliant .srt from the beat map.
  vtt    — write a WebVTT .vtt (for the page's <video> / web players).
  mux    — mux the .srt into the final mp4 as a SOFT, toggle-able subtitle track
           (ffmpeg `mov_text`), so a viewer turns captions on/off in their player
           (VLC, QuickTime, browsers). The picture is untouched — captions are a
           separate selectable stream, exactly like the user asked for.

Everything is derived from the beat map's `sentences[]` (idx/text/start/end); no
new source of truth, so `subtitle_check.py` can prove the caption text equals the
narration and the timings are sane.

Data contract — beatmap.json (same as beat_sync.py):
  {"fps": 30,
   "sentences": [{"idx": 1, "text": "…", "start": 0.0, "end": 3.2}, …]}

Usage:
  python3 scripts/render/subtitles.py srt _build/beatmap.json --out Coordn8/<slug>.srt
  python3 scripts/render/subtitles.py vtt _build/beatmap.json --out _build/<slug>.vtt
  python3 scripts/render/subtitles.py mux Coordn8/<slug>.mp4 Coordn8/<slug>.srt \
      --out Coordn8/<slug>.subbed.mp4 --lang eng
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.common import (CAPTION_MAX_CHARS, CAPTION_MAX_CHARS_PER_LINE,  # noqa: E402
                        CAPTION_MAX_CUE_SECONDS, CAPTION_MAX_LINES, have,
                        load_json, run)


def _fmt_ts(seconds: float, sep: str) -> str:
    """Seconds -> 'HH:MM:SS,mmm' (SRT, sep=',') or 'HH:MM:SS.mmm' (VTT, sep='.')."""
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    ms = total_ms % 1000
    total_s = total_ms // 1000
    s = total_s % 60
    m = (total_s // 60) % 60
    h = total_s // 3600
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _clean_text(text: str) -> str:
    """Subtitle cue text: collapse whitespace, keep the words verbatim.

    We do NOT paraphrase or shorten — accuracy is the whole point. We only strip
    surrounding whitespace and collapse internal runs so a multi-line beatmap
    entry renders as clean cue lines.
    """
    return " ".join(str(text).split())


def _pack_lines(words: list[str], max_line: int) -> list[str]:
    """Greedily pack VERBATIM words into display lines of ≤ `max_line` chars.

    Never drops, reorders, or edits a word. A word longer than the limit takes its
    own line (we never split inside a word — that would corrupt the text)."""
    lines: list[str] = []
    cur = ""
    for w in words:
        cand = f"{cur} {w}".strip()
        if cur and len(cand) > max_line:
            lines.append(cur)
            cur = w
        else:
            cur = cand
    if cur:
        lines.append(cur)
    return lines


def _segment_sentence(idx, text: str, start: float, end: float) -> list[dict]:
    """Turn ONE narration sentence into 1+ readable cues.

    A cue must be readable at a glance (≤ CAPTION_MAX_LINES lines, each ≤
    CAPTION_MAX_CHARS_PER_LINE wide, ≤ CAPTION_MAX_CUE_SECONDS on screen). We first
    pack the verbatim words into display lines at the per-line width, then group
    those lines into cues of at most CAPTION_MAX_LINES lines. If a cue's time span
    would still exceed the on-screen ceiling, we split it into more cues. Each
    cue's timing is a proportional slice of [start, end] by character length, so
    on-screen time tracks how much a cue has to say. Concatenating every cue's
    words back together reproduces the sentence exactly — nothing is lost.
    """
    import math as _m
    words = text.split()
    span = max(0.0, end - start)
    lines = _pack_lines(words, CAPTION_MAX_CHARS_PER_LINE)

    # Group lines into cues of at most CAPTION_MAX_LINES lines each (the size cap).
    groups = [lines[i:i + CAPTION_MAX_LINES]
              for i in range(0, len(lines), CAPTION_MAX_LINES)] or [[]]

    # Duration cap: if these size-bounded cues would still each linger too long
    # (slow narration), split finer down to ONE line per cue. Proportional timing
    # (below) can concentrate time on the wordier cue, so we size the target with
    # ~15% headroom — the builder must never trip its own subtitle gate.
    effective_cap = CAPTION_MAX_CUE_SECONDS * 0.85
    if span > effective_cap and len(lines) > len(groups):
        need = _m.ceil(span / effective_cap)
        if need > len(groups):
            groups = [[ln] for ln in lines]  # finest useful granularity: 1 line/cue

    cue_texts = ["\n".join(g) for g in groups]
    total_chars = sum(len(t.replace("\n", " ")) for t in cue_texts) or 1

    # Timing: give each cue a slice of [start, end] proportional to how much it has
    # to say, but CAP each cue's on-screen duration at the display cap (below the
    # 7 s gate, with headroom). A short line must never linger for a long pause —
    # if the narration's span for a cue exceeds the cap (e.g. the final sentence's
    # end runs to the video's outro, or a deliberate silent beat), the caption
    # shows for the cap and then CLEARS, leaving a blank gap until the next cue.
    # That's standard broadcast behavior and keeps the builder from ever tripping
    # its own subtitle gate. We do NOT stretch the last cue to `end` for the same
    # reason. Word order/content is untouched — only display timing is bounded.
    display_cap = CAPTION_MAX_CUE_SECONDS * 0.9
    cues = []
    t = start
    for ctext in cue_texts:
        frac = len(ctext.replace("\n", " ")) / total_chars
        dur = min(span * frac, display_cap)
        c_end = min(end, t + dur)
        if c_end < t:
            c_end = t
        cues.append({"idx": idx, "text": ctext, "start": t, "end": c_end})
        t = c_end
    return cues


def _ordered_cues(sentences: list[dict]) -> list[dict]:
    """Normalize + order sentences into renderable cues.

    - sort by start (the beatmap SHOULD already be monotonic, but never trust it)
    - SEGMENT any sentence too long/slow to read at a glance into multiple cues
      (words kept verbatim; timing split proportionally) — this is what prevents a
      single caption from becoming a screen-filling paragraph
    - clamp a cue end to the next cue's start if they overlap (players hate
      overlapping cues; the narration itself doesn't overlap)
    - drop empty-text cues (nothing to show) but keep their timing gap
    """
    ordered = []
    for i, s in enumerate(sentences):
        text = _clean_text(s.get("text", ""))
        if not text:
            continue
        start = float(s.get("start", 0.0))
        end = float(s.get("end", start))
        if end < start:
            end = start
        ordered.append((s.get("idx", i + 1), text, start, end))
    ordered.sort(key=lambda r: r[2])

    cues = []
    for idx, text, start, end in ordered:
        cues.extend(_segment_sentence(idx, text, start, end))
    # de-overlap: a cue never runs past the next cue's start
    for a, b in zip(cues, cues[1:]):
        if a["end"] > b["start"]:
            a["end"] = b["start"]
    # renumber sequentially (SRT indices must be 1..N in order)
    for n, c in enumerate(cues, 1):
        c["seq"] = n
    return cues


def _load_cues(beatmap: Path) -> list[dict]:
    data = load_json(beatmap)
    sentences = data.get("sentences") or []
    if not sentences:
        raise ValueError("beatmap has no sentences[] — nothing to caption")
    return _ordered_cues(sentences)


def write_srt(beatmap: Path, out: Path) -> int:
    cues = _load_cues(beatmap)
    lines = []
    for c in cues:
        lines.append(str(c["seq"]))
        lines.append(f'{_fmt_ts(c["start"], ",")} --> {_fmt_ts(c["end"], ",")}')
        lines.append(c["text"])
        lines.append("")  # blank line between cues
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    sys.stderr.write(f"[coordn8:subtitles] wrote SRT {out} ({len(cues)} cues)\n")
    return 0


def write_vtt(beatmap: Path, out: Path) -> int:
    cues = _load_cues(beatmap)
    lines = ["WEBVTT", ""]
    for c in cues:
        lines.append(str(c["seq"]))
        lines.append(f'{_fmt_ts(c["start"], ".")} --> {_fmt_ts(c["end"], ".")}')
        lines.append(c["text"])
        lines.append("")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    sys.stderr.write(f"[coordn8:subtitles] wrote VTT {out} ({len(cues)} cues)\n")
    return 0


def mux_soft_subs(mp4: Path, srt: Path, out: Path, lang: str = "eng") -> int:
    """Mux the SRT into the mp4 as a SOFT, selectable subtitle track.

    `mov_text` is the mp4-native subtitle codec; the resulting file plays with
    captions OFF by default and the viewer toggles them on in any standard player
    (VLC, QuickTime, browser). Video/audio streams are stream-copied (no re-encode,
    no quality loss); only the subtitle track is added.
    """
    if not have("ffmpeg"):
        sys.stderr.write("[coordn8:subtitles] ffmpeg not available — cannot mux "
                         "soft subs here. Ship the sidecar .srt instead, or run "
                         "on a machine with ffmpeg.\n")
        return 2
    if not mp4.exists() or not srt.exists():
        sys.stderr.write(f"[coordn8:subtitles] missing "
                         f"{'mp4' if not mp4.exists() else 'srt'}\n")
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)

    # ffmpeg cannot read and write the SAME file in one pass — it would error or
    # truncate. In-place muxing (--out == input mp4, the common "put captions in
    # the final <slug>.mp4" case) is therefore done via a temp sibling, then an
    # atomic os.replace. We detect same-file even across path spellings.
    in_place = out.resolve() == mp4.resolve()
    target = (out.with_name(out.name + ".coordn8-mux.tmp.mp4")
              if in_place else out)

    cmd = ["ffmpeg", "-y", "-i", str(mp4), "-i", str(srt),
           "-map", "0", "-map", "1",
           "-c", "copy", "-c:s", "mov_text",
           "-metadata:s:s:0", f"language={lang}",
           # default=0: captions start OFF; the viewer enables them.
           "-disposition:s:0", "0",
           str(target)]
    cp = run(cmd, timeout=600)
    if cp.returncode != 0 or not target.exists():
        sys.stderr.write(f"[coordn8:subtitles] mux failed: {cp.stderr[:300]}\n")
        if in_place and target.exists():
            target.unlink()  # never leave a half-written temp behind
        return 1
    if in_place:
        os.replace(target, out)  # atomic; the original mp4 now carries the track
    sys.stderr.write(f"[coordn8:subtitles] wrote {out} with a toggle-able "
                     f"'{lang}' subtitle track (off by default)\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("srt")
    s.add_argument("beatmap")
    s.add_argument("--out", required=True)

    v = sub.add_parser("vtt")
    v.add_argument("beatmap")
    v.add_argument("--out", required=True)

    m = sub.add_parser("mux")
    m.add_argument("mp4")
    m.add_argument("srt")
    m.add_argument("--out", required=True)
    m.add_argument("--lang", default="eng")

    args = ap.parse_args()
    if args.cmd == "srt":
        return write_srt(Path(args.beatmap), Path(args.out))
    if args.cmd == "vtt":
        return write_vtt(Path(args.beatmap), Path(args.out))
    if args.cmd == "mux":
        return mux_soft_subs(Path(args.mp4), Path(args.srt), Path(args.out),
                             args.lang)
    return 1


if __name__ == "__main__":
    sys.exit(main())
