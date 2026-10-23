#!/usr/bin/env python3
"""Suite "host": current Claude Code mechanism names (F5), per-host confirmed/assumed
parity (F6), connector detection (F7) and skill federation (F8).

Hermetic: every fixture is copied into a temp dir; CLAUDE_HOME / HOME / COORDN8_HOME
point into it; no network; the real ~/.claude, ~/.claude.json and ~/.coordn8 are never
read or written.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import PY, ROOT, Suite, run, sandbox_env, tmpdir  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
import federation  # noqa: E402
import host_detect  # noqa: E402

s = Suite("host")
FIX = ROOT / "tests" / "v2" / "fixtures" / "host"
DOC = ROOT / "docs" / "HOST_INTEGRATION.md"
CAPS_P = ROOT / "data" / "host_capabilities.json"
FACTS = Path(os.environ.get("C8_DOCS_FACTS",
             str(ROOT.parent / "Coordn8_v2_handoff" / "_working" / "CLAUDE_CODE_DOCS_FACTS.md")))
PROPOSAL = Path(os.environ.get("C8_PROPOSED_R3C",
                str(ROOT.parent / "Coordn8_v2_handoff" / "_working" / "w5s5" / "PROPOSED_R3c.md")))
SCRUB = ("CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION", "COORDN8_HOST")

# Every current Claude Code mechanism F5 names (re-checked 2026-10-23 at code.claude.com/docs).
MECHANISMS = ["Agent", "TaskCreate", "TaskGet", "TaskList", "TaskUpdate", "Monitor",
              "run_in_background", "TaskStop", "EnterPlanMode", "ExitPlanMode",
              "EnterWorktree", "isolation: worktree", "SendMessage", "PNG", "JPEG",
              "claude -p --output-format json --json-schema", "structured_output",
              "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS", "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH",
              "CLAUDE_CODE_ENABLE_TODO_TOOLS", "CLAUDE_CODE_ENABLE_TASKS=0",
              "Stop", "SubagentStop", "SessionStart", "PostToolUse"]
R3C_NEEDLES = ["TaskCreate", "TaskGet", "TaskList", "TaskUpdate", "TodoWrite", "Monitor",
               "run_in_background", "Read", "EnterPlanMode", "ExitPlanMode", "Agent",
               "20", "depth 3", "isolation: worktree", "PNG", "JPEG",
               "claude -p --output-format json --json-schema"]
TODO_QUALIFIERS = ("only", "disabled", "legacy", "never as the default", "not the default",
                   "CLAUDE_CODE_ENABLE_TASKS=0", "older", "formerly")


def todo_not_default(text: str) -> list[str]:
    """Lines that mention TodoWrite without saying it is legacy / only-where-enabled."""
    bad = []
    for ln in text.splitlines():
        if "TodoWrite" in ln and not any(q in ln for q in TODO_QUALIFIERS):
            bad.append(ln.strip()[:120])
    return bad


def bashoutput_not_current(text: str) -> list[str]:
    return [ln.strip()[:120] for ln in text.splitlines()
            if ("BashOutput" in ln or "TaskOutput" in ln)
            and not re.search(r"\bnot\b|removed|no longer", ln)]


def validate_caps(caps: dict) -> list[str]:
    errs = []
    st_ok = set(caps.get("statuses", []))
    srcs = caps.get("sources", {})
    seen = {}
    for i, r in enumerate(caps.get("rows", [])):
        for k in ("host", "mechanism", "status", "source", "checked", "how", "fallback"):
            if not r.get(k):
                errs.append(f"row {i} missing {k}")
        if r.get("status") not in st_ok:
            errs.append(f"row {i} bad status {r.get('status')!r}")
        if r.get("source") and r["source"] not in srcs:
            errs.append(f"row {i} source {r['source']!r} not in sources")
        if r.get("checked") and not re.match(r"^\d{4}-\d{2}-\d{2}$", r["checked"]):
            errs.append(f"row {i} bad date")
        if r.get("status") == "confirmed":
            url = srcs.get(r.get("source"), {}).get("url", "")
            if not url.startswith("https://code.claude.com/docs"):
                errs.append(f"row {i} confirmed without a checked docs URL")
        key = (r.get("host"), r.get("mechanism"))
        seen[key] = seen.get(key, 0) + 1
    for h in caps.get("hosts", []):
        for m in caps.get("mechanisms", []):
            n = seen.get((h, m["id"]), 0)
            if n != 1:
                errs.append(f"{h}/{m['id']} has {n} rows")
    return errs


def scrubbed_env(extra=None):
    e = {k: v for k, v in os.environ.items() if k not in SCRUB}
    if extra:
        e.update(extra)
    return e


def tree_state(root: Path):
    return sorted((str(p.relative_to(root)), p.stat().st_size, int(p.stat().st_mtime_ns))
                  for p in root.rglob("*") if p.is_file())


# =========================================================================== F5
doc = DOC.read_text(encoding="utf-8")
missing = [m for m in MECHANISMS if m not in doc]
s.check("HOST_INTEGRATION.md names every current Claude Code mechanism", not missing, missing)

facts_names = []
if FACTS.is_file():
    ft = FACTS.read_text(encoding="utf-8")
    sec = ft.split("## Tools (current names)", 1)[-1].split("\n## ", 1)[0]
    facts_names = sorted(set(re.findall(r"\b(?:[A-Z][a-z]+){2,}\b", sec)) | {"Agent", "Monitor", "Read"})
    miss_f = [n for n in facts_names if n not in doc]
    s.check("HOST_INTEGRATION.md names every tool listed in the docs-facts file", not miss_f,
            miss_f)
else:
    s.check("HOST_INTEGRATION.md names every tool listed in the docs-facts file",
            all(n in doc for n in ("TaskCreate", "TodoWrite", "TaskOutput", "BashOutput",
                                   "TaskStop", "EnterPlanMode", "EnterWorktree")),
            "facts file absent: checked the fixed list instead")

s.check("HOST_INTEGRATION.md does not recommend TodoWrite as the default",
        not todo_not_default(doc), todo_not_default(doc))
phase_rows = [ln for ln in doc.splitlines() if ln.startswith("| Phase / scene tracking")]
s.check("phase tracking row names TaskCreate/TaskGet/TaskList/TaskUpdate",
        phase_rows and all(t in phase_rows[0] for t in ("TaskCreate", "TaskGet", "TaskList", "TaskUpdate")),
        phase_rows)
s.check("seeded defect: a doc line recommending TodoWrite for phases is caught",
        todo_not_default("| Track phases | **TodoWrite**, one item per phase |") != [])
s.check("HOST_INTEGRATION.md presents BashOutput/TaskOutput only as not current",
        not bashoutput_not_current(doc), bashoutput_not_current(doc))
s.check("seeded defect: 'poll with BashOutput' is caught",
        bashoutput_not_current("then poll with **BashOutput**") != [])
s.check("doc states the background output is read with Read",
        "output goes to a file the agent reads with **`Read`**" in doc)
s.check("doc gives the subagent cap 20 and nesting depth 3",
        "**20** subagents" in doc and "**3** layers" in doc)
s.check("doc keeps the v1.5 host needles (Task tool, TodoWrite, BashOutput, blindness, Other hosts)",
        all(n in doc for n in ("Task tool", "TodoWrite", "BashOutput",
                               "Blindness is a dispatch property", "Other hosts")))

caps = json.loads(CAPS_P.read_text(encoding="utf-8"))
cc_rows = [r for r in caps["rows"] if r["host"] == "claude-code"]
s.check("every Claude Code row is confirmed against a code.claude.com docs URL with a date",
        cc_rows and all(r["status"] == "confirmed" and
                        caps["sources"][r["source"]]["url"].startswith("https://code.claude.com/docs")
                        and r["checked"] for r in cc_rows), "")

if PROPOSAL.is_file():
    pt = PROPOSAL.read_text(encoding="utf-8")
    section = pt.split("\n---\n", 1)[0]
    size = len(section.encode("utf-8"))
    s.check("PROPOSED_R3c.md exists and its replacement section is <= 3 KB", size <= 3072, size)
    miss_p = [n for n in R3C_NEEDLES if n not in section]
    s.check("PROPOSED_R3c.md names every mechanism", not miss_p, miss_p)
    s.check("PROPOSED_R3c.md keeps TodoWrite only where enabled",
            not todo_not_default(section), todo_not_default(section))
    s.check("PROPOSED_R3c.md lists outdated brain lines as old -> new",
            len(re.findall(r"^\d+\. L\d+.*→", pt, flags=re.M)) >= 5, "")
else:
    # After the supervisor merges the proposal, the brain itself must carry it.
    brain = (ROOT / "engine" / "PLAYBOOK.md").read_text(encoding="utf-8")
    r3c = brain.split("### R.3c", 1)[-1].split("\n### R.4", 1)[0]
    miss_p = [n for n in R3C_NEEDLES if n not in r3c]
    s.check("PROPOSED_R3c.md exists and its replacement section is <= 3 KB",
            False, "proposal absent; checking merged brain instead (see next check)")
    s.check("PROPOSED_R3c.md names every mechanism", not miss_p, f"brain R.3c missing {miss_p}")

# =========================================================================== F6
s.check("host_capabilities.json parses with 5 hosts and >= 15 mechanisms",
        set(caps["hosts"]) == {"claude-code", "codex", "cursor", "kiro", "generic"}
        and len(caps["mechanisms"]) >= 15, "")
errs = validate_caps(caps)
s.check("every row has status+source+date and every host has one row per mechanism",
        not errs, errs[:5])
bad = json.loads(json.dumps(caps))
bad["rows"][3].pop("source")
bad["rows"].append(dict(bad["rows"][0]))
bad["rows"].append({**bad["rows"][-1], "host": "codex", "mechanism": "hooks", "status": "confirmed",
                    "source": "not-checked"})
s.check("seeded defects (missing source, duplicate row, unverified 'confirmed') are caught",
        len(validate_caps(bad)) >= 3, validate_caps(bad))
s.check("no codex/cursor/kiro/generic row claims confirmed (their docs were not consulted)",
        all(r["status"] != "confirmed" for r in caps["rows"] if r["host"] != "claude-code"), "")
s.check("every non-confirmed row names what Coordn8 does instead",
        all(r["fallback"] for r in caps["rows"] if r["status"] != "confirmed"), "")
block = doc.split("<!-- HOST-MATRIX:BEGIN -->", 1)[-1].split("<!-- HOST-MATRIX:END -->", 1)[0].strip()
s.check("doc confirmed/assumed matrix matches data/host_capabilities.json (no drift)",
        block == host_detect.matrix_markdown(caps), "re-run: python3 scripts/host_detect.py --matrix")
topics = ["skill", "Agent", "hooks", "run_in_background", "TaskCreate", "EnterPlanMode",
          "PNG", "worktree", "claude -p", "Codex", "Cursor", "Kiro", "Generic",
          "same blindness"]
s.check("doc covers skills, agents, hooks, background, tasks, plan, images, worktrees, headless for every host",
        all(t in doc for t in topics), [t for t in topics if t not in doc])

with tmpdir() as td:
    t = Path(td)
    hosts_dir = t / "hosts"
    shutil.copytree(FIX / "hosts", hosts_dir)
    claude_env = json.loads((hosts_dir / "claude_env.json").read_text())
    got = {
        "claude-code": host_detect.detect(claude_env, hosts_dir / "generic")["host"],
        "codex": host_detect.detect({}, hosts_dir / "codex")["host"],
        "cursor": host_detect.detect({}, hosts_dir / "cursor")["host"],
        "kiro": host_detect.detect({}, hosts_dir / "kiro")["host"],
        "generic": host_detect.detect({}, hosts_dir / "generic")["host"],
    }
    for want, have in got.items():
        s.check(f"host_detect identifies the {want} fixture", have == want, got)
    r = run("scripts/host_detect.py", ["--env-json", hosts_dir / "claude_env.json",
                                      "--cwd", hosts_dir / "kiro", "--json"])
    rep = json.loads(r.stdout or "{}")
    s.check("CLI: confirmed environment signal outranks project files",
            r.returncode == 0 and rep.get("host") == "claude-code" and rep.get("basis") == "environment",
            r.stderr[-300:])
    r = subprocess.run([PY, str(ROOT / "scripts" / "host_detect.py"), "--json", "--cwd",
                        str(hosts_dir / "generic")], capture_output=True, text=True,
                       env=scrubbed_env(), timeout=60)
    s.check("CLI with the real environment scrubbed of host variables -> generic",
            json.loads(r.stdout or "{}").get("host") == "generic", r.stdout[-200:])
    s.check("COORDN8_HOST override wins",
            host_detect.detect({"COORDN8_HOST": "kiro", "CLAUDECODE": "1"}, hosts_dir / "codex")["host"] == "kiro")
    s.check("an unknown COORDN8_HOST is ignored, not trusted",
            host_detect.detect({"COORDN8_HOST": "bogus"}, hosts_dir / "cursor")["host"] == "cursor")
    # Walk-up stops at cwd when there is no git root, and at the git root when there is.
    (t / "outer" / ".kiro").mkdir(parents=True)
    (t / "outer" / "inner").mkdir()
    s.check("host files above cwd are ignored when there is no git root",
            host_detect.detect({}, t / "outer" / "inner")["host"] == "generic")
    (t / "outer" / ".git").mkdir()
    s.check("host files at the git root are found from a subdirectory",
            host_detect.detect({}, t / "outer" / "inner")["host"] == "kiro")
    rep = host_detect.detect({}, hosts_dir / "codex")
    s.check("a host without confirmed spawning gets sequential passes with the same blindness",
            "never the builder's claim list" in rep["fleet"] and rep["degrade"], rep["fleet"][:120])
    rep = host_detect.detect(claude_env, hosts_dir / "generic")
    s.check("Claude Code report carries per-mechanism status, source URL and date",
            rep["capabilities"]["task_tools"]["status"] == "confirmed"
            and rep["capabilities"]["task_tools"]["source"].startswith("https://code.claude.com"), "")
    # Never network: no network module in the source, and a socket-blocked run still works.
    src = (ROOT / "scripts" / "host_detect.py").read_text(encoding="utf-8")
    s.check("host_detect.py imports no network module",
            not re.search(r"^\s*(import|from)\s+(socket|urllib|http|requests|ssl)\b", src, re.M), "")
    code = ("import socket,sys,json\n"
            "def _no(*a,**k): raise RuntimeError('network used')\n"
            "socket.socket=_no; socket.create_connection=_no; socket.getaddrinfo=_no\n"
            f"sys.path.insert(0,{str(ROOT / 'scripts')!r})\n"
            "import host_detect\n"
            f"print(json.dumps(host_detect.detect({{}}, {str(hosts_dir / 'kiro')!r})['host']))\n")
    r = subprocess.run([PY, "-c", code], capture_output=True, text=True, env=scrubbed_env(), timeout=60)
    s.check("host_detect runs with every socket call blocked", r.stdout.strip() == '"kiro"', r.stderr[-300:])

# =========================================================================== F7
with tmpdir() as td:
    t = Path(td)
    env = sandbox_env(t)
    env["PYTHONDONTWRITEBYTECODE"] = "1"   # the interpreter's own cache is not our write
    home, ch = Path(env["HOME"]), Path(env["CLAUDE_HOME"])
    shutil.copytree(FIX / "claude_home", ch, dirs_exist_ok=True)
    proj = t / "proj"
    proj.mkdir()
    (proj / ".mcp.json").write_text((FIX / "mcp" / "project" / "mcp.json").read_text())
    (home / ".claude.json").write_text((FIX / "mcp" / "home" / "claude.json").read_text()
                                       .replace("__PROJECT__", str(proj.resolve())))
    sess = t / "session"
    shutil.copytree(FIX / "session", sess)
    before = tree_state(t)
    r = run("scripts/federation.py", ["connectors", "--home", home, "--claude-home", ch, "--project", proj,
                                      "--tool-names", sess / "tool_names.txt",
                                      "--session-init", sess / "init.json"], env=env)
    res = json.loads(r.stdout or "{}")
    found = {(f["kind"], f["name"]): f["stages"] for f in res.get("found", [])}
    s.check("F7: connectors CLI runs on a fixture MCP config", r.returncode == 0, r.stderr[-300:])
    s.check("F7: user-scope, local-scope, project and plugin MCP servers are all found",
            {("local MCP", "coordn8-browser"), ("local MCP", "blender-bridge"), ("local MCP", "figma"),
             ("plugin MCP", "image-tools")} <= set(found), sorted(found))
    s.check("F7: claude.ai connectors recognised from session tool names and MCP list",
            ("claude.ai connector", "Figma") in found and ("claude.ai connector", "Whiteboard") in found,
            sorted(found))
    s.check("F7: stage suggestions map tools to stages",
            "design" in found.get(("claude.ai connector", "Figma"), [])
            and "diagram" in found.get(("claude.ai connector", "Whiteboard"), [])
            and "review" in found.get(("local MCP", "coordn8-browser"), [])
            and "video" in found.get(("local MCP", "blender-bridge"), []), found)
    s.check("F7: by_stage lists the design tools",
            any("Figma" in x for x in res.get("by_stage", {}).get("design", [])), res.get("by_stage"))
    s.check("F7: nothing is ever required", res.get("found") and
            all(f["required"] is False for f in res["found"]), "")
    s.check("F7: sign-in-only tools are offered last, never auto-installed",
            any(o["id"] == "claude-ai-connectors" and o["auto_install"] is False
                for o in res.get("offer_last", [])), res.get("offer_last"))
    roots = [str(p.resolve()) for p in (home, ch, proj, sess)]
    s.check("F7: connector detection reads only inside the given roots",
            res.get("files_read") and all(any(f.startswith(rt) for rt in roots) for f in res["files_read"]),
            res.get("files_read"))
    s.check("F7: connector detection writes nothing", tree_state(t) == before, "tree changed")
    empty = federation.connectors(t / "nohome", t / "noch", None)
    s.check("F7: with no MCP config at all it still answers, every stage left to the built-in",
            empty["found"] == [] and set(empty["stages_without_a_tool"]) == set(federation.STAGES), empty)
    cs = federation.load_caps()["connector_stages"]
    unrelated = [st for kw, sts in cs["keywords"].items() if kw in "unrelateddb" for st in sts]
    s.check("F7: an unrelated server name maps to no stage", unrelated == [], unrelated)

# =========================================================================== F8
with tmpdir() as td:
    t = Path(td)
    env = sandbox_env(t)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    ch = Path(env["CLAUDE_HOME"])
    shutil.copytree(FIX / "claude_home", ch, dirs_exist_ok=True)

    r = run("scripts/federation.py", ["list"], env=env)          # CLAUDE_HOME from the env
    lst = json.loads(r.stdout or "{}")
    inv = sorted(x["invoke_as"] for x in lst.get("skills", []))
    s.check("F8: list finds the 3 fixture skills (personal + plugin) via CLAUDE_HOME",
            inv == ["/arch-diagrams", "/plain-prose", "/release-kit:changelog"], inv)
    pp = [x for x in lst.get("skills", []) if x["name"] == "plain-prose"]
    s.check("F8: folded YAML description is parsed",
            pp and pp[0]["description"].startswith("Edit prose into plain language"), pp)

    r = run("scripts/federation.py", ["match", "--stage", "diagram"], env=env)
    m = json.loads(r.stdout or "{}")
    dc = m.get("stages", {}).get("diagram", {}).get("candidates", [])
    s.check("F8: the diagram skill ranks first for 'diagram' with its matched phrases",
            dc and dc[0]["invoke_as"] == "/arch-diagrams" and "diagram" in dc[0]["matched"], dc)
    r = run("scripts/federation.py", ["match", "--stage", "video"], env=env)
    v = json.loads(r.stdout or "{}").get("stages", {}).get("video", {})
    s.check("F8: 'video' returns no candidate when no video skill is installed",
            v.get("candidates") == [] and v.get("use", "").startswith("built-in"), v)
    allm = federation.match(lst["skills"])
    s.check("F8: every stage names a built-in implementation that exists in the package",
            all(allm[st]["builtin"] and all((ROOT / b).exists() for b in allm[st]["builtin"])
                for st in federation.STAGES),
            {st: allm[st]["builtin"] for st in federation.STAGES})
    s.check("F8: Coordn8 stays complete with zero installed skills",
            all(r["candidates"] == [] and r["builtin"] for r in federation.match([]).values()), "")

    # Plugin name from .claude-plugin/plugin.json; project skills; self is excluded.
    pdir = ch / "plugins" / "cache" / "demo-market" / "release-kit" / "1.2.0"
    (pdir / ".claude-plugin").mkdir()
    (pdir / ".claude-plugin" / "plugin.json").write_text('{"name": "release-tools"}')
    proj = t / "proj"
    (proj / ".claude" / "skills" / "diagram-studio").mkdir(parents=True)
    (proj / ".claude" / "skills" / "diagram-studio" / "SKILL.md").write_text(
        "---\nname: diagram-studio\ndescription: Diagram, flowchart, sequence diagram and "
        "architecture diagram maker; decision tree layouts.\n---\n")
    (ch / "skills" / "coordn8").mkdir()
    (ch / "skills" / "coordn8" / "SKILL.md").write_text(
        "---\nname: coordn8\ndescription: video diagram explain design\n---\n")
    skills, rd = federation.list_skills(ch, proj)
    inv = {x["invoke_as"] for x in skills}
    s.check("F8: plugin skills are namespaced by the plugin.json name",
            "/release-tools:changelog" in inv, sorted(inv))
    s.check("F8: project .claude/skills are listed", "/diagram-studio" in inv, sorted(inv))
    mm = federation.match(skills, ["diagram", "video"])
    s.check("F8: Coordn8 itself is never offered as a federation candidate",
            all(c["skill"] != "coordn8" for st in mm.values() for c in st["candidates"]), mm)

    # Never outside the roots: a symlinked skill dir and a symlinked plugin dir.
    outside = t / "outside"
    (outside / "evil").mkdir(parents=True)
    (outside / "evil" / "SKILL.md").write_text(
        "---\nname: evil\ndescription: video animation voiceover OUTSIDE-MARKER\n---\n")
    (outside / "plug" / "skills" / "x").mkdir(parents=True)
    (outside / "plug" / "skills" / "x" / "SKILL.md").write_text(
        "---\nname: x\ndescription: video OUTSIDE-MARKER\n---\n")
    os.symlink(outside / "evil", ch / "skills" / "evil")
    os.symlink(outside / "plug", ch / "plugins" / "plug")
    r = run("scripts/federation.py", ["match", "--claude-home", ch, "--project", proj], env=env)
    out = r.stdout or ""
    res = json.loads(out or "{}")
    roots = (str(ch.resolve()), str(proj.resolve()))
    s.check("F8: federation never reads outside the given roots (symlinks out are skipped)",
            "OUTSIDE-MARKER" not in out and res.get("files_read")
            and all(f.startswith(roots) for f in res["files_read"])
            and res["stages"]["video"]["candidates"] == [], res.get("skipped_outside_roots"))
    s.check("F8: the skipped outside path is reported, not silently dropped",
            any("evil" in p for p in res.get("skipped_outside_roots", [])), res.get("skipped_outside_roots"))

    # Probe: used + gates passed -> adopt, and an adopted candidate ranks first next time.
    before_ch = tree_state(ch)
    gate = t / "gate.json"
    gate.write_text('{"gate": "diagram", "status": "PASS"}')
    r = run("scripts/federation.py", ["probe", "--project", proj, "--stage", "diagram",
                                      "--skill", "/arch-diagrams", "--used", "yes", "--gate-json", gate], env=env)
    rec = json.loads(r.stdout or "{}").get("record", {})
    s.check("F8 probe: used + gates PASS -> adopt", rec.get("verdict") == "adopt", r.stdout[-300:])
    m2 = federation.match(skills, ["diagram"], history=federation.read_history(proj))
    c0 = m2["diagram"]["candidates"]
    s.check("F8 probe: an adopted candidate ranks first even over a higher score",
            c0 and c0[0]["invoke_as"] == "/arch-diagrams" and c0[0]["probe"] == "adopt"
            and any(c["score"] > c0[0]["score"] for c in c0[1:]), c0)
    gate.write_text('{"gate": "diagram", "status": "FAIL"}')
    rec2 = json.loads(run("scripts/federation.py", ["probe", "--project", proj, "--stage", "diagram",
                                                    "--skill", "/diagram-studio", "--used", "yes",
                                                    "--gate-json", gate], env=env).stdout or "{}").get("record", {})
    rec3 = federation.record_probe(proj, "writing", "/plain-prose", False, True)
    rec4 = federation.record_probe(proj, "writing", "/plain-prose", True, None)
    s.check("F8 probe: gates FAIL -> reject; not used -> reject; no gate result -> unproven",
            rec2.get("verdict") == "reject" and rec3["verdict"] == "reject" and rec4["verdict"] == "unproven",
            (rec2, rec3, rec4))
    m3 = federation.match(skills, ["diagram"], history=federation.read_history(proj))
    s.check("F8 probe: a rejected candidate ranks last",
            m3["diagram"]["candidates"][-1]["invoke_as"] == "/diagram-studio", m3["diagram"]["candidates"])
    logf = proj / "Coordn8" / "_build" / "orchestration" / "federation.jsonl"
    s.check("F8 probe: records land only in the project's build folder, never in CLAUDE_HOME",
            logf.is_file() and len(logf.read_text().splitlines()) == 4 and tree_state(ch) == before_ch, "")
    unmatched = [st for st, v in federation.match(skills).items()
                 if any(c["invoke_as"] == "/release-tools:changelog" for c in v["candidates"])]
    s.check("F8: list returns every installed skill, matched or not (menu, not fence)",
            "/release-tools:changelog" in {x["invoke_as"] for x in skills} and unmatched == [],
            unmatched)

sys.exit(s.finish())
