#!/usr/bin/env python3
"""
Coordn8 — gate runner (convenience).

Runs every applicable gate against a Coordn8/ build folder and prints
a consolidated ADVISORY table. This never blocks: it summarizes what each gate
found so the agent knows where to look. Missing inputs => SKIPPED, not failure.

The deliverables are auto-named after the project (`<slug>.html` / `<slug>.mp4`),
so this discovers them by glob rather than a fixed name.

Expected (all optional — each gate skips if its input is absent):
    <build>/<slug>.html          (the interactive page — any *.html at the root)
    <build>/_build/flows.json
    <build>/_build/claims.json
    <build>/<slug>.mp4           (the video — any *.mp4 at the root)
    <build>/_build/beatmap.json
    <build>/_research/jargon.txt

Usage:
    python3 scripts/run_gates.py path/to/Coordn8
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
GATES = HERE / "gates"

STATUS = {0: "PASS", 1: "REVIEW", 2: "SKIPPED", 3: "ERROR"}


def run_gate(script: Path, args: list[str]) -> tuple[str, str]:
    cp = subprocess.run([sys.executable, str(script), *args],
                        capture_output=True, text=True)
    status = STATUS.get(cp.returncode, f"?{cp.returncode}")
    # last stderr line carries the human summary
    summary = ""
    for line in reversed((cp.stderr or "").splitlines()):
        if line.strip():
            summary = line.strip()
            break
    return status, summary


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: run_gates.py <build_folder>")
        return 2
    build = Path(sys.argv[1])

    def first_glob(pattern: str, default: str) -> Path:
        # Deliverables are auto-named after the project; pick the root-level match.
        hits = sorted(p for p in build.glob(pattern) if p.is_file())
        return hits[0] if hits else build / default

    html = first_glob("*.html", "index.html")
    mp4 = first_glob("*.mp4", "video.mp4")
    srt = first_glob("*.srt", "captions.srt")
    flows = build / "_build" / "flows.json"
    claims = build / "_build" / "claims.json"
    beatmap = build / "_build" / "beatmap.json"
    layout = build / "_build" / "layout.json"
    storyboard = build / "_build" / "storyboard.json"
    page_plan = build / "_build" / "page_plan.json"
    narration = build / "_build" / "narration.txt"
    verification = build / "_build" / "verification.json"
    jargon = build / "_research" / "jargon.txt"

    # A representative rendered frame for the real-PIXEL layout probe. The
    # geometry probe reads a layout.json (which the agent must extract from the
    # rendered DOM, never hand-type — PLAYBOOK 2.6); the pixel probe measures an
    # ACTUAL captured frame so overflow/crowding is caught on real pixels, not a
    # self-description. Pick the first frame we can find under video/ or _build/.
    def first_frame() -> Path | None:
        for pat in ("video/**/*.png", "_build/frames/**/*.png",
                    "_build/**/frame_*.png"):
            hits = sorted(p for p in build.glob(pat) if p.is_file())
            if hits:
                return hits[0]
        return None

    frame = first_frame()
    layout_args = [str(layout)]
    if frame is not None:
        layout_args += ["--shot", str(frame)]  # OC-2/REQ-11: real pixels, not JSON

    plan = [
        ("env_detect", HERE / "env_detect.py", ["--json"]),
        ("tree_shape", GATES / "tree_shape.py", [str(flows)]),
        ("facts_trace", GATES / "facts_trace.py", [str(claims), "--repo", "."]),
        ("dom_smoke", GATES / "dom_smoke.py", [str(html)]),
        ("content_ban:html", GATES / "content_ban.py",
         [str(html)] + (["--jargon", str(jargon)] if jargon.exists() else [])),
        # Content bans also sweep the NARRATION script (0.3a/2.2a): a spoken
        # personal aside, a read-aloud URL, an audience greeting, an invented
        # acronym full-form, or a leaked version stamp never appears as page text.
        ("content_ban:narration", GATES / "content_ban.py",
         [str(narration)] + (["--jargon", str(jargon)] if jargon.exists() else [])),
        # Visual-density runs on the PLAN, before rendering — the one-shot fix for
        # sparse/static/filler deliverables (PLAYBOOK 2.1a / 1.1a). Two invocations:
        # page plan and video storyboard, each explicitly typed.
        ("visual_density:page", GATES / "visual_density.py",
         [str(page_plan), "--kind", "page"]),
        ("visual_density:video", GATES / "visual_density.py",
         [str(storyboard), "--kind", "video"]),
        ("beat_sync", GATES / "beat_sync.py", [str(mp4), str(beatmap)]),
        # Captions must carry the exact spoken words at sane times (PLAYBOOK 2.3a).
        # Derived from the beatmap, so this should pass trivially — it catches a
        # hand-edit or a regression that lets caption text drift from the voice.
        ("subtitle_check", GATES / "subtitle_check.py",
         [str(srt), str(beatmap)] + (["--mp4", str(mp4)] if mp4.exists() else [])),
        ("media_probe", GATES / "media_probe.py", [str(mp4)]),
        ("layout_probe", GATES / "layout_probe.py", layout_args),
        # LAST line of defense: the end-of-build manifest. No "Verified: …" claim
        # reaches a human unless every required item passed with evidence here
        # (PLAYBOOK final audit / verification claim).
        ("verify_manifest", GATES / "verify_manifest.py", [str(verification)]),
    ]

    print(f"\nCoordn8 gate report — {build}\n" + "=" * 60)
    rows = []
    for name, script, args in plan:
        if not script.exists():
            rows.append((name, "MISSING", "gate script not found"))
            continue
        status, summary = run_gate(script, args)
        rows.append((name, status, summary))

    width = max(len(r[0]) for r in rows)
    for name, status, summary in rows:
        print(f"  {name.ljust(width)}  {status.ljust(8)}  {summary[:80]}")
    print("=" * 60)
    print("Gates are ADVISORY. REVIEW = look at it; SKIPPED = input absent or "
          "tool missing here. Re-check load-bearing claims yourself.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
