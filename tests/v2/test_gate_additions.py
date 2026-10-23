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
    return s.finish()


if __name__ == "__main__":
    sys.exit(main())
