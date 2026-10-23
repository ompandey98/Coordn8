#!/usr/bin/env python3
"""
Coordn8 — the BLIND BRIEF handed to the frame reviewer.

The reviewer gets exactly two kinds of input: the contact-sheet images and the exact
spoken sentences. Nothing else. A reviewer pointed at the builder's checklist finds
checklist items and misses everything off it (PLAYBOOK F.5 rule 1), so this module

  * writes the brief from the manifest's sheets + sentences ONLY (no storyboard text,
    no claim list, no verification manifest, no gate output), and
  * SCANS the finished brief for any builder-claim string it can find on disk
    (`_build/claims.json`, `_build/verification.json`, the storyboard's `explains` /
    `primary_visual` / notes, ...) and refuses to hand over a brief that leaks one.

A claim string that is also part of a spoken sentence is not a leak — the sentence is
legitimately in the brief because the viewer hears it.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from gates.stage_v import SCHEMA  # noqa: E402
except Exception:  # noqa: BLE001 — keep the brief writable even if the gate moved
    SCHEMA = {}

REVIEWER_AGENT = "coordn8-frame-reviewer"
LIMITS = ("> **Limits:** never send text, audio or code to any non-Anthropic hosted AI service. "
          "Local open-source voice models installed by Coordn8 are allowed. Read-only on anything "
          "outside the build folder unless your role says otherwise. Never name any external "
          "project in output that ships.")
MIN_CLAIM_CHARS = 12
# Files whose text is a BUILDER CLAIM about the deliverable (relative to the build dir).
CLAIM_SOURCES = ("_build/claims.json", "_build/verification.json", "_build/storyboard.json",
                 "_build/page_plan.json", "_build/claims.md", "_build/handover.md",
                 "_build/orchestration/judgement.json", "_build/review/claims.json")
# Storyboard keys that are structure, not claims (ids, engines, regions...).
_NON_CLAIM_KEYS = {"id", "engine", "kind", "region", "ref", "beat", "status", "subjectId",
                   "from", "to", "section", "uses_regions", "evidence_strength"}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def _strings(obj, key: str = "") -> list[str]:
    out: list[str] = []
    if isinstance(obj, str):
        if key not in _NON_CLAIM_KEYS:
            out.append(obj)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            out.extend(_strings(v, str(k)))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_strings(v, key))
    return out


def claim_strings(build: Path, extra: list | None = None) -> list[str]:
    """Every builder-claim string findable for this build (deduplicated, >= 12 chars)."""
    found: list[str] = []
    paths = [Path(build) / rel for rel in CLAIM_SOURCES] + [Path(p) for p in (extra or [])]
    for p in paths:
        if not p.is_file():
            continue
        txt = p.read_text(encoding="utf-8", errors="replace")
        if p.suffix == ".json":
            try:
                found.extend(_strings(json.loads(txt)))
                continue
            except ValueError:
                pass
        found.extend(ln for ln in txt.splitlines())
    seen, out = set(), []
    for s in found:
        n = _norm(s)
        if len(n) >= MIN_CLAIM_CHARS and not re.fullmatch(r"[\w./\\:#-]+", n) and n not in seen:
            seen.add(n)
            out.append(s.strip())
    return out


def find_leaks(brief_text: str, claims: list[str], sentences: list[str]) -> list[str]:
    """Claims that appear in the brief and are NOT part of a spoken sentence."""
    body = _norm(brief_text)
    spoken = [_norm(s) for s in sentences]
    leaks = []
    for c in claims:
        n = _norm(c)
        if len(n) < MIN_CLAIM_CHARS:
            continue
        if any(n in s for s in spoken):
            continue
        if n in body:
            leaks.append(c)
    return leaks


def slots_from_manifest(manifest: dict) -> list[dict]:
    """One verdict slot per spoken line, pointing at its sheet cell."""
    slots = []
    for sh in manifest.get("sheets", []):
        for c in sh.get("cells", []):
            if c["kind"] == "line" and c["src"] == "now":
                ln = manifest["lines"][c["cell"]]
                slots.append({"slot": c["cell"], "line_idx": ln["line_idx"],
                              "scene": sh["scene"], "sheet": sh["path"],
                              "frame": f"{sh['path']}#{c['cell']}",
                              "sentence": ln["sentence"],
                              "previous_cut": bool(ln.get("prev")),
                              "label": c["label"]})
    return slots


def events_from_manifest(manifest: dict) -> list[dict]:
    ev = []
    for sh in manifest.get("sheets", []):
        for c in sh.get("cells", []):
            if c["kind"] in ("cut", "hit"):
                ev.append({"cell": c["cell"], "kind": c["kind"], "sheet": sh["path"],
                           "label": c["label"]})
    return ev


def template(manifest: dict) -> dict:
    """The answer skeleton: every slot pre-filled with its frame + exact sentence."""
    schema_keys = [k for k in SCHEMA if k not in ("frame", "sentence")]
    return {"reviewer": REVIEWER_AGENT, "blind": True,
            "verdicts": [dict({"slot": s["slot"], "line_idx": s["line_idx"], "frame": s["frame"],
                               "sentence": s["sentence"]}, **{k: None for k in schema_keys})
                         for s in slots_from_manifest(manifest)],
            "events": [{"cell": e["cell"], "ok": None, "note": ""}
                       for e in events_from_manifest(manifest)]}


def render_markdown(manifest: dict) -> str:
    slots = slots_from_manifest(manifest)
    events = events_from_manifest(manifest)
    sheets = manifest.get("sheets", [])
    L = [f"# Frame review — blind brief for `{REVIEWER_AGENT}`", "", LIMITS, "",
         "## Your role",
         "You are a fresh reviewer. You have NOT seen how this video was planned or what its "
         "builder believes it shows. Judge only what is visible on the sheets against the exact "
         "sentence spoken at that moment.",
         "",
         "## Read these files and nothing else",
         "Open each sheet with the Read tool. Do not open any other file in the build — not the "
         "storyboard, not a claims file, not gate output. Reading them would make this review worthless.",
         ""]
    for sh in sheets:
        L.append(f"- `{sh['path']}` — scene `{sh['scene']}`"
                 + (f", page {sh['page']}" if sh.get("page", 1) > 1 else "")
                 + f"; each cell is {sh['scale']:.3f} of 1:1 (divide a glyph height measured on "
                   f"the sheet by {sh['scale']:.3f} to get its 1:1 height)")
    L += ["", "## How the sheets are laid out",
          "- `NOW` cells: the frame 85-90% through a spoken line — entrances have settled, the "
          "beat is at full strength.",
          "- `BEFORE` cells (only when a previous cut exists): the same line in the previous cut. "
          "Ask: what used to be here? Did something get lost?",
          "- `CUT-4f` cells: 4 frames before the scene cuts away. Everything this scene was meant "
          "to show should already be visible; nothing should still be waiting to appear.",
          "- `-16f / -3f / +2f` cells around a key reveal: they should read darkening, then held "
          "still, then already released.",
          "", "## One verdict per spoken line (every slot must be answered)", ""]
    for s in slots:
        L.append(f"### slot `{s['slot']}` — sheet `{Path(s['sheet']).name}`, cell `{s['label']}`")
        L.append("Exact sentence:")
        L.append(f"> {s['sentence']}")
        L.append("Previous cut beside it: " + ("yes" if s["previous_cut"] else "no"))
        L.append("")
    if events:
        L += ["## Cut and reveal cells (answer `ok` true/false with a note)", ""]
        for e in events:
            L.append(f"- `{e['cell']}` ({e['kind']}) on `{Path(e['sheet']).name}` — `{e['label']}`")
        L.append("")
    L += ["## Answer schema (every field, every slot)", ""]
    for k, v in SCHEMA.items():
        L.append(f"- `{k}`: {v}")
    L += ["", "`shows_what_is_said: contradicts` on any slot blocks the build. Measure glyph heights; "
          "do not estimate. An unanswered field counts as NOT verified.", "",
          "## Return format",
          "Reply with ONE fenced ```json block and nothing else. Start from the template file "
          "named below (you may Read it): keep every `slot`, `line_idx`, `frame` and `sentence` "
          "exactly as given and fill every other field.", "",
          f"Template: `{manifest.get('template_path', '')}`", ""]
    return "\n".join(L)


def write(manifest: dict, review_dir: Path) -> dict:
    """Write blind_brief.md, blind_brief.json (sheets + slots only) and the template."""
    review_dir = Path(review_dir)
    tpl_path = review_dir / "verdicts.template.json"
    manifest = dict(manifest, template_path=str(tpl_path))
    md = render_markdown(manifest)
    data = {"reviewer": REVIEWER_AGENT, "blind": True,
            "sheets": [{"path": s["path"], "scene": s["scene"], "scale": s["scale"]}
                       for s in manifest.get("sheets", [])],
            "slots": slots_from_manifest(manifest), "events": events_from_manifest(manifest),
            "schema": SCHEMA, "template": str(tpl_path)}
    (review_dir / "blind_brief.md").write_text(md, encoding="utf-8")
    (review_dir / "blind_brief.json").write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n",
                                                 encoding="utf-8")
    tpl_path.write_text(json.dumps(template(manifest), indent=1, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    return {"md": str(review_dir / "blind_brief.md"), "json": str(review_dir / "blind_brief.json"),
            "template": str(tpl_path), "text": md + "\n" + json.dumps(data, ensure_ascii=False)
            + "\n" + tpl_path.read_text(encoding="utf-8")}
