#!/usr/bin/env python3
"""Coordn8 memory CURATION — turns what the owner says into typed memory, and recurring
failures into lessons (K1, K5, F4 promotion). The memory-curator agent drives this file.

Public API (stable; other parts of Coordn8 call these names):

    classify(statement) -> dict
        Read-only. {"memory_worthy", "type", "confidence", "scope_hint", "text", "subject",
        "why"} for one stated preference / decision / lesson / fact.

    capture(statement, source="chat", scope=None, repo=None, subject=None, home=None,
            confidence=None, type=None) -> dict
        Classify the statement and append ONE typed entry to the user store
        (~/.coordn8/memory/entries.jsonl, or COORDN8_HOME) with the verbatim statement as
        `source_quote` and a confidence. Returns {"action", "entry", "superseded",
        "conflicts", "overrides", "classified", "project_decision"}; action is one of
        added | duplicate | superseded | conflict-surfaced | ignored.
        A project-scoped decision is also logged to <repo>/Coordn8/_research/decisions.md.

    supersede_if_contrary(store, text, type, scope, subject=None, **entry_fields) -> dict
        Appends the new entry. If a CURRENT entry in the SAME scope on the same subject says
        something contrary, the new entry names it in `supersedes` with a reason; the old line
        is never touched (both stay in `Store.history`). Same-subject entries that are only
        possibly contrary are NOT superseded — they are returned as conflicts to surface.

    promote_lessons(repo, home=None, min_in_project=2, min_projects=2) -> dict
        Idempotent. A cause_class seen >= min_in_project times in this project's
        failure_log.jsonl becomes a project lesson in slop_lessons.md (recall loads project
        lessons FIRST). Every cause_class seen in a project is registered once in the user
        store as a `failure` entry (scope project:<slug>, with the repo path); a class
        registered by >= min_projects distinct projects becomes ONE user-level lesson
        (scope global, `cause_class`, `projects`, `source_refs`). Re-running writes nothing.

    contrary(a_text, b_text) / agrees(a_text, b_text) -> bool
        The comparison used by supersession and by recall's live-brief conflict check.

How "contrary" is decided (a documented heuristic, not a judgement):
  Both statements must share the store's SUBJECT KEY (memory.store.subject_key). Each
  statement's subject phrase is split into its HEAD (the key) and its MODIFIERS (the other
  content words in the phrase, e.g. "male" in "use a male voice"). They AGREE when polarity
  is the same and one modifier set contains the other ("female voice" agrees with "female
  narration voice"; "male narrator voice" does not agree with "female narrator voice").
  A disagreement is acted on automatically (supersede) only when it is clear: opposite
  polarity, an explicit change marker ("actually", "instead", "no longer", "switch to",
  "from now on", ...), modifiers where neither set contains the other, or an explicit
  `subject`. Subjects are computed on the statement minus preambles such as "we decided to"
  or "I always want" (core_text), so the key is the thing decided, not the verb.
  Anything less clear is surfaced as a conflict and both stay current. A supersede never
  crosses scopes: a project instruction that contradicts a global preference is recorded
  as a project OVERRIDE (the global one still applies to every other project).

CLI:
    curate.py classify  "<statement>"                                  [--json]
    curate.py capture   "<statement>" [--source S] [--scope S] [--repo R] [--subject S] [--json]
    curate.py promote   --repo R                                       [--json]
  Every command accepts --home DIR (else COORDN8_HOME / ~/.coordn8).
Zero third-party deps; Python 3.9+.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
from pathlib import Path
from typing import Mapping, Optional, Union

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from memory import store as _st  # noqa: E402
from memory.project import ProjectMemory  # noqa: E402
from memory.store import Store, project_scope, subject_key  # noqa: E402

try:
    import fcntl as _fcntl  # type: ignore
except ImportError:  # pragma: no cover - non-POSIX
    _fcntl = None

CAPTURE_TYPES = ("preference", "decision", "lesson", "fact")
SUPERSEDE_TYPES = ("preference", "decision")
PROMOTE_SOURCE = "failure_log"

# ------------------------------------------------------------------ text rules
_FILLER = re.compile(
    r"^\s*(?:(?:actually|ok(?:ay)?|so|also|hey|well|wait|no|nope|hmm|right|and|but|"
    r"scratch that|on second thought|change of plan|correction|update|note|fyi|"
    r"please|kindly|can you|could you|would you|will you|i'd like you to|i want you to)"
    r"\s*[,:;.!-]*\s+)+", re.I)
_TRAIL = re.compile(r"\s*(?:,?\s*(?:instead|please|thanks|thank you|from now on|"
                    r"going forward|anymore|any more))+\s*[.!?]*\s*$", re.I)
_WH_QUESTION = re.compile(r"^\s*(?:what|why|how|where|when|who|whom|whose|which|is|are|was|"
                          r"were|does|did|do|has|have|had)\b[^.!]*\?\s*$", re.I)

_LESSON = re.compile(r"\b(lesson|learn(?:ed|t)|next time|never again|don'?t repeat|do not repeat|"
                     r"avoid repeating|the mistake|went wrong|keeps? happening|again and again)\b", re.I)
_DECISION = re.compile(r"\b(we decided|i decided|decided (?:to|on|that)|decision|let'?s go with|"
                       r"we'?ll go with|go with|going with|approved?|sign(?:ed)? off|settled|"
                       r"final(?:ly)? (?:answer|call|choice)|we chose|i chose|chosen|agreed)\b", re.I)
_PREFERENCE = re.compile(r"\b(prefer|i like|i love|i hate|i want|i'?d like|i would like|always|never|"
                         r"don'?t|do not|avoid|use|keep|make|should|must|no more|stop|start|"
                         r"instead|from now on|rather|switch|i'?d rather|please)\b", re.I)
_FACT = re.compile(r"^\s*(?:the|this|our|its?|that|these|those|[A-Z][\w.-]*)\b.*\b(?:is|are|was|were|"
                   r"runs|lives|uses|reads|writes|calls|means|stands for|contains)\b", re.I)

_HEDGE = re.compile(r"\b(maybe|perhaps|i think|i guess|i suppose|might|possibly|probably|"
                    r"not sure|for now|kind of|sort of|could try|let'?s try|try)\b", re.I)
_STRONG = re.compile(r"\b(always|never|from now on|every time|must|non-negotiable|make sure|"
                     r"without exception|in every|for all|under no circumstances|ever again|"
                     r"i always|i never|absolutely|mandatory|required)\b", re.I)

_GLOBAL = re.compile(r"\b(always|from now on|going forward|every project|all projects|any project|"
                     r"across projects|in general|generally|every time|by default|whenever|"
                     r"in every|for all (?:my )?(?:videos|projects|builds))\b", re.I)
_PROJECT = re.compile(r"\b(this project|this repo(?:sitory)?|this codebase|this build|this video|"
                      r"this page|this one|this time|here|in this|for now)\b", re.I)

_CHANGE = re.compile(r"\b(actually|instead|no longer|not anymore|any ?more|rather than|"
                     r"switch(?:ed)? (?:to|over)|change(?:d)? (?:it |that )?to|from now on|"
                     r"scratch that|forget|on second thought|replace|stop using|no more|"
                     r"going forward|correction)\b", re.I)


_PREAMBLE = re.compile(
    r"^\s*(?:(?:we|i)\s+(?:have\s+)?decided(?:\s+(?:to|on|that))?|decided(?:\s+(?:to|on|that))?|"
    r"let'?s\s+go\s+with|we'?ll\s+go\s+with|go(?:ing)?\s+with|we\s+chose|i\s+chose|"
    r"i\s+(?:would|'d)\s+(?:like|rather)(?:\s+you\s+to)?|i\s+(?:always\s+|never\s+)?want(?:\s+you\s+to)?|"
    r"i\s+prefer|i\s+like|i\s+love|maybe|perhaps|i\s+think|i\s+guess|probably|"
    r"next\s+time|lesson(?:\s+learned)?|from\s+now\s+on|going\s+forward|in\s+general|"
    r"by\s+default|for\s+this\s+(?:project|one|repo|build|video)|in\s+this\s+(?:project|repo))"
    r"\s*[,:;-]*\s*", re.I)


def core_text(text: str) -> str:
    """The statement minus decision/preference preambles, used only to find its subject."""
    t = str(text or "").strip()
    prev = None
    while prev != t:
        prev = t
        t = _PREAMBLE.sub("", t, count=1).strip()
    return t or str(text or "").strip()


def key_of(item) -> str:
    """Subject key of an entry (explicit `subject` wins) or a bare text, on its core text."""
    if isinstance(item, Mapping):
        subj = item.get("subject")
        if isinstance(subj, str) and _st._norm(subj):
            return _st._norm(subj)
        return subject_key({"text": core_text(str(item.get("text") or ""))})
    return subject_key({"text": core_text(str(item or ""))})


def _content_words(text: str) -> list:
    return re.findall(r"[a-z0-9']+", (text or "").lower())


def _phrase(text: str) -> tuple:
    """(head, modifiers, negative) mirroring store.subject_key's phrase walk."""
    neg = bool(_st._NEG.search(str(text or "").lower()))
    t = core_text(text)
    m = re.match(r"^\s*([^:\n]{1,40}):(.*)$", t)
    if m and 1 <= len(m.group(1).split()) <= 4:
        head = _st._norm(m.group(1))
        mods = {_st._singular(w) for w in _content_words(m.group(2))
                if w not in _st._LEAD_STOP and w not in _st._BOUNDARY}
        return head, mods, neg
    words = _content_words(t)
    i = 0
    while i < len(words) and words[i] in _st._LEAD_STOP:
        i += 1
    phrase = []
    while i < len(words) and len(phrase) < 3 and words[i] not in _st._BOUNDARY:
        if words[i] not in _st._LEAD_STOP:
            phrase.append(words[i])
        i += 1
    if not phrase:
        return _st._norm(t)[:40], set(), neg
    head = _st._singular(phrase[-1])
    mods = {_st._singular(w) for w in phrase[:-1]}
    return head, mods, neg


