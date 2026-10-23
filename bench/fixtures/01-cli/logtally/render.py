"""Two renderers for the same counts: an aligned text table and JSON."""
from __future__ import annotations

import json


def render_table(counts, key: str = "level") -> str:
    width = max([len(key)] + [len(v) for v, _ in counts])
    lines = [f"{key.ljust(width)}  count"]
    lines += [f"{v.ljust(width)}  {n:>5}" for v, n in counts]
    lines.append(f"{'total'.ljust(width)}  {sum(n for _, n in counts):>5}")
    return "\n".join(lines) + "\n"


def render_json(counts) -> str:
    return json.dumps({"counts": dict(counts), "total": sum(n for _, n in counts)}, indent=2) + "\n"
