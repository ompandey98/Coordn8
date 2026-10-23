#!/usr/bin/env python3
"""Coordn8 — the ONE place paths are defined. Zero third-party deps.

Two storage zones, never mixed:

  PACKAGE-OWNED   the installed skill / agents / hooks / bin / adapters and the
                  package checkout itself. Install and update replace these freely.
  USER-OWNED      ~/.coordn8/ (memory, profiles, library, voices, refs, backups,
                  telemetry history) and every project's Coordn8/ build folder.
                  Install, update and uninstall may only CREATE MISSING DIRECTORIES
                  here — never modify or delete an existing file.

Overrides (used by tests and sandboxes):
  COORDN8_HOME   replaces ~/.coordn8
  CLAUDE_HOME    replaces ~/.claude
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parents[2]

# Every sub-directory of the user zone, with what lives there.
USER_SUBDIRS = {
    "memory": "typed memory entries, generated index, per-build choice history",
    "library": "invented palettes/kits/diagram forms/scenes and their promotion record",
    "profiles": "saved brand profiles",
    "voices": "downloaded local voice models (survive reinstall)",
    "refs": "fetched public reference docs with fetch dates",
    "backups": "timestamped copies taken before any user file is touched",
    "telemetry": "run history",
}

USER_SCHEMA_VERSION = 2


def user_home() -> Path:
    env = os.environ.get("COORDN8_HOME")
    return Path(env).expanduser() if env else Path.home() / ".coordn8"


def claude_home() -> Path:
    env = os.environ.get("CLAUDE_HOME")
    return Path(env).expanduser() if env else Path.home() / ".claude"


def user_dir(name: str) -> Path:
    if name not in USER_SUBDIRS:
        raise KeyError(f"unknown user-zone directory: {name}")
    return user_home() / name


def ensure_user_home() -> list[str]:
    """Create MISSING user-zone directories (and schema.json if absent).
    Never modifies an existing file. Returns what was created."""
    created: list[str] = []
    home = user_home()
    for d in [home] + [home / n for n in USER_SUBDIRS]:
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            created.append(str(d))
    schema = home / "schema.json"
    if not schema.exists():
        schema.write_text(json.dumps({"schema": USER_SCHEMA_VERSION,
                                      "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                      "migrated": []}, indent=2) + "\n",
                          encoding="utf-8")
        created.append(str(schema))
    return created


def project_build(repo: str | Path) -> Path:
    """A project's Coordn8 folder (deliverables, _research/, _build/). USER-OWNED."""
    return Path(repo).resolve() / "Coordn8"


def project_research(repo: str | Path) -> Path:
    return project_build(repo) / "_research"


def project_build_dir(repo: str | Path) -> Path:
    return project_build(repo) / "_build"


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def is_user_owned(path: str | Path) -> bool:
    """True for anything inside the user zone or inside any project's Coordn8/ folder."""
    p = Path(path).expanduser()
    if _within(p, user_home()):
        return True
    return any(part == "Coordn8" for part in p.resolve().parts[:-1]) and not _within(p, PKG_ROOT)


if __name__ == "__main__":
    print(json.dumps({"PKG_ROOT": str(PKG_ROOT), "USER_HOME": str(user_home()),
                      "CLAUDE_HOME": str(claude_home()),
                      "user_subdirs": USER_SUBDIRS}, indent=2))
