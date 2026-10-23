#!/usr/bin/env python3
"""Coordn8 — skill federation and connector suggestions. Zero third-party deps.

At run start Coordn8 lists the other skills installed on this machine and the MCP
servers / claude.ai connectors visible to the session, and suggests which could
serve one of its stages (explain, diagram, design, writing, creativity, video,
review). A suggestion is never a dependency: every stage has a built-in
implementation (listed in the output), so Coordn8 is complete when nothing matches.
A candidate is ADOPTED for a stage only after a probe shows it was used and its
output passed that stage's gates.

    federation.py list      [--claude-home DIR] [--project DIR]
    federation.py match     [--stage S ...] [--claude-home DIR] [--project DIR]
    federation.py probe     --project DIR --stage S --skill NAME --used yes|no
                            [--gates-passed yes|no|unknown | --gate-json FILE]
    federation.py history   --project DIR
    federation.py connectors [--home DIR] [--claude-home DIR] [--project DIR]
                            [--tool-names FILE] [--session-init FILE]

Where it looks (read-only, and nowhere else — a symlink that leaves a root is skipped):
  skills      CLAUDE_HOME/skills/*/SKILL.md, CLAUDE_HOME/plugins/**/skills/*/SKILL.md,
              <project>/.claude/skills/*/SKILL.md
  MCP config  HOME/.claude.json (mcpServers, and projects[<project>].mcpServers),
              <project>/.mcp.json, CLAUDE_HOME/plugins/**/.mcp.json
  connectors  claude.ai connectors are fetched by the host at run time and are not in
              any local file, so they are recognised from the session's tool names
              (--tool-names, one per line) or its MCP server list (--session-init,
              a JSON object with mcp_servers). The name rules are recorded as assumed.

Matching table and stage phrases: data/host_capabilities.json -> federation.
The only file this script ever writes is the probe log
<project>/Coordn8/_build/orchestration/federation.jsonl (a user-owned build folder).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.paths import PKG_ROOT, claude_home, project_build_dir  # noqa: E402

CAPS = PKG_ROOT / "data" / "host_capabilities.json"
MCP_MENU = PKG_ROOT / "data" / "mcp_servers.json"
STAGES = ("explain", "diagram", "design", "writing", "creativity", "video", "review")
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}
MAX_DEPTH = 9
MAX_FILES = 4000


def load_caps() -> dict:
    return json.loads(CAPS.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- reading
class Reader:
    """Every file read goes through here, so 'never outside the roots' is checkable."""

    def __init__(self, roots):
        self.roots = [Path(r).resolve() for r in roots if r and Path(r).exists()]
        self.read: list[str] = []
        self.skipped: list[str] = []

    def inside(self, p: Path) -> bool:
        try:
            rp = p.resolve()
        except OSError:
            return False
        for r in self.roots:
            try:
                rp.relative_to(r)
                return True
            except ValueError:
                continue
        return False

    def text(self, p: Path) -> str | None:
        if not self.inside(p):
            self.skipped.append(str(p))
            return None
        try:
            t = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        self.read.append(str(p.resolve()))
        return t

    def walk(self, top: Path, want):
        """Yield files under `top` for which want(path) is true, without following
        symlinked directories and within depth/file caps."""
        top = Path(top)
        if not top.is_dir() or not self.inside(top):
            return
        base = len(top.parts)
        seen = 0
        for dirpath, dirnames, filenames in os.walk(top, followlinks=False):
            d = Path(dirpath)
            if len(d.parts) - base >= MAX_DEPTH:
                dirnames[:] = []
            dirnames[:] = sorted(n for n in dirnames if n not in SKIP_DIRS)
            for f in sorted(filenames):
                p = d / f
                if want(p):
                    seen += 1
                    if seen > MAX_FILES:
                        return
                    yield p


def parse_frontmatter(text: str) -> dict:
    """Minimal YAML frontmatter: key: value, quoted values, and | / > blocks."""
    out: dict = {}
    if not text.startswith("---"):
        return out
    end = text.find("\n---", 3)
    if end < 0:
        return out
    lines = text[3:end].strip("\n").splitlines()
    i = 0
    while i < len(lines):
        m = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", lines[i])
        i += 1
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if val in ("|", ">", "|-", ">-", "|+", ">+"):
            block = []
            while i < len(lines) and (lines[i].startswith((" ", "\t")) or not lines[i].strip()):
                block.append(lines[i].strip())
                i += 1
            val = ("\n" if val.startswith("|") else " ").join(b for b in block).strip()
        elif len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        out[key] = val
    return out


def _plugin_name(skill_md: Path, plugins_root: Path, reader: Reader) -> str | None:
    """Nearest ancestor (below plugins_root) carrying .claude-plugin/plugin.json -> its
    name; otherwise the directory that holds skills/ (skipping a version-like dir)."""
    skills_dir = skill_md.parent.parent          # .../<plugin>/skills
    d = skills_dir.parent
    pr = plugins_root.resolve()
    while True:
        try:
            d.resolve().relative_to(pr)
        except ValueError:
            break
        pj = d / ".claude-plugin" / "plugin.json"
        if pj.is_file():
            t = reader.text(pj)
            if t:
                try:
                    n = json.loads(t).get("name")
                    if n:
                        return str(n)
                except ValueError:
                    pass
        if d.resolve() == pr:
            break
        d = d.parent
    cand = skills_dir.parent
    if re.match(r"^v?\d+(\.\d+)*", cand.name) and cand.parent.resolve() != pr:
        cand = cand.parent
    return cand.name if cand.resolve() != pr else None


def list_skills(claude_home_dir: Path, project: Path | None = None, reader: Reader | None = None):
    claude_home_dir = Path(claude_home_dir)
    roots = [claude_home_dir] + ([Path(project)] if project else [])
    reader = reader or Reader(roots)
    found: list[dict] = []

    def add(p: Path, origin: str, plugin: str | None):
        t = reader.text(p)
        if t is None:
            return
        fm = parse_frontmatter(t)
        name = fm.get("name") or p.parent.name
        desc = fm.get("description", "")
        if not desc:
            body = t.split("\n---", 2)[-1] if t.startswith("---") else t
            desc = next((ln.strip("# ").strip() for ln in body.splitlines() if ln.strip()), "")
        invoke = f"/{plugin}:{name}" if plugin else f"/{name}"
        found.append({"name": name, "plugin": plugin, "invoke_as": invoke, "origin": origin,
                      "description": desc, "when_to_use": fm.get("when_to_use", ""),
                      "path": str(p)})

    personal = claude_home_dir / "skills"
    if personal.is_dir():
        for d in sorted(personal.iterdir()):
            p = d / "SKILL.md"
            if p.is_file():
                add(p, "personal", None)
    plugins = claude_home_dir / "plugins"
    for p in reader.walk(plugins, lambda q: q.name == "SKILL.md" and q.parent.parent.name == "skills"):
        add(p, "plugin", _plugin_name(p, plugins, reader))
    if project:
        ps = Path(project) / ".claude" / "skills"
        if ps.is_dir():
            for d in sorted(ps.iterdir()):
                p = d / "SKILL.md"
                if p.is_file():
                    add(p, "project", None)

    # The same plugin can sit in a marketplace checkout and in the install cache.
    seen, uniq = set(), []
    for s in found:
        k = (s["invoke_as"], s["description"])
        if k not in seen:
            seen.add(k)
            uniq.append(s)
    return uniq, reader


# --------------------------------------------------------------------------- matching
def _phrase_re(ph: str):
    return re.compile(r"(?<![a-z0-9])" + re.escape(ph.lower()).replace(r"\ ", r"[\s_-]+")
                      + r"(?:s|es)?(?![a-z0-9])")


def score_skill(skill: dict, stage_cfg: dict, name_mult: int = 2):
    name = re.sub(r"[_-]+", " ", skill.get("name", "").lower())
    text = (skill.get("description", "") + " " + skill.get("when_to_use", "")).lower()
    score, hits = 0, []
    for ph, w in stage_cfg["phrases"].items():
        rx = _phrase_re(ph)
        s = 0
        if rx.search(name):
            s += w * name_mult
        if rx.search(text):
            s += w
        if s:
            score += s
            hits.append(ph)
    return score, hits


def match(skills, stages=None, caps=None, history=None):
    caps = caps or load_caps()
    fed = caps["federation"]
    excl = tuple(fed.get("exclude_name_prefixes", []))
    history = history or {}
    out = {}
    for st in (stages or STAGES):
        cfg = fed["stages"][st]
        cands = []
        for sk in skills:
            if sk["name"].lower().startswith(excl):
                continue
            sc, hits = score_skill(sk, cfg, fed.get("name_multiplier", 2))
            if sc >= fed["min_score"]:
                rec = history.get((st, sk["invoke_as"]))
                cands.append({"skill": sk["name"], "invoke_as": sk["invoke_as"],
                              "origin": sk["origin"], "score": sc, "matched": hits,
                              "probe": rec["verdict"] if rec else "not probed"})
        cands.sort(key=lambda c: (-(c["probe"] == "adopt"), c["probe"] == "reject",
                                  -c["score"], c["invoke_as"]))
        out[st] = {"candidates": cands,
                   "builtin": cfg["builtin"],
                   "use": ("built-in (no installed skill matched)" if not cands else
                           "built-in until a probe adopts a candidate"
                           if not any(c["probe"] == "adopt" for c in cands) else
                           "adopted candidate, built-in as fallback")}
    return out


# --------------------------------------------------------------------------- probes
def _log_path(project: Path) -> Path:
    return project_build_dir(project) / "orchestration" / "federation.jsonl"


def read_history(project: Path | None) -> dict:
    hist: dict = {}
    if not project:
        return hist
    p = _log_path(Path(project))
    if not p.is_file():
        return hist
    for ln in p.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        hist[(r.get("stage"), r.get("invoke_as"))] = r     # latest wins
    return hist


def record_probe(project: Path, stage: str, skill: str, used: bool, gates_passed) -> dict:
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}")
    invoke = skill if skill.startswith("/") else f"/{skill}"
    if not used:
        verdict = "reject"
    elif gates_passed is True:
        verdict = "adopt"
    elif gates_passed is False:
        verdict = "reject"
    else:
        verdict = "unproven"
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "stage": stage, "invoke_as": invoke,
           "used": bool(used), "gates_passed": gates_passed, "verdict": verdict}
    p = _log_path(Path(project))
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")
    return rec


def gates_from_json(path: Path):
    """A gate report in Coordn8's emit format: status PASS -> True, FAIL/REVIEW -> False."""
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    st = str(d.get("status", d.get("verdict", ""))).upper()
    if st in ("PASS", "READY", "OK"):
        return True
    if st in ("FAIL", "REVIEW", "NOT READY", "BLOCKED"):
        return False
    return None


