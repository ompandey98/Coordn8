#!/usr/bin/env python3
"""Coordn8 failure analysis and self-correction loop (requirement F4).

Every failed gate, refused budget, aborted render or rejected agent output becomes ONE record in
`<repo>/Coordn8/_research/failure_log.jsonl` (SPEC 3.6), written through the project memory
(`memory.project.ProjectMemory.log_failure`, which validates the record and the cause class
against `data/cause_taxonomy.json`). The supervisor then:

  1. asks `attempt_policy(item)` whether the item may run again (max 2 retries per item:
     attempts 1, 2, 3; the third failure is recorded as `escalated` and no 4th attempt is
     allowed — `record` refuses it and `wrap` refuses to run the command);
  2. appends `retry_prompt(item)` to the retry brief, so the retry carries every prior record
     for that item: what failed, where, the rule, the cause class, the evidence, the fix
     already tried, and the prevention;
  3. after each record, `memory.curate.promote_lessons(repo)` runs: a cause class seen twice in
     one project becomes a project lesson (loaded first next run); seen in two or more projects
     it becomes exactly one user-level lesson (idempotent).

`classify(gate_result)` maps the JSON a gate, `orchestrate.py`, `run_gates.py` or
`verify_manifest.py` prints into a cause class DETERMINISTICALLY: an ordered rule table keyed on
the gate name, the status and patterns in the message. The first matching rule wins; nothing
is judged by a model. When no rule matches the answer is `wrong-assumption` with LOW confidence
and `flagged: true`, so a person or the failure-analyst agent re-reads it — never a silent guess.

Writes: only `<repo>/Coordn8/_research/` (project memory) and the user store
(`COORDN8_HOME`, default ~/.coordn8) through `memory.curate`. Both are append-only.

CLI:
    reflect.py record   --item I --what W --where X --rule R --cause C --evidence E
                        --fix-tried F [--prevention P] [--outcome fail|aborted]
                        [--invented-reason T] [--from-gate FILE|-]   [--repo DIR] [--json]
    reflect.py classify FILE|-                                       [--json]
    reflect.py retry-prompt --item I                                 [--repo DIR] [--json]
    reflect.py status                                                [--repo DIR] [--json]
    reflect.py promote                                               [--repo DIR] [--json]
    reflect.py wrap [--item I] [--fix-tried F] [--repo DIR] [--json] -- <command ...>
Every command accepts --home DIR (else COORDN8_HOME / ~/.coordn8).

Exit codes: 0 ok · 1 the wrapped command failed and was recorded · 3 bad input ·
4 ESCALATED (the item used all its attempts; nothing was run or written).
`wrap` returns the wrapped command's own exit code when it ran.

Zero third-party deps; Python 3.9+.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Mapping, Optional, Union

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from lib.paths import PKG_ROOT  # noqa: E402
from memory.project import ProjectMemory, _Lock, load_taxonomy  # noqa: E402

MAX_RETRIES = 2
MAX_ATTEMPTS = MAX_RETRIES + 1
EXIT_OK, EXIT_FAILED, EXIT_BAD_INPUT, EXIT_ESCALATED = 0, 1, 3, 4
TAXONOMY_PATH = PKG_ROOT / "data" / "cause_taxonomy.json"
FALLBACK_CLASS = "wrong-assumption"

RC_STATUS = {0: "PASS", 1: "REVIEW", 2: "SKIPPED", 3: "ERROR", 124: "TIMEOUT", 127: "NO-TOOL"}
FAIL_STATUSES = {"REVIEW", "ERROR", "TIMEOUT", "NO-TOOL", "MISSING", "FAIL", "FAILED",
                 "REJECT", "REJECTED", "ABORTED", "CONTRADICTS"}
_TABLE_LINE = re.compile(r"^\s*(\S+)\s+(PASS|REVIEW|SKIPPED|ERROR|TIMEOUT|NO-TOOL|MISSING|\?\d+)"
                         r"\s+(.*)$")


class AttemptsExhausted(RuntimeError):
    """The item already used every attempt (or was escalated). Nothing was written."""


# ------------------------------------------------------------------------- taxonomy
def taxonomy_details(path: Union[str, Path, None] = None) -> dict:
    """{id: {description, typical_evidence, default_prevention}} for every allowed class.
    Classes the file does not describe (e.g. the built-in list when the file is absent) get
    empty fields, never an error."""
    ids = load_taxonomy(path)
    p = Path(path) if path else TAXONOMY_PATH
    found: dict = {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        for c in data.get("classes", []) if isinstance(data, dict) else []:
            if isinstance(c, dict) and c.get("id"):
                found[c["id"]] = c
    except (OSError, ValueError, AttributeError):
        pass
    return {i: {"description": str(found.get(i, {}).get("description", "")),
                "typical_evidence": list(found.get(i, {}).get("typical_evidence", []) or []),
                "default_prevention": str(found.get(i, {}).get("default_prevention", ""))}
            for i in ids}


# --------------------------------------------------------------------- normalisation
def _load_any(obj):
    """Text -> JSON object(s) or a run_gates text table; anything else passes through."""
    if not isinstance(obj, str):
        return obj
    s = obj.strip()
    if not s:
        return {"gate": "?", "status": "ERROR", "summary": "empty output"}
    try:
        return json.loads(s)
    except ValueError:
        pass
    found = []
    for line in s.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                found.append(json.loads(line))
                continue
            except ValueError:
                pass
        m = _TABLE_LINE.match(line)
        if m:
            found.append({"gate": m.group(1), "status": m.group(2), "summary": m.group(3)})
    if found:
        return found
    return {"gate": "?", "status": "FAIL", "summary": s[-600:]}


def gate_results(obj) -> list:
    """Flatten any supported shape into a list of {gate, status, summary, ...} dicts:
    a single gate JSON, an orchestrate gate entry (with `payload`), an orchestrate stage
    record (`gates`), an orchestrate run report (`stages`), a list, a run_gates text table,
    or an agent verdict ({agent|role, verdict, why})."""
    obj = _load_any(obj)
    out: list = []
    if isinstance(obj, list):
        for x in obj:
            out.extend(gate_results(x))
        return out
    if not isinstance(obj, dict):
        return [{"gate": "?", "status": "FAIL", "summary": str(obj)[:600]}]
    if isinstance(obj.get("stages"), list):
        for st in obj["stages"]:
            for g in (st.get("gates") or []) if isinstance(st, dict) else []:
                r = gate_results(g)
                for x in r:
                    x.setdefault("stage", st.get("stage"))
                out.extend(r)
        return out
    if isinstance(obj.get("gates"), list) and "gate" not in obj:
        for g in obj["gates"]:
            r = gate_results(g)
            for x in r:
                if obj.get("stage"):
                    x.setdefault("stage", obj.get("stage"))
            out.extend(r)
        return out
    if "gate" not in obj and (obj.get("agent") or obj.get("role")) and obj.get("verdict"):
        v = str(obj.get("verdict")).strip().upper()
        return [{"gate": f"agent:{obj.get('agent') or obj.get('role')}",
                 "status": v if v in FAIL_STATUSES else ("PASS" if v in ("PASS", "ACCEPT", "OK",
                                                                         "MATCH") else "REJECT"),
                 "summary": str(obj.get("why") or obj.get("reason") or obj.get("summary") or ""),
                 "findings": obj.get("findings") or obj.get("defects") or [],
                 "verdict": obj.get("verdict")}]
    r = dict(obj)
    payload = r.pop("payload", None)
    if isinstance(payload, dict):
        merged = dict(payload)
        merged.update({k: v for k, v in r.items() if v not in (None, "")})
        merged["gate"] = r.get("gate") or payload.get("gate") or "?"
        r = merged
    r["gate"] = str(r.get("gate") or "?")
    if not r.get("status"):
        rc = r.get("returncode")
        r["status"] = RC_STATUS.get(rc, "FAIL") if isinstance(rc, int) else "FAIL"
    r["status"] = str(r["status"]).upper()
    r["summary"] = str(r.get("summary") or "")
    return [r]


def _flatten_text(v, depth: int = 0) -> list:
    if depth > 4 or v is None:
        return []
    if isinstance(v, str):
        return [v]
    if isinstance(v, (int, float, bool)):
        return [str(v)]
    if isinstance(v, Mapping):
        out = []
        for k, x in v.items():
            if isinstance(x, (str, int, float)) and not isinstance(x, bool):
                out.append(f"{k}={x}" if k not in ("detail", "message", "summary", "why")
                           else str(x))
            else:
                out.extend(_flatten_text(x, depth + 1))
        return out
    if isinstance(v, (list, tuple)):
        out = []
        for x in v[:50]:
            out.extend(_flatten_text(x, depth + 1))
        return out
    return [str(v)]


def _findings_text(r: Mapping) -> list:
    out = []
    for key in ("findings", "still_open", "overclaimed", "semantic_unauthorized", "defects",
                "verdicts"):
        out.extend(_flatten_text(r.get(key)))
    return out


def _codes(r: Mapping) -> list:
    """Nonzero S1-S8 / C1-C8 counts, highest first (ties: blocking S1/S5/S6 first)."""
    counts = r.get("counts") if isinstance(r.get("counts"), Mapping) else {}
    prio = {"S1": 0, "S5": 1, "S6": 2}
    codes = [(k, v) for k, v in counts.items()
             if re.fullmatch(r"[SC][1-8]", str(k)) and isinstance(v, (int, float)) and v > 0]
    codes.sort(key=lambda kv: (-kv[1], prio.get(kv[0], 9), kv[0]))
    if not codes:  # fall back to codes named in the findings text ("S5: ...")
        seen = []
        for t in _findings_text(r) + [r.get("summary", "")]:
            for m in re.finditer(r"\b([SC][1-8])\b", str(t)):
                if m.group(1) not in seen:
                    seen.append(m.group(1))
        return seen
    return [k for k, _ in codes]


def _check_names(r: Mapping) -> list:
    names = []
    for f in r.get("findings") or [] if isinstance(r.get("findings"), list) else []:
        if isinstance(f, Mapping):
            n = f.get("check") or f.get("rule") or f.get("id") or f.get("code")
            if n and str(n) not in names:
                names.append(str(n))
    counts = r.get("counts") if isinstance(r.get("counts"), Mapping) else {}
    for k, v in counts.items():
        if isinstance(v, (int, float)) and v > 0 and str(k) not in names:
            names.append(str(k))
    return names


# ------------------------------------------------------------------------ rule table
# Ordered; the first matching rule wins. Each rule: id, gate regex (searched in the lower-case
# gate name, None = any gate), not_gate regex (rule skipped for these gates), statuses (None = any), text regex (searched in summary +
# findings, case-insensitive, None = any), cause class, confidence. `code_map` rules resolve
# the class from the dominant S#/C# code of the semantic / invariant gates.
SEMANTIC_CODES = {
    "S1": "timing",                # non-finite alpha from a timing expression -> invisible
    "S2": "timing",                # raw boundary index past the narration lines
    "S3": "layout",                # text escapes its own container
    "S4": "layout",                # text sits on a drawn connector
    "S5": "wrong-choice:scene",    # asset subject differs from the beat's subject
    "S6": "wrong-choice:diagram",  # arrow / path contradicts the declared direction
    "S7": "wrong-choice:scene",    # a declared section renders nothing
    "S8": "wrong-choice:scene",    # unexplained ink loss vs the last approved cut
    "C1": "layout",                # panels overlap / legible text collides
    "C2": "input-missing",         # a declared asset never draws
    "C3": "layout",                # cursor parked in dead space
    "C5": "input-missing",         # no // SRC: provenance comment
    "C6": "timing",                # sustained void while a line is spoken
    "C7": "layout",                # panel below the legibility scale floor
    "C8": "wrong-choice:scene",    # asset from another subsystem
}

_TOOL_RX = (r"\b(ffmpeg|ffprobe|chrom(e|ium)|playwright|manim|piper|espeak|tts|browser|node|"
            r"cairo|pango)\b[^.;\n]{0,40}\b(not found|not installed|missing|unavailable|absent|"
            r"could not be (found|installed))|\bcommand not found\b|\bno (headless )?"
            r"(chrome|chromium|browser)\b|\bnot installed\b|\bno such (tool|command|executable)\b"
            r"|\bgate script not found\b|\bexecutable not found\b")
_STALE_RX = (r"\bstale\b|\bolder than\b|\bout of date\b|\bfreshness\b|\boutdated\b|"
             r"\bnot newer than\b|\bretracted\b|\bhash (changed|mismatch)\b")
_INPUT_RX = (r"\bno such file\b|\bnot found\b|\bmissing\b|\bno [\w .-]{0,40}\bat\b|"
             r"\bcannot read\b|\bcould not read\b|\bdoes not exist\b|\bempty\b|\bno input\b|"
             r"\bnothing to (judge|check)\b|\bhas no\b")
_BUDGET_RX = (r"\b(time|disk|memory|token|render|worker|cpu|ram) budget\b|\bbudget "
              r"(refused|exceeded)\b|\bover (the |its )?(time |disk |render )?budget\b|"
              r"\bno space left\b|\bout of (disk|memory)\b|\brefused\b[^.;\n]{0,30}\bbudget\b|"
              r"\btimed? ?out\b|\bexceeded its time\b")

RULES: list = [
    # environment and inputs first: a gate that could not judge says nothing about a choice
    {"id": "R01-timeout", "status": {"TIMEOUT"}, "cause": "budget", "conf": "high"},
    {"id": "R02-no-tool", "status": {"NO-TOOL", "MISSING"}, "cause": "tool-absent",
     "conf": "high"},
    {"id": "R03-tool-text", "status": {"SKIPPED", "ERROR", "FAIL", "FAILED", "ABORTED"},
     "text": _TOOL_RX, "cause": "tool-absent", "conf": "high"},
    {"id": "R04-stale", "not_gate": r"^(beat_index|check_indices)\b", "text": _STALE_RX,
     "cause": "stale-artefact", "conf": "high"},
    {"id": "R05-input", "status": {"SKIPPED", "ERROR", "FAIL", "FAILED"}, "text": _INPUT_RX,
     "cause": "input-missing", "conf": "high"},
    {"id": "R06-budget-gate", "gate": r"^(check_)?(disk|perf)\b", "cause": "budget",
     "conf": "high"},
    # gate-specific outcomes
    {"id": "R10-palette", "gate": r"^(check_)?palette\b|agent:palette", "cause":
     "wrong-choice:palette", "conf": "high"},
    {"id": "R11-a11y-contrast", "gate": r"^check_page_a11y\b", "text": r"contrast",
     "cause": "wrong-choice:palette", "conf": "high"},
    {"id": "R12-a11y", "gate": r"^check_page_a11y\b", "cause": "layout", "conf": "med"},
    {"id": "R13-diagram", "gate": r"^(check_)?(diagram_budget|diagram_plan|tree_shape|"
     r"connectors)\b|agent:diagram", "cause": "wrong-choice:diagram", "conf": "high"},
    {"id": "R14-codes", "gate": r"^(check_)?(semantics|invariants)\b", "code_map": True,
     "conf": "high"},
    {"id": "R15-scene", "gate": r"^(check_)?(visual_density|density|continuity|stage_v)\b|"
     r"agent:(frame|storyboard|blind)", "cause": "wrong-choice:scene", "conf": "high"},
    {"id": "R16-layout", "gate": r"^(layout_probe|dom_smoke|check_layout|layout)\b",
     "cause": "layout", "conf": "high"},
    {"id": "R17-captions-text", "gate": r"^subtitle_check\b", "text": r"verbatim|diverge|"
     r"text (differs|mismatch)|language|mojibake", "cause": "writing-rule", "conf": "med"},
    {"id": "R18-timing", "gate": r"^(beat_sync|beat_index|check_indices|check_word_timing|"
     r"subtitle_check)\b", "cause": "timing", "conf": "high"},
    {"id": "R19-media-duration", "gate": r"^media_probe\b", "text": r"duration|fps|drift",
     "cause": "timing", "conf": "med"},
    {"id": "R20-writing", "gate": r"^(content_ban|content_integrity|check_content|language|"
     r"check_language|terms|check_terms|check_explain|check_page_content|check_caption_marks)"
     r"\b|agent:(writing|register|repetition)", "cause": "writing-rule", "conf": "high"},
    {"id": "R21-coverage", "gate": r"^content_coverage\b", "cause": "misread-brief",
     "conf": "high"},
    {"id": "R22-manifest-missing", "gate": r"^verify_manifest\b", "text": r"\bMISSING from "
     r"the manifest\b", "cause": "input-missing", "conf": "high"},
    {"id": "R23-claims", "gate": r"^(facts_trace|check_truth|check_claims|truth)\b|"
     r"agent:claim", "cause": "wrong-assumption", "conf": "high"},
    {"id": "R24-overclaim", "gate": r"^verify_manifest\b", "text": r"OVERCLAIM",
     "cause": "wrong-assumption", "conf": "high"},
    {"id": "R25-manifest", "gate": r"^verify_manifest\b", "cause": "wrong-assumption",
     "conf": "med"},
    {"id": "R26-determinism", "gate": r"^(check_)?(determinism|freedom)\b",
     "cause": "wrong-assumption", "conf": "med"},
    {"id": "R27-idea", "gate": r"agent:idea", "cause": "wrong-choice:scene", "conf": "med"},
    # message patterns for gates the table does not name (medium confidence)
    {"id": "R40-budget-text", "text": _BUDGET_RX, "cause": "budget", "conf": "med"},
    {"id": "R41-contrast", "text": r"\bcontrast\b|\boff-palette\b|\bpalette\b|\baccent\b",
     "cause": "wrong-choice:palette", "conf": "med"},
    {"id": "R42-layout", "text": r"\boverlap|\boverflow|\bsafe.?area\b|\bclip(ped|s)?\b|"
     r"\bcollid|\bcollision\b|\bmisalign|\bfont.?size\b|\btoo small\b|\bout of frame\b",
     "cause": "layout", "conf": "med"},
    {"id": "R43-timing", "text": r"\bsync\b|\bahead of\b|\bdrift|\bmistim|\bout of sync\b|"
     r"\bcue\b|\blags?\b|\bbeat\b", "cause": "timing", "conf": "med"},
    {"id": "R44-writing", "text": r"\brepeat|\bn-?gram\b|\bjargon\b|\bbanned\b|\bfiller\b|"
     r"\breading level\b|\bsentence\b|\bwording\b|\bnarration\b", "cause": "writing-rule",
     "conf": "med"},
    {"id": "R45-brief", "text": r"\bbrief\b|\brequirement\b|\bin-scope\b|\bnot covered\b|"
     r"\basked for\b|\bout of scope\b", "cause": "misread-brief", "conf": "med"},
    {"id": "R46-diagram", "text": r"\bdiagram\b|\bconnector\b|\barrow\b|\bedge label\b|"
     r"\bnode count\b", "cause": "wrong-choice:diagram", "conf": "med"},
    {"id": "R47-scene", "text": r"\bscene\b|\bstoryboard\b|\bprimary visual\b|\bdead space\b|"
     r"\bstatic\b", "cause": "wrong-choice:scene", "conf": "med"},
    {"id": "R48-input-text", "text": _INPUT_RX, "cause": "input-missing", "conf": "low"},
]


def _rule_matches(rule: Mapping, gate: str, status: str, text: str) -> bool:
    if rule.get("gate") and not re.search(rule["gate"], gate, re.I):
        return False
    if rule.get("not_gate") and re.search(rule["not_gate"], gate, re.I):
        return False
    if rule.get("status") is not None and status not in rule["status"]:
        return False
    if rule.get("text") and not re.search(rule["text"], text, re.I):
        return False
    return True


def _evidence(r: Mapping, limit: int = 600) -> str:
    parts = []
    if r.get("summary"):
        parts.append(str(r["summary"]))
    for t in _findings_text(r)[:6]:
        t = re.sub(r"\s+", " ", t).strip()
        if t and t not in parts:
            parts.append(t)
    s = " | ".join(parts) or f"{r.get('gate')} {r.get('status')}"
    return s if len(s) <= limit else s[: limit - 1] + "…"


def classify_one(r: Mapping) -> dict:
    """Classify ONE normalised gate result. Deterministic: same input, same answer."""
    gate = str(r.get("gate") or "?")
    status = str(r.get("status") or "FAIL").upper()
    texts = [str(r.get("summary") or "")] + _findings_text(r)
    if r.get("stderr"):
        texts.append(str(r["stderr"]))
    text = "\n".join(texts)
    checks = _check_names(r)
    base = {"gate": gate, "status": status, "stage": r.get("stage"),
            "what": f"{gate} {'gate' if not gate.startswith('agent:') else 'output'} {status}",
            "rule": f"{gate}: {', '.join(checks[:4])}" if checks else gate,
            "evidence": _evidence(r), "checks": checks}
    if status == "PASS":
        return dict(base, cause_class=None, confidence=None, flagged=False, rule_id=None,
                    failing=False, reason="the result passed; nothing to classify")
    for rule in RULES:
        if not _rule_matches(rule, gate.lower(), status, text):
            continue
        if rule.get("code_map"):
            codes = [c for c in _codes(r) if c in SEMANTIC_CODES]
            if not codes:
                continue
            cc = SEMANTIC_CODES[codes[0]]
            return dict(base, cause_class=cc, confidence=rule["conf"], flagged=False,
                        rule_id=f"{rule['id']}:{codes[0]}", failing=True,
                        rule=f"{gate}: {', '.join(codes[:4])}",
                        reason=f"dominant code {codes[0]} maps to {cc}")
        return dict(base, cause_class=rule["cause"], confidence=rule["conf"], flagged=False,
                    rule_id=rule["id"], failing=True,
                    reason=f"rule {rule['id']} matched gate {gate!r} status {status}")
    return dict(base, cause_class=FALLBACK_CLASS, confidence="low", flagged=True,
                rule_id="R99-fallback", failing=True,
                reason="no rule matched: recorded as wrong-assumption with low confidence and "
                       "FLAGGED for the failure analyst to re-read")


def classify(gate_result_json) -> dict:
    """Map gate / orchestrate / run_gates / verify_manifest / agent-verdict failure output to a
    cause class. Accepts a dict, a list, or the raw text a command printed.

    Returns the classification of the FIRST failing result, plus `results` (one classification
    per failing result, in input order) and `failing_count`. When nothing failed,
    `cause_class` is None and `failing` is False."""
    results = gate_results(gate_result_json)
    single = len(results) == 1
    picked = []
    for r in results:
        st = str(r.get("status") or "").upper()
        if st in FAIL_STATUSES or (single and st not in ("PASS",)):
            picked.append(classify_one(r))
    if not picked:
        return {"cause_class": None, "confidence": None, "flagged": False, "failing": False,
                "failing_count": 0, "results": [],
                "reason": "no failing result in the input"}
    out = dict(picked[0])
    out["failing_count"] = len(picked)
    out["results"] = picked
    return out


# --------------------------------------------------------------------- record / policy
def item_key(rec: Mapping) -> str:
    return str(rec.get("item") or rec.get("where") or "?")


def _item_records(item: str, records: Iterable[Mapping]) -> list:
    """This item's records since its last `pass` record (a pass closes the item)."""
    rows = [r for r in records if item_key(r) == item]
    last_pass = max([i for i, r in enumerate(rows) if r.get("outcome") == "pass"] or [-1])
    return rows[last_pass + 1:]


