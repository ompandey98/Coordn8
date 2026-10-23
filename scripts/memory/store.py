#!/usr/bin/env python3
"""Coordn8 user memory store — typed, append-only, never rewrites a line.

Lives in the USER zone (default ~/.coordn8, override COORDN8_HOME):

    memory/entries.jsonl     typed entries (preference, decision, lesson, failure, fact, choice)
    memory/choices.jsonl     per-build creative choices (anti-repetition history)
    memory/index.md          generated compact view for session start (hard cap 4 KB)
    library/inventions.jsonl invented palettes / kits / diagram forms / scenes

Guarantees (see docs/DATA_SAFETY.md):
  * APPEND-ONLY. Every write is one `os.write` of one complete JSON line to a file opened
    with O_APPEND. No existing byte of a .jsonl file is ever changed or removed. Superseding
    an entry appends a NEW entry that names the old one; the old line stays exactly as it was.
  * NEVER FATAL ON BAD DATA. A line that is not a JSON object is skipped and recorded in
    `Store.corrupt` (file, line number, first 80 chars). The rest of the file still loads.
  * A torn last line (no trailing newline, e.g. after a crash) is never merged with the next
    write: the next append starts with a newline so the new entry stays on its own line.
  * The constructor creates MISSING directories only (memory/, library/). It writes no file.
    `Store(home, create=False)` creates nothing at all (used by doctor, which must never write).

Entry shape (SPEC §3.7):
    {"id": "m-<12 hex>", "type": ..., "scope": "global" | "project:<slug>", "text": ...,
     "source_quote": ..., "source": "chat|build|reflect|migrate|cli|...", "date": ISO-8601,
     "confidence": "high|med|low", "supersedes": null | "<id>", "source_hash": null | "<sha256>",
     "source_path": null | "<path>"}               + optional "subject", "reason"

Current view: an entry is SUPERSEDED when any later entry names it in `supersedes`.
`list()` hides superseded entries unless include_superseded=True. Both stay on disk forever.

Scope view: `list(scope="project:b")` returns what applies while working in project b, i.e.
global entries PLUS project:b entries (a global preference stated in project a is therefore
visible in project b). `exact_scope=True` returns only that exact scope.

Conflict heuristic (K5) — documented because it is a heuristic, not a judgement:
  Two CURRENT entries of type preference or decision conflict when
    (1) their scopes overlap (either is global, or both are the same project), and
    (2) they have the same SUBJECT KEY, and
    (3) their normalised texts differ (identical text = a duplicate, not a conflict).
  SUBJECT KEY = the entry's explicit "subject" field, normalised (lower-case, non-alphanumerics
  collapsed to one space), when given. Otherwise the text's leading noun phrase: if the text
  starts "<= 4 words>: ..." the words before the colon; else drop leading directive/stop words
  ("always", "never", "use", "prefer", "don't", "a", "the", ...), take words up to the next
  stop word / preposition (max 3), and keep the HEAD noun (last word, crude plural stripped).
  So "Use a female narration voice" and "Never use a female voice" both key to "voice".
  The heuristic over-reports rather than under-reports: a surfaced conflict is a question for
  the owner, never an automatic resolution. Give "subject" explicitly for exact matching.

CLI (wired later as `coordn8 memory ...`):
    store.py list      [--type T] [--scope S] [--all] [--json]
    store.py add       <type> <text> [--scope S] [--quote Q] [--source S] [--confidence C]
                       [--subject S] [--source-path P] [--json]
    store.py supersede <old_id> <text> --reason R [--quote Q] [--scope S] [--json]
    store.py export    [--out FILE]                 (every entry incl. superseded, JSON)
    store.py index     [--max-bytes N] [--scope S]  (writes memory/index.md, prints path)
    store.py conflicts [--json]
    store.py stale     [--json]                     (hashes each source_path on disk)
  Every command accepts --home DIR (else COORDN8_HOME / ~/.coordn8).
Zero third-party deps; Python 3.9+.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import secrets
import sys
import tempfile
from pathlib import Path
from typing import Callable, Iterable, Mapping, Optional, Union

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from lib.paths import user_home  # noqa: E402

TYPES = ("preference", "decision", "lesson", "failure", "fact", "choice")
CONFIDENCE = ("high", "med", "low")
INDEX_TYPES = ("preference", "decision", "lesson")
CONFLICT_TYPES = ("preference", "decision")
CHOICE_KEYS = ("project", "build", "palette_family", "kit", "opening_scene", "spine", "voice")
_SCOPE_RE = re.compile(r"^(global|project:[a-z0-9][a-z0-9._-]*)$")

ENTRIES = "memory/entries.jsonl"
CHOICES = "memory/choices.jsonl"
INDEX = "memory/index.md"
INVENTIONS = "library/inventions.jsonl"


# ------------------------------------------------------------------ helpers
def now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def new_id() -> str:
    return "m-" + secrets.token_hex(6)


def project_scope(name: Union[str, Path]) -> str:
    """'My Repo_v2' or a repo path -> 'project:my-repo-v2'."""
    base = Path(str(name)).name if ("/" in str(name) or "\\" in str(name)) else str(name)
    slug = re.sub(r"[^a-z0-9._-]+", "-", base.lower()).strip("-._") or "unnamed"
    return f"project:{slug}"


def file_hash(path: Union[str, Path]) -> Optional[str]:
    """sha256 of a file's bytes, or None if it cannot be read."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 16), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


