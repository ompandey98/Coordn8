#!/usr/bin/env python3
"""Suite "reflect" — the failure-analysis and self-correction loop (requirement F4).

Proves: a seeded palette contrast failure is classified wrong-choice:palette and recorded
(SPEC 3.6) through the project memory; the retry brief carries the record's evidence and the
fix tried; the third failure escalates and no 4th attempt is allowed; the same cause twice in
one project becomes a project lesson; the same cause in two projects is promoted to exactly
one user-level lesson (idempotent); an unknown cause class is rejected; the failure log is
append-only (byte-prefix check); `wrap` records on failure and records nothing on success;
every write lands under <repo>/Coordn8/_research/ or the sandbox COORDN8_HOME.
Hermetic: temp dirs only, sandbox home, no network.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import ROOT, PY, Suite, run, sandbox_env, tmpdir  # noqa: E402

FX = ROOT / "tests" / "v2" / "fixtures" / "reflect"
F4_CLASSES = ["input-missing", "wrong-assumption", "stale-artefact", "budget", "tool-absent",
              "wrong-choice:palette", "wrong-choice:diagram", "wrong-choice:scene",
              "writing-rule", "timing", "layout", "misread-brief"]

s = Suite("reflect")
_td = tmpdir()
TMP = Path(_td.name)
ENV = sandbox_env(TMP)
# the system interpreter otherwise caches bytecode under $HOME/Library/Caches — that is the
# interpreter, not reflect; switching it off keeps the write-scope check about reflect alone
ENV["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ.update(ENV)  # in-process calls see the sandbox home too
HOME = Path(ENV["COORDN8_HOME"])

sys.path.insert(0, str(ROOT / "scripts"))
import reflect  # noqa: E402
from lib.paths import user_home  # noqa: E402
from memory.project import ProjectMemory  # noqa: E402
from memory.recall import session_context  # noqa: E402
from memory.store import Store  # noqa: E402


def tree(root: Path) -> dict:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def fx(name: str) -> dict:
    return json.loads((FX / name).read_text(encoding="utf-8"))


def mkrepo(name: str) -> Path:
    r = TMP / "repos" / name
    r.mkdir(parents=True, exist_ok=True)
    (r / "app.py").write_text("print('hi')\n", encoding="utf-8")
    return r


PKG_WATCH = [ROOT / "data", FX, ROOT / "scripts" / "memory"]
pkg_before = {str(d): tree(d) for d in PKG_WATCH}
REPO_A, REPO_B, REPO_C = mkrepo("alpha"), mkrepo("beta"), mkrepo("gamma")
tmp_before = tree(TMP)

# --------------------------------------------------------------------------- taxonomy
tax_path = ROOT / "data" / "cause_taxonomy.json"
tax = json.loads(tax_path.read_text(encoding="utf-8"))
ids = [c.get("id") for c in tax.get("classes", [])]
s.check("data/cause_taxonomy.json lists exactly the twelve F4 cause classes", ids == F4_CLASSES,
        ids)
s.check("every taxonomy class has a description, typical evidence and a default prevention",
        all(isinstance(c.get("description"), str) and len(c["description"]) > 20
            and isinstance(c.get("typical_evidence"), list) and c["typical_evidence"]
            and isinstance(c.get("default_prevention"), str) and len(c["default_prevention"]) > 20
            for c in tax["classes"]))
from memory.project import load_taxonomy  # noqa: E402
s.check("project memory reads the taxonomy file (load_taxonomy returns the twelve ids)",
        list(load_taxonomy()) == F4_CLASSES)
rule_classes = {r["cause"] for r in reflect.RULES if r.get("cause")} | set(
    reflect.SEMANTIC_CODES.values()) | {reflect.FALLBACK_CLASS}
s.check("every class the rule table can return is in the taxonomy",
        rule_classes <= set(F4_CLASSES), sorted(rule_classes - set(F4_CLASSES)))

# --------------------------------------------------------------------------- classify
pal = fx("palette_contrast_fail.json")
c = reflect.classify(pal)
s.check("seeded palette contrast fail JSON classifies as wrong-choice:palette (high, not flagged)",
        c["cause_class"] == "wrong-choice:palette" and c["confidence"] == "high"
        and not c["flagged"], c)
s.check("classification evidence carries the measured finding (caption 3.1:1 on #2a2f3a)",
        "3.1:1" in c["evidence"] and "#2a2f3a" in c["evidence"], c["evidence"])
s.check("classify is deterministic: same input, identical answer (in-process and CLI)",
        reflect.classify(json.loads(json.dumps(pal))) == c and
        json.loads(run("scripts/reflect.py", ["classify", FX / "palette_contrast_fail.json",
                                              "--json"], env=ENV).stdout) == json.loads(
            json.dumps(c)))
expect = {
    "palette_no_design.json": "input-missing",
    "media_no_ffmpeg.json": "tool-absent",
    "stage_v_stale.json": "stale-artefact",
    "timeout.json": "budget",
    "disk_low.json": "budget",
    "beat_sync_ahead.json": "timing",
    "layout_overflow.json": "layout",
    "language_long.json": "writing-rule",
    "coverage_open.json": "misread-brief",
    "tree_linear.json": "wrong-choice:diagram",
    "semantics_s5.json": "wrong-choice:scene",
    "invariants_c1.json": "layout",
    "manifest_overclaim.json": "wrong-assumption",
    "agent_frame_reject.json": "wrong-choice:scene",
}
got = {k: reflect.classify(fx(k))["cause_class"] for k in expect}
s.check("rule table maps 14 seeded gate/agent failures to their expected cause classes",
        got == expect, {k: v for k, v in got.items() if expect[k] != v})
s.check("a gate that could not run (palette SKIPPED, no design system) is input-missing, "
        "not a palette choice", got["palette_no_design.json"] == "input-missing")
s.check("semantic gate classified by its dominant code (S5 -> scene, C1 -> layout)",
        reflect.classify(fx("semantics_s5.json"))["rule_id"].endswith("S5")
        and reflect.classify(fx("invariants_c1.json"))["rule_id"].endswith("C1"))
orc = reflect.classify(fx("orchestrate_run.json"))
s.check("orchestrate run report: only the failing gate is classified (visual_density -> scene)",
        orc["cause_class"] == "wrong-choice:scene" and orc["failing_count"] == 1
        and orc["gate"] == "visual_density:video" and orc["stage"] == "plan", orc)
tbl = reflect.classify((FX / "run_gates_table.txt").read_text(encoding="utf-8"))
s.check("run_gates text table: the REVIEW row is classified (content_ban -> writing-rule)",
        tbl["cause_class"] == "writing-rule" and tbl["gate"] == "content_ban", tbl)
unk = reflect.classify(fx("unknown_gate.json"))
s.check("unknown failure -> wrong-assumption with LOW confidence and flagged",
        unk["cause_class"] == "wrong-assumption" and unk["confidence"] == "low"
        and unk["flagged"] is True, unk)
s.check("a passing result classifies to no cause (negative)",
        reflect.classify({"gate": "palette", "status": "PASS"})["cause_class"] is None)
s.check("guard: 'node ... missing' in a diagram REVIEW is a diagram choice, not tool-absent",
        reflect.classify({"gate": "diagram_plan", "status": "REVIEW",
                          "summary": "node auth missing a label"})["cause_class"]
        == "wrong-choice:diagram")
s.check("guard: a stale beat index is timing, not a stale artefact",
        reflect.classify({"gate": "beat_index", "status": "REVIEW",
                          "summary": "stale index beat(7) after a narration edit"})["cause_class"]
        == "timing")

# --------------------------------------------------------------------------- record
pmA = ProjectMemory(REPO_A)
log = pmA.failures_path
ITEM = "scene s3 palette"
snap = []  # byte snapshots of failure_log.jsonl after every write
s.check("no failure log exists before the first record", not log.exists())
r1 = reflect.record_from_gate(REPO_A, pal, item=ITEM, fix_tried="raise ink L* +12")
snap.append(log.read_bytes())
row1 = r1["record"]
s.check("seeded failure -> record with cause wrong-choice:palette in failure_log.jsonl",
        row1["cause_class"] == "wrong-choice:palette"
        and pmA.failures()[-1]["cause_class"] == "wrong-choice:palette", row1)
s.check("record follows SPEC 3.6 (all fields, attempt 1, outcome fail, promoted false)",
        all(k in row1 for k in ("ts", "what", "where", "rule", "cause_class", "evidence",
                                "fix_tried", "outcome", "attempt", "prevention", "promoted"))
        and row1["attempt"] == 1 and row1["outcome"] == "fail" and row1["promoted"] is False)
s.check("record carries the classifier's rule id, confidence and flag",
        row1.get("classifier", {}).get("rule_id") == "R10-palette"
        and row1["classifier"]["confidence"] == "high")
s.check("an empty prevention is filled from the taxonomy's default prevention",
        "role table" in row1["prevention"], row1["prevention"])
s.check("the record goes through ProjectMemory.log_failure into <repo>/Coordn8/_research/",
        log.resolve() == (REPO_A / "Coordn8" / "_research" / "failure_log.jsonl").resolve()
        and log.exists())
s.check("one record in a project does not make a lesson (negative)",
        not [le for le in pmA.lessons() if le.get("cause_class") == "wrong-choice:palette"])
pol1 = reflect.attempt_policy(ITEM, repo=REPO_A)
s.check("after the 1st failure the policy allows retry 1 of 2 (next attempt 2)",
        pol1["action"] == "retry" and pol1["allowed"] and pol1["next_attempt"] == 2
        and pol1["retry_number"] == 1, pol1)

# --------------------------------------------------------------------------- retry prompt
rp = reflect.retry_prompt(ITEM, repo=REPO_A)
s.check("retry prompt contains the record's evidence and its fix_tried",
        "3.1:1" in rp and "#2a2f3a" in rp and "raise ink L* +12" in rp, rp[:400])
s.check("retry prompt names the cause class, rule, where, prevention and attempt 2 of 3",
        "wrong-choice:palette" in rp and "text-contrast" in rp and ITEM in rp
        and "role table" in rp and "attempt 2 of 3" in rp)
rp_records = reflect.retry_prompt(ITEM, records=pmA.failures())
s.check("retry_prompt(item, records) works from passed-in records alone",
        "raise ink L* +12" in rp_records and "3.1:1" in rp_records)
s.check("retry prompt for an item with no record is empty (negative)",
        reflect.retry_prompt("scene s9", repo=REPO_A) == "")
cli_rp = run("scripts/reflect.py", ["retry-prompt", "--item", ITEM, "--repo", REPO_A], env=ENV)
s.check("retry-prompt CLI prints the same addendum", cli_rp.returncode == 0
        and "raise ink L* +12" in cli_rp.stdout and "3.1:1" in cli_rp.stdout, cli_rp.stderr)

# --------------------------------------------------------------------------- second failure
r2 = reflect.record_from_gate(REPO_A, pal, item=ITEM, fix_tried="swap caption to role ink-2")
snap.append(log.read_bytes())
s.check("second failure of the item is attempt 2, outcome fail",
        r2["record"]["attempt"] == 2 and r2["record"]["outcome"] == "fail")
rp2 = reflect.retry_prompt(ITEM, repo=REPO_A)
s.check("the retry prompt carries EVERY prior record's fix_tried (both attempts)",
        "raise ink L* +12" in rp2 and "swap caption to role ink-2" in rp2
        and "attempt 3 of 3" in rp2)
lesA = [le for le in pmA.lessons() if le.get("cause_class") == "wrong-choice:palette"]
s.check("same cause twice in one project -> project lesson with that cause class",
        len(lesA) == 1 and r2["promotion"]["project_lessons_created"], lesA)
s.check("the retry prompt also lists the project lesson for that cause",
        "Lessons already learned" in rp2)
ctx = session_context(REPO_A)
s.check("the project lesson is in the project's session context (loaded first next run)",
        "wrong-choice:palette" in ctx, ctx[:300])
s.check("one project alone never produces a user-level lesson (negative)",
        not [e for e in Store(HOME).all_entries() if e.get("type") == "lesson"
             and e.get("cause_class") == "wrong-choice:palette"])

# --------------------------------------------------------------------------- escalation
r3 = reflect.record_from_gate(REPO_A, pal, item=ITEM, fix_tried="darken paper to #10131a")
snap.append(log.read_bytes())
s.check("third failure is recorded with outcome escalated (attempt 3)",
        r3["record"]["attempt"] == 3 and r3["record"]["outcome"] == "escalated"
        and r3["escalated"] is True, r3["record"])
pol3 = reflect.attempt_policy(ITEM, repo=REPO_A)
s.check("after the third failure the policy is escalate and no 4th attempt is allowed",
        pol3["action"] == "escalate" and pol3["allowed"] is False
        and pol3["next_attempt"] is None and len(pol3["log"]) == 3, pol3)
size3 = log.stat().st_size
try:
    reflect.record_from_gate(REPO_A, pal, item=ITEM, fix_tried="a fourth try")
    fourth_refused = False
except reflect.AttemptsExhausted:
    fourth_refused = True
s.check("a 4th record for the escalated item is refused and writes nothing",
        fourth_refused and log.stat().st_size == size3)
rp3 = reflect.retry_prompt(ITEM, repo=REPO_A)
s.check("the escalated item's addendum says ESCALATED, do not run again, with the full log",
        "ESCALATED" in rp3 and "Do NOT run it again" in rp3 and "darken paper" in rp3
        and "raise ink L* +12" in rp3)
w4 = run("scripts/reflect.py", ["wrap", "--repo", REPO_A, "--item", ITEM, "--", PY,
                                FX / "fake_gate.py"], env=ENV)
s.check("wrap refuses to run an escalated item (exit 4, command never executed, no write)",
        w4.returncode == 4 and "FAKE-GATE-RAN" not in w4.stderr + w4.stdout
        and log.stat().st_size == size3, (w4.returncode, w4.stderr[-300:]))
stc = run("scripts/reflect.py", ["status", "--repo", REPO_A, "--json"], env=ENV)
st = json.loads(stc.stdout) if stc.returncode == 0 else {}
its = {p["item"]: p for p in st.get("items", [])}
s.check("status prints per-item attempts and escalations",
        its.get(ITEM, {}).get("attempts") == 3 and its[ITEM]["escalated"] is True
        and [p["item"] for p in st["escalations"]] == [ITEM], stc.stdout[:300])

# --------------------------------------------------------------------------- user-level promotion
pmB = ProjectMemory(REPO_B)
rb = reflect.record_from_gate(REPO_B, pal, item="scene s1 palette", fix_tried="raise ink")
snapB = [pmB.failures_path.read_bytes()]


def user_palette_lessons():
    return [e for e in Store(HOME).all_entries() if e.get("type") == "lesson"
            and e.get("scope") == "global" and e.get("cause_class") == "wrong-choice:palette"]


ul = user_palette_lessons()
s.check("same cause in two projects -> exactly one user-level lesson",
        len(ul) == 1 and rb["promotion"]["user_lessons_created"], ul)
s.check("the user-level lesson names both projects",
        len(ul) == 1 and set(ul[0].get("projects", [])) >= {pmA.scope, pmB.scope}, ul)
reflect.record_from_gate(REPO_B, pal, item="scene s2 palette", fix_tried="raise ink again")
snapB.append(pmB.failures_path.read_bytes())
for repo in (REPO_A, REPO_B):
    run("scripts/reflect.py", ["promote", "--repo", repo, "--json"], env=ENV)
s.check("user-level promotion happens exactly once (more records + promote CLI in both projects)",
        len(user_palette_lessons()) == 1, len(user_palette_lessons()))
s.check("project lesson stays one per class after repeated promotion (idempotent)",
        len([le for le in pmA.lessons() if le.get("cause_class") == "wrong-choice:palette"]) == 1)
ctxC = session_context(REPO_C)
s.check("the user-level lesson reaches a third project's session context",
        "wrong-choice:palette" in ctxC, ctxC[:300])

# --------------------------------------------------------------------------- rejection
sizeB = pmB.failures_path.stat().st_size
try:
    reflect.record(REPO_B, item="x", what="w", where="x", rule="r", cause_class="gremlins",
                   evidence="e", fix_tried="f")
    rejected = False
except ValueError:
    rejected = True
s.check("unknown cause_class is rejected and nothing is written", rejected
        and pmB.failures_path.stat().st_size == sizeB)
cli_bad = run("scripts/reflect.py", ["record", "--repo", REPO_B, "--item", "x", "--what", "w",
                                     "--where", "x", "--rule", "r", "--cause", "gremlins",
                                     "--evidence", "e", "--fix-tried", "f"], env=ENV)
s.check("record CLI with an unknown cause_class exits 3 and writes nothing",
        cli_bad.returncode == 3 and pmB.failures_path.stat().st_size == sizeB, cli_bad.stderr)
try:
    reflect.record(REPO_B, item="x", what="w", where="x", rule="r", cause_class="layout",
                   evidence="", fix_tried="f")
    rej_missing = False
except ValueError:
    rej_missing = True
s.check("a record missing evidence is rejected (negative)", rej_missing)
inv = reflect.record(REPO_B, item="font", what="font cache corrupt", where="render", rule="E1",
                     cause_class="invented:font-cache", evidence="glyph boxes on frame 12",
                     fix_tried="clear cache",
                     extra={"invented_reason": "none of the twelve classes covers a corrupt cache"})
snapB.append(pmB.failures_path.read_bytes())
s.check("an invented cause_class with a stated reason is accepted (freedom doctrine)",
        inv["record"]["cause_class"] == "invented:font-cache"
        and "no default prevention" in inv["record"]["prevention"])
cli_ok = run("scripts/reflect.py", ["record", "--repo", REPO_B, "--item", "cap", "--what",
                                    "caption too fast", "--where", "scene s5", "--rule", "E1 cps",
                                    "--cause", "timing", "--evidence", "26 cps on cue 4",
                                    "--fix-tried", "none yet", "--json"], env=ENV)
snapB.append(pmB.failures_path.read_bytes())
s.check("record CLI writes one SPEC 3.6 record with a valid cause",
        cli_ok.returncode == 0 and pmB.failures()[-1]["cause_class"] == "timing"
        and pmB.failures()[-1]["evidence"] == "26 cps on cue 4", cli_ok.stderr)

# --------------------------------------------------------------------------- wrap
pmC = ProjectMemory(REPO_C)
ok = run("scripts/reflect.py", ["wrap", "--repo", REPO_C, "--", PY, FX / "fake_gate.py", "pass"],
         env=ENV)
s.check("wrap on success passes the exit code through and records nothing (no _research dir)",
        ok.returncode == 0 and "FAKE-GATE-RAN" in ok.stderr
        and not pmC.failures_path.exists() and not pmC.root.exists(), ok.stderr[-300:])
bad = run("scripts/reflect.py", ["wrap", "--repo", REPO_C, "--fix-tried", "first run", "--json",
                                 "--", PY, FX / "fake_gate.py"], env=ENV)
rows_c = pmC.failures()
s.check("wrap on failure passes the exit code through and records one classified record",
        bad.returncode == 1 and len(rows_c) == 1
        and rows_c[0]["cause_class"] == "wrong-choice:palette"
        and rows_c[0]["item"] == "cmd:fake_gate" and rows_c[0]["fix_tried"] == "first run"
        and "3.1:1" in rows_c[0]["evidence"], (bad.returncode, bad.stderr[-300:]))
s.check("wrap echoes the gate's own output unchanged",
        '"gate": "palette"' in bad.stdout and "FAKE-GATE-RAN" in bad.stderr)
before_ok = pmC.failures_path.read_bytes()
ok2 = run("scripts/reflect.py", ["wrap", "--repo", REPO_C, "--", PY, FX / "fake_gate.py",
                                 "pass"], env=ENV)
s.check("wrap on success after a failure still writes nothing (log byte-identical)",
        ok2.returncode == 0 and pmC.failures_path.read_bytes() == before_ok)
nt = run("scripts/reflect.py", ["wrap", "--repo", REPO_C, "--item", "render s1", "--",
                                str(TMP / "no-such-renderer")], env=ENV)
last = pmC.failures()[-1]
s.check("wrap on a command that cannot be found records tool-absent",
        nt.returncode == 127 and last["item"] == "render s1"
        and last["cause_class"] == "tool-absent", (nt.returncode, last))

# --------------------------------------------------------------------------- append-only and write scope
s.check("failure_log.jsonl is append-only: every snapshot is a byte prefix of the next",
        all(b.startswith(a) for a, b in zip(snap, snap[1:]))
        and log.read_bytes().startswith(snap[-1])
        and all(b.startswith(a) for a, b in zip(snapB, snapB[1:]))
        and pmB.failures_path.read_bytes().startswith(snapB[-1]))
seeded = bytearray(snap[0])
seeded[10:14] = b"XXXX"
s.check("seeded in-place edit is caught by the byte-prefix check (negative control)",
        not log.read_bytes().startswith(bytes(seeded)))
s.check("every failure-log line is valid JSON (no torn or merged lines)",
        all(json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()))
tmp_after = tree(TMP)
allowed = [str(REPO_A / "Coordn8" / "_research"), str(REPO_B / "Coordn8" / "_research"),
           str(REPO_C / "Coordn8" / "_research"), str(HOME)]
changed = [p for p, h in tmp_after.items() if tmp_before.get(p) != h]
removed = [p for p in tmp_before if p not in tmp_after]
outside = [p for p in changed if not any(p.startswith(a + os.sep) for a in allowed)]
s.check("all writes land under <repo>/Coordn8/_research/ or the sandbox COORDN8_HOME only",
        changed and not outside and not removed, outside[:5] + removed[:5])
s.check("the user store in use is the sandbox COORDN8_HOME, never the real home",
        Path(user_home()).resolve() == HOME.resolve()
        and (HOME / "memory" / "entries.jsonl").exists())
s.check("no package file was written (data/, fixtures, scripts/memory unchanged)",
        all(tree(Path(d)) == h for d, h in pkg_before.items()))

_td.cleanup()
s.check("suite leaves nothing on disk (temp dir removed)", not TMP.exists())
sys.exit(s.finish())
