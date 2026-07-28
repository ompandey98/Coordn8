#!/usr/bin/env python3
"""
Coordn8 gate — BEAT SYNC (video).

Verifies voice/visual synchronization on the FINAL mp4. For each sentence in the
beat map it extracts the frame at (start + offset) so the agent can confirm the
on-screen content matches the spoken words, and it checks structural sync rules:
  - no visual scheduled to enter AHEAD of its sentence,
  - beat timestamps monotonic and within the video duration,
  - total narration span within tolerance of the video duration.

ADVISORY: whether a frame's visuals actually match the sentence is a judgment
only the agent (looking at the frames) can make — this gate lays out the
sentence-vs-frame table and flags the structural violations it CAN detect.

Data contract — beatmap.json (default shape; deviate if needed):
{
  "fps": 30,
  "sentences": [
    {"idx": 1, "text": "...", "start": 0.0, "end": 3.2,
     "visual_enter": 0.2}   # when the sentence's visual appears; must be >= start
  ]
}

Usage:
    python3 scripts/gates/beat_sync.py final.mp4 beatmap.json \
        --outdir _build/beat_frames --offset 1.0
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.common import (EXIT_ERROR, EXIT_PASS, EXIT_REVIEW, EXIT_SKIPPED,  # noqa: E402
                        emit, have, load_json, run)


def video_duration(mp4: Path) -> float | None:
    if not have("ffprobe"):
        return None
    cp = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
              "-of", "default=noprint_wrappers=1:nokey=1", str(mp4)], timeout=60)
    try:
        return float(cp.stdout.strip())
    except (ValueError, AttributeError):
        return None


def extract_frame(mp4: Path, t: float, out: Path) -> bool:
    if not have("ffmpeg"):
        return False
    cp = run(["ffmpeg", "-y", "-ss", f"{t:.3f}", "-i", str(mp4),
              "-frames:v", "1", "-q:v", "2", str(out)], timeout=60)
    return cp.returncode == 0 and out.exists()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mp4")
    ap.add_argument("beatmap")
    ap.add_argument("--outdir", default="_build/beat_frames")
    ap.add_argument("--offset", type=float, default=1.0,
                    help="seconds after sentence start to sample the frame")
    ap.add_argument("--tolerance", type=float, default=0.10,
                    help="allowed |narration - video| duration fraction")
    args = ap.parse_args()

    mp4 = Path(args.mp4)
    bm = Path(args.beatmap)
    if not mp4.exists() or not bm.exists():
        emit({"gate": "beat_sync", "status": "SKIPPED",
              "summary": f"missing {'mp4' if not mp4.exists() else 'beatmap'} "
                         f"— video tier not reached or beatmap not built yet"})
        return EXIT_SKIPPED

    if not have("ffmpeg") or not have("ffprobe"):
        emit({"gate": "beat_sync", "status": "SKIPPED",
              "summary": "ffmpeg/ffprobe not available — cannot verify video "
                         "sync here (Tier B). Run on a machine with ffmpeg."})
        return EXIT_SKIPPED

    try:
        data = load_json(bm)
    except Exception as e:
        emit({"gate": "beat_sync", "status": "ERROR",
              "summary": f"bad beatmap: {e}"})
        return EXIT_ERROR

    sentences = data.get("sentences", [])
    if not sentences:
        emit({"gate": "beat_sync", "status": "SKIPPED",
              "summary": "beatmap has no sentences[]"})
        return EXIT_SKIPPED

    dur = video_duration(mp4)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    findings = []
    table = []
    prev_start = -1.0
    for s in sentences:
        idx = s.get("idx")
        start = float(s.get("start", 0))
        end = float(s.get("end", start))
        venter = s.get("visual_enter")

        # Rule: monotonic starts.
        if start < prev_start:
            findings.append(f"sentence {idx}: start {start:.2f}s is before the "
                            f"previous sentence — beatmap not monotonic.")
        prev_start = start

        # Rule: within video duration.
        if dur is not None and start > dur + 0.5:
            findings.append(f"sentence {idx}: start {start:.2f}s is past the "
                            f"video end ({dur:.2f}s).")

        # Rule: no visual ahead of its sentence.
        if venter is not None and float(venter) < start - 0.05:
            findings.append(f"sentence {idx}: visual enters at {float(venter):.2f}s, "
                            f"BEFORE the sentence starts at {start:.2f}s — visual "
                            f"is ahead of narration.")

        # Extract the frame for the agent to eyeball.
        t = start + args.offset
        if dur is not None:
            t = min(t, max(0.0, dur - 0.1))
        frame = outdir / f"beat_{idx:03d}.png"
        ok = extract_frame(mp4, t, frame)
        table.append({
            "idx": idx,
            "text": s.get("text", "")[:100],
            "sample_t": round(t, 2),
            "frame": str(frame) if ok else None,
            "frame_extracted": ok,
            "on_screen": "<<agent: look at the frame and fill this in>>",
            "match": "<<agent: yes/no>>",
        })
        if not ok:
            findings.append(f"sentence {idx}: could not extract frame at {t:.2f}s.")

    # Duration tolerance vs narration span.
    narration_end = max((float(s.get("end", 0)) for s in sentences), default=0.0)
    if dur is not None and narration_end > 0:
        diff = abs(dur - narration_end) / narration_end
        if diff > args.tolerance:
            findings.append(
                f"video duration {dur:.2f}s vs narration span {narration_end:.2f}s "
                f"differ by {diff*100:.1f}% (> {args.tolerance*100:.0f}% tolerance).")

    emit({
        "gate": "beat_sync",
        "status": "REVIEW" if findings else "PASS",
        "mp4": str(mp4),
        "video_duration_s": dur,
        "narration_span_s": round(narration_end, 2),
        "sentences": len(sentences),
        "frames_dir": str(outdir),
        "sentence_vs_visible_table": table,
        "findings": findings,
        "summary": (
            f"{len(findings)} structural sync issue(s); frames in {outdir} for "
            f"visual confirmation" if findings
            else f"structural sync OK; {len(table)} frames in {outdir} — now "
                 f"LOOK at each and confirm on-screen matches the words"),
        "advisory_note": "Structural checks passing is NOT full sync verification. "
                         "The agent must inspect every extracted frame and fill "
                         "in on_screen/match — a passing table with unread frames "
                         "does not count as verified.",
    })
    return EXIT_REVIEW if findings else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
