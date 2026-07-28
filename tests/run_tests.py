#!/usr/bin/env python3
"""
Coordn8 test suite — zero third-party deps (no pytest required).

Covers the two things that protect the core guarantee:
  1. VERBATIM: every generated adapter embeds PLAYBOOK.md byte-for-byte, and the
     generator's --check agrees. This is what makes "never inferior to the raw
     prompt" mechanically true.
  2. GATES: each advisory gate returns the right exit code / status on crafted
     good and bad fixtures (a linear chain is flagged; a real tree passes; banned
     ids are caught; broken references are caught; missing inputs SKIP cleanly).

Run:  python3 tests/run_tests.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
PASS, FAIL = 0, 0
FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        FAILURES.append(f"{name}: {detail}")
        print(f"  FAIL {name}  {detail}")


def run(script: str, args: list[str]):
    return subprocess.run([PY, str(ROOT / script), *args],
                          capture_output=True, text=True)


# ---------------------------------------------------------------------------
def test_verbatim():
    print("\n[verbatim: adapters embed the brain byte-for-byte]")
    brain = (ROOT / "engine" / "PLAYBOOK.md").read_text(encoding="utf-8")
    sys.path.insert(0, str(ROOT / "build"))
    import generate_adapters as ga  # noqa

    planned = ga.build_all()
    for path, contents in planned.items():
        embedded = ga.extract_brain(contents)
        rel = path.relative_to(ROOT)
        check(f"{rel} embeds brain verbatim",
              embedded == brain,
              "embedded brain differs from PLAYBOOK.md")
        check(f"{rel} exists on disk and matches generator",
              path.exists() and path.read_text(encoding="utf-8") == contents,
              "on-disk adapter is stale — run generate_adapters.py")

    r = run("build/generate_adapters.py", ["--check"])
    check("generator --check passes", r.returncode == 0,
          r.stdout + r.stderr)


def test_tree_shape():
    print("\n[gate: tree_shape flags linear chains, passes real trees]")
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        # a real decision tree: 2 branches, 3 terminals, labeled exits
        good = {
            "flows": [{
                "id": "u1", "title": "Refund", "expected_outcomes": 3,
                "nodes": [
                    {"id": "n1", "kind": "branch", "label": "Within 30 days?"},
                    {"id": "n2", "kind": "branch", "label": "Item damaged?"},
                    {"id": "t1", "kind": "terminal", "label": "Full refund + R1"},
                    {"id": "t2", "kind": "terminal", "label": "Store credit + R2"},
                    {"id": "t3", "kind": "terminal", "label": "Deny + R3"},
                ],
                "edges": [
                    {"from": "n1", "to": "n2", "when": "within 30 days"},
                    {"from": "n1", "to": "t3", "when": "past 30 days"},
                    {"from": "n2", "to": "t1", "when": "damaged"},
                    {"from": "n2", "to": "t2", "when": "not damaged"},
                ],
            }]
        }
        # a linear chain masquerading as a 3-outcome flow: 0 branches
        bad = {
            "flows": [{
                "id": "u2", "title": "Bad", "expected_outcomes": 3,
                "nodes": [
                    {"id": "a", "kind": "process", "label": "step 1"},
                    {"id": "b", "kind": "process", "label": "step 2"},
                    {"id": "c", "kind": "terminal", "label": "done"},
                ],
                "edges": [
                    {"from": "a", "to": "b", "when": ""},
                    {"from": "b", "to": "c", "when": ""},
                ],
            }]
        }
        gp = d / "good.json"; gp.write_text(json.dumps(good))
        bp = d / "bad.json"; bp.write_text(json.dumps(bad))

        r = run("scripts/gates/tree_shape.py", [str(gp)])
        check("real tree PASSES (exit 0)", r.returncode == 0, r.stdout)

        r = run("scripts/gates/tree_shape.py", [str(bp)])
        check("linear chain flagged REVIEW (exit 1)", r.returncode == 1, r.stdout)
        check("linear-chain finding names 'linear chain'",
              "linear chain" in r.stdout.lower(), r.stdout)

        r = run("scripts/gates/tree_shape.py", [str(d / "missing.json")])
        check("missing flows -> SKIPPED (exit 2)", r.returncode == 2, r.stdout)


def test_content_ban():
    print("\n[gate: content_ban catches ids/emails, passes clean text]")
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        dirty = d / "dirty.html"
        dirty.write_text(
            "<p>Processed order #100294 for a customer.</p>\n"
            "<p>Contact alice@example.com about case-55321.</p>\n")
        clean = d / "clean.html"
        clean.write_text(
            "<h1>How the refund engine decides</h1>\n"
            "<p>The system routes each request by channel, then by age.</p>\n"
            "<p>This is a plain explanation with no identifiers at all.</p>\n"
            "<p>It uses machine learning for one judgment step only.</p>\n")

        r = run("scripts/gates/content_ban.py", [str(dirty)])
        check("dirty payload flagged REVIEW (exit 1)", r.returncode == 1, r.stdout)
        check("catches order id", "100294" in r.stdout, r.stdout)
        check("catches email", "example.com" in r.stdout, r.stdout)

        r = run("scripts/gates/content_ban.py", [str(clean)])
        check("clean payload PASSES (exit 0)", r.returncode == 0, r.stdout)


def test_facts_trace():
    print("\n[gate: facts_trace catches broken references]")
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "src").mkdir()
        code = d / "src" / "engine.py"
        code.write_text("\n".join(f"line {i}" for i in range(1, 51)))  # 50 lines
        manifest = {
            "repo_root": str(d),
            "claims": [
                {"id": "c1", "text": "handles routing", "ref": "src/engine.py:10"},
                {"id": "c2", "text": "bad line", "ref": "src/engine.py:999"},
                {"id": "c3", "text": "missing file", "ref": "src/nope.py:1"},
                {"id": "c4", "text": "no ref"},
            ],
        }
        mp = d / "claims.json"; mp.write_text(json.dumps(manifest))
        r = run("scripts/gates/facts_trace.py", [str(mp)])
        check("broken refs flagged REVIEW (exit 1)", r.returncode == 1, r.stdout)
        try:
            payload = json.loads(r.stdout.splitlines()[0])
            check("exactly 3 unresolved (999, missing, no-ref)",
                  payload.get("unresolved") == 3, r.stdout)
        except Exception as e:  # noqa
            check("facts_trace emits parseable JSON", False, str(e))


def test_dom_smoke():
    print("\n[gate: dom_smoke catches external deps + missing markers]")
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        bad = d / "bad.html"
        bad.write_text(
            "<html><head>"
            "<link rel='stylesheet' href='https://cdn.example.com/x.css'>"
            "</head><body>" + ("<div class='tab'>x</div>" * 3) +
            ("padding " * 500) + "</body></html>")
        r = run("scripts/gates/dom_smoke.py",
                [str(bad), "--no-chrome", "--expect", "tab:3",
                 "nonexistent-marker:1"])
        # external dep + missing marker => REVIEW (or SKIPPED if no chrome but
        # still finds the external dep => REVIEW). Must not be PASS.
        check("external dep + missing marker not PASS",
              r.returncode in (1,), r.stdout)
        check("reports external dependency", "external depend" in r.stdout.lower(),
              r.stdout)


def test_env_detect():
    print("\n[env_detect: emits a valid tier + bounded worker budget]")
    r = run("scripts/env_detect.py", ["--json"])
    check("env_detect runs (exit 0)", r.returncode == 0, r.stderr)
    try:
        data = json.loads(r.stdout)
        check("tier in A/B/C/D", data.get("tier") in ("A", "B", "C", "D"),
              str(data.get("tier")))
        w = data.get("parallel_budget", {}).get("cpu_bound_workers", 0)
        check("cpu workers bounded >=1 and <= cores",
              1 <= w <= (data.get("machine", {}).get("cpu_cores") or 1),
              f"workers={w}")
    except Exception as e:  # noqa
        check("env_detect emits valid JSON", False, str(e))


def test_parallel_pool():
    print("\n[render: bounded parallel pool self-test]")
    r = run("scripts/render/parallel.py", ["--selftest"])
    check("parallel pool self-test passes", r.returncode == 0,
          r.stdout + r.stderr)


def main() -> int:
    print("Coordn8 test suite")
    print("=" * 50)
    test_verbatim()
    test_tree_shape()
    test_content_ban()
    test_facts_trace()
    test_dom_smoke()
    test_env_detect()
    test_parallel_pool()
    print("=" * 50)
    print(f"RESULT: {PASS} passed, {FAIL} failed")
    if FAILURES:
        print("\nFailures:")
        for f in FAILURES:
            print("  - " + f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
