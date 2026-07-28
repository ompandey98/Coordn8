#!/usr/bin/env python3
"""
Coordn8 gate — DOM SMOKE TEST (webpage).

Loads the final single-file HTML in headless Chrome and checks that expected
structural markers are present in the rendered DOM (all tabs render, counts
match). Also fails loudly if the "zero external dependencies" contract is broken
(CDN links, external src/href), since that silently breaks offline double-click.

Degrades honestly: if no headless Chrome is available, it still runs a static
check on the file (dependency sweep + marker counts in raw HTML) and reports
SKIPPED-for-render so the agent knows the live DOM check did not run.

Usage:
    python3 scripts/gates/dom_smoke.py Coordn8/<slug>.html \
        --expect tab-overview:1 tab-architecture:1 flow-node:5
Each --expect item is "substring:min_count" checked against the rendered DOM
(or raw HTML if Chrome is unavailable).
"""
from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.common import (EXIT_ERROR, EXIT_PASS, EXIT_REVIEW, EXIT_SKIPPED,  # noqa: E402
                        emit, find_chrome, run)

EXTERNAL_DEP = re.compile(
    r"""(?:src|href)\s*=\s*["'](https?:)?//""", re.IGNORECASE)


def dump_dom(chrome: str, file_url: str, timeout: int = 45) -> str | None:
    try:
        cp = run([chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
                  "--dump-dom", file_url], timeout=timeout)
        if cp.returncode == 0 and cp.stdout:
            return cp.stdout
        # older chrome flag fallback
        cp = run([chrome, "--headless", "--disable-gpu", "--dump-dom", file_url],
                 timeout=timeout)
        return cp.stdout or None
    except Exception:
        return None


def parse_expect(items: list[str]) -> list[tuple[str, int]]:
    out = []
    for it in items:
        if ":" in it:
            sub, _, cnt = it.rpartition(":")
            try:
                out.append((sub, int(cnt)))
            except ValueError:
                out.append((it, 1))
        else:
            out.append((it, 1))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("html")
    ap.add_argument("--expect", nargs="*", default=[],
                    help='markers as "substring:min_count"')
    ap.add_argument("--no-chrome", action="store_true",
                    help="skip the headless render; run static checks only "
                         "(fast; used by tests and Tier B)")
    ap.add_argument("--timeout", type=int, default=45,
                    help="per headless-Chrome call timeout in seconds")
    args = ap.parse_args()

    path = Path(args.html).resolve()
    if not path.exists():
        emit({"gate": "dom_smoke", "status": "SKIPPED",
              "summary": f"no HTML at {path}"})
        return EXIT_SKIPPED

    raw = path.read_text(encoding="utf-8", errors="replace")
    expects = parse_expect(args.expect)
    findings = []

    # Contract check: zero external dependencies (works with or without Chrome).
    ext = EXTERNAL_DEP.findall(raw)
    ext_lines = [i + 1 for i, ln in enumerate(raw.splitlines())
                 if EXTERNAL_DEP.search(ln)]
    if ext_lines:
        findings.append(
            f"external dependency reference(s) at line(s) "
            f"{ext_lines[:10]} — breaks the offline single-file contract.")

    chrome = None if args.no_chrome else find_chrome()
    dom = dump_dom(chrome, path.as_uri(), args.timeout) if chrome else None
    haystack = dom if dom is not None else raw
    if dom is not None:
        render_mode = "rendered-dom"
    elif chrome is None:
        render_mode = "raw-html (no headless Chrome found)"
    else:
        render_mode = "raw-html (Chrome present but render failed/timed out)"

    marker_report = []
    for sub, need in expects:
        got = haystack.count(sub)
        ok = got >= need
        marker_report.append({"marker": sub, "need": need, "got": got, "ok": ok})
        if not ok:
            findings.append(f"marker '{sub}' found {got}x, expected >= {need}")

    # Sanity: a real interactive page is not tiny.
    if len(raw) < 2000:
        findings.append(f"HTML is only {len(raw)} bytes — suspiciously small for "
                        f"a full interactive page.")

    static_only = dom is None
    status = "REVIEW" if findings else "PASS"
    if dom is not None:
        note = "live DOM render checked."
    elif chrome is None:
        note = ("headless Chrome not found — ran STATIC checks only; the live "
                "DOM render was NOT verified. Install Chrome/Chromium (or "
                "Playwright's chromium) for the full smoke test.")
    else:
        note = ("headless Chrome was found but the render failed or timed out — "
                "ran STATIC checks only; the live DOM render was NOT verified. "
                "Re-run when the machine is less loaded, or raise --timeout.")

    emit({
        "gate": "dom_smoke",
        "status": status,
        "html": str(path),
        "render_mode": render_mode,
        "static_only": static_only,
        "external_dependency_hits": len(ext),
        "markers": marker_report,
        "findings": findings,
        "summary": (f"{len(findings)} issue(s) to review ({render_mode})"
                    if findings else f"page OK ({render_mode})"),
        "advisory_note": note,
    })
    # If we could only do static checks (Chrome absent OR failed), tell the agent
    # the live render is unverified so it never mistakes this for a full pass.
    if static_only and not findings:
        return EXIT_SKIPPED
    return EXIT_REVIEW if findings else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
