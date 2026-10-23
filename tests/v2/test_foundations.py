#!/usr/bin/env python3
"""Foundations: one paths module, and the safe writer (backup -> block merge -> verify)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import ROOT, Suite, run, sandbox_env, tmpdir  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts" / "lib"))


def main() -> int:
    s = Suite("foundations")
    with tmpdir() as d:
        d = Path(d)
        env = sandbox_env(d)
        os.environ.update(env)
        import importlib
        import paths
        importlib.reload(paths)
        import safe_write
        importlib.reload(safe_write)
        s.check("paths: COORDN8_HOME overrides the user home", str(paths.user_home()) == env["COORDN8_HOME"],
                str(paths.user_home()))
        s.check("paths: CLAUDE_HOME overrides ~/.claude", str(paths.claude_home()) == env["CLAUDE_HOME"], "")
        created = paths.ensure_user_home()
        s.check("paths: ensure_user_home creates every user-zone dir + schema.json",
                all((paths.user_home() / n).is_dir() for n in paths.USER_SUBDIRS)
                and (paths.user_home() / "schema.json").exists(), str(created))
        (paths.user_home() / "schema.json").write_text('{"schema": 1, "mine": true}')
        paths.ensure_user_home()
        s.check("paths: ensure_user_home never modifies an existing file",
                json.loads((paths.user_home() / "schema.json").read_text()).get("mine") is True, "")
        s.check("paths: a project's Coordn8 folder is user-owned",
                paths.is_user_owned(d / "repo" / "Coordn8" / "x.html"), "")

        # safe_write: pre-existing user file survives install + reinstall with one block
        agents = d / "repo" / "AGENTS.md"
        agents.parent.mkdir(parents=True)
        user_text = "# My project rules\n\nAlways run make test.\n"
        agents.write_text(user_text)
        r1 = safe_write.merge_block(agents, "BRAIN v1\n", "2.0.0")
        t1 = agents.read_text()
        s.check("safe_write: user content survives the first merge", t1.startswith(user_text), t1[:120])
        s.check("safe_write: a backup was taken before the first edit",
                r1["backup"] and Path(r1["backup"]).read_text() == user_text, str(r1))
        r2 = safe_write.merge_block(agents, "BRAIN v2\n", "2.0.1")
        t2 = agents.read_text()
        s.check("safe_write: reinstall replaces only the block (exactly one block, new content)",
                t2.count("COORDN8:BEGIN") == 1 and "BRAIN v2" in t2 and "BRAIN v1" not in t2
                and t2.startswith(user_text), t2)
        r3 = safe_write.remove_block(agents)
        s.check("safe_write: removing the block restores the user's text exactly",
                agents.read_text() == user_text, repr(agents.read_text()))
        # nested brain fences inside our block do not confuse the parser
        safe_write.merge_block(agents, "<!-- COORDN8:BRAIN:BEGIN -->\nx\n<!-- COORDN8:BRAIN:END -->\n", "2.0.0")
        s.check("safe_write: inner brain fences are not mistaken for block markers",
                agents.read_text().count("<!-- COORDN8:END -->") == 1, "")
        # broken markers -> refuse
        bad = d / "bad.md"
        bad.write_text("x\n<!-- COORDN8:BEGIN v1 -->\nno end\n")
        try:
            safe_write.merge_block(bad, "y", "2")
            refused = False
        except safe_write.SafeWriteError:
            refused = True
        s.check("safe_write: an unmatched BEGIN marker is refused, never guessed", refused, "")
        # JSON: only our key changes
        cfg = d / "claude.json"
        cfg.write_text(json.dumps({"theme": "dark", "mcpServers": {"theirs": {"command": "x"}}}, indent=4))
        safe_write.merge_json(cfg, "mcpServers.coordn8-fetch", {"command": "uvx", "args": ["mcp-server-fetch"]})
        data = json.loads(cfg.read_text())
        s.check("safe_write: JSON merge adds only the Coordn8 key and keeps every other key",
                data["theme"] == "dark" and data["mcpServers"]["theirs"] == {"command": "x"}
                and "coordn8-fetch" in data["mcpServers"], json.dumps(data))
        safe_write.remove_json(cfg, "mcpServers.coordn8-fetch")
        s.check("safe_write: JSON key removal leaves the user's keys intact",
                json.loads(cfg.read_text()) == {"theme": "dark", "mcpServers": {"theirs": {"command": "x"}}}, "")
        fresh = d / "fresh.json"
        safe_write.merge_json(fresh, "mcpServers.coordn8-fetch", {"command": "uvx"})
        nokey = d / "nokey.json"
        nokey.write_text(json.dumps({"theme": "light"}))
        safe_write.merge_json(nokey, "mcpServers.coordn8-fetch", {"command": "uvx"})
        s.check("safe_write: JSON merge creates a missing file / missing parent object",
                json.loads(fresh.read_text()) == {"mcpServers": {"coordn8-fetch": {"command": "uvx"}}}
                and json.loads(nokey.read_text()) == {"theme": "light", "mcpServers": {"coordn8-fetch": {"command": "uvx"}}},
                fresh.read_text() + nokey.read_text())
        badj = d / "bad.json"
        badj.write_text("{not json")
        try:
            safe_write.merge_json(badj, "a", 1)
            refused = False
        except safe_write.SafeWriteError:
            refused = True
        s.check("safe_write: an unparseable JSON file is refused and left byte-identical",
                refused and badj.read_text() == "{not json", "")
        # CLI
        cf = d / "content.txt"
        cf.write_text("CLI BLOCK\n")
        r = run("scripts/lib/safe_write.py", ["merge-block", str(agents), str(cf), "--version", "2.0.0"], env=env)
        s.check("safe_write CLI: merge-block prints ok JSON", r.returncode == 0 and json.loads(r.stdout)["ok"],
                r.stdout + r.stderr)
    return s.finish()


if __name__ == "__main__":
    sys.exit(main())
