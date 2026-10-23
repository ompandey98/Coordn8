#!/usr/bin/env python3
"""Suite "hooks" (F3): the four host hooks and the settings.json block for the non-plugin path.

Every hook runs as a real subprocess fed the documented stdin fields (fixtures in
tests/v2/fixtures/hooks/), inside a sandbox: COORDN8_HOME / CLAUDE_HOME / HOME point into a
temp dir and the "project" is a temp repo. Nothing touches the real user home.
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import PY, ROOT, Suite, sandbox_env, tmpdir  # noqa: E402

s = Suite("hooks")
FIX = ROOT / "tests" / "v2" / "fixtures" / "hooks"
HOOKS = ROOT / "hooks"
SCRIPTS = ("session_start", "post_render", "subagent_log", "stop_gate")
TIMES: list = []
TRACEBACKS: list = []


def fixture(name: str, cwd: Path, session: str = "sess-1", transcript: str = "", **over) -> dict:
    raw = (FIX / name).read_text(encoding="utf-8")
    raw = raw.replace("__CWD__", str(cwd)).replace("__SESSION__", session) \
        .replace("__TRANSCRIPT__", transcript or str(cwd / "no-transcript.jsonl"))
    d = json.loads(raw)
    d.update(over)
    return d


def hook(script: str, payload, env: dict, cwd: Path):
    """(returncode, stdout, stderr, seconds). payload: dict -> JSON, str -> raw stdin."""
    data = payload if isinstance(payload, str) else json.dumps(payload)
    e = dict(os.environ)
    e.update(env)
    e.pop("CLAUDE_PROJECT_DIR", None)
    t0 = time.monotonic()
    try:
        p = subprocess.run([PY, str(HOOKS / f"{script}.py")], input=data, capture_output=True,
                           text=True, env=e, cwd=str(cwd), timeout=20)
        rc, so, se = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        rc, so, se = 124, "", "TIMEOUT"
    dt = time.monotonic() - t0
    TIMES.append((script, dt))
    if "Traceback" in se or "Traceback" in so:
        TRACEBACKS.append((script, se[-300:]))
    return rc, so, se, dt


def one_json(text: str):
    t = text.strip()
    if not t:
        return None
    try:
        v = json.loads(t)
    except ValueError:
        return "INVALID"
    return v if isinstance(v, dict) else "INVALID"


def project(tmp: Path, name: str = "repo", build: bool = True) -> Path:
    repo = tmp / name
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "README.md").write_text("demo\n", encoding="utf-8")
    if build:
        (repo / "Coordn8" / "_build").mkdir(parents=True, exist_ok=True)
    return repo


def marker(repo: Path, session: str, start: float) -> None:
    d = repo / "Coordn8" / "_build" / ".coordn8_sessions"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{session}.json").write_text(json.dumps({"session_id": session, "start": start}),
                                       encoding="utf-8")


def tree(root: Path, skip_build: bool = False) -> dict:
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if skip_build and (rel == "Coordn8/_build" or rel.startswith("Coordn8/_build/")):
            continue
        if p.is_file():
            out[rel] = (p.stat().st_size, p.read_bytes())
        else:
            out[rel] = "dir"
    return out


# ============================================================ hooks.json
hj_path = HOOKS / "hooks.json"
try:
    hj = json.loads(hj_path.read_text(encoding="utf-8"))
    hj_ok = isinstance(hj.get("hooks"), dict)
except Exception as e:  # noqa: BLE001
    hj, hj_ok = {"hooks": {}}, False
s.check("hooks.json parses with a top-level hooks object", hj_ok, str(hj_path))
events = hj.get("hooks", {})
s.check("hooks.json registers SessionStart, PostToolUse, SubagentStop and Stop",
        sorted(events) == sorted(["SessionStart", "PostToolUse", "SubagentStop", "Stop"]),
        sorted(events))
cmds = [h.get("command", "") for groups in events.values() for g in groups for h in g.get("hooks", [])]
paths = [m.group(1) for c in cmds for m in re.finditer(r"\$\{CLAUDE_PLUGIN_ROOT\}/([^\s\"']+)", c)]
s.check("every hooks.json command path exists in the package",
        len(paths) == 4 and all((ROOT / p).is_file() for p in paths), paths)
s.check("every hooks.json command quotes ${CLAUDE_PLUGIN_ROOT} and sets a numeric timeout",
        all(re.search(r'"\$\{CLAUDE_PLUGIN_ROOT\}[^"]*"', c) for c in cmds) and
        all(isinstance(h.get("timeout"), (int, float)) for groups in events.values()
            for g in groups for h in g.get("hooks", [])), cmds)
s.check("PostToolUse is matched to the Bash tool only",
        [g.get("matcher") for g in events.get("PostToolUse", [])] == ["Bash"],
        events.get("PostToolUse"))
try:
    sys.path.insert(0, str(ROOT / "scripts"))
    from install.plugin_layout import check_hooks  # noqa: E402
    probs = check_hooks(ROOT)
except Exception as e:  # noqa: BLE001
    probs = [f"import failed: {e}"]
s.check("plugin layout validator reports no problem in hooks/hooks.json", probs == [], probs)
fx_names = ["stop.json", "stop_active.json", "subagent_stop.json", "session_start.json",
            "post_tool_use_render.json", "post_tool_use_gates.json", "post_tool_use_other.json"]
doc_fields = {"stop.json": {"session_id", "transcript_path", "cwd", "hook_event_name",
                            "stop_hook_active", "last_assistant_message"},
              "subagent_stop.json": {"agent_id", "agent_type", "agent_transcript_path",
                                     "last_assistant_message", "stop_hook_active"},
              "post_tool_use_render.json": {"tool_name", "tool_input", "tool_response",
                                            "tool_use_id"},
              "session_start.json": {"session_id", "cwd", "source", "hook_event_name"}}
fx_ok = all((FIX / n).is_file() for n in fx_names) and all(
    f <= set(json.loads((FIX / n).read_text())) for n, f in doc_fields.items())
s.check("fixtures carry the documented stdin field names for each event", fx_ok, fx_names)

with tmpdir() as td:
    tmp = Path(td)
    env = sandbox_env(tmp / "sb")
    home = Path(env["COORDN8_HOME"])

    # ======================================================= stop_gate
    repo = project(tmp, "stop")
    b = repo / "Coordn8" / "_build"
    marker(repo, "sess-1", time.time() - 120)
    (b / "verification.json").write_text((FIX / "manifest_open.json").read_text(), encoding="utf-8")
    rc, so, se, _ = hook("stop_gate", fixture("stop.json", repo), env, repo)
    out = one_json(so)
    blocked = isinstance(out, dict) and out.get("decision") == "block"
    s.check("stop: fake 'done' with a non-READY manifest is blocked with decision=block",
            rc == 0 and blocked, f"rc={rc} out={so[:300]} err={se[-200:]}")
    reason = out.get("reason", "") if isinstance(out, dict) else ""
    s.check("stop: block reason lists the open items by id",
            all(x in reason for x in ("tree_shape", "dom_smoke", "content_ban")), reason[:600])
    s.check("stop: block reason names the refused claims", "explainability" in reason, reason[:600])
    s.check("stop: block reason is at most 1,500 characters", 0 < len(reason) <= 1500, len(reason))
    s.check("stop: stdout is exactly one JSON object", so.strip().count("\n") == 0 and
            isinstance(out, dict), so[:200])

    (b / "verification.json").write_text((FIX / "manifest_ready.json").read_text(), encoding="utf-8")
    rc, so, se, _ = hook("stop_gate", fixture("stop.json", repo), env, repo)
    s.check("stop: READY manifest is allowed (no output, exit 0)", rc == 0 and so.strip() == "",
            f"rc={rc} out={so[:300]}")

    (b / "verification.json").unlink()
    (b / "storyboard.json").write_text("{}", encoding="utf-8")
    rc, so, se, _ = hook("stop_gate", fixture("stop.json", repo), env, repo)
    out = one_json(so)
    s.check("stop: a modified build with NO manifest is blocked and says the manifest is missing",
            isinstance(out, dict) and out.get("decision") == "block" and
            "manifest" in out.get("reason", "").lower(), so[:300])

    (b / "verification.json").write_text((FIX / "manifest_open.json").read_text(), encoding="utf-8")
    rc, so, se, _ = hook("stop_gate", fixture("stop_active.json", repo), env, repo)
    s.check("stop: stop_hook_active=true is allowed (loop guard)", rc == 0 and so.strip() == "",
            so[:300])

    rc, so, se, _ = hook("stop_gate", fixture(
        "stop.json", repo, last_assistant_message="Which audience should the video target, "
        "new engineers or leadership?"), env, repo)
    s.check("stop: a question to the owner with no completion claim is allowed",
            rc == 0 and so.strip() == "", so[:300])
    rc, so, se, _ = hook("stop_gate", fixture(
        "stop.json", repo, last_assistant_message="The build is done and verified. Want the page too?"),
        env, repo)
    s.check("stop: a completion claim ending in a question is still blocked",
            isinstance(one_json(so), dict) and one_json(so).get("decision") == "block", so[:300])

    # nothing modified since the session began -> allowed
    old = time.time() - 3600
    for p in b.rglob("*"):
        if p.is_file():
            os.utime(p, (old, old))
    marker(repo, "sess-2", time.time() - 10)
    rc, so, se, _ = hook("stop_gate", fixture("stop.json", repo, session="sess-2"), env, repo)
    s.check("stop: a build untouched this session is allowed even with an open manifest",
            rc == 0 and so.strip() == "", so[:300])

    # hook-owned files alone do not count as a build
    (b / "orchestration").mkdir(exist_ok=True)
    (b / "orchestration" / "agents.jsonl").write_text('{"agent_id":"x"}\n', encoding="utf-8")
    rc, so, se, _ = hook("stop_gate", fixture("stop.json", repo, session="sess-2"), env, repo)
    s.check("stop: the hooks' own agent log does not count as a build change",
            rc == 0 and so.strip() == "", so[:300])

    # no marker -> transcript first timestamp is the session start
    tr = tmp / "transcript.jsonl"
    tr.write_text(json.dumps({"type": "user", "timestamp": time.strftime(
        "%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - 60))}) + "\n", encoding="utf-8")
    (b / "scene_s1.json").write_text("{}", encoding="utf-8")
    rc, so, se, _ = hook("stop_gate", fixture("stop.json", repo, session="sess-none",
                                              transcript=str(tr)), env, repo)
    s.check("stop: without a session marker the transcript's first timestamp marks the session",
            isinstance(one_json(so), dict) and one_json(so).get("decision") == "block", so[:300])
    tr.write_text(json.dumps({"type": "user", "timestamp": time.strftime(
        "%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() + 600))}) + "\n", encoding="utf-8")
    rc, so, se, _ = hook("stop_gate", fixture("stop.json", repo, session="sess-none",
                                              transcript=str(tr)), env, repo)
    s.check("stop: a transcript that started after the last build change is allowed",
            rc == 0 and so.strip() == "", so[:300])

    # no Coordn8 folder -> silent
    bare = project(tmp, "bare", build=False)
    before = tree(bare)
    rc, so, se, _ = hook("stop_gate", fixture("stop.json", bare), env, bare)
    s.check("stop: no Coordn8 build present -> allowed with no output",
            rc == 0 and so == "" and se == "", f"rc={rc} out={so!r} err={se[-200:]!r}")
    s.check("stop: never creates a Coordn8 folder in a repo that has none", tree(bare) == before, "")

    # unreadable manifest -> exit 0, no traceback (block or allow, never crash)
    vm = b / "verification.json"
    os.chmod(vm, 0)
    try:
        rc, so, se, _ = hook("stop_gate", fixture("stop.json", repo), env, repo)
    finally:
        os.chmod(vm, stat.S_IRUSR | stat.S_IWUSR)
    s.check("stop: an unreadable manifest exits 0 with no traceback",
            rc == 0 and "Traceback" not in se and one_json(so) != "INVALID", f"{rc} {se[-200:]}")

    # writes nothing anywhere
    before = tree(tmp / "stop")
    hook("stop_gate", fixture("stop.json", repo), env, repo)
    s.check("stop: the Stop hook writes no file at all", tree(tmp / "stop") == before, "")

    # ======================================================= subagent_log
    repo = project(tmp, "sub")
    log = repo / "Coordn8" / "_build" / "orchestration" / "agents.jsonl"
    rc, so, se, _ = hook("subagent_log", fixture("subagent_stop.json", repo), env, repo)
    lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    rec = json.loads(lines[0]) if len(lines) == 1 else {}
    s.check("subagent_log: one SubagentStop appends exactly one line",
            rc == 0 and len(lines) == 1 and so.strip() == "", f"rc={rc} lines={len(lines)} out={so!r}")
    s.check("subagent_log: the line carries ts, agent_id, agent_type, last_assistant_message",
            set(rec) == {"ts", "agent_id", "agent_type", "last_assistant_message"} and
            rec.get("agent_id") == "def456" and rec.get("agent_type") == "coordn8-reader", rec)
    long_msg = "x" * 2000
    hook("subagent_log", fixture("subagent_stop.json", repo, last_assistant_message=long_msg,
                                 agent_id="a2"), env, repo)
    lines = log.read_text(encoding="utf-8").splitlines()
    s.check("subagent_log: a second subagent adds exactly one more line, message cut to 500 chars",
            len(lines) == 2 and len(json.loads(lines[1])["last_assistant_message"]) == 500,
            len(lines))
    bare2 = project(tmp, "bare2", build=False)
    before = tree(bare2)
    rc, so, se, _ = hook("subagent_log", fixture("subagent_stop.json", bare2), env, bare2)
    s.check("subagent_log: no Coordn8 folder -> nothing written, no output",
            rc == 0 and so == "" and tree(bare2) == before, so)
    os.chmod(log, stat.S_IRUSR)
    try:
        rc, so, se, _ = hook("subagent_log", fixture("subagent_stop.json", repo), env, repo)
    finally:
        os.chmod(log, stat.S_IRUSR | stat.S_IWUSR)
    s.check("subagent_log: an unwritable log file exits 0 with no output or traceback",
            rc == 0 and so == "" and "Traceback" not in se, f"{rc} {se[-200:]}")

    # ======================================================= post_render
    repo = project(tmp, "post")
    bb = repo / "Coordn8" / "_build"
    (bb / "trace_s1.json").write_text(
        (ROOT / "tests/v2/fixtures/layout/trace_through_box.json").read_text(), encoding="utf-8")
    before = tree(repo)
    rc, so, se, dt = hook("post_render", fixture("post_tool_use_render.json", repo), env, repo)
    out = one_json(so)
    ctx = (out or {}).get("hookSpecificOutput", {}) if isinstance(out, dict) else {}
    text = ctx.get("additionalContext", "")
    s.check("post_render: a capture_scene.py command returns PostToolUse additionalContext",
            rc == 0 and ctx.get("hookEventName") == "PostToolUse" and text, so[:300])
    s.check("post_render: the scorecard carries the quality build score and the disk result",
            "build score" in text and "- disk:" in text and "quality" in text, text[:500])
    s.check("post_render: the scorecard says scores are not a quality certificate and what was NOT verified",
            "never a quality certificate" in text and "NOT verified" in text, text[-400:])
    s.check("post_render: after a render the frame-review instruction is appended with the real package root",
            "Invoke the coordn8-frame-review skill now" in text and
            f"python3 {ROOT}/scripts/review/frame_review.py prepare" in text and "<pkg>" not in text
            and "`contradicts` blocks the build." in text, text[-700:])
    s.check("post_render: running the measurements wrote nothing into the project", tree(repo) == before, "")

    rc, so, se, _ = hook("post_render", fixture("post_tool_use_gates.json", repo), env, repo)
    text = ((one_json(so) or {}).get("hookSpecificOutput", {}) if isinstance(one_json(so), dict)
            else {}).get("additionalContext", "")
    s.check("post_render: run_gates.py gets the scorecard but not the frame-review instruction",
            "build score" in text and "coordn8-frame-review" not in text, text[:300])

    rc, so, se, _ = hook("post_render", fixture("post_tool_use_other.json", repo), env, repo)
    s.check("post_render: an unrelated Bash command gets no output (no scorecard, no frame-review text)",
            rc == 0 and so.strip() == "", so[:200])
    rc, so, se, _ = hook("post_render", fixture("post_tool_use_render.json", repo,
                                                 tool_name="Write"), env, repo)
    s.check("post_render: a non-Bash tool gets no output", rc == 0 and so.strip() == "", so[:200])

    (bb / "trace_s1.json").unlink()
    rc, so, se, _ = hook("post_render", fixture("post_tool_use_gates.json", repo), env, repo)
    text = ((one_json(so) or {}).get("hookSpecificOutput", {}) if isinstance(one_json(so), dict)
            else {}).get("additionalContext", "")
    s.check("post_render: with no trace, quality is reported UNMEASURED rather than passed",
            "UNMEASURED" in text and "unmeasured is not a pass" in text, text[:300])

    bare3 = project(tmp, "bare3", build=False)
    rc, so, se, _ = hook("post_render", fixture("post_tool_use_gates.json", bare3), env, bare3)
    s.check("post_render: no Coordn8 folder and a gate command -> no output",
            rc == 0 and so.strip() == "", so[:200])

    # ======================================================= session_start
    seed = (f"import sys; sys.path.insert(0, {str(ROOT / 'scripts')!r}); "
            "from memory.store import Store; st = Store(); "
            "st.add('preference', 'Always use a warm amber accent palette in every video', "
            "scope='global', source_quote='use warm amber accents', confidence='high')")
    e2 = dict(os.environ); e2.update(env)
    sp = subprocess.run([PY, "-c", seed], env=e2, capture_output=True, text=True, timeout=30)
    s.check("session_start: test seeds one preference into the sandbox memory store",
            sp.returncode == 0, sp.stderr[-300:])
    repo = project(tmp, "sess")
    home_before = tree(home)
    rc, so, se, _ = hook("session_start", fixture("session_start.json", repo, session="s-a"), env, repo)
    out = one_json(so)
    ctx = out.get("hookSpecificOutput", {}) if isinstance(out, dict) else {}
    s.check("session_start: emits SessionStart additionalContext",
            rc == 0 and ctx.get("hookEventName") == "SessionStart" and ctx.get("additionalContext"),
            so[:300])
    s.check("session_start: whole stdout is at most 4,096 bytes", 0 < len(so.encode("utf-8")) <= 4096,
            len(so.encode("utf-8")))
    s.check("session_start: injected context contains the seeded preference",
            "warm amber accent palette" in ctx.get("additionalContext", ""),
            ctx.get("additionalContext", "")[:400])
    s.check("session_start: injected context states that the live brief outranks memory",
            "live brief" in ctx.get("additionalContext", ""), "")
    mk = repo / "Coordn8" / "_build" / ".coordn8_sessions" / "s-a.json"
    try:
        start1 = json.loads(mk.read_text())["start"]
    except Exception:  # noqa: BLE001
        start1 = None
    s.check("session_start: writes the session marker inside Coordn8/_build/", start1 is not None, mk)
    time.sleep(0.05)
    hook("session_start", fixture("session_start.json", repo, session="s-a", source="resume"), env, repo)
    try:
        start2 = json.loads(mk.read_text())["start"]
    except Exception:  # noqa: BLE001
        start2 = None
    s.check("session_start: a resumed session keeps its original start time", start1 == start2,
            (start1, start2))
    s.check("session_start: never writes into the user memory store", tree(home) == home_before, "")

    # big memory still fits
    seed_many = (f"import sys; sys.path.insert(0, {str(ROOT / 'scripts')!r}); "
                 "from memory.store import Store; st = Store()\n"
                 "for i in range(150): st.add('preference', 'Prefer option number %d with a fairly "
                 "long explanation of why it matters for every single build we run' % i, "
                 "scope='global', source_quote='q', confidence='med')")
    subprocess.run([PY, "-c", seed_many], env=e2, capture_output=True, text=True, timeout=60)
    rc, so, se, _ = hook("session_start", fixture("session_start.json", repo, session="s-b"), env, repo)
    s.check("session_start: with 150 stored preferences stdout is still <= 4,096 bytes and valid JSON",
            isinstance(one_json(so), dict) and len(so.encode("utf-8")) <= 4096, len(so.encode("utf-8")))

    env_empty = sandbox_env(tmp / "sb_empty")
    bare4 = project(tmp, "bare4", build=False)
    before = tree(bare4)
    rc, so, se, _ = hook("session_start", fixture("session_start.json", bare4), env_empty, bare4)
    s.check("session_start: no Coordn8 folder and no memory -> no output, nothing created",
            rc == 0 and so.strip() == "" and tree(bare4) == before, so[:200])

    # ======================================================= robustness, every hook
    robust_repo = project(tmp, "robust")
    marker(robust_repo, "sess-1", time.time() - 100)
    (robust_repo / "Coordn8" / "_build" / "x.json").write_text("{}", encoding="utf-8")
    bad_inputs = {"malformed JSON": "{not json", "empty stdin": "", "JSON array": "[1,2]",
                  "wrong field types": json.dumps({"cwd": 5, "session_id": [], "stop_hook_active": "no",
                                                   "tool_input": "str", "agent_id": {"a": 1},
                                                   "last_assistant_message": 7, "transcript_path": 3}),
                  "missing fields": json.dumps({"hook_event_name": "Stop"}),
                  "cwd does not exist": json.dumps({"cwd": str(tmp / "nope"), "session_id": "z"})}
    worst = []
    for script in SCRIPTS:
        for label, raw in bad_inputs.items():
            rc, so, se, _ = hook(script, raw, env, robust_repo)
            if rc != 0 or "Traceback" in se or one_json(so) == "INVALID":
                worst.append(f"{script}/{label}: rc={rc} out={so[:80]!r} err={se[-120:]!r}")
    s.check("every hook: malformed stdin, missing or mistyped fields exit 0 with no traceback",
            not worst, worst[:4])

    # a hook whose package scripts are missing still exits 0 silently
    shadow = tmp / "shadow"
    (shadow / "hooks").mkdir(parents=True)
    for f in HOOKS.glob("*.py"):
        (shadow / "hooks" / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    bad = []
    for script in SCRIPTS:
        payload = fixture({"stop_gate": "stop.json", "post_render": "post_tool_use_render.json",
                           "subagent_log": "subagent_stop.json",
                           "session_start": "session_start.json"}[script], robust_repo)
        p = subprocess.run([PY, str(shadow / "hooks" / f"{script}.py")], input=json.dumps(payload),
                           capture_output=True, text=True, env=e2, timeout=20)
        if p.returncode != 0 or "Traceback" in p.stderr or one_json(p.stdout) == "INVALID":
            bad.append(f"{script}: {p.returncode} {p.stderr[-150:]}")
    s.check("every hook: with its package scripts missing it still exits 0 with no traceback",
            not bad, bad)

    # every hook writes only under Coordn8/_build
    wrepo = project(tmp, "writes")
    marker(wrepo, "w", time.time() - 100)
    (wrepo / "Coordn8" / "_build" / "trace_s1.json").write_text(
        (ROOT / "tests/v2/fixtures/layout/trace_through_box.json").read_text(), encoding="utf-8")
    outside_before = tree(wrepo, skip_build=True)
    hb = tree(home)
    for script, fx in (("session_start", "session_start.json"), ("post_render", "post_tool_use_render.json"),
                       ("subagent_log", "subagent_stop.json"), ("stop_gate", "stop.json")):
        hook(script, fixture(fx, wrepo, session="w"), env, wrepo)
    s.check("every hook: nothing written outside <cwd>/Coordn8/_build/ and nothing into the user store",
            tree(wrepo, skip_build=True) == outside_before and tree(home) == hb, "")

s.check("every hook run finished in under 2 seconds",
        TIMES and max(t for _, t in TIMES) < 2.0,
        sorted(TIMES, key=lambda x: -x[1])[:3])
s.check("no hook run printed a Python traceback", not TRACEBACKS, TRACEBACKS[:3])

# ============================================================ settings block (npx path)
try:
    from install import hooks_settings as HS  # noqa: E402
    hs_ok = True
except Exception as e:  # noqa: BLE001
    hs_ok, HS = False, None
s.check("hooks_settings imports", hs_ok, "")
if hs_ok:
    blk = HS.settings_block(ROOT)
    flat = [h["command"] for groups in blk.values() for g in groups for h in g["hooks"]]
    s.check("settings_block: same events as hooks.json, absolute paths, no ${CLAUDE_PLUGIN_ROOT}",
            sorted(blk) == sorted(events) and all("${CLAUDE_PLUGIN_ROOT}" not in c for c in flat)
            and len(flat) == 4, flat)
    s.check("settings_block: every command points at an existing hook script",
            all(Path(re.sub(r"^python3\s+", "", c).strip("'\"")).is_file() for c in flat), flat)
    s.check("settings_block: is pure (two calls give equal results, nothing written)",
            HS.settings_block(ROOT) == blk, "")
    s.check("is_ours: a user's own hook script is not mistaken for a Coordn8 hook",
            not HS.is_ours({"hooks": [{"type": "command", "command": "python3 /me/hooks/lint.py"}]})
            and not HS.is_ours({"hooks": [{"type": "command", "command": "~/.claude/hooks/deny.sh"}]})
            and all(HS.is_ours(g) for groups in blk.values() for g in groups), "")

    with tmpdir() as td:
        tmp = Path(td)
        env = sandbox_env(tmp / "sb")
        old_env = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            user = {"model": "opus", "permissions": {"allow": ["Bash(ls:*)"], "deny": []},
                    "env": {"FOO": "bar"}, "statusLine": {"type": "command", "command": "echo hi"},
                    "hooks": {"PreToolUse": [{"matcher": "WebFetch", "hooks": [
                        {"type": "command", "command": "~/.claude/hooks/deny.sh"}]}],
                        "Stop": [{"hooks": [{"type": "command", "command": "afplay done.aiff"}]}]}}
            sp = tmp / "settings.json"
            original = json.dumps(user, indent=2, ensure_ascii=False) + "\n"
            sp.write_text(original, encoding="utf-8")
            HS.merge_settings(sp, ROOT)
            merged = json.loads(sp.read_text())
            keys_same = all(json.dumps(merged[k], sort_keys=False) == json.dumps(user[k], sort_keys=False)
                            for k in user if k != "hooks")
            s.check("merge: every non-hooks user key is byte-identical after merge", keys_same,
                    {k: merged.get(k) for k in user})
            s.check("merge: user hook groups are kept byte-identical and first; Coordn8 groups appended",
                    json.dumps(merged["hooks"]["PreToolUse"]) == json.dumps(user["hooks"]["PreToolUse"])
                    and json.dumps(merged["hooks"]["Stop"][0]) == json.dumps(user["hooks"]["Stop"][0])
                    and len(merged["hooks"]["Stop"]) == 2 and HS.is_ours(merged["hooks"]["Stop"][1])
                    and all(ev in merged["hooks"] for ev in blk), merged["hooks"])
            r2 = HS.merge_settings(sp, ROOT)
            s.check("merge: a second merge changes nothing (idempotent, no duplicate groups)",
                    all(e["action"] == "unchanged" for e in r2["events"]) and
                    len(json.loads(sp.read_text())["hooks"]["Stop"]) == 2, r2)
            moved = tmp / "pkg v2"
            (moved / "hooks").mkdir(parents=True)
            for f in HOOKS.iterdir():
                (moved / "hooks" / f.name).write_bytes(f.read_bytes())
            HS.merge_settings(sp, moved)
            mv = json.loads(sp.read_text())
            s.check("merge: re-install from a new package path (with a space) replaces the old Coordn8 "
                    "groups instead of duplicating them",
                    len(mv["hooks"]["Stop"]) == 2 and str(moved) in mv["hooks"]["Stop"][1]["hooks"][0]["command"]
                    and "'" in mv["hooks"]["Stop"][1]["hooks"][0]["command"], mv["hooks"]["Stop"])
            bk = list((Path(env["COORDN8_HOME"]) / "backups").rglob("*settings.json*"))
            s.check("merge: a backup of the user's settings.json is taken in the user backups folder",
                    len(bk) >= 1, bk)
            HS.remove_settings(sp)
            s.check("remove: restores the seeded settings.json byte-identically",
                    sp.read_text(encoding="utf-8") == original, sp.read_text()[:400])

            # no hooks key before -> removed again; odd formatting -> same content
            odd = tmp / "odd.json"
            odd_text = '{"theme":"dark",   "includeCoAuthoredBy": false}'
            odd.write_text(odd_text, encoding="utf-8")
            HS.merge_settings(odd, ROOT)
            HS.remove_settings(odd)
            s.check("remove: a file without hooks before install has the same content after remove",
                    json.loads(odd.read_text()) == json.loads(odd_text), odd.read_text())
            badf = tmp / "bad.json"
            badf.write_text('{"hooks": ["not", "an", "object"]}', encoding="utf-8")
            try:
                HS.merge_settings(badf, ROOT)
                refused = False
            except Exception:  # noqa: BLE001
                refused = True
            s.check("merge: refuses (and leaves the file untouched) when hooks is not an object",
                    refused and badf.read_text() == '{"hooks": ["not", "an", "object"]}', badf.read_text())
            cli = subprocess.run([PY, str(ROOT / "scripts/install/hooks_settings.py"), "block"],
                                 capture_output=True, text=True, env=dict(os.environ), timeout=30)
            s.check("hooks_settings CLI 'block' prints the hooks object",
                    cli.returncode == 0 and set(json.loads(cli.stdout)["hooks"]) == set(blk), cli.stderr)
        finally:
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

sys.exit(s.finish())