_LEAD_STOP = set("""
i we you they he she it me us my our your the a an this that these those
always never usually generally often sometimes please do does don't dont not no
should must shall will would can could may might need needs want wants like likes
prefer prefers preferred use uses using used avoid avoids keep keeps make makes
be is are was were been being have has had go with without more less very really
only just also all every any some each let lets stick stay start stop
""".split())
_BOUNDARY = set("""
for in on at to of from by with without over under into onto than then when while
because so but and or is are be should must will can as that which who whose
""".split())
_NEG = re.compile(r"\b(never|not|no|don't|dont|avoid|without|stop)\b")


def _singular(w: str) -> str:
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def subject_key(entry: Mapping) -> str:
    """The conflict key for an entry (see module docstring)."""
    subj = entry.get("subject")
    if isinstance(subj, str) and _norm(subj):
        return _norm(subj)
    text = str(entry.get("text") or "")
    m = re.match(r"^\s*([^:\n]{1,40}):", text)
    if m and 1 <= len(m.group(1).split()) <= 4:
        return _norm(m.group(1))
    words = re.findall(r"[a-z0-9']+", text.lower())
    i = 0
    while i < len(words) and words[i] in _LEAD_STOP:
        i += 1
    phrase = []
    while i < len(words) and len(phrase) < 3 and words[i] not in _BOUNDARY:
        if words[i] not in _LEAD_STOP:
            phrase.append(words[i])
        i += 1
    if not phrase:
        return _norm(text)[:40]
    return _singular(phrase[-1])