def agrees(a_text: str, b_text: str) -> bool:
    """Same subject, same polarity, compatible modifiers (see module docstring)."""
    ha, ma, na = _phrase(a_text)
    hb, mb, nb = _phrase(b_text)
    if ha != hb or na != nb:
        return False
    return ma <= mb or mb <= ma


def contrary(a_text: str, b_text: str) -> bool:
    """Same subject key and not agreeing."""
    return key_of(a_text) == key_of(b_text) and not agrees(a_text, b_text)


def _clearly_contrary(old_text: str, new_text: str, explicit_subject: bool) -> tuple:
    """(is_clear, why) — only clear disagreements are superseded automatically."""
    ho, mo, no = _phrase(old_text)
    hn, mn, nn = _phrase(new_text)
    if no != nn:
        return True, "opposite polarity on the same subject"
    if _CHANGE.search(new_text):
        return True, "explicit change marker in the newer statement"
    if mo and mn and not (mo <= mn or mn <= mo):
        return True, f"different {hn} ({', '.join(sorted(mo))} -> {', '.join(sorted(mn))})"
    if explicit_subject:
        return True, "newer statement on the same explicit subject"
    return False, "same subject, different statement (not clearly contrary)"


def clean_statement(statement: str) -> str:
    """Strip conversational fillers and trailing 'instead/please' so the stored text is the rule."""
    t = re.sub(r"\s+", " ", str(statement or "")).strip()
    prev = None
    while prev != t:
        prev = t
        t = _FILLER.sub("", t).strip()
    t = _TRAIL.sub("", t).strip()
    t = t.rstrip(" .!;?")
    if t:
        t = t[0].upper() + t[1:]
    return t


