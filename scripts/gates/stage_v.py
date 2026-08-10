#!/usr/bin/env python3
"""
Coordn8 — STAGE V: the BLIND VISUAL REVERSE CHECK (a mandatory pipeline stage).

WHY THIS STAGE EXISTS
---------------------
Every mechanical gate proves only "not provably broken in one specific way". Only this
stage answers the question a human actually asks: *does this frame show what is being
said, and would a stranger call it well-made?* A build passed ten gates and shipped ten
real defects; that gap is what Stage V closes.

WHAT IT DOES
------------
For every spoken line of every changed scene it builds a REVIEW PACKET containing:
  - the frame rendered at 85-90% through the line (entrances settled, beat at full
    strength — measured at the moment the viewer actually sees the composed beat),
  - the EXACT spoken sentence for that line,
  - the previous approved cut's matching frame, when one exists,
  - a fixed answer schema the reviewer must fill.

The packets are handed to a vision-capable reviewer (a subagent, or the human). This
script does not call any model or network service: it PREPARES the packets, ENFORCES
the three rules below, and VALIDATES the returned verdicts. That keeps it free of API
keys and paid services while making the stage mechanically enforceable.

THE THREE RULES — each learned from this stage FAILING
-----------------------------------------------------
  1. VERIFIERS MUST BE BLIND to the builder's claims. Verifiers briefed with a claim
     list verified the claims and missed everything not on it — a reviewer pointed at a
     checklist finds checklist items. So a packet carries the sentence and the image,
     never the claim list, and `--claims` may only be attached to the *claim-auditor*
     role, which runs LAST.
  2. ASSERT ARTIFACT FRESHNESS FIRST. Two verifier reports reviewed an artifact older
     than the fixes and reported already-fixed defects as severe, which made their
     genuine findings easy to dismiss. Every frame's mtime is compared against every
     source edit; a stale frame is refused, not reviewed.
  3. ASK "WHAT USED TO BE HERE?" Supply the previous cut's matching frame. That single
     question is the only thing that surfaces a section which silently lost its visual.

`contradicts` on ANY frame blocks the build. Everything else is a ranked defect list.

Usage:
    # 1. prepare packets (asserts freshness, pairs prev-cut frames)
    python3 scripts/gates/stage_v.py prepare --beatmap _build/beatmap.json \\
        --frames _build/frames/s1 --fps 30 --sources video/s1.js engine/PLAYBOOK.md \\
        --prev-frames _build/prev/frames/s1 --out _build/stage_v/packets.json

    # 2. (reviewer fills verdicts using the schema; then:)
    python3 scripts/gates/stage_v.py check _build/stage_v/verdicts.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.common import (EXIT_ERROR, EXIT_PASS, EXIT_REVIEW,  # noqa: E402
                        EXIT_SKIPPED, emit, load_json)

SAMPLE_AT = 0.875        # 85-90% through the line

# The fixed answer schema. A reviewer MUST answer every field; a missing field is
# treated as "not verified", never as a pass.
SCHEMA = {
    "frame": "path of the frame reviewed",
    "sentence": "the exact spoken sentence for this frame",
    "shows_what_is_said": "match | partial | contradicts",
    "text_clipped_or_overflowing": "bool + where",
    "text_crossing_a_line": "bool + where",
    "any_panel_unreadable_at_1to1": "bool + measured glyph height px (measure, do not estimate)",
    "large_dead_region": "bool + which part of the frame",
    "ragged_alignment": "bool + where",
    "direction_contradicts_sentence": "bool + which arrow/flow",
    "well_made_or_low_effort": "well-made | low-effort + specifically why",
    "resembles_real_identifier_or_private_data": "bool + what",
    "what_used_to_be_here": "vs the previous cut: same | something was lost | improved",
}

BLOCKING_FIELD = "shows_what_is_said"
BLOCKING_VALUE = "contradicts"


def assert_freshness(frames: list[Path], sources: list[Path]) -> list[str]:
    """RULE 2: refuse to review an artifact older than any source edit."""
    problems = []
    if not sources:
        problems.append("NO SOURCES SUPPLIED — freshness is UNVERIFIED; pass --sources so a "
                        "stale artifact cannot be reviewed as if it were current")
        return problems
    newest_src, newest_name = 0.0, ""
    for s in sources:
        if s.exists():
            m = s.stat().st_mtime
            if m > newest_src:
                newest_src, newest_name = m, s.name
    for f in frames:
        if not f.exists():
            problems.append(f"frame missing: {f}")
            continue
        if f.stat().st_mtime < newest_src:
            problems.append(
                f"STALE ARTIFACT: {f.name} is older than the source edit {newest_name} "
                f"— re-render before reviewing. (Reviewing a stale frame produced reports of "
                f"already-fixed defects, which discredited the genuine findings.)")
    return problems


def prepare(args) -> int:
    bm_path = Path(args.beatmap)
    if not bm_path.exists():
        emit({"gate": "stage_v", "status": "SKIPPED",
              "summary": f"missing beatmap {bm_path} — no narration lines to review against"})
        return EXIT_SKIPPED
    try:
        bm = load_json(bm_path)
    except Exception as e:  # noqa: BLE001
        emit({"gate": "stage_v", "status": "ERROR",
              "summary": f"could not parse beatmap: {type(e).__name__}: {e}"})
        return EXIT_ERROR

    sentences = bm.get("sentences") or bm.get("lines") or []
    if not sentences:
        emit({"gate": "stage_v", "status": "SKIPPED",
              "summary": "beatmap has no sentences[] — nothing to review"})
        return EXIT_SKIPPED

    fdir = Path(args.frames)
    frames = sorted(list(fdir.glob("*.png")) + list(fdir.glob("*.ppm"))) if fdir.is_dir() else []
    if not frames:
        emit({"gate": "stage_v", "status": "SKIPPED",
              "summary": f"no rendered frames in {fdir} — the video tier was not reached, or "
                         f"the render has not run. Stage V CANNOT be marked done; record it "
                         f"as an open item, never as a pass."})
        return EXIT_SKIPPED

    prev_dir = Path(args.prev_frames) if args.prev_frames else None
    prev_frames = (sorted(list(prev_dir.glob("*.png")) + list(prev_dir.glob("*.ppm")))
                   if prev_dir and prev_dir.is_dir() else [])

    packets, chosen = [], []
    for s in sentences:
        start, end = float(s.get("start", 0.0)), float(s.get("end", 0.0))
        t_at = start + (end - start) * SAMPLE_AT
        idx = min(len(frames) - 1, max(0, int(round(t_at * args.fps))))
        f = frames[idx]
        chosen.append(f)
        prev_f = None
        if prev_frames:
            pidx = min(len(prev_frames) - 1, max(0, int(round(t_at * args.fps))))
            prev_f = str(prev_frames[pidx])
        packets.append({
            "line_idx": s.get("idx"),
            "beat_name": s.get("name"),
            "t": round(t_at, 3),
            "frame": str(f),
            # The DISPLAY text is what the viewer hears/reads; `speech` (2.2c) is a
            # TTS-only form and must NOT be shown to the reviewer as the sentence.
            "sentence": s.get("text", ""),
            "previous_cut_frame": prev_f,
            "answer_schema": SCHEMA,
        })

    problems = assert_freshness(chosen, [Path(p) for p in (args.sources or [])])

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "stage": "V",
        "rules": {
            "blind": "The reviewer is NOT shown the builder's claims. A reviewer pointed at a "
                     "checklist finds only checklist items.",
            "freshness": "Every frame must be newer than every source edit.",
            "previous_cut": "Ask 'what used to be here?' against the previous cut's frame — the "
                            "only way to find a section that silently lost its visual.",
        },
        "blocking_rule": f"{BLOCKING_FIELD} == '{BLOCKING_VALUE}' on ANY frame blocks the build",
        "freshness_problems": problems,
        "packets": packets,
    }
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    emit({
        "gate": "stage_v_prepare",
        "status": "REVIEW" if problems else "PASS",
        "packets": len(packets),
        "packets_file": str(out),
        "previous_cut_paired": bool(prev_frames),
        "findings": problems,
        "summary": (
            f"{len(problems)} freshness problem(s) — do NOT review these artifacts yet"
            if problems else
            f"{len(packets)} blind review packet(s) prepared at {out}"
            + ("" if prev_frames else " (NO previous cut supplied — the 'what used to be here?' "
                                      "question cannot be asked, so a silently-lost visual "
                                      "would go undetected)")),
    })
    return EXIT_REVIEW if problems else EXIT_PASS


def check(args) -> int:
    vp = Path(args.verdicts)
    if not vp.exists():
        emit({"gate": "stage_v", "status": "SKIPPED",
              "summary": f"no verdicts at {vp} — Stage V has NOT run. It cannot be reported as "
                         f"passed; it is an OPEN ITEM until a blind reviewer returns verdicts."})
        return EXIT_SKIPPED
    try:
        data = load_json(vp)
    except Exception as e:  # noqa: BLE001
        emit({"gate": "stage_v", "status": "ERROR",
              "summary": f"could not parse verdicts: {type(e).__name__}: {e}"})
        return EXIT_ERROR

    verdicts = data.get("verdicts") if isinstance(data, dict) else data
    if not verdicts:
        emit({"gate": "stage_v", "status": "SKIPPED",
              "summary": "verdicts file has no verdicts[] — Stage V is an open item"})
        return EXIT_SKIPPED

    contradicts, incomplete, defects = [], [], []
    for i, v in enumerate(verdicts):
        where = v.get("frame") or f"verdict[{i}]"
        missing = [k for k in SCHEMA if k not in v]
        if missing:
            incomplete.append(f"{where}: unanswered field(s) {missing} — an unanswered field is "
                              f"NOT VERIFIED, never a pass")
        val = str(v.get(BLOCKING_FIELD, "")).strip().lower()
        if val == BLOCKING_VALUE:
            contradicts.append(f"{where}: frame CONTRADICTS its sentence "
                               f"{str(v.get('sentence'))[:70]!r} — this blocks the build")
        for field, label in (
            ("text_clipped_or_overflowing", "text clipped/overflowing"),
            ("text_crossing_a_line", "text crossing a connector"),
            ("any_panel_unreadable_at_1to1", "panel unreadable at 1:1"),
            ("large_dead_region", "large dead region"),
            ("ragged_alignment", "ragged alignment"),
            ("direction_contradicts_sentence", "direction contradicts the sentence"),
            ("resembles_real_identifier_or_private_data", "possible real identifier/PII"),
        ):
            raw = v.get(field)
            truthy = (raw is True or (isinstance(raw, str)
                                      and raw.strip().lower().startswith(("true", "yes"))))
            if truthy:
                defects.append(f"{where}: {label} — {raw if isinstance(raw, str) else ''}".strip())
        eff = str(v.get("well_made_or_low_effort", "")).strip().lower()
        if eff.startswith("low"):
            defects.append(f"{where}: reviewer called this LOW-EFFORT — "
                           f"{v.get('well_made_or_low_effort')}")
        lost = str(v.get("what_used_to_be_here", "")).strip().lower()
        if "lost" in lost:
            defects.append(f"{where}: something was LOST vs the previous cut — {lost}")

    blocked = bool(contradicts)
    emit({
        "gate": "stage_v",
        "status": "REVIEW" if (blocked or defects or incomplete) else "PASS",
        "reviewed_frames": len(verdicts),
        "blocking": contradicts,
        "incomplete": incomplete,
        "ranked_defects": defects,
        "summary": (
            f"BLOCKED — {len(contradicts)} frame(s) contradict their spoken sentence; "
            f"{len(defects)} other defect(s); {len(incomplete)} incomplete verdict(s)"
            if blocked else
            (f"{len(defects)} ranked defect(s), {len(incomplete)} incomplete verdict(s) — "
             f"no frame contradicts its sentence" if (defects or incomplete) else
             f"all {len(verdicts)} frames: the picture matches the words, no defects reported "
             f"by a blind reviewer")),
    })
    return EXIT_REVIEW if (blocked or defects or incomplete) else EXIT_PASS


def main() -> int:
    ap = argparse.ArgumentParser(description="Coordn8 Stage V — blind visual reverse check.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("prepare")
    p.add_argument("--beatmap", required=True)
    p.add_argument("--frames", required=True)
    p.add_argument("--fps", type=float, default=30.0)
    p.add_argument("--sources", nargs="*", default=[],
                   help="every source file whose edit must predate the frames")
    p.add_argument("--prev-frames", default=None)
    p.add_argument("--out", default="_build/stage_v/packets.json")

    c = sub.add_parser("check")
    c.add_argument("verdicts")

    args = ap.parse_args()
    if args.cmd == "prepare":
        return prepare(args)
    return check(args)


if __name__ == "__main__":
    sys.exit(main())
