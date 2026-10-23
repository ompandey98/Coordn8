#!/usr/bin/env python3
"""
Coordn8 — refresh the dated local copies of public reference sources.

Every source in data/refs_sources.json (the Claude Code docs index, the npm
release record of each vendored library, …) is fetched into

    <user_home>/refs/<id>.md | <id>.json        the last GOOD copy
    <user_home>/refs/index.json                  {id: {url, fetched_at, bytes, sha256,
                                                       ok, error, stale, has_copy, …}}

(<user_home> is ~/.coordn8, or $COORDN8_HOME). At run time `check.py` reads the
index, lists anything missing or older than its max age, and prints the
"as of <date>" stamp the agent puts on anything it relied on.

Rules this script keeps, every run:
  - Writes ONLY under <user_home>/refs/ (it creates that directory if missing and
    nothing else). Every write is atomic (temp file + rename).
  - NEVER deletes or overwrites a good copy with a bad response: a failed fetch,
    an HTTP error, an HTML page where text was expected, or JSON that does not
    parse all keep the previous copy byte-for-byte and mark its entry stale with
    the error. Offline (--offline or COORDN8_OFFLINE=1) makes no request at all
    and marks every selected entry stale, keeping the copies.
  - Only public pages: https (or file://, or plain http to a loopback address for
    local testing). A redirect to anything else is refused. Requests are plain
    GETs; nothing is ever sent to any hosted AI service.
  - Every request has a timeout and a size cap; fetches run in a small bounded
    pool; the exit code is 0 even when some fetches fail (they are reported).

Usage:
    python3 scripts/refs/refresh.py                  # refresh every source
    python3 scripts/refs/refresh.py --only npm-d3    # just one (or several) ids
    python3 scripts/refs/refresh.py --offline        # no network: keep copies, mark stale
    python3 scripts/refs/refresh.py check            # same as scripts/refs/check.py
    python3 scripts/refs/refresh.py list             # the configured sources
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
from lib.paths import PKG_ROOT, user_home  # noqa: E402

DEFAULT_SOURCES = PKG_ROOT / "data" / "refs_sources.json"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
EXT = {"md": ".md", "json": ".json"}
MAX_BYTES = 8 * 1024 * 1024
DEFAULT_TIMEOUT = 20.0
DEFAULT_MAX_AGE = 14
USER_AGENT = "coordn8-refs/1 (public documentation fetch)"


def log(msg: str) -> None:
    sys.stderr.write(f"[coordn8:refs] {msg}\n")
    sys.stderr.flush()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s) -> datetime | None:
    if not isinstance(s, str) or not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def refs_dir() -> Path:
    return user_home() / "refs"


def index_path() -> Path:
    return refs_dir() / "index.json"


# --- sources -----------------------------------------------------------------

def load_sources(path: Path | None = None) -> tuple[list[dict], int]:
    """(sources, default_max_age_days). Raises ValueError on a malformed file."""
    p = Path(path) if path else DEFAULT_SOURCES
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("sources"), list):
        raise ValueError(f"{p}: expected an object with a 'sources' list")
    default_age = data.get("default_max_age_days", DEFAULT_MAX_AGE)
    if not isinstance(default_age, int) or default_age <= 0:
        default_age = DEFAULT_MAX_AGE
    return [s for s in data["sources"] if isinstance(s, dict)], default_age


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def url_error(url) -> str | None:
    """None if this URL may be fetched, else the reason it is refused."""
    if not isinstance(url, str) or not url:
        return "missing url"
    u = urllib.parse.urlparse(url)
    if u.scheme == "https" and u.netloc:
        return None
    if u.scheme == "file":
        return None
    if u.scheme == "http" and _is_loopback(u.hostname):
        return None
    return (f"refused url scheme/host '{u.scheme}://{u.hostname or ''}': only https, "
            "file://, or http to a loopback address are allowed")


def source_error(src: dict) -> str | None:
    sid = src.get("id")
    if not isinstance(sid, str) or not ID_RE.match(sid):
        return (f"refused id {sid!r}: ids must match {ID_RE.pattern} "
                "(no path separators, no '..')")
    if src.get("format") not in EXT:
        return f"refused format {src.get('format')!r}: one of {sorted(EXT)}"
    age = src.get("max_age_days")
    if age is not None and (not isinstance(age, int) or age <= 0):
        return f"refused max_age_days {age!r}: a positive integer"
    return url_error(src.get("url"))


def max_age_of(src: dict, default_age: int) -> int:
    a = src.get("max_age_days")
    return a if isinstance(a, int) and a > 0 else default_age


def dest_of(src: dict) -> Path:
    return refs_dir() / f"{src['id']}{EXT[src['format']]}"


# --- index ---------------------------------------------------------------------

def read_index() -> dict:
    p = index_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{time.monotonic_ns()}")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def write_index(entries: dict) -> None:
    p = index_path()
    if p.exists():
        try:
            json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — keep the unreadable one for inspection
            os.replace(p, p.with_name(f"index.json.unreadable-{int(time.time())}"))
    _atomic_write(p, (json.dumps(entries, indent=2, sort_keys=True) + "\n").encode("utf-8"))


# --- fetching ----------------------------------------------------------------

class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        why = url_error(newurl)
        if why:
            raise urllib.error.URLError(f"redirect to {newurl} {why}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_SafeRedirect)


def fetch_bytes(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "text/markdown, text/plain, "
                                                         "application/json;q=0.9, */*;q=0.1"})
    with _OPENER.open(req, timeout=timeout) as r:  # noqa: S310 — scheme checked above
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError(f"response larger than {MAX_BYTES} bytes")
    return data


def validate_payload(fmt: str, data: bytes) -> tuple[bool, str | None, dict]:
    """Is this response a usable copy? Never let an error page replace a good copy."""
    if not data:
        return False, "empty response", {}
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False, "response is not UTF-8 text", {}
    if fmt == "json":
        try:
            obj = json.loads(text)
        except ValueError as e:
            return False, f"response is not valid JSON ({e})", {}
        if not isinstance(obj, (dict, list)):
            return False, "JSON response is not an object or array", {}
        meta = {}
        if isinstance(obj, dict) and isinstance(obj.get("version"), str):
            meta["version"] = obj["version"]
        return True, None, meta
    head = text.lstrip()[:200].lower()
    if head.startswith("<!doctype html") or head.startswith("<html"):
        return False, "got an HTML page where markdown/text was expected", {}
    return True, None, {}


def _file_iso(p: Path) -> str:
    return datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def refresh_one(src: dict, prev: dict | None, offline: bool, timeout: float,
                default_age: int) -> tuple[dict, str]:
    """Returns (index entry, outcome) — outcome in fetched | kept-stale | failed."""
    dest = dest_of(src)
    attempt = now_iso()
    base = {"url": src["url"], "kind": src.get("kind"), "format": src["format"],
            "file": dest.name, "max_age_days": max_age_of(src, default_age)}
    have_copy = dest.exists() and dest.stat().st_size > 0

    def keep(error: str) -> tuple[dict, str]:
        e = dict(prev or {})
        e.update(base)
        if have_copy and not (prev or {}).get("fetched_at"):
            data = dest.read_bytes()   # a copy with no index entry: describe it honestly
            e.update(fetched_at=_file_iso(dest), fetched_at_source="file-mtime",
                     bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        if not have_copy:
            e.update(fetched_at=None, bytes=0, sha256=None)
        e.update(ok=False, error=error, stale=True, has_copy=have_copy,
                 last_attempt=attempt)
        return e, ("kept-stale" if have_copy else "failed")

    if offline:
        return keep("offline: not refreshed (last good copy kept)")
    try:
        data = fetch_bytes(src["url"], timeout)
    except urllib.error.HTTPError as e:
        return keep(f"HTTP {e.code} {e.reason}")
    except (urllib.error.URLError, ValueError, OSError) as e:
        return keep(f"{type(e).__name__}: {getattr(e, 'reason', e)}")
    ok, why, meta = validate_payload(src["format"], data)
    if not ok:
        return keep(f"rejected response: {why}")
    digest = hashlib.sha256(data).hexdigest()
    refs_dir().mkdir(parents=True, exist_ok=True)
    _atomic_write(dest, data)
    entry = dict(base)
    entry.update(fetched_at=attempt, bytes=len(data), sha256=digest, ok=True, error=None,
                 stale=False, has_copy=True, last_attempt=attempt,
                 changed=digest != (prev or {}).get("sha256"))
    if meta.get("version"):
        entry["version"] = meta["version"]
    return entry, "fetched"


def refresh(sources: list[dict], default_age: int, only: list[str] | None = None,
            offline: bool = False, timeout: float = DEFAULT_TIMEOUT) -> dict:
    rdir = refs_dir()
    rdir.mkdir(parents=True, exist_ok=True)
    selected = [s for s in sources if not only or s.get("id") in only]
    refused, valid = [], []
    for s in selected:
        why = source_error(s)
        (refused if why else valid).append({"id": s.get("id"), "error": why} if why else s)
    index = read_index()
    jobs = [(s, index.get(s["id"]) if isinstance(index.get(s["id"]), dict) else None)
            for s in valid]
    results: list[tuple[dict, dict, str]] = []
    if jobs:
        with ThreadPoolExecutor(max_workers=min(4, len(jobs))) as pool:
            futs = [(s, pool.submit(refresh_one, s, prev, offline, timeout, default_age))
                    for s, prev in jobs]
            for s, f in futs:
                entry, outcome = f.result()
                results.append((s, entry, outcome))
    for s, entry, _ in results:
        index[s["id"]] = entry
    if results:
        write_index(index)
    return {
        "tool": "refs_refresh",
        "refs_dir": str(rdir),
        "index": str(index_path()),
        "offline": offline,
        "fetched": [s["id"] for s, _, o in results if o == "fetched"],
        "kept_stale": [s["id"] for s, _, o in results if o == "kept-stale"],
        "failed": [{"id": s["id"], "error": e.get("error"), "kept_copy": e.get("has_copy")}
                   for s, e, o in results if o != "fetched"],
        "refused": refused,
        "as_of": {s["id"]: (e.get("fetched_at") or "")[:10] or None for s, e, _ in results},
    }


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "check":
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "c8_refs_check", Path(__file__).resolve().parent / "check.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.main(argv[1:])
    ap = argparse.ArgumentParser(description="Refresh dated local copies of public references.")
    ap.add_argument("action", nargs="?", choices=["refresh", "list"], default="refresh")
    ap.add_argument("--only", nargs="+", default=None, metavar="ID",
                    help="refresh only these source ids")
    ap.add_argument("--offline", action="store_true",
                    help="no network: keep last good copies, mark them stale "
                         "(also COORDN8_OFFLINE=1)")
    ap.add_argument("--sources", default=None, help="sources file (default data/refs_sources.json)")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                    help=f"per-request timeout in seconds (default {DEFAULT_TIMEOUT:g})")
    ap.add_argument("--json", action="store_true", help="JSON only")
    ap.add_argument("--non-interactive", action="store_true",
                    help="accepted for the installer; this script never prompts")
    args = ap.parse_args(argv)
    try:
        sources, default_age = load_sources(args.sources)
    except Exception as e:  # noqa: BLE001
        log(f"cannot read sources: {type(e).__name__}: {e}")
        return 3
    if args.action == "list":
        print(json.dumps({"tool": "refs_sources", "default_max_age_days": default_age,
                          "sources": sources}, indent=2))
        return 0
    if args.only:
        unknown = sorted(set(args.only) - {s.get("id") for s in sources})
        if unknown:
            log(f"unknown source id(s) {unknown}; known: {[s.get('id') for s in sources]}")
            return 2
    offline = args.offline or os.environ.get("COORDN8_OFFLINE", "") not in ("", "0")
    summary = refresh(sources, default_age, args.only, offline, args.timeout)
    print(json.dumps(summary, indent=2))
    if not args.json:
        log(f"{len(summary['fetched'])} refreshed, {len(summary['kept_stale'])} kept "
            f"(stale), {len(summary['failed']) - len(summary['kept_stale'])} missing, "
            f"{len(summary['refused'])} refused -> {summary['refs_dir']}")
        for f in summary["failed"]:
            log(f"  not refreshed {f['id']}: {f['error']}"
                + ("  (last good copy kept)" if f["kept_copy"] else ""))
    # Advisory: a failed fetch never fails the install.
    return 0


if __name__ == "__main__":
    sys.exit(main())
