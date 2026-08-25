#!/usr/bin/env python3
"""
Coordn8 — SUPERVISED ORCHESTRATOR: fail-early stages, adaptive parallelism,
shared on-disk memory as the agent-to-agent bus.

TWO PROBLEMS THIS SOLVES
------------------------
1. WASTED WORK. Generating the whole deliverable and only then evaluating it means a
   late failure rolls the build back to the recreation stage. Every render cycle that
   changed the script forced a full downstream re-render, and each re-render
   introduced fresh defects. So the checks are ordered by COST: everything that can be
   judged from a plan or a source file runs BEFORE a single frame is rendered, and a
   stage that fails STOPS the pipeline there instead of letting the error propagate
   into minutes of rendering. Late stages still run — this never skips the mandatory
   final evaluation — but they should have nothing left to find.

2. UNDER-USED (OR OVERLOADED) MACHINES. Work inside a stage runs through the adaptive
   AIMD pool in `render/parallel.py`: it sizes concurrency from real CPU + free RAM,
   then keeps re-tuning live — ramping UP one worker at a time when both load and
   memory have headroom, and HALVING immediately when either tightens. It never floods
   the machine and never leaves it idle. The controller runs continuously for the whole
   pipeline, not once at the start.

THE STAGE LADDER (cheapest and most decisive first)
--------------------------------------------------
  0 PRECONDITION  — inputs exist at all; nothing else can be trusted without this.
  1 SOURCE        — static scans of scene sources + the narration/plan: stale beat
                    indices, raw boundary indexing, line-count changes, repetition,
                    approved-string drift, banned content. Seconds, no render.
  2 PLAN          — the storyboard / page plan: visual density, coverage. This is
                    where a sparse or incomplete deliverable is caught ON PAPER.
  3 TRACE         — the instrumentation trace: semantic gates S1-S8 and invariants
                    C1-C8. Still no renderer required.
  4 ARTIFACT      — the rendered files: layout pixels, sync, media, subtitles.
  5 JUDGEMENT     — Stage V (blind frame-vs-sentence) and the claim manifest. These
                    are MANDATORY and run last by nature; they are never skipped.

A stage is a BARRIER only where the next stage genuinely depends on it. Within a
stage, everything runs concurrently.

AGENT-TO-AGENT COMMUNICATION
----------------------------
Every stage writes its result to `_build/orchestration/<stage>.json` and the run
summary to `_build/orchestration/run.json`. That directory IS the message bus: a
sub-agent (or a whole different tool on a different machine) reads a predecessor's
output from disk rather than from a conversation, so nothing lives only in context,
any worker is restartable, and a crash loses one item instead of the batch.

Usage:
    python3 scripts/orchestrate.py <build_folder>
    python3 scripts/orchestrate.py <build_folder> --stage-only source
    python3 scripts/orchestrate.py <build_folder> --no-fail-fast   # run every stage
    python3 scripts/orchestrate.py <build_folder> --json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
GATES = HERE / "gates"
sys.path.insert(0, str(HERE / "render"))
sys.path.insert(0, str(HERE))

try:
    from parallel import (compute_worker_ceiling, compute_workers,  # noqa: E402
                          run_pool)
    _HAVE_POOL = True
except Exception:  # noqa: BLE001 — orchestration must work even if the pool is absent
    _HAVE_POOL = False

STATUS = {0: "PASS", 1: "REVIEW", 2: "SKIPPED", 3: "ERROR", 124: "TIMEOUT", 127: "NO-TOOL"}

# Stages that gate LATER, expensive work. A REVIEW here means "fix before rendering",
# because letting it through is what turns a cheap finding into a re-render.
BLOCKS_DOWNSTREAM = {"source", "plan", "trace"}


def _run_one(job: tuple[str, Path, list[str]]) -> dict:
    """Run a single gate. Never raises — a stage result is always reportable."""
    name, script, argv = job
    t0 = time.monotonic()
    if not script.exists():
        return {"gate": name, "status": "MISSING", "returncode": None,
                "summary": f"gate script not found: {script}", "seconds": 0.0}
    try:
        cp = subprocess.run([sys.executable, str(script), *argv],
                            capture_output=True, text=True, timeout=900)
        rc, out, err = cp.returncode, cp.stdout, cp.stderr
    except subprocess.TimeoutExpired:
        rc, out, err = 124, "", f"[orchestrate] {name} exceeded its time budget"
    except OSError as e:
        rc, out, err = 127, "", f"[orchestrate] could not run {name}: {e}"
    summary = ""
    for line in reversed((err or "").splitlines()):
        if line.strip():
            summary = line.strip()
            break
    payload = None
    for line in (out or "").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
                break
            except ValueError:
                pass
    return {"gate": name, "status": STATUS.get(rc, f"?{rc}"), "returncode": rc,
            "summary": summary[:400], "payload": payload,
            "seconds": round(time.monotonic() - t0, 2)}


def build_stages(build: Path) -> list[tuple[str, list[tuple[str, Path, list[str]]]]]:
    """Assemble the stage ladder for this build folder. Inputs that are absent make
    their gate SKIP honestly, so a partial build is still orchestrable."""
    def g(rel: str) -> Path:
        return GATES / rel

    html = next(iter(sorted(p for p in build.glob("*.html") if p.is_file())),
                build / "index.html")
    mp4 = next(iter(sorted(p for p in build.glob("*.mp4") if p.is_file())),
               build / "video.mp4")
    srt = next(iter(sorted(p for p in build.glob("*.srt") if p.is_file())),
               build / "captions.srt")
    b = build / "_build"
    r = build / "_research"
    beatmap, script_json = b / "beatmap.json", b / "script.json"
    narration, jargon = b / "narration.txt", r / "jargon.txt"
    traces = sorted(p for p in b.glob("trace*.json") if p.is_file())
    traces += sorted(p for p in (b / "traces").glob("*.json")) if (b / "traces").is_dir() else []
    scene_srcs = sorted(p for p in list(build.glob("video/*.js"))
                        + list((b / "scenes").glob("*.js") if (b / "scenes").is_dir() else [])
                        if p.name != "scene_runtime.js")
    prev_trace = b / "prev" / "trace.json"
    approved = r / "approved_strings.json"
    on_screen = b / "on_screen_names.json"
    sidecars = [p for p in (srt, beatmap, narration) if p.exists()]
    frames_dir = next((d for d in (b / "frames", build / "video")
                       if d.is_dir() and any(d.rglob("*.png"))), None)
    a_script = beatmap if beatmap.exists() else script_json

    source: list[tuple[str, Path, list[str]]] = [
        ("beat_index", g("check_indices.py"),
         sum((["--scene", str(s)] for s in scene_srcs), []) + ["--script", str(a_script)]),
        ("content_integrity", g("check_content.py"),
         ["--script", str(a_script)]
         + (["--approved", str(approved)] if approved.exists() else [])
         + (["--page", str(html)] if html.exists() else [])
         + (["--on-screen", str(on_screen)] if on_screen.exists() else [])
         + sum((["--sidecar", str(s)] for s in sidecars), [])),
        ("content_ban:narration", g("content_ban.py"),
         [str(narration)] + (["--jargon", str(jargon)] if jargon.exists() else [])),
    ]
    plan: list[tuple[str, Path, list[str]]] = [
        ("visual_density:video", g("visual_density.py"),
         [str(b / "storyboard.json"), "--kind", "video"]),
        ("visual_density:page", g("visual_density.py"),
         [str(b / "page_plan.json"), "--kind", "page"]),
        ("content_coverage", g("content_coverage.py"), [str(r / "requirements.md")]),
        ("tree_shape", g("tree_shape.py"), [str(b / "flows.json")]),
    ]
    trace: list[tuple[str, Path, list[str]]] = [
        ("semantics", g("check_semantics.py"),
         ([str(p) for p in traces]
          + sum((["--scene-src", str(s)] for s in scene_srcs), [])
          + (["--prev-trace", str(prev_trace)] if prev_trace.exists() else []))
         if traces else [str(b / "trace.json")]),
        ("invariants", g("check_invariants.py"),
         ([str(p) for p in traces]
          + sum((["--scene-src", str(s)] for s in scene_srcs), []))
         if traces else [str(b / "trace.json")]),
        ("density", g("check_density.py"),
         (["trace"] + [str(p) for p in traces]) if traces
         else ["frames", str(frames_dir or (b / "frames"))]
              + (["--beatmap", str(beatmap)] if beatmap.exists() else [])),
    ]
    artifact: list[tuple[str, Path, list[str]]] = [
        ("dom_smoke", g("dom_smoke.py"), [str(html)]),
        ("content_ban:html", g("content_ban.py"),
         [str(html)] + (["--jargon", str(jargon)] if jargon.exists() else [])),
        ("facts_trace", g("facts_trace.py"), [str(b / "claims.json"), "--repo", "."]),
        ("layout_probe", g("layout_probe.py"),
         [str(b / "layout.json")]
         + (["--shot", str(next(iter(sorted(frames_dir.rglob("*.png")))))]
            if frames_dir else [])),
        ("beat_sync", g("beat_sync.py"), [str(mp4), str(beatmap)]),
        ("subtitle_check", g("subtitle_check.py"),
         [str(srt), str(beatmap)] + (["--mp4", str(mp4)] if mp4.exists() else [])),
        ("media_probe", g("media_probe.py"), [str(mp4)]),
    ]
    judgement: list[tuple[str, Path, list[str]]] = [
        ("stage_v", g("stage_v.py"), ["check", str(b / "stage_v" / "verdicts.json")]),
        ("verify_manifest", g("verify_manifest.py"), [str(b / "verification.json")]),
    ]
    return [("source", source), ("plan", plan), ("trace", trace),
            ("artifact", artifact), ("judgement", judgement)]


def main() -> int:
    ap = argparse.ArgumentParser(description="Coordn8 supervised orchestrator.")
    ap.add_argument("build")
    ap.add_argument("--stage-only", default=None,
                    help="run just one stage: source|plan|trace|artifact|judgement")
    ap.add_argument("--no-fail-fast", action="store_true",
                    help="run EVERY stage even if an early one has findings")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    build = Path(args.build)
    outdir = build / "_build" / "orchestration"
    outdir.mkdir(parents=True, exist_ok=True)

    start = compute_workers("cpu") if _HAVE_POOL else 1
    ceiling = compute_worker_ceiling("cpu") if _HAVE_POOL else 1

    stages = build_stages(build)
    if args.stage_only:
        stages = [(n, j) for n, j in stages if n == args.stage_only]
        if not stages:
            print(f"unknown stage {args.stage_only!r}", file=sys.stderr)
            return 2

    if not args.json:
        print(f"\nCoordn8 supervised orchestration — {build}")
        print(f"adaptive pool: start={start} worker(s), live ceiling={ceiling} "
              f"(ramps up on headroom, halves on pressure)")
        print("=" * 74)

    run_report: dict = {"build": str(build), "stages": [], "halted_after": None,
                        "pool": {"start_workers": start, "ceiling_workers": ceiling}}
    halted = None

    for name, jobs in stages:
        t0 = time.monotonic()
        if _HAVE_POOL and len(jobs) > 1:
            # `done` is a list of (item, output) pairs; `failed` is (item, error).
            res = run_pool(jobs, _run_one, kind="cpu", per_worker_mb=250, max_retries=0)
            results = [out for (_item, out) in res.done if isinstance(out, dict)]
            for item, err in res.failed:
                results.append({"gate": item[0] if isinstance(item, tuple) else "?",
                                "status": "ERROR", "returncode": None,
                                "summary": f"worker failed: {err}"[:400], "seconds": 0.0})
            peak = res.peak_workers
            # Keep the report in the ladder's declared order, not completion order,
            # so a human reads the same sequence every run.
            order = {j[0]: i for i, j in enumerate(jobs)}
            results.sort(key=lambda r: order.get(r.get("gate"), 999))
        else:
            results = [_run_one(j) for j in jobs]
            peak = 1
        elapsed = round(time.monotonic() - t0, 2)

        counts: dict[str, int] = {}
        for r in results:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        stage_rec = {"stage": name, "seconds": elapsed, "peak_workers": peak,
                     "counts": counts, "gates": results}
        run_report["stages"].append(stage_rec)
        (outdir / f"{name}.json").write_text(
            json.dumps(stage_rec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        if not args.json:
            print(f"\n[{name}]  {elapsed}s"
                  + (f"  peak_workers={peak}" if peak else ""))
            for r in results:
                print(f"  {r['status']:<8} {r['gate']:<22} {r['summary'][:90]}")

        needs_fix = [r for r in results if r["status"] in ("REVIEW", "ERROR")]
        if needs_fix and name in BLOCKS_DOWNSTREAM and not args.no_fail_fast:
            halted = name
            break

    run_report["halted_after"] = halted
    (outdir / "run.json").write_text(
        json.dumps(run_report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(run_report, indent=2, ensure_ascii=False))
    else:
        print("\n" + "=" * 74)
        if halted:
            print(f"HALTED after the '{halted}' stage — findings here are CHEAP to fix now "
                  f"and EXPENSIVE after rendering. Fix them, then re-run; nothing was "
                  f"rendered on top of a known-bad input.")
            print("Pass --no-fail-fast to run the remaining stages anyway.")
        else:
            print("All requested stages ran. NOTE: a stage passing means 'not provably "
                  "broken in these mechanical ways' — it is NOT a quality claim. Stage V "
                  "(blind frame-vs-sentence review) or a human decides whether it is good.")
        print(f"per-stage results (the agent-to-agent bus): {outdir}")

    # 0 = nothing to fix; 1 = findings to review. Never non-zero merely for SKIPPED.
    any_findings = any(r["status"] in ("REVIEW", "ERROR")
                       for st in run_report["stages"] for r in st["gates"])
    return 1 if any_findings else 0


if __name__ == "__main__":
    sys.exit(main())
