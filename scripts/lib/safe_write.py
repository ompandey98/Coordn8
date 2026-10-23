#!/usr/bin/env python3
"""Coordn8 — the ONLY way the package touches a file it does not own.

Every write to a user file (an existing AGENTS.md, .cursor/rules/*, .kiro/steering/*,
an MCP config, a settings file) goes through here, and always in this order:

    1. BACKUP   copy the file to <user_home>/backups/<timestamp>/<mangled-path>
    2. MERGE    change ONLY a delimited Coordn8 block (text files) or ONLY the
                Coordn8-owned keys (JSON files); everything else is untouched
    3. VERIFY   re-read the planned output and prove every byte outside the block
                (or every key not ours) is identical to the original — else abort
    4. WRITE    atomically (temp file + os.replace), so a crash never leaves half a file

Block markers (text files):
    HTML-comment style:  <!-- COORDN8:BEGIN v<ver> --> ... <!-- COORDN8:END -->
    hash style:          # COORDN8:BEGIN v<ver> ... # COORDN8:END
If the block is absent it is appended; if present only its contents are replaced.

CLI (used by install.sh):
    safe_write.py merge-block  <file> <content-file> --version V [--style html|hash]
    safe_write.py remove-block <file> [--style html|hash]
    safe_write.py merge-json   <file> <dotted.key> <value-json-file>
    safe_write.py remove-json  <file> <dotted.key>
    safe_write.py backup       <file>
Every command prints one JSON line: {"ok": bool, "action": ..., "file": ..., "backup": ...}.
Zero third-party deps; Python 3.8+.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import user_home  # noqa: E402

STYLES = {
    "html": ("<!-- COORDN8:BEGIN v{ver} -->", "<!-- COORDN8:END -->",
             re.compile(r"^<!-- COORDN8:BEGIN v[^\n]* -->$"), "<!-- COORDN8:END -->"),
    "hash": ("# COORDN8:BEGIN v{ver}", "# COORDN8:END",
             re.compile(r"^# COORDN8:BEGIN v\S*$"), "# COORDN8:END"),
}


class SafeWriteError(RuntimeError):
    pass


# --------------------------------------------------------------------------- io
def _read(path: Path) -> str:
    with open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".coordn8-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        if path.exists():
            shutil.copymode(str(path), tmp)
        os.replace(tmp, str(path))
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


_STAMP = None


def _stamp() -> str:
    """One timestamp per process, so every file one install touches lands in one folder."""
    global _STAMP
    if _STAMP is None:
        _STAMP = time.strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}"
    return _STAMP


def backup(path: str | Path) -> Path | None:
    """Copy `path` into the user backups folder. Returns the backup path (None if no file)."""
    p = Path(path).expanduser()
    if not p.exists():
        return None
    mangled = str(p.resolve()).lstrip("/").replace("/", "__").replace("\\", "__").replace(":", "")
    dest = user_home() / "backups" / _stamp() / mangled
    dest.parent.mkdir(parents=True, exist_ok=True)
    n = 1
    while dest.exists():  # same file touched twice in one run: keep the FIRST original too
        dest = dest.with_name(f"{mangled}.{n}")
        n += 1
    shutil.copy2(str(p), str(dest))
    return dest


# ----------------------------------------------------------------- text blocks
def _find_block(lines: list[str], style: str):
    _, _, begin_re, end_line = STYLES[style]
    start = end = None
    for i, raw in enumerate(lines):
        s = raw.rstrip("\r\n")
        if start is None and begin_re.match(s):
            start = i
        elif start is not None and s == end_line:
            end = i
            break
    if start is not None and end is None:
        raise SafeWriteError("found a COORDN8:BEGIN marker with no matching END — refusing to guess")
    return start, end


def _outside(text: str, style: str) -> str:
    lines = text.splitlines(keepends=True)
    start, end = _find_block(lines, style)
    if start is None:
        return text
    return "".join(lines[:start] + lines[end + 1:])


def plan_merge_block(original: str | None, content: str, version: str, style: str = "html") -> str:
    begin_tpl, end_line, _, _ = STYLES[style]
    body = content if content.endswith("\n") else content + "\n"
    block = f"{begin_tpl.format(ver=version)}\n{body}{end_line}\n"
    if original is None or original == "":
        return block
    lines = original.splitlines(keepends=True)
    start, end = _find_block(lines, style)
    if start is None:
        sep = "" if original.endswith("\n") else "\n"
        return original + sep + "\n" + block
    return "".join(lines[:start]) + block + "".join(lines[end + 1:])


def merge_block(path: str | Path, content: str, version: str, style: str = "html") -> dict:
    p = Path(path).expanduser()
    original = _read(p) if p.exists() else None
    planned = plan_merge_block(original, content, version, style)
    if original is not None:
        # VERIFY: everything outside our block is byte-identical. For an append, the
        # original is a prefix of the planned output (plus our separator).
        before, after = _outside(original, style), _outside(planned, style)
        appended = _find_block(original.splitlines(keepends=True), style)[0] is None
        if appended:
            ok = after.rstrip("\n") == original.rstrip("\n") or after.startswith(original)
        else:
            ok = before == after
        if not ok:
            raise SafeWriteError(f"refusing to write {p}: content outside the Coordn8 block would change")
    if planned == original:
        return {"ok": True, "action": "unchanged", "file": str(p), "backup": None}
    bk = backup(p) if original is not None else None
    _atomic_write(p, planned)
    return {"ok": True, "action": "created" if original is None else "merged",
            "file": str(p), "backup": str(bk) if bk else None}


def remove_block(path: str | Path, style: str = "html") -> dict:
    p = Path(path).expanduser()
    if not p.exists():
        return {"ok": True, "action": "absent", "file": str(p), "backup": None}
    original = _read(p)
    lines = original.splitlines(keepends=True)
    start, end = _find_block(lines, style)
    if start is None:
        return {"ok": True, "action": "no-block", "file": str(p), "backup": None}
    head, tail = lines[:start], lines[end + 1:]
    # drop the single blank separator line merge_block added before the block
    if head and head[-1].strip() == "" and not tail:
        head = head[:-1]
    planned = "".join(head + tail)
    bk = backup(p)
    if planned.strip() == "":
        p.unlink()
        return {"ok": True, "action": "deleted-empty", "file": str(p), "backup": str(bk)}
    _atomic_write(p, planned)
    return {"ok": True, "action": "block-removed", "file": str(p), "backup": str(bk)}


# ------------------------------------------------------------------ JSON keys
def _get(d: dict, keys: list[str]):
    for k in keys:
        if not isinstance(d, dict) or k not in d:
            return None, False
        d = d[k]
    return d, True


def merge_json(path: str | Path, dotted: str, value) -> dict:
    """Set ONE dotted key (e.g. 'mcpServers.playwright') in a JSON file; nothing else changes."""
    p = Path(path).expanduser()
    keys = [k for k in dotted.split(".") if k]
    if not keys:
        raise SafeWriteError("empty key path")
    original_text = _read(p) if p.exists() else None
    try:
        data = json.loads(original_text) if original_text and original_text.strip() else {}
    except ValueError as e:
        raise SafeWriteError(f"refusing to touch {p}: it is not valid JSON ({e})")
    if not isinstance(data, dict):
        raise SafeWriteError(f"refusing to touch {p}: top level is not an object")
    cur, found = _get(data, keys)
    if found and cur == value:
        return {"ok": True, "action": "unchanged", "file": str(p), "backup": None}
    snapshot = json.loads(json.dumps(data))
    node = data
    created_parents: list[list[str]] = []
    for i, k in enumerate(keys[:-1]):
        if k not in node or not isinstance(node[k], dict):
            if k in node:
                raise SafeWriteError(f"refusing: '{k}' in {p} exists and is not an object")
            node[k] = {}
            created_parents.append(keys[:i + 1])
        node = node[k]
    node[keys[-1]] = value
    # VERIFY: every other key unchanged (parents we had to create count as ours)
    check = json.loads(json.dumps(data))
    _del(check, keys)
    for parent in reversed(created_parents):
        _del(check, parent)
    _del(snapshot, keys)
    if check != snapshot:
        raise SafeWriteError("internal check failed: keys other than the target would change")
    bk = backup(p) if original_text is not None else None
    indent = 2
    _atomic_write(p, json.dumps(data, indent=indent, ensure_ascii=False) + "\n")
    return {"ok": True, "action": "created" if original_text is None else "merged",
            "file": str(p), "backup": str(bk) if bk else None}


def _del(d: dict, keys: list[str]) -> None:
    for k in keys[:-1]:
        if not isinstance(d, dict) or k not in d:
            return
        d = d[k]
    if isinstance(d, dict):
        d.pop(keys[-1], None)


def remove_json(path: str | Path, dotted: str) -> dict:
    p = Path(path).expanduser()
    if not p.exists():
        return {"ok": True, "action": "absent", "file": str(p), "backup": None}
    keys = [k for k in dotted.split(".") if k]
    data = json.loads(_read(p) or "{}")
    _, found = _get(data, keys)
    if not found:
        return {"ok": True, "action": "no-key", "file": str(p), "backup": None}
    bk = backup(p)
    _del(data, keys)
    _atomic_write(p, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return {"ok": True, "action": "key-removed", "file": str(p), "backup": str(bk)}


# ------------------------------------------------------------------------ CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Coordn8 safe writer (backup → block-merge → verify)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("merge-block"); m.add_argument("file"); m.add_argument("content_file")
    m.add_argument("--version", required=True); m.add_argument("--style", default="html", choices=list(STYLES))
    r = sub.add_parser("remove-block"); r.add_argument("file"); r.add_argument("--style", default="html", choices=list(STYLES))
    j = sub.add_parser("merge-json"); j.add_argument("file"); j.add_argument("key"); j.add_argument("value_file")
    k = sub.add_parser("remove-json"); k.add_argument("file"); k.add_argument("key")
    b = sub.add_parser("backup"); b.add_argument("file")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "merge-block":
            res = merge_block(a.file, _read(Path(a.content_file)), a.version, a.style)
        elif a.cmd == "remove-block":
            res = remove_block(a.file, a.style)
        elif a.cmd == "merge-json":
            res = merge_json(a.file, a.key, json.loads(_read(Path(a.value_file))))
        elif a.cmd == "remove-json":
            res = remove_json(a.file, a.key)
        else:
            bk = backup(a.file)
            res = {"ok": True, "action": "backup", "file": a.file, "backup": str(bk) if bk else None}
    except (SafeWriteError, OSError, ValueError) as e:
        print(json.dumps({"ok": False, "action": a.cmd, "file": getattr(a, "file", None), "error": str(e)}))
        return 1
    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
