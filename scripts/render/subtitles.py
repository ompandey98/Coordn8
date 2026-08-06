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
from lib.common import have, load_json, run  # noqa: E402


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


def _ordered_cues(sentences: list[dict]) -> list[dict]:
    """Normalize + order sentences into renderable cues.

    - sort by start (the beatmap SHOULD already be monotonic, but never trust it)
    - clamp a cue end to the next cue's start if they overlap (players hate
      overlapping cues; the narration itself doesn't overlap)
    - drop empty-text cues (nothing to show) but keep their timing gap
    """
    cues = []
    for i, s in enumerate(sentences):
        text = _clean_text(s.get("text", ""))
        if not text:
            continue
        start = float(s.get("start", 0.0))
        end = float(s.get("end", start))
        if end < start:
            end = start
        cues.append({"idx": s.get("idx", i + 1), "text": text,
                     "start": start, "end": end})
    cues.sort(key=lambda c: c["start"])
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
