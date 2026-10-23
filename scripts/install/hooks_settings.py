#!/usr/bin/env python3
"""Coordn8 hooks for the NON-plugin install path (npx / install.sh): the settings.json block.

When Coordn8 is installed as a plugin, the host reads hooks/hooks.json and expands
${CLAUDE_PLUGIN_ROOT}. When it is installed without the plugin system, the same four hooks
have to be merged into a host settings.json, with ABSOLUTE paths (that variable is not
expanded there). This module is the one place that does it.

    settings_block(pkg_root) -> {"SessionStart": [...], "PostToolUse": [...],
                                 "SubagentStop": [...], "Stop": [...]}
        Pure: reads <pkg_root>/hooks/hooks.json (falls back to the built-in table) and
        rewrites every ${CLAUDE_PLUGIN_ROOT} script path to an absolute, shell-quoted path.

    merge_settings(settings_path, pkg_root) -> dict
        For each event: keep every user matcher group exactly as it was, drop any older
        Coordn8 group (e.g. from a previous version's install path), append ours. Each
        changed event is written with scripts/lib/safe_write.merge_json (backup -> verify
        nothing else changes -> atomic write). Idempotent.

    remove_settings(settings_path) -> dict
        Drops every Coordn8 group; an event left empty is removed; a "hooks" object left
        empty is removed (via safe_write.remove_json). Every user key/group is untouched.
        Known edge: a user file that had an EMPTY "hooks": {} before install loses that
        empty object on removal (nothing records that it pre-existed).

A group is "ours" only when every command in it is exactly the shape settings_block
writes: python3 <quoted path>/hooks/<one of our four scripts>.py.

CLI:
    hooks_settings.py block  [--pkg-root DIR]
    hooks_settings.py merge  --settings FILE [--pkg-root DIR]
    hooks_settings.py remove --settings FILE
Zero third-party deps; Python 3.9+.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.safe_write import merge_json, remove_json  # noqa: E402

PKG_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ("session_start", "post_render", "subagent_log", "stop_gate")
EVENTS = ("SessionStart", "PostToolUse", "SubagentStop", "Stop")
PLUGIN_VAR = "${CLAUDE_PLUGIN_ROOT}"

# Built-in table — used only if hooks/hooks.json cannot be read. Mirrors hooks.json.
_FALLBACK = {
    "SessionStart": [{"hooks": [{"type": "command", "script": "session_start", "timeout": 10}]}],
    "PostToolUse": [{"matcher": "Bash",
                     "hooks": [{"type": "command", "script": "post_render", "timeout": 10}]}],
    "SubagentStop": [{"hooks": [{"type": "command", "script": "subagent_log", "timeout": 10}]}],
    "Stop": [{"hooks": [{"type": "command", "script": "stop_gate", "timeout": 10}]}],
}

_OURS = re.compile(r"""^\s*python3\s+(?:'(?P<sq>[^']+)'|"(?P<dq>[^"]+)"|(?P<bare>\S+))\s*$""")
_SCRIPT_TAIL = re.compile(r"(?:^|/)hooks/(%s)\.py$" % "|".join(SCRIPTS))


def _command_for(pkg_root: Path, script: str) -> str:
    return "python3 " + shlex.quote(str(Path(pkg_root).resolve() / "hooks" / f"{script}.py"))


