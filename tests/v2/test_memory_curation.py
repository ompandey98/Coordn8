#!/usr/bin/env python3
"""Memory curation suite (K1, K5, F4 promotion): curate.py, project.py, recall.py.

Hermetic: every store lives in a temp COORDN8_HOME; every project is a temp repo. Nothing is
written outside the temp dir, and the real user home is never read or written.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import ROOT, Suite, run, sandbox_env, tmpdir  # noqa: E402

s = Suite("memory_curation")
_tmp = tmpdir()
TMP = Path(_tmp.name)
ENV = sandbox_env(TMP)
os.environ.update(ENV)  # this process too: user_home() must resolve into the sandbox
HOME = Path(ENV["COORDN8_HOME"])

sys.path.insert(0, str(ROOT / "scripts"))
from memory import curate, recall  # noqa: E402
from memory.project import ProjectMemory  # noqa: E402
from memory.store import Store  # noqa: E402


def repo(name: str) -> Path:
    p = TMP / "repos" / name
    p.mkdir(parents=True, exist_ok=True)
    (p / "main.py").write_text("print('hi')\n")
    return p


class Prefix:
    """Records every snapshot of a set of files; proves each later snapshot starts with the
    earlier one byte for byte (nothing edited, nothing removed)."""

    def __init__(self):
        self.snaps: dict = {}
        self.bad: list = []

    def snap(self, *paths: Path):
        for p in paths:
            b = p.read_bytes() if p.exists() else b""
            prev = self.snaps.get(str(p))
            if prev is not None and not b.startswith(prev):
                self.bad.append(str(p))
            self.snaps[str(p)] = b


PFX = Prefix()
ENTRIES = HOME / "memory" / "entries.jsonl"


def files_of(*pms: ProjectMemory):
    out = [ENTRIES]
    for pm in pms:
        out += [pm.decisions_path, pm.lessons_path, pm.failures_path]
    return out


# A fixture CONSUMER: what a build does with the recalled text. It reads the session context
# only (the same text the SessionStart hook injects) and picks the narration voice: a live-brief
# conflict wins; else a preference scoped to this project; else a global preference.
_PREF_LINE = re.compile(r"^- (.*) \((m-[0-9a-f]+), ([^,]+), [^,]+, [^)]*\)")


def consumer_voice(ctx: str, slug: str) -> str:
    lines = ctx.splitlines()
    sec, prefs, brief = None, [], []
    for ln in lines:
        if ln.startswith("## "):
            sec = ln
            continue
        if sec and sec.startswith("## Conflicts with this chat") and "[voice]" in ln:
            m = re.search(r'this chat says "([^"]*)"', ln)
            if m:
                brief.append(m.group(1))
        if sec == "## Standing preferences":
            m = _PREF_LINE.match(ln)
            if m and "voice" in m.group(1).lower():
                prefs.append((m.group(1), m.group(3)))

    def pick(t: str) -> str:
        t = t.lower()
        return "female" if "female" in t else "male" if "male" in t else "default"

    if brief:
        return pick(brief[0])
    for t, tag in prefs:
        if tag == slug:
            return pick(t)
    for t, tag in prefs:
        if tag == "global":
            return pick(t)
    return "default"


A, B, C = repo("alpha-app"), repo("beta-svc"), repo("gamma-lib")
pmA, pmB, pmC = ProjectMemory(A), ProjectMemory(B), ProjectMemory(C)

# ------------------------------------------------------------------ K1 capture
quote = "I always want a male narrator voice in these videos."
r1 = curate.capture(quote, source="chat", repo=A)
e1 = r1["entry"] or {}
PFX.snap(*files_of(pmA, pmB, pmC))
s.check("capture types a stated preference with verbatim source quote, confidence and global scope",
        r1["action"] == "added" and e1.get("type") == "preference" and e1.get("scope") == "global"
        and e1.get("source_quote") == quote and e1.get("confidence") == "high"
        and e1.get("source") == "chat" and e1.get("date"), r1)

ctxB = recall.session_context(B, "", 4096)
s.check("preference captured in project A build 1 appears in session_context for project B build 2",
        "male narrator voice" in ctxB and e1.get("id", "?") in ctxB, ctxB[:600])
s.check("fixture consumer applies the recalled preference in project B (voice = male)",
        consumer_voice(ctxB, "beta-svc") == "male", ctxB[:600])
s.check("without any stored preference the fixture consumer falls back to default (negative control)",
        consumer_voice("# Coordn8 recall\n", "beta-svc") == "default")

rp = curate.capture("For this project, keep the palette dark and calm.", source="chat", repo=A)
PFX.snap(*files_of(pmA))
ctxA = recall.session_context(A, "", 4096)
ctxB2 = recall.session_context(B, "", 4096)
s.check("project-scoped preference is visible in its own project only (not leaked into another)",
        rp["entry"]["scope"] == "project:alpha-app" and "palette dark" in ctxA
        and "palette dark" not in ctxB2, (rp["entry"].get("scope"), ctxB2[:300]))

rd = curate.capture("We decided to use the layered flowchart for the routing figure in this project",
                    source="chat", repo=A)
PFX.snap(*files_of(pmA))
decs = pmA.decisions()
d0 = decs[-1] if decs else {}
s.check("a project decision is logged to decisions.md with seq, timestamp, ask, reason and change",
        rd["entry"]["type"] == "decision" and rd["project_decision"] is not None
        and d0.get("seq") == 1 and d0.get("id") == "D-0001" and d0.get("ts")
        and "layered flowchart" in d0.get("ask", "") and d0.get("reason")
        and rd["entry"]["id"] in d0.get("change", ""), d0)

before = ENTRIES.read_bytes()
rq = curate.capture("What does the router module do?", source="chat", repo=A)
s.check("a question is not memory: classified ignored and nothing is written (negative)",
        rq["action"] == "ignored" and rq["entry"] is None and ENTRIES.read_bytes() == before, rq)
s.check("classify: hedged statement -> low confidence, strong statement -> high confidence",
        curate.classify("Maybe try a warmer accent colour")["confidence"] == "low"
        and curate.classify("Never put text on a connector line, ever again")["confidence"] == "high")

rdup = curate.capture("I always want a male narrator voice in these videos", source="chat", repo=C)
s.check("re-stating an existing preference writes nothing (duplicate)",
        rdup["action"] == "duplicate" and rdup["entry"]["id"] == e1.get("id")
        and ENTRIES.read_bytes() == before, rdup["action"])

# ---------------------------------------------------------- K5 supersede & conflicts
r2 = curate.capture("Actually, use a female narrator voice instead.", source="chat", repo=B)
e2 = r2["entry"] or {}
PFX.snap(*files_of(pmA, pmB, pmC))
st = Store(HOME)
s.check("later contrary instruction supersedes: the new entry names the old one with a reason",
        r2["action"] == "superseded" and e2.get("supersedes") == e1.get("id")
        and "contrary" in (e2.get("reason") or "") and e2.get("scope") == "global", r2["action"])
hist = st.history(e2.get("id", ""))
cur_voice = [e for e in st.list(type="preference") if "voice" in e.get("text", "")]
s.check("both versions stay visible in history; only the newer one is current",
        [h.get("id") for h in hist] == [e1.get("id"), e2.get("id")]
        and [e.get("id") for e in cur_voice] == [e2.get("id")]
        and any(e.get("id") == e1.get("id") for e in st.list(include_superseded=True)),
        [h.get("id") for h in hist])
ctxC = recall.session_context(C, "", 4096)
s.check("session_context shows the current version only; the superseded one is not applied",
        e2.get("id", "?") in ctxC and e1.get("id", "?") not in ctxC
        and consumer_voice(ctxC, "gamma-lib") == "female", ctxC[:500])

ro = curate.capture("For this project use a male narrator voice", source="chat", repo=C)
PFX.snap(*files_of(pmC))
st = Store(HOME)
ctxC2 = recall.session_context(C, "", 4096)
ctxB3 = recall.session_context(B, "", 4096)
s.check("a project instruction against a global one is a recorded override, not a global supersede",
        ro["action"] == "added" and ro["entry"]["scope"] == "project:gamma-lib"
        and ro["entry"].get("supersedes") is None and e2.get("id") in (ro["entry"].get("overrides") or [])
        and any(e.get("id") == e2.get("id") for e in st.list(type="preference")), ro["action"])
s.check("the override applies in its project and the global preference still applies elsewhere",
        consumer_voice(ctxC2, "gamma-lib") == "male" and consumer_voice(ctxB3, "beta-svc") == "female",
        (consumer_voice(ctxC2, "gamma-lib"), consumer_voice(ctxB3, "beta-svc")))

snap_before = {str(p): p.read_bytes() for p in files_of(pmA, pmB, pmC) if p.exists()}
brief = "Make the video under three minutes. Use a male voice for this one, and keep captions on."
ctxConf = recall.session_context(B, brief, 4096)
s.check("memory-vs-live-brief conflict is surfaced explicitly: memory says X; this chat says Y",
        "CONFLICT [voice]" in ctxConf and "memory says \"Use a female narrator voice\"" in ctxConf
        and "this chat says \"Use a male voice for this one" in ctxConf, ctxConf[:900])
s.check("the surfaced conflict states that the live brief wins and asks the owner",
        "The live brief wins" in ctxConf and "ask the owner" in ctxConf
        and ctxConf.index("CONFLICT") < ctxConf.index("## Standing preferences"), ctxConf[:400])
s.check("fixture consumer follows the live brief over memory when they conflict",
        consumer_voice(ctxConf, "beta-svc") == "male")
s.check("the conflict is never resolved silently: recall wrote nothing to memory",
        all(Path(k).read_bytes() == v for k, v in snap_before.items())
        and Store(HOME).list(type="preference")[0].get("id") is not None, "bytes changed")
ctxAgree = recall.session_context(B, "Please use a female voice and keep it short.", 4096)
ctxOther = recall.session_context(B, "Explain the ingestion path in detail.", 4096)
s.check("a live brief that agrees with memory raises no conflict (negative)",
        "CONFLICT" not in ctxAgree, ctxAgree[:400])
s.check("a live brief on an unrelated subject raises no conflict (negative)",
        "CONFLICT" not in ctxOther, ctxOther[:400])

# memory-internal conflict (two current entries on one subject written outside curate)
st.add("preference", "Use serif type for titles", scope="global", subject="title type", source="cli")
st.add("preference", "Use sans type for titles", scope="project:beta-svc", subject="title type",
       source="cli")
PFX.snap(*files_of(pmB))
ctxIn = recall.session_context(B, "", 4096)
s.check("two current entries that disagree on one subject are surfaced as a memory conflict, both kept",
        "## Conflicts inside memory" in ctxIn and "serif type" in ctxIn and "sans type" in ctxIn,
        ctxIn[:700])

# recall on an empty home creates nothing
empty = TMP / "empty_home"
ctxE = recall.session_context(A, "use a male voice", 4096, home=empty)
s.check("recall never writes or creates the user store (empty home stays absent)",
        not empty.exists() and ctxE.startswith("# Coordn8 recall"), ctxE[:200])

# -------------------------------------------------------------- F4 promotion
def fail(pm: ProjectMemory, cc: str, n: int, where: str = "scene s3"):
    return pm.log_failure({"what": f"gate failed #{n}", "where": where, "rule": "A2 contrast",
                           "cause_class": cc, "evidence": f"caption 3.1:1 run {n}",
                           "fix_tried": "raise ink lightness", "outcome": "fail", "attempt": n,
                           "prevention": "pick ink from the role table, never hand-tune"})


fail(pmA, "wrong-choice:palette", 1)
PFX.snap(*files_of(pmA))
p1 = curate.promote_lessons(A)
PFX.snap(*files_of(pmA))
s.check("a cause class seen once in a project does not become a lesson (negative)",
        not p1["project_lessons_created"] and not pmA.lessons() and not p1["user_lessons_created"], p1)
fail(pmA, "wrong-choice:palette", 2)
p2 = curate.promote_lessons(A)
PFX.snap(*files_of(pmA))
les = pmA.lessons()
s.check("a cause class seen twice in one project becomes a project lesson with its cause class",
        len(p2["project_lessons_created"]) == 1 and len(les) == 1
        and les[0].get("cause_class") == "wrong-choice:palette" and "role table" in les[0].get("fix", "")
        and les[0].get("source") == "promote", les)
ctxL = recall.session_context(A, "", 4096)
s.check("the project lesson is loaded first: listed before preferences in that project's context",
        "## Project lessons (load first)" in ctxL and "wrong-choice:palette" in ctxL
        and ctxL.index("## Project lessons") < ctxL.index("## Standing preferences"), ctxL[:800])
b_les = pmA.lessons_path.read_bytes()
b_ent = ENTRIES.read_bytes()
p3 = curate.promote_lessons(A)
s.check("promote is idempotent in one project: a re-run writes no byte",
        not p3["project_lessons_created"] and not p3["user_lessons_created"]
        and not p3["failure_classes_registered"]
        and pmA.lessons_path.read_bytes() == b_les and ENTRIES.read_bytes() == b_ent, p3)
s.check("one project alone never produces a user-level lesson (negative)",
        not [e for e in Store(HOME).list(type="lesson", scope="global")
             if e.get("cause_class") == "wrong-choice:palette"])

fail(pmB, "wrong-choice:palette", 1, where="scene s1")
pB = curate.promote_lessons(B)
PFX.snap(*files_of(pmA, pmB))
glob = [e for e in Store(HOME).list(type="lesson", scope="global")
        if e.get("cause_class") == "wrong-choice:palette"]
g = glob[0] if glob else {}
s.check("the same cause class in two projects is promoted to exactly one user-level lesson",
        len(pB["user_lessons_created"]) == 1 and len(glob) == 1 and g.get("scope") == "global"
        and set(g.get("projects") or []) == {"project:alpha-app", "project:beta-svc"}, glob)
s.check("the user-level lesson carries source refs to both projects' failure logs and a quote",
        any(str(pmA.failures_path) in r for r in g.get("source_refs") or [])
        and any(str(pmB.failures_path) in r for r in g.get("source_refs") or [])
        and g.get("source_quote") and g.get("source") == "reflect", g)
fail(pmB, "wrong-choice:palette", 2, where="scene s2")
b_ent2 = ENTRIES.read_bytes()
pA2, pB2 = curate.promote_lessons(A), curate.promote_lessons(B)
glob2 = [e for e in Store(HOME).list(type="lesson", scope="global", include_superseded=True)
         if e.get("cause_class") == "wrong-choice:palette"]
s.check("re-running promotion in either project never creates a second user-level lesson",
        len(glob2) == 1 and not pA2["user_lessons_created"] and not pB2["user_lessons_created"]
        and ENTRIES.read_bytes() == b_ent2, (len(glob2), pA2["user_lessons_created"]))
PFX.snap(*files_of(pmA, pmB))
ctxG = recall.session_context(C, "", 4096)
s.check("the promoted user-level lesson appears in a third project's session context",
        g.get("id", "?") in ctxG and "## Lessons (user-level)" in ctxG, ctxG[:900])

# concurrent promoters for a fresh class in two fresh projects -> still one lesson
D, E = repo("delta-x"), repo("echo-y")
pmD, pmE = ProjectMemory(D), ProjectMemory(E)
for pm in (pmD, pmE):
    fail(pm, "timing", 1, where="scene s9")
    fail(pm, "timing", 2, where="scene s9")
errs: list = []


def _promo(r):
    try:
        curate.promote_lessons(r)
    except Exception as ex:  # noqa: BLE001
        errs.append(repr(ex))


ths = [threading.Thread(target=_promo, args=(r,)) for r in (D, E, D, E, D, E)]
[t.start() for t in ths]
[t.join() for t in ths]
tim = [e for e in Store(HOME).list(type="lesson", scope="global", include_superseded=True)
       if e.get("cause_class") == "timing"]
s.check("six concurrent promoters across two projects still write one user-level lesson and one "
        "project lesson each", not errs and len(tim) == 1 and len(pmD.lessons()) == 1
        and len(pmE.lessons()) == 1, (errs, len(tim), len(pmD.lessons()), len(pmE.lessons())))

# failure-log validation
def rejects(rec) -> bool:
    try:
        pmC.log_failure(rec)
        return False
    except (ValueError, TypeError):
        return True


good = {"what": "w", "where": "x", "rule": "r", "cause_class": "layout", "evidence": "e",
        "fix_tried": "f", "outcome": "fail", "prevention": "p"}
s.check("log_failure rejects a record missing a required field (negative)",
        rejects({k: v for k, v in good.items() if k != "evidence"}))
s.check("log_failure rejects an unknown cause_class (negative)",
        rejects(dict(good, cause_class="gremlins")))
s.check("an invented cause_class is accepted only with a stated reason (H1)",
        rejects(dict(good, cause_class="invented:font-cache")) and
        not rejects(dict(good, cause_class="invented:font-cache",
                         invented_reason="font cache served a stale glyph table")))
rows = pmC.failures()
s.check("failure records follow SPEC 3.6 (ts, attempt, promoted=false filled in)",
        rows and all(k in rows[-1] for k in ("ts", "what", "where", "rule", "cause_class", "evidence",
                                               "fix_tried", "outcome", "attempt", "prevention",
                                               "promoted"))
        and rows[-1]["promoted"] is False and rows[-1]["attempt"] == 1, rows[-1:] or rows)

# torn tail + legacy content
with open(pmC.failures_path, "ab") as f:
    f.write(b'{"what": "torn')
torn = pmC.failures_path.read_bytes()
pmC.log_failure(dict(good, what="after torn"))
tail_rows = pmC.failures()
s.check("a torn last line in failure_log.jsonl is never merged with the next record",
        pmC.failures_path.read_bytes().startswith(torn) and tail_rows[-1]["what"] == "after torn"
        and len(pmC.corrupt) == 1, (len(pmC.corrupt), tail_rows[-1:]))
legacy_repo = repo("legacy-proj")
pmL = ProjectMemory(legacy_repo)
pmL.root.mkdir(parents=True, exist_ok=True)
legacy = "# Decisions\n\n- slow the intro down (owner, week 1)\n- cut the auth section\n"
pmL.decisions_path.write_text(legacy, encoding="utf-8")
pmL.add_decision(ask="open on cost for the leadership cut", reason="owner asked", change="opening")
dl = pmL.decisions()
s.check("hand-written decisions.md content is kept byte for byte and still read as legacy entries",
        pmL.decisions_path.read_text(encoding="utf-8").startswith(legacy)
        and [d.get("text") for d in dl if d.get("legacy")] == ["slow the intro down (owner, week 1)",
                                                               "cut the auth section"]
        and dl[-1].get("id") == "D-0001", dl)
rs = pmL.add_decision(ask="open on the incident instead", reason="owner changed mind",
                      change="opening", supersedes="D-0001")
s.check("a reversed project decision is a new block naming the old one; current view hides the old",
        rs["supersedes"] == "D-0001" and [d["id"] for d in pmL.decisions(current_only=True)
                                         if not d.get("legacy")] == ["D-0002"]
        and len([d for d in pmL.decisions() if not d.get("legacy")]) == 2)

# -------------------------------------------------------------- budget
big = repo("big-proj")
pmBig = ProjectMemory(big)
big_home = TMP / "big_home"
sb = Store(big_home)
lines = []
for i in range(2000):
    t = ("preference", "decision", "lesson")[i % 3]
    lines.append(json.dumps({"id": f"m-{i:012x}", "type": t, "scope": "global" if i % 2 else
                             "project:big-proj", "text": f"Rule number {i} about subject{i} with a "
                             "long explanation " + "x" * 120, "source_quote": "q", "source": "cli",
                             "date": "2026-10-01T00:00:00+00:00", "confidence": "med",
                             "supersedes": None, "source_hash": None, "source_path": None}))
sb.entries_path.parent.mkdir(parents=True, exist_ok=True)
sb.entries_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
sb.add("preference", "Use a female narrator voice", scope="global", source="chat")
pmBig.root.mkdir(parents=True, exist_ok=True)
pmBig.requirements_path.write_text(
    "| id | requirement | source | in_scope | importance | status | where_covered |\n"
    "|---|---|---|---|---|---|---|\n" + "".join(
        f"| R{i} | requirement {i} explained at length {'y' * 60} | live-brief | yes | must | "
        f"{'covered' if i % 4 == 0 else 'open'} | {'s1' if i % 4 == 0 else ''} |\n" for i in range(200)),
    encoding="utf-8")
for i in range(300):
    pmBig.add_lesson(pattern=f"lesson {i} " + "z" * 80, fix="fix it", source="critic")
t0 = time.time()
ctxBig = recall.session_context(big, "Use a male voice for this one.", 4096, home=big_home)
dt = time.time() - t0
nb = len(ctxBig.encode("utf-8"))
s.check("session_context stays <= 4096 bytes with a large store (2001 entries, 300 lessons, 200 rows)",
        nb <= 4096, nb)
s.check("under the budget the live-brief conflict and every section still appear, and a footer "
        "counts what was left out",
        "CONFLICT [voice]" in ctxBig and "## Project lessons" in ctxBig
        and "## Open requirement rows" in ctxBig and "## Standing preferences" in ctxBig
        and "## Standing decisions" in ctxBig and "## Lessons (user-level)" in ctxBig
        and re.search(r"\(\d+ more not shown", ctxBig) is not None, ctxBig[-300:])
s.check("open items are uncovered in-scope rows only (no covered row listed)",
        "R1 [must, open]" in ctxBig and "R0 [" not in ctxBig and "R4 [" not in ctxBig, ctxBig[:1500])
s.check("session_context on the large store finishes in under 2 seconds", dt < 2.0, f"{dt:.2f}s")
small = recall.session_context(big, "", 512, home=big_home)
s.check("a smaller max_bytes cap is honoured exactly", len(small.encode("utf-8")) <= 512,
        len(small.encode("utf-8")))
with open(sb.entries_path, "ab") as f:
    f.write(b"\nthis is not json\n")
ctxCor = recall.session_context(big, "", 4096, home=big_home)
s.check("a corrupt memory line is skipped and reported, never fatal",
        "unreadable memory line" in ctxCor and "## Standing preferences" in ctxCor, ctxCor[:300])

# -------------------------------------------------------------- CLI
rc = run("scripts/memory/recall.py", ["--repo", B, "--brief", "Use a male voice for this one.",
                                      "--json", "--max-bytes", "2048"], env=ENV, timeout=60)
try:
    cj = json.loads(rc.stdout)
except ValueError:
    cj = {}
s.check("recall.py --repo --brief --json prints the structured recall plus a text <= max-bytes",
        rc.returncode == 0 and cj.get("conflicts_brief") and len(cj.get("text", "").encode()) <= 2048
        and cj.get("project") == "project:beta-svc", (rc.returncode, rc.stderr[-300:]))
rc2 = run("scripts/memory/curate.py", ["capture", "From now on avoid gradients on titles",
                                       "--repo", A, "--json"], env=ENV, timeout=60)
try:
    cc2 = json.loads(rc2.stdout)
except ValueError:
    cc2 = {}
s.check("curate.py capture CLI writes one typed entry into the sandbox COORDN8_HOME only",
        rc2.returncode == 0 and cc2.get("action") == "added"
        and any(e.get("id") == (cc2.get("entry") or {}).get("id") for e in Store(HOME).all_entries()),
        (rc2.returncode, rc2.stderr[-300:]))
rc3 = run("scripts/memory/curate.py", ["promote", "--repo", A, "--json"], env=ENV, timeout=60)
s.check("curate.py promote CLI is idempotent (re-run reports nothing new)",
        rc3.returncode == 0 and json.loads(rc3.stdout or "{}").get("user_lessons_created") == [],
        rc3.stderr[-300:])
PFX.snap(*files_of(pmA, pmB, pmC, pmL))

# seeded defects: the prefix tracker catches an in-place edit; a taxonomy file narrows the classes
probe = Prefix()
pf = TMP / "probe.jsonl"
pf.write_bytes(b'{"a": 1}\n')
probe.snap(pf)
pf.write_bytes(b'{"a": 2}\n{"b": 1}\n')
probe.snap(pf)
s.check("seeded in-place edit of a memory file is caught by the byte-prefix check (negative control)",
        probe.bad == [str(pf)], probe.bad)
from memory.project import load_taxonomy  # noqa: E402
tx = TMP / "cause_taxonomy.json"
tx.write_text(json.dumps({"schema": 1, "classes": [{"id": "layout"}, {"id": "timing"}]}))
try:
    pmC.validate_failure(dict(good, cause_class="budget"), taxonomy=load_taxonomy(tx))
    tx_ok = False
except ValueError:
    tx_ok = True
s.check("a cause_class outside data/cause_taxonomy.json is rejected when that file defines the list",
        tx_ok and load_taxonomy(tx) == ("layout", "timing"))

# -------------------------------------------------------------- safety
s.check("nothing deleted: every memory and project file only ever grew by appended bytes",
        not PFX.bad and len(PFX.snaps) >= 10, PFX.bad)
outside = []
for r in (A, B, C, D, E, legacy_repo, big):
    for p in r.rglob("*"):
        if p.is_file() and p.name != "main.py":
            try:
                p.relative_to(r / "Coordn8" / "_research")
            except ValueError:
                outside.append(str(p))
s.check("project memory writes only under <repo>/Coordn8/_research/", not outside, outside[:5])
s.check("the real user home was never the store (sandbox COORDN8_HOME in use)",
        str(Store().home) == str(HOME) and ENTRIES.exists())

_tmp.cleanup()
sys.exit(s.finish())
