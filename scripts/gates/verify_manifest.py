#!/usr/bin/env python3
"""
Coordn8 gate — FINAL VERIFICATION MANIFEST (both deliverables).

This is the anti-fraud gate for the BUILD ITSELF (PLAYBOOK "Final audit" +
end-of-build verification claim). Its whole job: a deliverable may only be called
"done", and the end-user-facing "Verified: sync, animation, explainability, …"
claim may only be WRITTEN, when every checklist item it names has actually
passed with matching evidence. It makes "COORDN8 can never give garbage output"
mechanical instead of aspirational.

It reads a `_build/verification.json` the agent fills in as it runs the gates and
its own checks — one entry per checklist item — and enforces three things a model
under pressure tends to violate:

  1. NO EMPTY ASSERTIONS. Every item that claims "pass" must carry `evidence`
     (what was run / seen). A "pass" with no evidence is downgraded to a finding —
     matches the honesty rule "no claim without the evidence that matches it".
  2. NO OVERCLAIM. The manifest's `claims` list (the exact words that would be
     shown to the end user, e.g. "sync", "animation", "explainability") is
     cross-checked against item status. If a claim names something that is not a
     genuine `pass`, that is a FRAUD and is flagged hard — the claim string is
     REFUSED. The gate then emits the ONLY claim string that is safe to show:
     the intersection of "claimed" and "actually passed".
  3. NOTHING SILENTLY OWED. Any required item that is missing, `fail`,
     `untested`, or `skipped` is surfaced as still-open, with any `risk` /
     `assumption` the agent recorded — so it goes in the chat, not swept away.

REQUIRED items (must be `pass` before a video ships; the page subset applies when
there is no video) are the load-bearing gates from the consolidated gate table.
The set is overridable per project via `required_items` in the manifest, because
the brain is advisory — but DROPPING a default required item is itself recorded
and surfaced, never silent.

Data contract (`_build/verification.json`):
    {
      "deliverable": "video",              # "video" | "page" | "both"
      "claims": ["sync", "animation", "explainability"],  # words shown to user
      "required_items": [...optional override...],
      "items": [
        {"id": "beat_sync", "status": "pass",
         "evidence": "beat_sync.py on final.mp4: 42/42 rows matched [verified-live]",
         "claim": "sync"},
        {"id": "layout", "status": "pass",
         "evidence": "layout_probe.py: 0 overflow/collision across 30 samples",
         "claim": "animation"},
        {"id": "explainability", "status": "fail",
         "evidence": "explainability critic: scene 4 told-not-shown",
         "claim": "explainability", "risk": "scene 4 needs a better visual"}
      ]
    }

status ∈ pass | fail | untested | skipped.  ADVISORY like every gate (exit 1 =
REVIEW), but its findings are the last line of defense before a false "Verified"
reaches a human — treat them as blocking in spirit.

Usage:
    python3 scripts/gates/verify_manifest.py _build/verification.json
    python3 scripts/gates/verify_manifest.py _build/verification.json --deliverable page
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.common import (EXIT_ERROR, EXIT_PASS, EXIT_REVIEW, EXIT_SKIPPED,  # noqa: E402
                        emit, load_json)

VALID_STATUS = {"pass", "fail", "untested", "skipped"}

# Load-bearing checklist items per deliverable (from the consolidated gate table
# in PLAYBOOK.md). These must be `pass` before the deliverable is called done.
# Overridable via manifest `required_items`, but any drop is surfaced, not silent.
REQUIRED = {
    "page": [
        "facts_trace", "tree_shape", "dom_smoke", "content_ban",
        "visual_density", "shareability", "explainability", "adversarial",
    ],
    "video": [
        "facts_trace", "content_ban", "visual_density", "layout", "beat_sync",
        "media_probe", "shareability", "voice_meaning", "explainability",
        "no_meta_chrome", "anti_slop",
    ],
}


def _required_for(deliverable: str, override) -> list[str]:
    if isinstance(override, list) and override:
        return [str(x) for x in override]
    if deliverable == "both":
        # union, page items first, de-duped, preserving order
        seen, out = set(), []
        for it in REQUIRED["page"] + REQUIRED["video"]:
            if it not in seen:
                seen.add(it); out.append(it)
        return out
    return list(REQUIRED.get(deliverable, []))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest", help="_build/verification.json (see docstring)")
    ap.add_argument("--deliverable", choices=("video", "page", "both"),
                    default=None, help="override the manifest's deliverable")
    args = ap.parse_args()

    path = Path(args.manifest)
    if not path.exists():
        emit({"gate": "verify_manifest", "status": "SKIPPED",
              "summary": f"no verification manifest at {path} — the build must "
                         f"produce one before any 'Verified: …' claim is shown "
                         f"(PLAYBOOK final audit)"})
        return EXIT_SKIPPED
    try:
        data = load_json(path)
    except Exception as e:  # noqa
        emit({"gate": "verify_manifest", "status": "ERROR",
              "summary": f"could not parse {path}: {e}"})
        return EXIT_ERROR

    deliverable = args.deliverable or data.get("deliverable") or "both"
    items = data.get("items") or []
    if not items:
        emit({"gate": "verify_manifest", "status": "SKIPPED",
              "summary": "manifest has no items[] — nothing verified yet"})
        return EXIT_SKIPPED

    # Index items by id and by the claim word they back.
    by_id: dict[str, dict] = {}
    findings: list[str] = []
    passed_claims: set[str] = set()
    passed_ids: set[str] = set()

    for it in items:
        iid = str(it.get("id") or "?")
        status = str(it.get("status") or "").lower().strip()
        evidence = str(it.get("evidence") or "").strip()
        claim = str(it.get("claim") or "").strip()
        by_id[iid] = it

        if status not in VALID_STATUS:
            findings.append(
                f"item '{iid}' has invalid status {status!r} "
                f"(expected pass|fail|untested|skipped).")
            continue

        # 1 — no empty assertions: a pass must carry evidence.
        if status == "pass" and not evidence:
            findings.append(
                f"item '{iid}' is marked pass but has NO evidence — a pass "
                f"without matching evidence is not a pass (honesty rule). Record "
                f"what was run/seen, or change the status.")
            continue

        if status == "pass":
            passed_ids.add(iid)
            if claim:
                passed_claims.add(claim.lower())

    # 2 — no overclaim: every word in `claims` must be backed by a genuine pass.
    claims = [str(c).strip() for c in (data.get("claims") or []) if str(c).strip()]
    unbacked = [c for c in claims if c.lower() not in passed_claims]
    for c in unbacked:
        # find the offending item(s) that name this claim but didn't pass
        offenders = [str(it.get("id"))
                     for it in items
                     if str(it.get("claim") or "").strip().lower() == c.lower()
                     and str(it.get("status") or "").lower() != "pass"]
        where = f" (item(s): {', '.join(offenders)})" if offenders else \
                " (no item backs this claim at all)"
        findings.append(
            f"OVERCLAIM: the end-user claim \"{c}\" is NOT backed by a passing "
            f"item{where}. Refusing to authorize this claim string — never write "
            f"'Verified: {c}' unless {c} actually passed.")

    # The ONLY claim string safe to show a human: claimed AND actually passed.
    safe_claims = [c for c in claims if c.lower() in passed_claims]

    # 3 — nothing silently owed: required items missing or not-passing are open.
    required = _required_for(deliverable, data.get("required_items"))
    dropped = [] if not isinstance(data.get("required_items"), list) else \
        [d for d in _required_for(deliverable, None) if d not in required]
    for d in dropped:
        findings.append(
            f"required item '{d}' was DROPPED from this build's required_items — "
            f"allowed (advisory), but recorded here so it isn't silently skipped. "
            f"Confirm it genuinely does not apply to THIS project.")

    still_open: list[dict] = []
    for req in required:
        it = by_id.get(req)
        if it is None:
            still_open.append({"id": req, "status": "missing",
                               "note": "required but not in the manifest"})
            findings.append(
                f"required item '{req}' is MISSING from the manifest — it must be "
                f"run and recorded before the {deliverable} is called done.")
        elif str(it.get("status") or "").lower() != "pass" or req not in passed_ids:
            still_open.append({
                "id": req, "status": str(it.get("status") or "?"),
                "evidence": str(it.get("evidence") or ""),
                "risk": str(it.get("risk") or ""),
                "assumption": str(it.get("assumption") or ""),
            })

    # Collect risks/assumptions across ALL items (surface in chat, per PLAYBOOK).
    risks = [{"id": str(it.get("id")), "risk": str(it.get("risk") or ""),
              "assumption": str(it.get("assumption") or "")}
             for it in items
             if str(it.get("risk") or "").strip()
             or str(it.get("assumption") or "").strip()]

    ready = not still_open and not unbacked and not findings
    total = len(items)
    n_pass = len(passed_ids)

    emit({
        "gate": "verify_manifest",
        "status": "PASS" if ready else "REVIEW",
        "deliverable": deliverable,
        "items_total": total,
        "items_passed": n_pass,
        "required_count": len(required),
        "still_open": still_open,
        "overclaimed": unbacked,
        # the exact, safe claim string the agent MAY show the end user — empty if
        # nothing is genuinely verified yet. Never show a claim not in this list.
        "authorized_claim": (
            "Verified: " + ", ".join(safe_claims) if safe_claims else ""),
        "authorized_claims_list": safe_claims,
        "risks_and_assumptions": risks,
        "findings": findings,
        "summary": (
            f"READY: all {len(required)} required item(s) pass; "
            f"authorized claim = \"Verified: {', '.join(safe_claims)}\""
            if ready else
            f"NOT READY: {len(still_open)} required item(s) open, "
            f"{len(unbacked)} overclaim(s) — do NOT show a 'Verified' claim yet; "
            f"surface the open items and any risk/assumption in chat"),
        "advisory_note": "This is the last line of defense before a false "
                         "'Verified' reaches a human. Treat REVIEW as blocking: "
                         "fix the open items, or narrow the claim to "
                         "authorized_claim, and state what remains owed.",
    })
    return EXIT_PASS if ready else EXIT_REVIEW


if __name__ == "__main__":
    sys.exit(main())
