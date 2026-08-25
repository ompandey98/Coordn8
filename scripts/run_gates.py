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
    requirements = build / "_research" / "requirements.md"

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

    # ---- Phase-F inputs (PLAYBOOK F.3–F.7) --------------------------------
    # Instrumentation traces from scene_runtime.js — one per scene. These are what
    # make the semantic gates possible at all, and they need NO browser, so the
    # semantic layer runs even on a machine that cannot render.
    traces = sorted(p for p in build.glob("_build/trace*.json") if p.is_file())
    traces += sorted(p for p in build.glob("_build/traces/*.json") if p.is_file())
    prev_trace = build / "_build" / "prev" / "trace.json"      # previous approved cut (S8)
    scene_srcs = sorted(
        p for p in list(build.glob("video/*.js")) + list(build.glob("_build/scenes/*.js"))
        if p.is_file() and p.name != "scene_runtime.js")
    script_json = build / "_build" / "script.json"
    approved = build / "_research" / "approved_strings.json"    # R5 / D9
    on_screen = build / "_build" / "on_screen_names.json"       # D10b
    stage_v_verdicts = build / "_build" / "stage_v" / "verdicts.json"
    # Sidecars MUST be swept for identifiers too: retracted figures once survived in
    # a stale caption file long after being removed from the video (F.4).
    sidecars = [p for p in (srt, build / "_build" / "captions.vtt", beatmap, narration)
                if p.exists()]
    sidecars += sorted(p for p in build.glob("*.vtt") if p.is_file())

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
        # Content coverage: the COMPLETENESS backstop (PLAYBOOK 0.4b / 0.7a). Reads
        # the requirement-coverage ledger and flags any in-scope must/should ask
        # that is not covered — content's counterpart to visual_density. This is
        # what stops a pretty deliverable that silently dropped substance.
        ("content_coverage", GATES / "content_coverage.py", [str(requirements)]),
        ("beat_sync", GATES / "beat_sync.py", [str(mp4), str(beatmap)]),
        # Captions must carry the exact spoken words at sane times (PLAYBOOK 2.3a).
        # Derived from the beatmap, so this should pass trivially — it catches a
        # hand-edit or a regression that lets caption text drift from the voice.
        ("subtitle_check", GATES / "subtitle_check.py",
         [str(srt), str(beatmap)] + (["--mp4", str(mp4)] if mp4.exists() else [])),
        ("media_probe", GATES / "media_probe.py", [str(mp4)]),
        ("layout_probe", GATES / "layout_probe.py", layout_args),
        # ---- PHASE F: the SEMANTIC layer (PLAYBOOK F.3-F.7) ----------------
        # Everything above measures GEOMETRY and PRESENCE. Everything a human
        # complains about is MEANING. These are the gates that see meaning.
        # S1-S8 read the instrumentation traces emitted by scene_runtime.js.
        ("semantics", GATES / "check_semantics.py",
         ([str(p) for p in traces]
          + sum((["--scene-src", str(s)] for s in scene_srcs), [])
          + (["--prev-trace", str(prev_trace)] if prev_trace.exists() else []))
         if traces else [str(build / "_build" / "trace.json")]),
        # Beat-index safety (D2): the one cause behind six historical defects.
        ("beat_index", GATES / "check_indices.py",
         (sum((["--scene", str(s)] for s in scene_srcs), [])
          + ["--script", str(beatmap if beatmap.exists() else script_json)])),
        # Density (D7): "looks low-effort" as a number. Trace mode needs no
        # renderer, so it runs at authoring time too — before minutes of render.
        ("density", GATES / "check_density.py",
         (["trace"] + [str(p) for p in traces]) if traces
         else ["frames", str((frame.parent if frame is not None else build / "_build" / "frames"))]
              + (["--beatmap", str(beatmap)] if beatmap.exists() else [])),
        # Content integrity (D9/D10): approved strings byte-exact, zero repeated
        # 5-grams, no screen-reading, identifier sweep over frames AND SIDECARS.
        ("content_integrity", GATES / "check_content.py",
         ([("--script"), str(beatmap if beatmap.exists() else script_json)]
          + (["--approved", str(approved)] if approved.exists() else [])
          + (["--page", str(html)] if html.exists() else [])
          + (["--on-screen", str(on_screen)] if on_screen.exists() else [])
          + sum((["--sidecar", str(s)] for s in sidecars), []))),
        # Invariant gates C1-C8 (F.4): overlap, declared-asset-never-drawn,
        # cursor-in-dead-space, source provenance, sustained void under speech,
        # panel legibility scale, cross-subsystem assets. Necessary, never
        # sufficient — they never authorize a quality claim.
        ("invariants", GATES / "check_invariants.py",
         ([str(p) for p in traces]
          + sum((["--scene-src", str(sx)] for sx in scene_srcs), []))
         if traces else [str(build / "_build" / "trace.json")]),
        # Stage V (F.5): the blind frame-vs-sentence review. SKIPPED (an OPEN
        # ITEM, never a pass) until a blind reviewer returns verdicts.
        ("stage_v", GATES / "stage_v.py", ["check", str(stage_v_verdicts)]),
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