# --------------------------------------------------------------------------- connectors
def _servers_from(obj, where: str):
    out = []
    if isinstance(obj, dict) and isinstance(obj.get("mcpServers"), dict):
        for name, cfg in obj["mcpServers"].items():
            out.append({"name": name, "source": where,
                        "kind": "plugin MCP" if "plugin" in where else "local MCP"})
    return out


def connectors(home: Path, claude_home_dir: Path, project: Path | None = None,
               tool_names: Path | None = None, session_init: Path | None = None, caps=None):
    caps = caps or load_caps()
    cs = caps["connector_stages"]
    roots = [home, claude_home_dir] + [p for p in (project, tool_names, session_init) if p]
    reader = Reader(roots)
    servers: list[dict] = []

    cj = Path(home) / ".claude.json"
    if cj.is_file():
        t = reader.text(cj)
        try:
            d = json.loads(t or "{}")
        except ValueError:
            d = {}
        servers += _servers_from(d, "~/.claude.json (user scope)")
        if project and isinstance(d.get("projects"), dict):
            key = str(Path(project).resolve())
            for k, v in d["projects"].items():
                if str(Path(k).expanduser()) in (key, str(project)):
                    servers += _servers_from(v, "~/.claude.json (local scope)")
    if project:
        pm = Path(project) / ".mcp.json"
        if pm.is_file():
            try:
                servers += _servers_from(json.loads(reader.text(pm) or "{}"), ".mcp.json (project scope)")
            except ValueError:
                pass
    for pm in reader.walk(Path(claude_home_dir) / "plugins", lambda q: q.name == ".mcp.json"):
        try:
            servers += _servers_from(json.loads(reader.text(pm) or "{}"), f"plugin {pm.parent.name}")
        except ValueError:
            pass

    prefix = cs.get("claude_ai_tool_prefix", "mcp__claude_ai_")
    sprefix = cs.get("claude_server_prefix", "claude.ai").lower()
    if tool_names and Path(tool_names).is_file():
        seen = set()
        for ln in (reader.text(Path(tool_names)) or "").splitlines():
            ln = ln.strip()
            if ln.startswith(prefix):
                srv = ln[len(prefix):].split("__", 1)[0]
                if srv not in seen:
                    seen.add(srv)
                    servers.append({"name": srv, "source": "session tool names", "kind": "claude.ai connector",
                                    "recognised_by": f"tool prefix {prefix} (assumed)"})
    if session_init and Path(session_init).is_file():
        try:
            d = json.loads(reader.text(Path(session_init)) or "{}")
        except ValueError:
            d = {}
        for s in d.get("mcp_servers", []) if isinstance(d, dict) else []:
            n = str(s.get("name", "")) if isinstance(s, dict) else str(s)
            if n.lower().startswith(sprefix):
                servers.append({"name": n[len(sprefix):].strip(" :_-") or n, "source": "session MCP list",
                                "kind": "claude.ai connector",
                                "recognised_by": f"server name prefix {sprefix!r} (assumed)"})

    # Design commands (/design, /design-sync): built into some Claude Code builds (assumed — not in
    # the commands reference checked), so they are recognised from the session's own command list
    # (the init message's "slash_commands") or from an installed command file of that name.
    design_cmds = cs.get("design_commands", ["design", "design-sync"])
    seen_cmds = set()
    if session_init and Path(session_init).is_file():
        try:
            d0 = json.loads(reader.text(Path(session_init)) or "{}")
        except ValueError:
            d0 = {}
        for c in (d0.get("slash_commands") or []) if isinstance(d0, dict) else []:
            n = str(c).lstrip("/").split(":")[-1]
            if n in design_cmds and n not in seen_cmds:
                seen_cmds.add(n)
                servers.append({"name": "/" + n, "source": "session command list", "kind": "design command",
                                "recognised_by": "init slash_commands", "stages_override": ["design"]})
    for cdir in [Path(claude_home_dir) / "commands"] + ([Path(project) / ".claude" / "commands"] if project else []):
        for n in design_cmds:
            if n not in seen_cmds and (cdir / f"{n}.md").is_file() and reader.text(cdir / f"{n}.md") is not None:
                seen_cmds.add(n)
                servers.append({"name": "/" + n, "source": str(cdir), "kind": "design command",
                                "recognised_by": "installed command file", "stages_override": ["design"]})

    suggestions = []
    for s in servers:
        if s.get("stages_override"):
            suggestions.append({**{k: v for k, v in s.items() if k != "stages_override"},
                                "stages": s["stages_override"], "required": False})
            continue
        low = s["name"].lower().replace("-", "").replace("_", "")
        stages = sorted({st for kw, sts in cs["keywords"].items() if kw.replace("-", "") in low for st in sts})
        suggestions.append({**s, "stages": stages, "required": False})

    covered = {st for s in suggestions for st in s["stages"]}
    offers = []
    try:
        menu = json.loads(MCP_MENU.read_text(encoding="utf-8"))
        for o in menu.get("signin_offers", []):
            offers.append({"id": o["id"], "name": o["name"], "link": o.get("link"),
                           "needs_signin": True, "auto_install": False})
    except (OSError, ValueError, KeyError):
        pass
    by_stage = {st: [f"{s['kind']}: {s['name']}" for s in suggestions if st in s["stages"]]
                for st in STAGES}
    return {
        "gate": "federation.connectors",
        "found": suggestions,
        "by_stage": by_stage,
        "stages_without_a_tool": [st for st in STAGES if st not in covered],
        "offer_last": offers,
        "policy": ("Suggestions only: no stage requires a connector or MCP server, and "
                   "Coordn8 never installs or authorises a claude.ai connector (only the user "
                   "can, at claude.ai; on Team and Enterprise only admins). Choose only tools "
                   "that do not run a non-Anthropic AI model on your content."),
        "files_read": reader.read,
        "skipped_outside_roots": reader.skipped,
    }