def attempt_policy(item: str, repo: Union[str, Path, None] = None,
                   records: Optional[Iterable[Mapping]] = None) -> dict:
    """May this item run again? Max 2 retries: attempts 1, 2, 3. After the third failure (or any
    `escalated` record) the action is `escalate`, allowed is False, and the log is attached."""
    if records is None:
        records = ProjectMemory(repo or ".").failures()
    rows = _item_records(item, records)
    n = len(rows)
    escalated = n >= MAX_ATTEMPTS or any(r.get("outcome") == "escalated" for r in rows)
    if escalated:
        action = "escalate"
    elif n == 0:
        action = "run"
    else:
        action = "retry"
    return {"item": item, "attempts_used": n, "next_attempt": None if escalated else n + 1,
            "max_attempts": MAX_ATTEMPTS, "max_retries": MAX_RETRIES,
            "retries_used": max(0, min(n, MAX_ATTEMPTS) - 1),
            "retry_number": (n if action == "retry" else None),
            "action": action, "allowed": not escalated, "escalated": escalated,
            "log": rows if (escalated or n) else []}


def _promote(repo, home) -> dict:
    from memory.curate import promote_lessons
    return promote_lessons(repo, home=home)


def record(repo: Union[str, Path], item: str, what: str, where: str, rule: str,
           cause_class: str, evidence: str, fix_tried: str, prevention: Optional[str] = None,
           outcome: str = "fail", home: Union[str, Path, None] = None,
           extra: Optional[Mapping] = None, promote: bool = True) -> dict:
    """Write one SPEC 3.6 record through ProjectMemory.log_failure, then promote lessons.

    The attempt number is derived from the item's earlier records. The third failure is
    written with outcome `escalated`. A further record for an escalated item is REFUSED
    (AttemptsExhausted) and nothing is written. An unknown cause_class raises ValueError
    (invented:<slug> needs extra={'invented_reason': ...})."""
    if outcome not in ("fail", "aborted", "escalated"):
        raise ValueError("record() logs failures: outcome must be fail, aborted or escalated")
    pm = ProjectMemory(repo)
    details = taxonomy_details()
    cc = str(cause_class or "").strip()
    if not prevention:
        prevention = details.get(cc, {}).get("default_prevention") or (
            "no default prevention for this class yet — the failure analyst writes one")
    rec = {"item": str(item or where or "?"), "what": what, "where": where, "rule": rule,
           "cause_class": cc, "evidence": evidence, "fix_tried": fix_tried,
           "outcome": outcome, "prevention": prevention}
    for k, v in (extra or {}).items():
        if k not in rec:
            rec[k] = v
    pm.validate_failure(dict(rec, attempt=1))  # reject bad input before touching the log
    with _Lock(pm.failures_path):
        policy = attempt_policy(rec["item"], records=pm.failures())
        if not policy["allowed"]:
            raise AttemptsExhausted(
                f"item {rec['item']!r} already used {policy['attempts_used']} attempt(s) and was "
                f"escalated; no further attempt is allowed — hand the log to the owner")
        rec["attempt"] = policy["attempts_used"] + 1
        if rec["attempt"] >= MAX_ATTEMPTS:
            rec["outcome"] = "escalated"
        row = pm.log_failure(rec)
    after = attempt_policy(rec["item"], records=pm.failures())
    promotion = _promote(repo, home) if promote else None
    return {"record": row, "policy": after, "escalated": after["escalated"],
            "promotion": promotion, "log": str(pm.failures_path)}


