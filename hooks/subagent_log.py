#!/usr/bin/env python3
"""Coordn8 SubagentStop hook — one line per finished subagent, so the fleet is auditable.

Appends {ts, agent_id, agent_type, last_assistant_message[:500]} as ONE JSON line to
<cwd>/Coordn8/_build/orchestration/agents.jsonl — only when <cwd>/Coordn8 exists (a repo
that never used Coordn8 gets nothing). Never blocks the subagent, prints nothing, never
raises. The line is written with a single O_APPEND write so parallel subagents finishing
at once cannot interleave half-lines.
"""
from __future__ import annotations

import json
import os
import sys
import time

try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _common as C
except BaseException:  # noqa: BLE001
    os._exit(0)

MSG_MAX = 500


def main():
    data = C.read_stdin()
    if not C.coordn8_dir(data).is_dir():
        return None
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "agent_id": C.s(data.get("agent_id")),
           "agent_type": C.s(data.get("agent_type")),
           "last_assistant_message": C.s(data.get("last_assistant_message"))[:MSG_MAX]}
    path = C.safe_build_path(data, *C.AGENT_LOG)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)
    return None


if __name__ == "__main__":
    C.guarded(main)
