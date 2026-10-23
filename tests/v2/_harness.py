"""Shared harness for every tests/v2 suite. Zero third-party deps.

A suite is a plain script:

    from _harness import Suite
    s = Suite("palette")
    s.check("five subjects give five different palette families", cond, detail)
    sys.exit(s.finish())

`python3 tests/v2/test_x.py --json OUT` also writes {"suite", "passed": [...], "failed": [{name, detail}]}
so tests/run_tests.py, tests/audit_v2.py and the coverage-matrix test can consume exact check names.
Check names are the evidence IDs: keep them stable once mapped.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable


class Suite:
    def __init__(self, name: str):
        self.name = name
        self.passed: list[str] = []
        self.failed: list[dict] = []
        print(f"\n[v2:{name}]")

    def check(self, name: str, cond, detail: str = "") -> bool:
        ok = bool(cond)
        if ok:
            self.passed.append(name)
            print(f"  ok   {name}")
        else:
            self.failed.append({"name": name, "detail": str(detail)[:400]})
            print(f"  FAIL {name}  {str(detail)[:300]}")
        return ok

    def finish(self) -> int:
        print(f"RESULT: {len(self.passed)} passed, {len(self.failed)} failed")
        out = None
        if "--json" in sys.argv:
            out = sys.argv[sys.argv.index("--json") + 1]
        if out:
            Path(out).write_text(json.dumps({"suite": self.name, "passed": self.passed,
                                             "failed": self.failed}, indent=1), encoding="utf-8")
        return 1 if self.failed else 0


def run(script: str, args=(), env=None, timeout: int = 600, cwd=None, input_text=None):
    """Run a package script with the current interpreter; never raises on timeout."""
    e = dict(os.environ)
    if env:
        e.update(env)
    try:
        return subprocess.run([PY, str(ROOT / script), *map(str, args)], capture_output=True,
                              text=True, env=e, timeout=timeout, cwd=cwd, input=input_text)
    except subprocess.TimeoutExpired as ex:
        return subprocess.CompletedProcess(ex.cmd, 124, ex.stdout or "", f"TIMEOUT {timeout}s")


def sandbox_env(tmp: Path) -> dict:
    """COORDN8_HOME / CLAUDE_HOME / HOME pointed into a temp dir — never the real user home."""
    tmp = Path(tmp)
    for d in ("coordn8_home", "claude_home", "home"):
        (tmp / d).mkdir(parents=True, exist_ok=True)
    return {"COORDN8_HOME": str(tmp / "coordn8_home"), "CLAUDE_HOME": str(tmp / "claude_home"),
            "HOME": str(tmp / "home"), "COORDN8_SANDBOX": "1"}


def tmpdir() -> tempfile.TemporaryDirectory:
    return tempfile.TemporaryDirectory(prefix="c8v2-")
