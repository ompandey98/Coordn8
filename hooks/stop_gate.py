#!/usr/bin/env python3
"""Coordn8 Stop hook — a build may not be called done while its verification manifest is open.

Decision, in order (first match wins):
  1. stop_hook_active is true            -> allow (loop guard: we already blocked once).
  2. no <cwd>/Coordn8/_build/ folder     -> allow, print nothing.
  3. nothing under _build/ changed this session (hook-owned files ignored) -> allow.
  4. the last assistant message is a question to the owner with no completion claim
     (a pause for input, not a "done")   -> allow.
  5. verify_manifest.py on _build/verification.json says READY -> allow.
  6. otherwise                            -> {"decision": "block", "reason": <open items>}
     (reason <= 1,500 chars).

"This session" starts at the marker session_start.py wrote for this session_id; failing
that, at the transcript's first timestamp (or the transcript file's creation time); failing
both, the last 2 hours. Anything the hook cannot decide (manifest check too slow, a read
error) ALLOWS: a hook that cannot tell must never trap the owner.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _common as C
except BaseException:  # noqa: BLE001
    os._exit(0)

REASON_MAX = 1500
UNKNOWN_START_WINDOW_S = 2 * 3600
WALK_MAX_ENTRIES = 50_000
WALK_MAX_SECONDS = 0.6
MANIFEST_TIMEOUT = 1.2

CLAIM = re.compile(
    r"\b(done|complete[ds]?|finished|verified|shipped|deliver(?:ed|ables?)|"
    r"all (?:gates|checks|tests) pass\w*|here(?:'s| is) (?:the|your)|"
    r"(?:the )?(?:video|page|build) is (?:ready|built|rendered))\b", re.I)


def _iso_epoch(text: str):
    t = C.s(text).strip()
    if not t:
        return None
    try:
        t = t.replace("Z", "+00:00")
        return datetime.fromisoformat(t).timestamp()
    except Exception:  # noqa: BLE001
        return None


def session_start(data: dict, build: Path):
    """(epoch, source) for the start of this session, or (None, 'unknown')."""
    sid = C.s(data.get("session_id"))
    if sid:
        marker = build / C.SESSION_DIR / (C.safe_name(sid) + ".json")
        try:
            m = json.loads(marker.read_text(encoding="utf-8"))
            st = float(m.get("start"))
            if st > 0:
                return st, "session marker"
        except Exception:  # noqa: BLE001
            try:
                return marker.stat().st_mtime, "session marker mtime"
            except Exception:  # noqa: BLE001
                pass
    tp = C.s(data.get("transcript_path"))
    if tp:
        p = Path(tp).expanduser()
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                for _ in range(20):
                    line = f.readline()
                    if not line:
                        break
                    try:
                        ts = _iso_epoch(json.loads(line).get("timestamp"))
                    except Exception:  # noqa: BLE001
                        ts = None
                    if ts:
                        return ts, "transcript first timestamp"
        except Exception:  # noqa: BLE001
            pass
        try:
            st = p.stat()
            return getattr(st, "st_birthtime", None) or st.st_ctime, "transcript file time"
        except Exception:  # noqa: BLE001
            pass
    return None, "unknown"


def changed_since(build: Path, since: float):
    """First non-hook-owned file under build/ with mtime > since, or None. Bounded walk."""
    t_end = time.monotonic() + WALK_MAX_SECONDS
    seen = 0
    stack = [(build, "")]
    while stack:
        d, rel = stack.pop()
        try:
            it = os.scandir(d)
        except OSError:
            continue
        with it:
            for e in it:
                seen += 1
                if seen > WALK_MAX_ENTRIES or time.monotonic() > t_end:
                    return None
                r = f"{rel}/{e.name}" if rel else e.name
                if r in C.HOOK_OWNED:
                    continue
                try:
                    if e.is_dir(follow_symlinks=False):
                        stack.append((Path(e.path), r))
                    elif e.stat(follow_symlinks=False).st_mtime > since:
                        return r
                except OSError:
                    continue
    return None


def is_pause_for_input(msg) -> bool:
    m = C.s(msg).strip()
    if not m:
        return False
    return m.rstrip("`*_ \n").endswith("?") and not CLAIM.search(m)


def manifest_state(build: Path):
    """(ready: bool|None, result dict|None). None = could not decide in time."""
    manifest = build / "_build" / "verification.json"
    gate = C.SCRIPTS / "gates" / "verify_manifest.py"
    if not gate.is_file():
        return None, None
    res = C.run_bounded([sys.executable, str(gate), str(manifest)], MANIFEST_TIMEOUT)
    if res is None:
        return None, None
    code, out, _err = res
    obj = C.first_json(out) or {}
    if code == 0 and obj.get("status") == "PASS":
        return True, obj
    if obj.get("status") in ("PASS", "REVIEW", "SKIPPED", "ERROR"):
        return False, obj
    return None, None


def build_reason(obj: dict, manifest: Path, changed: str) -> str:
    head = (f"Coordn8: this session changed Coordn8/_build/ ({changed}) but the verification "
            f"manifest is NOT READY, so the build may not be called done or 'verified'.")
    parts = []
    st = obj.get("status")
    if st == "SKIPPED":
        parts.append(f"No usable manifest: {C.clip(obj.get('summary'), 220)}")
    elif st == "ERROR":
        parts.append(f"Manifest unreadable: {C.clip(obj.get('summary'), 220)}")
    opened = obj.get("still_open") or []
    if opened:
        items = []
        for it in opened:
            if not isinstance(it, dict):
                continue
            bit = f"{C.s(it.get('id'), '?')} ({C.s(it.get('status'), '?')}"
            ev = C.s(it.get("evidence") or it.get("risk") or it.get("note")).strip()
            items.append(bit + (f": {C.clip(ev, 60)})" if ev else ")"))
        parts.append(f"Open items ({len(items)}): " + "; ".join(items))
    over = [C.s(x) for x in (obj.get("overclaimed") or []) + (obj.get("semantic_unauthorized") or [])]
    if over:
        parts.append("Claims refused: " + ", ".join(dict.fromkeys(over)))
    if not opened and not over and obj.get("findings"):
        parts.append("Findings: " + "; ".join(C.clip(f, 120) for f in obj["findings"][:3]))
    tail = (f"Next: run the open gates, record each in {manifest}, then re-run "
            f"`python3 {C.SCRIPTS / 'gates' / 'verify_manifest.py'} {manifest}`. If you are not "
            f"claiming done, say plainly which items remain open and what was NOT verified, "
            f"then stop; this hook does not block twice in a row.")
    body = " ".join(parts)
    room = REASON_MAX - len(head) - len(tail) - 2
    return f"{head} {C.clip(body, max(0, room))} {tail}"[:REASON_MAX]


def main():
    data = C.read_stdin()
    if C.truthy(data.get("stop_hook_active")):
        return None
    build = C.coordn8_dir(data)
    if not (build / "_build").is_dir():
        return None
    start, _src = session_start(data, build / "_build")
    since = start if start is not None else time.time() - UNKNOWN_START_WINDOW_S
    changed = changed_since(build / "_build", since)
    if not changed:
        return None
    if is_pause_for_input(data.get("last_assistant_message")):
        return None
    ready, obj = manifest_state(build)
    if ready is None or ready:
        return None
    return {"decision": "block",
            "reason": build_reason(obj or {}, build / "_build" / "verification.json", changed)}


if __name__ == "__main__":
    C.guarded(main)
