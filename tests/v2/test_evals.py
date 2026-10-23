#!/usr/bin/env python3
"""Skill-vs-baseline evals (G3): eval files, counted assertions, repeats, spread, stability flag,
baseline isolation and the canary check.

Hermetic: a STUB `claude` (a small Python script in a temp dir) answers with canned outputs and logs
every argv / env it receives. The real CLI is never called here.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # leave nothing on disk
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import ROOT, PY, Suite, sandbox_env, tmpdir  # noqa: E402

EVALS_DIR = ROOT / "bench" / "evals"
RUNNER = EVALS_DIR / "run_evals.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


R = load_module("run_evals_under_test", RUNNER)
s = Suite("evals")

# ------------------------------------------------------------------ the eval files
files = [p for p in sorted(EVALS_DIR.glob("*.json")) if p.name not in R.REPORT_NAMES]
parsed, parse_errors = [], []
for p in files:
    try:
        parsed.append(json.loads(p.read_text(encoding="utf-8")))
    except ValueError as e:
        parse_errors.append(f"{p.name}: {e}")
s.check("every eval file parses as JSON", files and not parse_errors, "; ".join(parse_errors))
s.check("at least 8 eval files", len(parsed) >= 8, str(len(parsed)))
problems = {e.get("id"): R.validate_eval(e) for e in parsed}
problems = {k: v for k, v in problems.items() if v}
s.check("every eval validates (prompt, id, plain-words describe, valid regexes)", not problems, json.dumps(problems)[:300])
s.check("every eval has at least 2 assertions and every assertion is a counted kind",
        all(len(e["assertions"]) >= 2 and all(a["kind"] in R.KINDS for a in e["assertions"]) for e in parsed),
        "")
s.check("no assertion kind asks a model to grade (only code-counted kinds exist)",
        not any(re.search(r"judge|grade_by_model|llm|model_graded", k) for k in R.KINDS), str(list(R.KINDS)))
areas = {e.get("requirement") for e in parsed}
s.check("evals cover audience framing, shapes, fenced analogy, act, experts, claims, plain language, palette",
        {"I1", "I2", "I3", "I4", "I5", "C3", "D1", "A1"} <= areas, str(sorted(a for a in areas if a)))
s.check("no eval prompt contains a canary phrase (else the baseline check means nothing)",
        not any(re.search(c, e["prompt"], re.I) for e in parsed for c in R.canaries_for(e)), "")
ids = [e["id"] for e in parsed]
s.check("eval ids are unique and match their file names",
        len(set(ids)) == len(ids) and all((EVALS_DIR / f"{i}.json").exists() for i in ids), str(ids))

# a seeded bad eval file is refused
bad_one = {"id": "x", "prompt": "Explain coordn8.", "assertions": [{"id": "a", "kind": "vibes", "describe": "d"}]}
bp = R.validate_eval(bad_one)
s.check("validator refuses a model-judged kind, a single assertion and a canary in the prompt",
        any("not a counted kind" in p for p in bp) and any("at least 2" in p for p in bp)
        and any("canary" in p for p in bp), "; ".join(bp))
with tmpdir() as t:
    t = Path(t)
    (t / "broken.json").write_text(json.dumps(bad_one))
    (t / "report.json").write_text("{}")
    r = __import__("subprocess").run([PY, str(RUNNER), "--validate-only", "--evals-dir", str(t)],
                                     capture_output=True, text=True)
    s.check("--validate-only exits ERROR on an invalid eval file and ignores report.json",
            r.returncode == 3 and '"x"' in r.stdout and "report.json" not in r.stdout, r.stdout[-300:])
r = __import__("subprocess").run([PY, str(RUNNER), "--validate-only"], capture_output=True, text=True)
s.check("--validate-only passes on the shipped evals", r.returncode == 0 and '"PASS"' in r.stdout, r.stdout[-300:])

# ------------------------------------------------------------------ counted assertions on known text
ev_by = {e["id"]: e for e in parsed}


def g(eid, text, structured=None, wd=None):
    return {x["id"]: x["passed"] for x in R.grade(ev_by[eid], text, structured, wd)}


with tmpdir() as t:
    fx = R.write_builtin_fixture(Path(t) / "repo")
    s.check("builtin fixture is a small CLI with an entry file, config and counter",
            (fx / "tally" / "cli.py").is_file() and (fx / "tally" / "config.py").is_file(), "")
    s.check("path:line resolves for a real file and line, fails for a missing file or a line past the end",
            R.resolve_ref(fx, "tally/cli.py", 11) and R.resolve_ref(fx, "config.py", 11)
            and not R.resolve_ref(fx, "tally/cli.py", 999) and not R.resolve_ref(fx, "nope.py", 1), "")

    good_err = ("The line that matters is `KeyError: 'limit'`. It means the code asked the settings for a "
                "`limit` value that is not set. `tally/cli.py:28` reads `config[\"limit\"]` when you do not pass "
                "`--limit`, and `tally/config.py:11` only adds the key when `tally.ini` has it.\n\n"
                "Add `limit = 10` under `[tally]` in `tally.ini`, or pass `--limit 10`.")
    res = g("shape-error-log", good_err, wd=fx)
    s.check("error shape: a good answer passes every counted assertion", all(res.values()), str(res))
    wrong_order = ("Add `limit = 10` to the settings file. The cause is a missing key. Error was KeyError: 'limit'. "
                   "See tally/cli.py:28.")
    res2 = g("shape-error-log", wrong_order, wd=fx)
    s.check("error shape: a fix-before-the-quote answer fails the ordering assertion only",
            not res2["line-then-cause-then-fix"] and res2["quotes-the-line"], str(res2))
    res3 = g("shape-error-log", good_err.replace("tally/cli.py:28", "tally/cli.py:280")
             .replace("tally/config.py:11", "tally/settings.py:11"), wd=fx)
    s.check("a path:line pointing past the end or at a missing file does not count as resolved",
            not res3["points-at-the-code"], str(res3))

    claims = "\n".join(f"{i}. Claim {i}. See {ref}." for i, ref in enumerate(
        ["tally/cli.py:11", "tally/cli.py:22", "tally/config.py:11", "tally/counter.py:5", "tally/counter.py:18"], 1))
    res = g("claims-path-line", claims, wd=fx)
    s.check("claims: five resolving path:line refs in five list items pass", all(res.values()), str(res))
    fake = claims.replace("tally/counter.py", "tally/parser.py")
    res = g("claims-path-line", fake, wd=fx)
    s.check("claims: invented files are counted as unresolved (seeded defect caught)",
            res["five-references"] and not res["references-resolve"], str(res))

    expert = ("`build_parser` (tally/cli.py:9) creates one ArgumentParser and calls add_subparsers with "
              "dest='command' and required=True (tally/cli.py:11). Each subparser takes a positional path; "
              "`top` adds an int option --limit with default None (tally/cli.py:16). parse_args runs at "
              "tally/cli.py:21 and dispatch is a plain if on args.command.")
    res = g("stand-down-for-experts", expert, wd=fx)
    s.check("experts: an answer in the field's own terms passes", all(res.values()), str(res))
    res = g("stand-down-for-experts", "In simple terms, imagine a recipe. " + expert, wd=fx)
    s.check("experts: talking down is caught", not res["no-talking-down"] and res["keeps-field-terms"], str(res))

pal = {"palette": [{"hex": "#1F3A5F", "role": "input text", "reason": "dark blue reads as the raw source material"},
                   {"hex": "#E07A2F", "role": "counting step", "reason": "warm orange draws the eye to the work"},
                   {"hex": "#3C9D5D", "role": "output counts", "reason": "green marks the finished result clearly"},
                   {"hex": "#F4F1EA", "role": "background", "reason": "a warm off-white keeps contrast high everywhere"}]}
res = g("palette-reasons", "", pal)
s.check("palette: a structured answer with 4 colours, roles and reasons passes", all(res.values()), str(res))
res = g("palette-reasons", "```json\n" + json.dumps(pal) + "\n```")
s.check("palette: a JSON answer in the text is recovered when no structured output is returned",
        all(res.values()), str(res))
bad_pal = {"palette": pal["palette"][:3] + [{"hex": "blue", "role": "x", "reason": "nice"}, dict(pal["palette"][0])]}
res = g("palette-reasons", "", bad_pal)
s.check("palette: bad hex, a thin reason and a repeated colour are each caught",
        not res["valid-hex"] and not res["reason-per-colour"] and not res["distinct-colours"]
        and res["four-to-six-colours"], str(res))

plain_good = ("This tool counts the words in a text file. It can also list the words you use most. "
              "You type one short command and it prints the answer.")
plain_bad = plain_good + " It will leverage your files."
res_g, res_b = g("plain-language", plain_good), g("plain-language", plain_bad)
s.check("plain language: good text passes all, seeded inflated word fails exactly one assertion",
        all(res_g.values()) and sum(1 for v in res_b.values() if not v) == 1 and not res_b["no-inflated-words"],
        f"{res_g} {res_b}")
long_s = "This " + " ".join(["very"] * 40) + " long sentence goes on."
s.check("plain language: a 40-word sentence fails the longest-sentence bound",
        not g("plain-language", long_s)["no-very-long-sentence"], "")
ana = ("Think of it like a librarian who counts every word in a book. Unlike a librarian, it never "
       "understands what the words mean. You give it a file and it tells you the totals.")
res = g("fenced-analogy", ana)
s.check("fenced analogy: an analogy with its limit stated passes", all(res.values()), str(res))
res = g("fenced-analogy", "Think of it like a librarian who counts every word in a book. For example a novel. "
        "For instance a letter.")
s.check("fenced analogy: an unfenced analogy with two examples fails both bounds",
        not res["analogy-is-fenced"] and not res["one-example-only"], str(res))
act = "Add a parser in tally/cli.py:14.\n\nYou can now run `tally yours notes.txt` to try it."
res = g("end-where-they-can-act", act, wd=None)
s.check("act: ending on a sign-off instead of an action is caught",
        not g("end-where-they-can-act", act + "\n\nHope this helps, let me know!")["no-sign-off"]
        and res["ends-on-an-action"], str(res))

# ------------------------------------------------------------------ stub claude
STUB = r'''#!__PY__
import json, os, sys, time
argv = sys.argv[1:]
prompt = sys.stdin.read()
plan = json.load(open(os.environ["C8_STUB_PLAN"]))
logp = os.environ["C8_STUB_LOG"]
key = "without"
if "--plugin-dir" in argv:
    pd = argv[argv.index("--plugin-dir") + 1]
    key = "plugin:" + os.path.basename(pd.rstrip("/")) if ("plugin:" + os.path.basename(pd.rstrip("/"))) in plan else "with"
cnt = logp + "." + key.replace("/", "_").replace(":", "_") + ".n"
n = int(open(cnt).read()) if os.path.exists(cnt) else 0
open(cnt, "w").write(str(n + 1))
ans = plan[key][n % len(plan[key])]
cfg = os.environ.get("CLAUDE_CONFIG_DIR", "")
rec = {"argv": argv, "key": key, "cwd": os.getcwd(), "cwd_files": sorted(os.listdir(".")),
       "prompt": prompt, "cfg": cfg, "cfg_empty": bool(cfg) and os.path.isdir(cfg) and not os.listdir(cfg),
       "env": {k: os.environ.get(k) for k in ("HOME", "COORDN8_HOME", "CLAUDECODE", "CLAUDE_CODE_SESSION_ID",
               "CLAUDE_CODE_USE_BEDROCK", "PYTHONDONTWRITEBYTECODE", "C8_UNRELATED_SECRET")}}
open(logp, "a").write(json.dumps(rec) + "\n")
time.sleep(ans.get("sleep", 0))
if ans.get("raw") is not None:
    sys.stdout.write(ans["raw"]); sys.exit(ans.get("rc", 0))
init = {"type": "system", "subtype": "init", "plugins": ans.get("init_plugins", []), "skills": [],
        "slash_commands": []}
content = [{"type": "text", "text": "working"}]
if ans.get("skill_call"):
    content.append({"type": "tool_use", "name": "Skill", "input": {"skill": "coordn8:coordn8"}})
result = {"type": "result", "subtype": "success", "is_error": False, "result": ans.get("text", ""),
          "num_turns": 2, "total_cost_usd": 0.01}
if "structured" in ans:
    result["structured_output"] = ans["structured"]
out = [init, {"type": "assistant", "message": {"content": content}}, result]
sys.stdout.write(json.dumps(out if ans.get("shape", "list") == "list" else result))
sys.exit(ans.get("rc", 0))
'''


def run_stub(plan: dict, args: list, extra_env=None, fixtures=None):
    import subprocess
    t = Path(TMP)
    bindir = t / "bin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "claude"
    stub.write_text(STUB.replace("__PY__", PY))
    stub.chmod(0o755)
    planp, logp = t / "plan.json", t / f"log{len(list(t.glob('log*.jsonl')))}.jsonl"
    planp.write_text(json.dumps(plan))
    env = dict(os.environ)
    env.update(sandbox_env(t / "sbx"))
    env.update({"PATH": str(bindir) + os.pathsep + env.get("PATH", ""), "C8_STUB_PLAN": str(planp),
                "C8_STUB_LOG": str(logp), "CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "parent-session",
                "CLAUDE_CODE_USE_BEDROCK": "1", "C8_UNRELATED_SECRET": "x"})
    env.update(extra_env or {})
    rep = t / f"report{len(list(t.glob('report*.json')))}.json"
    fx = fixtures or (t / "nofixtures")
    cmd = [PY, str(RUNNER), "--report", str(rep), "--fixtures-dir", str(fx), *args]
    p = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=120)
    log = [json.loads(l) for l in logp.read_text().splitlines()] if logp.exists() else []
    data = json.loads(rep.read_text()) if rep.exists() else None
    return p, data, log


GOOD, BAD = {"text": plain_good}, {"text": plain_bad}
with tmpdir() as TMP:
    import tempfile
    before_tmp = set(os.listdir(tempfile.gettempdir()))
    p, rep, log = run_stub({"with": [GOOD, GOOD, GOOD], "without": [GOOD, BAD, GOOD]},
                           ["--eval", "plain-language", "--mode", "both"])
    s.check("stub run: both modes x 3 repeats = 6 CLI calls and a report", rep is not None and len(log) == 6,
            p.stdout[-400:] + p.stderr[-400:])
    if rep:
        row = rep["evals"][0]
        w, wo = row["modes"]["with"], row["modes"]["without"]
        s.check("pass rate is the mean of per-repeat pass rates (with = 1.0, without = (1+0.75+1)/3)",
                w["pass_rate"] == 1.0 and abs(wo["pass_rate"] - round((1 + 0.75 + 1) / 3, 4)) < 1e-9,
                f"{w['pass_rate']} {wo['pass_rate']}")
        s.check("spread is max - min across repeats (with 0.0, without 0.25)",
                w["spread"] == 0.0 and wo["spread"] == 0.25, f"{w['spread']} {wo['spread']}")
        s.check("stability flag: identical repeats are stable; a 0.25 spread over the 0.2 threshold is unstable",
                w["stability"] == "stable" and wo["stability"] == "unstable", f"{w['stability']} {wo['stability']}")
        s.check("the assertion that flipped between repeats is named",
                wo["flipped_assertions"] == ["no-inflated-words"], str(wo["flipped_assertions"]))
        s.check("delta (with - without) is reported per eval and overall",
                abs(row["delta"] - round(1.0 - round((1 + 0.75 + 1) / 3, 4), 4)) < 1e-9
                and rep["overall"]["delta"] == row["delta"], f"{row.get('delta')} {rep['overall'].get('delta')}")
        s.check("overall: 11/12 baseline assertions, pass rate by repeat [1, 0.75, 1], marked unstable",
                rep["overall"]["without"]["assertions_passed"] == 11
                and rep["overall"]["without"]["pass_rate_by_repeat"] == [1.0, 0.75, 1.0]
                and rep["overall"]["without"]["stability"] == "unstable", json.dumps(rep["overall"]["without"]))
        s.check("an unstable eval makes the run exit REVIEW (1)", p.returncode == 1, str(p.returncode))
        md = rep.get("markdown", "")
        mdp = Path(TMP) / "report0.md"
        s.check("a markdown section with the per-eval table is written beside the report",
                mdp.exists() and "| plain-language |" in md and "100.0%" in md and "unstable" in md, md[:300])
        s.check("report records counted grading and the stability threshold",
                "no model grading" in rep["grading"] and rep["stability_threshold"] == 0.2, rep["grading"])
    base = [l for l in log if l["key"] == "without"]
    withs = [l for l in log if l["key"] == "with"]
    s.check("the baseline invocation never includes --plugin-dir (from the stub's argv log)",
            base and all("--plugin-dir" not in l["argv"] for l in base), json.dumps([l["argv"] for l in base])[:300])
    s.check("the skill invocation passes --plugin-dir <package> and claude -p with JSON output",
            withs and all(l["argv"][l["argv"].index("--plugin-dir") + 1] == str(ROOT) and "-p" in l["argv"]
                          and l["argv"][l["argv"].index("--output-format") + 1] == "json" for l in withs),
            json.dumps([l["argv"] for l in withs])[:300])
    s.check("every run uses an isolated, fresh, empty config dir (never the real ~/.claude) and a temp HOME",
            all(l["cfg_empty"] and l["cfg"] and not l["cfg"].startswith(str(Path.home() / ".claude"))
                and l["env"]["HOME"] != str(Path.home()) for l in log)
            and len({l["cfg"] for l in log}) == len(log), json.dumps([(l["cfg"], l["env"]["HOME"]) for l in log])[:300])
    s.check("the same prompt is sent to both modes, on stdin",
            len({l["prompt"] for l in log}) == 1 and log[0]["prompt"] == ev_by["plain-language"]["prompt"], "")
    s.check("child env drops the parent session vars and unrelated vars, keeps provider auth, blocks .pyc writes",
            all(l["env"]["CLAUDECODE"] is None and l["env"]["CLAUDE_CODE_SESSION_ID"] is None
                and l["env"]["C8_UNRELATED_SECRET"] is None and l["env"]["CLAUDE_CODE_USE_BEDROCK"] == "1"
                and l["env"]["PYTHONDONTWRITEBYTECODE"] == "1" for l in log), json.dumps(log[0]["env"]))
    s.check("bench/fixtures/01-cli absent -> each run gets its own temp copy of the builtin CLI fixture",
            all("tally" in l["cwd_files"] for l in log) and len({l["cwd"] for l in log}) == len(log)
            and all("evalrun-" in l["cwd"] for l in log)
            and (rep and all("fallback" in r["fixture"] for r in rep["evals"][0]["modes"]["with"]["runs"])), "")
    s.check("temp work and config dirs are removed after the runs",
            not any(n.startswith(("evalrun-", "evalcfg-")) for n in set(os.listdir(tempfile.gettempdir())) - before_tmp),
            "")
    s.check("report rows carry the isolation proof for every baseline run",
            rep and rep["baseline"]["clean"] and rep["baseline"]["never_passed_plugin_dir"]
            and rep["baseline"]["config_isolated"], json.dumps(rep and rep["baseline"]))

    # threshold is a parameter: the same data is stable at 0.3
    p, rep2, _ = run_stub({"with": [GOOD], "without": [GOOD, BAD, GOOD]},
                          ["--eval", "plain-language", "--stability-threshold", "0.3"])
    s.check("the stability threshold is configurable (0.25 spread is stable at 0.3)",
            rep2 and rep2["evals"][0]["modes"]["without"]["stability"] == "stable" and p.returncode == 0,
            p.stdout[-300:])

    # canary: a contaminated baseline is flagged
    p, rep3, _ = run_stub({"with": [dict(GOOD, skill_call=True)],
                           "without": [GOOD, {"text": plain_good + " Coordn8 would build a video."}, GOOD]},
                          ["--eval", "plain-language"])
    s.check("canary: a baseline answer containing a skill-only phrase is marked CONTAMINATED",
            rep3 and not rep3["baseline"]["clean"] and rep3["baseline"]["contaminated_runs"] == ["plain-language#1"],
            json.dumps(rep3 and rep3["baseline"]))
    s.check("canary: a contaminated baseline makes the run exit REVIEW (1)", p.returncode == 1, str(p.returncode))
    s.check("skill engagement in the skill runs is detected from the Skill tool call",
            rep3 and rep3["skill_engaged"] == {"runs": 3, "engaged": 3}, json.dumps(rep3 and rep3["skill_engaged"]))
    p, rep4, _ = run_stub({"with": [GOOD], "without": [dict(GOOD, init_plugins=[{"name": "coordn8", "path": "/x"}])]},
                          ["--eval", "plain-language"])
    s.check("canary: a baseline whose CLI init lists the plugin is contaminated even with a clean answer",
            rep4 and not rep4["baseline"]["clean"] and len(rep4["baseline"]["contaminated_runs"]) == 3, "")

    # repeats
    s.check("repeats default to 3", R.parse_args([]).repeats == 3, str(R.parse_args([]).repeats))
    p, rep5, log5 = run_stub({"with": [GOOD], "without": [GOOD]}, ["--eval", "plain-language", "--repeats", "2"])
    s.check("--repeats 2 is refused for a report (exit ERROR, no CLI call, no report)",
            p.returncode == 3 and rep5 is None and not log5 and "refused" in p.stdout, p.stdout[-300:])
    p, rep6, log6 = run_stub({"with": [GOOD], "without": [GOOD]},
                             ["--eval", "plain-language", "--repeats", "1", "--no-report"])
    s.check("--repeats 1 --no-report runs as a smoke test and writes no report",
            p.returncode == 0 and rep6 is None and len(log6) == 2, p.stdout[-300:])

    # timeouts / bad output -> incomplete, never a pass
    p, rep7, _ = run_stub({"with": [dict(GOOD, sleep=3), GOOD, GOOD], "without": [{"raw": "not json"}, GOOD, GOOD]},
                          ["--eval", "plain-language", "--timeout", "1"])
    if rep7:
        mw, mo = rep7["evals"][0]["modes"]["with"], rep7["evals"][0]["modes"]["without"]
        s.check("a timed-out repeat or unparsable output makes the eval 'incomplete', never stable",
                mw["stability"] == "incomplete" and mo["stability"] == "incomplete"
                and mw["completed"] == 2 and mo["completed"] == 2
                and mw["runs"][0]["status"] == "timeout" and mo["runs"][0]["status"] == "error", json.dumps(mw)[:300])
    else:
        s.check("a timed-out repeat or unparsable output makes the eval 'incomplete', never stable", False,
                p.stdout[-300:] + p.stderr[-300:])

    # single-object JSON output and structured output are parsed
    p, rep8, log8 = run_stub({"with": [{"text": "", "structured": pal, "shape": "object"}],
                              "without": [{"text": "Blue and green.", "shape": "object"}]},
                             ["--eval", "palette-reasons"])
    if rep8:
        s.check("structured output (--json-schema) is graded: with 100%, without 0% (no palette)",
                rep8["evals"][0]["modes"]["with"]["pass_rate"] == 1.0
                and rep8["evals"][0]["modes"]["without"]["pass_rate"] == 0.0, rep8["markdown"][:300])
    else:
        s.check("structured output (--json-schema) is graded: with 100%, without 0% (no palette)", False, p.stdout[-300:])
    s.check("--json-schema is passed identically to both modes",
            log8 and all("--json-schema" in l["argv"] for l in log8)
            and len({l["argv"][l["argv"].index("--json-schema") + 1] for l in log8}) == 1, "")

    # a real fixture directory is used when present
    fxroot = Path(TMP) / "fixtures"
    (fxroot / "01-cli").mkdir(parents=True)
    (fxroot / "01-cli" / "MARKER.txt").write_text("fixture")
    (fxroot / "01-cli" / ".claude").mkdir()
    (fxroot / "01-cli" / ".claude" / "settings.json").write_text("{}")
    p, rep9, log9 = run_stub({"with": [GOOD], "without": [GOOD]}, ["--eval", "plain-language"], fixtures=fxroot)
    s.check("bench/fixtures/01-cli is copied when present, minus any agent config dir",
            log9 and all("MARKER.txt" in l["cwd_files"] and ".claude" not in l["cwd_files"] for l in log9)
            and (fxroot / "01-cli" / ".claude").exists(), json.dumps(log9 and log9[0]["cwd_files"]))

    # A/B mode
    pa, pb = Path(TMP) / "skillA", Path(TMP) / "skillB"
    pa.mkdir(); pb.mkdir()
    p, rep10, log10 = run_stub({"plugin:skillA": [GOOD], "plugin:skillB": [BAD], "with": [GOOD], "without": [GOOD]},
                               ["--eval", "plain-language", "--mode", "ab", "--plugin-dir", str(pa),
                                "--plugin-dir-b", str(pb)])
    s.check("A/B mode compares two plugin dirs (a 100%, b 75%, delta +25 pts)",
            rep10 and rep10["evals"][0]["modes"]["a"]["pass_rate"] == 1.0
            and rep10["evals"][0]["modes"]["b"]["pass_rate"] == 0.75 and rep10["overall"]["delta"] == 0.25,
            p.stdout[-300:])

    # no CLI -> SKIPPED
    p, _, _ = run_stub({"with": [GOOD], "without": [GOOD]},
                       ["--eval", "plain-language", "--claude-bin", str(Path(TMP) / "no-such-claude")])
    s.check("no claude CLI -> exit SKIPPED (2) with the reason", p.returncode == 2 and "SKIPPED" in p.stdout,
            p.stdout[-200:])

# ------------------------------------------------------------------ the builder refuses a polluted baseline
try:
    cmd = R.build_command("without", ev_by["plain-language"], "claude", None, Path("/x"))
    s.check("build_command never emits --plugin-dir for the baseline, even when handed one",
            "--plugin-dir" not in cmd, str(cmd))
except Exception as e:  # noqa: BLE001
    s.check("build_command never emits --plugin-dir for the baseline, even when handed one", False, repr(e))

# ------------------------------------------------------------------ no reference names in shipped files
NL = load_module("name_leak_for_evals", ROOT / "tests" / "v2" / "test_name_leak.py")
with tmpdir() as t:
    t = Path(t)
    for p in [RUNNER, Path(__file__).resolve()] + files:
        shutil.copy(p, t / p.name)
    hits = NL.scan(t)
    s.check("no reference project names in the eval files, runner or this test", not hits, "; ".join(hits))

sys.exit(s.finish())
