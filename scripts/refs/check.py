#!/usr/bin/env python3
"""
Coordn8 — run-time freshness check of the local reference copies. READ-ONLY.

Reads <user_home>/refs/index.json (written by refresh.py) against the configured
sources and reports, per reference:

    fresh     a good copy exists, its bytes match the index, and it is no older
              than the source's max_age_days
    expired   a good copy exists but is older than max_age_days      -> stale
    modified  the copy on disk no longer matches the fetched bytes    -> stale
    missing   no good copy (never fetched, or the file is gone)       -> stale

plus `refresh_failed` (the last refresh attempt did not succeed — the copy shown
is the last good one) and, for a vendored library's release record, whether the
upstream latest version differs from the version Coordn8 pins.

Every row carries the stamp the agent writes on anything it relied on:
"<id>: as of YYYY-MM-DD". When something is stale, re-fetch it (`coordn8 refs`,
or the host's own web fetch for that one public URL) before relying on it.

Exit code (advisory): 0 all fresh · 1 at least one stale · 3 the check itself
could not run. Never writes anything.

Usage:
    python3 scripts/refs/check.py [--json] [--only ID ...] [--max-age-days N]
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_refresh = _load("c8_refs_refresh", HERE / "refresh.py")


def pinned_versions() -> dict:
    """{vendored lib key: pinned version} from the vendoring script (no network)."""
    try:
        libs = _load("c8_fetch_visual_libs_ro", SCRIPTS / "render" / "fetch_visual_libs.py").LIBS
        return {k: v.get("version") for k, v in libs.items()}
    except Exception:  # noqa: BLE001
        return {}


def _vtuple(v: str):
    try:
        return tuple(int(x) for x in v.split("-")[0].split("."))
    except (ValueError, AttributeError):
        return None


def compare_versions(pinned: str | None, latest: str | None) -> str | None:
    if not pinned or not latest:
        return None
    if pinned == latest:
        return "current"
    a, b = _vtuple(pinned), _vtuple(latest)
    if a is not None and b is not None:
        return "upstream-newer" if b > a else "pinned-newer"
    return "differs"


def evaluate(sources_path=None, only=None, max_age_override: int | None = None,
             now: datetime | None = None) -> dict:
    """Pure read: compute freshness for every configured source."""
    now = now or datetime.now(timezone.utc)
    sources, default_age = _refresh.load_sources(sources_path)
    index = _refresh.read_index()
    pins = pinned_versions()
    rows = []
    for src in sources:
        sid = src.get("id")
        if only and sid not in only:
            continue
        why = _refresh.source_error(src)
        if why:
            rows.append({"id": sid, "status": "invalid", "stale": True, "error": why,
                         "stamp": f"{sid}: not usable ({why})"})
            continue
        max_age = max_age_override or _refresh.max_age_of(src, default_age)
        entry = index.get(sid) if isinstance(index.get(sid), dict) else {}
        path = _refresh.dest_of(src)
        fetched = _refresh.parse_iso(entry.get("fetched_at"))
        row = {"id": sid, "url": src["url"], "kind": src.get("kind"),
               "file": str(path), "max_age_days": max_age,
               "fetched_at": entry.get("fetched_at"),
               "refresh_failed": bool(entry.get("stale")),
               "last_error": entry.get("error")}
        if not path.exists() or not entry.get("has_copy", path.exists()) or fetched is None:
            row.update(status="missing", stale=True, age_days=None,
                       stamp=f"{sid}: never fetched — fetch {src['url']} before relying on it")
            rows.append(row)
            continue
        data = path.read_bytes()
        age = (now - fetched).total_seconds() / 86400.0
        row["age_days"] = round(age, 1)
        day = entry["fetched_at"][:10]
        if entry.get("sha256") and hashlib.sha256(data).hexdigest() != entry["sha256"]:
            row.update(status="modified", stale=True)
        elif age > max_age:
            row.update(status="expired", stale=True)
        else:
            row.update(status="fresh", stale=False)
        row["stamp"] = f"{sid}: as of {day}"
        lib = src.get("vendored_lib")
        if lib and src.get("format") == "json":
            try:
                latest = json.loads(data.decode("utf-8")).get("version")
            except Exception:  # noqa: BLE001
                latest = None
            row.update(vendored_lib=lib, pinned_version=pins.get(lib), latest_version=latest,
                       version_status=compare_versions(pins.get(lib), latest))
        rows.append(row)
    stale = [r["id"] for r in rows if r["stale"]]
    newer = [{"lib": r["vendored_lib"], "pinned": r["pinned_version"],
              "latest": r["latest_version"], "as_of": (r.get("fetched_at") or "")[:10]}
             for r in rows if r.get("version_status") == "upstream-newer"]
    return {
        "tool": "refs_check",
        "refs_dir": str(_refresh.refs_dir()),
        "checked_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "refs": rows,
        "fresh": [r["id"] for r in rows if not r["stale"]],
        "stale": stale,
        "refresh_failed": [r["id"] for r in rows if r.get("refresh_failed")],
        "upstream_newer": newer,
        "stamps": [r["stamp"] for r in rows],
        "advice": ("all references are within their max age" if not stale else
                   "re-fetch before relying on: " + ", ".join(stale)
                   + "  (run `coordn8 refs`, or fetch that one public URL directly)"),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Report which local references are stale.")
    ap.add_argument("--json", action="store_true", help="JSON only")
    ap.add_argument("--only", nargs="+", default=None, metavar="ID")
    ap.add_argument("--max-age-days", type=int, default=None,
                    help="override every source's max age")
    ap.add_argument("--sources", default=None, help="sources file (default data/refs_sources.json)")
    args = ap.parse_args(argv)
    try:
        rep = evaluate(args.sources, args.only, args.max_age_days)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[coordn8:refs-check] could not run: {type(e).__name__}: {e}\n")
        return 3
    print(json.dumps(rep, indent=2))
    if not args.json:
        for r in rep["refs"]:
            extra = ""
            if r.get("version_status") == "upstream-newer":
                extra = f"  (pinned {r['pinned_version']}, upstream {r['latest_version']})"
            if r.get("refresh_failed"):
                extra += "  [last refresh failed: showing the last good copy]"
            sys.stderr.write(f"  {r['status']:<8} {r['stamp']}{extra}\n")
        sys.stderr.write(f"[coordn8:refs-check] {rep['advice']}\n")
    return 1 if rep["stale"] else 0


if __name__ == "__main__":
    sys.exit(main())
