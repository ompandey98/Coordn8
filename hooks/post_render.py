#!/usr/bin/env python3
"""Coordn8 PostToolUse hook (Bash) — measure the build right after a render or gate run.

Fires only when tool_input.command mentions capture_scene.py, orchestrate.py or
run_gates.py. Then, in parallel under one 1.8 s deadline:
  * scripts/quality.py --trace <each trace> --json   (read-only: no --out, no telemetry)
  * scripts/gates/check_disk.py <cwd>/Coordn8
and returns a compact scorecard as additionalContext. A check that would overrun is
SKIPPED and says so — never waited on. After a render (capture_scene.py / orchestrate.py)
it also appends the standing instruction to run the blind frame review.

Scores are measurements, never a quality certificate; the scorecard says so and ends
with what it did NOT verify. Never raises; prints nothing for unrelated commands.
"""
from __future__ import annotations

import json
import os
import re
import sys

try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _common as C
except BaseException:  # noqa: BLE001
    os._exit(0)

TRIGGER = re.compile(r"(capture_scene|orchestrate|run_gates)\.py\b")
RENDER = re.compile(r"(capture_scene|orchestrate)\.py\b")
CHECK_DEADLINE = 1.8
TOTAL_BUDGET = 1.9
CONTEXT_MAX = 3000

FRAME_REVIEW = (
    "Coordn8: a render just ran. Invoke the coordn8-frame-review skill now (automatic, not "
    "optional): `python3 <pkg>/scripts/review/frame_review.py prepare <build>`, then dispatch "
    "the coordn8-frame-reviewer agent with ONLY <build>/_build/review/blind_brief.md, save its "
    "JSON reply verbatim as <build>/_build/review/verdicts.json, then `frame_review.py collect "
    "<build>/_build/review/verdicts.json`. `contradicts` blocks the build.")


def find_traces(build):
    """Same search order as scripts/quality.py find_traces."""
    hits = sorted(p for p in build.glob("_build/trace*.json") if p.is_file())
    hits += sorted(p for p in build.glob("_build/traces/*.json") if p.is_file())
    hits += sorted(p for p in build.glob("trace*.json") if p.is_file())
    return list(dict.fromkeys(hits))[:200]


def quality_lines(res) -> list:
    if res is None:
        return [f"- quality: SKIPPED (did not finish within {CHECK_DEADLINE} s; run "
                f"`python3 {C.SCRIPTS / 'quality.py'} <build>` yourself)"]
    code, out, _err = res
    try:
        q = json.loads(out)
    except Exception:  # noqa: BLE001
        q = C.first_json(out) or {}
    if q.get("status") == "SKIPPED" or not q:
        return ["- quality: UNMEASURED (no instrumentation trace) - unmeasured is not a pass"]
    lines = [f"- quality: build score {q.get('build_score')}/100 over {q.get('scene_count')} "
             f"scene(s); weakest scene {q.get('weakest_scene')}, weakest dimension "
             f"{q.get('weakest_dimension')}; {len(q.get('findings') or [])} finding(s)"]
    avg = q.get("dimension_averages") or {}
    if avg:
        lines.append("  dimensions: " + ", ".join(f"{k} {v}" for k, v in avg.items()))
    for f in (q.get("findings") or [])[:5]:
        lines.append("  * " + C.clip(f, 160))
    return lines


def disk_lines(res) -> list:
    if res is None:
        return [f"- disk: SKIPPED (did not finish within {CHECK_DEADLINE} s)"]
    _code, out, _err = res
    d = C.first_json(out) or {}
    if not d:
        return ["- disk: no result"]
    lines = [f"- disk: {d.get('status')}{' CRITICAL' if d.get('critical') else ''} - "
             f"{C.clip(d.get('summary'), 220)}"]
    for f in (d.get("findings") or [])[:3]:
        lines.append("  * " + C.clip(f, 200))
    return lines


def main():
    data = C.read_stdin()
    if C.s(data.get("tool_name"), "Bash") != "Bash":
        return None
    ti = data.get("tool_input")
    cmd = C.s(ti.get("command")) if isinstance(ti, dict) else ""
    if not cmd or not TRIGGER.search(cmd):
        return None
    rendered = bool(RENDER.search(cmd))
    build = C.coordn8_dir(data)
    parts = []
    if build.is_dir():
        traces = find_traces(build)
        cmds = {"disk": [sys.executable, str(C.SCRIPTS / "gates" / "check_disk.py"), str(build)]}
        if traces:
            q = [sys.executable, str(C.SCRIPTS / "quality.py"), "--json"]
            for t in traces:
                q += ["--trace", str(t)]
            cmds["quality"] = q
        budget = min(CHECK_DEADLINE, TOTAL_BUDGET - C.elapsed())
        res = C.run_parallel(cmds, budget, cwd=str(build))
        parts.append("Coordn8 scorecard after `" + C.clip(cmd.strip(), 80) + "`:")
        parts += quality_lines(res.get("quality")) if traces else \
            ["- quality: UNMEASURED (no instrumentation trace) - unmeasured is not a pass"]
        parts += disk_lines(res.get("disk"))
        parts.append("Scores are MEASUREMENTS of mechanical dimensions, never a quality "
                     "certificate. NOT verified by this hook: whether frames show what the "
                     "narration says, explainability, register, or anything semantic.")
    if rendered:
        fr = FRAME_REVIEW.replace("<pkg>", str(C.PKG_ROOT))
        if build.is_dir():
            fr += f" (<build> here = {build})"
        parts.append(fr)
    if not parts:
        return None
    text = "\n".join(parts)
    if len(text) > CONTEXT_MAX and rendered:
        keep = CONTEXT_MAX - len(parts[-1]) - 1
        text = C.clip("\n".join(parts[:-1]), keep) + "\n" + parts[-1]
    return C.context("PostToolUse", C.clip(text, CONTEXT_MAX + 400))


if __name__ == "__main__":
    C.guarded(main)
