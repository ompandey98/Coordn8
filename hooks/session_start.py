#!/usr/bin/env python3
"""Coordn8 SessionStart hook — recall memory into the session, and mark when it began.

1. Injects scripts/memory/recall.session_context(cwd, live_brief="") as additionalContext:
   the rule that the live brief outranks memory, the project's lessons and open items, and
   the owner's standing preferences/decisions. The WHOLE stdout is <= 4,096 bytes.
   Falls back to ~/.coordn8/memory/index.md (read-only) if recall cannot be imported.
   Prints nothing when there is neither a Coordn8 build here nor any stored memory.
2. Writes a session marker at <cwd>/Coordn8/_build/.coordn8_sessions/<session_id>.json
   (only when <cwd>/Coordn8 already exists; an existing marker is kept, so a resumed
   session keeps its original start). The Stop hook uses it to tell "this session".
Never writes to the user store; never raises.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _common as C
except BaseException:  # noqa: BLE001
    os._exit(0)

MAX_STDOUT = 4096
NO_MEMORY = "(no stored memory applies yet)"
PRUNE_AFTER_S = 30 * 86400
PRUNE_SCAN = 300


def write_marker(data: dict) -> None:
    if not C.coordn8_dir(data).is_dir():
        return
    sid = C.s(data.get("session_id"))
    if not sid:
        return
    d = C.safe_build_path(data, C.SESSION_DIR)
    d.mkdir(parents=True, exist_ok=True)
    marker = C.safe_build_path(data, C.SESSION_DIR, C.safe_name(sid) + ".json")
    if not marker.exists():
        tmp = marker.with_name(marker.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"session_id": sid, "start": time.time(),
                                   "source": C.s(data.get("source")),
                                   "transcript_path": C.s(data.get("transcript_path"))}) + "\n",
                       encoding="utf-8")
        os.replace(str(tmp), str(marker))
    # keep the marker folder small: drop markers older than 30 days
    now = time.time()
    try:
        for i, e in enumerate(os.scandir(d)):
            if i >= PRUNE_SCAN:
                break
            try:
                if e.is_file() and e.name.endswith(".json") and \
                        now - e.stat().st_mtime > PRUNE_AFTER_S and e.path != str(marker):
                    os.unlink(e.path)
            except OSError:
                continue
    except OSError:
        pass


def recall_text(data: dict, max_bytes: int) -> str:
    repo = C.project_dir(data)
    try:
        if str(C.SCRIPTS) not in sys.path:
            sys.path.insert(0, str(C.SCRIPTS))
        from memory.recall import session_context  # noqa: E402
        return session_context(repo, live_brief="", max_bytes=max_bytes)
    except Exception:  # noqa: BLE001 - fall back to the generated index, read-only
        home = os.environ.get("COORDN8_HOME")
        idx = (Path(home).expanduser() if home else Path.home() / ".coordn8") / "memory" / "index.md"
        try:
            text = idx.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return text.encode("utf-8")[:max_bytes].decode("utf-8", "ignore")


def fit(text: str) -> dict:
    """Wrap text as SessionStart additionalContext; trim whole lines until stdout <= 4 KB."""
    lines = text.rstrip("\n").split("\n")
    while lines:
        body = "\n".join(lines) + "\n"
        out = C.context("SessionStart", body)
        if len((json.dumps(out, ensure_ascii=False) + "\n").encode("utf-8")) <= MAX_STDOUT:
            return out
        lines.pop()
    return {}


def main():
    data = C.read_stdin()
    try:
        write_marker(data)
    except Exception:  # noqa: BLE001 - a marker failure must not stop the recall
        pass
    text = recall_text(data, MAX_STDOUT - 200)
    if not text.strip():
        return None
    if NO_MEMORY in text and not C.coordn8_dir(data).is_dir():
        return None  # nothing to say in a repo that has never used Coordn8
    return fit(text)


if __name__ == "__main__":
    C.guarded(main)
