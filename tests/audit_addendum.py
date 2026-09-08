#!/usr/bin/env python3
"""
Coordn8 — ADDENDUM COVERAGE AUDIT.

The first-go-quality addendum (docs/FIRST_GO_QUALITY_ADDENDUM.md) enumerates every
defect class, rule, gate and package change that a real production build proved
necessary. This script answers ONE question mechanically, so it can never be
answered by assertion again:

    For every item that document calls out, WHERE is it addressed, and is that
    place actually present in this package right now?

Why a script and not a paragraph: the failure this whole package exists to prevent
is a *true but worthless* claim — "everything is covered" — with no way to check it.
So coverage is a checked artifact. Every row names the concrete evidence (a file
that must exist, a symbol that must appear in it, or a doctrine line that must be
in the brain) and the audit FAILS if any required row is unsupported.

Run:  python3 tests/audit_addendum.py            # human table
      python3 tests/audit_addendum.py --json     # machine-readable
Exit: 0 = every required item supported, 1 = at least one gap.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    p = ROOT / rel
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


# Each row: (id, what the addendum demands, evidence file, symbols that must ALL appear)
# `symbols` are matched case-sensitively against the file's text.
ROWS: list[tuple[str, str, str, list[str]]] = [
    # ---- The ten defect classes (Part 1) ----
    ("D1", "non-finite alpha renders nothing, silently — must be fatal + gated",
     "scripts/render/scene_runtime.js", ["non-finite alpha", "fail("]),
    ("D1-gate", "S1 gate flags any non-finite alpha in the trace",
     "scripts/gates/check_semantics.py", ['f["S1"]', "renders nothing"]),
    ("D2", "beats bound BY NAME; an out-of-range index THROWS instead of clamping",
     "scripts/render/scene_runtime.js", ["OUT OF RANGE", "unknown beat name", "byName"]),
    ("D2-gate", "I1/I2 static scan for stale beat refs + lineCount fallback guards",
     "scripts/gates/check_indices.py", ["HELPER_RE", "RAW_RE", "GUARD_RE", "strip_comments"]),
    ("D3", "bounded text is the DEFAULT inside containers; unbounded is opt-in",
     "scripts/render/scene_runtime.js", ["s.unbounded", "fitted", "_ellipsize"]),
    ("D3-gate", "S3 flags a text box escaping its own container",
     "scripts/gates/check_semantics.py", ['f["S3"]', "escapes its own container"]),
    ("D4", "every asset and beat declares subjectId",
     "scripts/render/scene_runtime.js", ["must declare subjectId", "subjectId"]),
    ("D4-gate", "S5 fails when an asset's subject != the beat's subject",
     "scripts/gates/check_semantics.py", ['f["S5"]', "contradicting the voice"]),
    ("D5", "every connector declares from/to; arrowhead drawn at the declared end",
     "scripts/render/scene_runtime.js", ["requires from/to", "arrowAt", "edgePoints"]),
    ("D5-gate", "S6 checks drawn endpoints + arrowhead against the declaration",
     "scripts/gates/check_semantics.py", ['f["S6"]', "arrowhead points backwards"]),
    ("D6-gate", "S4 flags visible text sitting on a drawn connector",
     "scripts/gates/check_semantics.py", ['f["S4"]', "sits on connector"]),
    ("D7-gate", "DEN measures ink per band per spoken line + ranks weakest beats",
     "scripts/gates/check_density.py", ["MAX_DEAD_RUN", "weakest_beats_ranked", "BAND_PX"]),
    ("D8", "classify every requester line DIRECTION / CONTENT / CORRECTION",
     "engine/PLAYBOOK.md", ["DIRECTION / CONTENT / CORRECTION", "never narrated"]),
    ("D9", "approved_strings.json held byte-exact; a theme phrase never rewrites it",
     "scripts/gates/check_content.py",
     ["approved_strings", "APPROVED STRING MISSING", "never a licence to rewrite"]),
    ("D10a", "n-gram repetition across narration lines",
     "scripts/gates/check_content.py", ["gate_repetition", "NGRAM"]),
    ("D10b", "voice must not recite a list already on the frame",
     "scripts/gates/check_content.py", ["gate_screen_reading", "speak the CATEGORY"]),

    # ---- Pre-build rules (Part 2) ----
    ("R1", "read reference material end to end before scripting",
     "engine/PLAYBOOK.md", ["Read the reference material end to end"]),
    ("R2", "freeze a sourced fact ledger; no source -> not renderable",
     "engine/PLAYBOOK.md", ["sourced fact ledger", "not renderable"]),
    ("R3", "lock CONTENT before VISUALS — human gate on the script as text",
     "engine/PLAYBOOK.md", ["Lock CONTENT before building VISUALS", "HUMAN GATE"]),
    ("R4", "requester-line classification is logged",
     "engine/PLAYBOOK.md", ["Classify every requester line"]),
    ("R5", "approved strings frozen until the owner changes them",
     "engine/PLAYBOOK.md", ["approved_strings.json"]),
    ("R6", "one idea per frame; parts of one idea reveal in place",
     "engine/PLAYBOOK.md", ["One idea per frame"]),
    ("R7", "prefer the shorter, denser cut",
     "engine/PLAYBOOK.md", ["shorter, denser cut"]),

    # ---- Semantic gates (Part 3) ----
    ("S1..S8", "all eight semantic gates implemented over the instrumentation trace",
     "scripts/gates/check_semantics.py",
     ['f["S1"]', 'f["S2"]', 'f["S3"]', 'f["S4"]', 'f["S5"]', 'f["S6"]', 'f["S7"]', 'f["S8"]']),
    ("S7", "declared sections must render content of their own",
     "scripts/render/scene_runtime.js", ["section(name, fn)", "_sectionsSeen"]),
    ("S8", "previous-cut ink regression diff",
     "scripts/gates/check_semantics.py", ["prev", "lost", "S8_DROP"]),
    ("instr", "instrumentation mode records boxes, connectors, assets per frame",
     "scripts/render/scene_runtime.js", ["INSTRUMENT", "_connectors", "_assets", "buildTrace"]),

    # ---- Invariant gates (Part 4) ----
    ("C1", "partial overlap of visible panels; legible text collision",
     "scripts/gates/check_invariants.py", ['f["C1"]', "PANEL_OVERLAP_FRAC", "_contains"]),
    ("C2", "a declared asset that never draws",
     "scripts/gates/check_invariants.py", ['f["C2"]', "NEVER draws"]),
    ("C3", "cursor in dead space during its spoken line",
     "scripts/gates/check_invariants.py", ['f["C3"]', "dead space", "CURSOR_MARGIN"]),
    ("C5", "source provenance comment per scene",
     "scripts/gates/check_invariants.py", ['f["C5"]', "SRC:"]),
    ("C6", "sustained void >0.6s while SPEAKING; silence never flagged",
     "scripts/gates/check_invariants.py", ['f["C6"]', "MAX_VOID_S", "SUSTAINED VOID"]),
    ("C7", "panel legibility floor 0.42x, governed by WIDTH not height",
     "scripts/gates/check_invariants.py", ['f["C7"]', "MIN_SHOT_SCALE", "governed by WIDTH"]),
    ("C8", "cross-subsystem asset inside another subsystem's scene",
     "scripts/gates/check_invariants.py", ['f["C8"]', "subsystem"]),
    ("SUB", "caption fidelity + readability limits",
     "scripts/gates/subtitle_check.py", ["CAPTION_MAX_CHARS_PER_LINE", "CAPTION_MAX_CPS"]),
    ("ID-sidecar", "identifier sweep covers SIDECAR files, not just frames",
     "scripts/gates/check_content.py", ["sidecar", "ID_PATTERNS"]),

    # ---- Stage V (Part 5) ----
    ("V-blind", "reviewer is blind to the builder's claims",
     "scripts/gates/stage_v.py",
     ["VERIFIERS MUST BE BLIND", "NOT shown the builder's claims"]),
    ("V-fresh", "artifact freshness asserted before any review",
     "scripts/gates/stage_v.py", ["assert_freshness", "STALE ARTIFACT"]),
    ("V-prev", "previous cut's frame supplied — 'what used to be here?'",
     "scripts/gates/stage_v.py", ["previous_cut_frame", "what_used_to_be_here"]),
    ("V-block", "a `contradicts` verdict blocks the build",
     "scripts/gates/stage_v.py", ["BLOCKING_VALUE", "contradicts"]),
    ("V-sample", "frame sampled at 85-90% through the spoken line",
     "scripts/gates/stage_v.py", ["SAMPLE_AT"]),
    ("V-open", "no render => OPEN ITEM, never a pass",
     "scripts/gates/stage_v.py", ["open item", "never a pass"]),

    # ---- Fleet (Part 6) ----
    ("fleet", "six-phase verification fleet with blind verifiers",
     "engine/PLAYBOOK.md",
     ["THE VERIFICATION FLEET", "verifiers are BLIND", "claim-auditor", "plan-gate"]),

    # ---- Claim discipline (Part 7) ----
    ("claim-1", "report what a gate MEASURES, never what it implies",
     "engine/PLAYBOOK.md", ["never what it IMPLIES"]),
    ("claim-2", "a gate passing is not evidence of quality — stated every time",
     "scripts/gates/check_semantics.py", ["gate_pass_is_not_quality"]),
    ("claim-3", "semantic claims REFUSED without frame-level verification",
     "scripts/gates/verify_manifest.py",
     ["SEMANTIC_CLAIMS", "FRAME_VERIFIED_IDS", "SEMANTIC OVERCLAIM"]),
    ("claim-4", "evidence strength must be stated and must match",
     "scripts/gates/verify_manifest.py", ["VALID_STRENGTH", "evidence_strength"]),
    ("claim-6", "every status ends with what was NOT verified",
     "engine/PLAYBOOK.md", ["what was **NOT** verified"]),

    # ---- Package changes (Part 8) ----
    ("P8-1", "named beats that throw (highest-value change)",
     "scripts/render/scene_runtime.js", ["Prefer beat('name')"]),
    ("P8-2", "instrumentation shipped as a first-class feature",
     "scripts/render/scene_runtime.js", ["buildTrace"]),
    ("P8-3", "section() declarations",
     "scripts/render/scene_runtime.js", ["section(name, fn)"]),
    ("P8-4", "bounded text default",
     "scripts/render/scene_runtime.js", ["unbounded"]),
    ("P8-5", "subjectId required",
     "scripts/render/scene_runtime.js", ["must declare subjectId"]),
    ("P8-6", "from/to required on connectors",
     "scripts/render/scene_runtime.js", ["requires from/to"]),
    ("P8-7", "Stage V shipped as a pipeline stage",
     "scripts/run_gates.py", ["stage_v"]),
    ("P8-8", "approved_strings + diff gate",
     "scripts/gates/check_content.py", ["gate_approved"]),
    ("P8-9", "claim manifest emitted automatically",
     "scripts/gates/verify_manifest.py", ["authorized_claim"]),
    ("P8-10", "warn LOUDLY when the narration line count changes, naming the scenes",
     "scripts/gates/check_indices.py",
     ["line_count_changes", "NARRATION LINE COUNT CHANGED", "must be re-checked"]),

    # ---- First-go checklist (Part 9) ----
    ("P9", "the first-go checklist is in the brain",
     "engine/PLAYBOOK.md", ["FIRST-GO CHECKLIST", "Before rendering", "before ANY claim"]),

    # ---- Resource safety: a render may never fill the user's disk (2.5a) ----
    ("disk-stream", "stream mode writes NO frames to disk (structural fix)",
     "scripts/render/capture_scene.py", ["capture_stream", "image2pipe", "ZERO frames"]),
    ("disk-budget", "budget from a MEASURED frame; refuse up front with the numbers",
     "scripts/render/diskguard.py", ["REFUSING TO START", "measured_frame_bytes"]),
    ("disk-watchdog", "a live watchdog TRIPS AN ABORT the pool honours",
     "scripts/render/diskguard.py", ["DiskWatchdog", "self.abort.set()"]),
    ("disk-abortable", "the worker pool can actually be stopped",
     "scripts/render/parallel.py", ["abort_event", "aborted"]),
    ("disk-cleanup", "frames reclaimed after encode / failure / Ctrl-C",
     "scripts/render/capture_scene.py", ["cleanup_on_exit", "reclaim("]),
    ("disk-safe-reclaim", "cleanup refuses anything that is not a render intermediate",
     "scripts/render/diskguard.py", ["refusing to delete", "SAFE_SUFFIXES"]),
    ("disk-format", "frame format chosen by MEASURING both, not folklore",
     "scripts/render/capture_scene.py", ["format probe", "smaller for this scene"]),
    ("disk-gate", "build-level accumulation gate incl. CRITICAL low-free-space",
     "scripts/gates/check_disk.py", ["CRITICAL", "STALE", "reclaim_command"]),
    ("disk-doctrine", "the brain carries the resource-safety rules",
     "engine/PLAYBOOK.md",
     ["RESOURCE SAFETY", "may NEVER fill", "ENCODE-AND-RECLAIM PER SCENE"]),
    ("disk-cli", "`clean` and `disk` are first-class CLI commands",
     "bin/coordn8.js", ["'clean'", "'disk'"]),

    # ---- Observability & speed: a long run must be visible, fast AND gentle (2.5b) ----
    ("obs-telemetry", "event stream + summary written AS THE RUN GOES",
     "scripts/render/telemetry.py", ["class Run", "events.jsonl", "flush()"]),
    ("obs-timeseries", "a resource TIME SERIES, not a single snapshot",
     "scripts/render/telemetry.py", ["ResourceSampler", "free_disk_bytes"]),
    ("obs-progress", "progress, achieved rate and an ETA, on a throttle",
     "scripts/render/telemetry.py", ["class Progress", "eta_s", "EWMA_ALPHA"]),
    ("obs-verdict", "a live verdict naming WHICH failure mode occurred",
     "scripts/render/telemetry.py",
     ["def verdict", "throttled", "host-pressure", "slow"]),
    ("obs-wired", "the render actually reports progress and finishes telemetry",
     "scripts/render/capture_scene.py", ["Progress(", "ResourceSampler", "telem.finish"]),
    ("speed-persistent", "ONE browser, many frames (the persistent-engine speed fix)",
     "scripts/render/capture_scene.py",
     ["capture_fast", "renderAt", "persistent browser"]),
    ("speed-fallback", "cold-launch fallback is honest and names the install fix",
     "scripts/render/capture_scene.py", ["engine.fallback", "cold-launch"]),
    ("perf-gate", "a gate judging run health in BOTH directions",
     "scripts/gates/check_perf.py",
     ["Both directions are failures", "was abusing the ", "COLD-LAUNCH",
      "host-pressure"]),
    ("doctor", "a MEASURED preflight: engine, real frames/s, frame size, projection",
     "scripts/doctor.py",
     ["probe_engine_and_rate", "cold_launch_s", "frame_bytes", "measured"]),
    ("obs-doctrine", "the brain carries the observability + speed rules",
     "engine/PLAYBOOK.md",
     ["OBSERVABILITY & SPEED", "ONE BROWSER, MANY FRAMES", "LIVE QUALITY ASSESSMENT"]),
    ("obs-cli", "`doctor` and `perf` are first-class CLI commands",
     "bin/coordn8.js", ["'doctor'", "'perf'"]),

    # ---- Quality observability: measured output quality per scene (2.5c) ----
    ("q-dimensions", "six measured quality dimensions, not a terminal pass/fail",
     "scripts/quality.py",
     ["dim_alignment", "dim_overlap", "dim_voice_sync", "dim_density",
      "dim_legibility", "dim_motion"]),
    ("q-magnitude", "voice-sync reported as DRIFT IN MILLISECONDS, with lead vs lag",
     "scripts/quality.py",
     ["max_lag_ms", "median_drift_ms", "LEAD_OK_MS", "on_beat_pct"]),
    ("q-perscene", "per-scene scores + the weakest scene AND dimension named",
     "scripts/quality.py", ["weakest_scene", "weakest_dimension", "build_score"]),
    ("q-parallel", "the assessment fans out across the adaptive pool",
     "scripts/quality.py", ["run_pool", "peak_workers"]),
    ("q-trend", "per-dimension REGRESSION detection vs the previous cut",
     "scripts/quality.py", ["def add_trend", "regressions", "unrequested regression"]),
    ("q-alignment", "alignment measured: safe area, grid, shared edges",
     "scripts/quality.py",
     ["safe_area_violations", "off_grid_draws", "floating_elements"]),
    ("q-humility", "a score is a MEASUREMENT, never a quality certificate",
     "scripts/quality.py",
     ["not_a_quality_certificate", "NOT evidence the video"]),
    ("q-doctrine", "the brain carries the quality-observability rules",
     "engine/PLAYBOOK.md",
     ["QUALITY OBSERVABILITY", "A bare verdict is not an observation",
      "never a quality certificate"]),
    ("q-cli", "`quality` is a first-class CLI command and a wired gate",
     "bin/coordn8.js", ["'quality'"]),

    # ---- Host integration: use the host's REAL mechanisms, by name (R.3c) ----
    ("host-task", "fleet roles dispatched via the Task tool, ALL IN ONE MESSAGE",
     "engine/PLAYBOOK.md", ["Task tool", "ONE message", "subagent_type"]),
    ("host-todo", "TodoWrite tracks phases so a dropped stage is visible",
     "engine/PLAYBOOK.md", ["TodoWrite", "in_progress"]),
    ("host-bg", "long renders run in BACKGROUND bash and are polled",
     "engine/PLAYBOOK.md", ["run_in_background", "BashOutput"]),
    ("host-plan", "the Phase-0 plan uses the host's plan affordance",
     "engine/PLAYBOOK.md", ["ExitPlanMode"]),
    ("host-tools", "Read/Glob/Grep preferred over shelling out",
     "engine/PLAYBOOK.md", ["Read / Glob / Grep"]),
    ("host-web", "WebSearch/WebFetch allowed as an enhancement, vendored",
     "engine/PLAYBOOK.md", ["WebSearch", "WebFetch"]),
    ("host-blind", "verifier blindness is a DISPATCH property, not an intention",
     "engine/PLAYBOOK.md", ["Blindness is a dispatch property"]),
    ("host-concurrency", "the stated rule: independent work goes in the same message",
     "engine/PLAYBOOK.md", ["do not depend on each other"]),
    ("host-own-tools", "Coordn8's own commands are named as part of the toolkit",
     "engine/PLAYBOOK.md",
     ["coordn8 doctor --probe", "coordn8 quality", "capture_scene.py stream"]),
    ("host-degrade", "other hosts degrade but keep order + blindness",
     "engine/PLAYBOOK.md", ["Never claim a fleet ran when it did not"]),
    ("host-doc", "a docs page maps every engine concept to a host mechanism",
     "docs/HOST_INTEGRATION.md",
     ["Task tool", "TodoWrite", "BashOutput", "Blindness is a dispatch property"]),

    # ---- Wiring: the gates must actually be RUN, not merely exist ----
    ("wired", "every Phase-F gate is wired into the gate runner",
     "scripts/run_gates.py",
     ["check_semantics.py", "check_indices.py", "check_density.py",
      "check_content.py", "check_invariants.py", "stage_v.py", "check_disk.py", "check_perf.py", "quality.py"]),
    ("shipped", "the addendum itself ships as provenance",
     "docs/FIRST_GO_QUALITY_ADDENDUM.md", ["REQUIREMENTS ADDENDUM"]),

    # ---- Execution: fail-early ordering, adaptive parallelism, supervised fleet ----
    # The addendum's Part 6 fleet is only real if the work is actually ordered and
    # actually parallel, so those properties are checked here too.
    ("fail-early", "cheap source/plan/trace stages run BEFORE expensive ones, and a "
                   "finding in them halts the run",
     "scripts/orchestrate.py",
     ["BLOCKS_DOWNSTREAM", "build_stages", "halted_after", "no-fail-fast"]),
    ("stage-ladder", "the five-stage cost ladder is explicit",
     "scripts/orchestrate.py", ["source", "plan", "trace", "artifact", "judgement"]),
    ("adaptive", "concurrency is sized from live CPU+RAM and re-tuned continuously "
                 "(AIMD: grow by one, halve under pressure)",
     "scripts/render/parallel.py",
     ["_controller_decision", "compute_worker_ceiling", "_DynamicSemaphore",
      "peak_workers"]),
    ("bus", "workers hand off through files on disk, not a conversation",
     "scripts/orchestrate.py", ["orchestration", "run.json"]),
    ("fleet-doctrine", "the supervised fleet + host portability is in the brain",
     "engine/PLAYBOOK.md",
     ["THE SUPERVISED FLEET", "FAIL EARLY", "bus is the filesystem",
      "Host portability"]),
    ("no-crash-run", "every shelled command returns a result instead of raising, so a "
                     "hung tool degrades honestly rather than crashing",
     "scripts/lib/common.py", ["TimeoutExpired", "ALWAYS return a CompletedProcess"]),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    results = []
    for rid, demand, rel, symbols in ROWS:
        text = _read(rel)
        exists = bool(text)
        missing = [s for s in symbols if s not in text]
        ok = exists and not missing
        results.append({"id": rid, "requirement": demand, "evidence_file": rel,
                        "file_present": exists, "missing_symbols": missing,
                        "status": "covered" if ok else "GAP"})

    gaps = [r for r in results if r["status"] != "covered"]

    if args.json:
        print(json.dumps({"total": len(results), "covered": len(results) - len(gaps),
                          "gaps": len(gaps), "rows": results}, indent=2))
    else:
        print("Coordn8 — addendum coverage audit")
        print("=" * 78)
        for r in results:
            mark = "ok  " if r["status"] == "covered" else "GAP "
            print(f"  {mark} {r['id']:<11} {r['requirement'][:56]:<56} {r['evidence_file']}")
            if r["missing_symbols"]:
                print(f"        missing in that file: {r['missing_symbols']}")
        print("=" * 78)
        print(f"RESULT: {len(results) - len(gaps)}/{len(results)} addendum items supported "
              f"by present, checked evidence")
        if gaps:
            print("\nGAPS (each is a real, unaddressed item):")
            for g in gaps:
                print(f"  - {g['id']}: {g['requirement']}  ->  {g['evidence_file']} "
                      f"{'(file missing)' if not g['file_present'] else g['missing_symbols']}")
        else:
            print("No gaps. NOTE: this proves the mechanism EXISTS and is wired — it is not "
                  "a claim that any particular video is good. Only Stage V or a human says that.")
    return 1 if gaps else 0


if __name__ == "__main__":
    sys.exit(main())
