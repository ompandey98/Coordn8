#!/usr/bin/env python3
"""
Coordn8 gate — MEDIA PROBES (video).

Probes the FINAL mp4 for structural sanity: resolution, fps, audio stream
present, container decodes cleanly with zero errors, and duration within
tolerance of the expected narration length.

ADVISORY: reports issues for the agent to act on; never deletes or "fixes" the
file. SKIPPED honestly when ffmpeg/ffprobe are unavailable (Tier B).

Usage:
    python3 scripts/gates/media_probe.py final.mp4 \
        --min-width 1920 --min-height 1080 --expect-duration 240 --tolerance 0.10
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.common import (EXIT_ERROR, EXIT_PASS, EXIT_REVIEW, EXIT_SKIPPED,  # noqa: E402
                        emit, have, run)


def ffprobe_json(mp4: Path) -> dict | None:
    cp = run(["ffprobe", "-v", "error", "-show_format", "-show_streams",
              "-of", "json", str(mp4)], timeout=60)
    if cp.returncode != 0:
        return None
    try:
        return json.loads(cp.stdout)
    except json.JSONDecodeError:
        return None


def clean_decode(mp4: Path) -> tuple[bool, str]:
    cp = run(["ffmpeg", "-v", "error", "-i", str(mp4), "-f", "null", "-"],
             timeout=300)
    errs = (cp.stderr or "").strip()
    return (errs == "", errs[:500])


def parse_fps(rate: str) -> float | None:
    try:
        if "/" in rate:
            num, den = rate.split("/")
            den = float(den)
            return float(num) / den if den else None
        return float(rate)
    except (ValueError, ZeroDivisionError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mp4")
    ap.add_argument("--min-width", type=int, default=1280)
    ap.add_argument("--min-height", type=int, default=720)
    ap.add_argument("--expect-duration", type=float, default=None,
                    help="expected narration duration in seconds")
    ap.add_argument("--tolerance", type=float, default=0.10)
    ap.add_argument("--min-fps", type=float, default=24.0)
    args = ap.parse_args()

    mp4 = Path(args.mp4)
    if not mp4.exists():
        emit({"gate": "media_probe", "status": "SKIPPED",
              "summary": f"no mp4 at {mp4} — video tier not reached"})
        return EXIT_SKIPPED
    if not have("ffprobe") or not have("ffmpeg"):
        emit({"gate": "media_probe", "status": "SKIPPED",
              "summary": "ffmpeg/ffprobe unavailable — cannot probe video here"})
        return EXIT_SKIPPED

    meta = ffprobe_json(mp4)
    if not meta:
        emit({"gate": "media_probe", "status": "REVIEW",
              "summary": "ffprobe could not read the container — file may be corrupt"})
        return EXIT_REVIEW

    findings = []
    vstreams = [s for s in meta.get("streams", []) if s.get("codec_type") == "video"]
    astreams = [s for s in meta.get("streams", []) if s.get("codec_type") == "audio"]

    width = height = None
    fps = None
    if not vstreams:
        findings.append("no video stream present.")
    else:
        v = vstreams[0]
        width = v.get("width")
        height = v.get("height")
        fps = parse_fps(v.get("avg_frame_rate") or v.get("r_frame_rate") or "0")
        if width and width < args.min_width:
            findings.append(f"width {width} < required {args.min_width}.")
        if height and height < args.min_height:
            findings.append(f"height {height} < required {args.min_height}.")
        if fps is None or fps < args.min_fps:
            findings.append(f"fps {fps} < required {args.min_fps} (or unreadable).")

    if not astreams:
        findings.append("no audio stream — narration is missing.")

    dur = None
    try:
        dur = float(meta.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        findings.append("could not read container duration.")

    if args.expect_duration and dur:
        diff = abs(dur - args.expect_duration) / args.expect_duration
        if diff > args.tolerance:
            findings.append(
                f"duration {dur:.1f}s vs expected {args.expect_duration:.1f}s "
                f"differ by {diff*100:.1f}% (> {args.tolerance*100:.0f}%).")

    if dur and dur > 360:
        findings.append(f"duration {dur:.0f}s exceeds the 5–6 min "
                        f"target — consider tightening.")

    clean, err = clean_decode(mp4)
    if not clean:
        findings.append(f"decode produced errors: {err}")

    emit({
        "gate": "media_probe",
        "status": "REVIEW" if findings else "PASS",
        "mp4": str(mp4),
        "resolution": f"{width}x{height}" if width else None,
        "fps": round(fps, 3) if fps else None,
        "has_audio": bool(astreams),
        "duration_s": round(dur, 2) if dur else None,
        "clean_decode": clean,
        "findings": findings,
        "summary": (f"{len(findings)} media issue(s) to review" if findings
                    else f"media OK: {width}x{height} @ {round(fps,1) if fps else '?'}fps, "
                         f"audio present, clean decode"),
    })
    return EXIT_REVIEW if findings else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