# --------------------------------------------------------------------------- CLI
def _yn(v):
    v = (v or "").lower()
    return True if v in ("yes", "y", "true", "1") else False if v in ("no", "n", "false", "0") else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for n in ("list", "match", "connectors"):
        p = sub.add_parser(n)
        p.add_argument("--claude-home", default=None)
        p.add_argument("--project", default=None)
        if n == "match":
            p.add_argument("--stage", action="append", choices=STAGES)
        if n == "connectors":
            p.add_argument("--home", default=None)
            p.add_argument("--tool-names", default=None)
            p.add_argument("--session-init", default=None)
    p = sub.add_parser("probe")
    p.add_argument("--project", required=True)
    p.add_argument("--stage", required=True, choices=STAGES)
    p.add_argument("--skill", required=True, help="installed name, e.g. /plugin:skill")
    p.add_argument("--used", required=True)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--gates-passed", default="unknown")
    g.add_argument("--gate-json", default=None)
    p = sub.add_parser("history")
    p.add_argument("--project", required=True)
    a = ap.parse_args(argv)

    ch = Path(a.claude_home).expanduser() if getattr(a, "claude_home", None) else claude_home()
    proj = Path(a.project).expanduser() if getattr(a, "project", None) else None

    if a.cmd == "list":
        skills, rd = list_skills(ch, proj)
        print(json.dumps({"gate": "federation.list", "skills": skills, "count": len(skills),
                          "files_read": rd.read, "skipped_outside_roots": rd.skipped}, indent=2))
    elif a.cmd == "match":
        skills, rd = list_skills(ch, proj)
        res = match(skills, a.stage, history=read_history(proj))
        print(json.dumps({"gate": "federation.match", "skills_seen": len(skills), "stages": res,
                          "complete_without_federation": True,
                          "files_read": rd.read, "skipped_outside_roots": rd.skipped}, indent=2))
    elif a.cmd == "probe":
        gp = gates_from_json(Path(a.gate_json)) if a.gate_json else _yn(a.gates_passed)
        rec = record_probe(proj, a.stage, a.skill, bool(_yn(a.used)), gp)
        print(json.dumps({"gate": "federation.probe", "record": rec,
                          "log": str(_log_path(proj))}, indent=2))
    elif a.cmd == "history":
        h = read_history(proj)
        print(json.dumps({"gate": "federation.history",
                          "records": [v for v in h.values()]}, indent=2))
    elif a.cmd == "connectors":
        home = Path(a.home).expanduser() if a.home else Path.home()
        res = connectors(home, ch, proj, Path(a.tool_names) if a.tool_names else None,
                         Path(a.session_init) if a.session_init else None)
        print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
