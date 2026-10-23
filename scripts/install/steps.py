#!/usr/bin/env python3
"""Coordn8 installer — the step runner.

Every install step after `package` is a separate script run as a subprocess, and every
one of them is bounded three ways, so an install can never get stuck:

  * TIME    a hard timeout per step (COORDN8_STEP_TIMEOUT overrides all of them,
            COORDN8_STEP_TIMEOUT_<NAME> overrides one); on expiry the step's whole
            process group is killed and the step is reported as timed out.
  * STDIN   a step never inherits a pipe or a closed stdin: it gets /dev/null. The one
            exception is the sign-in offer on a real terminal (and never with --yes),
            and that offer carries its own 20-second default-to-skip timeout.
  * FAILURE a failing step is recorded with its reason and the run moves on.

A step may report foreign edits it made (a JSON key in a host config, a text block in a
user file) by printing one line:   COORDN8_RECORD {"json_keys":[{"file":..,"key":..}],
"blocks":[{"file":..,"style":"html"}], "files":{"<abs path>":"<sha256>"}}
The installer stores those in installed.json so `uninstall` removes exactly them.
Zero third-party deps; Python 3.8+.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

RECORD_PREFIX = "COORDN8_RECORD "

# name, script (package-relative), args, default timeout (s), disabling flags
STEP_ORDER = ["package", "deps", "voices", "mcp", "refs", "signin"]
STEP_DEFS = {
    "deps": ("scripts/setup_env.py", [], 2400, ("no_deps",)),
    "voices": ("scripts/voice/voices.py", ["install"], 1800, ("no_voices",)),
    "mcp": ("scripts/mcp/install_mcp.py", ["install"], 600, ("no_mcp",)),
    "refs": ("scripts/refs/refresh.py", [], 180, ("no_refs",)),
    "signin": ("scripts/mcp/install_mcp.py", ["offer", "--timeout", "20"], 45, ("no_mcp", "no_signin")),
}
PACKAGE_TIMEOUT = 120  # adapter regeneration inside the package step

NONINTERACTIVE_FLAGS = ("--non-interactive", "--noninteractive", "--yes")


def step_timeout(name: str, default: float) -> float:
    for key in (f"COORDN8_STEP_TIMEOUT_{name.upper()}", "COORDN8_STEP_TIMEOUT"):
        v = os.environ.get(key)
        if v:
            try:
                f = float(v)
                if f > 0:
                    return f
            except ValueError:
                pass
    return float(default)


def forced_failures() -> set:
    raw = os.environ.get("COORDN8_FAIL_STEP", "")
    return {x.strip().lower() for x in raw.replace(";", ",").split(",") if x.strip()}


def noninteractive_flag(script: Path) -> str | None:
    """The non-interactive flag a script declares, if any (read from its source)."""
    try:
        src = script.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for flag in NONINTERACTIVE_FLAGS:
        if f'"{flag}"' in src or f"'{flag}'" in src:
            return flag
    return None


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass


def _summarise(lines: list[str]) -> str:
    """A one-line detail from a step's output: a JSON summary's guidance if it printed
    one, else its last meaningful line."""
    text = "\n".join(lines)
    start = text.rfind("\n{")
    for blob in ([text[start + 1:]] if start >= 0 else []) + ([text] if text.startswith("{") else []):
        try:
            obj = json.loads(blob)
        except ValueError:
            continue
        if isinstance(obj, dict):
            for k in ("summary", "guidance", "detail", "message", "status"):
                if isinstance(obj.get(k), str) and obj[k].strip():
                    return obj[k].strip()
    for ln in reversed(lines):
        s = ln.strip()
        if s and s not in ("{", "}", "]", "[") and not s.startswith(RECORD_PREFIX):
            return s
    return ""


def run_step(name: str, cmd: list[str], timeout: float, env: dict, interactive_stdin: bool,
             echo=None, cwd: str | None = None) -> dict:
    """Run one step. Never raises, never waits on stdin, never outlives `timeout`."""
    t0 = time.time()
    records: list[dict] = []
    tail: deque = deque(maxlen=4000)
    try:
        proc = subprocess.Popen(
            cmd, cwd=cwd, env=env,
            stdin=None if interactive_stdin else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=(os.name == "posix"))
    except OSError as e:
        return {"status": "failed", "detail": f"could not start: {e}", "rc": None,
                "seconds": round(time.time() - t0, 2), "records": []}

    def pump():
        assert proc.stdout is not None
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if line.startswith(RECORD_PREFIX):
                try:
                    rec = json.loads(line[len(RECORD_PREFIX):])
                    if isinstance(rec, dict):
                        records.append(rec)
                except ValueError:
                    pass
                continue
            tail.append(line)
            if echo:
                echo(f"  [{name}] {line}")

    th = threading.Thread(target=pump, daemon=True)
    th.start()
    timed_out = False
    try:
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_group(proc)
        try:
            rc = proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            rc = None
    th.join(timeout=3)
    secs = round(time.time() - t0, 2)
    lines = list(tail)
    if timed_out:
        return {"status": "timeout", "detail": f"timed out after {timeout:g}s and was stopped; "
                "the rest of the install continued", "rc": rc, "seconds": secs, "records": records}
    detail = _summarise(lines)
    if rc == 0:
        status = "ok"
    elif rc == 1:
        status = "partial"
    elif rc == 2:
        status = "skipped"
    else:
        status = "failed"
    if status != "ok" and not detail:
        detail = f"exit {rc}"
    return {"status": status, "detail": detail, "rc": rc, "seconds": secs, "records": records,
            "lines": lines}


def stdin_is_tty() -> bool:
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (ValueError, OSError):
        return False
