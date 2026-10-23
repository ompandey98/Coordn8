#!/usr/bin/env python3
"""Checks for the wiring the supervisor merged between streams (shared files):
the opt-in hooks merge on the non-plugin install route, the frame-review stage in the
orchestrator, and the package root in `doctor --json`. Hermetic: temp homes only."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import ROOT, Suite, sandbox_env, tmpdir  # noqa: E402

PY = sys.executable
s = Suite("supervisor_merges")


def env_for(t: Path, extra=None) -> dict:
    e = {k: v for k, v in os.environ.items() if not k.startswith(("COORDN8_", "C8_", "CLAUDE_"))}
    e.update(sandbox_env(t))
    e.update(extra or {})
    return e


def installer(args, env, cwd):
    return subprocess.run([PY, str(ROOT / "scripts/install/installer.py"), *args], capture_output=True,
                          text=True, env=env, cwd=str(cwd), timeout=300, stdin=subprocess.DEVNULL)


with tmpdir() as d:
    base = Path(d)
    # ---- hooks: opt-in merge into the host settings file, user keys untouched, removed on uninstall
    t = base / "hooks_on"
    env = env_for(t, {"COORDN8_HOOKS": "1"})
    ch = Path(env["CLAUDE_HOME"])
    ch.mkdir(parents=True, exist_ok=True)
    user = {"model": "x", "permissions": {"allow": ["Bash(ls)"]},
            "hooks": {"Stop": [{"matcher": "", "hooks": [{"type": "command", "command": "echo mine"}]}]}}
    (ch / "settings.json").write_text(json.dumps(user, indent=2))
    proj = t / "proj"
    proj.mkdir()
    r = installer(["init", "--target", "claude", "--steps", "package"], env, proj)
    st = json.loads((ch / "settings.json").read_text())
    cmds = [h["command"] for ev in st.get("hooks", {}).values() for g in ev for h in g.get("hooks", [])]
    s.check("COORDN8_HOOKS=1: the four Coordn8 hooks are merged into the host settings.json",
            r.returncode == 0 and all(any(n in c for c in cmds) for n in
                                      ("stop_gate.py", "post_render.py", "subagent_log.py", "session_start.py")),
            (r.stdout + r.stderr)[-400:])
    s.check("COORDN8_HOOKS=1: the user's own keys and their own Stop hook are kept",
            st.get("model") == "x" and st.get("permissions") == user["permissions"] and "echo mine" in cmds, "")
    s.check("hook commands use absolute paths into the package (no plugin variable on this route)",
            all("${CLAUDE_PLUGIN_ROOT}" not in c for c in cmds if "coordn8" in c.lower() or "hooks/" in c), "")
    r2 = installer(["uninstall"], env, proj)
    st2 = json.loads((ch / "settings.json").read_text())
    s.check("uninstall removes only the Coordn8 hooks; the user's settings come back exactly",
            r2.returncode == 0 and st2 == user, json.dumps(st2)[:300])

    # ---- hooks default OFF on the npx route: settings.json byte-identical
    t = base / "hooks_off"
    env = env_for(t)
    ch = Path(env["CLAUDE_HOME"])
    ch.mkdir(parents=True, exist_ok=True)
    (ch / "settings.json").write_text('{"model": "y"}\n')
    proj = t / "proj"
    proj.mkdir()
    r = installer(["init", "--target", "claude", "--steps", "package"], env, proj)
    s.check("default npx route: settings.json is not touched and the output says how to enable hooks",
            (ch / "settings.json").read_text() == '{"model": "y"}\n' and "COORDN8_HOOKS=1" in r.stdout,
            r.stdout[-300:])

# ---- orchestrator: frame-review prepare is part of the artifact stage; still five stages
sys.path.insert(0, str(ROOT / "scripts"))
import orchestrate  # noqa: E402

stages = orchestrate.build_stages(Path("/nonexistent-build"))
names = {st: [j[0] for j in jobs] for st, jobs in stages}
s.check("orchestrator keeps exactly five stages (v1.5.0 contract)",
        [st for st, _ in stages] == ["source", "plan", "trace", "artifact", "judgement"], str(list(names)))
s.check("orchestrator artifact stage runs frame_review:prepare (automatic frame review)",
        "frame_review:prepare" in names["artifact"] and "stage_v" in names["judgement"], json.dumps(names)[:300])

# ---- doctor --json reports the package root (agents on the npx route resolve scripts from it)
r = subprocess.run([PY, str(ROOT / "scripts/doctor.py"), "--json"], capture_output=True, text=True, timeout=120)
try:
    pr = json.loads(r.stdout).get("pkg_root")
except Exception:
    pr = None
s.check("doctor --json reports pkg_root, and it holds scripts/doctor.py",
        bool(pr) and (Path(pr) / "scripts" / "doctor.py").is_file(), str(pr))

# ---- run_gates: the 2.0 writing/claims/freedom rows are wired, additive, and SKIP on an empty build
with tmpdir() as d:
    r = subprocess.run([PY, str(ROOT / "scripts/run_gates.py"), d], capture_output=True, text=True, timeout=600)
    rows = {ln.split()[0]: ln.split()[1] for ln in r.stdout.splitlines()
            if ln.startswith("  ") and len(ln.split()) >= 2}
    want = ("language:page", "language:narration", "terms", "truth", "page_content", "freedom")
    s.check("run_gates lists the 2.0 language, terms, truth, page-content and freedom rows",
            all(w in rows for w in want), str({w: rows.get(w) for w in want}))
    s.check("on an empty build the new rows SKIP (freedom PASSES); none FAILS or REVIEWS",
            all(rows.get(w) == "SKIPPED" for w in want[:-1]) and rows.get("freedom") == "PASS"
            and "claims:video" not in rows, str({w: rows.get(w) for w in want}))

# ---- host-02: /design and /design-sync are recognised when present, suggested for the design stage only
with tmpdir() as d:
    d = Path(d)
    (d / "ch" / "commands").mkdir(parents=True)
    (d / "home").mkdir()
    (d / "init.json").write_text(json.dumps({"slash_commands": ["/design", "/review", "/design-sync"]}))
    def fed(*extra):
        r = subprocess.run([PY, str(ROOT / "scripts/federation.py"), "connectors", "--home", str(d / "home"),
                            "--claude-home", str(d / "ch"), *extra], capture_output=True, text=True, timeout=60)
        return json.loads(r.stdout)
    r1 = fed("--session-init", str(d / "init.json"))
    s.check("host-02: /design and /design-sync in the session command list are suggested for the design stage",
            r1["by_stage"]["design"] == ["design command: /design", "design command: /design-sync"]
            and all(not f["required"] for f in r1["found"]), json.dumps(r1["by_stage"]))
    s.check("host-02: an unrelated command (/review) is not offered as a design tool",
            not any("/review" in x for v in r1["by_stage"].values() for x in v), "")
    r0 = fed()
    s.check("host-02: with neither present, nothing is suggested and the design stage uses the built-in tools",
            r0["by_stage"]["design"] == [] and "design" in r0["stages_without_a_tool"], json.dumps(r0["by_stage"]))
    (d / "ch" / "commands" / "design.md").write_text("---\ndescription: design\n---\n")
    r2 = fed()
    s.check("host-02: an installed /design command file is recognised",
            r2["by_stage"]["design"] == ["design command: /design"], json.dumps(r2["by_stage"]))

sys.exit(s.finish())
