#!/usr/bin/env python3
"""Coordn8 PROJECT-level memory — the per-project half of the two-level memory (K1).

Everything lives under `<repo>/Coordn8/_research/` (USER-OWNED; see docs/DATA_SAFETY.md):

    decisions.md        append-only decisions & feedback log (PLAYBOOK 0.2): one block per
                        entry with a sequence number, timestamp, the ask, the reason, the change
    slop_lessons.md     append-only lessons (PLAYBOOK R.5), loaded FIRST on every later pass
    failure_log.jsonl   one failure record per line (SPEC 3.6, requirement F4)
    requirements.md     the requirement-coverage ledger (PLAYBOOK 0.7a) — READ ONLY here

Guarantees:
  * APPEND-ONLY. Every write is one `os.write` to a file opened with O_APPEND. No existing byte
    of any project file is ever changed or removed. A decision that reverses an earlier one is
    a NEW block that names the old one in `supersedes` (both stay on disk; K5).
  * Writes happen only under `<repo>/Coordn8/_research/`. The constructor writes nothing; the
    directory is created on the first write.
  * A torn last line / block (crash mid-write) is never merged with the next write.
  * Hand-written content already in decisions.md / slop_lessons.md is kept and still read:
    top-level bullet lines outside a structured block load as legacy entries (seq None).

Block format (decisions.md):

    ## D-0003 · 2026-10-23T10:00:00+05:30
    - ask: use blue for model judgement
    - reason: the owner stated it in the chat
    - change: palette role model-judgement -> blue
    - source: chat
    - supersedes: D-0001            (only when it reverses an earlier decision)

Block format (slop_lessons.md):

    ## L-0002 · 2026-10-23T10:00:00+05:30
    - pattern: ...
    - where: ...
    - fix: ...
    - cause_class: layout          (set for lessons promoted from the failure log)
    - source: promote|critic|user
    - evidence: 2 failures: <ts> <what> @ <where>; ...

Failure record (SPEC 3.6): required what, where, rule, cause_class, evidence, fix_tried,
outcome, prevention; `ts` (default now), `attempt` (default 1), `promoted` (default false) are
filled in. `cause_class` must be in data/cause_taxonomy.json when that file exists, else in the
built-in F4 taxonomy below; a class outside it is accepted only as `invented:<slug>` together
with a non-empty `invented_reason` (H1: a taxonomy is a menu, an invention must say why).
Because the log is append-only, `promoted` is never flipped in place: promotion is recorded as
a lesson (project) or a user-level memory entry, and that record is what makes it idempotent.

Public API (stable; the reflect loop and recall call these):
    ProjectMemory(repo)                                  writes nothing until asked to
      .add_decision(ask, reason, change="", source="chat", supersedes=None, quote=None) -> dict
      .decisions(current_only=False) -> list           .recent_decisions(n=5) -> list
      .add_lesson(pattern, fix="", where="", cause_class=None, source="critic", evidence="")
      .add_lesson_once(cause_class, pattern, ...) -> (lesson, created)   idempotent per class
      .lessons() -> list
      .log_failure(record) -> dict                     validates SPEC 3.6, appends one line
      .failures(cause_class=None) -> list              .failure_counts() -> {class: n}
      .requirements() -> list                          .open_items() -> list   (read only)
      .files() -> list                                 .scope -> "project:<slug>"

Zero third-party deps; Python 3.9+.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Iterable, Mapping, Optional, Union

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from lib.paths import PKG_ROOT, project_research  # noqa: E402
from memory.store import now_iso, read_jsonl  # noqa: E402

try:  # advisory lock so two writers never take the same sequence number
    import fcntl as _fcntl  # type: ignore
except ImportError:  # pragma: no cover - non-POSIX
    _fcntl = None

DECISIONS = "decisions.md"
LESSONS = "slop_lessons.md"
FAILURES = "failure_log.jsonl"
REQUIREMENTS = "requirements.md"

# F4's fixed cause taxonomy, used when data/cause_taxonomy.json is absent.
BUILTIN_CAUSES = ("input-missing", "wrong-assumption", "stale-artefact", "budget", "tool-absent",
                  "wrong-choice:palette", "wrong-choice:diagram", "wrong-choice:scene",
                  "writing-rule", "timing", "layout", "misread-brief")
FAILURE_REQUIRED = ("what", "where", "rule", "cause_class", "evidence", "fix_tried", "outcome",
                    "prevention")
OUTCOMES = ("pass", "fail", "escalated", "aborted")

_DEC_HEAD = re.compile(r"^##\s+D-(\d+)\s*(?:[·|-]\s*(.*))?$")
_LES_HEAD = re.compile(r"^##\s+L-(\d+)\s*(?:[·|-]\s*(.*))?$")
_FIELD = re.compile(r"^\s*-\s+([a-z_]+):\s?(.*)$")
_BULLET = re.compile(r"^(?:[-*]|\d+[.)])\s+(.+)$")

DECISIONS_HEADER = ("# Decisions & feedback log\n\n"
                    "Append-only (PLAYBOOK 0.2). Every design decision and every change the user "
                    "asked for, with its reason. A reversal is a new entry that names the old one "
                    "in `supersedes`; nothing here is ever edited or removed.\n")
LESSONS_HEADER = ("# Slop lessons\n\n"
                  "Append-only (PLAYBOOK R.5). Loaded FIRST on every later pass and every rebuild; "
                  "design these flaws out up front.\n")


def load_taxonomy(path: Union[str, Path, None] = None) -> tuple:
    """Allowed cause_class values: data/cause_taxonomy.json if present and readable, else F4's list.
    Accepts {"classes"|"values"|"cause_classes"|"causes"|"taxonomy": [str | {"id": str}] or
    {id: {...}}}, a bare {id: {...}} mapping, or a bare list."""
    p = Path(path) if path else PKG_ROOT / "data" / "cause_taxonomy.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return BUILTIN_CAUSES
    if isinstance(data, dict):
        lst = (data.get("classes") or data.get("values") or data.get("cause_classes")
               or data.get("causes") or data.get("taxonomy"))
        if isinstance(lst, dict):  # {"<id>": {...}, ...}
            lst = list(lst)
        if lst is None:  # a bare {"<id>": {...}} mapping (ignoring a "schema" key)
            lst = [k for k, v in data.items() if k != "schema" and isinstance(v, dict)]
        data = lst
    out = []
    for it in data if isinstance(data, list) else []:
        v = it.get("id") if isinstance(it, dict) else it
        if isinstance(v, str) and v.strip():
            out.append(v.strip())
    return tuple(out) or BUILTIN_CAUSES


def _one_line(v) -> str:
    """Field values are single-line by construction so a value can never forge a block heading."""
    if v is None:
        return ""
    if not isinstance(v, str):
        v = json.dumps(v, ensure_ascii=False)
    return re.sub(r"\s+", " ", v).strip()


def _append_bytes(path: Path, data: bytes, sep: bytes = b"\n") -> None:
    """One O_APPEND write. If the file's tail does not already end in `sep`, the write starts with
    enough newlines to keep the new content separate (a torn tail is never merged)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        size = os.fstat(fd).st_size
        if size > 0:
            with open(path, "rb") as r:
                r.seek(max(0, size - len(sep)))
                tail = r.read()
            # pad with the fewest newlines that make the tail end in `sep`
            for k in range(len(sep) + 1):
                if (tail + b"\n" * k).endswith(sep):
                    data = b"\n" * k + data
                    break
        os.write(fd, data)
        if os.environ.get("COORDN8_FSYNC") == "1":
            os.fsync(fd)
    finally:
        os.close(fd)


