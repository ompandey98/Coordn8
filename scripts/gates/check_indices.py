#!/usr/bin/env python3
"""
Coordn8 gate — BEAT-INDEX (I1/I2): the bug class that produced SIX unrelated-looking
defects from ONE cause.

WHY THIS GATE EXISTS
--------------------
Scenes address narration beats by INDEX (`beat(3)`, `lineIn(t, 4)`, `B[i]`). Frame-lock
helpers have historically CLAMPED an out-of-range index to the last line instead of
raising. So every edit to the narration line count — the most common operation in the
entire workflow — silently mistimes some beat. One root cause, six symptoms:

  * ~8 seconds of dead screen (a stage gated on a beat that no longer existed)
  * a whole scene on wrong timings (a `lineCount >= N` guard fell through to a fallback)
  * a stat tile never on screen while its own sentence was spoken
  * two element groups printing on top of each other (fade-out clamped onto the fade-in)
  * a badge row never drawing at all (raw out-of-range index -> NaN alpha)
  * a flow animation drawn a beat late

I1 scans beat-helper calls with a literal index; I2 scans raw boundary-array indexing.
Comments are stripped FIRST — prose about `B[9]` otherwise creates false hits.

THE REAL FIX IS UPSTREAM OF THIS GATE. `scripts/render/scene_runtime.js` makes the
helpers THROW on an out-of-range index and lets scenes bind beats BY NAME
(`beat('the-worked-example')`). Then deleting a line is one loud resolve error instead
of six silent mistimings. This gate is the backstop for scenes that still use raw
indices, and it actively reports how much of each scene is already name-bound.

ADVISORY: findings are REVIEW signals. Exit 2 SKIPPED when there is nothing to scan.

Usage:
    # explicit: one scene source + the script/beatmap that defines its lines
    python3 scripts/gates/check_indices.py --scene video/s1.js --script _build/beatmap.json
    # a scene dir + a beatmap holding per-scene lines
    python3 scripts/gates/check_indices.py --scene-dir video/scenes --script _build/script.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.common import (EXIT_ERROR, EXIT_PASS, EXIT_REVIEW,  # noqa: E402
                        EXIT_SKIPPED, emit)

# Beat-addressing helpers, across the naming conventions seen in real scene code.
HELPER_RE = re.compile(
    r"\b(beat|beatAlpha|beatProgress|lineIn|lineOut|lineStart|lineEnd|lineCount"
    r"|bIn|bOut|capIn|capOut|seg|L|Le|Lend)\(\s*(?:t\s*,\s*)?(\d+)\s*[,)]")
# Raw boundary-array indexing.
RAW_RE = re.compile(r"\b(B|bounds|BOUNDS|beats|BEATS)\[\s*(\d+)\s*\]")
# Named-beat usage — the good pattern; counted so the gate can reward it.
NAMED_RE = re.compile(r"\b(?:beat|beatAlpha|beatProgress)\(\s*['\"]([^'\"]+)['\"]")
# A line-count guard that silently falls through to a hardcoded-timing branch.
GUARD_RE = re.compile(r"\blineCount\s*(?:>=|>|===|==|<|<=)\s*(\d+)")


def strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(re.sub(r"//.*$", "", ln) for ln in src.split("\n"))


def scene_lines(script: dict, scene_key: str) -> tuple[int, list[str]]:
    """Resolve how many narration lines a scene has, and their declared names.

    Accepts the several shapes a script/beatmap legitimately takes:
      {"scenes": {"s1": {"lines": [...]}}} | {"s1": {"lines": [...]}}
      {"scenes": [{"id": "s1", "lines": [...]}]} | {"sentences": [...]} (single scene)
    """
    def _from_lines(lst):
        names = [l.get("name") for l in lst if isinstance(l, dict) and l.get("name")]
        return len(lst), names

    scenes = script.get("scenes")
    if isinstance(scenes, dict) and scene_key in scenes:
        return _from_lines(scenes[scene_key].get("lines") or [])
    if isinstance(scenes, list):
        for s in scenes:
            if str(s.get("id") or s.get("name") or "") == scene_key:
                return _from_lines(s.get("lines") or s.get("sentences") or [])
    if scene_key in script and isinstance(script[scene_key], dict):
        return _from_lines(script[scene_key].get("lines") or [])
    if "sentences" in script:          # single-scene beatmap
        return _from_lines(script["sentences"] or [])
    if "lines" in script:
        return _from_lines(script["lines"] or [])
    return -1, []


def scan(src_text: str, n_lines: int, names: list[str]) -> dict:
    src = strip_comments(src_text)
    helper_bad, raw_bad, unknown_names, guards = [], [], [], []

    for m in HELPER_RE.finditer(src):
        i = int(m.group(2))
        if n_lines >= 0 and i >= n_lines:
            helper_bad.append(f"{m.group(1)}({i}) — valid 0..{n_lines - 1}")
    # Boundary array = n_lines starts + 1 trailing end => valid 0..n_lines.
    for m in RAW_RE.finditer(src):
        i = int(m.group(2))
        if n_lines >= 0 and i > n_lines:
            raw_bad.append(f"{m.group(1)}[{i}] — valid 0..{n_lines}")
    for m in NAMED_RE.finditer(src):
        nm = m.group(1)
        if names and nm not in names:
            unknown_names.append(f"beat('{nm}') — not a declared beat name "
                                 f"(declared: {', '.join(names) or 'none'})")
    for m in GUARD_RE.finditer(src):
        guards.append(f"lineCount guard against a hardcoded {m.group(1)} — a script edit "
                      f"silently changes which branch runs (this shipped a whole scene "
                      f"on wrong timings)")

    named_count = len(NAMED_RE.findall(src))
    indexed_count = len(HELPER_RE.findall(src))
    return {
        "helper_out_of_range": helper_bad,
        "raw_index_out_of_range": raw_bad,
        "unknown_beat_names": unknown_names,
        "line_count_guards": guards,
        "named_refs": named_count,
        "indexed_refs": indexed_count,
    }


def line_count_changes(script: dict, files: list[Path], counts_path: Path,
                       full_sweep: bool = False) -> tuple[list[str], dict]:
    """PACKAGE CHANGE #10: WARN LOUDLY WHEN THE NARRATION LINE COUNT CHANGES.

    Editing the narration is the single most dangerous edit in the system: every
    scene that addresses a beat is now suspect, and historically the failure was
    silent. So we persist the per-scene line count and, on the next run, name
    EXACTLY which scenes must be re-checked — before anything is re-rendered.

    Returns (warnings, current_counts). The counts file is the memory; the first
    run simply records a baseline and says so.
    """
    current: dict[str, int] = {}
    for p in files:
        n, _ = scene_lines(script, p.stem)
        if n >= 0:
            current[p.stem] = n

    previous: dict[str, int] = {}
    if counts_path.exists():
        try:
            previous = {k: int(v) for k, v in
                        json.loads(counts_path.read_text(encoding="utf-8")).items()}
        except Exception:  # noqa: BLE001 — a corrupt baseline must not break the gate
            previous = {}

    warnings: list[str] = []
    if not previous:
        return warnings, current

    for scene, n in current.items():
        was = previous.get(scene)
        if was is None:
            # A brand-new scene is EXPECTED, not a defect. Reporting it as a finding
            # is exactly the noise that makes a gate get ignored — and an ignored gate
            # is how the real defects shipped. Only a CHANGE is a finding.
            continue
        if was != n:
            warnings.append(
                f"⚠ NARRATION LINE COUNT CHANGED in '{scene}': {was} -> {n}. This is the "
                f"most dangerous edit in the system — EVERY beat reference in this scene "
                f"must be re-checked, and any frame already rendered from it is stale. "
                f"Bind beats by NAME so this edit cannot mistime them silently.")
    # A scene "missing" from a TARGETED scan is not missing from the project — only a
    # full sweep can tell the difference. Claiming otherwise on a one-scene run is a
    # false positive, and false positives are what get a gate ignored.
    if full_sweep:
        for scene in previous:
            if scene not in current:
                warnings.append(
                    f"scene '{scene}' had a baseline ({previous[scene]} lines) and is now "
                    f"absent from a full sweep — confirm it was deliberately removed and "
                    f"that no other scene still references its beats")
    return warnings, {**previous, **current}


def main() -> int:
    ap = argparse.ArgumentParser(description="Coordn8 beat-index gate (I1/I2).")
    ap.add_argument("--scene", action="append", default=[], help="a scene source file")
    ap.add_argument("--scene-dir", default=None, help="directory of scene sources (*.js)")
    ap.add_argument("--script", required=True,
                    help="script/beatmap JSON defining each scene's narration lines")
    ap.add_argument("--counts", default=None,
                    help="path to persist per-scene line counts, so a CHANGE in the "
                         "narration line count is reported loudly on the next run "
                         "(package change #10). Default: <script dir>/.line_counts.json")
    ap.add_argument("--no-update-counts", action="store_true",
                    help="report line-count changes but do not update the baseline")
    args = ap.parse_args()

    script_path = Path(args.script)
    if not script_path.exists():
        emit({"gate": "beat_index", "status": "SKIPPED",
              "summary": f"missing script/beatmap {script_path} — nothing to check indices against"})
        return EXIT_SKIPPED
    try:
        script = json.loads(script_path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        emit({"gate": "beat_index", "status": "ERROR",
              "summary": f"could not parse {script_path}: {type(e).__name__}: {e}"})
        return EXIT_ERROR

    files = [Path(p) for p in args.scene]
    if args.scene_dir:
        d = Path(args.scene_dir)
        if d.is_dir():
            files += sorted(p for p in d.glob("*.js") if p.name != "scene_runtime.js")
    files = [p for p in files if p.exists()]
    if not files:
        emit({"gate": "beat_index", "status": "SKIPPED",
              "summary": "no scene sources found to scan"})
        return EXIT_SKIPPED

    # PACKAGE CHANGE #10 — the loud warning on a narration line-count change.
    counts_path = (Path(args.counts) if args.counts
                   else script_path.parent / ".line_counts.json")
    count_warnings, current_counts = line_count_changes(
        script, files, counts_path, full_sweep=bool(args.scene_dir))

    per_scene, total_bad, unresolved = {}, 0, []
    total_named, total_indexed = 0, 0
    for p in files:
        key = p.stem
        n_lines, names = scene_lines(script, key)
        if n_lines < 0:
            unresolved.append(key)
            continue
        res = scan(p.read_text(encoding="utf-8"), n_lines, names)
        bad = (len(res["helper_out_of_range"]) + len(res["raw_index_out_of_range"])
               + len(res["unknown_beat_names"]) + len(res["line_count_guards"]))
        total_bad += bad
        total_named += res["named_refs"]
        total_indexed += res["indexed_refs"]
        entry = {"lines": n_lines, "declared_names": names, **res}
        if bad:
            per_scene[key] = entry
        else:
            per_scene[key] = {"lines": n_lines, "named_refs": res["named_refs"],
                              "indexed_refs": res["indexed_refs"], "status": "clean"}

    findings = []
    for key, e in per_scene.items():
        for k in ("helper_out_of_range", "raw_index_out_of_range",
                  "unknown_beat_names", "line_count_guards"):
            for item in e.get(k, []):
                findings.append(f"{key}: {item}")
    for key in unresolved:
        findings.append(f"{key}: could not resolve this scene's narration lines from "
                        f"{script_path.name} — cannot verify its beat indices (treat as UNCHECKED, "
                        f"not as passing)")

    findings = count_warnings + findings

    # Persist the baseline so the NEXT run can detect the next change. Written after
    # the comparison, and skippable so a dry run can't silently move the goalposts.
    # NOTE: current_counts is MERGED over the previous baseline, so scanning a single
    # scene can never erase the recorded history of the others.
    if not args.no_update_counts and current_counts:
        try:
            counts_path.parent.mkdir(parents=True, exist_ok=True)
            counts_path.write_text(json.dumps(current_counts, indent=2) + "\n",
                                   encoding="utf-8")
        except OSError:
            pass  # a read-only tree must not break the gate

    total_refs = total_named + total_indexed
    name_pct = (100.0 * total_named / total_refs) if total_refs else 0.0

    emit({
        "gate": "beat_index",
        "status": "REVIEW" if findings else "PASS",
        "scenes_scanned": len(per_scene),
        "unresolved_scenes": unresolved,
        "named_refs": total_named,
        "indexed_refs": total_indexed,
        "name_bound_pct": round(name_pct, 1),
        "per_scene": per_scene,
        "line_count_changes": count_warnings,
        "line_counts": current_counts,
        "findings": findings,
        "recommendation": (
            "Bind beats BY NAME (beat('the-worked-example')) via scripts/render/scene_runtime.js, "
            "which THROWS on an out-of-range index instead of clamping. That converts this entire "
            "defect class from six silent mistimings into one loud resolve error."),
        "summary": (
            f"{len(findings)} stale/unsafe beat reference(s) across {len(per_scene)} scene(s)"
            + (f"; {len(unresolved)} scene(s) UNCHECKED" if unresolved else "")
            + f". {name_pct:.0f}% of beat refs are name-bound."
            if findings else
            f"no stale beat references in {len(per_scene)} scene(s); "
            f"{name_pct:.0f}% of beat refs are name-bound"
            + (f"; {len(unresolved)} scene(s) UNCHECKED" if unresolved else "")),
    })
    return EXIT_REVIEW if findings else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