def classify(statement: str) -> dict:
    """Read-only classification of one statement. Deterministic (rule table above)."""
    raw = str(statement or "").strip()
    text = clean_statement(raw)
    out = {"memory_worthy": False, "type": None, "confidence": None, "scope_hint": None,
           "text": text, "subject": None, "why": ""}
    if not text or len(_content_words(text)) < 2:
        out["why"] = "too short to be a rule"
        return out
    if _WH_QUESTION.match(raw):
        out["why"] = "a question, not a stated preference or decision"
        return out
    if _LESSON.search(raw):
        typ, why = "lesson", "lesson marker"
    elif _DECISION.search(raw):
        typ, why = "decision", "decision marker"
    elif _PREFERENCE.search(raw):
        typ, why = "preference", "directive / preference marker"
    elif _FACT.search(raw):
        typ, why = "fact", "declarative statement about the system"
    else:
        typ, why = "preference", "no marker; treated as a preference"
    if _HEDGE.search(raw):
        conf = "low"
    elif _STRONG.search(raw):
        conf = "high"
    else:
        conf = "med"
    if typ == "preference" and why.startswith("no marker"):
        conf = "low"
    if _PROJECT.search(raw):
        hint = "project"
    elif _GLOBAL.search(raw):
        hint = "global"
    else:
        hint = "global" if typ == "preference" else "project"
    out.update(memory_worthy=True, type=typ, confidence=conf, scope_hint=hint,
               subject=key_of(text), why=why)
    return out