def _scopes_overlap(a: str, b: str) -> bool:
    return a == "global" or b == "global" or a == b


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".coordn8-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, str(path))
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def append_jsonl(path: Path, obj: Mapping, fsync: bool = False) -> None:
    """Append ONE JSON object as ONE line with ONE write. Never touches existing bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(obj, ensure_ascii=False, sort_keys=False) + "\n").encode("utf-8")
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        size = os.fstat(fd).st_size
        if size > 0:
            with open(path, "rb") as r:  # torn tail? start on a fresh line, never merge
                r.seek(size - 1)
                if r.read(1) != b"\n":
                    data = b"\n" + data
        os.write(fd, data)
        if fsync or os.environ.get("COORDN8_FSYNC") == "1":
            os.fsync(fd)
    finally:
        os.close(fd)


def read_jsonl(path: Path, corrupt: Optional[list] = None) -> list:
    """Every JSON-object line, in file order. Bad lines are skipped and reported."""
    out: list = []
    if not path.exists():
        return out
    with open(path, "rb") as f:
        raw = f.read()
    for n, line in enumerate(raw.split(b"\n"), 1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            obj = None
        if not isinstance(obj, dict):
            if corrupt is not None:
                corrupt.append({"file": str(path), "line": n,
                                "text": line[:80].decode("utf-8", "replace")})
            continue
        out.append(obj)
    return out


# --------------------------------------------------------------------- store
class Store:
    """Typed, append-only user memory. Thread/process safe for appends (O_APPEND)."""

    def __init__(self, home: Union[str, Path, None] = None, create: bool = True):
        self.home = Path(home).expanduser() if home else user_home()
        self.corrupt: list = []
        if create:
            for d in (self.home, self.home / "memory", self.home / "library"):
                if not d.exists():
                    d.mkdir(parents=True, exist_ok=True)

    # ---- paths
    @property
    def entries_path(self) -> Path:
        return self.home / ENTRIES

    @property
    def choices_path(self) -> Path:
        return self.home / CHOICES

    @property
    def index_path(self) -> Path:
        return self.home / INDEX

    @property
    def inventions_path(self) -> Path:
        return self.home / INVENTIONS

    # ---- raw reads
    def _read(self, path: Path) -> list:
        bad: list = []
        rows = read_jsonl(path, bad)
        seen = {(c["file"], c["line"]) for c in self.corrupt}
        self.corrupt.extend(c for c in bad if (c["file"], c["line"]) not in seen)
        return rows

    def all_entries(self) -> list:
        """Every entry ever written (superseded included), oldest first."""
        return self._read(self.entries_path)

    def get(self, entry_id: str) -> Optional[dict]:
        for e in self.all_entries():
            if e.get("id") == entry_id:
                return e
        return None

    @staticmethod
    def _superseded_ids(entries: Iterable[Mapping]) -> set:
        ids: set = set()
        for e in entries:
            s = e.get("supersedes")
            if isinstance(s, str) and s:
                ids.add(s)
            elif isinstance(s, list):
                ids.update(x for x in s if isinstance(x, str))
        return ids

    # ---- writes
    def add(self, type: str, text: str, scope: str = "global", source_quote: Optional[str] = None,
            source: str = "build", confidence: str = "med", supersedes: Optional[str] = None,
            source_hash: Optional[str] = None, source_path: Union[str, Path, None] = None,
            subject: Optional[str] = None, reason: Optional[str] = None, **extra) -> dict:
        if type not in TYPES:
            raise ValueError(f"type must be one of {TYPES}, got {type!r}")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text must be a non-empty string")
        if not _SCOPE_RE.match(scope or ""):
            raise ValueError(f"scope must be 'global' or 'project:<slug>' (lower-case), got {scope!r}")
        if confidence not in CONFIDENCE:
            raise ValueError(f"confidence must be one of {CONFIDENCE}, got {confidence!r}")
        if not isinstance(source, str) or not source:
            raise ValueError("source must be a non-empty string")
        if supersedes is not None and self.get(supersedes) is None:
            raise KeyError(f"cannot supersede unknown entry {supersedes!r}")
        sp = str(source_path) if source_path else None
        if sp and source_hash is None:
            source_hash = file_hash(sp)
        entry = {"id": new_id(), "type": type, "scope": scope, "text": text.strip(),
                 "source_quote": source_quote, "source": source, "date": now_iso(),
                 "confidence": confidence, "supersedes": supersedes, "source_hash": source_hash,
                 "source_path": sp}
        if subject:
            entry["subject"] = subject
        if reason:
            entry["reason"] = reason
        for k, v in extra.items():
            if k not in entry:
                entry[k] = v
        append_jsonl(self.entries_path, entry)
        return entry

    def supersede(self, old_id: str, text: str, reason: str, **kw) -> dict:
        """Append a NEW entry that replaces `old_id` in the current view. The old line is untouched.
        Type, scope and subject default to the old entry's."""
        old = self.get(old_id)
        if old is None:
            raise KeyError(f"no entry {old_id!r}")
        if not reason or not str(reason).strip():
            raise ValueError("a supersede needs a reason")
        kw.setdefault("type", old.get("type", "fact"))
        kw.setdefault("scope", old.get("scope", "global"))
        if old.get("subject"):
            kw.setdefault("subject", old["subject"])
        return self.add(text=text, supersedes=old_id, reason=str(reason).strip(), **kw)

    # ---- views
    def list(self, type: Optional[str] = None, scope: Optional[str] = None,
             include_superseded: bool = False, exact_scope: bool = False) -> list:
        """Current entries, NEWEST FIRST. scope='project:x' -> global + project:x (unless exact_scope)."""
        rows = self.all_entries()
        gone = set() if include_superseded else self._superseded_ids(rows)
        out = []
        for e in rows:
            if e.get("id") in gone:
                continue
            if type and e.get("type") != type:
                continue
            if scope:
                es = e.get("scope", "global")
                if exact_scope or scope == "global":
                    if es != scope:
                        continue
                elif es not in ("global", scope):
                    continue
            out.append(e)
        out.reverse()
        return out

    def history(self, entry_id: str) -> list:
        """The supersede chain through `entry_id`, oldest first."""
        rows = self.all_entries()
        by_id = {e.get("id"): e for e in rows}
        chain = []
        cur = by_id.get(entry_id)
        while cur is not None and cur not in chain:  # walk back
            chain.insert(0, cur)
            cur = by_id.get(cur.get("supersedes")) if isinstance(cur.get("supersedes"), str) else None
        tip = chain[-1] if chain else None
        while tip is not None:  # walk forward
            nxt = [e for e in rows if e.get("supersedes") == tip.get("id") and e not in chain]
            tip = nxt[0] if nxt else None
            if tip is not None:
                chain.append(tip)
        return chain

    def conflicts(self, detail: bool = False) -> list:
        """Pairs (older, newer) of current preference/decision entries that contradict (heuristic)."""
        cur = [e for e in self.list(include_superseded=False) if e.get("type") in CONFLICT_TYPES]
        cur.reverse()  # oldest first so pairs read (older, newer)
        out = []
        for i, a in enumerate(cur):
            ka = subject_key(a)
            for b in cur[i + 1:]:
                if not _scopes_overlap(a.get("scope", "global"), b.get("scope", "global")):
                    continue
                if subject_key(b) != ka or _norm(a.get("text", "")) == _norm(b.get("text", "")):
                    continue
                if detail:
                    pol = bool(_NEG.search(a.get("text", "").lower())) != bool(
                        _NEG.search(b.get("text", "").lower()))
                    out.append({"a": a, "b": b, "subject": ka,
                                "why": "opposite polarity on the same subject" if pol
                                else "different statements on the same subject"})
                else:
                    out.append((a, b))
        return out

    def conflicts_with(self, text: str, subject: Optional[str] = None,
                       scope: str = "global") -> list:
        """Current preference/decision entries that a NEW statement (e.g. from the live brief)
        would conflict with. Read-only: the caller surfaces them; nothing is resolved here."""
        probe = {"text": text, "subject": subject}
        key = subject_key(probe)
        return [e for e in self.list(scope=None)
                if e.get("type") in CONFLICT_TYPES
                and _scopes_overlap(e.get("scope", "global"), scope)
                and subject_key(e) == key and _norm(e.get("text", "")) != _norm(text)]

    def stale(self, file_hash_lookup: Union[Callable, Mapping, None] = None,
              include_superseded: bool = False) -> list:
        """Entries whose recorded source_hash differs from the current hash of source_path (K3).
        `file_hash_lookup` is a callable path->hash (None if missing) or a dict path->hash;
        default hashes the file on disk. A missing source counts as stale."""
        if file_hash_lookup is None:
            look = file_hash
        elif callable(file_hash_lookup):
            look = file_hash_lookup
        else:
            look = lambda p: file_hash_lookup.get(p)  # noqa: E731
        out = []
        for e in self.list(include_superseded=include_superseded):
            sp, sh = e.get("source_path"), e.get("source_hash")
            if not sp or not sh:
                continue
            now = look(sp)
            if now != sh:
                row = dict(e)
                row["stale_reason"] = "source missing" if now is None else "source changed"
                row["current_hash"] = now
                out.append(row)
        return out

    # ---- choices (H3 data)
    def append_choice(self, choice: Mapping) -> dict:
        if not isinstance(choice, Mapping):
            raise TypeError("choice must be a dict")
        row = {"ts": now_iso()}
        for k in CHOICE_KEYS:
            row[k] = choice.get(k)
        for k, v in choice.items():
            if k not in row:
                row[k] = v
        append_jsonl(self.choices_path, row)
        return row

    def recent_choices(self, n: int = 3) -> list:
        rows = self._read(self.choices_path)
        rows.reverse()
        return rows[:max(0, int(n))]

    # ---- inventions (H5 data)
    def add_invention(self, kind: str, name: str, spec, used_in, gates) -> dict:
        if not kind or not name:
            raise ValueError("an invention needs a kind and a name")
        row = {"ts": now_iso(), "id": "inv-" + secrets.token_hex(5), "kind": kind, "name": name,
               "spec": spec, "used_in": used_in, "gates": gates}
        append_jsonl(self.inventions_path, row)
        return row

    def inventions(self, kind: Optional[str] = None) -> list:
        rows = self._read(self.inventions_path)
        return [r for r in rows if kind is None or r.get("kind") == kind]

    @staticmethod
    def _gates_passed(g) -> bool:
        if isinstance(g, bool):
            return g
        if isinstance(g, str):
            return g.strip().upper() in ("PASS", "PASSED", "OK", "GREEN")
        if isinstance(g, Mapping):
            return bool(g) and all(Store._gates_passed(v) for v in g.values())
        if isinstance(g, (list, tuple)):
            return bool(g) and all(Store._gates_passed(v) for v in g)
        return False

    def promotion_candidates(self, min_builds: int = 2) -> list:
        """(kind, name) inventions that passed their gates in >= min_builds distinct builds."""
        uses: dict = {}
        for r in self.inventions():
            if not self._gates_passed(r.get("gates")):
                continue
            u = r.get("used_in")
            builds = u if isinstance(u, list) else [u]
            uses.setdefault((r.get("kind"), r.get("name")), set()).update(
                str(b) for b in builds if b)
        return [{"kind": k, "name": n, "builds": sorted(b)}
                for (k, n), b in sorted(uses.items(), key=lambda kv: str(kv[0]))
                if len(b) >= min_builds]

    # ---- generated index (session start)
    def render_index(self, max_bytes: int = 4096, scope: Optional[str] = None) -> str:
        cur = [e for e in self.list(scope=scope) if e.get("type") in INDEX_TYPES]  # newest first
        n_conf = len(self.conflicts())
        head = ["# Coordn8 memory (generated; do not edit)",
                "The live brief outranks everything here. Newest first; superseded entries hidden.",
                ""]
        if n_conf:
            head.append(f"! {n_conf} unresolved conflict(s): run `coordn8 memory conflicts`.")
            head.append("")
        titles = {"preference": "## Preferences", "decision": "## Decisions", "lesson": "## Lessons"}
        chosen: dict = {t: [] for t in INDEX_TYPES}

        def build(extra_footer: str) -> str:
            parts = list(head)
            for t in INDEX_TYPES:
                if chosen[t]:
                    parts.append(titles[t])
                    parts.extend(chosen[t])
                    parts.append("")
            parts.append(extra_footer)
            return "\n".join(parts) + "\n"

        def line(e: Mapping) -> str:
            txt = re.sub(r"\s+", " ", str(e.get("text", ""))).strip()
            if len(txt) > 160:
                txt = txt[:157].rstrip() + "..."
            sc = e.get("scope", "global")
            tag = "" if sc == "global" else f"[{sc[len('project:'):]}] "
            return f"- {tag}{txt} ({e.get('id', '?')}, {str(e.get('date', ''))[:10]})"

        shown = 0
        for e in cur:
            chosen[e["type"]].append(line(e))
            footer = f"({len(cur) - shown - 1} older entries not shown: `coordn8 memory list`)"
            if len(build(footer).encode("utf-8")) > max_bytes:
                chosen[e["type"]].pop()
                break
            shown += 1
        rest = len(cur) - shown
        footer = (f"({rest} older entries not shown: `coordn8 memory list`)" if rest
                  else f"({shown} entries)")
        text = build(footer)
        b = text.encode("utf-8")
        if len(b) > max_bytes:  # tiny caps: hard truncate on a character boundary
            text = b[:max_bytes].decode("utf-8", "ignore")
        return text

    def write_index(self, max_bytes: int = 4096, scope: Optional[str] = None) -> Path:
        _atomic_write_text(self.index_path, self.render_index(max_bytes, scope))
        return self.index_path


