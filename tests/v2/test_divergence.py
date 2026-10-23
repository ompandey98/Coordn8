#!/usr/bin/env python3
"""Divergence + anti-repetition: H2 (divergence.json, separate critic, never lower the bar),
H3 (real seeded draws, choice history penalty), and the H1 freedom parts (invented techniques
pass; no option rule consults the catalog).

Hermetic: COORDN8_HOME / CLAUDE_HOME / HOME point into a temp dir for the whole run; the
real ~/.coordn8 is checked to be untouched. Nothing is left on disk.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import inspect
import json
import os
import pwd
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import PY, ROOT, Suite, run, sandbox_env, tmpdir  # noqa: E402

SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

_TMP = tmpdir()
TMP = Path(_TMP.name)
ENV = sandbox_env(TMP)
REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)  # never $HOME: the suite overrides it
REAL_CHOICES = REAL_HOME / ".coordn8" / "memory" / "choices.jsonl"


def _stat(p: Path):
    return (p.stat().st_size, p.stat().st_mtime_ns) if p.exists() else None


REAL_BEFORE = _stat(REAL_CHOICES)
os.environ.update(ENV)


def _load(name: str):
    try:
        return __import__(f"design.{name}", fromlist=[name])
    except Exception:  # design/__init__.py absent or broken: import by path
        spec = importlib.util.spec_from_file_location(f"c8_design_{name}", SCRIPTS / "design" / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod


seed = _load("seed")
history = _load("history")
divergence = _load("divergence")

s = Suite("divergence")
CAT_PATH = ROOT / "data" / "idea_techniques.json"
CAT = json.loads(CAT_PATH.read_text(encoding="utf-8"))

# ====================================================================== catalog (menu)
NAMED = ["provocation", "constraint-then-relax", "inversion", "assumption-reversal", "analogy-by-structure",
         "role-swap", "time-shift", "negative-space", "synthesis"]
ids = [t["id"] for t in CAT["techniques"]]
s.check("catalog lists >= 9 techniques including the nine named ones",
        len(ids) >= 9 and all(n in ids for n in NAMED), f"{len(ids)} {sorted(set(NAMED) - set(ids))}")
s.check("catalog carries every reference technique (>= 15) plus its own additions",
        len(ids) >= 15 and len(set(ids)) == len(ids), str(len(ids)))
bad = [t["id"] for t in CAT["techniques"]
       if not (isinstance(t.get("how_to"), list) and len(t["how_to"]) >= 3 and t.get("good_for")
               and all(k in (t.get("example") or {}) for k in ("problem", "move", "result"))
               and t.get("failure_modes"))]
s.check("every technique has how_to steps, good_for problem types, a worked mini-example and failure modes",
        not bad, str(bad))
pts = set(CAT["problem_types"])
needed_pts = {"design", "narrative-spine", "explanation", "visual-metaphor", "structure", "naming"}
cover = {p: [t["id"] for t in CAT["techniques"] if p in t["good_for"]] for p in pts}
s.check("every problem type (design, narrative spine, explanation, visual metaphor, structure, naming, ...) "
        "is served by >= 3 techniques, so 3 different techniques can always be drawn",
        needed_pts <= pts and all(len(v) >= 3 for v in cover.values()),
        str({k: len(v) for k, v in cover.items()}))
s.check("every good_for entry is a declared problem type",
        all(set(t["good_for"]) <= pts for t in CAT["techniques"]), "")
P = CAT["pools"]
s.check("seeded stimulus pools: constraints >= 30, distant fields >= 40, words/objects >= 60, no duplicates",
        len(P["constraints"]) >= 30 and len(P["fields"]) >= 40 and len(P["words"]) >= 60
        and all(len(set(map(str.lower, v))) == len(v) for v in P.values()),
        str({k: len(v) for k, v in P.items()}))
s.check("the catalog's critic tests are exactly the three rejection tests",
        [c["id"] for c in CAT["critic_tests"]] == ["user-could-think-of-it", "known-default", "surprising-not-useful"]
        and list(divergence.CRITIC_TESTS) == [c["id"] for c in CAT["critic_tests"]], "")
rules = " ".join(CAT["rules"]).lower()
s.check("catalog rules say never lower the bar, separate critic, pivotal decisions only (no 'when in doubt, inject')",
        "never lower the bar" in rules and "different agents" in rules and "pivotal" in rules
        and "when in doubt" not in CAT_PATH.read_text(encoding="utf-8").lower(), "")

# ====================================================================== seed (H3)
s.check("derive_seed is a SHA-256 of (project slug, build count), not Python's hash()",
        seed.derive_seed("payments-api", 4)
        == int(hashlib.sha256(b"coordn8-seed|payments-api|4").hexdigest()[:12], 16)
        and not any(isinstance(n, ast.Call) and getattr(n.func, "id", "") == "hash"
                    for n in ast.walk(ast.parse(Path(seed.__file__).read_text(encoding="utf-8")))), "")
outs = set()
for hs in ("0", "1", "12345"):
    r = subprocess.run([PY, "-c", "import sys; sys.path.insert(0, sys.argv[1]); from design.seed import derive_seed;"
                        " print(derive_seed('payments-api', 4))", str(SCRIPTS)],
                       capture_output=True, text=True, env={**os.environ, "PYTHONHASHSEED": hs}, timeout=60)
    outs.add(r.stdout.strip())
s.check("derive_seed is identical across processes with different PYTHONHASHSEED",
        len(outs) == 1 and outs != {""} and outs == {str(seed.derive_seed("payments-api", 4))}, str(outs))
seeds = [seed.derive_seed("payments-api", k) for k in range(1, 21)]
s.check("different builds of one project get different seeds (builds 1..20 all distinct)",
        len(set(seeds)) == 20, str(seeds[:3]))
s.check("the same build number of different projects gets different seeds",
        len({seed.derive_seed(p, 1) for p in ("payments-api", "billing-api", "ledger-api")}) == 3, "")

d = seed.Draw(4471)
s.check("draws use only random(): the index stream for seed 4471 is pinned (version-stable)",
        [d._index(1000) for _ in range(6)] == [916, 653, 87, 849, 513, 731], "")
d = seed.Draw(4471)
t1 = d.technique("design")
d.constraint(); d.field(); d.word(); d.oblique(); d.sample("words", 3); d.sample(["a", "b", "c", "d"], 2)
s.check("every draw is appended to Draw.log with kind, args and value",
        [e["kind"] for e in d.log] == ["technique", "constraint", "field", "word", "oblique", "sample", "sample"]
        and all({"i", "kind", "args", "value"} <= set(e) for e in d.log), str(d.log[:2]))
rec = d.record()
s.check("recorded draws replay exactly from the seed (replay() returns no mismatch)",
        seed.replay(rec["seed"], rec["draws"]) == [], "")
tampered = copy.deepcopy(rec["draws"])
tampered[2]["value"] = "a field the model liked"
s.check("a hand-edited draw does not replay (model-picked 'randomness' is caught)",
        len(seed.replay(rec["seed"], tampered)) == 1, "")

okmatch = True
for p in pts:
    dd = seed.Draw(seed.derive_seed("match", 1))  # same seed, every problem type
    for _ in range(40):
        t = dd.technique(p)
        okmatch &= p in t["good_for"]
s.check("technique draws are matched to the problem type (every drawn technique lists it in good_for)", okmatch, "")
sets_ok = all(len({o["technique"] for o in seed.Draw(k).draw_set("opening", 3)}) == 3 for k in range(50))
s.check("draw_set(problem, 3) always yields 3 different techniques, each with its stimulus if it takes one", sets_ok, "")
du = seed.Draw(7)
tu = du.technique("a-problem-type-nobody-listed")
s.check("an unlisted problem type still draws (menu, not fence), recorded as matched=false",
        tu["id"] in ids and du.log[-1]["matched"] is False, "")

r1 = run("scripts/design/seed.py", ["draw", "--problem", "design", "--n", "3", "--seed", "4471", "--json"], env=ENV, timeout=60)
r2 = run("scripts/design/seed.py", ["draw", "--problem", "design", "--n", "3", "--seed", "4471", "--json"],
         env={**ENV, "PYTHONHASHSEED": "99"}, timeout=60)
same = False
try:
    j1, j2 = json.loads(r1.stdout), json.loads(r2.stdout)
    inproc = seed.Draw(4471)
    inproc.draw_set("design", 3)
    same = (j1["draws"] == j2["draws"] == inproc.record()["draws"] and j1["seed"] == 4471)
except (ValueError, KeyError) as ex:
    j1 = {"err": str(ex)}
s.check("same seed reproduces the same draws in a fresh process (two CLI runs + in-process all equal)",
        r1.returncode == 0 and same, (r1.stderr or str(j1))[:300])
ALT_PY = sorted((REAL_HOME / ".local" / "share" / "uv" / "python").glob("cpython-3.1[1-9]*/bin/python3"))
if ALT_PY:
    ra = subprocess.run([str(ALT_PY[-1]), "-B", str(ROOT / "scripts" / "design" / "seed.py"), "draw", "--problem",
                         "design", "--n", "3", "--seed", "4471", "--json"], capture_output=True, text=True,
                        env=dict(os.environ), timeout=60)
    try:
        alt_same = json.loads(ra.stdout)["draws"] == j1.get("draws")
    except (ValueError, KeyError):
        alt_same = False
    s.check("same seed gives the same draws on a second local Python version (3.11+ vs this 3.9)", alt_same,
            (ALT_PY[-1].parts[-3] + " " + ra.stderr[-200:]))
else:
    s.check("cross-version draw check SKIPPED: no second local Python found (reason recorded)", True, "")
r3 = run("scripts/design/seed.py", ["draw", "--problem", "narrative-spine", "--project", "payments-api",
                                    "--build", "2", "--json"], env=ENV, timeout=60)
try:
    j3 = json.loads(r3.stdout)
except ValueError:
    j3 = {}
s.check("seed.py draw --project/--build derives the seed with derive_seed and records it",
        j3.get("seed") == seed.derive_seed("payments-api", 2) and j3.get("seed_source") == "derived"
        and len(j3.get("options", [])) == 3, r3.stderr[:200])

# ====================================================================== history penalty (H3)
H = [{"project": "p", "build": 4, "palette_family": "ink-wash", "opening_scene": "map-zoom", "kit": "k-ink",
      "spine": "problem-first", "voice": "v-warm"},
     {"project": "p", "build": 3, "palette_family": "blueprint", "opening_scene": "question-card", "kit": "k-blue",
      "spine": "tour", "voice": "v-brisk"},
     {"project": "p", "build": 2, "palette_family": "editorial-light", "opening_scene": "cold-open", "kit": "k-ed",
      "spine": "mystery", "voice": "v-calm"},
     {"project": "p", "build": 1, "palette_family": "harbour-dusk", "opening_scene": "single-object",
      "kit": "k-dusk", "spine": "timeline", "voice": "v-deep"}]
pen, why = history.penalty("palette_family", "editorial-light", H)
s.check("penalty: a palette family used 3 builds ago (inside the last 3) costs exactly -0.35, with a reason",
        pen == -0.35 and "editorial-light" in why, f"{pen} {why}")
s.check("penalty: a palette family used 4 builds ago is free with window=3 and costs -0.35 with window=4",
        history.penalty("palette_family", "harbour-dusk", H) == (0.0, "")
        and history.penalty("palette_family", "harbour-dusk", H, window=4)[0] == -0.35, "")
s.check("penalty: kit, spine and voice inside the last 3 builds each cost -0.35",
        all(history.penalty(k, v, H)[0] == -0.35 for k, v in
            (("kit", "k-blue"), ("spine", "mystery"), ("voice", "v-warm"))), "")
s.check("penalty: an opening scene equal to the LAST build's costs exactly -0.2",
        history.penalty("opening_scene", "map-zoom", H)[0] == -0.2, "")
s.check("penalty: an opening scene used 2 builds ago (not the last) is free",
        history.penalty("opening_scene", "question-card", H) == (0.0, ""), "")
s.check("penalty returns (0.0, '') for an unused value and for an empty history",
        history.penalty("palette_family", "never-used", H) == (0.0, "")
        and history.penalty("opening_scene", "x", []) == (0.0, ""), "")
pw = history.penalty("palette_family", "ink-wash", H, warranted="the brief asks for the house ink style")
s.check("a repeat the subject warrants is not penalised and the reason is recorded",
        pw[0] == 0.0 and "house ink style" in pw[1], str(pw))
try:
    history.penalty("font", "x", H)
    raised = False
except ValueError:
    raised = True
s.check("penalty rejects an unknown kind instead of guessing", raised, "")
rk = history.rank("palette_family", [("editorial-light", 0.9), ("signal-bold", 0.6)], H, seed=1)
s.check("rank applies the SPEC formula: score = fit - 0.35 (0.9 -> 0.55) and re-orders by it",
        [r["id"] for r in rk] == ["signal-bold", "editorial-light"] and rk[1]["score"] == 0.55, str(rk))
tie = [("a", 0.5), ("b", 0.5), ("c", 0.5), ("d", 0.5)]
o1 = [r["id"] for r in history.rank("kit", tie, [], seed=11)]
o1b = [r["id"] for r in history.rank("kit", tie, [], seed=11)]
others = {tuple(r["id"] for r in history.rank("kit", tie, [], seed=k)) for k in range(30)}
s.check("ties are broken by the recorded seed: same seed same order, different seeds different orders",
        o1 == o1b and len(others) > 1, str(others)[:200])
s.check("pair_repeats finds a (palette family, opening scene) pair used twice",
        history.pair_repeats(H + [dict(H[1], build=0)])[0]["pair"] == ["blueprint", "question-card"]
        and history.pair_repeats(H) == [], "")

# ---- 5 consecutive fixture builds on similar subjects, history in the sandboxed COORDN8_HOME
FAMS = [("editorial-light", 0.90), ("blueprint", 0.86), ("ink-wash", 0.80), ("harbour-dusk", 0.62),
        ("signal-bold", 0.55)]
OPENS = [("cold-open-on-failure", 0.90), ("question-card", 0.84), ("single-object", 0.78), ("map-zoom", 0.70)]
SUBJECTS = ["payments-api", "billing-api", "invoicing-api", "refunds-api", "ledger-api"]
for k, proj in enumerate(SUBJECTS, 1):
    sd = seed.derive_seed(proj, 1)
    pick = history.pick_pair(FAMS, OPENS, history.recent_history(50), sd)
    history.record_build({"project": proj, "build": 1, "palette_family": pick["a"], "opening_scene": pick["b"],
                          "seed": sd, "reasons": [pick["a_reason"], pick["b_reason"], pick["pair_rule"]]})
stored = history.recent_history(50)
pairs = [(r["palette_family"], r["opening_scene"]) for r in stored]
sandbox_file = Path(ENV["COORDN8_HOME"]) / "memory" / "choices.jsonl"
s.check("record_build writes the history into the sandboxed COORDN8_HOME (5 rows, newest first)",
        sandbox_file.exists() and len(stored) == 5 and stored[0]["project"] == "ledger-api", str(len(stored)))
s.check("5 consecutive fixture builds on similar subjects never repeat a palette family + opening scene pair",
        len(set(pairs)) == 5 and history.pair_repeats(stored) == [], str(pairs))
s.check("the picker still honours the -0.35 window: no palette family repeats within 3 consecutive builds",
        all(len({p[0] for p in pairs[i:i + 3]}) == 3 for i in range(len(pairs) - 2)), str(pairs))
# the formula alone (separate argmax per kind, no pair rule) on the same fixture
naive: list = []
for proj in SUBJECTS:
    hist = list(reversed(naive))
    fam = history.rank("palette_family", FAMS, hist, seed.derive_seed(proj, 1))[0]["id"]
    opn = history.rank("opening_scene", OPENS, hist, seed.derive_seed(proj, 1))[0]["id"]
    naive.append({"palette_family": fam, "opening_scene": opn})
naive_pairs = [(r["palette_family"], r["opening_scene"]) for r in naive]
s.check("negative control: the per-kind formula alone repeats a pair on this fixture (so the pair rule is needed)",
        len(set(naive_pairs)) < 5, str(naive_pairs))
s.check("the real ~/.coordn8/memory/choices.jsonl was not touched", _stat(REAL_CHOICES) == REAL_BEFORE, "")

# ====================================================================== divergence.json (H2)
IDEAS = {
    "look": ["Departure-board look: every stage flips like a railway board, colour roles map to on time, delayed and cancelled",
             "Two-colour field notebook: pencil grey plus one red ink stroke reserved for money that went missing",
             "Dark slide with a glowing blue accent and a title card on every scene"],
    "narrative-spine": ["Start from one missing amount and walk backwards through every component that could have lost it",
                        "Replay the same transfer three times with the power cut at three different moments",
                        "Introduce each service in order from the edge to the database, then summarise"],
    "hero-figure": ["Canal-lock pipeline: a stalled lower chamber visibly holds boats in the upper chambers",
                    "Month-long sawtooth strip where each tooth is one compaction run, single run as an inset",
                    "Boxes for every service joined left to right with arrows"],
    "pivotal-scene": ["Open on a single job leaving and circling a loop marked later before it returns",
                      "Open with the ledger total wrong by one cent and ask the viewer to find where",
                      "Open on the product logo and a title that names the system"],
}
PROBLEM = {"look": "design", "narrative-spine": "narrative-spine", "hero-figure": "visual-metaphor",
           "pivotal-scene": "opening"}
WHAT = {"look": "the look", "narrative-spine": "narrative spine", "hero-figure": "hero figure: routing",
        "pivotal-scene": "opening scene"}
BUILD_SEED = seed.derive_seed("payments-api", 3)


def make_decision(kind: str) -> dict:
    sd = seed.decision_seed(BUILD_SEED, WHAT[kind])
    dr = seed.Draw(sd)
    drawn = dr.draw_set(PROBLEM[kind], 3)
    opts = [{"id": f"o{i + 1}", "idea": IDEAS[kind][i], "technique": drawn[i]["technique"]} for i in range(3)]
    dec = {"what": WHAT[kind], "kind": kind, "problem_type": PROBLEM[kind],
           "techniques": [o["technique"] for o in opts], "options": opts,
           "critic": [{"id": "o1", "verdict": "keep", "why": "maps every stage state to a colour role the viewer reads"},
                      {"id": "o2", "verdict": "keep", "why": "surprising and still tied to the one thing that breaks"},
                      {"id": "o3", "verdict": "reject", "test": "known-default",
                       "why": "remove the wording and the standard template is all that remains"}],
           "chosen": "o1", "why": "the strongest survivor: it teaches the state model without extra words"}
    dec.update(dr.record())
    if kind == "hero-figure":
        dec["subject"] = "fig-routing"
    return dec


GOOD = {"schema": 1, "generator_agent": "coordn8-idea-generator", "critic_agent": "coordn8-idea-critic",
        "pivotal": [{"kind": "hero-figure", "subject": "fig-routing"}, {"kind": "pivotal-scene"}],
        "decisions": [make_decision(k) for k in ("look", "narrative-spine", "hero-figure", "pivotal-scene")]}


def codes(doc, required=()):
    return [f["code"] for f in divergence.check(doc, required)["findings"]]


res = divergence.check(GOOD)
s.check("a fixture where every pivotal decision (look, spine, hero figure, pivotal scene) has >= 3 options "
        "with different techniques, a replayable seed and critic reasons validates (PASS)",
        res["status"] == "PASS" and res["decisions"] == 4, json.dumps(res["findings"])[:300])
bdir = TMP / "repo" / "Coordn8" / "_build"
bdir.mkdir(parents=True)
(bdir / "divergence.json").write_text(json.dumps(GOOD), encoding="utf-8")
rc = run("scripts/design/divergence.py", ["check", str(bdir)], env=ENV, timeout=60)
s.check("divergence.py check <build_dir> exits 0 (PASS) on the good fixture", rc.returncode == 0, rc.stderr[-300:])

g = copy.deepcopy(GOOD)
g["critic_agent"] = "coordn8-idea-generator"
s.check("generator_agent == critic_agent is rejected (the critic must be a separate agent)",
        "same-agent" in codes(g) and divergence.check(g)["status"] == "REVIEW", str(codes(g)))
g = copy.deepcopy(GOOD)
g["decisions"][0]["critic_agent"] = g["generator_agent"]
s.check("a per-decision critic_agent equal to the generator is also rejected", "same-agent" in codes(g), "")
g = copy.deepcopy(GOOD)
del g["critic_agent"]
s.check("a missing critic_agent is rejected", "agents-missing" in codes(g), "")
(bdir / "divergence.json").write_text(json.dumps(dict(GOOD, critic_agent=GOOD["generator_agent"])), encoding="utf-8")
rc = run("scripts/design/divergence.py", ["check", str(bdir)], env=ENV, timeout=60)
s.check("divergence.py check exits 1 (REVIEW) when generator and critic are the same agent", rc.returncode == 1, "")
(bdir / "divergence.json").write_text(json.dumps(GOOD), encoding="utf-8")

g = copy.deepcopy(GOOD)
g["decisions"][1]["options"][1]["idea"] = ("Start from one missing amount and walk backwards through every "
                                           "component that might have lost it")
s.check("two near-identical options are flagged as not genuinely different (token Jaccard >= 0.6)",
        "near-duplicate" in codes(g), str(codes(g)))
jac = max(divergence.jaccard(a, b) for k in IDEAS for i, a in enumerate(IDEAS[k]) for b in IDEAS[k][i + 1:])
s.check("genuinely different options are not flagged (max pairwise Jaccard in the fixture < 0.6)",
        jac < 0.6 and "near-duplicate" not in codes(GOOD), f"{jac:.2f}")
g = copy.deepcopy(GOOD)
g["decisions"][0]["options"] = g["decisions"][0]["options"][:2]
s.check("a decision with fewer than 3 options is flagged", "too-few-options" in codes(g), "")
g = copy.deepcopy(GOOD)
g["decisions"][0]["options"][2]["technique"] = g["decisions"][0]["options"][0]["technique"]
g["decisions"][0]["techniques"] = [o["technique"] for o in g["decisions"][0]["options"]]
s.check("two options made with the same technique are flagged", "technique-repeated" in codes(g), "")

g = copy.deepcopy(GOOD)
dec = g["decisions"][3]
dec["options"][2] = {"id": "o3", "idea": "Open on the narrator's own wrong guess about where the cent went, then correct it",
                     "technique": "invented:echo-of-the-error",
                     "technique_description": "start from a plausible mistake the audience would make, then let the "
                                              "system disprove it on screen"}
dec["techniques"] = [o["technique"] for o in dec["options"]]
dec["critic"][2] = {"id": "o3", "verdict": "keep", "why": "a mistake the viewer shares makes the correction stick"}
s.check("an invented technique (with a description) passes the gate", divergence.check(g)["status"] == "PASS",
        str(codes(g)))
g2 = copy.deepcopy(g)
del g2["decisions"][3]["options"][2]["technique_description"]
s.check("an invented technique without a description is flagged", "invented-technique-undescribed" in codes(g2), "")

# no gate references catalog membership for options
g = copy.deepcopy(GOOD)
for i, name in enumerate(["mirror-the-audience", "weather-report-framing", "museum-label"]):
    g["decisions"][2]["options"][i]["technique"] = name
g["decisions"][2]["techniques"] = [o["technique"] for o in g["decisions"][2]["options"]]
s.check("options whose techniques are not in the catalog at all pass (only the difference rule applies)",
        divergence.check(g)["status"] == "PASS" and not set(["mirror-the-audience"]) & set(ids), str(codes(g)))
(bdir / "divergence.json").write_text(json.dumps(g), encoding="utf-8")
rc = run("scripts/design/divergence.py", ["check", str(bdir)],
         env={**ENV, "COORDN8_IDEA_CATALOG": str(TMP / "no-such-catalog.json")}, timeout=60)
s.check("the validator passes with the technique catalog absent (seed replay skipped with a note, nothing failed)",
        rc.returncode == 0 and "replay skipped" in rc.stdout, (rc.stdout + rc.stderr)[-300:])
(bdir / "divergence.json").write_text(json.dumps(GOOD), encoding="utf-8")
optsrc = inspect.getsource(divergence.check_decision) + inspect.getsource(divergence.check)
s.check("no option rule reads the catalog: check()/check_decision() never call load_catalog or read good_for/ids",
        "load_catalog" not in optsrc and "good_for" not in optsrc and "idea_techniques" not in optsrc
        and "good_for" not in Path(divergence.__file__).read_text(encoding="utf-8"), "")

# never lower the bar
g = copy.deepcopy(GOOD)
for c in g["decisions"][0]["critic"]:
    c.update(verdict="reject", test="user-could-think-of-it", why="the first thing anyone in the user's seat would say")
r_all = divergence.check(g)
s.check("an all-rejected decision fails and demands another generation round (bar not lowered)",
        r_all["status"] == "REVIEW" and "no-survivor" in [f["code"] for f in r_all["findings"]]
        and r_all["needs_round"] and "another generation round" in r_all["summary"], r_all["summary"])
s.check("an all-rejected decision cannot be rescued by choosing a rejected option", "chosen-rejected" in codes(g), "")
g = copy.deepcopy(GOOD)
first = make_decision("look")
for c in first["critic"]:
    c.update(verdict="reject", test="known-default", why="the standard template with new paint on it")
second = make_decision("look")
second["options"][0]["idea"] = first["options"][0]["idea"]  # the rejected idea comes back as kept o1
second["rounds"] = [{"round": 1, "options": first["options"], "critic": first["critic"]}]
g["decisions"][0] = second
s.check("re-admitting an idea the critic rejected in an earlier round is flagged as lowering the bar",
        "bar-lowered" in codes(g), str(codes(g)))
g = copy.deepcopy(GOOD)
fresh_round = make_decision("look")
fresh_round["rounds"] = [{"round": 1, "options": [{"id": "r1", "idea": "Corporate blue slides with a logo top left",
                                                    "technique": "subtraction"},
                                                   {"id": "r2", "idea": "Neon gradient over a black background",
                                                    "technique": "inversion"},
                                                   {"id": "r3", "idea": "Stock photography of servers behind text",
                                                    "technique": "time-shift"}],
                          "critic": [{"id": x, "verdict": "reject", "test": "known-default",
                                      "why": "a stock default dressed up"} for x in ("r1", "r2", "r3")]}]
g["decisions"][0] = fresh_round
s.check("a genuine new round after an all-rejected round passes (new ideas, nothing re-admitted)",
        divergence.check(g)["status"] == "PASS", str(codes(g)))

g = copy.deepcopy(GOOD)
g["decisions"][0]["critic"][2].pop("test")
s.check("a rejection that names no test is flagged", "reject-without-test" in codes(g), "")
g = copy.deepcopy(GOOD)
g["decisions"][0]["critic"] = g["decisions"][0]["critic"][:2]
s.check("an option the critic never judged is flagged", "option-unjudged" in codes(g), "")
g = copy.deepcopy(GOOD)
g["decisions"][0]["chosen"] = "o3"
s.check("choosing the option the critic rejected is flagged", "chosen-rejected" in codes(g), "")
g = copy.deepcopy(GOOD)
g["decisions"][0]["why"] = ""
s.check("a choice with no reason is flagged", "chosen-why-missing" in codes(g), "")
g = copy.deepcopy(GOOD)
g["pivotal"].append({"kind": "hero-figure", "subject": "fig-payout"})
s.check("a pivotal hero figure listed in 'pivotal' with no decision is flagged", "missing-pivotal" in codes(g), "")
g = copy.deepcopy(GOOD)
g["decisions"] = [d for d in g["decisions"] if d["kind"] != "narrative-spine"]
s.check("a build with no narrative-spine decision is flagged (look and spine are always pivotal)",
        "missing-pivotal" in codes(g), "")
s.check("--require adds a pivotal decision to the check",
        "missing-pivotal" in codes(GOOD, ["pivotal-scene:payoff"]), "")
g = copy.deepcopy(GOOD)
del g["decisions"][0]["seed"]
g["decisions"][1]["draws"] = []
s.check("a decision without a recorded seed or without draws is flagged",
        {"no-seed", "no-draws"} <= set(codes(g)), str(codes(g)))
g = copy.deepcopy(GOOD)
g["decisions"][0]["draws"][0]["value"] = "synthesis" if g["decisions"][0]["draws"][0]["value"] != "synthesis" else "inversion"
s.check("recorded draws that do not replay from the seed are flagged (fabricated randomness)",
        "draws-do-not-replay" in codes(g), str(codes(g)))
g = copy.deepcopy(GOOD)
g["decisions"][0].pop("kind")
g["decisions"][0]["what"] = "palette and kit"
s.check("a decision without 'kind' is classified from 'what' (palette -> look)", divergence.check(g)["status"] == "PASS",
        str(codes(g)))

# CLI: missing file, add (valid / invalid)
empty = TMP / "empty_build"
empty.mkdir()
rc = run("scripts/design/divergence.py", ["check", str(empty)], env=ENV, timeout=60)
s.check("check on a build with no divergence.json is SKIPPED (exit 2), never a pass", rc.returncode == 2, "")
newb = TMP / "new_build"
dfile = TMP / "decision.json"
dd_look = make_decision("look")
dd_look.pop("techniques")
dfile.write_text(json.dumps(dict(dd_look, generator_agent="coordn8-idea-generator",
                                 critic_agent="coordn8-idea-critic")), encoding="utf-8")
ra = run("scripts/design/divergence.py", ["add", str(newb), str(dfile)], env=ENV, timeout=60)
written = json.loads((newb / "divergence.json").read_text(encoding="utf-8")) if (newb / "divergence.json").exists() else {}
s.check("divergence.py add writes a valid decision into <build>/divergence.json (schema, agents, techniques filled)",
        ra.returncode == 0 and written.get("schema") == 1 and written.get("critic_agent") == "coordn8-idea-critic"
        and written["decisions"][0]["techniques"] == [o["technique"] for o in dd_look["options"]], ra.stderr[-300:])
for k in ("narrative-spine", "hero-figure", "pivotal-scene"):
    dfile.write_text(json.dumps(make_decision(k)), encoding="utf-8")
    run("scripts/design/divergence.py", ["add", str(newb), str(dfile)], env=ENV, timeout=60)
dfile.write_text(json.dumps(make_decision("look")), encoding="utf-8")
ra2 = run("scripts/design/divergence.py", ["add", str(newb), str(dfile)], env=ENV, timeout=60)
after = json.loads((newb / "divergence.json").read_text(encoding="utf-8"))
s.check("adding a decision of the same kind replaces it rather than duplicating it",
        ra2.returncode == 0 and sum(1 for d in after["decisions"] if d["kind"] == "look") == 1
        and len(after["decisions"]) == 4, "")
before_bytes = (newb / "divergence.json").read_bytes()
badd = make_decision("look")
for c in badd["critic"]:
    c.update(verdict="reject", test="known-default", why="every option is the stock template again")
dfile.write_text(json.dumps(badd), encoding="utf-8")
rb = run("scripts/design/divergence.py", ["add", str(newb), str(dfile)], env=ENV, timeout=60)
s.check("add refuses an all-rejected decision (exit 1) and leaves divergence.json byte-identical",
        rb.returncode == 1 and (newb / "divergence.json").read_bytes() == before_bytes, rb.stdout[-200:])
rc = run("scripts/design/divergence.py", ["check", str(newb)], env=ENV, timeout=60)
s.check("a divergence.json assembled with add (4 pivotal decisions) passes check", rc.returncode == 0,
        rc.stdout[-300:])

_TMP.cleanup()
s.check("the suite leaves nothing on disk (temp dir removed)", not TMP.exists(), str(TMP))
sys.exit(s.finish())
