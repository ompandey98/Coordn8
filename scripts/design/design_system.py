#!/usr/bin/env python3
"""Read-modify-write helper for a build's `design_system.json` (SPEC §3.2).

Several design steps each own ONE top-level key of the same file — the palette director
writes "palette", the kit picker writes "kit"/"type"/"motion", and so on. They may run in
parallel. So every write goes through `update(build_dir, key, value)`, which:

  * preserves every other top-level key byte-for-byte in meaning (the file is re-read
    under the lock, so a key written by another process a moment ago survives);
  * holds an exclusive advisory lock (`design_system.json.lock`, fcntl) for the whole
    read-modify-write, so two writers cannot interleave and lose an update;
  * writes atomically: a temp file in the same directory, fsync, then os.replace —
    a reader never sees a half-written file, and a crash leaves the old file intact;
  * keeps `"schema": 1` present.

`build_dir` is the directory that holds design_system.json (normally `<repo>/Coordn8/_build`).
A path that ends in `.json` is taken as the file itself.

CLI:
    design_system.py show <build_dir>
    design_system.py get  <build_dir> <key>
    design_system.py set  <build_dir> <key> <json-value>
Zero third-party deps; Python 3.9+.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Union

FILENAME = "design_system.json"
SCHEMA = 1
_THREAD_LOCK = threading.Lock()

# ---------------------------------------------------------------- palette role semantics
# The role vocabulary every palette (catalog or invented) is judged in. These are facts about
# what a ROLE means, never about where a palette came from, so both the palette director and
# the palette gate read them from here.
PAPER = "paper"
# Every role except paper may carry text somewhere (a label in the role colour), so every one
# of them is held to the text contrast bar on its own paper, in both skins.
TEXT_ROLES = ("ink", "muted", "accent", "link", "positive", "negative", "warning",
              "deterministic", "model-judgement", "data-field", "vision")
STANDARD_ROLES = (PAPER,) + TEXT_ROLES
# Roles that can appear side by side carrying DIFFERENT meanings must be told apart.
# Pairs across groups (e.g. data-field vs positive) may legitimately share a colour.
SEPARATION_GROUPS = (
    ("ink", "muted", "accent"),                                        # text hierarchy + focal
    ("accent", "link", "positive", "negative", "warning"),            # page/status colours
    ("accent", "deterministic", "model-judgement", "data-field", "vision"),  # source-of-truth legend
)
MIN_ROLE_SEPARATION = 10.0     # CIEDE2000; below this two roles read as one colour
TEXT_MIN = 4.5                 # WCAG 2.x body text
LARGE_TEXT_MIN = 3.0           # WCAG 2.x large text
LARGE_PX = 24.0                # >= 24 px is large
LARGE_BOLD_PX = 18.66          # >= 14 pt (18.66 px) bold is large (WCAG definition)
ACCENT_BUDGET = 2              # accent on at most two elements per frame
NEAR_MATCH_DE = 2.0            # a colour within this CIEDE2000 of a role IS that role
# Words an element may use to declare what it means -> the role whose colour carries it.
MEANING_ROLE = {
    "positive": "positive", "increase": "positive", "up": "positive", "gain": "positive",
    "good": "positive", "pass": "positive", "success": "positive",
    "negative": "negative", "decrease": "negative", "down": "negative", "loss": "negative",
    "bad": "negative", "fail": "negative", "error": "negative",
    "warning": "warning", "caution": "warning",
    "focal": "accent", "accent": "accent", "link": "link",
    "deterministic": "deterministic", "model-judgement": "model-judgement",
    "data-field": "data-field", "vision": "vision",
}
POLARITY = {"positive": +1, "negative": -1}

try:  # POSIX advisory locks; on hosts without fcntl the thread lock still serialises in-process
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore


def path_for(build_dir: Union[str, Path]) -> Path:
    p = Path(build_dir)
    return p if p.suffix == ".json" else p / FILENAME


def read(build_dir: Union[str, Path]) -> dict:
    """The current document, or {"schema": 1} if there is none yet."""
    p = path_for(build_dir)
    if not p.exists():
        return {"schema": SCHEMA}
    data = json.loads(p.read_text(encoding="utf-8") or "{}")
    if not isinstance(data, dict):
        raise ValueError(f"{p} does not hold a JSON object")
    data.setdefault("schema", SCHEMA)
    return data


@contextlib.contextmanager
def _locked(p: Path):
    p.parent.mkdir(parents=True, exist_ok=True)
    with _THREAD_LOCK:
        lock_path = p.with_name(p.name + ".lock")
        fh = open(lock_path, "a+")
        try:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            fh.close()


def _atomic_write(p: Path, data: dict) -> None:
    text = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
    if p.exists():
        mode = p.stat().st_mode & 0o777
    else:
        um = os.umask(0)
        os.umask(um)
        mode = 0o666 & ~um
    fd, tmp = tempfile.mkstemp(prefix=".ds-", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, p)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def update(build_dir: Union[str, Path], key: str, value: Any) -> dict:
    """Set one top-level key; keep every other key. Returns the document as written."""
    if not isinstance(key, str) or not key:
        raise ValueError("key must be a non-empty string")
    if key == "schema":
        raise ValueError("'schema' is managed by this helper")
    p = path_for(build_dir)
    with _locked(p):
        data = read(p)
        data[key] = value
        _atomic_write(p, data)
        return data


def update_many(build_dir: Union[str, Path], values: dict) -> dict:
    """Set several top-level keys in one locked write."""
    p = path_for(build_dir)
    with _locked(p):
        data = read(p)
        for k, v in values.items():
            if k == "schema":
                continue
            data[k] = v
        _atomic_write(p, data)
        return data


def main(argv=None) -> int:
    a = list(sys.argv[1:] if argv is None else argv)
    if len(a) < 2 or a[0] not in ("show", "get", "set"):
        print(__doc__.split("CLI:")[1].split("Zero")[0].rstrip(), file=sys.stderr)
        return 2
    cmd, bd = a[0], a[1]
    if cmd == "show":
        print(json.dumps(read(bd), indent=2, ensure_ascii=False))
        return 0
    if cmd == "get":
        if len(a) < 3:
            return 2
        print(json.dumps(read(bd).get(a[2]), indent=2, ensure_ascii=False))
        return 0
    if len(a) < 4:
        return 2
    update(bd, a[2], json.loads(a[3]))
    print(str(path_for(bd)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