def record_from_gate(repo, gate_output, item: Optional[str] = None,
                     fix_tried: Optional[str] = None, home=None, where: Optional[str] = None,
                     outcome: str = "fail", promote: bool = True) -> Optional[dict]:
    """Classify gate output and record its first failing result. None when nothing failed."""
    c = classify(gate_output)
    if not c.get("failing"):
        return None
    it = item or c.get("gate") or "?"
    res = record(repo, item=it, what=c["what"], where=where or it, rule=c["rule"],
                 cause_class=c["cause_class"], evidence=c["evidence"],
                 fix_tried=fix_tried or "none recorded (captured automatically by reflect)",
                 outcome=outcome, home=home, promote=promote,
                 extra={"classifier": {"rule_id": c["rule_id"], "confidence": c["confidence"],
                                       "flagged": c["flagged"], "gate": c["gate"],
                                       "status": c["status"],
                                       "failing_count": c["failing_count"]}})
    res["classification"] = c
    return res


# ------------------------------------------------------------------------ retry prompt
def _line(v) -> str:
    if v is None:
        return ""
    if not isinstance(v, str):
        v = json.dumps(v, ensure_ascii=False)
    return re.sub(r"\s+", " ", v).strip()


def retry_prompt(item: str, records: Optional[Iterable[Mapping]] = None,
                 repo: Union[str, Path, None] = None) -> str:
    """The brief addendum the supervisor appends to the retry brief for `item`: every prior
    record (what failed, where, rule, cause, evidence, fix tried, outcome, prevention), the
    fixes not to repeat, and the project lessons for those causes. Empty string when the item
    has no failure record."""
    pm = ProjectMemory(repo or ".")
    all_rows = list(records) if records is not None else pm.failures()
    rows = _item_records(item, all_rows)
    if not rows:
        return ""
    pol = attempt_policy(item, records=all_rows)
    details = taxonomy_details()
    out = [f"## Retry brief addendum — {_line(item)} (failure analysis, F4)", ""]
    if pol["escalated"]:
        out.append(f"ESCALATED: this item failed {pol['attempts_used']} time(s), the limit is "
                   f"{MAX_ATTEMPTS} attempts ({MAX_RETRIES} retries). Do NOT run it again. Hand "
                   f"the owner the log below and ask how to proceed.")
    else:
        out.append(f"This item failed {pol['attempts_used']} time(s). This is attempt "
                   f"{pol['next_attempt']} of {MAX_ATTEMPTS} (retry {pol['retry_number']} of "
                   f"{MAX_RETRIES}). Read every record below before changing anything. Do not "
                   f"repeat a fix that already failed; remove the cause, then apply the "
                   f"prevention. A third failure escalates to the owner.")
    for r in rows:
        cc = _line(r.get("cause_class"))
        desc = details.get(cc, {}).get("description", "")
        out += ["", f"### Attempt {r.get('attempt', '?')} — outcome {_line(r.get('outcome'))}"
                    f" — {_line(r.get('ts'))}",
                f"- what failed: {_line(r.get('what'))}",
                f"- where: {_line(r.get('where'))}",
                f"- rule: {_line(r.get('rule'))}",
                f"- cause class: {cc}" + (f" — {_line(desc)}" if desc else ""),
                f"- evidence: {_line(r.get('evidence'))}",
                f"- fix tried: {_line(r.get('fix_tried'))}",
                f"- prevention: {_line(r.get('prevention'))}"]
        cl = r.get("classifier")
        if isinstance(cl, Mapping) and cl.get("flagged"):
            out.append("- note: the cause class was a low-confidence fallback — re-read the "
                       "evidence and correct the class if it is wrong")
    tried = []
    for r in rows:
        f = _line(r.get("fix_tried"))
        if f and f not in tried:
            tried.append(f)
    out += ["", "### Fixes already tried (do not repeat them unchanged)"]
    out += [f"- {f}" for f in tried]
    causes = []
    for r in rows:
        c = _line(r.get("cause_class"))
        if c and c not in causes:
            causes.append(c)
    lessons = ([le for le in pm.lessons() if le.get("cause_class") in causes]
               if (records is None or repo is not None) else [])
    if lessons:
        out += ["", "### Lessons already learned for these causes"]
        out += [f"- {_line(le.get('pattern'))} — fix: {_line(le.get('fix'))}" for le in lessons]
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------------------ status
def status(repo: Union[str, Path]) -> dict:
    pm = ProjectMemory(repo)
    rows = pm.failures()
    items: list = []
    for r in rows:
        k = item_key(r)
        if k not in items:
            items.append(k)
    per = []
    for k in items:
        pol = attempt_policy(k, records=rows)
        last = [r for r in rows if item_key(r) == k][-1]
        per.append({"item": k, "attempts": pol["attempts_used"], "action": pol["action"],
                    "escalated": pol["escalated"], "last_cause": last.get("cause_class"),
                    "last_outcome": last.get("outcome"), "last_ts": last.get("ts")})
    return {"repo": str(pm.repo), "log": str(pm.failures_path), "records": len(rows),
            "items": per, "escalations": [p for p in per if p["escalated"]],
            "counts": pm.failure_counts(),
            "project_lessons": [{"id": le.get("id"), "cause_class": le.get("cause_class")}
                                for le in pm.lessons() if le.get("cause_class")],
            "corrupt_lines": pm.corrupt}