def settings_block(pkg_root) -> dict:
    """The hooks dict (event -> matcher groups) to merge into a host settings.json."""
    root = Path(pkg_root).resolve()
    try:
        src = json.loads((root / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
        if not isinstance(src, dict):
            raise ValueError("hooks is not an object")
    except Exception:  # noqa: BLE001
        src = None
    out: dict = {}
    if src is None:
        for ev in EVENTS:
            groups = []
            for g in _FALLBACK[ev]:
                ng = {k: v for k, v in g.items() if k != "hooks"}
                ng["hooks"] = [{"type": "command", "command": _command_for(root, h["script"]),
                                "timeout": h["timeout"]} for h in g["hooks"]]
                groups.append(ng)
            out[ev] = groups
        return out
    for ev, groups in src.items():
        new_groups = []
        for g in groups if isinstance(groups, list) else []:
            ng = copy.deepcopy(g)
            for h in ng.get("hooks") or []:
                cmd = h.get("command")
                if isinstance(cmd, str):
                    m = re.search(r"\$\{CLAUDE_PLUGIN_ROOT\}/hooks/([A-Za-z0-9_]+)\.py", cmd)
                    if m:
                        h["command"] = _command_for(root, m.group(1))
                    else:
                        h["command"] = cmd.replace(PLUGIN_VAR, str(root))
            new_groups.append(ng)
        out[ev] = new_groups
    return out


def _is_ours_command(cmd) -> bool:
    if not isinstance(cmd, str):
        return False
    m = _OURS.match(cmd)
    if not m:
        return False
    path = m.group("sq") or m.group("dq") or m.group("bare") or ""
    return bool(_SCRIPT_TAIL.search(path))


def is_ours(group) -> bool:
    if not isinstance(group, dict):
        return False
    hooks = group.get("hooks")
    if not isinstance(hooks, list) or not hooks:
        return False
    return all(isinstance(h, dict) and _is_ours_command(h.get("command")) for h in hooks)


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level is not an object")
    return data


def merge_settings(settings_path, pkg_root=PKG_ROOT) -> dict:
    p = Path(settings_path).expanduser()
    block = settings_block(pkg_root)
    data = _load(p)
    hooks = data.get("hooks")
    if hooks is not None and not isinstance(hooks, dict):
        raise ValueError(f"{p}: 'hooks' exists and is not an object; refusing to touch it")
    actions = []
    for ev, ours in block.items():
        data = _load(p)
        current = (data.get("hooks") or {}).get(ev)
        if current is not None and not isinstance(current, list):
            raise ValueError(f"{p}: hooks.{ev} is not an array; refusing to touch it")
        current = current or []
        new = [g for g in current if not is_ours(g)] + ours
        if new == current:
            actions.append({"event": ev, "action": "unchanged"})
            continue
        res = merge_json(p, f"hooks.{ev}", new)
        actions.append({"event": ev, "action": res.get("action"), "backup": res.get("backup")})
    return {"ok": True, "file": str(p), "events": actions}


def remove_settings(settings_path) -> dict:
    p = Path(settings_path).expanduser()
    if not p.exists():
        return {"ok": True, "file": str(p), "events": [], "action": "absent"}
    actions = []
    for ev in EVENTS:
        data = _load(p)
        hooks = data.get("hooks")
        if not isinstance(hooks, dict) or not isinstance(hooks.get(ev), list):
            continue
        current = hooks[ev]
        kept = [g for g in current if not is_ours(g)]
        if kept == current:
            continue
        if kept:
            res = merge_json(p, f"hooks.{ev}", kept)
        else:
            res = remove_json(p, f"hooks.{ev}")
        actions.append({"event": ev, "action": res.get("action"), "backup": res.get("backup")})
    data = _load(p)
    if actions and data.get("hooks") == {}:
        res = remove_json(p, "hooks")
        actions.append({"event": "*", "action": res.get("action"), "backup": res.get("backup")})
    return {"ok": True, "file": str(p), "events": actions}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Coordn8 hooks block for a host settings.json")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("block"); b.add_argument("--pkg-root", default=str(PKG_ROOT))
    m = sub.add_parser("merge"); m.add_argument("--settings", required=True)
    m.add_argument("--pkg-root", default=str(PKG_ROOT))
    r = sub.add_parser("remove"); r.add_argument("--settings", required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "block":
            res = {"hooks": settings_block(a.pkg_root)}
        elif a.cmd == "merge":
            res = merge_settings(a.settings, a.pkg_root)
        else:
            res = remove_settings(a.settings)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "action": a.cmd, "error": f"{type(e).__name__}: {e}"}))
        return 1
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
