#!/usr/bin/env python3
"""Coordn8 session RECALL — what memory says at the start of a session, in <= 4 KB (K1, K5).

READ-ONLY. Never writes, never creates a directory (the user store is opened with
create=False), so the SessionStart hook can call it safely.

Public API (stable; the SessionStart hook calls the first name):

    session_context(repo=None, live_brief="", max_bytes=4096, home=None) -> str
        The compact text injected at session start. Contains, in this order:
          1. the rule: the live brief in this chat outranks everything below;
          2. CONFLICTS between memory and the live brief, each written as
             'memory says "X" ...; this chat says "Y". The live brief wins ...' — surfaced,
             never resolved silently (nothing is written);
          3. conflicts inside memory (both entries current on the same subject);
          4. project lessons (slop_lessons.md — loaded FIRST, PLAYBOOK R.5);
          5. project open items (in-scope must/should requirements rows not covered);
          6. user standing preferences, standing decisions, user-level lessons
             (global + this project's scope; superseded entries hidden; stale ones marked);
          7. recent project decisions (decisions.md, current only).
        Budget: every section first gets up to 3 lines (conflicts all of theirs), then the
        rest fill in priority order; whatever does not fit is counted in a footer. The
        result is always <= max_bytes UTF-8 bytes. Never raises: on an internal error it
        returns the rule line plus the error class name.

    recall(repo=None, live_brief="", home=None) -> dict
        The same content, structured and untruncated (for agents and --json).

    brief_conflicts(live_brief, entries, decisions=()) -> list
        The memory-vs-brief comparison on its own. A brief clause conflicts with a memory
        entry when both have the same subject and they do not agree (curate.agrees).

CLI:
    recall.py [--repo DIR] [--brief TEXT | --brief-file F] [--max-bytes N] [--json] [--home DIR]
Zero third-party deps; Python 3.9+.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Iterable, Union

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from memory.curate import agrees, classify, clean_statement, key_of  # noqa: E402
from memory.project import ProjectMemory  # noqa: E402
from memory.store import Store, project_scope  # noqa: E402

RULE = ("The live brief in this chat outranks everything below. Where they disagree, follow the "
        "brief and tell the owner; never apply memory silently over what was just said.")
LINE_MAX = 240
FIRST_PASS = 3
SECTIONS = (  # (key, title) in display AND priority order
    ("conflicts_brief", "## Conflicts with this chat's brief (the live brief wins)"),
    ("conflicts_memory", "## Conflicts inside memory (both kept; ask the owner)"),
    ("project_lessons", "## Project lessons (load first)"),
    ("open_items", "## Open requirement rows (this project)"),
    ("preferences", "## Standing preferences"),
    ("decisions", "## Standing decisions"),
    ("lessons", "## Lessons (user-level)"),
    ("recent_decisions", "## Recent project decisions"),
)
_CLAUSE_SPLIT = re.compile(r"(?<=[.!?;])\s+|\n+|\s+(?:and also|and then|but|also|plus)\s+|"
                           r"\s+and\s+(?=(?:use|make|keep|don'?t|do not|never|always|avoid|"
                           r"stop|start|go|prefer|put|add|show|skip|cut)\b)", re.I)


def _one(s, n: int = LINE_MAX) -> str:
    t = re.sub(r"\s+", " ", str(s or "")).strip()
    return t if len(t) <= n else t[:n - 3].rstrip() + "..."


def clauses(live_brief: str) -> list:
    """Instruction-like clauses of the live brief (questions and fragments dropped)."""
    out = []
    for c in _CLAUSE_SPLIT.split(str(live_brief or "")):
        c = (c or "").strip()
        if not c:
            continue
        cl = classify(c)
        if cl["memory_worthy"] and cl["type"] in ("preference", "decision"):
            out.append(c)
    return out


def brief_conflicts(live_brief: str, entries: Iterable[dict],
                    decisions: Iterable[dict] = ()) -> list:
    """[{memory, memory_id, memory_date, memory_scope, brief, subject}] — read-only."""
    mem = []
    for e in entries:
        if e.get("type") in ("preference", "decision"):
            mem.append((key_of(e), e.get("text", ""), e.get("id"), str(e.get("date", ""))[:10],
                        e.get("scope", "global")))
    for d in decisions:
        if d.get("ask"):
            mem.append((key_of(d.get("ask")), d.get("ask"), d.get("id"), str(d.get("ts", ""))[:10],
                        "project decisions.md"))
    out, seen = [], set()
    for c in clauses(live_brief):
        text = clean_statement(c)
        k = key_of(text)
        for mk, mt, mid, mdate, msc in mem:
            if mk != k or agrees(mt, text) or (mid, c) in seen:
                continue
            seen.add((mid, c))
            out.append({"subject": k, "memory": mt, "memory_id": mid, "memory_date": mdate,
                        "memory_scope": msc, "brief": c.strip()})
    return out


def _memory_conflicts(entries: list) -> list:
    """Current preference/decision pairs on one subject that disagree, scopes overlapping.
    O(n) grouping by subject (store.conflicts is O(n^2) and too slow for a hook)."""
    groups: dict = {}
    for e in reversed(entries):  # oldest first
        if e.get("type") in ("preference", "decision"):
            groups.setdefault(key_of(e), []).append(e)
    out = []
    for k, rows in groups.items():
        if len(rows) < 2:
            continue
        for i, a in enumerate(rows[-6:]):
            for b in rows[-6:][i + 1:]:
                sa, sb = a.get("scope", "global"), b.get("scope", "global")
                if not (sa == sb or "global" in (sa, sb)):
                    continue
                if b.get("overrides") and a.get("id") in (b.get("overrides") or []):
                    continue  # a recorded project override is not an open conflict
                if agrees(a.get("text", ""), b.get("text", "")):
                    continue
                out.append({"subject": k, "a": a, "b": b})
    return out


def recall(repo: Union[str, Path, None] = None, live_brief: str = "",
           home: Union[str, Path, None] = None) -> dict:
    st = Store(home, create=False)
    scope = project_scope(Path(repo).resolve()) if repo else None
    entries = [e for e in st.list(scope=scope) if e.get("type") in
               ("preference", "decision", "lesson")]  # newest first, superseded hidden
    stale_ids = set()
    try:
        stale_ids = {e.get("id") for e in st.stale()}
    except OSError:
        pass
    pm = ProjectMemory(repo) if repo else None
    decisions_cur = pm.decisions(current_only=True) if pm else []
    out = {"rule": RULE, "project": scope, "repo": str(Path(repo).resolve()) if repo else None,
           "conflicts_brief": brief_conflicts(live_brief, entries, decisions_cur),
           "conflicts_memory": _memory_conflicts(entries),
           "project_lessons": pm.lessons() if pm else [],
           "open_items": pm.open_items() if pm else [],
           "preferences": [e for e in entries if e.get("type") == "preference"],
           "decisions": [e for e in entries if e.get("type") == "decision"],
           "lessons": [e for e in entries if e.get("type") == "lesson"],
           "recent_decisions": list(reversed(decisions_cur)),
           "stale_ids": sorted(i for i in stale_ids if i),
           "corrupt_lines": len(st.corrupt) + (len(pm.corrupt) if pm else 0)}
    return out


def _lines(data: dict) -> dict:
    stale = set(data.get("stale_ids") or [])

    def ent(e):
        sc = e.get("scope", "global")
        tag = "global" if sc == "global" else sc[len("project:"):] if sc.startswith("project:") else sc
        st = " STALE: source changed, re-check" if e.get("id") in stale else ""
        return _one(f"- {e.get('text', '')} ({e.get('id', '?')}, {tag}, "
                    f"{e.get('confidence', '?')}, {str(e.get('date', ''))[:10]}){st}")

    L = {}
    L["conflicts_brief"] = [_one(
        f"- CONFLICT [{c['subject']}]: memory says \"{_one(c['memory'], 90)}\" "
        f"({c['memory_id']}, {c['memory_scope']}, {c['memory_date']}); this chat says "
        f"\"{_one(c['brief'], 90)}\". The live brief wins: follow the chat and ask the owner "
        f"whether memory should change.", 400) for c in data["conflicts_brief"]]
    L["conflicts_memory"] = [_one(
        f"- [{c['subject']}] \"{_one(c['a'].get('text'), 70)}\" ({c['a'].get('id')}) vs "
        f"\"{_one(c['b'].get('text'), 70)}\" ({c['b'].get('id')})", 300)
        for c in data["conflicts_memory"]]
    L["project_lessons"] = [_one(
        f"- {r.get('id') or 'note'}: {r.get('text') or r.get('pattern', '')}"
        + (f" — fix: {r['fix']}" if r.get("fix") else "")) for r in data["project_lessons"]]
    L["open_items"] = [_one(
        f"- {r.get('id') or '?'} [{r.get('importance_class', '?')}, {r.get('state', '?')}] "
        f"{r.get('requirement', '')}") for r in data["open_items"]]
    for k in ("preferences", "decisions", "lessons"):
        L[k] = [ent(e) for e in data[k]]
    L["recent_decisions"] = [_one(
        f"- {d.get('id') or 'note'}: {d.get('ask') or d.get('text', '')}"
        + (f" -> {d['change']}" if d.get("change") else "")) for d in data["recent_decisions"]]
    return L


def render(data: dict, max_bytes: int = 4096) -> str:
    L = _lines(data)
    chosen = {k: [] for k, _ in SECTIONS}
    proj = data.get("project")
    head = ["# Coordn8 recall (read-only; generated at session start)", RULE,
            f"Project: {proj[len('project:'):]}" if proj else "Project: none (user memory only)"]
    if data.get("corrupt_lines"):
        head.append(f"! {data['corrupt_lines']} unreadable memory line(s) skipped.")

    def total_left() -> int:
        return sum(len(L[k]) - len(chosen[k]) for k, _ in SECTIONS)

    def build() -> str:
        parts = list(head)
        for k, title in SECTIONS:
            if chosen[k]:
                parts.append("")
                parts.append(title)
                parts.extend(chosen[k])
        left = total_left()
        if left:
            parts.append("")
            parts.append(f"({left} more not shown: python3 scripts/memory/recall.py --json)")
        if not any(chosen.values()) and not left:
            parts.append("")
            parts.append("(no stored memory applies yet)")
        return "\n".join(parts) + "\n"

    def fits() -> bool:
        return len(build().encode("utf-8")) <= max_bytes

    # pass 1: conflicts in full, then up to FIRST_PASS lines of every section; pass 2: the rest
    for pass_no in (1, 2):
        for k, _ in SECTIONS:
            limit = len(L[k]) if (pass_no == 2 or k.startswith("conflicts")) else FIRST_PASS
            while len(chosen[k]) < min(limit, len(L[k])):
                chosen[k].append(L[k][len(chosen[k])])
                if not fits():
                    chosen[k].pop()
                    break
    text = build()
    b = text.encode("utf-8")
    if len(b) > max_bytes:  # tiny caps only: hard cut on a character boundary
        text = b[:max(0, max_bytes)].decode("utf-8", "ignore")
    return text


def session_context(repo: Union[str, Path, None] = None, live_brief: str = "",
                    max_bytes: int = 4096, home: Union[str, Path, None] = None) -> str:
    """See module docstring. Never raises; never writes."""
    try:
        return render(recall(repo, live_brief or "", home), max_bytes)
    except Exception as e:  # noqa: BLE001 - a session-start helper must never crash its host
        text = f"# Coordn8 recall\n{RULE}\n(memory unavailable: {type(e).__name__})\n"
        return text.encode("utf-8")[:max(0, max_bytes)].decode("utf-8", "ignore")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Coordn8 session recall (read-only)")
    ap.add_argument("--repo", default=None)
    ap.add_argument("--brief", default="")
    ap.add_argument("--brief-file", default=None)
    ap.add_argument("--max-bytes", type=int, default=4096)
    ap.add_argument("--home", default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    brief = a.brief
    if a.brief_file:
        try:
            brief = Path(a.brief_file).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
    if a.json:
        try:
            data = recall(a.repo, brief, a.home)
        except Exception as e:  # noqa: BLE001
            print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
            return 2
        data["text"] = render(data, a.max_bytes)
        print(json.dumps(data, ensure_ascii=False, indent=1, default=str))
    else:
        sys.stdout.write(session_context(a.repo, brief, a.max_bytes, a.home))
    return 0


if __name__ == "__main__":
    sys.exit(main())
