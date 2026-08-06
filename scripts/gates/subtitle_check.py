#!/usr/bin/env python3
"""
Coordn8 gate — SUBTITLE CHECK.

The captions MUST carry the exact spoken words at sane, in-bounds times. Because
subtitles are generated FROM the beat map (see scripts/render/subtitles.py), this
gate proves that contract actually held on the emitted file:

  - every cue's text equals the corresponding narration sentence, VERBATIM
    (whitespace-normalized only — never paraphrased, never re-transcribed);
  - the set of cues covers the narration (no dropped/added lines);
  - timestamps are monotonic, non-negative, start < end, and — when a video
    duration is knowable — within the video's bounds.

This is the anti-drift backstop for the subtitle feature: if someone hand-edits
the .srt, or a future change lets the caption text diverge from the voiced text,
this gate flags it. It parses BOTH .srt and .vtt.

ADVISORY (same protocol as every Coordn8 gate): a finding is a REVIEW signal, not
a hard veto. Exit 2 SKIPPED when there's nothing to check here.

Usage:
    python3 scripts/gates/subtitle_check.py <subs.srt|subs.vtt> _build/beatmap.json
    python3 scripts/gates/subtitle_check.py subs.srt beatmap.json --mp4 final.mp4
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.common import (EXIT_ERROR, EXIT_PASS, EXIT_REVIEW, EXIT_SKIPPED,  # noqa: E402
                        emit, have, load_json, run)

# HH:MM:SS,mmm (srt) or HH:MM:SS.mmm (vtt), also tolerating MM:SS.mmm
_TS = r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})"
_CUE_LINE = re.compile(rf"{_TS}\s*-->\s*{_TS}")


def _ts_to_seconds(h, m, s, ms) -> float:
    h = int(h) if h else 0
    ms = int((ms + "000")[:3]) if ms else 0  # normalize to milliseconds
    return h * 3600 + int(m) * 60 + int(s) + ms / 1000.0


def _norm(text: str) -> str:
    """Whitespace-normalize for verbatim comparison (no paraphrase tolerance)."""
    return " ".join(str(text).split())


def parse_subtitles(path: Path) -> list[dict]:
    """Parse .srt or .vtt into [{start, end, text}] in file order."""
    raw = path.read_text(encoding="utf-8-sig")  # tolerate BOM
    # Normalize newlines, split into blocks on blank lines.
    blocks = re.split(r"\n\s*\n", raw.replace("\r\n", "\n").replace("\r", "\n"))
    cues = []
    for block in blocks:
        lines = [ln for ln in block.split("\n") if ln.strip() != ""]
        if not lines:
            continue
        if lines[0].strip().upper().startswith("WEBVTT"):
            lines = lines[1:]  # drop the WEBVTT header line
            if not lines:
                continue
        # Find the timing line; a leading numeric/id line may precede it.
        tline_i = next((i for i, ln in enumerate(lines)
                        if _CUE_LINE.search(ln)), None)
        if tline_i is None:
            continue
        mobj = _CUE_LINE.search(lines[tline_i])
        g = mobj.groups()
        start = _ts_to_seconds(g[0], g[1], g[2], g[3])
        end = _ts_to_seconds(g[4], g[5], g[6], g[7])
        text = _norm(" ".join(lines[tline_i + 1:]))
        cues.append({"start": start, "end": end, "text": text})
    return cues


def _expected_texts(beatmap: Path) -> list[str]:
    data = load_json(beatmap)
    sentences = data.get("sentences") or []
    # Match subtitles.py: order by start, drop empty text.
    ordered = sorted(
        (s for s in sentences if _norm(s.get("text", ""))),
        key=lambda s: float(s.get("start", 0.0)),
    )
    return [_norm(s.get("text", "")) for s in ordered]


def video_duration(mp4: Path) -> float | None:
    if not have("ffprobe"):
        return None
    cp = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
              "-of", "default=noprint_wrappers=1:nokey=1", str(mp4)], timeout=60)
    try:
        return float(cp.stdout.strip())
    except (ValueError, AttributeError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("subs")
    ap.add_argument("beatmap")
    ap.add_argument("--mp4", default=None,
                    help="optional final mp4, to bound cue times by its duration")
    args = ap.parse_args()

    subs = Path(args.subs)
    bm = Path(args.beatmap)
    if not subs.exists() or not bm.exists():
        emit({"gate": "subtitle_check", "status": "SKIPPED",
              "summary": f"missing {'subtitles' if not subs.exists() else 'beatmap'}"
                         " — nothing to check"})
        return EXIT_SKIPPED

    try:
        cues = parse_subtitles(subs)
        expected = _expected_texts(bm)
    except Exception as e:  # noqa: BLE001 — surface any parse error honestly
        emit({"gate": "subtitle_check", "status": "ERROR",
              "summary": f"could not parse inputs: {type(e).__name__}: {e}"})
        return EXIT_ERROR

    if not cues:
        emit({"gate": "subtitle_check", "status": "REVIEW",
              "summary": f"no cues parsed from {subs} — empty or malformed"})
        return EXIT_REVIEW

    findings = []

    # 1) Text fidelity: caption text == narration text, verbatim, in order.
    cue_texts = [c["text"] for c in cues]
    if len(cue_texts) != len(expected):
        findings.append(
            f"cue count {len(cue_texts)} != narration sentence count "
            f"{len(expected)} — a caption line was dropped or added.")
    for i, (got, want) in enumerate(zip(cue_texts, expected), 1):
        if got != want:
            findings.append(
                f"cue {i} text differs from narration.\n"
                f"    caption:   {got!r}\n"
                f"    narration: {want!r}")

    # 2) Timing sanity.
    dur = video_duration(Path(args.mp4)) if args.mp4 else None
    prev_start = -1.0
    for i, c in enumerate(cues, 1):
        if c["start"] < 0:
            findings.append(f"cue {i}: negative start {c['start']:.3f}s")
        if c["end"] < c["start"]:
            findings.append(
                f"cue {i}: end {c['end']:.3f}s before start {c['start']:.3f}s")
        if c["start"] < prev_start - 1e-6:
            findings.append(
                f"cue {i}: start {c['start']:.3f}s is before the previous cue "
                f"— cues not monotonic")
        prev_start = c["start"]
        if dur is not None and c["start"] > dur + 0.5:
            findings.append(
                f"cue {i}: start {c['start']:.3f}s is past video end {dur:.2f}s")

    emit({
        "gate": "subtitle_check",
        "status": "REVIEW" if findings else "PASS",
        "subtitles": str(subs),
        "cues": len(cues),
        "narration_sentences": len(expected),
        "video_duration_s": dur,
        "findings": findings,
        "summary": (
            f"{len(findings)} subtitle issue(s) — captions may not match the "
            f"spoken words or timings are off" if findings
            else f"{len(cues)} cues match narration verbatim; timings sane"),
    })
    return EXIT_REVIEW if findings else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
