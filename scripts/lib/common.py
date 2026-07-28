#!/usr/bin/env python3
"""Shared helpers for Coordn8 gate + render scripts. Zero third-party deps."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

# ---- advisory result protocol ---------------------------------------------
# Gates are ADVISORY. Exit codes communicate a signal to the agent; they never
# override the agent's own project-fitted judgment.
#   0  PASS      — nothing to review
#   1  REVIEW    — findings the agent should look at (NOT a hard failure)
#   2  SKIPPED   — could not run here (missing tool / missing input); degrade honestly
#   3  ERROR     — the gate itself broke (bug in input or script)
EXIT_PASS = 0
EXIT_REVIEW = 1
EXIT_SKIPPED = 2
EXIT_ERROR = 3


def emit(result: dict) -> None:
    """Print a machine-readable result line the agent can parse, plus a human line."""
    print(json.dumps(result, ensure_ascii=False))
    status = result.get("status", "?")
    gate = result.get("gate", "gate")
    msg = result.get("summary", "")
    sys.stderr.write(f"[coordn8:{gate}] {status}: {msg}\n")


def have(tool: str) -> bool:
    return shutil.which(tool) is not None


def find_chrome() -> str | None:
    """Locate a headless-capable Chrome/Chromium across platforms."""
    for name in ("google-chrome", "google-chrome-stable", "chromium",
                 "chromium-browser", "chrome"):
        p = shutil.which(name)
        if p:
            return p
    mac = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
           "/Applications/Chromium.app/Contents/MacOS/Chromium")
    for p in mac:
        if Path(p).exists():
            return p
    return None


def load_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
