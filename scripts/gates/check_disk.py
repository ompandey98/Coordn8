#!/usr/bin/env python3
"""
Coordn8 gate — DISK ACCUMULATION. The build folder may not grow without bound.

WHY THIS GATE EXISTS
--------------------
A real build accumulated frames forever, produced NO video, filled a 1 TB volume and
killed the machine. The per-scene guards in `scripts/render/diskguard.py` stop any
SINGLE capture from being reckless, but a build is many captures: eleven scenes that
each fit comfortably still add up, and they only stay small if each scene is encoded
and its frames reclaimed BEFORE the next one starts. Nothing was watching the total.

So this gate watches the whole build folder and answers three questions:

  * How much intermediate data is this build holding RIGHT NOW?
  * Is any of it stale — frames that already have a rendered mp4, so they are pure
    waste and should have been reclaimed at encode time?
  * Is the volume close enough to full that continuing is dangerous?

It also prints the exact `coordn8 clean` command to reclaim what it found, because a
finding the user cannot act on is not much better than no finding.

ADVISORY, with one exception: the LOW-FREE-SPACE condition is reported as CRITICAL.
A gate may be advisory about taste; it may not be relaxed about destroying a host.

Usage:
    python3 scripts/gates/check_disk.py path/to/Coordn8
    python3 scripts/gates/check_disk.py path/to/Coordn8 --max-gb 20 --min-free-gb 10
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.common import EXIT_PASS, EXIT_REVIEW, EXIT_SKIPPED, emit  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "render"))
from diskguard import dir_bytes, disk_free, disk_total, human  # noqa: E402

# Where render intermediates legitimately live inside a build folder.
FRAME_GLOBS = ("_build/frames/*", "_build/frames", "video/frames/*",
               "_build/_manim_*", "video/_manim_*", "_build/traces")
FRAME_EXTS = {".png", ".jpg", ".jpeg", ".ppm", ".webp", ".y4m", ".raw"}

DEFAULT_MAX_GB = 20.0      # total intermediates a build may hold
DEFAULT_MIN_FREE_GB = 10.0  # below this, continuing is dangerous


def frame_dirs(build: Path) -> list[Path]:
    """Directories under `build` that actually contain frame files."""
    seen: dict[Path, None] = {}
    for pat in FRAME_GLOBS:
        for p in build.glob(pat):
            if p.is_dir():
                seen.setdefault(p, None)
    # Also catch any directory holding frame files, wherever it sits.
    for p in build.rglob("frame_*"):
        if p.is_file() and p.suffix.lower() in FRAME_EXTS:
            seen.setdefault(p.parent, None)
    return sorted(seen)


def main() -> int:
    ap = argparse.ArgumentParser(description="Coordn8 disk-accumulation gate.")
    ap.add_argument("build", help="the Coordn8/ build folder")
    ap.add_argument("--max-gb", type=float, default=DEFAULT_MAX_GB)
    ap.add_argument("--min-free-gb", type=float, default=DEFAULT_MIN_FREE_GB)
    args = ap.parse_args()

    build = Path(args.build)
    if not build.exists():
        emit({"gate": "disk", "status": "SKIPPED",
              "summary": f"no build folder at {build} — nothing to measure"})
        return EXIT_SKIPPED

    max_bytes = int(args.max_gb * 1024 ** 3)
    min_free = int(args.min_free_gb * 1024 ** 3)

    dirs = frame_dirs(build)
    per_dir = []
    total_frames_bytes = 0
    stale = []
    for d in dirs:
        n_files = sum(1 for p in d.iterdir()
                      if p.is_file() and p.suffix.lower() in FRAME_EXTS)
        size = dir_bytes(d)
        total_frames_bytes += size
        # A frame dir whose scene ALREADY has an mp4 is pure waste: encode should
        # have reclaimed it. Look for <name>.mp4 next to the usual output spots.
        mp4s = list(build.glob(f"video/{d.name}.mp4")) + \
            list(build.glob(f"_build/{d.name}.mp4")) + \
            list(build.glob(f"{d.name}.mp4"))
        entry = {"dir": str(d), "frames": n_files, "bytes": size,
                 "bytes_h": human(size), "encoded_mp4": [str(m) for m in mp4s]}
        per_dir.append(entry)
        if mp4s and size > 0:
            stale.append(entry)

    build_total = dir_bytes(build)
    free = disk_free(build)
    total = disk_total(build)
    findings: list[str] = []
    critical = False

    if free < min_free:
        critical = True
        findings.append(
            f"CRITICAL: only {human(free)} free on the volume holding {build} "
            f"(floor {human(min_free)}). Do NOT start another render — reclaim first: "
            f"`coordn8 clean {build}` or "
            f"`python3 scripts/render/capture_scene.py clean <frame dirs>`.")

    if total_frames_bytes > max_bytes:
        findings.append(
            f"intermediate frames total {human(total_frames_bytes)} across "
            f"{len(dirs)} directory(ies), over the {human(max_bytes)} budget. Encode "
            f"each scene and reclaim its frames BEFORE capturing the next — frames are "
            f"a pure function of t, so keeping them after the mp4 exists is pure cost.")

    for e in stale:
        findings.append(
            f"STALE: {e['dir']} still holds {e['bytes_h']} of frames although "
            f"{e['encoded_mp4'][0]} already exists — these should have been reclaimed "
            f"at encode time. Reclaim: `python3 scripts/render/capture_scene.py clean "
            f"{e['dir']}`")

    reclaimable = total_frames_bytes
    emit({
        "gate": "disk",
        "status": "REVIEW" if findings else "PASS",
        "critical": critical,
        "build": str(build),
        "build_total_bytes": build_total,
        "build_total": human(build_total),
        "intermediate_frames_bytes": total_frames_bytes,
        "intermediate_frames": human(total_frames_bytes),
        "reclaimable": human(reclaimable),
        "volume_free": human(free),
        "volume_total": human(total),
        "frame_dirs": per_dir,
        "limits": {"max_intermediates": human(max_bytes),
                   "min_free": human(min_free)},
        "reclaim_command": (f"python3 scripts/render/capture_scene.py clean "
                            + " ".join(e["dir"] for e in per_dir) if per_dir else ""),
        "findings": findings,
        "summary": (
            f"{len(findings)} disk finding(s): holding {human(total_frames_bytes)} of "
            f"reclaimable frames, {human(free)} free"
            + (" — CRITICAL, free space before rendering again" if critical else "")
            if findings else
            f"build holds {human(build_total)} total, {human(total_frames_bytes)} of it "
            f"reclaimable frames; {human(free)} free. Nothing accumulating out of bounds."),
    })
    return EXIT_REVIEW if findings else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