# ---------------------------------------------------------------------------------- wrap
def wrap(cmd: list, repo: Union[str, Path], item: Optional[str] = None,
         fix_tried: Optional[str] = None, home=None, echo: bool = True) -> dict:
    """Run a gate command. Exit 0 -> nothing is written. Non-zero -> classify its output and
    record it. An escalated item is not run at all (returncode EXIT_ESCALATED)."""
    name = item or _cmd_item(cmd)
    pol = attempt_policy(name, repo=repo)
    if not pol["allowed"]:
        return {"ran": False, "returncode": EXIT_ESCALATED, "item": name, "policy": pol,
                "recorded": None,
                "reason": "escalated: the item used every attempt; nothing was run"}
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True)
        rc, out, err = cp.returncode, cp.stdout, cp.stderr
    except OSError as e:
        rc, out, err = 127, "", f"could not run {cmd[0] if cmd else '?'}: command not found ({e})"
    if echo:
        if out:
            sys.stdout.write(out)
        if err:
            sys.stderr.write(err)
    if rc == 0:
        return {"ran": True, "returncode": 0, "item": name, "recorded": None, "policy": pol}
    gate = Path(cmd[0]).name if cmd else "?"
    for a in cmd[1:]:
        if a.endswith((".py", ".js", ".sh")):
            gate = Path(a).stem
            break
    parsed = _load_any(out) if (out or "").strip() else None
    results = gate_results(parsed) if parsed is not None else []
    for r in results:
        if r.get("gate") in (None, "", "?"):
            r["gate"] = gate
    if not results or all(str(r.get("status", "")).upper() == "PASS" for r in results):
        results = [{"gate": gate, "status": RC_STATUS.get(rc, "FAIL"),
                    "summary": ((err or "").strip().splitlines() or [f"exit code {rc}"])[-1][:400],
                    "stderr": (err or "")[-1500:]}]
    else:
        for r in results:
            if r.get("status") in (None, "", "PASS") and len(results) == 1:
                r["status"] = RC_STATUS.get(rc, "FAIL")
            r.setdefault("stderr", (err or "")[-1500:])
    res = record_from_gate(repo, results, item=name, fix_tried=fix_tried, home=home)
    if res is None:  # the command failed but printed only passes: record the exit code itself
        res = record_from_gate(repo, [{"gate": name, "status": RC_STATUS.get(rc, "FAIL"),
                                       "summary": f"exit code {rc}", "stderr": err[-1500:]}],
                               item=name, fix_tried=fix_tried, home=home)
    return {"ran": True, "returncode": rc, "item": name, "recorded": res,
            "policy": res["policy"] if res else pol}