class _Lock:
    """Advisory exclusive flock on the target file itself (no extra lock file is created)."""

    def __init__(self, path: Path):
        self.path = path
        self.fd = None

    def __enter__(self):
        if _fcntl is None:
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(str(self.path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        _fcntl.flock(self.fd, _fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        if self.fd is not None:
            try:
                _fcntl.flock(self.fd, _fcntl.LOCK_UN)
            finally:
                os.close(self.fd)
        return False


def _parse_blocks(text: str, head_re, prefix: str) -> list:
    """Structured `## X-NNNN · ts` blocks + legacy top-level bullets outside any block."""
    out: list = []
    cur: Optional[dict] = None
    for raw in text.splitlines():
        line = raw.rstrip()
        m = head_re.match(line)
        if m:
            cur = {"seq": int(m.group(1)), "id": f"{prefix}-{int(m.group(1)):04d}",
                   "ts": (m.group(2) or "").strip(), "legacy": False}
            out.append(cur)
            continue
        if line.startswith("#"):
            cur = None  # any other heading ends a block
            continue
        if cur is not None:
            f = _FIELD.match(line)
            if f:
                cur[f.group(1)] = f.group(2).strip()
                continue
            if not line.strip():
                continue
            cur = None  # free text after a block: back to legacy mode
        b = _BULLET.match(line)
        if b and not raw.startswith((" ", "\t")):
            out.append({"seq": None, "id": None, "ts": "", "legacy": True, "text": b.group(1).strip()})
    return out


class ProjectMemory:
    """Per-project memory under <repo>/Coordn8/_research/. Writing nothing until asked to."""

    def __init__(self, repo: Union[str, Path]):
        self.repo = Path(repo).resolve()
        self.root = project_research(self.repo)
        self.corrupt: list = []

    # ---- paths
    @property
    def decisions_path(self) -> Path:
        return self.root / DECISIONS

    @property
    def lessons_path(self) -> Path:
        return self.root / LESSONS

    @property
    def failures_path(self) -> Path:
        return self.root / FAILURES

    @property
    def requirements_path(self) -> Path:
        return self.root / REQUIREMENTS

    def _read_text(self, p: Path) -> str:
        try:
            return p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    # ---- decisions (PLAYBOOK 0.2)
    def decisions(self, current_only: bool = False) -> list:
        """Every decision block in file order (legacy bullets included, seq None).
        current_only=True hides blocks that a later block names in `supersedes`."""
        rows = _parse_blocks(self._read_text(self.decisions_path), _DEC_HEAD, "D")
        if current_only:
            gone = {r.get("supersedes") for r in rows if r.get("supersedes")}
            rows = [r for r in rows if r.get("id") not in gone]
        return rows

    def add_decision(self, ask: str, reason: str, change: str = "", source: str = "chat",
                     supersedes: Optional[str] = None, quote: Optional[str] = None,
                     ts: Optional[str] = None) -> dict:
        if not _one_line(ask):
            raise ValueError("a decision needs the ask")
        if not _one_line(reason):
            raise ValueError("a decision needs a reason")
        with _Lock(self.decisions_path):
            existing = self.decisions()
            if supersedes is not None and not any(r.get("id") == supersedes for r in existing):
                raise KeyError(f"cannot supersede unknown decision {supersedes!r}")
            seq = max([r["seq"] for r in existing if r.get("seq")] or [0]) + 1
            row = {"seq": seq, "id": f"D-{seq:04d}", "ts": ts or now_iso(), "ask": _one_line(ask),
                   "reason": _one_line(reason), "change": _one_line(change),
                   "source": _one_line(source) or "chat"}
            if quote:
                row["quote"] = _one_line(quote)
            if supersedes:
                row["supersedes"] = supersedes
            lines = [f"## {row['id']} · {row['ts']}"]
            for k in ("ask", "reason", "change", "source", "quote", "supersedes"):
                if row.get(k):
                    lines.append(f"- {k}: {row[k]}")
            block = "\n".join(lines) + "\n"
            if not self.decisions_path.exists() or self.decisions_path.stat().st_size == 0:
                block = DECISIONS_HEADER + "\n" + block
            _append_bytes(self.decisions_path, block.encode("utf-8"), sep=b"\n\n")
        row["legacy"] = False
        return row

    # ---- lessons (PLAYBOOK R.5)
    def lessons(self) -> list:
        """Every lesson in file order. Promoted (structured) lessons carry cause_class."""
        rows = _parse_blocks(self._read_text(self.lessons_path), _LES_HEAD, "L")
        for r in rows:
            if not r.get("text"):
                r["text"] = r.get("pattern", "")
        return rows

    def _append_lesson(self, existing: list, pattern: str, fix: str, where: str,
                       cause_class: Optional[str], source: str, evidence: str,
                       ts: Optional[str]) -> dict:
        """Append one lesson block. Caller holds the lessons lock and passes the parsed file."""
        seq = max([r["seq"] for r in existing if r.get("seq")] or [0]) + 1
        row = {"seq": seq, "id": f"L-{seq:04d}", "ts": ts or now_iso(),
               "pattern": _one_line(pattern), "where": _one_line(where), "fix": _one_line(fix),
               "cause_class": _one_line(cause_class), "source": _one_line(source) or "critic",
               "evidence": _one_line(evidence)}
        lines = [f"## {row['id']} · {row['ts']}"]
        for k in ("pattern", "where", "fix", "cause_class", "source", "evidence"):
            if row.get(k):
                lines.append(f"- {k}: {row[k]}")
        block = "\n".join(lines) + "\n"
        if not self.lessons_path.exists() or self.lessons_path.stat().st_size == 0:
            block = LESSONS_HEADER + "\n" + block
        _append_bytes(self.lessons_path, block.encode("utf-8"), sep=b"\n\n")
        row["legacy"] = False
        row["text"] = row["pattern"]
        return row

    def add_lesson(self, pattern: str, fix: str = "", where: str = "",
                   cause_class: Optional[str] = None, source: str = "critic",
                   evidence: str = "", ts: Optional[str] = None) -> dict:
        if not _one_line(pattern):
            raise ValueError("a lesson needs the pattern")
        with _Lock(self.lessons_path):
            return self._append_lesson(self.lessons(), pattern, fix, where, cause_class, source,
                                       evidence, ts)

    def add_lesson_once(self, cause_class: str, pattern: str, fix: str = "", where: str = "",
                        source: str = "promote", evidence: str = "",
                        ts: Optional[str] = None) -> tuple:
        """Idempotent lesson for a cause class: (lesson, created). If ANY lesson already carries
        this cause_class (promoted earlier, or written by a critic), nothing is written and that
        lesson is returned. The check and the append happen under one lock, so two concurrent
        promoters cannot both write it."""
        cc = _one_line(cause_class)
        if not cc:
            raise ValueError("add_lesson_once needs a cause_class")
        if not _one_line(pattern):
            raise ValueError("a lesson needs the pattern")
        with _Lock(self.lessons_path):
            existing = self.lessons()
            for r in existing:
                if r.get("cause_class") == cc:
                    return r, False
            return self._append_lesson(existing, pattern, fix, where, cc, source, evidence,
                                       ts), True

    # ---- failure log (F4, SPEC 3.6)
    def validate_failure(self, record: Mapping, taxonomy: Optional[Iterable] = None) -> dict:
        if not isinstance(record, Mapping):
            raise TypeError("a failure record must be a dict")
        missing = [k for k in FAILURE_REQUIRED if not _one_line(record.get(k))]
        if missing:
            raise ValueError(f"failure record is missing {missing}")
        allowed = tuple(taxonomy) if taxonomy is not None else load_taxonomy()
        cc = str(record["cause_class"]).strip()
        if cc not in allowed:
            if not (cc.startswith("invented:") and len(cc) > len("invented:")
                    and _one_line(record.get("invented_reason"))):
                raise ValueError(f"cause_class {cc!r} is not in the taxonomy {list(allowed)}; "
                                 "use one of them, or 'invented:<slug>' with an invented_reason")
        if str(record["outcome"]).strip() not in OUTCOMES:
            raise ValueError(f"outcome must be one of {OUTCOMES}")
        row = {"ts": record.get("ts") or now_iso()}
        for k in FAILURE_REQUIRED:
            row[k] = record[k] if not isinstance(record[k], str) else record[k].strip()
        att = record.get("attempt", 1)
        try:
            row["attempt"] = int(att)
        except (TypeError, ValueError):
            raise ValueError("attempt must be an integer")
        row["promoted"] = bool(record.get("promoted", False))
        for k, v in record.items():
            if k not in row:
                row[k] = v
        return row

    def log_failure(self, record: Mapping) -> dict:
        row = self.validate_failure(record)
        data = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
        _append_bytes(self.failures_path, data, sep=b"\n")
        return row

    def failures(self, cause_class: Optional[str] = None) -> list:
        bad: list = []
        rows = read_jsonl(self.failures_path, bad)
        seen = {(c["file"], c["line"]) for c in self.corrupt}
        self.corrupt.extend(c for c in bad if (c["file"], c["line"]) not in seen)
        if cause_class is not None:
            rows = [r for r in rows if r.get("cause_class") == cause_class]
        return rows

    # ---- requirements ledger (PLAYBOOK 0.7a) — read only
    def requirements(self) -> list:
        """Rows of requirements.md, parsed by the content-coverage gate's own parser
        (one reader for one file), each with `state` covered|partial|open|deferred|out."""
        text = self._read_text(self.requirements_path)
        if not text:
            return []
        try:
            from gates.content_coverage import (_truthy_scope, classify_importance,  # noqa
                                                classify_status, parse_ledger)
        except ImportError:  # pragma: no cover - the gate ships with the package
            return []
        cols, rows = parse_ledger(text)
        out = []
        for r in rows:
            row = dict(r)
            row["state"] = classify_status(r.get("status", ""))
            row["importance_class"] = classify_importance(r.get("importance", ""))
            row["in_scope_bool"] = _truthy_scope(r.get("in_scope", "")) if "in_scope" in cols else True
            out.append(row)
        return out

    def open_items(self) -> list:
        """In-scope must/should rows that are not covered (open or partial). Deferred and
        out-of-scope rows are not open items; edge rows never block."""
        return [r for r in self.requirements()
                if r["in_scope_bool"] and r["importance_class"] != "edge"
                and r["state"] in ("open", "partial")]

    @property
    def scope(self) -> str:
        """The user-store scope for this project, e.g. 'project:my-repo'."""
        from memory.store import project_scope
        return project_scope(self.repo)

    def recent_decisions(self, n: int = 5) -> list:
        """The n newest CURRENT decisions (superseded ones hidden), newest first."""
        rows = self.decisions(current_only=True)
        rows.reverse()
        return rows[:max(0, int(n))]

    def failure_counts(self) -> dict:
        """cause_class -> number of failure records in this project's log."""
        out: dict = {}
        for r in self.failures():
            cc = r.get("cause_class")
            if isinstance(cc, str) and cc:
                out[cc] = out.get(cc, 0) + 1
        return out

    def files(self) -> list:
        """Every file under _research/ (for audits: project memory never writes elsewhere)."""
        if not self.root.exists():
            return []
        return sorted(p for p in self.root.rglob("*") if p.is_file())


if __name__ == "__main__":  # tiny read-only CLI: project.py <repo> [decisions|lessons|failures|open]
    import argparse

    ap = argparse.ArgumentParser(description="Coordn8 project memory (read-only view)")
    ap.add_argument("repo")
    ap.add_argument("what", choices=("decisions", "lessons", "failures", "open"))
    a = ap.parse_args()
    pm = ProjectMemory(a.repo)
    data = {"decisions": pm.decisions, "lessons": pm.lessons, "failures": pm.failures,
            "open": pm.open_items}[a.what]()
    print(json.dumps(data, ensure_ascii=False, indent=1))
