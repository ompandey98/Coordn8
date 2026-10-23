#!/usr/bin/env python3
"""
Coordn8 gate — CAPTION MARKS (E1).

Display text may carry colour marks — `⟦good:…⟧ ⟦bad:…⟧ ⟦key:…⟧` (canonical syntax,
scripts/render/caption_marks.py). This gate judges OUTCOMES of that markup:

  * more than the kit's per-caption cap (never more than 2) marks in ONE caption — a
    caption is one on-screen cue after the same readability segmentation the .srt uses;
    when everything is emphasised, nothing is;
  * a mark the renderer cannot colour: not a mark of the active kit and not a palette
    role (an invented mark is fine when the kit declares it, or when its name IS a role);
  * a mark that maps to a role the active design system does not declare;
  * mark syntax in the SPOKEN form (`speech`) — the voice engine would read the brackets;
  * malformed syntax (unclosed, nested, empty) — it would leak into what a viewer reads;
  * the same term marked with different meanings in different captions (one colour,
    one meaning, for the whole video).

Notes (never findings): the alias form `⟦key⟧…⟦/key⟧`; a mark name that resolved
directly to a palette role.

ADVISORY like every Coordn8 gate: exit 0 PASS, 1 REVIEW (findings), 2 SKIPPED (nothing
to check), 3 ERROR (unreadable input).

Usage:
    python3 scripts/gates/check_caption_marks.py _build/beatmap.json [--kit design_system.json|kit.json]
    python3 scripts/gates/check_caption_marks.py captions.json        # {"captions": ["…", …]} or ["…", …]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE.parent / "render"))
import caption_marks as cm  # noqa: E402
from lib.common import EXIT_ERROR, EXIT_PASS, EXIT_REVIEW, EXIT_SKIPPED, emit  # noqa: E402

GATE = "check_caption_marks"


def _items(data) -> list:
    """Normalise the input into [{"id", "text", "speech", "start", "end"}]."""
    if isinstance(data, dict) and isinstance(data.get("sentences"), list):
        rows = data["sentences"]
    elif isinstance(data, dict) and isinstance(data.get("captions"), list):
        rows = data["captions"]
    elif isinstance(data, list):
        rows = data
    else:
        raise ValueError("expected a beatmap {sentences: [...]}, {captions: [...]} or a list")
    out = []
    for i, r in enumerate(rows):
        if isinstance(r, str):
            r = {"text": r}
        if not isinstance(r, dict):
            continue
        start = float(r.get("start", 0.0) or 0.0)
        end = float(r.get("end", start + 3.0) or (start + 3.0))
        out.append({"id": r.get("idx", r.get("id", i + 1)), "text": str(r.get("text", "")),
                    "speech": r.get("speech"), "start": start, "end": max(start, end)})
    return out


def check(items: list, kit: dict | None = None, skin: dict | None = None) -> dict:
    findings, notes = [], []
    cap = cm.max_marks(kit)
    roles = cm.design_roles(kit, skin)
    table = cm.mark_table(kit)
    term_meaning: dict = {}
    total_marks = 0
    for it in items:
        sid, text = it["id"], it["text"]
        p = cm.parse(text)
        total_marks += len(p["spans"])
        for e in p["errors"]:
            findings.append(f"line {sid}: {e}")
        for n in p["notes"]:
            notes.append(f"line {sid}: {n}")
        for sp in p["spans"]:
            name = sp["name"]
            r = cm.resolve_mark(name, kit, skin)
            if r is None:
                findings.append(
                    f"line {sid}: unknown mark {name!r} — not a mark of the active kit "
                    f"({', '.join(sorted(table))}) and not a palette role; declare it in the kit's "
                    f"caption_style.marks with a role and a non-colour style, or use a role name")
                continue
            if r["role"] not in roles:
                findings.append(
                    f"line {sid}: mark {name!r} maps to role {r['role']!r}, which the active design "
                    f"system does not declare (roles: {', '.join(sorted(roles))})")
            if r["source"] == "role":
                notes.append(f"line {sid}: mark {name!r} resolved directly to the palette role {name!r}")
            term = " ".join(sp["text"].lower().split()).strip(".,;:!?\"'()")
            if term:
                term_meaning.setdefault(term, []).append((sid, name))
        # per-caption cap: segment exactly as the .srt does
        if p["spans"]:
            for k, cue in enumerate(cm.cue_mark_counts(text, it["start"], it["end"]), 1):
                if cue["count"] > cap:
                    findings.append(
                        f"line {sid}, caption {k}: {cue['count']} marks ({', '.join(cue['marks'])}) in one "
                        f"caption — the cap is {cap}; keep the one or two words the sentence turns on")
        # the spoken form must be mark-free
        sp_text = it.get("speech")
        if sp_text is not None and cm.has_marks(str(sp_text)):
            findings.append(
                f"line {sid}: the spoken form (`speech`) contains caption-mark syntax — the voice engine "
                f"would read it; marks belong in the display `text` only")
        try:
            import pronounce  # noqa: E402  — the real derivation the TTS path uses
            derived = pronounce.to_speech(text)
            if cm.has_marks(derived):
                findings.append(f"line {sid}: the derived spoken form still carries mark syntax")
        except Exception as ex:  # noqa: BLE001 — never let a helper import hide the gate
            notes.append(f"pronounce helper unavailable ({type(ex).__name__}); derived spoken form not checked")
    for term, uses in sorted(term_meaning.items()):
        names = sorted({n for _s, n in uses})
        if len(names) > 1:
            where = ", ".join(f"line {s} as {n}" for s, n in uses)
            findings.append(
                f"the term {term!r} is marked with different meanings ({where}) — one colour must keep "
                f"one meaning for the whole video")
    return {"findings": findings, "notes": notes, "marks": total_marks, "cap": cap,
            "lines": len(items), "mark_names": sorted(table)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Check caption marks in display text.")
    ap.add_argument("source", help="beatmap.json, {captions:[...]} or a JSON list of strings")
    ap.add_argument("--kit", help="kit JSON or design_system.json (default: the standard roles and marks)")
    a = ap.parse_args(argv)
    src = Path(a.source)
    if not src.exists():
        emit({"gate": GATE, "status": "SKIPPED", "summary": f"no input at {src} — nothing to check"})
        return EXIT_SKIPPED
    try:
        items = _items(json.loads(src.read_text(encoding="utf-8")))
        kit = skin = None
        if a.kit:
            kit, skin = cm.load_kit(a.kit)
    except (OSError, ValueError) as ex:
        emit({"gate": GATE, "status": "ERROR", "summary": f"could not read inputs: {type(ex).__name__}: {ex}"})
        return EXIT_ERROR
    if not items:
        emit({"gate": GATE, "status": "SKIPPED", "summary": "no caption lines — nothing to check"})
        return EXIT_SKIPPED
    r = check(items, kit, skin)
    status = "REVIEW" if r["findings"] else "PASS"
    emit({"gate": GATE, "status": status, **r,
          "summary": (f"{len(r['findings'])} caption-mark issue(s) over {r['lines']} line(s)"
                      if r["findings"] else
                      f"{r['marks']} mark(s) over {r['lines']} line(s): each caption within the cap of "
                      f"{r['cap']}, every mark resolves to a declared role, spoken form mark-free")})
    return EXIT_REVIEW if r["findings"] else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
