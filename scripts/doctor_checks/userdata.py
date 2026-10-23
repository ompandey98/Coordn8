#!/usr/bin/env python3
"""Doctor section: the user-data zone (~/.coordn8 or COORDN8_HOME).

probe() -> {"summary": str, "lines": [str], "data": {...}}
Read-only by construction: it opens files for reading only, constructs the store with
create=False (no directory is made), and never calls a migration. It never raises.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

INDEX_CAP = 4096


def probe(home=None) -> dict:
    lines: list = []
    data: dict = {}
    try:
        from lib.paths import USER_SCHEMA_VERSION, user_home
        from memory import migrate
        from memory.store import TYPES, Store

        h = Path(home).expanduser() if home else user_home()
        data["home"] = str(h)
        lines.append(f"user-data zone: {h}" + ("" if h.exists() else "  (not created yet)"))
        if not h.exists():
            return {"summary": f"user data: {h} not created yet (first install creates it)",
                    "lines": lines, "data": data}

        st = migrate.status(h)
        data["schema"] = st
        ver = st.get("schema_version")
        pend = st.get("pending") or []
        lines.append(f"schema: {ver} ({st.get('schema_source')}); package expects {USER_SCHEMA_VERSION}")
        if st.get("error"):
            lines.append(f"schema problem: {st['error']}")
        for p in pend:
            lines.append(f"migration pending v{p['from']} -> v{p['to']}: " + "; ".join(p["reasons"])
                         + "  (run: python3 scripts/memory/migrate.py run — it backs up first)")

        s = Store(h, create=False)
        allrows = s.all_entries()
        current = s.list(include_superseded=False)
        by_type = {t: sum(1 for e in current if e.get("type") == t) for t in TYPES}
        data.update(entries_total=len(allrows), entries_current=len(current),
                    superseded=len(allrows) - len(current), by_type=by_type)
        lines.append(f"memory entries: {len(current)} current, {len(allrows) - len(current)} superseded "
                     "(" + ", ".join(f"{t} {n}" for t, n in by_type.items() if n) + ")"
                     if allrows else "memory entries: none yet")
        n_conf = len(s.conflicts())
        data["conflicts"] = n_conf
        if n_conf:
            lines.append(f"unresolved memory conflicts: {n_conf} (coordn8 memory conflicts)")
        ch = len(s.recent_choices(10 ** 9))
        inv = len(s.inventions())
        data.update(choices=ch, inventions=inv)
        lines.append(f"choice history: {ch} build(s); inventions logged: {inv}")
        if s.corrupt:
            data["corrupt_lines"] = len(s.corrupt)
            lines.append(f"corrupt lines skipped: {len(s.corrupt)} (first: "
                         f"{s.corrupt[0]['file']}:{s.corrupt[0]['line']})")
        idx = s.index_path
        if idx.exists():
            size = idx.stat().st_size
            data["index_bytes"] = size
            lines.append(f"memory index: {size} bytes (cap {INDEX_CAP})"
                         + ("  OVER CAP" if size > INDEX_CAP else ""))
        else:
            data["index_bytes"] = None
            lines.append("memory index: not generated yet")
        nb = len(migrate.list_backups(h))
        data["backups"] = nb
        lines.append(f"backups: {nb}")

        bits = [f"schema {ver}"]
        if pend:
            bits.append("MIGRATION PENDING")
        if st.get("error"):
            bits.append("SCHEMA PROBLEM")
        bits.append(f"{len(current)} memory entries")
        if n_conf:
            bits.append(f"{n_conf} conflict(s)")
        bits.append(f"{nb} backup(s)")
        return {"summary": "user data: " + ", ".join(bits), "lines": lines, "data": data}
    except Exception as e:  # doctor must never break
        return {"summary": f"user data: check could not complete ({type(e).__name__}: {e})",
                "lines": lines, "data": data}


if __name__ == "__main__":
    import json
    print(json.dumps(probe(), indent=1))
