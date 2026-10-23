#!/usr/bin/env python3
"""Coordn8 hooks — shared helpers. Zero third-party deps; Python 3.9+.

Every Coordn8 hook obeys four rules, and this module is how they are kept:

  1. NEVER RAISE. A hook that crashes must exit 0 with no output, so the host carries on
     exactly as if the hook were not installed. `guarded(main)` wraps every entry point.
  2. NO NETWORK, AND FAST. The whole hook finishes in well under 2 s; every subprocess is
     bounded by a deadline and skipped (not waited on) when it would overrun.
  3. WRITE ONLY INSIDE <cwd>/Coordn8/_build/. Never into the user store (~/.coordn8 is
     read-only to hooks), never into the repo itself. `safe_build_path()` enforces it.
  4. ONE JSON OBJECT ON STDOUT, OR NOTHING. The host parses stdout as JSON only when it is
     a single object with nothing but whitespace around it, so output is built first and
     printed once at the end.

Stdin fields used (host hook input): session_id, transcript_path, cwd, hook_event_name,
stop_hook_active, last_assistant_message, agent_id, agent_type, tool_name, tool_input.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent
PKG_ROOT = HOOKS_DIR.parent
SCRIPTS = PKG_ROOT / "scripts"

# Hook-owned files inside _build/ — the stop gate ignores these when deciding whether a
# build ran this session (a hook writing its own log is not a build).
SESSION_DIR = ".coordn8_sessions"
AGENT_LOG = ("orchestration", "agents.jsonl")
HOOK_OWNED = {SESSION_DIR, "orchestration/agents.jsonl"}

T0 = time.monotonic()


def elapsed() -> float:
    return time.monotonic() - T0


def read_stdin(limit: int = 4_000_000) -> dict:
    """The hook input as a dict; {} on anything unreadable or malformed."""
    try:
        raw = sys.stdin.read(limit)
    except Exception:  # noqa: BLE001
        return {}
    try:
        data = json.loads(raw) if raw and raw.strip() else {}
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def s(value, default: str = "") -> str:
    """A field as a string, whatever type arrived."""
    if value is None:
        return default
    if isinstance(value, str):
        return value
    try:
        return str(value)
    except Exception:  # noqa: BLE001
        return default


def truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes")
    if isinstance(value, (int, float)):
        return value != 0
    return False


def project_dir(data: dict) -> Path:
    """The project the hook fired in: stdin cwd, else CLAUDE_PROJECT_DIR, else os.getcwd()."""
    for cand in (s(data.get("cwd")), os.environ.get("CLAUDE_PROJECT_DIR", ""), ""):
        try:
            p = Path(cand).expanduser() if cand else Path(os.getcwd())
            if p.is_dir():
                return p.resolve()
        except Exception:  # noqa: BLE001
            continue
    return Path(".").resolve()


def coordn8_dir(data: dict) -> Path:
    return project_dir(data) / "Coordn8"


def safe_build_path(data: dict, *parts: str) -> Path:
    """A path under <cwd>/Coordn8/_build/. Raises ValueError if it would escape it."""
    base = (coordn8_dir(data) / "_build").resolve()
    p = base.joinpath(*parts).resolve()
    p.relative_to(base)  # ValueError when outside
    return p


def safe_name(text: str, n: int = 80) -> str:
    t = re.sub(r"[^A-Za-z0-9._-]+", "_", s(text)).strip("._")
    return (t or "unknown")[:n]


def run_bounded(cmd: list, timeout: float, cwd=None):
    """(returncode, stdout, stderr) or None when it would not finish in `timeout` s."""
    if timeout <= 0.05:
        return None
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd,
                           stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout or "", p.stderr or ""
    except Exception:  # noqa: BLE001 - includes TimeoutExpired
        return None


def run_parallel(cmds: dict, timeout: float, cwd=None) -> dict:
    """Run several commands at once under ONE shared deadline.
    {name: (returncode, stdout, stderr) | None}; None = skipped (too slow or failed to start)."""
    procs, out = {}, {}
    for name, cmd in cmds.items():
        try:
            procs[name] = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           stdin=subprocess.DEVNULL, text=True, cwd=cwd)
        except Exception:  # noqa: BLE001
            out[name] = None
    deadline = time.monotonic() + max(0.0, timeout)
    for name, p in procs.items():
        try:
            so, se = p.communicate(timeout=max(0.01, deadline - time.monotonic()))
            out[name] = (p.returncode, so or "", se or "")
        except Exception:  # noqa: BLE001
            try:
                p.kill()
                p.communicate(timeout=0.2)
            except Exception:  # noqa: BLE001
                pass
            out[name] = None
    return out


def first_json(text: str):
    """The first JSON object printed on its own line (gate scripts print one per line)."""
    for line in s(text).splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                v = json.loads(line)
                if isinstance(v, dict):
                    return v
            except Exception:  # noqa: BLE001
                continue
    try:
        v = json.loads(s(text))
        return v if isinstance(v, dict) else None
    except Exception:  # noqa: BLE001
        return None


def clip(text: str, n: int) -> str:
    t = s(text)
    return t if len(t) <= n else t[: max(0, n - 3)].rstrip() + "..."


def emit(obj: dict) -> None:
    """Print exactly one JSON object (or nothing when obj is empty)."""
    if not obj:
        return
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def context(event: str, text: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}


def guarded(main) -> None:
    """Run `main() -> dict|None`; print its result; on ANY failure print nothing, exit 0."""
    out = None
    try:
        out = main()
    except BaseException:  # noqa: BLE001 - a hook must never crash its host
        out = None
    try:
        if out:
            emit(out)
    except BaseException:  # noqa: BLE001
        pass
    try:
        sys.stdout.flush()
    except BaseException:  # noqa: BLE001
        pass
    os._exit(0)
