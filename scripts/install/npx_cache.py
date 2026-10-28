#!/usr/bin/env python3
"""Coordn8 installer — targeted npx cache cleanup (the J1 force-update).

`npx -y github:ompandey98/coordn8 init` re-uses a cached copy under <npm cache>/_npx/<hash>/
when one exists, which is how users ended up running an old version. The v1.5.0 fix was
`npm cache clean --force`, which wipes the user's ENTIRE npm cache. This module removes
ONLY Coordn8's own cached copies and nothing else:

  * cache dir  = $npm_config_cache (or $NPM_CONFIG_CACHE), else `npm config get cache`
                 (5 s timeout, stdin closed), else ~/.npm
  * candidates = direct child directories of <cache>/_npx/ (real dirs, never symlinks)
  * removed iff  <entry>/node_modules/coordn8 exists, OR <entry>/package.json lists a
                 dependency named `coordn8` or one whose spec points at the coordn8 repo
  * everything else — other _npx entries, _cacache, _logs, index files — is never opened
    for writing, let alone deleted.

CLI:  npx_cache.py [--dry-run] [--json]      Zero third-party deps; Python 3.8+.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PKG_NAME = "coordn8"
REPO_SPEC_MARKERS = ("ompandey98/coordn8",)


def npm_cache_dir() -> tuple[Path, str]:
    for key in ("npm_config_cache", "NPM_CONFIG_CACHE"):
        v = os.environ.get(key)
        if v:
            return Path(v).expanduser(), f"${key}"
    npm = "npm.cmd" if os.name == "nt" else "npm"
    try:
        r = subprocess.run([npm, "config", "get", "cache"], capture_output=True, text=True,
                           timeout=5, stdin=subprocess.DEVNULL)
        out = (r.stdout or "").strip().splitlines()
        if r.returncode == 0 and out and out[-1].strip() and out[-1].strip() not in ("undefined", "null"):
            return Path(out[-1].strip()).expanduser(), "npm config get cache"
    except (OSError, subprocess.SubprocessError):
        pass
    return Path.home() / ".npm", "default ~/.npm"


def _is_dep_spec_ours(name: str, spec) -> bool:
    if name == PKG_NAME:
        return True
    s = str(spec or "").lower()
    return any(m in s for m in REPO_SPEC_MARKERS)


def is_coordn8_entry(entry: Path) -> tuple[bool, str]:
    """Decide from READ-ONLY inspection whether one _npx entry is a Coordn8 copy."""
    if (entry / "node_modules" / PKG_NAME).exists():
        return True, f"node_modules/{PKG_NAME} present"
    pj = entry / "package.json"
    if pj.is_file():
        try:
            data = json.loads(pj.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False, "package.json unreadable"
        if isinstance(data, dict):
            for field in ("dependencies", "devDependencies", "optionalDependencies"):
                deps = data.get(field)
                if isinstance(deps, dict):
                    for n, spec in deps.items():
                        if _is_dep_spec_ours(str(n), spec):
                            return True, f"package.json {field} lists {n}"
    return False, "not a Coordn8 entry"


def find_coordn8_entries(cache: Path) -> list[tuple[Path, str]]:
    npx = cache / "_npx"
    if not npx.is_dir() or npx.is_symlink():
        return []
    found = []
    root = npx.resolve()
    for entry in sorted(npx.iterdir()):
        if entry.is_symlink() or not entry.is_dir():
            continue
        try:
            if entry.resolve().parent != root:
                continue
        except OSError:
            continue
        ok, why = is_coordn8_entry(entry)
        if ok:
            found.append((entry, why))
    return found


def _entry_in_use(entry: Path) -> str | None:
    """Another process (other than this one and its parents) running from this cached
    copy? Then it must not be deleted under it. Best effort: uses `ps` on POSIX."""
    if os.name != "posix":
        return None
    try:
        r = subprocess.run(["ps", "-Ao", "pid=,ppid=,command="], capture_output=True, text=True,
                           timeout=5, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    mine, rows = set(), {}
    for ln in r.stdout.splitlines():
        parts = ln.strip().split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            rows[int(parts[0])] = (int(parts[1]), parts[2])
    pid = os.getpid()
    while pid in rows and pid not in mine:          # this process and its ancestors
        mine.add(pid)
        pid = rows[pid][0]
    needle = str(entry)
    for pid, (_ppid, cmd) in rows.items():
        if pid not in mine and needle in cmd:
            return f"pid {pid}"
    return None


def clean(dry_run: bool = False, cache: Path | None = None) -> dict:
    """Remove only Coordn8's _npx entries. Never raises."""
    try:
        if cache is None:
            cache, source = npm_cache_dir()
        else:
            source = "argument"
        entries = find_coordn8_entries(cache)
        removed, errors = [], []
        for entry, why in entries:
            if dry_run:
                removed.append({"path": str(entry), "why": why, "dry_run": True})
                continue
            busy = _entry_in_use(entry)
            if busy:
                removed.append({"path": str(entry), "why": why, "kept": f"in use by {busy}"})
                continue
            try:
                # Rename first (atomic), then delete: npx never sees a half-deleted copy,
                # so a run starting at the same moment re-fetches instead of crashing with
                # "Cannot find module .../_npx/<hash>/node_modules/.bin/coordn8".
                gone = entry.with_name(entry.name + f".coordn8-removing-{os.getpid()}")
                os.rename(str(entry), str(gone))
                shutil.rmtree(str(gone), ignore_errors=True)
                removed.append({"path": str(entry), "why": why})
            except OSError as e:
                errors.append({"path": str(entry), "error": str(e)})
        return {"ok": not errors, "cache": str(cache), "cache_source": source,
                "removed": removed, "errors": errors}
    except Exception as e:  # pragma: no cover - defensive: cleanup must never break install
        return {"ok": False, "cache": str(cache) if cache else None, "removed": [], "errors": [str(e)]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Remove ONLY Coordn8's cached copies from the npx cache")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    res = clean(dry_run=a.dry_run)
    if a.json:
        print(json.dumps(res, indent=1))
    else:
        verb = "would remove" if a.dry_run else "removed"
        print(f"[coordn8] npx cache {res['cache']}: {verb} {len(res['removed'])} Coordn8 entr"
              f"{'y' if len(res['removed']) == 1 else 'ies'}; nothing else touched")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