def _cmd_item(cmd: list) -> str:
    for a in cmd:
        if a.endswith((".py", ".js", ".sh")):
            return f"cmd:{Path(a).stem}"
    return f"cmd:{Path(cmd[0]).name}" if cmd else "cmd:?"


# ----------------------------------------------------------------------------------- CLI
def _dump(obj, as_json: bool, text: str = "") -> None:
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=1, default=str))
    else:
        print(text or json.dumps(obj, ensure_ascii=False, indent=1, default=str))


def _read_input(src: str) -> str:
    return sys.stdin.read() if src == "-" else Path(src).read_text(encoding="utf-8")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    tail: list = []
    if "--" in argv:
        i = argv.index("--")
        argv, tail = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(description="Coordn8 failure analysis and self-correction (F4)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--repo", default=".")
        p.add_argument("--home", default=None)
        p.add_argument("--json", action="store_true")
        return p

    pr = common(sub.add_parser("record"))
    for f in ("item", "what", "where", "rule", "cause", "evidence", "fix-tried", "prevention",
              "invented-reason", "from-gate"):
        pr.add_argument(f"--{f}", default=None)
    pr.add_argument("--outcome", default="fail", choices=("fail", "aborted"))
    pc = sub.add_parser("classify")
    pc.add_argument("input", nargs="?", default="-")
    pc.add_argument("--json", action="store_true")
    pp = common(sub.add_parser("retry-prompt"))
    pp.add_argument("--item", required=True)
    common(sub.add_parser("status"))
    common(sub.add_parser("promote"))
    pw = common(sub.add_parser("wrap"))
    pw.add_argument("--item", default=None)
    pw.add_argument("--fix-tried", default=None)
    a = ap.parse_args(argv)
    home = getattr(a, "home", None)

    try:
        if a.cmd == "classify":
            c = classify(_read_input(a.input))
            _dump(c, a.json, f"{c.get('cause_class')} (confidence {c.get('confidence')}"
                             f"{', FLAGGED' if c.get('flagged') else ''}) — {c.get('reason')}")
            return EXIT_OK
        if a.cmd == "record":
            if a.from_gate:
                res = record_from_gate(a.repo, _read_input(a.from_gate), item=a.item,
                                       fix_tried=a.fix_tried, home=home, where=a.where,
                                       outcome=a.outcome)
                if res is None:
                    _dump({"recorded": None, "reason": "nothing failed"}, a.json,
                          "nothing failed: no record written")
                    return EXIT_OK
            else:
                extra = {"invented_reason": a.invented_reason} if a.invented_reason else None
                res = record(a.repo, item=a.item or a.where, what=a.what, where=a.where,
                             rule=a.rule, cause_class=a.cause, evidence=a.evidence,
                             fix_tried=a.fix_tried, prevention=a.prevention,
                             outcome=a.outcome, home=home, extra=extra)
            r = res["record"]
            _dump(res, a.json, f"recorded {r['item']} attempt {r['attempt']} cause "
                               f"{r['cause_class']} outcome {r['outcome']}"
                               + (" — ESCALATED" if res["escalated"] else ""))
            return EXIT_ESCALATED if res["escalated"] else EXIT_OK
        if a.cmd == "retry-prompt":
            text = retry_prompt(a.item, repo=a.repo)
            pol = attempt_policy(a.item, repo=a.repo)
            if a.json:
                _dump({"item": a.item, "policy": pol, "prompt": text}, True)
            else:
                sys.stdout.write(text or f"(no failure record for {a.item})\n")
            return EXIT_ESCALATED if pol["escalated"] else EXIT_OK
        if a.cmd == "status":
            st = status(a.repo)
            lines = [f"failure log: {st['log']} ({st['records']} record(s))"]
            for p in st["items"]:
                lines.append(f"  {p['item']:<32} attempts {p['attempts']}/{MAX_ATTEMPTS}  "
                             f"{p['action']:<8} last cause {p['last_cause']}")
            lines.append(f"escalations: {len(st['escalations'])}"
                         + "".join(f"\n  ESCALATED {p['item']}" for p in st["escalations"]))
            _dump(st, a.json, "\n".join(lines))
            return EXIT_OK
        if a.cmd == "promote":
            res = _promote(a.repo, home)
            _dump(res, a.json, f"project lessons created {len(res['project_lessons_created'])}, "
                               f"user lessons created {len(res['user_lessons_created'])}")
            return EXIT_OK
        if a.cmd == "wrap":
            if not tail:
                print("wrap needs a command after --", file=sys.stderr)
                return EXIT_BAD_INPUT
            res = wrap(tail, a.repo, item=a.item, fix_tried=a.fix_tried, home=home)
            if not res["ran"]:
                sys.stderr.write(f"[coordn8:reflect] ESCALATED {res['item']}: not run — "
                                 f"{res['reason']}\n")
            elif res["recorded"]:
                r = res["recorded"]["record"]
                sys.stderr.write(f"[coordn8:reflect] recorded {r['item']} attempt "
                                 f"{r['attempt']} cause {r['cause_class']} outcome "
                                 f"{r['outcome']}\n")
            if a.json:
                print(json.dumps(res, ensure_ascii=False, default=str))
            return res["returncode"]
    except AttemptsExhausted as e:
        print(f"[coordn8:reflect] ESCALATED: {e}", file=sys.stderr)
        return EXIT_ESCALATED
    except (ValueError, TypeError, OSError) as e:
        print(f"[coordn8:reflect] bad input: {e}", file=sys.stderr)
        return EXIT_BAD_INPUT
    return EXIT_BAD_INPUT


if __name__ == "__main__":
    sys.exit(main())