# ----------------------------------------------------------------------- CLI
def _print(obj, as_json: bool) -> None:
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=1))
    elif isinstance(obj, list):
        for e in obj:
            if isinstance(e, dict) and "text" in e:
                sup = f"  (supersedes {e['supersedes']})" if e.get("supersedes") else ""
                print(f"{e.get('id')}  {e.get('type'):<10} {e.get('scope'):<20} {e.get('text')}{sup}")
            else:
                print(json.dumps(e, ensure_ascii=False))
    else:
        print(obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Coordn8 user memory (append-only)")
    ap.add_argument("--home", default=None, help="user-data dir (default COORDN8_HOME or ~/.coordn8)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--json", action="store_true")
        p.add_argument("--home", default=argparse.SUPPRESS)
        return p

    p = common(sub.add_parser("list"))
    p.add_argument("--type"); p.add_argument("--scope"); p.add_argument("--all", action="store_true")
    p = common(sub.add_parser("add"))
    p.add_argument("type", choices=TYPES); p.add_argument("text")
    p.add_argument("--scope", default="global"); p.add_argument("--quote")
    p.add_argument("--source", default="cli"); p.add_argument("--confidence", default="med", choices=CONFIDENCE)
    p.add_argument("--subject"); p.add_argument("--source-path")
    p = common(sub.add_parser("supersede"))
    p.add_argument("old_id"); p.add_argument("text"); p.add_argument("--reason", required=True)
    p.add_argument("--quote"); p.add_argument("--scope"); p.add_argument("--source", default="cli")
    p.add_argument("--confidence", default="med", choices=CONFIDENCE)
    p = common(sub.add_parser("export")); p.add_argument("--out")
    p = common(sub.add_parser("index"))
    p.add_argument("--max-bytes", type=int, default=4096); p.add_argument("--scope")
    common(sub.add_parser("conflicts"))
    common(sub.add_parser("stale"))
    a = ap.parse_args(argv)
    try:
        st = Store(a.home)
        if a.cmd == "list":
            _print(st.list(type=a.type, scope=a.scope, include_superseded=a.all), a.json)
        elif a.cmd == "add":
            _print(st.add(a.type, a.text, scope=a.scope, source_quote=a.quote, source=a.source,
                          confidence=a.confidence, subject=a.subject, source_path=a.source_path),
                   a.json)
        elif a.cmd == "supersede":
            kw = {"source_quote": a.quote, "source": a.source, "confidence": a.confidence}
            if a.scope:
                kw["scope"] = a.scope
            _print(st.supersede(a.old_id, a.text, a.reason, **kw), a.json)
        elif a.cmd == "export":
            data = json.dumps({"schema": "coordn8-memory-export/1", "exported": now_iso(),
                               "entries": st.all_entries(), "choices": st.recent_choices(10 ** 9),
                               "inventions": st.inventions(), "corrupt_lines": st.corrupt},
                              ensure_ascii=False, indent=1)
            if a.out:
                Path(a.out).write_text(data + "\n", encoding="utf-8")
                print(a.out)
            else:
                print(data)
        elif a.cmd == "index":
            path = st.write_index(a.max_bytes, a.scope)
            _print({"path": str(path), "bytes": path.stat().st_size} if a.json else str(path), a.json)
        elif a.cmd == "conflicts":
            rows = st.conflicts(detail=True)
            if a.json:
                _print(rows, True)
            elif not rows:
                print("no conflicts")
            else:
                for r in rows:
                    print(f"[{r['subject']}] {r['why']}:\n  {r['a']['id']}: {r['a']['text']}\n"
                          f"  {r['b']['id']}: {r['b']['text']}")
        elif a.cmd == "stale":
            _print(st.stale(), a.json)
        for c in st.corrupt:
            print(f"warning: skipped corrupt line {c['file']}:{c['line']}: {c['text']!r}",
                  file=sys.stderr)
    except (ValueError, KeyError, TypeError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
