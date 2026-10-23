#!/usr/bin/env python3
"""tests/v2 — claims ledger (C3), page content rules (C4), explain modes and dials (C5) and the optional
understanding checks (C6).

Everything is built in a temp dir; HOME / COORDN8_HOME / CLAUDE_HOME are sandboxed for every subprocess.
The only things read from outside the package are the cached headless browser (read-only) and, for the
"same verdict as v1.5.0" check, the v1.5.0 copy of facts_trace (from the release zip, else git HEAD).
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import site
import subprocess
import sys
import zipfile
from pathlib import Path

REAL_HOME = os.path.expanduser("~")     # captured before sandboxing; used only to READ a cached browser
USER_SITE = site.getusersitepackages()

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import PY, ROOT, Suite, run, sandbox_env, tmpdir  # noqa: E402

s = Suite("claims_content")
FIX = ROOT / "tests" / "v2" / "fixtures" / "page"
KIT = ROOT / "data" / "style_kits" / "quiet-ward.json"
_td = tmpdir()
T = Path(_td.name)
ENV = sandbox_env(T)
ENV["PYTHONDONTWRITEBYTECODE"] = "1"
if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
    for cand in (Path(REAL_HOME) / "Library" / "Caches" / "ms-playwright", Path(REAL_HOME) / ".cache" / "ms-playwright"):
        if cand.is_dir():
            ENV["PLAYWRIGHT_BROWSERS_PATH"] = str(cand)
            break
if USER_SITE and Path(USER_SITE).is_dir():
    ENV["PYTHONPATH"] = os.pathsep.join(x for x in (os.environ.get("PYTHONPATH"), USER_SITE) if x)


def last_json(text: str) -> dict:
    for ln in reversed((text or "").strip().splitlines()):
        ln = ln.strip()
        if ln.startswith("{"):
            try:
                return json.loads(ln)
            except ValueError:
                continue
    return {}


def jdump(p: Path, obj) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj), encoding="utf-8")
    return p


def build(content: dict, out: Path, extra=()) -> tuple:
    out.parent.mkdir(parents=True, exist_ok=True)
    cfile = jdump(out.parent / (out.stem + ".content.json"), content)
    if not (out.parent / "flow.svg").exists():
        shutil.copy(FIX / "flow.svg", out.parent / "flow.svg")
    r = run("scripts/render/build_page.py", [cfile, "--design-system", KIT, "--out", out, *extra], env=ENV, timeout=120)
    return r, last_json(r.stdout)


def sections_of(h: str) -> list:
    return re.findall(r'<section id="([^"]+)" class="c8-section" data-c8-section', h)


# background jobs started early so the slow browser checks overlap the static ones
BG: dict = {}


def bg(name: str, args: list, log: Path):
    fh = open(log, "w")
    BG[name] = (subprocess.Popen(args, stdout=fh, stderr=subprocess.STDOUT, env={**os.environ, **ENV}), fh, log)


def bg_wait(name: str, timeout: int = 175) -> tuple:
    proc, fh, log = BG[name]
    try:
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        rc = 124
    fh.close()
    return rc, log.read_text(encoding="utf-8", errors="replace")


bg("test_page", [PY, str(ROOT / "tests" / "v2" / "test_page.py")], T / "test_page.log")

BASE = json.loads((FIX / "content.json").read_text(encoding="utf-8"))

# ======================================================================== C3 — facts_trace extension
code = T / "repo" / "src" / "engine.py"
code.parent.mkdir(parents=True)
code.write_text("\n".join(f"line {i}" for i in range(1, 51)), encoding="utf-8")
R = T / "repo"


def facts(manifest: dict, script: str = "scripts/gates/facts_trace.py", name: str = "m") -> tuple:
    mp = jdump(T / "ft" / f"{name}.json", manifest)
    if script.startswith("/"):
        p = subprocess.run([PY, script, str(mp)], capture_output=True, text=True, env={**os.environ, **ENV}, timeout=60)
    else:
        p = run(script, [mp], env=ENV, timeout=60)
    return p.returncode, last_json(p.stdout)


rc, out = facts({"repo_root": str(R), "claims": [{"id": "c1", "text": "routes by channel", "ref": "src/engine.py:10"},
                                                 {"id": "c2", "text": "an unmarked guess"}]}, name="unmarked")
s.check("C3 facts_trace: a claim with neither a resolvable path:line nor inferred:true FAILS (verdict FAIL, exit 1, "
        "rule C3.no_line_no_knowledge)",
        rc == 1 and out.get("verdict") == "FAIL" and out.get("unmarked_ids") == ["c2"]
        and any(f.get("rule") == "C3.no_line_no_knowledge" for f in out.get("findings", [])), out)
rc, out = facts({"repo_root": str(R), "claims": [{"id": "c1", "text": "routes by channel", "ref": "src/engine.py:10"},
                                                 {"id": "c2", "text": "copies may be stale", "inferred": True},
                                                 {"id": "c3", "text": "retries twice", "status": "inferred"}]},
                name="inferred")
s.check("C3 facts_trace: inferred claims with no line PASS and are counted and listed (inferred=2, read=1)",
        rc == 0 and out.get("status") == "PASS" and out.get("inferred") == 2 and out.get("inferred_ids") == ["c2", "c3"]
        and out.get("read") == 1 and "2 marked inferred" in out.get("summary", ""), out)
rc, out = facts({"repo_root": str(R), "claims": [{"id": "c1", "text": "x", "ref": "src/engine.py:999", "inferred": True}]},
                name="badinf")
s.check("C3 facts_trace: a broken reference is still flagged when the claim is also marked inferred",
        rc == 1 and out.get("unresolved") == 1, out)

# same verdict as v1.5.0 on v1.5.0-format claims files
OLD = T / "v150" / "scripts"
old_ok, old_why = False, ""
zp = ROOT.parent / "Coordn8_v2_handoff" / "_baseline" / "coordn8-v1.5.0.zip"
try:
    (OLD / "gates").mkdir(parents=True)
    (OLD / "lib").mkdir(parents=True)
    if zp.exists():
        with zipfile.ZipFile(zp) as z:
            names = z.namelist()
            pref = next((n[: -len("scripts/gates/facts_trace.py")] for n in names if n.endswith("scripts/gates/facts_trace.py")), None)
            if pref is not None:
                (OLD / "gates" / "facts_trace.py").write_bytes(z.read(pref + "scripts/gates/facts_trace.py"))
                (OLD / "lib" / "common.py").write_bytes(z.read(pref + "scripts/lib/common.py"))
                old_ok, old_why = True, "v1.5.0 release zip"
    if not old_ok:
        for f, dst in (("scripts/gates/facts_trace.py", OLD / "gates" / "facts_trace.py"),
                       ("scripts/lib/common.py", OLD / "lib" / "common.py")):
            g = subprocess.run(["git", "-C", str(ROOT), "show", f"HEAD:{f}"], capture_output=True, timeout=30)
            if g.returncode != 0:
                raise OSError(g.stderr.decode()[:200])
            dst.write_bytes(g.stdout)
        old_ok, old_why = True, "git HEAD (the v1.5.0 tree)"
except (OSError, zipfile.BadZipFile, subprocess.SubprocessError) as ex:
    old_why = f"no v1.5.0 copy available: {ex}"
V15_FIXTURES = {
    "mixed": {"repo_root": str(R), "claims": [{"id": "c1", "text": "handles routing", "ref": "src/engine.py:10"},
                                              {"id": "c2", "text": "bad line", "ref": "src/engine.py:999"},
                                              {"id": "c3", "text": "missing file", "ref": "src/nope.py:1"},
                                              {"id": "c4", "text": "no ref"}]},
    "all_good": {"repo_root": str(R), "claims": [{"id": "a", "text": "t", "ref": "src/engine.py:1-50"},
                                                 {"id": "b", "text": "t", "ref": "src/engine.py"}]},
    "empty": {"repo_root": str(R), "claims": []},
    "range_out": {"repo_root": str(R), "claims": [{"id": "a", "text": "t", "ref": "src/engine.py:40-51"}]},
}
if old_ok:
    same, diffs = True, {}
    for nm, man in V15_FIXTURES.items():
        rn, on = facts(man, name=f"new_{nm}")
        ro, oo = facts(man, script=str(OLD / "gates" / "facts_trace.py"), name=f"old_{nm}")
        key_n = (rn, on.get("status"), on.get("unresolved"), sorted(str(f.get("id")) for f in on.get("findings", [])))
        key_o = (ro, oo.get("status"), oo.get("unresolved"), sorted(str(f.get("id")) for f in oo.get("findings", [])))
        if key_n != key_o:
            same = False
            diffs[nm] = (key_n, key_o)
    s.check(f"C3 facts_trace: v1.5.0-format claims files get the same verdict as the v1.5.0 copy ({old_why}): "
            f"exit code, status, unresolved count and flagged ids on 4 fixtures", same, diffs)
else:
    s.check("C3 facts_trace: v1.5.0 comparison skipped honestly (no v1.5.0 copy readable here)", True, old_why)

# ======================================================================== C3 — the page ledger
P1 = T / "page" / "p.html"
r, summ = build(BASE, P1, extra=["--claims-out", T / "page" / "claims.json", "--plan-out", T / "page" / "plan.json"])
H1 = P1.read_text(encoding="utf-8") if P1.exists() else ""
main = H1[H1.find('<main'):H1.find('</main>')] if H1 else ""
last_open = main.rfind('<section id="')
s.check("C3 page: the page ENDS with the claims ledger (last section of <main> holds it, nothing teaching follows)",
        r.returncode == 0 and sections_of(H1)[-1:] == ["claims"] and "data-c8-claims" in main[last_open:]
        and main[last_open:].count("<section") == 1 and summ.get("claims_ledger", {}).get("last") is True,
        (sections_of(H1), r.stdout[-200:]))
rv = run("scripts/gates/check_claims.py", ["verify", P1, T / "page" / "claims.json"], env=ENV, timeout=60)
vj = last_json(rv.stdout)
s.check("C3 check_claims verify: the built page's ledger rows, tally and #c8-claims JSON equal claims.json",
        rv.returncode == 0 and vj.get("status") == "PASS" and vj.get("rows") == 5 and vj.get("inferred") == 1, vj)

inl = copy.deepcopy(BASE)
inl["sections"][1].setdefault("components", []).append(
    {"type": "claims", "rows": [{"id": "s1", "claim": "The resize keeps its aspect ratio.", "ref": "resize.py:12",
                                 "status": "read"},
                                {"id": "c1", "claim": BASE["claims"][0]["claim"], "ref": BASE["claims"][0]["ref"],
                                 "status": BASE["claims"][0]["status"]}]})
P2 = T / "inline" / "p.html"
r2, s2 = build(inl, P2, extra=["--claims-out", T / "inline" / "claims.json"])
cj2 = json.loads((T / "inline" / "claims.json").read_text()) if (T / "inline" / "claims.json").exists() else {}
rv2 = run("scripts/gates/check_claims.py", ["verify", P2, T / "inline" / "claims.json"], env=ENV, timeout=60)
s.check("C3 page: claims given inside a section are gathered into the final ledger once; --claims-out equals that ledger",
        r2.returncode == 0 and [c["id"] for c in cj2.get("claims", [])] == ["c1", "c2", "c3", "c4", "c5", "s1"]
        and rv2.returncode == 0, (cj2, last_json(rv2.stdout).get("findings")))
noc = copy.deepcopy(BASE)
noc.pop("claims")
P3 = T / "noclaims" / "p.html"
r3, s3 = build(noc, P3)
H3 = P3.read_text(encoding="utf-8") if P3.exists() else ""
s.check("C3 page: a page with no claims still ends with a ledger that says nothing was traced, and the build warns",
        r3.returncode == 0 and sections_of(H3)[-1:] == ["claims"] and "No claims are recorded for this page" in H3
        and any("C3" in w for w in s3.get("warnings", [])), (sections_of(H3), s3.get("warnings")))

# seeded ledger mismatches
man = json.loads((T / "page" / "claims.json").read_text())
seeds = {}
m1 = copy.deepcopy(man); m1["claims"][1]["text"] = "A stored copy is always re-fetched."; seeds["claim text differs"] = m1
m2 = copy.deepcopy(man); m2["claims"][0]["status"] = "inferred"; seeds["read/inferred basis differs"] = m2
m3 = copy.deepcopy(man); m3["claims"][2]["ref"] = "resize.py:31"; seeds["path:line differs"] = m3
m4 = copy.deepcopy(man); m4["claims"].append({"id": "c9", "text": "Extra.", "ref": "x.py:1", "status": "read"})
seeds["in the manifest but not in the ledger"] = m4
caught = {}
for prob, mm in seeds.items():
    rr = run("scripts/gates/check_claims.py", ["verify", P1, jdump(T / "seed" / f"{len(caught)}.json", mm)], env=ENV, timeout=60)
    jj = last_json(rr.stdout)
    caught[prob] = rr.returncode == 1 and any(f.get("problem") == prob for f in jj.get("findings", []))
s.check("C3 check_claims verify: a ledger mismatch is caught (changed text, flipped read/inferred, moved line, missing row)",
        all(caught.values()), caught)
moved = re.sub(r'(<section id="claims".*?</section>)(\s*)(</main>)',
               lambda m: m.group(1) + '\n<section id="late" class="c8-section" data-c8-section><h2>Late</h2><p>x</p></section>'
               + m.group(2) + m.group(3), H1, flags=re.S)
PL = T / "seed" / "late.html"
PL.write_text(moved, encoding="utf-8")
rl = run("scripts/gates/check_claims.py", ["verify", PL, T / "page" / "claims.json"], env=ENV, timeout=60)
s.check("C3 check_claims verify: a page whose ledger is NOT the last section is caught (rule C3.ledger_last)",
        rl.returncode == 1 and any(f.get("rule") == "C3.ledger_last" for f in last_json(rl.stdout).get("findings", [])),
        last_json(rl.stdout).get("findings"))

# the video sidecar
VD = T / "video" / "Coordn8"
VD.mkdir(parents=True)
(VD / "thumbs.mp4").write_bytes(b"\x00")
rvid0 = run("scripts/gates/check_claims.py", ["video", VD / "thumbs.mp4"], env=ENV, timeout=60)
re_ = run("scripts/gates/check_claims.py", ["emit", T / "page" / "claims.json", "--out", VD / "thumbs.claims.md"], env=ENV, timeout=60)
md = (VD / "thumbs.claims.md").read_text(encoding="utf-8") if (VD / "thumbs.claims.md").exists() else ""
s.check("C3 video: check_claims emit writes <slug>.claims.md (claim | path:line | read/inferred, with the tally)",
        re_.returncode == 0 and "| c1 | The lookup key is the image name plus the requested width. | `router.py:18` | read |" in md
        and "| inferred |" in md and "5 claims: 4 read in the code, 1 inferred." in md, md[:400])
rvid1 = run("scripts/gates/check_claims.py", ["video", VD / "thumbs.mp4", "--claims", T / "page" / "claims.json"], env=ENV, timeout=60)
s.check("C3 video: a video without <slug>.claims.md is flagged; with it (and matching the manifest) it passes",
        rvid0.returncode == 1 and "C3.video_sidecar" in rvid0.stdout and rvid1.returncode == 0, (rvid0.stdout[-200:], rvid1.stdout[-200:]))
tam = md.replace("| c2 | A stored copy", "| c2 | A cached copy")
(T / "seed" / "t.claims.md").write_text(tam, encoding="utf-8")
rmd = run("scripts/gates/check_claims.py", ["verify", T / "seed" / "t.claims.md", T / "page" / "claims.json"], env=ENV, timeout=60)
s.check("C3 video: an edited claims.md that no longer matches the manifest is caught", rmd.returncode == 1, rmd.stdout[-300:])
unm = {"claims": [{"id": "c1", "text": "read", "ref": "a.py:1"}, {"id": "c2", "text": "a guess with no line"}]}
ru = run("scripts/gates/check_claims.py", ["emit", jdump(T / "seed" / "unm.json", unm), "--out", T / "seed" / "u.claims.md"],
         env=ENV, timeout=60)
s.check("C3 video: emit refuses an unmarked claim and writes nothing", ru.returncode == 1 and not (T / "seed" / "u.claims.md").exists()
        and last_json(ru.stdout).get("unmarked_ids") == ["c2"], ru.stdout[-200:])

# ======================================================================== C4 — content rules
BAD = {"title": "Refunds", "conclusion": "First, the handler loads the order and then calls the policy engine.",
       "sections": [{"id": "internals", "title": "The `apply_policy()` helper", "summary": "Basically, this is how the policy runs.",
                     "what": "We changed this recently and it handles 1,000 refunds a day.\n\n"
                             "- cache: TTL 60 s, 512 MB, LRU eviction\n- queue: TTL 5 s, 64 MB, FIFO order\n"
                             "- store: no TTL, 2 GB, no eviction",
                     "components": [{"type": "stepper", "title": "A refund", "steps": [
                         {"title": "Request", "body": "The request arrives; eventually the customer gets their money back."},
                         {"title": "Check", "body": "The policy engine compares the total with the limit."},
                         {"title": "Pay", "body": "Finally the refund is paid."}]}]}]}
GOOD = {"title": "Refunds", "conclusion": "Refunds over 500 dollars always go to a human reviewer [[policy.py:40]].",
        "sections": [{"id": "what", "title": "What a refund request gets back",
                      "summary": "Every request ends approved, held for review, or refused.",
                      "what": {"html": "<table><tr><th>Store</th><th>TTL</th><th>Size</th><th>Eviction</th></tr>"
                                       "<tr><td>cache</td><td>60 s</td><td>512 MB</td><td>LRU</td></tr>"
                                       "<tr><td>queue</td><td>5 s</td><td>64 MB</td><td>FIFO</td></tr></table>"},
                      "components": [{"type": "stepper", "title": "A refund", "steps": [
                          {"title": "Request", "body": "The handler reads the order total."},
                          {"title": "Check", "body": "The policy engine compares the total with the limit."},
                          {"title": "Pay", "body": "In the end the refund is paid or held."}]}]}]}


def c4(obj, name: str, suffix: str = ".json", only=None) -> tuple:
    p = T / "c4" / f"{name}{suffix}"
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(obj, (dict, list)):
        p.write_text(json.dumps(obj), encoding="utf-8")
    else:
        p.write_text(obj, encoding="utf-8")
    before = hashlib.sha256(p.read_bytes()).hexdigest()
    rr = run("scripts/gates/check_page_content.py", [p] + (["--only", only] if only else []), env=ENV, timeout=60)
    j = last_json(rr.stdout)
    j["_unchanged"] = hashlib.sha256(p.read_bytes()).hexdigest() == before
    j["_rc"] = rr.returncode
    return j, [f["rule"] for f in j.get("findings", [])]


jb, rb = c4(BAD, "bad")
jg, rg = c4(GOOD, "good")
s.check("C4: a mechanism-first opening is flagged ('First, the handler loads … then calls …')",
        "C4.conclusion_first" in rb, jb.get("findings"))
s.check("C4: a conclusion-first opening passes", "C4.conclusion_first" not in rg, jg.get("findings"))
s.check("C4: a 3-attribute bullet list (>= 2 items) is flagged as a table",
        "C4.attributes_table" in rb, [f for f in jb.get("findings", []) if f["rule"] == "C4.attributes_table"])
s.check("C4: the same attributes as a table pass, and the good page has no leads at all (exit 0)",
        "C4.attributes_table" not in rg and jg.get("_rc") == 0 and rg == [], jg.get("findings"))
s.check("C4: delete-the-how-word test catches 'basically' and 'this is how'",
        sum(1 for x in rb if x == "C4.how_word") >= 2, [f["text"] for f in jb.get("findings", []) if f["rule"] == "C4.how_word"])
s.check("C4: effect-at-stage catches a non-final stage that describes the final result ('eventually …'); "
        "the last stage may state it",
        any(f["rule"] == "C4.effect_at_stage" and "step 1" in f["where"] for f in jb.get("findings", []))
        and not any(f["rule"] == "C4.effect_at_stage" and "step 3" in f["where"] for f in jb.get("findings", []))
        and "C4.effect_at_stage" not in rg, [f for f in jb.get("findings", []) if f["rule"] == "C4.effect_at_stage"])
s.check("C4: vague time words ('recently') and an unsourced round figure ('1,000') are flagged; "
        "the sourced figure on the good page is not",
        "C4.exact_values" in rb and "C4.estimate_marked" in rb and "C4.estimate_marked" not in rg, rb)
s.check("C4: outside-in: a first section named after an internal helper is flagged", "C4.outside_in" in rb, rb)
s.check("C4: the checker is report-only: it never edits its input (bytes unchanged) and exits REVIEW, not ERROR",
        jb.get("_unchanged") and jg.get("_unchanged") and jb.get("_rc") == 1, (jb.get("_rc"), jb.get("_unchanged")))
jf, rf = c4(BASE, "fixture")
s.check("C4: no false leads on the page fixture's content.json (all 7 rules run)", rf == [] and len(jf.get("rules_run", [])) == 7,
        jf.get("findings"))
jh, rh = c4(H1, "fixture_page", suffix=".html")
s.check("C4: no false leads on the built fixture page (.html input: conclusion, sections, stepper, ledger skipped)",
        rh == [] and jh.get("kind") == "html" and jh.get("sections") == 3, jh.get("findings"))
mdbad = ("# Refunds\n\nWhen a request arrives, the router reads the order and calls the policy.\n\n## Stores\n\n"
         "- cache: TTL 60 s, 512 MB, LRU\n- queue: TTL 5 s, 64 MB, FIFO\n\n1. The request comes in; the final result is a payment.\n"
         "2. The policy decides.\n")
jm, rm_ = c4(mdbad, "bad", suffix=".md")
s.check("C4: markdown input works (mechanism-first opening, attribute bullets, effect-at-stage in a numbered list)",
        {"C4.conclusion_first", "C4.attributes_table", "C4.effect_at_stage"} <= set(rm_), rm_)
inv = copy.deepcopy(GOOD)
inv["sections"][0]["custom_html"] = ('<div class="ring-of-stores"><p>Every store in the ring keeps its own copy.</p>'
                                     '<svg viewBox="0 0 10 10"><circle cx="5" cy="5" r="4"/></svg></div>')
ji, ri = c4(inv, "invented")
s.check("C4/H1: a section with an invented custom layout is judged on its words only and passes", ri == [], ji.get("findings"))

# ======================================================================== C5 — modes, dials, scope, write guard
REPO = T / "proj"
(REPO / "app").mkdir(parents=True)
(REPO / "other").mkdir()
(REPO / "app" / "__init__.py").write_text("", encoding="utf-8")
(REPO / "app" / "refunds.py").write_text("from .policy import check_limit\n\n\ndef approve_refund(order):\n"
                                         "    if check_limit(order):\n        return 'approved'\n    return 'held'\n",
                                         encoding="utf-8")
(REPO / "app" / "policy.py").write_text("from app.limits import LIMIT\n\n\ndef check_limit(order):\n"
                                        "    return order['total'] <= LIMIT\n", encoding="utf-8")
(REPO / "app" / "limits.py").write_text("LIMIT = 500\n", encoding="utf-8")
(REPO / "other" / "report.py").write_text("def monthly_report():\n    return 1\n", encoding="utf-8")
(REPO / "web").mkdir()
(REPO / "web" / "ui.js").write_text("import { fmt } from './fmt.js';\nexport function showRefund(r) { return fmt(r); }\n",
                                    encoding="utf-8")
(REPO / "web" / "fmt.js").write_text("export function fmt(r) { return String(r); }\n", encoding="utf-8")
DESC = {"id": "refund-flow", "title": "How a refund is decided",
        "text": "a refund request is checked against the limit and approved or held",
        "items": [{"id": "request", "label": "refund_request", "plain": "A refund is asked for", "ref": "app/refunds.py:4", "importance": 3},
                  {"id": "check", "label": "check_limit", "plain": "Is it under the limit?", "ref": "app/policy.py:4", "importance": 3},
                  {"id": "approve", "label": "approved", "plain": "Approved", "ref": "app/refunds.py:6", "importance": 2},
                  {"id": "hold", "label": "held", "plain": "Held for review", "ref": "app/refunds.py:7", "importance": 2},
                  {"id": "log", "label": "audit_log", "plain": "Written to the log", "inferred": True},
                  {"id": "notify", "label": "notify_customer", "plain": "Customer told", "inferred": True},
                  {"id": "limit", "label": "LIMIT", "plain": "The limit", "ref": "app/limits.py:1"},
                  {"id": "metrics", "label": "metrics", "plain": "Counted", "inferred": True}],
        "relations": [{"from": "request", "to": "check"}, {"from": "check", "to": "approve", "label": "under"},
                      {"from": "check", "to": "hold", "label": "over"}, {"from": "limit", "to": "check"},
                      {"from": "approve", "to": "log"}, {"from": "hold", "to": "log"}, {"from": "log", "to": "notify"},
                      {"from": "notify", "to": "metrics"}]}
DP = jdump(T / "inputs" / "desc.json", DESC)


def tree(root: Path) -> dict:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def modes(args: list) -> tuple:
    rr = run("scripts/design/modes.py", args, env=ENV, timeout=120)
    return rr.returncode, last_json(rr.stdout)


before_all = tree(REPO)
rh_, dh = modes(["diagram", "--repo", REPO, "--desc", DP, "--detail", "high", "--slug", "hi"])
rl_, dl = modes(["diagram", "--repo", REPO, "--desc", DP, "--detail", "low", "--slug", "lo"])
HD = (REPO / "Coordn8" / "lo.refund-flow.diagram.html")
HDt = HD.read_text(encoding="utf-8") if HD.exists() else ""
rvd = run("scripts/gates/check_claims.py", ["verify", HD, REPO / "Coordn8" / "_build" / "lo.refund-flow.diagram.claims.json",
                                            "--repo", REPO], env=ENV, timeout=60)
s.check("C5 diagram mode: exactly one figure plus its claims (the ledger ends the page and matches the claims file, "
        "every path:line resolves in the repo)",
        rl_ == 0 and dl.get("figures") == 1 and HDt.count('class="c8-flow"') == 1 and sections_of(HDt)[-1:] == ["claims"]
        and rvd.returncode == 0, (dl.get("summary"), last_json(rvd.stdout).get("findings")))
titles = re.findall(r'<g class="c8-node" data-node-id="([^"]+)" data-ref="([^"]*)"[^>]*><title>([^<]+)</title>', HDt)
s.check("C5 diagram mode: hovering a node shows its path:line (an SVG <title> on every node carries it)",
        len(titles) == dl.get("nodes") and all(ref and ref in t for _, ref, t in titles), titles)
s.check("C5 dials: low detail halves the nodes (8 -> 4) and renames none (every kept node has its high-detail name)",
        dh.get("nodes") == 8 and dl.get("nodes") == 4 and dl.get("source_nodes") == 8
        and all(dl["names"][k] == dh["names"][k] for k in dl.get("kept", [])), (dh.get("names"), dl.get("names")))
rf_, dfd = modes(["diagram", "--repo", REPO, "--desc", DP, "--detail", "low", "--audience", "field", "--slug", "lof"])
s.check("C5 dials: the audience dial changes what nodes are called, never how many are kept",
        dfd.get("kept") == dl.get("kept") and dfd.get("names", {}).get("check") == "check_limit"
        and dl.get("names", {}).get("check") == "Is it under the limit?", (dfd.get("names"), dl.get("names")))
nd = copy.deepcopy(DESC)
nd["items"][2].pop("ref")
rn_, dn = modes(["diagram", "--repo", REPO, "--desc", jdump(T / "inputs" / "noref.json", nd), "--slug", "noref",
                 "--detail", "high"])
s.check("C5/C3 diagram mode: a drawn node with no path:line and no inferred mark is refused, nothing written",
        rn_ == 3 and dn.get("writes") == [] and not (REPO / "Coordn8" / "noref.refund-flow.diagram.html").exists(), dn.get("summary"))

snap = tree(REPO)
ra_, da = modes(["answer", "--repo", REPO, "--topic", "how a refund is approved"])
s.check("C5 answer mode: chat only — writes zero files (repo tree byte-identical, writes == [])",
        ra_ == 0 and da.get("writes") == [] and tree(REPO) == snap and da.get("deliver") == "chat", da.get("summary"))
ra2, da2 = modes(["answer", "--repo", REPO, "--topic", "refund", "--out-dir", REPO / "Coordn8" / "x"])
s.check("C5 answer mode: refuses an --out-dir rather than write anything", ra2 == 3 and tree(REPO) == snap, da2.get("summary"))
sc = da.get("scope", {})
s.check("C5 scope: --topic traces from the topic's entry symbols through every in-repo import (refunds -> policy -> "
        "limits) and reports the rest as skipped",
        sc.get("files") == ["app/limits.py", "app/policy.py", "app/refunds.py"]
        and {"other/report.py", "web/ui.js", "app/__init__.py"} <= set(sc.get("skipped", []))
        and sc.get("entries") == [{"symbol": "approve_refund", "ref": "app/refunds.py:4"}], sc)
_, dj = modes(["answer", "--repo", REPO, "--entry", "showRefund"])
s.check("C5 scope: --entry names the symbol directly; JS relative imports are followed too",
        dj.get("scope", {}).get("files") == ["web/fmt.js", "web/ui.js"], dj.get("scope"))
_, dall = modes(["answer", "--repo", REPO, "--scope", "all"])
_, dnone = modes(["answer", "--repo", REPO, "--scope", "topic", "--topic", "zzzz qqqq"])
s.check("C5 scope: the whole codebase only with --scope all; an unmatched topic puts nothing in scope and says why",
        dall.get("scope", {}).get("skipped_count") == 0 and len(dall.get("scope", {}).get("files", [])) == 7
        and dnone.get("scope", {}).get("files") == [] and "whole codebase" in dnone.get("scope", {}).get("stop_condition", ""),
        (dall.get("scope", {}).get("files"), dnone.get("scope")))

rp_, dp_ = modes(["page", "--repo", REPO, "--content", FIX / "content.json", "--slug", "pg"])
rv_, dv_ = modes(["video", "--repo", REPO, "--claims", T / "page" / "claims.json", "--slug", "vid"])
s.check("C5 page and video modes: the page ends with its ledger; the video mode writes <slug>.claims.md",
        rp_ in (0, 1) and (REPO / "Coordn8" / "pg.html").exists()
        and sections_of((REPO / "Coordn8" / "pg.html").read_text(encoding="utf-8"))[-1:] == ["claims"]
        and rv_ == 0 and (REPO / "Coordn8" / "vid.claims.md").exists(), (dp_.get("summary"), dv_.get("summary")))
after_all = tree(REPO)
outside = sorted(k for k in set(after_all) | set(before_all)
                 if not k.startswith("Coordn8/") and after_all.get(k) != before_all.get(k))
all_writes = [w for d in (dh, dl, dfd, dp_, dv_) for w in d.get("writes", [])]
s.check("C5 write guard: page, diagram and video modes wrote nothing outside <repo>/Coordn8/ (repo diff + every reported path)",
        outside == [] and all_writes and all(Path(w).resolve().is_relative_to((REPO / "Coordn8").resolve())
                                              if hasattr(Path, "is_relative_to") else
                                              str(Path(w).resolve()).startswith(str((REPO / "Coordn8").resolve()) + os.sep)
                                              for w in all_writes), (outside, all_writes[:3]))
ro_, do_ = modes(["page", "--repo", REPO, "--content", FIX / "content.json", "--out-dir", T / "elsewhere"])
(T / "elsewhere2").mkdir()
os.symlink(T / "elsewhere2", REPO / "Coordn8" / "link")
rs_, ds_ = modes(["page", "--repo", REPO, "--content", FIX / "content.json", "--out-dir", REPO / "Coordn8" / "link"])
s.check("C5 write guard: an --out-dir outside Coordn8/, or a symlink inside it that points out, is refused (exit 3, nothing written)",
        ro_ == 3 and rs_ == 3 and not (T / "elsewhere").exists() and list((T / "elsewhere2").iterdir()) == [],
        (do_.get("summary"), ds_.get("summary")))

# ======================================================================== C6 — understanding checks
C6 = copy.deepcopy(BASE)
C6["sections"][0].setdefault("components", []).extend([
    {"type": "predict", "id": "pr", "prompt": "The cache misses. What does the service do next?",
     "hint": "Think about where the original lives.", "reveal": "It asks the origin store for the original image."},
    {"type": "assumption_toggle", "id": "as", "assumption": "The original is always in the origin store.",
     "holds": "Every miss is resized, kept and served.", "breaks": "A miss for a missing original answers 404 and keeps nothing.",
     "highlight": ["not-found"]},
    {"type": "self_check", "id": "sc", "questions": [
        {"q": "Where is a repeat request served from?", "options": ["The cache", "The origin store"], "answer": 0,
         "explain": "The first request keeps the resized copy, so the second one finds it in the cache."},
        {"q": "What happens when the original is missing?", "options": ["Status 500", "Status 404"], "answer": "Status 404",
         "explain": "The service answers not found and stores nothing."},
        {"q": "Is the same size resized twice?", "options": ["Yes", "No"], "answer": 1,
         "explain": "The copy from the first resize is kept and reused."}]}])
PC = T / "c6" / "p.html"
rc6, sc6 = build(C6, PC, extra=["--plan-out", T / "c6" / "plan.json"])
HC = PC.read_text(encoding="utf-8") if PC.exists() else ""
if rc6.returncode == 0:
    bg("a11y_c6", [PY, str(ROOT / "scripts/gates/check_page_a11y.py"), str(PC), "--json", str(T / "c6" / "a11y.json"),
                   "--only", "static,network,contrast,focus,affordance,phone,print"], T / "c6" / "a11y.log")
s.check("C6: all three components render (predict pause, assumption switch, self-check with 3 explained answers)",
        rc6.returncode == 0 and 'data-c8-widget="predict"' in HC and 'role="switch" aria-checked="false"' in HC
        and HC.count("data-c8-q ") == 3 and HC.count("<summary>Answer and why</summary>") == 3
        and 'aria-expanded="false"' in HC and sc6.get("components_inlined") == ["predict", "assumption_toggle", "self_check"],
        (rc6.stdout[-300:], sc6.get("components_inlined")))
s.check("C6: without script nothing is lost: the reveal and both assumption states are in the markup unhidden, the "
        "controls that need script start hidden, the answers sit in native <details>",
        re.search(r'<div class="c8-predict-reveal" id="pr-reveal"[^>]*>', HC) is not None
        and " hidden" not in re.search(r'<div class="c8-predict-reveal" id="pr-reveal"[^>]*>', HC).group(0)
        and 'data-c8-assume-state="holds" id="as-holds">' in HC and 'data-c8-assume-state="breaks" id="as-breaks">' in HC
        and re.search(r'data-c8-predict-btn[^>]*hidden data-c8-js-only', HC.replace("\n", " ")) is not None
        and re.search(r'data-c8-assume-switch hidden data-c8-js-only', HC) is not None)
s.check("C6: the components are optional — a page without them carries none of their markup, CSS or script",
        all(x not in H1 for x in ("c8-predict", "c8-assume", "c8-selfcheck", 'register("self_check"')) and r.returncode == 0)
rvd6 = run("scripts/gates/visual_density.py", [T / "c6" / "plan.json", "--kind", "page"], env=ENV, timeout=60)
s.check("C6: the page plan with the components still passes visual_density (each says what it explains)",
        rvd6.returncode == 0, last_json(rvd6.stdout).get("summary"))
bq = copy.deepcopy(C6)
bq["sections"][0]["components"][-1]["questions"][1].pop("explain")
rbq, sbq = build(bq, T / "c6bad" / "p.html")
two = copy.deepcopy(C6)
two["sections"][0]["components"][-1]["questions"] = two["sections"][0]["components"][-1]["questions"][:2]
rtw, stw = build(two, T / "c6two" / "p.html")
s.check("C6: a self-check answer without its explanation is refused; two questions are kept with a warning (a menu, not a fence)",
        rbq.returncode == 3 and "no explanation" in sbq.get("summary", "") and rtw.returncode == 0
        and any("2 questions" in w for w in stw.get("warnings", [])), (sbq.get("summary"), stw.get("warnings")))

# keyboard, in a real browser
try:
    from playwright.sync_api import sync_playwright
    have_pw = True
except ImportError:
    have_pw = False
kb = {}
pw_err = ""
if have_pw and rc6.returncode == 0:
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", ENV.get("PLAYWRIGHT_BROWSERS_PATH", ""))
    try:
        with sync_playwright() as p:
            br = p.chromium.launch()
            pg = br.new_page(viewport={"width": 1280, "height": 900})
            pg.goto(PC.as_uri())

            def tab_to(selector: str, limit: int = 120) -> bool:
                for _ in range(limit):
                    pg.keyboard.press("Tab")
                    if pg.evaluate("(s) => document.activeElement && document.activeElement.matches(s)", selector):
                        return True
                return False

            q = pg.evaluate
            kb["predict_starts_closed"] = q("() => document.querySelector('#pr-reveal').hidden === true")
            kb["predict_tab"] = tab_to("#pr [data-c8-predict-btn]")
            pg.keyboard.press("Enter")
            kb["predict_enter_opens"] = q("() => document.querySelector('#pr [data-c8-predict-btn]').getAttribute('aria-expanded') === 'true'"
                                         " && !document.querySelector('#pr-reveal').hidden")
            pg.keyboard.press("Space")
            kb["predict_space_closes"] = q("() => document.querySelector('#pr [data-c8-predict-btn]').getAttribute('aria-expanded') === 'false'"
                                          " && document.querySelector('#pr-reveal').hidden")
            kb["switch_tab"] = tab_to("#as [role=switch]")
            pg.keyboard.press("Space")
            kb["switch_space_flips"] = q("() => document.querySelector('#as [role=switch]').getAttribute('aria-checked') === 'true'"
                                        " && document.querySelector('#as-holds').hidden && !document.querySelector('#as-breaks').hidden"
                                        " && document.querySelector(\"[data-node-id='not-found']\").classList.contains('c8-hl')")
            kb["switch_announces"] = q("() => document.querySelector('#as [data-c8-assume-live]').textContent.length > 0")
            pg.keyboard.press("Enter")
            kb["switch_enter_flips_back"] = q("() => document.querySelector('#as [role=switch]').getAttribute('aria-checked') === 'false'"
                                             " && !document.querySelector('#as-holds').hidden")
            kb["option_tab"] = tab_to("#sc [data-c8-option]")
            pg.keyboard.press("Enter")
            kb["option_enter_answers"] = q("() => { const q = document.querySelector('#sc [data-c8-q]');"
                                          " return q.querySelector('[data-c8-option=\"0\"]').getAttribute('aria-pressed') === 'true'"
                                          " && /Right/.test(q.querySelector('[data-c8-q-feedback]').textContent)"
                                          " && q.querySelector('details').open; }")
            pg.keyboard.press("Tab")
            pg.keyboard.press("Space")
            kb["wrong_option_explains"] = q("() => { const q = document.querySelector('#sc [data-c8-q]');"
                                           " return /Not quite: the answer is .The cache./.test(q.querySelector('[data-c8-q-feedback]').textContent); }")
            kb["score_counts"] = q("() => /1 of 3 answered, 0 right/.test(document.querySelector('#sc [data-c8-selfcheck-score]').textContent)")
            br.close()
    except Exception as ex:  # noqa: BLE001
        pw_err = f"{type(ex).__name__}: {ex}"[:300]
if kb:
    s.check("C6 keyboard (real browser): Tab reaches the predict button; Enter reveals (aria-expanded=true), Space hides again",
            kb.get("predict_starts_closed") and kb.get("predict_tab") and kb.get("predict_enter_opens") and kb.get("predict_space_closes"), kb)
    s.check("C6 keyboard (real browser): Tab reaches the role=switch; Space flips it (aria-checked, the breaking state shown, "
            "the named flow node marked, announced); Enter flips it back",
            kb.get("switch_tab") and kb.get("switch_space_flips") and kb.get("switch_announces") and kb.get("switch_enter_flips_back"), kb)
    s.check("C6 keyboard (real browser): Tab reaches a self-check option; Enter answers it (aria-pressed, 'Right', the "
            "explanation opens); Space on a wrong option says the right answer; the score counts",
            kb.get("option_tab") and kb.get("option_enter_answers") and kb.get("wrong_option_explains") and kb.get("score_counts"), kb)
else:
    s.check("C6 keyboard checks skipped honestly: no Playwright/browser here", not have_pw, pw_err or "playwright missing")

if "a11y_c6" in BG:
    ra11, _log = bg_wait("a11y_c6")
    aj = json.loads((T / "c6" / "a11y.json").read_text()) if (T / "c6" / "a11y.json").exists() else {}
    if ra11 == 2:
        s.check("C6 a11y gate live checks skipped honestly: no headless browser", True, aj.get("summary"))
    else:
        s.check("C6: the a11y gate passes the page with all three components (contrast both skins, focus order and ring, "
                "honest affordance: every control responds, phone width, print)",
                ra11 == 0 and aj.get("status") == "PASS", aj.get("failed"))

# ======================================================================== existing page suite stays green
rtp, tlog = bg_wait("test_page")
res_line = next((ln for ln in reversed(tlog.splitlines()) if ln.startswith("RESULT:")), "")
s.check("tests/v2/test_page.py stays green with the C3 ledger and C6 components in build_page.py",
        rtp == 0 and " 0 failed" in res_line, res_line or tlog[-400:])

sys.exit(s.finish())
