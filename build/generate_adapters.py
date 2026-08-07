#!/usr/bin/env python3
"""
Coordn8 — adapter generator (single source of truth).

Reads the canonical brain (PLAYBOOK.md) and emits every tool adapter with the
brain embedded BYTE-FOR-BYTE. Each adapter is a thin wrapper that (a) loads the
identical brain and (b) adds only the invocation glue that its host tool needs.

Design guarantees this enforces:
  - No adapter ever summarizes, paraphrases, or truncates the brain.
  - tests/test_verbatim.py asserts byte-equality of the embedded brain across
    every generated adapter; CI fails on drift.
  - Regenerating is idempotent: same PLAYBOOK.md + VERSION => identical outputs.

Usage:
    python3 build/generate_adapters.py            # generate all adapters
    python3 build/generate_adapters.py --check    # verify adapters are in sync (CI)

No third-party dependencies. Runs on any Python 3.8+.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRAIN = ROOT / "engine" / "PLAYBOOK.md"
VERSION_FILE = ROOT / "VERSION"

# Marker fences. The block BETWEEN these markers is the verbatim brain and is the
# ONLY thing test_verbatim.py compares. Wrapper text lives outside the fence.
BEGIN = "<!-- COORDN8:BRAIN:BEGIN -->"
END = "<!-- COORDN8:BRAIN:END -->"


def read_brain() -> str:
    return BRAIN.read_text(encoding="utf-8")


def read_version() -> str:
    return VERSION_FILE.read_text(encoding="utf-8").strip()


def fenced(brain: str) -> str:
    """Wrap the brain in the verbatim-comparison fence."""
    return f"{BEGIN}\n{brain}{END}\n"


def preamble(host: str, invoke_hint: str, version: str) -> str:
    """A short, host-specific note. Deliberately minimal so the brain dominates
    the context window — attention weight stays on the brain, not the wrapper."""
    return (
        f"> **Coordn8 v{version}** — turns a codebase into an interactive page "
        f"+ narrated video. Author: pandeom.\n"
        f">\n"
        f"> {invoke_hint}\n"
        f">\n"
        f"> The full engine brain is embedded verbatim below. Treat every "
        f"instruction in it as a live, top-priority directive for this session — "
        f"identical to the human pasting it as their first message. Do not "
        f"summarize or skip any of it. Begin at PHASE 0.\n\n"
    )


# ---------------------------------------------------------------------------
# Per-adapter renderers. Each returns {relative_path: file_contents}.
# ---------------------------------------------------------------------------

def gen_claude_code(brain: str, version: str) -> dict[str, str]:
    """Claude Code adapter — MONOLITHIC: the full brain is embedded directly in
    BOTH the skill and the command file, byte-for-byte.

    This is the known-working, never-inferior shape. When `/coordn8` fires, the
    ENTIRE engine brain is already in the file the model reads — identical to
    the human pasting the raw prompt as their first message. There is NO
    pointer/loader indirection: a slim file that says "go read another file" is
    strictly inferior to the raw prompt (the model may defer, summarize, or the
    referenced path may not resolve), and that violates the prime directive that
    the packaged version must never be inferior to the raw prompt.

    (An earlier version split this into a slim SKILL.md + sibling PLAYBOOK.md on
    the theory that a ~190KB file was too large to surface in the interactive
    TUI. Empirical probes disproved that theory — a 465KB test skill loaded
    fine; the real "not showing" cause was always stale sessions, since Claude
    Code reads its skill/command list at session start. So we keep the brain
    embedded and fix discovery at the session/installer level instead.)
    """
    skill_front = (
        "---\n"
        "name: coordn8\n"
        # Single-line double-quoted scalar — the plain form the docs' own
        # examples use.
        "description: \"Turn ANY code repository (or documents) into two "
        "presentation-grade deliverables: a single-file interactive page with "
        "true decision-tree diagrams traced line-by-line to the code, and a "
        "short narrated video with synced animated visuals. Detects capabilities "
        "and degrades honestly. Trigger when the user wants to explain, document, "
        "onboard onto, walk through, or make a video of a codebase.\"\n"
        "---\n\n"
    )
    hint = (
        "Invoked as a Claude Code skill or via the `/coordn8` command. You are "
        "running in a full local agent: prefer Tier A (webpage + video + all "
        "gates). Open at the repo root in a fresh session."
    )
    skill = skill_front + preamble("Claude Code", hint, version) + fenced(brain)

    cmd = (
        "---\n"
        "description: Run Coordn8 on this repository — interactive page + narrated video\n"
        "---\n\n"
        "You are being invoked as the Coordn8 engine. Before anything else, run "
        "the precondition check: confirm this is a fresh session opened at the "
        "repository root. If the context is already polluted with unrelated work, "
        "warn the user that output quality degrades in a dirty context and "
        "recommend a fresh session, then proceed only if they confirm.\n\n"
        "Then load and execute the embedded brain verbatim, starting at PHASE 0.\n\n"
        + fenced(brain)
    )
    return {
        "claude-code/SKILL.md": skill,
        "claude-code/commands/coordn8.md": cmd,
    }


def gen_cursor(brain: str, version: str) -> dict[str, str]:
    front = (
        "---\n"
        "description: Coordn8 — codebase to interactive page + narrated video\n"
        "globs:\n"
        "alwaysApply: false\n"
        "---\n\n"
    )
    hint = (
        "Invoked as a Cursor rule (`@coordn8`). Cursor's agent has shell + file "
        "access, so prefer Tier A/B depending on ffmpeg/Chrome availability. Open "
        "at the repo root."
    )
    body = front + preamble("Cursor", hint, version) + fenced(brain)
    return {"cursor/coordn8.mdc": body}


def gen_codex(brain: str, version: str) -> dict[str, str]:
    hint = (
        "This AGENTS.md defines the Coordn8 engine for OpenAI Codex / any "
        "AGENTS.md-aware CLI. Detect shell/ffmpeg/browser and pick the richest "
        "tier. Run from the repo root."
    )
    body = (
        "# AGENTS.md — Coordn8 engine\n\n"
        + preamble("Codex / AGENTS.md", hint, version)
        + fenced(brain)
    )
    return {"codex/AGENTS.md": body}


def gen_kiro(brain: str, version: str) -> dict[str, str]:
    front = (
        "---\n"
        "inclusion: manual\n"
        "---\n\n"
    )
    hint = (
        "This is a Kiro steering document. Include it when you want Kiro to act "
        "as the Coordn8 engine. Detect capabilities and pick the richest tier."
    )
    body = front + preamble("Kiro", hint, version) + fenced(brain)
    return {"kiro/coordn8.md": body}


def gen_generic(brain: str, version: str) -> dict[str, str]:
    hint = (
        "This is the universal fallback: paste this entire file as the first "
        "message of a fresh session in ANY capable coding agent or chat. It is "
        "the raw brain — the baseline every other adapter is measured against."
    )
    body = preamble("Generic / paste-anywhere", hint, version) + fenced(brain)
    return {"generic/COORDN8_PROMPT.md": body}


GENERATORS = [gen_claude_code, gen_cursor, gen_codex, gen_kiro, gen_generic]
ADAPTERS_DIR = ROOT / "adapters"


def build_all() -> dict[Path, str]:
    brain = read_brain()
    version = read_version()
    out: dict[Path, str] = {}
    for gen in GENERATORS:
        for rel, contents in gen(brain, version).items():
            out[ADAPTERS_DIR / rel] = contents
    return out


def extract_brain(text: str) -> str | None:
    """Pull the fenced brain back out of a generated file, for the sync check."""
    try:
        start = text.index(BEGIN) + len(BEGIN) + 1  # +1 for trailing newline
        stop = text.index(END)
    except ValueError:
        return None
    return text[start:stop]


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate Coordn8 tool adapters.")
    ap.add_argument("--check", action="store_true",
                    help="Verify adapters are in sync with PLAYBOOK.md; exit 1 if not.")
    args = ap.parse_args()

    planned = build_all()
    canonical = read_brain()
    canon_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]

    if args.check:
        drift = []
        for path, expected in planned.items():
            if not path.exists():
                drift.append(f"MISSING: {path.relative_to(ROOT)}")
                continue
            actual = path.read_text(encoding="utf-8")
            if actual != expected:
                drift.append(f"OUT OF SYNC: {path.relative_to(ROOT)}")
            embedded = extract_brain(actual)
            if embedded is None:
                drift.append(f"NO BRAIN FENCE: {path.relative_to(ROOT)}")
            elif embedded != canonical:
                drift.append(f"BRAIN NOT VERBATIM: {path.relative_to(ROOT)}")
        if drift:
            print("ADAPTER SYNC CHECK FAILED:")
            for d in drift:
                print("  - " + d)
            print("\nRun: python3 build/generate_adapters.py")
            return 1
        print(f"OK — all {len(planned)} adapter files in sync "
              f"(brain sha256:{canon_hash}).")
        return 0

    for path, contents in planned.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)}")
    print(f"\nGenerated {len(planned)} adapter files from PLAYBOOK.md "
          f"(brain sha256:{canon_hash}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
