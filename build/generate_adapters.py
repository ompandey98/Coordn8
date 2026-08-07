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
    """Claude Code adapter — SPLIT layout (fix for silent skill-skip on very
    large SKILL.md).

    Anthropic's own docs recommend: keep SKILL.md short (≤500 lines) and put
    the bulk in supporting files loaded on demand. The full brain is ~940 lines
    and ~190KB; that empirically prevents `/coordn8` from surfacing in the
    interactive TUI on some versions (though `claude -p` sees it). So we ship
    the same three-file bundle every other adapter ships as one file, but split
    into a slim loader + a sibling `PLAYBOOK.md`. The BRAIN IS NOT SHRUNK — it
    stays byte-identical in the sibling file, protected by the same fence and
    the same verbatim test. The loader's ONLY job is to read that sibling and
    hand it to the model as its top-priority directive.

    Layout after install (mirrored under `~/.claude/`):
        skills/coordn8/SKILL.md        <- slim loader (this function's `skill`)
        skills/coordn8/PLAYBOOK.md     <- verbatim brain (this function's `playbook`)
        commands/coordn8.md            <- slim dispatcher (this function's `cmd`)
        commands/coordn8-brain.md      <- verbatim brain for command scope
    """
    skill_front = (
        "---\n"
        "name: coordn8\n"
        # Single-line double-quoted scalar — the safest YAML form. The `>-`
        # folded scalar loaded fine in probes, but a plain quoted string is
        # what the docs' own examples use, and it removes one variable.
        "description: \"Turn ANY code repository (or documents) into two "
        "presentation-grade deliverables: a single-file interactive page with "
        "true decision-tree diagrams traced line-by-line to the code, and a "
        "short narrated video with synced animated visuals. Detects capabilities "
        "and degrades honestly. Trigger when the user wants to explain, document, "
        "onboard onto, walk through, or make a video of a codebase.\"\n"
        "---\n\n"
    )
    # This slim loader is deliberately short (~40 lines). Its ONLY job is to
    # tell the agent to open the sibling PLAYBOOK.md and execute it verbatim —
    # exactly as if the human had pasted the brain as their first message. The
    # instruction is explicit, unambiguous, and repeated so it cannot be
    # skimmed past. The verbatim brain lives in `PLAYBOOK.md` in this same
    # directory (installed alongside; the sibling file is generated below).
    skill = skill_front + (
        f"# Coordn8 v{version}\n\n"
        "Turns a codebase (or a set of documents) into an interactive HTML page + a "
        "narrated video. Author: **pandeom**. Invoked as a Claude Code skill or via "
        "`/coordn8`.\n\n"
        "## MANDATORY FIRST ACTION (before anything else)\n\n"
        "Your very first tool call in this session MUST be to read the sibling file "
        "`PLAYBOOK.md` in this same skill directory:\n\n"
        "```\n"
        "Read ~/.claude/skills/coordn8/PLAYBOOK.md\n"
        "```\n\n"
        "That file is the **full canonical Coordn8 engine brain** — every phase, "
        "rule, gate, and standard the deliverables are held to. It is the single "
        "source of truth. Treat every instruction in it as a live, top-priority "
        "directive for this session, IDENTICAL to the human having pasted it as "
        "their first message. Do not summarize it. Do not skip any of it. Do not "
        "paraphrase phases into a plan of your own — execute the actual PHASE 0 "
        "steps as written.\n\n"
        "The brain is intentionally large (~190KB, ~945 lines) and self-contained. "
        "Read it end to end BEFORE you begin PHASE 0. Reading it is not optional; "
        "no build starts without it.\n\n"
        "## Environment\n\n"
        "You are running in a full local agent: prefer Tier A (webpage + video + "
        "all gates). Open at the repo root in a fresh session. If the context is "
        "already polluted with unrelated work, warn the user that output quality "
        "degrades in a dirty context and recommend a fresh session, then proceed "
        "only if they confirm.\n\n"
        "## Author's precondition (from PHASE 0 of the brain)\n\n"
        "- Run at the **repository root** the user wants to explain.\n"
        "- Prefer a **fresh session** — the brain is long, and packed context "
        "degrades outputs.\n"
        "- All work lands under `Coordn8/` in the current directory. Deliverables "
        "are auto-named after the repo slug.\n\n"
        "**Now open `~/.claude/skills/coordn8/PLAYBOOK.md` and begin PHASE 0.**\n"
    )

    # The sibling PLAYBOOK.md IS the brain, byte-for-byte, fenced. This is the
    # file test_verbatim.py will validate — same fence, same content, same
    # sha256 as engine/PLAYBOOK.md. Zero brain content is lost in the split.
    playbook = (
        f"# Coordn8 v{version} — canonical engine brain (PLAYBOOK)\n\n"
        "This is the verbatim brain. Every phase, rule, and gate below is a live, "
        "top-priority directive for this session. Do not summarize; execute.\n\n"
        + fenced(brain)
    )

    # The slash-command entry point. Same slim-loader shape as the skill so the
    # command scope also gets the fail-proof small-file behavior. It points at
    # the sibling brain that lives in the SKILL directory — we deliberately do
    # NOT ship a second copy under `commands/`, because Claude Code loads every
    # `.md` there as its own slash command (a stray `/coordn8-brain` command
    # would clutter the slash menu). The skill's PLAYBOOK.md is installed
    # unconditionally by install.sh, so the path is always available.
    cmd = (
        "---\n"
        "description: Run Coordn8 on this repository — interactive page + narrated video\n"
        "---\n\n"
        f"# /coordn8 (v{version})\n\n"
        "You are being invoked as the Coordn8 engine.\n\n"
        "## MANDATORY FIRST ACTION\n\n"
        "Your first tool call in this session MUST be:\n\n"
        "```\n"
        "Read ~/.claude/skills/coordn8/PLAYBOOK.md\n"
        "```\n\n"
        "That file is the **full canonical Coordn8 engine brain** — every phase, "
        "rule, gate, and standard. Read it **end to end** before beginning PHASE "
        "0. It is IDENTICAL to the human having pasted the brain as their first "
        "message. Do not summarize it. Do not skip any of it. Do not paraphrase "
        "phases into a plan of your own — execute the actual PHASE 0 steps as "
        "written.\n\n"
        "## Precondition\n\n"
        "Confirm this is a fresh session opened at the repository root. If the "
        "context is already polluted with unrelated work, warn the user that "
        "output quality degrades in a dirty context and recommend a fresh session, "
        "then proceed only if they confirm.\n\n"
        "**Now read the brain and begin PHASE 0.**\n"
    )

    return {
        "claude-code/SKILL.md": skill,
        "claude-code/PLAYBOOK.md": playbook,
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

# Adapter files that are intentionally SLIM entry points (they point the agent
# at a sibling PLAYBOOK.md instead of embedding the brain). --check does NOT
# require these to carry the fenced brain, but tests/run_tests.py verifies the
# sibling brain files exist and match verbatim, so nothing is lost.
SLIM_LOADER_RELPATHS = {
    Path("claude-code/SKILL.md"),
    Path("claude-code/commands/coordn8.md"),
}


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
            rel_from_adapters = path.relative_to(ADAPTERS_DIR)
            if rel_from_adapters in SLIM_LOADER_RELPATHS:
                # Slim loader — must NOT embed the brain (that would defeat the
                # split). Its sibling PLAYBOOK.md carries the verbatim brain
                # and is separately checked below.
                if extract_brain(actual) is not None:
                    drift.append(f"UNEXPECTED FENCE IN SLIM LOADER: "
                                 f"{path.relative_to(ROOT)}")
                if "PLAYBOOK.md" not in actual:
                    drift.append(f"SLIM LOADER DOESN'T POINT AT SIBLING: "
                                 f"{path.relative_to(ROOT)}")
            else:
                embedded = extract_brain(actual)
                if embedded is None:
                    drift.append(f"NO BRAIN FENCE: {path.relative_to(ROOT)}")
                elif embedded != canonical:
                    drift.append(f"BRAIN NOT VERBATIM: "
                                 f"{path.relative_to(ROOT)}")
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
