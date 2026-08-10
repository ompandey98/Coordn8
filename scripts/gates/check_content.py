#!/usr/bin/env python3
"""
Coordn8 gate — CONTENT INTEGRITY: approved strings, repetition, voice-reads-the-screen,
and the identifier sweep that also covers SIDECAR files.

FOUR DEFECT CLASSES, EACH OBSERVED IN A REAL BUILD
--------------------------------------------------
  APPROVED (D9) — the requester approves a specific string (a title, a tagline, a
      claim) verbatim; a later pass substitutes a different phrase — often a *theme*
      phrase from elsewhere in the brief — and the approved wording is silently lost.
      An approved title was replaced TWICE across versions and the requester had to
      search back through the conversation to recover their own words.
      Rule: a theme phrase is NEVER a licence to rewrite an approved string.

  REPETITION (D10a) — the same fact restated across beats, so the video feels long
      and circular. A reviewer reported the same information "repeating ten times"
      without ever connecting in the flow. One real build went 16 -> 0 on this gate.
      Test: a 5-gram shared by two DIFFERENT narration lines is a failure.

  SCREEN-READING (D10b) — the narration recites a list of names/values that is
      already visible on the frame. Rule: if a set of names/values is on the frame,
      the voice speaks the CATEGORY, not the list.

  IDENTIFIERS — real identifiers/PII on a frame **or in any sidecar file**. Retracted
      figures survived in a stale caption file long after being removed from the
      video; any re-mux would have shipped them. So the sweep MUST cover sidecars
      (.srt/.vtt, intermediate JSON, build logs shipped alongside), not just frames.

ADVISORY: findings are REVIEW signals. An approved-string drift is reported as
BLOCKING — it is the requester's own words being overwritten.

Usage:
    python3 scripts/gates/check_content.py --script _build/script.json \\
        --approved _research/approved_strings.json --sidecar Coordn8/out.srt
    python3 scripts/gates/check_content.py --narration-text narration.txt --on-screen frame_text.json
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

NGRAM = 5               # a shared 5-gram across two different lines = repetition
LIST_MIN = 3            # 3+ on-screen names recited in one sentence = screen-reading

# Identifier shapes that must never reach a frame or a sidecar. Deliberately
# conservative — every hit is a lead the agent clears, not an automatic veto.
ID_PATTERNS = [
    (r"\b\d{12}\b", "12-digit account-number shape"),
    (r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", "UUID"),
    (r"\b[0-9a-f]{40}\b", "40-hex (commit/sha1)"),
    (r"\b[0-9a-f]{64}\b", "64-hex (sha256)"),
    (r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", "AWS access key id"),
    (r"\barn:aws[^\s\"']{10,}", "AWS ARN"),
    (r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b", "email address"),
    (r"\b(?:\+?\d[\d\s-]{8,}\d)\b", "phone-number shape"),
    (r"/Users/[^/\s\"']+", "local user home path"),
    (r"/home/[^/\s\"']+", "local user home path"),
    (r"\bhttps?://(?:localhost|127\.0\.0\.1|10\.\d+\.\d+\.\d+|192\.168\.)[^\s\"']*",
     "internal/localhost URL"),
]


def norm_words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(text).lower())


def collect_lines(script: dict) -> list[tuple[str, str]]:
    """Return [(location, spoken_text)] from any of the script shapes we accept."""
    out: list[tuple[str, str]] = []
    scenes = script.get("scenes")
    if isinstance(scenes, dict):
        for sid, sc in scenes.items():
            for i, ln in enumerate(sc.get("lines") or []):
                out.append((f"{sid}[{i}]", ln.get("text") or ln.get("narration") or ""))
    elif isinstance(scenes, list):
        for sc in scenes:
            sid = sc.get("id") or sc.get("name") or "scene"
            lines = sc.get("lines") or sc.get("sentences") or []
            if lines:
                for i, ln in enumerate(lines):
                    out.append((f"{sid}[{i}]",
                                (ln.get("text") if isinstance(ln, dict) else str(ln)) or ""))
            elif sc.get("narration"):
                out.append((f"{sid}", sc["narration"]))
    for key in ("sentences", "lines"):
        if key in script and isinstance(script[key], list):
            for i, ln in enumerate(script[key]):
                out.append((f"{key}[{i}]",
                            (ln.get("text") if isinstance(ln, dict) else str(ln)) or ""))
    return [(loc, t) for loc, t in out if str(t).strip()]


def gate_repetition(lines: list[tuple[str, str]]) -> list[str]:
    seen: dict[str, str] = {}
    dups: list[str] = []
    for loc, text in lines:
        words = norm_words(text)
        for k in range(len(words) - NGRAM + 1):
            sh = " ".join(words[k:k + NGRAM])
            if sh in seen and seen[sh] != loc:
                dups.append(f'"{sh}" repeats in {seen[sh]} and {loc}')
            else:
                seen.setdefault(sh, loc)
    # De-duplicate overlapping reports of the same repeated phrase pair.
    uniq, out = set(), []
    for d in dups:
        key = d.split('"')[1][:40]
        if key not in uniq:
            uniq.add(key)
            out.append(d)
    return out


def gate_approved(deliverable_texts: dict[str, str], approved: list[dict]) -> list[str]:
    """Every approved string must appear BYTE-EXACT in the deliverable it belongs to."""
    findings = []
    for entry in approved:
        s = entry.get("string")
        if not s:
            continue
        where = entry.get("where") or "any"
        quote = entry.get("approved_by_quote") or entry.get("quote") or ""
        targets = ({k: v for k, v in deliverable_texts.items() if where in (k, "any")}
                   or deliverable_texts)
        if not any(s in v for v in targets.values()):
            near = ""
            # Try to show what replaced it — the most actionable part of the finding.
            for v in targets.values():
                head = " ".join(s.split()[:2])
                if head and head in v:
                    idx = v.find(head)
                    near = v[max(0, idx - 10): idx + len(s) + 30].replace("\n", " ")
                    break
            findings.append(
                f"APPROVED STRING MISSING from {where}: {s!r}"
                + (f" — nearest text found: …{near}…" if near else "")
                + (f" (approved by: {quote!r})" if quote else "")
                + ". An approved string is frozen until the owner changes it; a theme "
                  "phrase is never a licence to rewrite it.")
    return findings


def gate_screen_reading(lines: list[tuple[str, str]],
                        on_screen: dict[str, list[str]]) -> list[str]:
    """A sentence reciting >=LIST_MIN names that are already on the frame."""
    findings = []
    for loc, text in lines:
        scene = loc.split("[")[0]
        names = on_screen.get(scene) or on_screen.get("*") or []
        if not names:
            continue
        hits = [n for n in names if n and n.lower() in text.lower()]
        if len(hits) >= LIST_MIN:
            findings.append(
                f"{loc} recites {len(hits)} names already on the frame ({', '.join(hits[:6])}) "
                f"— the voice should speak the CATEGORY, the frame carries the list")
    return findings


def gate_identifiers(sources: dict[str, str]) -> list[str]:
    findings = []
    for name, text in sources.items():
        for pat, label in ID_PATTERNS:
            for m in re.finditer(pat, text):
                val = m.group(0)
                findings.append(f"{name}: {label} -> {val[:48]!r}")
    # cap the noise; the agent clears each lead
    return findings[:40]


def main() -> int:
    ap = argparse.ArgumentParser(description="Coordn8 content-integrity gate.")
    ap.add_argument("--script", default=None, help="script/beatmap JSON (narration source)")
    ap.add_argument("--narration-text", default=None, help="plain-text narration file")
    ap.add_argument("--approved", default=None, help="approved_strings.json")
    ap.add_argument("--page", default=None, help="the final HTML page (approved-string target)")
    ap.add_argument("--sidecar", action="append", default=[],
                    help="sidecar artifacts to sweep for identifiers (.srt/.vtt/.json/.log)")
    ap.add_argument("--on-screen", default=None,
                    help='JSON {"scene": ["Name A", "Name B"]} of names visible on frames')
    args = ap.parse_args()

    lines: list[tuple[str, str]] = []
    deliverable_texts: dict[str, str] = {}

    if args.script and Path(args.script).exists():
        try:
            script = json.loads(Path(args.script).read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            emit({"gate": "content_integrity", "status": "ERROR",
                  "summary": f"could not parse --script: {type(e).__name__}: {e}"})
            return EXIT_ERROR
        lines = collect_lines(script)
        deliverable_texts["narration"] = "\n".join(t for _, t in lines)
    if args.narration_text and Path(args.narration_text).exists():
        txt = Path(args.narration_text).read_text(encoding="utf-8")
        deliverable_texts["narration"] = deliverable_texts.get("narration", "") + "\n" + txt
        if not lines:
            lines = [(f"line[{i}]", ln) for i, ln in enumerate(
                [l for l in txt.splitlines() if l.strip()])]
    if args.page and Path(args.page).exists():
        deliverable_texts["page"] = Path(args.page).read_text(encoding="utf-8")

    if not lines and not deliverable_texts:
        emit({"gate": "content_integrity", "status": "SKIPPED",
              "summary": "no narration/page supplied — nothing to check"})
        return EXIT_SKIPPED

    findings: dict[str, list[str]] = {}
    findings["repetition"] = gate_repetition(lines) if lines else []

    approved_list: list[dict] = []
    if args.approved and Path(args.approved).exists():
        try:
            data = json.loads(Path(args.approved).read_text(encoding="utf-8"))
            approved_list = data.get("strings") if isinstance(data, dict) else data
        except Exception as e:  # noqa: BLE001
            emit({"gate": "content_integrity", "status": "ERROR",
                  "summary": f"could not parse --approved: {type(e).__name__}: {e}"})
            return EXIT_ERROR
    findings["approved_strings"] = (gate_approved(deliverable_texts, approved_list)
                                   if approved_list else [])

    on_screen = {}
    if args.on_screen and Path(args.on_screen).exists():
        try:
            on_screen = json.loads(Path(args.on_screen).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            on_screen = {}
    findings["voice_reads_screen"] = (gate_screen_reading(lines, on_screen)
                                     if on_screen and lines else [])

    sweep: dict[str, str] = dict(deliverable_texts)
    swept_sidecars = []
    for p in args.sidecar:
        pp = Path(p)
        if pp.exists():
            try:
                sweep[f"sidecar:{pp.name}"] = pp.read_text(encoding="utf-8", errors="replace")
                swept_sidecars.append(pp.name)
            except Exception:  # noqa: BLE001
                pass
    findings["identifiers"] = gate_identifiers(sweep)

    n = sum(len(v) for v in findings.values())
    blocking = len(findings["approved_strings"])
    not_checked = []
    if not approved_list:
        not_checked.append("approved_strings (no --approved ledger supplied)")
    if not on_screen:
        not_checked.append("voice_reads_screen (no --on-screen name map supplied)")
    if not swept_sidecars:
        not_checked.append("sidecar identifier sweep (no --sidecar files supplied)")

    emit({
        "gate": "content_integrity",
        "status": "REVIEW" if n else "PASS",
        "narration_lines": len(lines),
        "sidecars_swept": swept_sidecars,
        "counts": {k: len(v) for k, v in findings.items()},
        "blocking_count": blocking,
        "findings": {k: v for k, v in findings.items() if v},
        "not_checked": not_checked,
        "summary": (
            f"{n} content finding(s)"
            + (f" — {blocking} APPROVED-STRING drift (the requester's own words were "
               f"overwritten; this blocks)" if blocking else "")
            + (f". NOT CHECKED: {'; '.join(not_checked)}" if not_checked else "")
            if n else
            f"no content findings over {len(lines)} narration line(s)"
            + (f". NOT CHECKED: {'; '.join(not_checked)}" if not_checked else "")),
    })
    return EXIT_REVIEW if n else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
