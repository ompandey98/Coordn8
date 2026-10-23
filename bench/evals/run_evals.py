#!/usr/bin/env python3
"""Skill-vs-baseline evals with COUNTED assertions, repeated runs and a stability flag.

Every eval is one JSON file beside this script (`bench/evals/<id>.json`): a prompt, the fixture it runs
in, and two or more assertions. Every assertion is checked by code — a regex count, a word count,
sentence statistics, list structure, `path:line` references that must resolve in the fixture, or the
shape of a structured JSON answer. Nothing here asks a model to grade a model.

Modes
  with      `claude -p --plugin-dir <package>` in a fresh temp copy of the fixture, fresh config dir
  without   the SAME prompt, a fresh empty config dir, NO --plugin-dir (the baseline)
  both      with + without, and the delta between them
  ab        two plugin directories against each other (--plugin-dir vs --plugin-dir-b)

The baseline is proven not to load the skill three ways, per run, and the proof is in the report:
  1. its argv never contains --plugin-dir (the command builder refuses to emit one);
  2. CLAUDE_CONFIG_DIR and HOME point at fresh, empty temp dirs — never the real ones;
  3. canary phrases only the skill would produce must be absent from its answer, and the CLI's own
     init record (when printed) must not list the plugin or any skill invocation of it.
A baseline run that trips any of these is marked CONTAMINATED and the report says the baseline is
not clean.

Repeats default to 3; fewer than 3 is refused for a report (use --no-report for a smoke run).
Per eval and mode the report gives the mean pass rate, the spread (max - min pass rate across repeats),
the standard deviation, which assertions flipped between repeats, and a stability flag:
  stable      spread <= threshold (default 0.2) and every repeat completed
  unstable    spread  > threshold
  incomplete  a repeat timed out, errored or returned unparsable output

The only model this script ever calls is Claude, through the local `claude` CLI. Tests put a stub
`claude` on PATH; a real run is always an explicit, opt-in command:

    python3 bench/evals/run_evals.py --validate-only
    python3 bench/evals/run_evals.py --mode both --repeats 3 --eval claims-path-line --timeout 300 \
        --model <model id> --report /tmp/evals_report.json

Exit codes: 0 PASS · 1 REVIEW (contaminated baseline, unstable/incomplete eval) · 2 SKIPPED (no claude
CLI) · 3 ERROR (bad arguments, invalid eval file, repeats < 3 for a report).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG_ROOT = HERE.parents[1]
DEFAULT_FIXTURES = PKG_ROOT / "bench" / "fixtures"
DEFAULT_REPORT = HERE / "report.json"
REPORT_NAMES = {"report.json"}          # outputs that live beside the evals but are not evals
MIN_REPEATS = 3
DEFAULT_THRESHOLD = 0.2
DEFAULT_TIMEOUT = 300

EXIT_PASS, EXIT_REVIEW, EXIT_SKIPPED, EXIT_ERROR = 0, 1, 2, 3

# Phrases only the skill would produce. The prompts never contain them (a test proves it), so a hit in
# a baseline answer means the skill leaked in. An eval may add its own with "canary": [...].
DEFAULT_CANARIES = [
    r"\bcoordn8\b",
    r"memory palace",
    r"DELIVERABLE SCOPE",
    r"_research/",
    r"requirement-coverage ledger",
    r"\bTier [ABCD]\b",
    r"\bPHASE 0\b",
]

# Environment passed to the child CLI: auth/provider settings only. Session, IPC and telemetry
# variables of a parent session are dropped so the child is a clean, separate process.
ENV_KEEP_EXACT = {"PATH", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TERM", "USER", "LOGNAME", "SHELL",
                  "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
                  "CLAUDE_CODE_MAX_OUTPUT_TOKENS", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
                  "API_TIMEOUT_MS", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "SSL_CERT_FILE",
                  "NODE_EXTRA_CA_CERTS"}
ENV_KEEP_PREFIX = ("AWS_", "ANTHROPIC_", "C8_STUB_")
DISALLOWED_TOOLS = "WebFetch,WebSearch"


# --------------------------------------------------------------------------- eval files

def load_evals(evals_dir: Path = HERE) -> list[dict]:
    out = []
    for p in sorted(Path(evals_dir).glob("*.json")):
        if p.name in REPORT_NAMES:
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        d["_file"] = p.name
        out.append(d)
    return out


def validate_eval(ev: dict) -> list[str]:
    """Problems with one eval definition (empty list = valid)."""
    probs = []
    for k in ("id", "prompt", "assertions"):
        if k not in ev:
            probs.append(f"missing '{k}'")
    if probs:
        return probs
    if not isinstance(ev["assertions"], list) or len(ev["assertions"]) < 2:
        probs.append("needs at least 2 assertions")
    seen = set()
    for a in ev.get("assertions", []):
        kind = a.get("kind")
        if kind not in KINDS:
            probs.append(f"assertion {a.get('id')!r}: kind {kind!r} is not a counted kind")
        if not a.get("id"):
            probs.append("assertion without id")
        if a.get("id") in seen:
            probs.append(f"duplicate assertion id {a.get('id')!r}")
        seen.add(a.get("id"))
        if not a.get("describe"):
            probs.append(f"assertion {a.get('id')!r}: missing plain-words 'describe'")
        for key in ("pattern",):
            if key in a:
                try:
                    re.compile(a[key])
                except re.error as e:
                    probs.append(f"assertion {a.get('id')!r}: bad regex ({e})")
        for pat in a.get("patterns", []):
            try:
                re.compile(pat)
            except re.error as e:
                probs.append(f"assertion {a.get('id')!r}: bad regex ({e})")
        if kind in ("json_items", "json_each", "json_distinct") and not ev.get("json_schema"):
            probs.append(f"assertion {a.get('id')!r}: json assertions need a json_schema on the eval")
    for c in canaries_for(ev):
        if re.search(c, ev["prompt"], re.I):
            probs.append(f"prompt contains canary {c!r}; the baseline check would be meaningless")
    return probs


def canaries_for(ev: dict) -> list[str]:
    return DEFAULT_CANARIES + list(ev.get("canary", []))


# --------------------------------------------------------------------------- text helpers

FENCE = re.compile(r"```.*?(?:```|\Z)", re.S)
WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’-]*")
PATH_LINE = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)*[\w-][\w.-]*\.[A-Za-z0-9]{1,6}):(\d+)(?:\s*[-–]\s*(\d+))?")
LIST_ITEM = re.compile(r"^(?:\d+[.)]|[-*•])\s+\S")


def strip_fences(text: str) -> str:
    return FENCE.sub("\n", text)


def prose(text: str) -> str:
    """Text without code fences, inline code ticks, emphasis marks or heading hashes."""
    t = strip_fences(text)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"^\s{0,3}#{1,6}\s*", "", t, flags=re.M)
    t = re.sub(r"[*_]{1,3}", "", t)
    return t


def paragraphs(text: str) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", strip_fences(text)) if p.strip()]
    return paras


def first_paragraph(text: str) -> str:
    for p in paragraphs(text):
        if not re.fullmatch(r"#{1,6}\s.*", p):
            return p
    return ""


def last_paragraph(text: str) -> str:
    ps = paragraphs(text)
    return ps[-1] if ps else ""


def sentences(text: str) -> list[str]:
    t = prose(text)
    t = re.sub(r"^\s*(?:\d+[.)]|[-*•])\s+", "", t, flags=re.M)
    parts = []
    for line in t.splitlines():
        line = line.strip()
        if not line:
            continue
        # split on sentence-ending punctuation followed by space; keep e.g./i.e. intact
        line = re.sub(r"\b(e\.g|i\.e|etc|vs)\.", lambda m: m.group(1).replace(".", "․") + "․", line)
        for s in re.split(r"(?<=[.!?])\s+", line):
            if WORD.search(s):
                parts.append(s)
    return parts


def words(text: str) -> list[str]:
    return WORD.findall(prose(text))


def scoped(text: str, scope: str) -> str:
    if scope == "first_paragraph":
        return first_paragraph(text)
    if scope == "last_paragraph":
        return last_paragraph(text)
    if scope == "first_sentence":
        s = sentences(text)
        return s[0] if s else ""
    return text


def find_refs(text: str) -> list[tuple[str, int]]:
    seen, out = set(), []
    for m in PATH_LINE.finditer(text):
        key = (m.group(1), int(m.group(2)))
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def resolve_ref(root: Path | None, path: str, line: int) -> bool:
    """True when `path` names one file in the fixture copy and `line` exists in it."""
    if root is None or line < 1:
        return False
    root = Path(root)
    cand = root / path
    files = []
    if cand.is_file():
        files = [cand]
    else:
        tail = path.strip("./")
        files = [p for p in root.rglob("*") if p.is_file() and
                 (p.relative_to(root).as_posix() == tail or p.relative_to(root).as_posix().endswith("/" + tail))]
        if len(files) != 1:
            return False
    try:
        n = len(files[0].read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        return False
    return line <= n


def json_get(obj, dotted: str):
    cur = obj
    for part in [p for p in dotted.split(".") if p]:
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _bounds(n, a: dict) -> bool:
    if "min" in a and n < a["min"]:
        return False
    if "max" in a and n > a["max"]:
        return False
    return True


# --------------------------------------------------------------------------- counted assertion kinds
# Every kind returns (passed, observed). `observed` is the number (or numbers) the verdict came from.

def k_regex_count(a, text, ctx):
    flags = 0 if a.get("case_sensitive") else re.I
    n = len(re.findall(a["pattern"], scoped(text, a.get("scope", "all")), flags))
    return _bounds(n, a), {"count": n}


def k_word_count(a, text, ctx):
    n = len(words(scoped(text, a.get("scope", "all"))))
    return _bounds(n, a), {"words": n}


def k_code_fences(a, text, ctx):
    n = len(FENCE.findall(text))
    return _bounds(n, a), {"fences": n}


def k_list_items(a, text, ctx):
    n = sum(1 for line in strip_fences(text).splitlines() if LIST_ITEM.match(line))
    return _bounds(n, a), {"top_level_items": n}


def k_sentence_stats(a, text, ctx):
    ss = sentences(scoped(text, a.get("scope", "all")))
    lens = [len(WORD.findall(s)) for s in ss] or [0]
    avg = sum(lens) / len(lens)
    ok = True
    if "max_avg" in a and avg > a["max_avg"]:
        ok = False
    if "max_longest" in a and max(lens) > a["max_longest"]:
        ok = False
    if not ss:
        ok = False
    return ok, {"sentences": len(ss), "avg_words": round(avg, 2), "longest": max(lens)}


def _term_hits(terms, text):
    hits = {}
    for t in terms:
        pat = r"(?<![A-Za-z0-9])" + re.escape(t) + r"(?![A-Za-z0-9])"
        n = len(re.findall(pat, text, re.I))
        if n:
            hits[t] = n
    return hits


def k_distinct_terms(a, text, ctx):
    hits = _term_hits(a["terms"], prose(scoped(text, a.get("scope", "all"))))
    n = len(hits)
    return _bounds(n, a), {"distinct": n, "found": sorted(hits)}


def k_forbidden_terms(a, text, ctx):
    hits = _term_hits(a["terms"], prose(scoped(text, a.get("scope", "all"))))
    n = sum(hits.values())
    return n <= a.get("max", 0), {"occurrences": n, "found": sorted(hits)}


def k_ordered_patterns(a, text, ctx):
    flags = 0 if a.get("case_sensitive") else re.I
    pos, found = 0, []
    for pat in a["patterns"]:
        m = re.compile(pat, flags).search(text, pos)
        if not m:
            return False, {"matched_in_order": len(found), "of": len(a["patterns"])}
        found.append(m.start())
        pos = m.end()
    return True, {"matched_in_order": len(found), "of": len(a["patterns"])}


def k_path_line_refs(a, text, ctx):
    refs = find_refs(text)
    root = ctx.get("workdir")
    resolved = [r for r in refs if resolve_ref(root, r[0], r[1])] if a.get("resolve") else []
    ok = len(refs) >= a.get("min", 0)
    if a.get("resolve"):
        ok = ok and len(resolved) >= a.get("min_resolved", 1)
    return ok, {"refs": len(refs), "resolved": len(resolved) if a.get("resolve") else None,
                "sample": ["%s:%d" % r for r in refs[:8]]}


def _json_list(a, ctx):
    data = ctx.get("structured")
    val = json_get(data, a.get("path", "")) if data is not None else None
    return val if isinstance(val, list) else None


def k_json_items(a, text, ctx):
    lst = _json_list(a, ctx)
    if lst is None:
        return False, {"items": None, "why": "no structured answer at that path"}
    return _bounds(len(lst), a), {"items": len(lst)}


def k_json_each(a, text, ctx):
    lst = _json_list(a, ctx)
    if not lst:
        return False, {"items": 0, "why": "no structured answer at that path"}
    bad = 0
    for it in lst:
        v = it.get(a["field"]) if isinstance(it, dict) else None
        if not isinstance(v, str):
            bad += 1
            continue
        if "pattern" in a and not re.search(a["pattern"], v):
            bad += 1
        elif "min_words" in a and len(WORD.findall(v)) < a["min_words"]:
            bad += 1
    return bad == 0, {"items": len(lst), "failing_items": bad}


def k_json_distinct(a, text, ctx):
    lst = _json_list(a, ctx)
    if not lst:
        return False, {"items": 0}
    vals = [str(it.get(a["field"], "")).strip().lower() for it in lst if isinstance(it, dict)]
    return len(set(vals)) == len(vals), {"items": len(vals), "distinct": len(set(vals))}


KINDS = {
    "regex_count": k_regex_count,
    "word_count": k_word_count,
    "code_fences": k_code_fences,
    "list_items": k_list_items,
    "sentence_stats": k_sentence_stats,
    "distinct_terms": k_distinct_terms,
    "forbidden_terms": k_forbidden_terms,
    "ordered_patterns": k_ordered_patterns,
    "path_line_refs": k_path_line_refs,
    "json_items": k_json_items,
    "json_each": k_json_each,
    "json_distinct": k_json_distinct,
}


def grade(ev: dict, text: str, structured=None, workdir: Path | None = None) -> list[dict]:
    if structured is None and ev.get("json_schema"):
        structured = json_from_text(text)
    ctx = {"structured": structured, "workdir": workdir}
    out = []
    for a in ev["assertions"]:
        try:
            ok, obs = KINDS[a["kind"]](a, text or "", ctx)
        except Exception as e:  # a broken assertion is a failed assertion, never a crash
            ok, obs = False, {"error": repr(e)}
        out.append({"id": a["id"], "kind": a["kind"], "passed": bool(ok), "observed": obs})
    return out


def json_from_text(text: str):
    """Structured answer recovered from text when the CLI did not return structured_output."""
    if not text:
        return None
    blocks = re.findall(r"```(?:json)?\s*(.*?)```", text, re.S) + [text]
    for b in blocks:
        b = b.strip()
        for cand in (b, b[b.find("{"):b.rfind("}") + 1] if "{" in b else ""):
            if not cand:
                continue
            try:
                return json.loads(cand)
            except ValueError:
                continue
    return None


# --------------------------------------------------------------------------- fixtures

BUILTIN_CLI = {
    "README.md": "# tally\n\nCount words in text files and show the most common ones.\n\n"
                 "    tally count notes.txt\n    tally top notes.txt --limit 5\n\n"
                 "Settings live in `tally.ini` (section `[tally]`, keys `limit` and `ignore_case`).\n",
    "tally.ini": "[tally]\nignore_case = yes\n",
    "tally/__init__.py": '"""tally: count words in text files."""\n__version__ = "0.3.0"\n',
    "tally/cli.py": (
        '"""Command-line entry point: parse arguments and dispatch to a subcommand."""\n'
        "import argparse\n"
        "import sys\n"
        "\n"
        "from tally.config import load_config\n"
        "from tally.counter import count_words, top_words\n"
        "\n"
        "\n"
        "def build_parser():\n"
        '    parser = argparse.ArgumentParser(prog="tally", description="Count words in text files.")\n'
        '    sub = parser.add_subparsers(dest="command", required=True)\n'
        '    count = sub.add_parser("count", help="print the total number of words")\n'
        '    count.add_argument("path")\n'
        '    top = sub.add_parser("top", help="print the most common words")\n'
        '    top.add_argument("path")\n'
        '    top.add_argument("--limit", type=int, default=None, help="how many words to show")\n'
        "    return parser\n"
        "\n"
        "\n"
        "def main(argv=None):\n"
        "    args = build_parser().parse_args(argv)\n"
        '    config = load_config("tally.ini")\n'
        '    with open(args.path, encoding="utf-8") as handle:\n'
        "        text = handle.read()\n"
        '    if args.command == "count":\n'
        '        print(count_words(text, ignore_case=config["ignore_case"]))\n'
        "        return 0\n"
        '    limit = args.limit if args.limit is not None else config["limit"]\n'
        '    for word, n in top_words(text, limit, ignore_case=config["ignore_case"]):\n'
        '        print(f"{n:6d}  {word}")\n'
        "    return 0\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    sys.exit(main())\n"),
    "tally/config.py": (
        '"""Read settings from an ini file. Missing keys are NOT defaulted."""\n'
        "import configparser\n"
        "\n"
        "\n"
        "def load_config(path):\n"
        "    parser = configparser.ConfigParser()\n"
        "    parser.read(path)\n"
        '    section = parser["tally"] if parser.has_section("tally") else {}\n'
        "    config = {}\n"
        '    config["ignore_case"] = str(section.get("ignore_case", "no")).lower() in ("yes", "true", "1")\n'
        '    if "limit" in section:\n'
        '        config["limit"] = int(section["limit"])\n'
        "    return config\n"),
    "tally/counter.py": (
        '"""Split text into words and count them."""\n'
        "import re\n"
        "from collections import Counter\n"
        "\n"
        'WORD = re.compile(r"[A-Za-z\']+")\n'
        "\n"
        "\n"
        "def split_words(text, ignore_case=False):\n"
        "    found = WORD.findall(text)\n"
        "    return [w.lower() for w in found] if ignore_case else found\n"
        "\n"
        "\n"
        "def count_words(text, ignore_case=False):\n"
        "    return len(split_words(text, ignore_case))\n"
        "\n"
        "\n"
        "def top_words(text, limit, ignore_case=False):\n"
        "    return Counter(split_words(text, ignore_case)).most_common(limit)\n"),
    "tests/test_counter.py": (
        "from tally.counter import count_words, top_words\n"
        "\n"
        "\n"
        "def test_count():\n"
        '    assert count_words("a b a") == 3\n'
        "\n"
        "\n"
        "def test_top():\n"
        '    assert top_words("a b a", 1) == [("a", 2)]\n'),
}


def write_builtin_fixture(dest: Path) -> Path:
    dest = Path(dest)
    for rel, body in BUILTIN_CLI.items():
        p = dest / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return dest


def prepare_workdir(ev: dict, fixtures_dir: Path) -> tuple[Path, str]:
    """A fresh temp copy of the eval's fixture, outside any directory that carries agent config.

    Returns (workdir, source) where source says which fixture was used."""
    wd = Path(tempfile.mkdtemp(prefix="evalrun-"))
    name = ev.get("fixture", "01-cli")
    src = Path(fixtures_dir) / name
    if name != "builtin-cli" and src.is_dir():
        shutil.copytree(src, wd / "repo", ignore=shutil.ignore_patterns(".claude", ".claude-plugin", ".git",
                                                                         "CLAUDE.md", "AGENTS.md"))
        source = f"fixture:{name}"
    else:
        write_builtin_fixture(wd / "repo")
        source = "builtin-cli" if name == "builtin-cli" else f"builtin-cli (fallback: {name} absent)"
    return wd / "repo", source


# --------------------------------------------------------------------------- invocation

def real_claude_home() -> Path:
    return Path(os.path.expanduser("~")) / ".claude"


def build_command(mode: str, ev: dict, claude_bin: str, model: str | None, plugin_dir: Path | None) -> list[str]:
    cmd = [claude_bin, "-p", "--output-format", "json", "--verbose", "--no-session-persistence",
           "--disallowedTools", DISALLOWED_TOOLS]
    if model:
        cmd += ["--model", model]
    if ev.get("json_schema"):
        cmd += ["--json-schema", json.dumps(ev["json_schema"], separators=(",", ":"))]
    if mode in ("with", "a", "b"):
        if plugin_dir is None:
            raise ValueError("a skill run needs a plugin directory")
        cmd += ["--plugin-dir", str(plugin_dir)]
    elif mode == "without":
        pass
    else:
        raise ValueError(f"unknown mode {mode!r}")
    if mode == "without":
        for bad in ("--plugin-dir", "--plugin-url", "--settings", "--agents", "--add-dir"):
            if bad in cmd:  # defensive: the baseline must never carry a way to load the skill
                raise AssertionError(f"baseline command carries {bad}")
    return cmd


def build_env(base: dict, cfg: Path, home: Path, coordn8_home: Path) -> dict:
    env = {k: v for k, v in base.items() if k in ENV_KEEP_EXACT or k.startswith(ENV_KEEP_PREFIX)}
    real_home = Path(base.get("HOME") or os.path.expanduser("~"))
    # credentials are READ from the real AWS files when present; nothing under them is written
    for var, rel in (("AWS_SHARED_CREDENTIALS_FILE", ".aws/credentials"), ("AWS_CONFIG_FILE", ".aws/config")):
        if var not in env and (real_home / rel).is_file():
            env[var] = str(real_home / rel)
    env.update({
        "CLAUDE_CONFIG_DIR": str(cfg),
        "HOME": str(home),
        "CLAUDE_HOME": str(cfg),
        "COORDN8_HOME": str(coordn8_home),
        "PYTHONDONTWRITEBYTECODE": "1",
        "NO_COLOR": "1",
    })
    return env


def invoke(cmd: list[str], env: dict, cwd: Path, prompt: str, timeout: int) -> dict:
    t0 = time.time()
    try:
        p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, env=env, cwd=str(cwd),
                           timeout=timeout)
        return {"rc": p.returncode, "stdout": p.stdout, "stderr": p.stderr, "seconds": round(time.time() - t0, 2),
                "timed_out": False}
    except subprocess.TimeoutExpired as ex:
        out = ex.stdout.decode() if isinstance(ex.stdout, bytes) else (ex.stdout or "")
        return {"rc": 124, "stdout": out, "stderr": f"timeout after {timeout}s", "seconds": round(time.time() - t0, 2),
                "timed_out": True}
    except OSError as ex:
        return {"rc": 127, "stdout": "", "stderr": str(ex), "seconds": round(time.time() - t0, 2), "timed_out": False}


def parse_output(stdout: str) -> dict:
    """The CLI's JSON output: one result object, or (with --verbose) the list of every message."""
    info = {"parsed": False, "result": None, "structured": None, "init": None, "tool_uses": [], "is_error": None,
            "cost_usd": None, "num_turns": None}
    try:
        data = json.loads(stdout)
    except ValueError:
        # stream-style output: one JSON object per line
        data = []
        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    data.append(json.loads(line))
                except ValueError:
                    pass
        if not data:
            return info
    msgs = data if isinstance(data, list) else [data]
    for m in msgs:
        if not isinstance(m, dict):
            continue
        if m.get("type") == "system" and m.get("subtype") == "init":
            info["init"] = m
        if m.get("type") == "assistant":
            for c in (m.get("message") or {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    info["tool_uses"].append({"name": c.get("name"), "input": c.get("input")})
        if m.get("type") == "result":
            info["parsed"] = True
            info["result"] = m.get("result")
            info["structured"] = m.get("structured_output")
            info["is_error"] = bool(m.get("is_error"))
            info["cost_usd"] = m.get("total_cost_usd")
            info["num_turns"] = m.get("num_turns")
    return info


def skill_signals(parsed: dict, canaries: list[str]) -> dict:
    text = (parsed.get("result") or "") + "\n" + json.dumps(parsed.get("structured") or "")
    hits = [c for c in canaries if re.search(c, text, re.I)]
    init = parsed.get("init") or {}
    listing = json.dumps({k: init.get(k) for k in ("plugins", "skills", "slash_commands", "agents")}).lower()
    listed = "coordn8" in listing
    invoked = any(u.get("name") == "Skill" and "coordn8" in json.dumps(u.get("input")).lower()
                  for u in parsed.get("tool_uses") or [])
    return {"canary_hits": hits, "plugin_listed_in_init": listed, "skill_invoked": invoked,
            "init_seen": bool(parsed.get("init"))}


def run_one(ev: dict, mode: str, repeat: int, opts: dict) -> dict:
    scratch = Path(tempfile.mkdtemp(prefix="evalcfg-"))
    cfg, home, c8 = scratch / "config", scratch / "home", scratch / "c8home"
    for d in (cfg, home, c8):
        d.mkdir(parents=True)
    workdir, source = prepare_workdir(ev, opts["fixtures_dir"])
    plugin = opts["plugin_dir_b"] if mode == "b" else opts["plugin_dir"]
    rec = {"eval": ev["id"], "mode": mode, "repeat": repeat, "fixture": source}
    try:
        cmd = build_command(mode, ev, opts["claude_bin"], opts.get("model"), plugin if mode != "without" else None)
        isolation = {
            "config_dir_fresh_and_empty": cfg.is_dir() and not any(cfg.iterdir()),
            "config_dir_is_not_real": cfg.resolve() != real_claude_home().resolve(),
            "home_is_temp": home.resolve() != Path(os.path.expanduser("~")).resolve(),
            "argv_has_plugin_dir": "--plugin-dir" in cmd,
        }
        env = build_env(opts["base_env"], cfg, home, c8)
        res = invoke(cmd, env, workdir, ev["prompt"], opts["timeout"])
        parsed = parse_output(res["stdout"])
        sig = skill_signals(parsed, canaries_for(ev))
        rec.update({"argv": [a if len(a) < 200 else a[:200] + "..." for a in cmd], "isolation": isolation,
                    "seconds": res["seconds"], "rc": res["rc"], "cost_usd": parsed["cost_usd"],
                    "num_turns": parsed["num_turns"], "skill_signals": sig})
        if res["timed_out"]:
            rec["status"] = "timeout"
        elif res["rc"] != 0 or not parsed["parsed"] or parsed["is_error"]:
            rec["status"] = "error"
            rec["stderr_tail"] = (res["stderr"] or "")[-800:]
            rec["stdout_head"] = (res["stdout"] or "")[:400]
        else:
            rec["status"] = "ok"
        if rec["status"] == "ok":
            grades = grade(ev, parsed["result"] or "", parsed["structured"], workdir)
            rec["assertions"] = grades
            rec["passed"] = sum(1 for g in grades if g["passed"])
            rec["total"] = len(grades)
            rec["pass_rate"] = rec["passed"] / rec["total"]
            rec["answer_head"] = (parsed["result"] or "")[:1500]
        rec["contaminated"] = (mode == "without" and (bool(sig["canary_hits"]) or sig["plugin_listed_in_init"]
                                                      or sig["skill_invoked"]))
        rec["skill_engaged"] = mode != "without" and (sig["skill_invoked"] or bool(sig["canary_hits"]))
    finally:
        shutil.rmtree(workdir.parent, ignore_errors=True)
        shutil.rmtree(scratch, ignore_errors=True)
    return rec


# --------------------------------------------------------------------------- aggregation

def summarise(runs: list[dict], threshold: float) -> dict:
    ok = [r for r in runs if r.get("status") == "ok"]
    rates = [r["pass_rate"] for r in ok]
    mean = sum(rates) / len(rates) if rates else None
    spread = (max(rates) - min(rates)) if rates else None
    stdev = math.sqrt(sum((x - mean) ** 2 for x in rates) / len(rates)) if rates else None
    flips = []
    if ok:
        for i, a in enumerate(ok[0]["assertions"]):
            vals = {r["assertions"][i]["passed"] for r in ok}
            if len(vals) > 1:
                flips.append(a["id"])
    if len(ok) < len(runs) or not ok:
        stability = "incomplete"
    elif spread > threshold + 1e-9:
        stability = "unstable"
    else:
        stability = "stable"
    per_assertion = {}
    if ok:
        for i, a in enumerate(ok[0]["assertions"]):
            per_assertion[a["id"]] = sum(1 for r in ok if r["assertions"][i]["passed"]) / len(ok)
    return {"repeats": len(runs), "completed": len(ok), "pass_rate": _r(mean), "spread": _r(spread),
            "stdev": _r(stdev), "stability": stability, "flipped_assertions": flips,
            "assertion_pass_rates": {k: _r(v) for k, v in per_assertion.items()},
            "contaminated_runs": sum(1 for r in runs if r.get("contaminated")),
            "skill_engaged_runs": sum(1 for r in runs if r.get("skill_engaged"))}


def _r(x):
    return None if x is None else round(x, 4)


def overall(runs: list[dict], mode: str, repeats: int, threshold: float) -> dict:
    mine = [r for r in runs if r["mode"] == mode]
    ok = [r for r in mine if r.get("status") == "ok"]
    passed = sum(r["passed"] for r in ok)
    total = sum(r["total"] for r in ok)
    by_rep = []
    for i in range(repeats):
        rr = [r for r in ok if r["repeat"] == i]
        t = sum(r["total"] for r in rr)
        if t:
            by_rep.append(sum(r["passed"] for r in rr) / t)
    spread = (max(by_rep) - min(by_rep)) if by_rep else None
    return {"assertions_passed": passed, "assertions_total": total,
            "pass_rate": _r(passed / total) if total else None,
            "pass_rate_by_repeat": [_r(x) for x in by_rep], "spread": _r(spread),
            "stability": ("incomplete" if len(ok) < len(mine) or not ok else
                          "unstable" if spread > threshold + 1e-9 else "stable"),
            "runs": len(mine), "completed": len(ok)}


def build_report(evals: list[dict], runs: list[dict], modes: list[str], opts: dict) -> dict:
    rep = {"schema": 1, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "model": opts.get("model") or "(CLI default)", "repeats": opts["repeats"],
           "stability_threshold": opts["threshold"], "timeout_s": opts["timeout"], "modes": modes,
           "grading": "counted by code (regex / structure / path:line resolution); no model grading",
           "evals": [], "overall": {}}
    for ev in evals:
        row = {"id": ev["id"], "requirement": ev.get("requirement"), "covers": ev.get("covers"),
               "assertions": [{"id": a["id"], "kind": a["kind"], "describe": a["describe"]} for a in ev["assertions"]],
               "modes": {}}
        for m in modes:
            rr = sorted([r for r in runs if r["eval"] == ev["id"] and r["mode"] == m], key=lambda r: r["repeat"])
            row["modes"][m] = dict(summarise(rr, opts["threshold"]), runs=rr)
        a_m, b_m = _pair(modes)
        if a_m and row["modes"][a_m]["pass_rate"] is not None and row["modes"][b_m]["pass_rate"] is not None:
            row["delta"] = _r(row["modes"][a_m]["pass_rate"] - row["modes"][b_m]["pass_rate"])
        rep["evals"].append(row)
    for m in modes:
        o = overall(runs, m, opts["repeats"], opts["threshold"])
        per = [row["modes"][m]["stability"] for row in rep["evals"]]
        if "incomplete" in per or o["stability"] == "incomplete":
            o["stability"] = "incomplete"
        elif "unstable" in per or o["stability"] == "unstable":
            o["stability"] = "unstable"
        o["unstable_evals"] = [row["id"] for row in rep["evals"] if row["modes"][m]["stability"] != "stable"]
        rep["overall"][m] = o
    a_m, b_m = _pair(modes)
    if a_m and rep["overall"][a_m]["pass_rate"] is not None and rep["overall"][b_m]["pass_rate"] is not None:
        rep["overall"]["delta"] = _r(rep["overall"][a_m]["pass_rate"] - rep["overall"][b_m]["pass_rate"])
    base = [r for r in runs if r["mode"] == "without"]
    rep["baseline"] = {
        "runs": len(base),
        "clean": not any(r.get("contaminated") for r in base),
        "contaminated_runs": [f"{r['eval']}#{r['repeat']}" for r in base if r.get("contaminated")],
        "never_passed_plugin_dir": not any(r.get("isolation", {}).get("argv_has_plugin_dir") for r in base),
        "config_isolated": all(r.get("isolation", {}).get("config_dir_fresh_and_empty") and
                               r.get("isolation", {}).get("config_dir_is_not_real") for r in base),
    } if base else None
    skill = [r for r in runs if r["mode"] in ("with", "a", "b")]
    rep["skill_engaged"] = {"runs": len(skill), "engaged": sum(1 for r in skill if r.get("skill_engaged"))}
    rep["markdown"] = render_markdown(rep)
    return rep


def _pair(modes):
    if "with" in modes and "without" in modes:
        return "with", "without"
    if "a" in modes and "b" in modes:
        return "a", "b"
    return None, None


def _pct(x):
    return "—" if x is None else f"{x * 100:.1f}%"


def render_markdown(rep: dict) -> str:
    modes = rep["modes"]
    lines = ["## Skill-vs-baseline evals", "",
             f"Model: {rep['model']} · repeats: {rep['repeats']} · stability threshold (spread): "
             f"{rep['stability_threshold']} · grading: {rep['grading']}.", ""]
    head = "| eval | requirement | " + " | ".join(f"{m} pass · spread · stability" for m in modes)
    a_m, b_m = _pair(modes)
    head += " | delta |" if a_m else " |"
    lines += [head, "|" + "---|" * (2 + len(modes) + (1 if a_m else 0))]
    for row in rep["evals"]:
        cells = [row["id"], row.get("requirement") or ""]
        for m in modes:
            s = row["modes"][m]
            cells.append(f"{_pct(s['pass_rate'])} · {_pct(s['spread'])} · {s['stability']}")
        if a_m:
            d = row.get("delta")
            cells.append("—" if d is None else f"{d * 100:+.1f} pts")
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    for m in modes:
        o = rep["overall"][m]
        lines.append(f"- **{m}**: {o['assertions_passed']}/{o['assertions_total']} assertions "
                     f"({_pct(o['pass_rate'])}), spread across repeats {_pct(o['spread'])}, {o['stability']}; "
                     f"{o['completed']}/{o['runs']} runs completed.")
    if "delta" in rep["overall"]:
        lines.append(f"- **delta ({a_m} - {b_m})**: {rep['overall']['delta'] * 100:+.1f} percentage points.")
    b = rep.get("baseline")
    if b:
        lines.append(f"- **baseline isolation**: clean={b['clean']}, never passed --plugin-dir="
                     f"{b['never_passed_plugin_dir']}, fresh empty config dir={b['config_isolated']}"
                     + (f"; contaminated runs: {', '.join(b['contaminated_runs'])}" if b["contaminated_runs"] else "")
                     + ".")
    se = rep.get("skill_engaged") or {}
    if se.get("runs"):
        lines.append(f"- **skill engaged** (Skill tool call or skill-only phrase seen) in {se['engaged']}/{se['runs']} "
                     "skill runs; a run where it did not engage measures the plugin's presence, not its method.")
    lines.append("- A pass here means the counted assertions held; it is not by itself a judgement of quality.")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- main

def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Skill-vs-baseline evals with counted assertions.")
    ap.add_argument("--mode", choices=["with", "without", "both", "ab"], default="both")
    ap.add_argument("--repeats", type=int, default=MIN_REPEATS)
    ap.add_argument("--model", default=None)
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    ap.add_argument("--eval", action="append", default=[], help="run only this eval id (repeatable)")
    ap.add_argument("--evals-dir", default=str(HERE))
    ap.add_argument("--fixtures-dir", default=str(DEFAULT_FIXTURES))
    ap.add_argument("--plugin-dir", default=str(PKG_ROOT))
    ap.add_argument("--plugin-dir-b", default=None, help="second plugin directory for --mode ab")
    ap.add_argument("--claude-bin", default=None, help="claude CLI (default: `claude` on PATH)")
    ap.add_argument("--report", default=str(DEFAULT_REPORT))
    ap.add_argument("--markdown", default=None, help="markdown section path (default: report path with .md)")
    ap.add_argument("--no-report", action="store_true", help="smoke run: print, write no report")
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--stability-threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--validate-only", action="store_true")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    evals = load_evals(Path(args.evals_dir))
    bad = {ev.get("id", ev["_file"]): validate_eval(ev) for ev in evals}
    bad = {k: v for k, v in bad.items() if v}
    if bad:
        print(json.dumps({"status": "ERROR", "invalid_evals": bad}, indent=1))
        return EXIT_ERROR
    if args.validate_only:
        print(json.dumps({"status": "PASS", "evals": len(evals),
                          "assertions": sum(len(e["assertions"]) for e in evals)}))
        return EXIT_PASS
    if args.repeats < 1:
        print(json.dumps({"status": "ERROR", "reason": "--repeats must be >= 1"}))
        return EXIT_ERROR
    if args.repeats < MIN_REPEATS and not args.no_report:
        print(json.dumps({"status": "ERROR", "reason": f"--repeats {args.repeats} < {MIN_REPEATS}: refused for a "
                          "report (stability needs at least 3 repeats); use --no-report for a smoke run"}))
        return EXIT_ERROR
    if args.eval:
        evals = [e for e in evals if e["id"] in set(args.eval)]
        if not evals:
            print(json.dumps({"status": "ERROR", "reason": f"no eval with id {args.eval}"}))
            return EXIT_ERROR
    modes = {"with": ["with"], "without": ["without"], "both": ["with", "without"], "ab": ["a", "b"]}[args.mode]
    if args.mode == "ab" and not args.plugin_dir_b:
        print(json.dumps({"status": "ERROR", "reason": "--mode ab needs --plugin-dir-b"}))
        return EXIT_ERROR
    claude_bin = args.claude_bin or shutil.which("claude")
    if not claude_bin or not Path(claude_bin).exists():
        print(json.dumps({"status": "SKIPPED", "reason": "no `claude` CLI found on PATH (or --claude-bin)"}))
        return EXIT_SKIPPED
    opts = {"claude_bin": claude_bin, "model": args.model, "timeout": args.timeout,
            "fixtures_dir": Path(args.fixtures_dir), "plugin_dir": Path(args.plugin_dir).resolve(),
            "plugin_dir_b": Path(args.plugin_dir_b).resolve() if args.plugin_dir_b else None,
            "base_env": dict(os.environ), "repeats": args.repeats, "threshold": args.stability_threshold}
    jobs = [(ev, m, i) for ev in evals for i in range(args.repeats) for m in modes]
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        runs = list(pool.map(lambda j: run_one(j[0], j[1], j[2], opts), jobs))
    rep = build_report(evals, runs, modes, opts)
    print(rep["markdown"])
    if not args.no_report:
        rp = Path(args.report)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(rep, indent=1), encoding="utf-8")
        mp = Path(args.markdown) if args.markdown else rp.with_suffix(".md")
        mp.write_text(rep["markdown"], encoding="utf-8")
    review = (rep.get("baseline") and not rep["baseline"]["clean"]) or any(
        rep["overall"][m]["stability"] != "stable" for m in modes)
    return EXIT_REVIEW if review else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