# ------------------------------------------------------------------- locking
_TLOCKS: dict = {}
_TLOCKS_GUARD = threading.Lock()


class _FileLock:
    """Exclusive advisory flock on `path` (opened O_APPEND|O_CREAT) + a per-path thread lock,
    so check-then-append sequences cannot race. Not re-entrant within one thread."""

    def __init__(self, path: Path):
        self.path = Path(path)
        with _TLOCKS_GUARD:
            self.tlock = _TLOCKS.setdefault(str(self.path), threading.Lock())
        self.fd = None

    def __enter__(self):
        self.tlock.acquire()
        try:
            if _fcntl is not None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.fd = os.open(str(self.path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
                _fcntl.flock(self.fd, _fcntl.LOCK_EX)
        except BaseException:
            self.tlock.release()
            raise
        return self

    def __exit__(self, *exc):
        try:
            if self.fd is not None:
                try:
                    _fcntl.flock(self.fd, _fcntl.LOCK_UN)
                finally:
                    os.close(self.fd)
        finally:
            self.tlock.release()
        return False


# ---------------------------------------------------------------- supersede
def supersede_if_contrary(store: Store, text: str, type: str, scope: str,
                          subject: Optional[str] = None, **fields) -> dict:
    """Append the entry; supersede a clearly contrary CURRENT same-scope entry (see docstring).
    Returns {"action", "entry", "superseded", "conflicts", "overrides", "reason"}."""
    key = key_of({"text": text, "subject": subject})
    res = {"action": "added", "entry": None, "superseded": None, "conflicts": [],
           "overrides": [], "reason": None}
    with _FileLock(store.entries_path):
        current = store.list() if type in SUPERSEDE_TYPES else []
        same, cross = [], []
        for e in current:  # newest first
            if e.get("type") not in SUPERSEDE_TYPES or key_of(e) != key:
                continue
            es = e.get("scope", "global")
            if es == scope:
                same.append(e)
            elif es == "global" or scope == "global":
                cross.append(e)
        # exact duplicate (same scope, same normalised text, same type): write nothing
        for e in same:
            if e.get("type") == type and _st._norm(e.get("text", "")) == _st._norm(text):
                res.update(action="duplicate", entry=e)
                return res
        target, why = None, None
        for e in same:
            if agrees(e.get("text", ""), text) and not subject:
                continue
            clear, w = _clearly_contrary(e.get("text", ""), text, bool(subject))
            if clear and target is None:
                target, why = e, w
            elif not clear:
                res["conflicts"].append({"entry": e, "why": w})
        for e in cross:
            if not agrees(e.get("text", ""), text):
                res["overrides"].append({"entry": e, "why": (
                    "project instruction overrides a global one here; the global one still "
                    "applies elsewhere" if scope != "global" else
                    "global instruction disagrees with a project-specific one; both kept")})
        kw = dict(fields)
        if subject:
            kw["subject"] = subject
        if res["overrides"]:
            kw["overrides"] = [o["entry"].get("id") for o in res["overrides"]]
        if target is not None:
            reason = (f"contrary instruction ({why}); newer statement supersedes "
                      f"{target.get('id')} from {str(target.get('date', ''))[:10]}")
            entry = store.add(type, text, scope=scope, supersedes=target.get("id"),
                              reason=reason, **kw)
            res.update(action="superseded", entry=entry, superseded=target, reason=reason)
        else:
            entry = store.add(type, text, scope=scope, **kw)
            res["entry"] = entry
            if res["conflicts"]:
                res["action"] = "conflict-surfaced"
    return res


# ------------------------------------------------------------------- capture
def _resolve_scope(scope: Optional[str], hint: Optional[str], repo) -> str:
    if scope in (None, "", "auto"):
        scope = hint or "global"
    if scope == "project":
        if repo is None:
            return "global"
        return project_scope(Path(repo).resolve())
    return scope


def capture(statement: str, source: str = "chat", scope: Optional[str] = None,
            repo: Union[str, Path, None] = None, subject: Optional[str] = None,
            home: Union[str, Path, None] = None, confidence: Optional[str] = None,
            type: Optional[str] = None) -> dict:
    """Classify + append one typed entry (see module docstring)."""
    cls = classify(statement)
    res = {"action": "ignored", "entry": None, "superseded": None, "conflicts": [],
           "overrides": [], "classified": cls, "project_decision": None}
    if not cls["memory_worthy"] and type is None:
        return res
    typ = type or cls["type"] or "preference"
    if typ not in CAPTURE_TYPES and typ not in _st.TYPES:
        raise ValueError(f"type must be one of {_st.TYPES}")
    text = cls["text"] or clean_statement(statement)
    if not text:
        return res
    sc = _resolve_scope(scope, cls["scope_hint"], repo)
    st = Store(home)
    out = supersede_if_contrary(
        st, text, typ, sc, subject=subject, source_quote=str(statement).strip(),
        source=source or "chat", confidence=confidence or cls["confidence"] or "med")
    res.update({k: out[k] for k in ("action", "entry", "superseded", "conflicts", "overrides")})
    # a project-scoped decision also lands in the project's decisions log (PLAYBOOK 0.2)
    if (typ == "decision" and repo is not None and sc.startswith("project:")
            and out["action"] != "duplicate"):
        pm = ProjectMemory(repo)
        sup = None
        if out["superseded"] is not None:  # the D- block that recorded the superseded entry
            marker = f"memory entry {out['superseded'].get('id')}"
            for d in pm.decisions():
                if d.get("change") == marker:
                    sup = d.get("id")
        d = pm.add_decision(ask=text, reason=f"stated in {source or 'chat'}",
                            change=f"memory entry {out['entry'].get('id')}",
                            source=source or "chat", supersedes=sup, quote=str(statement).strip())
        res["project_decision"] = d
    return res


# ------------------------------------------------------------------- promote
def _slug(scope: str) -> str:
    return scope[len("project:"):] if scope.startswith("project:") else scope


def promote_lessons(repo: Union[str, Path], home: Union[str, Path, None] = None,
                    min_in_project: int = 2, min_projects: int = 2) -> dict:
    """Failure log -> project lessons -> user-level lessons. Idempotent (see module docstring)."""
    pm = ProjectMemory(repo)
    fails = pm.failures()
    by_cc: dict = {}
    for r in fails:
        cc = r.get("cause_class")
        if isinstance(cc, str) and cc:
            by_cc.setdefault(cc, []).append(r)
    res = {"repo": str(pm.repo), "scope": pm.scope, "counts": {k: len(v) for k, v in by_cc.items()},
           "project_lessons_created": [], "project_lessons_existing": [],
           "failure_classes_registered": [], "user_lessons_created": [],
           "user_lessons_existing": []}
    if not by_cc:
        return res
    # 1. project lessons (>= min_in_project in this project)
    for cc in sorted(by_cc):
        rows = by_cc[cc]
        if len(rows) < max(1, int(min_in_project)):
            continue
        last = rows[-1]
        prevs = []
        for r in reversed(rows):
            p = str(r.get("prevention") or "").strip()
            if p and p not in prevs:
                prevs.append(p)
        evidence = f"{len(rows)} failures: " + "; ".join(
            f"{r.get('ts', '')} {r.get('what', '')} @ {r.get('where', '')}" for r in rows[-5:])
        les, created = pm.add_lesson_once(
            cc, pattern=f"{cc}: failed {len(rows)} times in this project — latest: "
                        f"{last.get('what', '')} ({last.get('rule', '')})",
            fix="; ".join(prevs[:2]), where=str(last.get("where", "")), source="promote",
            evidence=evidence)
        (res["project_lessons_created"] if created else res["project_lessons_existing"]).append(
            {"id": les.get("id"), "cause_class": cc})
    # 2. user store: register each class once per project; promote across projects
    st = Store(home)
    repo_s = str(pm.repo)
    with _FileLock(st.entries_path):
        entries = st.all_entries()
        registered = {(e.get("cause_class"), e.get("repo")) for e in entries
                      if e.get("type") == "failure" and e.get("promoted_from") == PROMOTE_SOURCE}
        for cc in sorted(by_cc):
            if (cc, repo_s) in registered:
                continue
            r0 = by_cc[cc][0]
            e = st.add("failure", f"Failure class {cc} seen in project {_slug(pm.scope)}: "
                                  f"{r0.get('what', '')} @ {r0.get('where', '')}",
                       scope=pm.scope, source_quote=str(r0.get("evidence", "")) or None,
                       source="reflect", confidence="high", subject=f"failure-class {cc}",
                       cause_class=cc, repo=repo_s, promoted_from=PROMOTE_SOURCE,
                       prevention=r0.get("prevention"),
                       source_refs=[f"{pm.failures_path}#{r0.get('ts', '')}"])
            entries.append(e)
            registered.add((cc, repo_s))
            res["failure_classes_registered"].append({"id": e["id"], "cause_class": cc})
        promoted = {e.get("cause_class"): e for e in entries
                    if e.get("type") == "lesson" and e.get("scope") == "global"
                    and e.get("promoted_from") == PROMOTE_SOURCE}
        for cc in sorted(by_cc):
            regs = [e for e in entries if e.get("type") == "failure"
                    and e.get("promoted_from") == PROMOTE_SOURCE and e.get("cause_class") == cc]
            repos = []
            for e in regs:
                if e.get("repo") not in [x.get("repo") for x in repos]:
                    repos.append(e)
            if cc in promoted:
                res["user_lessons_existing"].append({"id": promoted[cc].get("id"),
                                                     "cause_class": cc})
                continue
            if len(repos) < max(2, int(min_projects)):
                continue
            prevs = []
            for e in repos:
                p = str(e.get("prevention") or "").strip()
                if p and p not in prevs:
                    prevs.append(p)
            scopes = [e.get("scope") for e in repos]
            slugs = ", ".join(_slug(s) for s in scopes)
            text = (f"Recurring failure class {cc} across {len(repos)} projects ({slugs}). "
                    f"Prevention: {'; '.join(prevs[:2]) or 'see the project failure logs'}")
            les = st.add("lesson", text, scope="global",
                         source_quote=str(repos[0].get("source_quote") or "") or None,
                         source="reflect", confidence="high" if len(repos) >= 3 else "med",
                         subject=f"failure-class {cc}", cause_class=cc,
                         promoted_from=PROMOTE_SOURCE, projects=scopes,
                         source_refs=[ref for e in repos for ref in (e.get("source_refs") or [])]
                         + [e.get("id") for e in repos])
            entries.append(les)
            promoted[cc] = les
            res["user_lessons_created"].append({"id": les["id"], "cause_class": cc})
    return res


# ----------------------------------------------------------------------- CLI
def _strip(obj):
    """JSON-friendly view of a result (entries kept whole)."""
    return json.loads(json.dumps(obj, ensure_ascii=False, default=str))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Coordn8 memory curation")
    ap.add_argument("--home", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--json", action="store_true")
        p.add_argument("--home", default=argparse.SUPPRESS)
        return p

    p = common(sub.add_parser("classify")); p.add_argument("statement")
    p = common(sub.add_parser("capture")); p.add_argument("statement")
    p.add_argument("--source", default="chat"); p.add_argument("--scope")
    p.add_argument("--repo"); p.add_argument("--subject")
    p.add_argument("--type", choices=CAPTURE_TYPES)
    p = common(sub.add_parser("promote")); p.add_argument("--repo", required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "classify":
            out = classify(a.statement)
            print(json.dumps(out, indent=1) if a.json else
                  f"{out['type'] or 'ignored'} ({out['confidence']}, {out['scope_hint']}): "
                  f"{out['text']}  [{out['why']}]")
        elif a.cmd == "capture":
            out = capture(a.statement, source=a.source, scope=a.scope, repo=a.repo,
                          subject=a.subject, home=a.home, type=a.type)
            if a.json:
                print(json.dumps(_strip(out), ensure_ascii=False, indent=1))
            else:
                e = out["entry"] or {}
                print(f"{out['action']}: {e.get('id', '-')} {e.get('type', '')} "
                      f"{e.get('scope', '')} {e.get('text', '')}")
                if out["superseded"]:
                    print(f"  supersedes {out['superseded'].get('id')}: "
                          f"{out['superseded'].get('text')}")
                for c in out["conflicts"] + out["overrides"]:
                    print(f"  surfaced: {c['entry'].get('id')}: {c['entry'].get('text')} "
                          f"({c['why']})")
        elif a.cmd == "promote":
            out = promote_lessons(a.repo, home=a.home)
            print(json.dumps(out, ensure_ascii=False, indent=1) if a.json else
                  f"project lessons +{len(out['project_lessons_created'])}, "
                  f"failure classes +{len(out['failure_classes_registered'])}, "
                  f"user lessons +{len(out['user_lessons_created'])}")
    except (ValueError, KeyError, TypeError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
