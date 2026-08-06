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
from lib.common import (CAPTION_MAX_CHARS, CAPTION_MAX_CHARS_PER_LINE,  # noqa: E402
                        CAPTION_MAX_CPS, CAPTION_MAX_CUE_SECONDS,
                        CAPTION_MAX_LINES, EXIT_ERROR, EXIT_PASS, EXIT_REVIEW,
                        EXIT_SKIPPED, emit, have, load_json, run)

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
        text_lines = [ln.strip() for ln in lines[tline_i + 1:] if ln.strip()]
        text = _norm(" ".join(text_lines))
        cues.append({"start": start, "end": end, "text": text,
                     "lines": text_lines})
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

    # 1) Text fidelity: the caption WORD STREAM equals the narration word stream,
    #    verbatim and in order. Captions are segmented for readability (one long
    #    narration sentence legitimately becomes several cues — see
    #    render/subtitles.py), so we compare the concatenation of all cue words to
    #    the concatenation of all narration words rather than cue==sentence. This
    #    still catches any dropped, added, reordered, or paraphrased word.
    caption_words = _norm(" ".join(c["text"] for c in cues)).split()
    narration_words = _norm(" ".join(expected)).split()
    if caption_words != narration_words:
        # Locate the first divergence so the finding is actionable, not just "differ".
        n = min(len(caption_words), len(narration_words))
        first = next((k for k in range(n)
                      if caption_words[k] != narration_words[k]), n)
        ctx_c = " ".join(caption_words[max(0, first - 3):first + 4])
        ctx_n = " ".join(narration_words[max(0, first - 3):first + 4])
        findings.append(
            f"caption words differ from narration words "
            f"({len(caption_words)} caption vs {len(narration_words)} narration; "
            f"first mismatch at word {first + 1}).\n"
            f"    caption near:   …{ctx_c}…\n"
            f"    narration near: …{ctx_n}…")

    # 2) Readability: no cue may be a screen-filling paragraph. Enforce the shared
    #    caption limits (chars total, chars/line, line count, on-screen seconds,
    #    reading speed) — this is the backstop for the "massive paragraph caption"
    #    failure. These are the SAME constants the builder segments to.
    for i, c in enumerate(cues, 1):
        text = c["text"]
        if len(text) > CAPTION_MAX_CHARS:
            findings.append(
                f"cue {i}: {len(text)} chars exceeds the {CAPTION_MAX_CHARS}-char "
                f"cap — a caption must be readable at a glance, never a paragraph. "
                f"Text: {text[:80]!r}…")
        cue_lines = c.get("lines") or [text]
        if len(cue_lines) > CAPTION_MAX_LINES:
            findings.append(
                f"cue {i}: {len(cue_lines)} lines exceeds the "
                f"{CAPTION_MAX_LINES}-line cap.")
        for ln in cue_lines:
            if len(ln) > CAPTION_MAX_CHARS_PER_LINE:
                findings.append(
                    f"cue {i}: a line is {len(ln)} chars, over the "
                    f"{CAPTION_MAX_CHARS_PER_LINE}-char/line width: {ln[:80]!r}")
                break
        span = c["end"] - c["start"]
        if span > CAPTION_MAX_CUE_SECONDS + 0.05:
            findings.append(
                f"cue {i}: on screen {span:.1f}s exceeds the "
                f"{CAPTION_MAX_CUE_SECONDS:.0f}s cap — split it.")
        if span > 0.05 and (len(text) / span) > CAPTION_MAX_CPS:
            findings.append(
                f"cue {i}: {len(text) / span:.0f} chars/sec exceeds the reading-"
                f"speed ceiling of {CAPTION_MAX_CPS:.0f} cps — too fast to read.")

    # 3) Timing sanity.
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
        "caption_limits": {
            "max_chars": CAPTION_MAX_CHARS,
            "max_chars_per_line": CAPTION_MAX_CHARS_PER_LINE,
            "max_lines": CAPTION_MAX_LINES,
            "max_cue_seconds": CAPTION_MAX_CUE_SECONDS,
            "max_cps": CAPTION_MAX_CPS,
        },
        "findings": findings,
        "summary": (
            f"{len(findings)} subtitle issue(s) — captions may not match the "
            f"spoken words, run too long on screen, or exceed the readable size"
            if findings
            else f"{len(cues)} cues carry the narration verbatim; all within the "
                 f"readable caption limits; timings sane"),
    })
    return EXIT_REVIEW if findings else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
