#!/usr/bin/env python3
"""Coordn8 user-data schema migrations (J6). Back up first, always.

`<user_home>/schema.json` records the shape of the user zone:
    {"schema": N, "created": ISO, "migrated": [{"from", "to", "date", "backup", ...}]}

CURRENT schema = lib.paths.USER_SCHEMA_VERSION (2).

Schema 1 -> 2 (the one real migration):
  * schema 1 kept preferences as a flat JSON list of strings in memory/preferences.json, and
    memory/entries.jsonl lines could lack `scope` / `confidence` (and other later fields).
  * schema 2 is the typed store (memory/store.py): every preference is an entry in
    entries.jsonl with type, scope, confidence, source, source_quote, date, supersedes,
    source_hash, source_path.
  The migration:
    1. BACKS UP the whole user home (except backups/ and voice model binaries) into
       backups/<timestamp>-pre-migrate-v1-to-v2/ with a sha256 manifest.
    2. adds missing fields to existing entries (scope "global", confidence "med", ...), listing
       what it added in each entry's "migrated_fields"; lines that need nothing keep their
       exact bytes; corrupt lines are kept verbatim (never dropped);
    3. appends one typed entry per legacy string (type preference, scope global, confidence med,
       source "migrate", source_quote = the original string) with a deterministic id, so a
       string is never imported twice;
    4. records the step in schema.json["migrated"] (with the legacy file's sha256).
  memory/preferences.json itself is LEFT IN PLACE (user data is never deleted).

Pending detection does not trust the version number alone: a schema-2 stamp with an
un-imported legacy preferences.json (or entries still missing fields) is still pending, so a
home stamped "2" by a fresh install before migration ran cannot silently skip its data.

Idempotent: when nothing is pending, `run` writes nothing and takes no backup.

Restore: `restore <backup-dir>` first takes a safety backup of the CURRENT state
(backups/<ts>-pre-restore/), then puts every backed-up file back byte-for-byte and removes
files that did not exist at backup time (they are in the safety backup). backups/ and voice
model binaries are never touched. Every restored file is re-hashed against the manifest.

CLI:
    migrate.py status            [--home DIR] [--json]
    migrate.py run               [--home DIR] [--json]
    migrate.py restore <backup>  [--home DIR] [--json]    (<backup> = path or name under backups/)
Zero third-party deps; Python 3.9+.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional, Union

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from lib.paths import USER_SCHEMA_VERSION, user_home  # noqa: E402
from memory.store import now_iso  # noqa: E402

CURRENT = USER_SCHEMA_VERSION
LEGACY_PREFS = "memory/preferences.json"
ENTRIES = "memory/entries.jsonl"
MANIFEST = "manifest.json"
DATA = "data"
# fields a schema-2 entry always carries, with the default used when a schema-1 line lacks one
ENTRY_DEFAULTS = (("scope", "global"), ("confidence", "med"), ("source", "migrate"),
                  ("source_quote", None), ("supersedes", None), ("source_hash", None),
                  ("source_path", None))
VOICE_BINARY_SUFFIXES = {".onnx", ".bin", ".pt", ".pth", ".safetensors", ".gguf", ".ckpt",
                         ".tar", ".gz", ".tgz", ".zip", ".npz", ".npy", ".wav"}
VOICE_SMALL_MAX = 1 << 20  # voice-dir files under 1 MiB (configs, lists) ARE backed up


def _home(home) -> Path:
    return Path(home).expanduser() if home else user_home()


def _sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _sha_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".coordn8-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        if path.exists():
            shutil.copymode(str(path), tmp)
        os.replace(tmp, str(path))
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# ------------------------------------------------------------- what to back up
def _excluded(rel: Path, p: Path) -> bool:
    parts = rel.parts
    if not parts or parts[0] == "backups":
        return True
    if parts[0] == "voices":
        try:
            return p.suffix.lower() in VOICE_BINARY_SUFFIXES or p.stat().st_size > VOICE_SMALL_MAX
        except OSError:
            return True
    return False


def user_files(home) -> list:
    """Relative paths of every user file a backup covers (sorted)."""
    h = _home(home)
    out = []
    if not h.exists():
        return out
    for p in sorted(h.rglob("*")):
        if p.is_file() and not p.is_symlink():
            rel = p.relative_to(h)
            if not _excluded(rel, p):
                out.append(rel)
    return out


def user_dirs(home) -> list:
    """Relative paths of every directory in the zone except backups/ and voices/ (sorted)."""
    h = _home(home)
    if not h.exists():
        return []
    out = []
    for p in sorted(h.rglob("*")):
        if p.is_dir() and not p.is_symlink():
            rel = p.relative_to(h)
            if rel.parts[0] not in ("backups", "voices"):
                out.append(rel)
    return out


def snapshot(home) -> dict:
    """{relative path: sha256} for every backed-up-class file. Used by tests and restore."""
    h = _home(home)
    return {str(r): _sha_file(h / r) for r in user_files(h)}


def backup_home(home, label: str) -> Path:
    h = _home(home)
    root = h / "backups"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = root / f"{stamp}-{label}"
    n = 1
    while dest.exists():
        dest = root / f"{stamp}-{label}-{n}"
        n += 1
    (dest / DATA).mkdir(parents=True)
    files = {}
    for rel in user_files(h):
        src = h / rel
        tgt = dest / DATA / rel
        tgt.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(tgt))
        files[str(rel)] = {"sha256": _sha_file(tgt), "bytes": tgt.stat().st_size}
    manifest = {"kind": "coordn8-user-backup", "label": label, "created": now_iso(),
                "home": str(h), "excludes": ["backups/", "voices/ model binaries"],
                "files": files, "dirs": [str(d) for d in user_dirs(h)]}
    (dest / MANIFEST).write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return dest


def list_backups(home) -> list:
    root = _home(home) / "backups"
    if not root.is_dir():
        return []
    return sorted(str(p) for p in root.iterdir() if p.is_dir())


# ------------------------------------------------------------------- schema io
def read_schema(home) -> dict:
    """{"version", "data", "source": file|inferred, "error"} — never raises, never writes."""
    h = _home(home)
    f = h / "schema.json"
    if f.exists():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            v = int(data.get("schema"))
            return {"version": v, "data": data, "source": "file", "error": None}
        except (ValueError, TypeError, AttributeError, OSError) as e:
            return {"version": None, "data": None, "source": "file",
                    "error": f"schema.json unreadable: {e}"}
    legacy = (h / LEGACY_PREFS).exists()
    return {"version": 1 if legacy else CURRENT, "data": None, "source": "inferred", "error": None}


def _entry_lines(home) -> list:
    p = _home(home) / ENTRIES
    if not p.exists():
        return []
    return p.read_bytes().split(b"\n")


def _missing_fields(obj: dict) -> list:
    return [k for k, _ in ENTRY_DEFAULTS if k not in obj] + [
        k for k in ("id", "type", "date") if k not in obj]


def _legacy_prefs(home):
    """(list_of_items | None, sha256 | None, error | None)."""
    p = _home(home) / LEGACY_PREFS
    if not p.exists():
        return None, None, None
    raw = p.read_bytes()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        return None, _sha_bytes(raw), f"{LEGACY_PREFS} is not valid JSON: {e}"
    if isinstance(data, dict) and isinstance(data.get("preferences"), list):
        data = data["preferences"]
    if not isinstance(data, list):
        return None, _sha_bytes(raw), f"{LEGACY_PREFS} is not a list"
    return data, _sha_bytes(raw), None


def _imported_shas(schema_data) -> set:
    out = set()
    for m in (schema_data or {}).get("migrated", []) or []:
        if isinstance(m, dict) and m.get("legacy_sha256"):
            out.add(m["legacy_sha256"])
    return out


def _legacy_id(s: str) -> str:
    return "m-" + hashlib.sha1(("migrate-v1:" + s).encode("utf-8")).hexdigest()[:12]


def _needs_1_to_2(home, schema) -> list:
    reasons = []
    if (schema.get("version") or 0) < 2:
        reasons.append(f"schema.json says {schema.get('version')}")
    items, sha, err = _legacy_prefs(home)
    if sha and sha not in _imported_shas(schema.get("data")):
        reasons.append(f"{LEGACY_PREFS} not yet imported" + (f" ({err})" if err else ""))
    n_missing = 0
    for line in _entry_lines(home):
        if not line.strip():
            continue
        try:
            obj = json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(obj, dict) and _missing_fields(obj):
            n_missing += 1
    if n_missing:
        reasons.append(f"{n_missing} entr{'y' if n_missing == 1 else 'ies'} missing schema-2 fields")
    return reasons


def status(home=None) -> dict:
    """Read-only. Never raises for a missing or odd home."""
    h = _home(home)
    s = read_schema(h)
    out = {"home": str(h), "exists": h.exists(), "schema_version": s["version"],
           "schema_source": s["source"], "current": CURRENT, "pending": [], "error": s["error"],
           "backups": len(list_backups(h)) if h.exists() else 0}
    if s["error"]:
        return out
    if s["version"] is not None and s["version"] > CURRENT:
        out["error"] = (f"user data is schema {s['version']}, newer than this package "
                        f"understands ({CURRENT}); update Coordn8 — nothing will be migrated")
        return out
    reasons = _needs_1_to_2(h, s) if h.exists() else []
    if reasons:
        out["pending"].append({"from": 1, "to": 2, "reasons": reasons})
    return out


# ----------------------------------------------------------------- 1 -> 2 step
def _migrate_1_to_2(h: Path, schema: dict, backup_dir: Path) -> dict:
    stamp = now_iso()
    fields_added = 0
    lines = _entry_lines(h)
    new_lines = []
    known_ids = set()
    changed = False
    for line in lines:
        if not line.strip():
            new_lines.append(line)
            continue
        try:
            obj = json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            obj = None
        if not isinstance(obj, dict):
            new_lines.append(line)  # corrupt: keep verbatim, never drop user bytes
            continue
        missing = _missing_fields(obj)
        if missing:
            for k, v in ENTRY_DEFAULTS:
                obj.setdefault(k, v)
            obj.setdefault("id", "m-" + hashlib.sha1(line).hexdigest()[:12])
            obj.setdefault("date", stamp)
            obj.setdefault("type", "fact")
            obj["migrated_fields"] = sorted(set(obj.get("migrated_fields") or []) | set(missing))
            fields_added += len(missing)
            line = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            changed = True
        known_ids.add(obj.get("id"))
        new_lines.append(line)

    items, sha, err = _legacy_prefs(h)
    imported, skipped = 0, []
    appended = []
    if items is not None and sha not in _imported_shas(schema.get("data")):
        for it in items:
            if isinstance(it, dict) and isinstance(it.get("text"), str):
                it = it["text"]
            if not isinstance(it, str) or not it.strip():
                skipped.append(repr(it)[:80])
                continue
            eid = _legacy_id(it)
            if eid in known_ids:
                continue
            known_ids.add(eid)
            appended.append(json.dumps({
                "id": eid, "type": "preference", "scope": "global", "text": it.strip(),
                "source_quote": it, "source": "migrate", "date": stamp, "confidence": "med",
                "supersedes": None, "source_hash": None, "source_path": None,
                "migrated_from": LEGACY_PREFS}, ensure_ascii=False).encode("utf-8"))
            imported += 1

    if changed or appended:
        body = list(new_lines)
        while body and not body[-1].strip():
            body.pop()
        data = b"\n".join(body + appended)
        if data:
            data += b"\n"
        _atomic_write_bytes(h / ENTRIES, data)
    return {"from": 1, "to": 2, "date": stamp, "backup": backup_dir.name, "imported": imported,
            "fields_added": fields_added, "skipped_legacy_items": skipped,
            "legacy_sha256": sha, "legacy_error": err}


def run(home=None) -> dict:
    """Apply every pending step. Backs up first. No-op (no write, no backup) when nothing is pending."""
    h = _home(home)
    st = status(h)
    if st["error"]:
        return {"ok": False, "changed": False, "error": st["error"], "status": st}
    schema = read_schema(h)
    if not st["pending"]:
        created = False
        if h.exists() and not (h / "schema.json").exists():  # create a missing file only
            _atomic_write_bytes(h / "schema.json", (json.dumps(
                {"schema": CURRENT, "created": now_iso(), "migrated": []}, indent=2) + "\n").encode())
            created = True
        return {"ok": True, "changed": created, "steps": [], "backup": None,
                "note": "nothing to migrate" + ("; schema.json created" if created else "")}
    first = st["pending"][0]
    bdir = backup_home(h, f"pre-migrate-v{first['from']}-to-v{st['pending'][-1]['to']}")
    steps = []
    for step in st["pending"]:
        if (step["from"], step["to"]) == (1, 2):
            steps.append(_migrate_1_to_2(h, schema, bdir))
    data = dict(schema.get("data") or {})
    data.setdefault("created", now_iso())
    data["migrated"] = list(data.get("migrated") or []) + steps
    data["schema"] = CURRENT
    ordered = {"schema": data.pop("schema"), "created": data.pop("created"),
               "migrated": data.pop("migrated")}
    ordered.update(data)
    _atomic_write_bytes(h / "schema.json", (json.dumps(ordered, indent=2, ensure_ascii=False)
                                            + "\n").encode("utf-8"))
    return {"ok": True, "changed": True, "steps": steps, "backup": str(bdir)}


# -------------------------------------------------------------------- restore
def _resolve_backup(home: Path, b: Union[str, Path]) -> Path:
    p = Path(b).expanduser()
    if not p.is_absolute() and not p.exists():
        p = home / "backups" / str(b)
    return p


def restore(backup: Union[str, Path], home=None) -> dict:
    h = _home(home)
    bdir = _resolve_backup(h, backup)
    mf = bdir / MANIFEST
    if not mf.exists() or not (bdir / DATA).is_dir():
        return {"ok": False, "error": f"not a Coordn8 backup (no {MANIFEST} + {DATA}/): {bdir}"}
    manifest = json.loads(mf.read_text(encoding="utf-8"))
    files = manifest.get("files", {})
    for rel, meta in files.items():  # verify the backup itself before touching anything
        src = bdir / DATA / rel
        if not src.exists() or _sha_file(src) != meta.get("sha256"):
            return {"ok": False, "error": f"backup is damaged at {rel}; nothing was changed"}
    safety = backup_home(h, "pre-restore")
    restored, removed = [], []
    for rel in files:
        src = bdir / DATA / rel
        tgt = h / rel
        _atomic_write_bytes(tgt, src.read_bytes())
        shutil.copystat(str(src), str(tgt))
        restored.append(rel)
    for rel in user_files(h):
        if str(rel) not in files:
            (h / rel).unlink()
            removed.append(str(rel))
    keep_dirs = set(manifest.get("dirs", [])) | {str(Path(r).parent) for r in files}
    for d in sorted(user_dirs(h), key=lambda x: len(x.parts), reverse=True):
        if str(d) not in keep_dirs and "dirs" in manifest:
            try:
                (h / d).rmdir()  # only ever removes an EMPTY directory made after the backup
                removed.append(str(d) + "/")
            except OSError:
                pass
    bad = [rel for rel, meta in files.items() if _sha_file(h / rel) != meta.get("sha256")]
    return {"ok": not bad, "restored": len(restored), "removed_since_backup": removed,
            "safety_backup": str(safety), "mismatched": bad, "from": str(bdir)}


# ------------------------------------------------------------------------ CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Coordn8 user-data schema migrations (backs up first)")
    ap.add_argument("--home", default=None)
    ap.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("status", "run"):
        p = sub.add_parser(name)
        p.add_argument("--home", default=argparse.SUPPRESS)
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    p = sub.add_parser("restore")
    p.add_argument("backup")
    p.add_argument("--home", default=argparse.SUPPRESS)
    p.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.cmd == "status":
        res = status(a.home)
        ok = not res["error"]
    elif a.cmd == "run":
        res = run(a.home)
        ok = res["ok"]
    else:
        res = restore(a.backup, a.home)
        ok = res["ok"]
    if a.json:
        print(json.dumps(res, indent=1, ensure_ascii=False))
    elif a.cmd == "status":
        print(f"user data: {res['home']}  schema {res['schema_version']} ({res['schema_source']}), "
              f"package expects {res['current']}; backups: {res['backups']}")
        if res["error"]:
            print(f"error: {res['error']}")
        for p in res["pending"]:
            print(f"pending: v{p['from']} -> v{p['to']}: " + "; ".join(p["reasons"]))
        if not res["pending"] and not res["error"]:
            print("no migration pending")
    else:
        print(json.dumps(res, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
