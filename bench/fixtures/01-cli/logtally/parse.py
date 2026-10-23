"""Turn raw log lines into Entry records. Malformed lines are counted, never fatal."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

LINE = re.compile(r"^(?P<ts>\S+)\s+(?P<level>DEBUG|INFO|WARN|ERROR)\s+\[(?P<component>[\w.-]+)\]\s+(?P<msg>.*)$")


@dataclass(frozen=True)
class Entry:
    ts: datetime
    level: str
    component: str
    message: str


def parse_line(line: str):
    """Return an Entry, or None when the line does not match the log format."""
    m = LINE.match(line.rstrip("\n"))
    if not m:
        return None
    try:
        ts = datetime.fromisoformat(m.group("ts"))
    except ValueError:
        return None
    return Entry(ts, m.group("level").lower(), m.group("component"), m.group("msg"))


def parse_file(path: str):
    """Read one file. Returns (entries, skipped_count)."""
    entries, skipped = [], 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.strip():
                continue
            e = parse_line(line)
            if e is None:
                skipped += 1
            else:
                entries.append(e)
    return entries, skipped
