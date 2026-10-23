#!/usr/bin/env python3
"""
Coordn8 — requirements audit for the 2.0 contract.

Every requirement is mapped to the exact check names (in tests/v2 suites) that
prove its acceptance test. A requirement is SUPPORTED only when every mapped
check exists and passed in this run. No mapping = OPEN. This proves a mechanism
exists and is exercised — it is not a claim that any particular video is good.

    python3 tests/audit_v2.py            # runs every v2 suite, prints the table
    python3 tests/audit_v2.py --json     # machine-readable
Exit 0 only at 100 %.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "v2"))
import _runall  # noqa: E402

# id: (priority, title, [evidence "suite::check name", ...])
REQS: dict[str, tuple[str, str, list[str]]] = {
    "V1": ("P0", "Local neural voice engines installed and used by default", []),
    "V2": ("P0", "Voice casting per narrative and audience", []),
    "V3": ("P1", "Per-line expressiveness (emphasis, pauses, pace)", []),
    "V4": ("P1", "Voice quality check (clipping, gaps, glossary terms)", []),
    "A1": ("P0", "Palette Director", []),
    "A2": ("P0", "Palette and contrast gate", []),
    "A3": ("P0", "Style kits (>= 8, original)", []),
    "A4": ("P1", "Style distillation from reference videos/images", []),
    "A5": ("P1", "Brand onboarding and saved profiles", []),
    "B1": ("P0", "Diagram type selector (>= 40 types)", []),
    "B2": ("P0", "Complexity budget gate", []),
    "B3": ("P0", "Deterministic layout engine", []),
    "B4": ("P0", "Connector and geometry gate", []),
    "B5": ("P1", "Chart honesty (data bound to marks, recomputed)", []),
    "B6": ("P2", "Redraw from Mermaid / draw.io / Excalidraw with a fidelity ledger", []),
    "B7": ("P1", "Diagram export (SVG, PNG, JSON registry)", []),
    "C1": ("P0", "Page template + component library", []),
    "C2": ("P0", "Accessible glossary tooltips", []),
    "C3": ("P0", "Claims ledger on every page; claims sidecar for every video", []),
    "C4": ("P0", "Content rules (page)", []),
    "C5": ("P1", "Explain modes and dials", []),
    "C6": ("P1", "Understanding checks", []),
    "D1": ("P0", "Plain-language checker with register profiles", []),
    "D2": ("P0", "One term per concept across page, captions, voice", []),
    "D3": ("P1", "Truth tests (unbacked claim -> estimate)", []),
    "E1": ("P0", "Colour-coded caption markup -> colour roles", []),
    "E2": ("P0", "Word-level timing from real alignment; throws on a missing word", []),
    "E3": ("P1", "Key-reveal hit + optional beat grid", []),
    "E4": ("P1", "Local music and SFX, ducking, -16 LUFS, stems", []),
    "E5": ("P1", "Audio QA (loudness, true peak, SFX audibility)", []),
    "E6": ("P0", "Automatic frame review (contact sheets, blind reviewer)", []),
    "E7": ("P0", "Scene catalog (>= 25 content-shape -> scene components)", []),
    "E8": ("P1", "Caption style library (never below 28 px)", []),
    "E9": ("P1", "Transitions and continuity", []),
    "E10": ("P0", "Determinism (seeded randomness; clock/random banned in scenes)", []),
    "F1": ("P0", "Ship as a Claude Code plugin", []),
    "F2": ("P0", "8+ dedicated subagents with tool allowlists", []),
    "F3": ("P0", "Hooks (stop gate, post-render quality, agent log, session recall)", []),
    "F4": ("P0", "Failure-analysis and self-correction loop", []),
    "F5": ("P0", "Current Claude Code mechanisms by current names", []),
    "F6": ("P1", "Kiro / Codex / Cursor parity for every script and data file", []),
    "F7": ("P1", "Use connectors and design tools when present", []),
    "F8": ("P1", "Skill federation", []),
    "G1": ("P0", "Benchmark suite v2.0 vs v1.5.0", []),
    "G2": ("P0", "Name-leak test (0 reference names)", []),
    "G3": ("P1", "Skill-vs-baseline evals with assertions and stability", []),
    "G4": ("P0", "Capability-coverage test", []),
    "H1": ("P0", "Freedom doctrine: catalogs are menus, never fences", []),
    "H2": ("P0", "Divergence step with named techniques + separate critic", []),
    "H3": ("P0", "Anti-repetition across runs (seeded randomness + history)", []),
    "H4": ("P1", "Variant treatments for pivotal moments, judged blind", []),
    "H5": ("P1", "Invention ledger with promotion", []),
    "I1": ("P0", "Audience framing", []),
    "I2": ("P0", "Explanation shapes per subject type", []),
    "I3": ("P0", "Fenced analogies and one example", []),
    "I4": ("P0", "End where they can act; caveats only when they cost something", []),
    "I5": ("P1", "Stand down for experts in their own field", []),
    "J1": ("P0", "Every install force-updates to the latest", []),
    "J2": ("P0", "User data lives outside the package and is never touched", []),
    "J3": ("P0", "No user file overwritten without backup and merge", []),
    "J4": ("P0", "Install also installs every dependency, never stuck", []),
    "J5": ("P0", "Uninstall removes the package only; purge is explicit", []),
    "J6": ("P1", "Versioned user-data schema with backed-up migrations", []),
    "J7": ("P0", "MCP servers installed automatically; sign-in ones last", []),
    "J8": ("P1", "Up-to-date references refreshed at install", []),
    "K1": ("P0", "Structured two-level memory", []),
    "K2": ("P0", "Retrieval index over the project's files", []),
    "K3": ("P0", "Freshness: every index entry knows its source hash", []),
    "K4": ("P1", "Directory maps", []),
    "K5": ("P1", "Memory hygiene: supersede, never delete; conflict surfacing", []),
}

EVIDENCE_FILE = HERE / "v2" / "evidence_map.json"


def load_evidence() -> dict[str, list[str]]:
    """Supervisor-maintained mapping requirement -> check names (merged after each wave)."""
    ev = {k: list(v[2]) for k, v in REQS.items()}
    if EVIDENCE_FILE.exists():
        extra = json.loads(EVIDENCE_FILE.read_text(encoding="utf-8"))
        for k, names in extra.items():
            if k in ev:
                ev[k] = list(dict.fromkeys(ev[k] + names))
    return ev


def audit(allres: dict) -> list[dict]:
    passed = _runall.passed_names(allres)
    rows = []
    for rid, (pri, title, _) in REQS.items():
        names = load_evidence()[rid]
        missing = [n for n in names if n not in passed]
        status = "SUPPORTED" if names and not missing else ("OPEN" if not names else "FAILING")
        rows.append({"id": rid, "priority": pri, "title": title, "status": status,
                     "evidence": names, "missing": missing})
    return rows


def main() -> int:
    allres = _runall.load_or_run()
    rows = audit(allres)
    ok = sum(r["status"] == "SUPPORTED" for r in rows)
    if "--json" in sys.argv:
        print(json.dumps({"supported": ok, "total": len(rows), "rows": rows}, indent=1))
    else:
        print("Coordn8 2.0 requirements audit")
        print("=" * 78)
        for r in rows:
            mark = {"SUPPORTED": "ok  ", "OPEN": "OPEN", "FAILING": "FAIL"}[r["status"]]
            print(f"  {mark} {r['id']:4s} {r['priority']}  {r['title'][:58]}")
            for m in r["missing"]:
                print(f"         missing/failed: {m}")
        print("=" * 78)
        p0 = [r for r in rows if r["priority"] == "P0"]
        print(f"P0: {sum(r['status'] == 'SUPPORTED' for r in p0)}/{len(p0)}")
        print(f"RESULT: {ok}/{len(rows)} requirements supported by present, passing checks "
              f"({100 * ok // len(rows)} %)")
        print("NOTE: this proves each mechanism exists and its acceptance test passes — "
              "it is not a claim that any particular video is good.")
    return 0 if ok == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
