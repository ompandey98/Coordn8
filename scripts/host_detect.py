#!/usr/bin/env python3
"""Coordn8 — which agent host is this, and what can it do? Zero third-party deps.

Reads environment variables and a few files in the working directory (walking up
to the nearest git root, never past it), then reports the host and its per-mechanism
capability rows from data/host_capabilities.json. It NEVER uses the network and
never writes anything.

Precedence, strongest first:
  1. COORDN8_HOST=<claude-code|codex|cursor|kiro|generic>   (explicit override)
  2. environment signals recorded as confirmed (e.g. CLAUDECODE=1)
  3. project files (.kiro/, .cursor/, AGENTS.md, .claude/, CLAUDE.md) — weak, assumed
  4. generic

Usage:
    python3 scripts/host_detect.py [--cwd DIR] [--env-json FILE] [--json]

--env-json replaces the process environment with the JSON object in FILE (tests
use it so a run inside one host can prove detection of another).

The report also carries `degrade`: what Coordn8 does for every mechanism the host
does not have confirmed — chiefly, agent roles run as sequential passes that keep
the same blindness (a verifier pass reads only the artefact and the sentence,
never the builder's claim list).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parents[1]
CAPS = PKG_ROOT / "data" / "host_capabilities.json"

BLIND_SEQUENCE = ("Run each agent role as its own pass, in the stage order, writing the same "
                  "bus files under _build/orchestration/. Each pass re-reads only its "
                  "predecessor's artefact. A verifier pass reads the artefact and the "
                  "sentence it is checking, never the builder's claim list; the claim "
                  "auditor runs last and is the only pass that reads the claims. Say in the "
                  "handover which roles ran in sequence.")


def load_caps(path: Path = CAPS) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _search_dirs(cwd: Path) -> list[Path]:
    """cwd, then its parents up to and including the nearest git root. If there is
    no git root above cwd, only cwd itself (so a stray ~/.claude never counts)."""
    cwd = cwd.resolve()
    chain = [cwd] + list(cwd.parents)
    for i, d in enumerate(chain):
        if (d / ".git").exists():
            return chain[: i + 1]
    return [cwd]


def detect(env: dict | None = None, cwd: str | Path | None = None, caps: dict | None = None) -> dict:
    env = dict(os.environ) if env is None else dict(env)
    cwd = Path(cwd or os.getcwd())
    caps = caps or load_caps()
    hosts = caps["hosts"]
    det = caps["detection"]
    signals: list[dict] = []
    scores = {h: 0 for h in hosts}

    override = (env.get(det.get("override_env", "COORDN8_HOST")) or "").strip().lower()
    if override:
        if override in hosts:
            signals.append({"kind": "override", "detail": f"{det['override_env']}={override}",
                            "host": override, "status": "confirmed"})
            return _report(override, "override", signals, scores, caps, cwd)
        signals.append({"kind": "override-ignored", "detail": f"unknown host {override!r}",
                        "host": None, "status": "ignored"})

    for sig in det.get("env_signals", []):
        val = env.get(sig["var"])
        if val is None:
            continue
        if sig.get("equals") is not None and val != sig["equals"]:
            continue
        signals.append({"kind": "env", "detail": f"{sig['var']} set", "host": sig["host"],
                        "status": sig["status"], "note": sig.get("note", "")})
        scores[sig["host"]] += 10 if sig["status"] == "confirmed" else 4

    env_hit = max(scores.values()) > 0
    for d in _search_dirs(cwd):
        for fs in det.get("file_signals", []):
            p = d / fs["path"]
            ok = p.is_dir() if fs["kind"] == "dir" else p.is_file()
            if ok:
                signals.append({"kind": "file", "detail": str(p), "host": fs["host"],
                                "status": fs["status"], "note": fs.get("note", "")})
                scores[fs["host"]] += fs.get("weight", 1)

    best = max(hosts, key=lambda h: (scores[h], -hosts.index(h)))
    if scores[best] == 0:
        return _report("generic", "no signal", signals, scores, caps, cwd)
    basis = "environment" if env_hit and scores[best] >= 10 else "project files"
    return _report(best, basis, signals, scores, caps, cwd)


def _report(host: str, basis: str, signals, scores, caps, cwd) -> dict:
    rows = [r for r in caps["rows"] if r["host"] == host]
    srcs = caps.get("sources", {})
    caps_out = {}
    degrade = []
    for r in rows:
        caps_out[r["mechanism"]] = {"status": r["status"], "how": r["how"],
                                    "fallback": r["fallback"],
                                    "source": srcs.get(r["source"], {}).get("url", r["source"]),
                                    "checked": r["checked"]}
        if r["status"] != "confirmed" and r["mechanism"] != "todo_legacy":
            degrade.append({"mechanism": r["mechanism"], "status": r["status"],
                            "do_instead": r["fallback"]})
    spawn = caps_out.get("subagents", {}).get("status")
    return {
        "gate": "host_detect",
        "host": host,
        "basis": basis,
        "confidence": "high" if basis in ("override", "environment") else
                      ("low" if basis == "project files" else "none"),
        "cwd": str(Path(cwd).resolve()),
        "signals": signals,
        "scores": scores,
        "capabilities": caps_out,
        "fleet": ("parallel sub-agents, one context each" if spawn == "confirmed"
                  else BLIND_SEQUENCE),
        "degrade": degrade,
        "network": "never used",
    }


STATUS_MARK = {"confirmed": "confirmed", "assumed": "assumed", "unsupported": "unsupported"}


def matrix_markdown(caps: dict | None = None) -> str:
    """The per-host x per-mechanism table embedded in docs/HOST_INTEGRATION.md."""
    caps = caps or load_caps()
    hosts = caps["hosts"]
    idx = {(r["host"], r["mechanism"]): r for r in caps["rows"]}
    head = "| Mechanism | " + " | ".join(hosts) + " |"
    sep = "|---|" + "---|" * len(hosts)
    lines = [head, sep]
    for m in caps["mechanisms"]:
        cells = []
        for h in hosts:
            r = idx.get((h, m["id"]))
            cells.append(STATUS_MARK.get(r["status"], r["status"]) if r else "MISSING")
        lines.append(f"| {m['label']} (`{m['id']}`) | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cwd", default=None)
    ap.add_argument("--env-json", default=None, help="replace the environment with this JSON object")
    ap.add_argument("--json", action="store_true", help="JSON only")
    ap.add_argument("--matrix", action="store_true",
                    help="print the confirmed/assumed table for docs/HOST_INTEGRATION.md")
    a = ap.parse_args(argv)
    if a.matrix:
        print(matrix_markdown())
        return 0
    env = None
    if a.env_json:
        env = json.loads(Path(a.env_json).read_text(encoding="utf-8"))
        if not isinstance(env, dict):
            print(json.dumps({"gate": "host_detect", "status": "ERROR",
                              "error": "--env-json must hold a JSON object"}))
            return 3
    rep = detect(env, a.cwd)
    print(json.dumps(rep, indent=2))
    if not a.json:
        sys.stderr.write(f"\n[coordn8:host] {rep['host']} (basis: {rep['basis']}, "
                         f"confidence: {rep['confidence']})\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
