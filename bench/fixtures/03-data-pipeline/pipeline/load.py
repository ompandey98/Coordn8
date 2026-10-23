"""Write the clean rows and the quarantined rows as JSON Lines, one file per date."""
from __future__ import annotations

import json
from pathlib import Path


def write_jsonl(rows, out_dir: str, date: str, name: str) -> int:
    path = Path(out_dir) / f"{date}-{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
            n += 1
    tmp.replace(path)
    return n
