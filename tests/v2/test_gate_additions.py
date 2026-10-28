#!/usr/bin/env python3
"""Supervisor-owned checks for small additive changes to existing v1.5.0 gates."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import Suite, run, tmpdir  # noqa: E402


def status(r):
    try:
        return json.loads(r.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return {}


def main() -> int:
    s = Suite("gate_additions")
    with tmpdir() as d:
        d = Path(d)
        css_only = d / "css.html"
        css_only.write_text("<html><style>.a{width:calc(100% - 2rem)} :root{--c8-ink:#111}</style>"
                            "<script>function f(){return push(item)}</script><p>A clean sentence.</p></html>")
        r0, r1 = run("scripts/gates/content_ban.py", [css_only]), run("scripts/gates/content_ban.py", [css_only, "--skip-code"])
        s.check("content_ban default behaviour unchanged: inline CSS/JS is still swept without the flag",
                status(r0).get("status") == "REVIEW", r0.stdout[:200])
        s.check("content_ban --skip-code ignores inline CSS/JS bodies on a clean page",
                status(r1).get("status") == "PASS", r1.stdout[:200])
        body_hit = d / "body.html"
        body_hit.write_text("<html><style>.a{x:1}</style><p>Welcome to the team! It is great.</p></html>")
        r2 = run("scripts/gates/content_ban.py", [body_hit, "--skip-code"])
        s.check("content_ban --skip-code still catches banned reader-facing text",
                status(r2).get("status") == "REVIEW", r2.stdout[:200])
        data_hit = d / "data.html"
        data_hit.write_text('<html><script type="application/json">{"t":"welcome aboard, new hire"}</script><p>x</p></html>')
        r3 = run("scripts/gates/content_ban.py", [data_hit, "--skip-code"])
        s.check("content_ban --skip-code still sweeps JSON data blocks",
                status(r3).get("status") == "REVIEW", r3.stdout[:200])
    with tmpdir() as d2:
        import os, shutil, subprocess
        d2 = Path(d2); root = d2 / "plugin"
        (root / "bin").mkdir(parents=True); (root / "hooks").mkdir()
        from _harness import ROOT
        for rel in ("bin/coordn8", "bin/coordn8.js", "install.sh", "hooks/session_start.py", "hooks/_common.py"):
            shutil.copy(ROOT / rel, root / rel); os.chmod(root / rel, 0o644)
        env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(root), HOME=str(d2), COORDN8_HOME=str(d2 / "c8"))
        r = subprocess.run([sys.executable, str(root / "hooks/session_start.py")], input='{"cwd": "%s"}' % d2,
                           capture_output=True, text=True, env=env, timeout=30)
        s.check("session start restores the executable flag a browser upload stripped (bin/coordn8, coordn8.js, install.sh)",
                all(os.access(str(root / x), os.X_OK) for x in ("bin/coordn8", "bin/coordn8.js", "install.sh")),
                r.stdout[-200:] + r.stderr[-200:])
        s.check("the repaired hook still exits cleanly", r.returncode == 0, r.stderr[-300:])
    # Manim: detected wherever it lives; never reinstalled; never holds setup for long
    with tmpdir() as d3:
        import os, stat, subprocess, time as _t
        from _harness import ROOT
        d3 = Path(d3); fb = d3 / "bin"; fb.mkdir()
        fake_py = fb / "python3.12"
        fake_py.write_text('#!/bin/sh\nif [ "$1" = "-m" ] && [ "$2" = "manim" ]; then echo "Manim Community v0.19.0"; exit 0; fi\nexit 1\n')
        fake_py.chmod(0o755)
        env = dict(os.environ, PATH=f"{fb}:/usr/bin:/bin", HOME=str(d3))
        code = ("import sys; sys.path.insert(0, %r); from lib.common import find_manim; print(find_manim())" % str(ROOT / "scripts"))
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=120)
        s.check("Manim installed only for ANOTHER local python is found (python3.12 -m manim)",
                "python3.12" in r.stdout and "manim" in r.stdout, r.stdout + r.stderr[-200:])
        code2 = ("import sys; sys.argv=['x']; sys.path.insert(0, %r); import setup_env as S; r=S.ensure_manim(False); print(r['status'])" % str(ROOT / "scripts"))
        r2 = subprocess.run([sys.executable, "-c", code2], capture_output=True, text=True, env=env, timeout=120)
        s.check("setup treats that Manim as already present and does not pip-install it",
                r2.stdout.strip().endswith("already-present"), r2.stdout[-200:] + r2.stderr[-300:])
        env3 = dict(os.environ, PATH="/usr/bin:/bin", HOME=str(d3), COORDN8_NO_MANIM="1")
        code3 = ("import sys; sys.argv=['x']; sys.path.insert(0, %r); import setup_env as S; S.has_manim=lambda: False; print(S.ensure_manim(False)['status'])" % str(ROOT / "scripts"))
        r3 = subprocess.run([sys.executable, "-c", code3], capture_output=True, text=True, env=env3, timeout=60)
        s.check("COORDN8_NO_MANIM skips the Manim install entirely", r3.stdout.strip().endswith("skipped"), r3.stdout + r3.stderr[-200:])
        code4 = ("import sys; sys.argv=['x']; sys.path.insert(0, %r); import setup_env as S; print(S.run(['sleep','60'], timeout=22)[0])" % str(ROOT / "scripts"))
        t0 = _t.time()
        r4 = subprocess.run([sys.executable, "-c", code4], capture_output=True, text=True, timeout=90)
        s.check("a long child prints a 'still running' heartbeat and is killed at its budget (exit 124, < 30 s)",
                r4.stdout.strip().endswith("124") and "still running" in r4.stderr and _t.time() - t0 < 30,
                (r4.stdout[-80:], r4.stderr[-200:], round(_t.time() - t0, 1)))
        src = (ROOT / "scripts" / "setup_env.py").read_text()
        s.check("the Manim install is wheels-only, bounded, and never runs an OS-package build during setup",
                "--only-binary=:all:" in src and "MANIM_BUDGET" in src and "_manim_system_deps(dry=False)" not in src, "")
    src_i = (ROOT / "scripts" / "install" / "installer.py").read_text()
    s.check("the installer ignores SIGTTOU/SIGTTIN so a background terminal write can never pause the install",
            "_never_pause_on_the_terminal()" in src_i and "SIGTTOU" in src_i and "SIGTTIN" in src_i, "")
    # One install at a time; npx cleanup never deletes a copy another process runs from
    with tmpdir() as d4:
        import os, subprocess, time as _t
        from _harness import ROOT, sandbox_env
        d4 = Path(d4); env = sandbox_env(d4)
        home = Path(env["COORDN8_HOME"]); home.mkdir(parents=True, exist_ok=True)
        sleeper = subprocess.Popen(["sleep", "60"])
        (home / ".install.lock").write_text(str(sleeper.pid))
        r = run("scripts/install/installer.py", ["init", "--target", "generic", "--steps", "package",
                "--project-dir", str(d4 / "proj")], env=env, timeout=120)
        s.check("a second install while one is running refuses with a clear message and the other pid (exit 3)",
                r.returncode == 3 and str(sleeper.pid) in (r.stdout + r.stderr), (r.returncode, (r.stdout + r.stderr)[-300:]))
        sleeper.kill(); sleeper.wait()
        r2 = run("scripts/install/installer.py", ["init", "--target", "generic", "--steps", "package",
                 "--project-dir", str(d4 / "proj")], env=env, timeout=300)
        s.check("a lock left by a process that is gone is stale and the install proceeds",
                r2.returncode == 0 and not (home / ".install.lock").exists(), (r2.returncode, (r2.stdout + r2.stderr)[-300:]))
        cache = d4 / "npmcache" / "_npx"; entry = cache / "abc123"
        (entry / "node_modules" / "coordn8").mkdir(parents=True)
        holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", f"{entry}/node_modules/coordn8/bin/coordn8.js"])
        _t.sleep(0.5)
        code = ("import sys; sys.path.insert(0, %r); from install import npx_cache as N; import json; "
                "print(json.dumps(N.clean(cache=__import__('pathlib').Path(%r))))") % (str(ROOT / "scripts"), str(d4 / "npmcache"))
        r3 = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        s.check("npx cleanup keeps a cached copy another process is running from", entry.exists(), r3.stdout[-300:])
        holder.kill(); holder.wait()
        r4 = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        s.check("npx cleanup removes the whole cached copy (no half-deleted folder left behind) once nothing uses it",
                not entry.exists() and not list(cache.glob("*removing*")), r4.stdout[-300:])
    return s.finish()


if __name__ == "__main__":
    sys.exit(main())
