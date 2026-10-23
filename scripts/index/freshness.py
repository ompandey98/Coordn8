#!/usr/bin/env python3
"""Coordn8 — freshness of the project index (K3): is what the index says still true?

Every index entry (symbol, doc chunk, cross-reference, entry point, directory map) records
the sha256 of the file it was derived from. This module compares those hashes with the
files on disk — before anything is used.

    freshness.py <repo> [--json]      report changed / added / removed files and stale entries
    freshness.py check <repo>         exit 1 if anything is stale, 0 if fresh, 2 if no index

Library use:
    from index.freshness import stale, file_hash, hash_lookup, flag_entries
    stale(repo)                         -> {"fresh": bool, "changed": [...], "added": [...],
                                            "removed": [...], "stale_entries": {...}}
    file_hash(path)                     -> sha256 hex or None (same digest as memory.store)
    hash_lookup(repo)                   -> callable path -> current sha, for
                                           memory.store.Store.stale(hash_lookup(repo))
    flag_entries(repo, rows)            -> rows with stale / stale_reason set per row
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from index import file_hash, read_json, read_jsonl  # noqa: E402,F401  (file_hash re-exported)
from lib.paths import project_research  # noqa: E402

__all__ = ["stale", "file_hash", "hash_lookup", "flag_entries", "is_fresh"]


def _index(repo) -> Path:
    return project_research(repo) / "index"


def hash_lookup(repo) -> Callable[[str], Optional[str]]:
    """path -> current sha256 (None when missing). Relative paths resolve against the repo,
    absolute ones are used as given. Cached per call of the returned function's lifetime."""
    root = Path(repo).resolve()
    cache: Dict[str, Optional[str]] = {}

    def look(p: str) -> Optional[str]:
        if p not in cache:
            q = Path(p)
            cache[p] = file_hash(q if q.is_absolute() else root / q)
        return cache[p]

    return look


def flag_entries(repo, rows: Iterable[dict], look: Optional[Callable] = None) -> List[dict]:
    """Mark each row derived from a file (`path` + `sha`) as stale when that file's hash
    no longer matches. Rows are copied; `stale` is always set."""
    look = look or hash_lookup(repo)
    out = []
    for r in rows:
        row = dict(r)
        p, sha = row.get("path"), row.get("sha")
        if p and sha:
            now = look(p)
            row["stale"] = now != sha
            if row["stale"]:
                row["stale_reason"] = "source missing" if now is None else "source changed"
        else:
            row["stale"] = False
        out.append(row)
    return out


def stale(repo, rescan: bool = True) -> dict:
    """Compare the manifest with the files on disk. `rescan` also finds files ADDED since
    the build (needs a directory walk); without it only indexed files are re-hashed."""
    repo = Path(repo).resolve()
    idx = _index(repo)
    manifest = read_json(idx / "manifest.json")
    if not manifest:
        return {"fresh": False, "indexed": False, "changed": [], "added": [], "removed": [],
                "stale_entries": {}, "reason": "no index — run: coordn8 index <repo>"}
    files = manifest.get("files", {})
    look = hash_lookup(repo)
    changed, removed = [], []
    for p, meta in sorted(files.items()):
        now = look(p)
        if now is None:
            removed.append(p)
        elif now != meta.get("sha"):
            changed.append(p)
    added: List[str] = []
    if rescan:
        from index.build_index import walk_repo
        rels, _ = walk_repo(repo)
        added = sorted(p for p in rels if p not in files)
    bad = set(changed) | set(removed)
    counts: Dict[str, Dict[str, int]] = {}
    if bad:
        for name in ("symbols", "chunks", "xref"):
            for r in read_jsonl(idx / f"{name}.jsonl"):
                if r.get("path") in bad:
                    counts.setdefault(r["path"], {}).setdefault(name, 0)
                    counts[r["path"]][name] += 1
        for e in (read_json(idx / "entrypoints.json", {}) or {}).get("entrypoints", []):
            if e.get("path") in bad:
                counts.setdefault(e["path"], {}).setdefault("entrypoints", 0)
                counts[e["path"]]["entrypoints"] += 1
    state = (read_json(idx / "maps" / "_state.json", {}) or {}).get("dirs", {})
    stale_maps = []
    for d, e in sorted(state.items()):
        for name, sha in e.get("files", {}).items():
            rel = name if d == "." else f"{d}/{name}"
            if rel in bad:
                stale_maps.append(d)
                break
    for p in added:
        d = str(Path(p).parent.as_posix())
        d = "." if d in ("", ".") else d
        if d in state and d not in stale_maps:
            stale_maps.append(d)
    return {"fresh": not (changed or removed or added), "indexed": True,
            "built_at": manifest.get("built_at"), "changed": changed, "added": added,
            "removed": removed, "stale_entries": counts, "stale_maps": sorted(stale_maps)}


def is_fresh(repo) -> bool:
    return bool(stale(repo).get("fresh"))


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    check = bool(argv) and argv[0] == "check"
    if check:
        argv = argv[1:]
    ap = argparse.ArgumentParser(description="Freshness of the Coordn8 project index (K3).")
    ap.add_argument("repo", nargs="?", default=".")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-rescan", action="store_true", help="do not look for newly added files")
    a = ap.parse_args(argv)
    res = stale(a.repo, rescan=not a.no_rescan)
    if a.json or check:
        print(json.dumps(res, indent=1, sort_keys=True))
    else:
        if not res["indexed"]:
            print(f"[freshness] {res['reason']}")
        elif res["fresh"]:
            print("[freshness] index is fresh")
        else:
            print(f"[freshness] STALE: {len(res['changed'])} changed, {len(res['added'])} added, "
                  f"{len(res['removed'])} removed; maps affected: {', '.join(res['stale_maps']) or 'none'}")
            for p in res["changed"] + res["removed"]:
                print(f"  {p}: {res['stale_entries'].get(p, {})}")
            print("  refresh: coordn8 index <repo> --incremental")
    if not res["indexed"]:
        return 2
    return 0 if res["fresh"] else 1


if __name__ == "__main__":
    sys.exit(main())
