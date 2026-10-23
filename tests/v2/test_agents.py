#!/usr/bin/env python3
"""Suite "agents": the dedicated Coordn8 subagents in agents/ (requirement F2, plus the
agent-side parts of H2 and E6).

Proves, from the files themselves:
  * every SPEC section-6 role has a file, and every file has documented, valid frontmatter;
  * the Limits block opens every body, verbatim;
  * least privilege: reader = Read/Glob/Grep, frame reviewer = Read only, claims auditor =
    Read/Grep/Glob; an agent that documents a script command has Bash, one that does not, does not;
  * blindness: the frame reviewer never asks for builder claims; the claims auditor reads claims
    LAST; the idea critic never sees the generator's preference;
  * generator and critic are different agents with different tools, the critic carries the three
    rejection tests, and the documented generator/critic file shapes merge into a divergence.json
    that the real divergence gate passes (and a reject-all round is refused, not passed);
  * the frame reviewer's answer schema is exactly the Stage V schema, and a contradicting verdict
    blocks the real stage_v.py check;
  * every referenced package path exists or is a documented in-flight file (reported);
  * data/agents.json matches the files exactly; no reference-project name leaks;
  * every validator rejects a seeded defect.
Hermetic: temp dirs only, sandboxed homes, no network, never calls the claude CLI.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # leave nothing on disk (no __pycache__ from the imports below)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import PY, ROOT, Suite, run, sandbox_env, tmpdir  # noqa: E402

AGENTS = ROOT / "agents"
REGISTRY = ROOT / "data" / "agents.json"

LIMITS = ("> **Limits:** never send text, audio or code to any non-Anthropic hosted AI service. "
          "Local open-source voice models installed by Coordn8 are allowed. Read-only on anything "
          "outside the build folder unless your role says otherwise. Never name any external project "
          "in output that ships.")

SPEC_ROLES = ["reader", "cartographer", "audience", "idea-generator", "idea-critic",
              "palette-director", "diagram-architect", "storyboarder", "frame-reviewer",
              "writing-critic", "claims-auditor", "failure-analyst", "memory-curator", "voice-caster"]
DESIGN_ROLES = ["audience", "idea-generator", "idea-critic", "palette-director", "diagram-architect",
                "storyboarder", "voice-caster"]
BLIND_ROLES = ["frame-reviewer", "claims-auditor", "idea-critic"]

# Documented frontmatter (CLAUDE_CODE_DOCS_FACTS.md "Agents").
KNOWN_KEYS = {"name", "description", "tools", "disallowedTools", "model", "color", "effort",
              "maxTurns", "skills", "memory", "background", "isolation"}
PLUGIN_IGNORED = {"permissionMode", "hooks", "mcpServers", "initialPrompt"}
MODELS = {"sonnet", "opus", "haiku", "fable", "inherit"}
COLORS = {"red", "blue", "green", "yellow", "purple", "orange", "pink", "cyan"}
TOOLS = {"Read", "Write", "Edit", "Bash", "Glob", "Grep", "NotebookEdit", "WebFetch", "WebSearch",
         "Agent", "Skill", "Monitor", "TaskStop", "TaskCreate", "TaskGet", "TaskList", "TaskUpdate",
         "EnterPlanMode", "ExitPlanMode", "EnterWorktree"}
DESC_MAX = 1536

# Files being written in parallel by other streams (brief W5-S2), plus the W5-S4 taxonomy.
IN_FLIGHT = {"scripts/gates/check_language.py", "scripts/gates/check_terms.py",
             "scripts/gates/check_truth.py", "scripts/memory/curate.py", "scripts/memory/recall.py",
             "scripts/index/query.py", "scripts/index/build_index.py", "scripts/reflect.py",
             "scripts/render/contact_sheet.py", "data/cause_taxonomy.json"}

REF = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/([A-Za-z0-9_./-]+)")
CMD = re.compile(r'^\s*(python3|node)\s+"?\$\{CLAUDE_PLUGIN_ROOT\}', re.M)
# Artefacts that state what the builder intended or claims (a frame reviewer must never be given them).
CLAIM_ARTEFACTS = ["verification.json", "claims.json", ".claims.md", "storyboard.json",
                   "page_plan.json", "requirements.md", "decisions.md", "orchestration/"]
HOSTED = ["openai", "chatgpt", "gemini", "elevenlabs", "edge-tts", "mistral", "cohere", "perplexity",
          "deepseek", "replicate", "openrouter", "groq", "azure speech", "polly"]
TESTS3 = ["user-could-think-of-it", "known-default", "surprising-not-useful"]


# --------------------------------------------------------------------------- parsing
def split(text: str):
    """-> (frontmatter_text, body) or (None, text)."""
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return None, text
    return m.group(1), text[m.end():]


def parse_frontmatter(fm: str) -> dict:
    try:
        import yaml  # optional; a strict mini-parser is used when absent
        data = yaml.safe_load(fm)
        if not isinstance(data, dict):
            raise ValueError("frontmatter is not a mapping")
        return data
    except ImportError:
        pass
    out = {}
    for line in fm.splitlines():
        if not line.strip():
            continue
        k, sep, v = line.partition(":")
        if not sep:
            raise ValueError(f"not key: value: {line!r}")
        v = v.strip()
        if v.startswith('"'):
            if not v.endswith('"') or len(v) < 2:
                raise ValueError(f"unterminated quote: {line!r}")
            v = v[1:-1]
        elif ": " in v:
            raise ValueError(f"unquoted ': ' in value: {line!r}")
        out[k.strip()] = v
    return out


def tools_of(fm: dict) -> list:
    t = fm.get("tools")
    if isinstance(t, list):
        return [str(x).strip() for x in t]
    return [x.strip() for x in str(t or "").split(",") if x.strip()]


def sections(body: str) -> dict:
    out, cur = {}, None
    for line in body.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            cur = m.group(1)
            out[cur] = []
        elif cur:
            out[cur].append(line)
    return {k: "\n".join(v) for k, v in out.items()}


def section(body: str, prefix: str) -> str:
    return "\n".join(v for k, v in sections(body).items() if k.lower().startswith(prefix.lower()))


def load_agent(path: Path):
    text = path.read_text(encoding="utf-8")
    fm_text, body = split(text)
    if fm_text is None:
        raise ValueError("no YAML frontmatter")
    return parse_frontmatter(fm_text), body, text


# --------------------------------------------------------------------------- validators
def validate_agent(path: Path) -> list:
    """Problems with one agent file (empty = valid). Used on the real files and on seeded defects."""
    try:
        fm, body, text = load_agent(path)
    except Exception as e:  # noqa: BLE001
        return [f"unparseable: {e}"]
    p = []
    name = str(fm.get("name") or "")
    if not name:
        p.append("missing name")
    if ":" in name:
        p.append("':' in name")
    if name != path.stem:
        p.append(f"name {name!r} != file stem {path.stem!r}")
    if not name.startswith("coordn8-"):
        p.append("name must start with coordn8-")
    desc = str(fm.get("description") or "")
    if not desc:
        p.append("missing description")
    if len(desc) > DESC_MAX:
        p.append(f"description {len(desc)} chars > {DESC_MAX}")
    if "Use " not in desc or "Triggers:" not in desc:
        p.append("description lacks 'Use ...' and 'Triggers:' text")
    raw_tools = fm.get("tools")
    if raw_tools in (None, "", []):
        p.append("tools missing (would inherit every tool)")
    if "Bash(" in json.dumps(raw_tools):
        p.append("Bash(pattern) is not supported in agent tools")
    tl = tools_of(fm)
    unknown = [t for t in tl if t not in TOOLS]
    if unknown:
        p.append(f"unknown tools {unknown}")
    model = str(fm.get("model") or "")
    if model not in MODELS and not re.match(r"^claude-[a-z0-9-]+$", model):
        p.append(f"model {model!r} not documented")
    if str(fm.get("color") or "") not in COLORS:
        p.append(f"color {fm.get('color')!r} not documented")
    bad = [k for k in fm if k not in KNOWN_KEYS]
    if bad:
        p.append(f"undocumented/ignored keys {bad}")
    first = next((ln for ln in body.splitlines() if ln.strip()), "")
    if first != LIMITS:
        p.append("Limits block is not verbatim at the top of the body")
    for sec in ("Role", "Inputs", "Output", "Stop when", "Never"):
        if not section(body, sec).strip():
            p.append(f"missing section '## {sec}'")
    has_cmd = bool(CMD.search(body))
    if has_cmd and "Bash" not in tl:
        p.append("documents script commands but has no Bash tool")
    if "Bash" in tl and not has_cmd:
        p.append("has Bash but documents no script command (least privilege)")
    if REF.search(body):
        if "pkg_root" not in body:
            p.append("uses ${CLAUDE_PLUGIN_ROOT} without the pkg_root fallback")
        if "Bash" in tl and "coordn8 doctor --json" not in body:
            p.append("Bash agent does not document `coordn8 doctor --json` -> pkg_root")
    low = body.lower()
    named = [h for h in HOSTED if h in low]
    if named:
        p.append(f"names hosted AI services {named}")
    role = name[len("coordn8-"):]
    if role == "reader" and not set(tl) <= {"Read", "Glob", "Grep"}:
        p.append(f"reader tools {tl} exceed Read/Glob/Grep")
    if role == "frame-reviewer":
        if not set(tl) <= {"Read"}:
            p.append(f"frame-reviewer tools {tl} exceed Read")
        inputs = section(body, "Inputs")
        leaked = [a for a in CLAIM_ARTEFACTS if a in inputs]
        if leaked:
            p.append(f"frame-reviewer inputs ask for builder claims {leaked}")
        if not re.search(r"never read, and never ask for", body) or "Blindness" not in body:
            p.append("frame-reviewer body lacks the blindness rule")
    if role == "claims-auditor":
        if not set(tl) <= {"Read", "Grep", "Glob"}:
            p.append(f"claims-auditor tools {tl} exceed Read/Grep/Glob")
        a, b = body.find("Phase A"), body.find("Phase B")
        if a < 0 or b < 0 or a > b:
            p.append("claims-auditor lacks Phase A (blind) before Phase B (claims last)")
        if "do not read the builder's claim lists" not in body:
            p.append("claims-auditor Phase A does not forbid reading claim lists")
    if role == "idea-critic":
        if "Bash" in tl:
            p.append("critic must not have Bash")
        rej = section(body, "The three rejection tests")
        missing = [t for t in TESTS3 if t not in rej]
        if missing:
            p.append(f"critic lacks rejection tests {missing}")
        if "Never lower the bar to reach a count" not in body:
            p.append("critic lacks 'never lower the bar'")
        if "blind to the generator's preference" not in body:
            p.append("critic does not state it is blind to the generator's preference")
    if role == "idea-generator":
        if "seed.py\" draw" not in body:
            p.append("generator does not draw with seed.py")
        out = section(body, "Output")
        block = out.split("```")[1] if out.count("```") >= 2 else ""
        bad_fields = [f for f in ("chosen", "preferred", "rank", "score", "recommended")
                      if re.search(r'"%s"\s*:' % f, block)]
        if bad_fields:
            p.append(f"generator output carries preference fields {bad_fields}")
        if "Never rank, score, recommend or choose" not in body:
            p.append("generator does not forbid ranking")
    if role in DESIGN_ROLES:
        if "Freedom (H1)" not in body or not ("never a fence" in body or "not in a catalog" in body):
            p.append("design agent lacks the freedom doctrine (catalogs are menus)")
        if "invent" not in low:
            p.append("design agent does not allow inventing")
    return p


def registry_problems(reg: dict, agent_dir: Path) -> list:
    """data/agents.json must match the agent files exactly."""
    p = []
    files = {f.stem: f for f in agent_dir.glob("coordn8-*.md")}
    entries = {a.get("name"): a for a in reg.get("agents") or []}
    if set(files) != set(entries):
        p.append(f"names differ: files-only {sorted(set(files) - set(entries))}, "
                 f"registry-only {sorted(set(entries) - set(files))}")
    if reg.get("limits") != LIMITS[2:]:
        p.append("registry limits text differs from the Limits block")
    for name in sorted(set(files) & set(entries)):
        fm, body, text = load_agent(files[name])
        e = entries[name]
        if e.get("file") != f"agents/{files[name].name}":
            p.append(f"{name}: file {e.get('file')!r}")
        if e.get("tools") != tools_of(fm):
            p.append(f"{name}: tools {e.get('tools')} != {tools_of(fm)}")
        for k in ("model", "color"):
            if e.get(k) != fm.get(k):
                p.append(f"{name}: {k} {e.get(k)!r} != {fm.get(k)!r}")
        if sorted(e.get("references") or []) != sorted(set(REF.findall(text))):
            p.append(f"{name}: references drift")
        blind_body = "blind" in body.lower()
        if bool(e.get("blind")) and not blind_body:
            p.append(f"{name}: registry says blind but the body states no blindness rule")
        role = name[len("coordn8-"):]
        if role in BLIND_ROLES and not e.get("blind"):
            p.append(f"{name}: must be blind:true")
        if role in SPEC_ROLES and not e.get("spec_row"):
            p.append(f"{name}: SPEC role not marked spec_row")
        if role not in SPEC_ROLES and not e.get("why_added"):
            p.append(f"{name}: extra agent without why_added")
        for k in ("role", "inputs", "outputs", "phase"):
            if not e.get(k):
                p.append(f"{name}: missing {k}")
    return p


def reference_status(agent_dir: Path, root: Path) -> tuple:
    """-> (existing, in_flight, missing) package paths referenced by the agent bodies."""
    refs = set()
    for f in agent_dir.glob("coordn8-*.md"):
        refs |= set(REF.findall(f.read_text(encoding="utf-8")))
    exist = sorted(r for r in refs if (root / r).exists())
    flight = sorted(r for r in refs if not (root / r).exists() and r in IN_FLIGHT)
    missing = sorted(r for r in refs if not (root / r).exists() and r not in IN_FLIGHT)
    return exist, flight, missing


def seeded(tmp: Path, name: str, mutate) -> Path:
    """Copy one real agent file into tmp (same file name) and apply a defect."""
    src = AGENTS / f"{name}.md"
    dst = tmp / f"{name}.md"
    dst.write_text(mutate(src.read_text(encoding="utf-8")), encoding="utf-8")
    return dst


# --------------------------------------------------------------------------- suite
def main() -> int:
    s = Suite("agents")
    files = sorted(AGENTS.glob("coordn8-*.md")) if AGENTS.is_dir() else []
    stems = {f.stem for f in files}

    missing_roles = [r for r in SPEC_ROLES if f"coordn8-{r}" not in stems]
    s.check("every SPEC section-6 role has an agent file", not missing_roles, f"missing {missing_roles}")
    s.check("at least 8 dedicated subagents ship (F2)", len(files) >= 8, f"{len(files)} files")

    parsed, problems = {}, {}
    for f in files:
        problems[f.stem] = validate_agent(f)
        try:
            parsed[f.stem] = load_agent(f)
        except Exception:  # noqa: BLE001
            pass
    s.check("every agent file parses as YAML frontmatter + body", len(parsed) == len(files) and files,
            [k for k in stems if k not in parsed])
    bad = {k: v for k, v in problems.items() if v}
    s.check("every agent file passes the full validator (frontmatter, limits, sections, privilege, blindness)",
            files and not bad, json.dumps(bad)[:400])

    def every(pred):
        return bool(parsed) and all(pred(*parsed[k]) for k in parsed)

    s.check("every frontmatter has name, description, tools, model (documented values)",
            every(lambda fm, b, t: fm.get("name") and fm.get("description") and fm.get("tools")
                  and (fm.get("model") in MODELS)))
    s.check("no agent lists Bash(pattern) in tools", every(lambda fm, b, t: "Bash(" not in json.dumps(fm.get("tools"))))
    s.check("no agent name contains ':'", every(lambda fm, b, t: ":" not in str(fm.get("name"))))
    s.check("no agent uses keys plugin agents ignore (permissionMode, hooks, mcpServers, initialPrompt)",
            every(lambda fm, b, t: not (set(fm) & PLUGIN_IGNORED)))
    longest = max((len(str(p[0].get("description") or "")) for p in parsed.values()), default=0)
    s.check("every description is <= 1,536 chars", parsed and longest <= DESC_MAX, f"longest {longest}")
    s.check("every description says when to use it (Use ... + Triggers:)",
            every(lambda fm, b, t: "Use " in fm["description"] and "Triggers:" in fm["description"]))
    s.check("the Limits block is verbatim as the first line of every agent body",
            every(lambda fm, b, t: next((ln for ln in b.splitlines() if ln.strip()), "") == LIMITS))

    def tools(name):
        return set(tools_of(parsed[name][0])) if name in parsed else {"<missing>"}

    s.check("reader tools are a subset of Read, Glob, Grep", tools("coordn8-reader") <= {"Read", "Glob", "Grep"},
            tools("coordn8-reader"))
    s.check("frame-reviewer tools are a subset of Read (cannot Write, Edit or Bash)",
            tools("coordn8-frame-reviewer") <= {"Read"}
            and not tools("coordn8-frame-reviewer") & {"Write", "Edit", "Bash", "NotebookEdit"},
            tools("coordn8-frame-reviewer"))
    s.check("claims-auditor tools are a subset of Read, Grep, Glob",
            tools("coordn8-claims-auditor") <= {"Read", "Grep", "Glob"}, tools("coordn8-claims-auditor"))
    if "coordn8-frame-reviewer" in parsed:
        fr_body = parsed["coordn8-frame-reviewer"][1]
        fr_inputs = section(fr_body, "Inputs")
        s.check("frame-reviewer inputs never ask for builder claims or plans",
                not [a for a in CLAIM_ARTEFACTS if a in fr_inputs], fr_inputs[:300])
        s.check("frame-reviewer body forbids reading builder claims (blind)",
                "never read, and never ask for" in fr_body and "builder" in fr_body)
    else:
        s.check("frame-reviewer inputs never ask for builder claims or plans", False, "file missing")
        s.check("frame-reviewer body forbids reading builder claims (blind)", False, "file missing")
    ca_body = parsed.get("coordn8-claims-auditor", ({}, "", ""))[1]
    s.check("claims-auditor inspects artefacts blind (Phase A) before reading claims (Phase B, last)",
            0 <= ca_body.find("Phase A") < ca_body.find("Phase B")
            and "do not read the builder's claim lists" in ca_body)

    gen, cri = "coordn8-idea-generator", "coordn8-idea-critic"
    s.check("idea generator and idea critic are different files with different tool sets",
            gen in parsed and cri in parsed and tools(gen) != tools(cri), (tools(gen), tools(cri)))
    cri_body = parsed.get(cri, ({}, "", ""))[1]
    rej = section(cri_body, "The three rejection tests")
    s.check("idea critic body contains the three rejection tests",
            all(t in rej for t in TESTS3)
            and "Could the user have come up with this alone" in rej
            and "known default" in rej and "surprising but fails" in rej, rej[:200])
    s.check("idea critic never lowers the bar and never sees the generator's preference",
            "Never lower the bar to reach a count" in cri_body
            and "blind to the generator's preference" in cri_body and "contaminated" in cri_body)
    gen_body = parsed.get(gen, ({}, "", ""))[1]
    s.check("idea generator uses seed.py draws (real randomness) and never ranks",
            'seed.py" draw' in gen_body and 'seed.py" replay' in gen_body
            and "Never rank, score, recommend or choose" in gen_body)
    s.check("freedom doctrine (catalogs are menus, inventing allowed) is stated in every design agent",
            all(f"coordn8-{r}" in parsed and "Freedom (H1)" in parsed[f"coordn8-{r}"][1]
                and "invent" in parsed[f"coordn8-{r}"][1].lower() for r in DESIGN_ROLES))
    s.check("memory-curator uses a cheaper model (haiku); writing-critic uses sonnet (SPEC section 6)",
            parsed.get("coordn8-memory-curator", ({},))[0].get("model") == "haiku"
            and parsed.get("coordn8-writing-critic", ({},))[0].get("model") == "sonnet")
    s.check("agents that write user memory or append-only logs have no Write/Edit tool",
            not tools("coordn8-memory-curator") & {"Write", "Edit"}
            and not tools("coordn8-failure-analyst") & {"Write", "Edit"})

    exist, flight, missing = reference_status(AGENTS, ROOT)
    s.check("every referenced package path exists or is a documented in-flight file",
            exist and not missing,
            f"missing {missing}; in-flight (not yet in package) {flight}; existing {len(exist)}")
    print(f"       references: {len(exist)} present, in-flight not yet present: {flight or 'none'}")

    # every referenced script that exists answers --help (the documented CLI is real)
    helpless = []
    with tmpdir() as td:
        env = sandbox_env(Path(td))
        for r in exist:
            if r.endswith(".py") and r.startswith("scripts/"):
                cp = run(r, ["--help"], env=env, timeout=60, cwd=td)
                if cp.returncode not in (0, 2) or not (cp.stdout + cp.stderr).strip():
                    helpless.append(f"{r}:{cp.returncode}")
            elif r.endswith("_cli.js") and shutil.which("node"):
                cp = subprocess.run(["node", str(ROOT / r), "--help"], capture_output=True, text=True, timeout=60)
                if "usage" not in (cp.stdout + cp.stderr).lower():
                    helpless.append(f"{r}:{cp.returncode}")
    s.check("every referenced script that exists answers --help", not helpless, helpless)

    # registry
    try:
        reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        reg = {}
        s.check("data/agents.json parses", False, e)
    else:
        s.check("data/agents.json parses", reg.get("schema") == 1 and reg.get("agents"))
    rp = registry_problems(reg, AGENTS)
    s.check("data/agents.json matches the agent files exactly", not rp, rp)
    s.check("data/agents.json in-flight list equals the documented in-flight list",
            set(reg.get("in_flight_references") or []) == IN_FLIGHT)

    # name leak (reuse the shipped leak scanner on what this stream owns)
    try:
        import test_name_leak as nl
        with tmpdir() as td:
            t = Path(td) / "pkg"
            shutil.copytree(AGENTS, t / "agents")
            (t / "data").mkdir(parents=True)
            shutil.copy2(REGISTRY, t / "data" / "agents.json")
            shutil.copy2(Path(__file__), t / "test_agents.py")
            hits = nl.scan(t)
        s.check("no reference-project name in agents/, data/agents.json or this test", not hits, hits[:5])
    except Exception as e:  # noqa: BLE001
        s.check("no reference-project name in agents/, data/agents.json or this test", False, f"scan error {e}")

    # ---------------------------------------------------------------- H2 end to end on the real gate
    with tmpdir() as td:
        tmp = Path(td)
        env = sandbox_env(tmp)
        build = tmp / "repo" / "Coordn8" / "_build"
        (build / "divergence").mkdir(parents=True)
        cp = run("scripts/design/seed.py", ["draw", "--problem", "opening", "--n", "3", "--project", "demo-agents",
                                            "--build", "2", "--json"], env=env, cwd=td, timeout=60)
        try:
            draw = json.loads(cp.stdout)
        except Exception:  # noqa: BLE001
            draw = {}
        cp2 = run("scripts/design/seed.py", ["draw", "--problem", "opening", "--n", "3", "--project", "other-proj",
                                             "--build", "2", "--json"], env=env, cwd=td, timeout=60)
        try:
            draw2 = json.loads(cp2.stdout)
        except Exception:  # noqa: BLE001
            draw2 = {}
        s.check("generator's documented seed.py command yields recorded draws; different projects draw differently",
                isinstance(draw.get("seed"), int) and draw.get("draws") and len(draw.get("options") or []) >= 3
                and draw.get("seed") != draw2.get("seed"), cp.stderr[-300:])
        ideas = ["Open on a single delayed parcel whose timer keeps climbing while every box around it stays green",
                 "Start from the silence after the alarm and rewind the night hop by hop until the cause appears",
                 "Show the system as a kitchen ticket rail where each order slides right only when its cook calls"]
        opts = [{"id": f"o{i + 1}", "idea": ideas[i], "technique": o["technique"], "stimulus": o.get("stimulus")}
                for i, o in enumerate((draw.get("options") or [])[:3])]
        options_file = {"schema": 1, "kind": "pivotal-scene", "subject": "opening", "what": "opening scene",
                        "problem_type": "opening", "generator_agent": gen, "seed": draw.get("seed"),
                        "catalog_hash": draw.get("catalog_hash"), "draws": draw.get("draws"),
                        "techniques": [o["technique"] for o in opts], "options": opts}
        critic_file = {"schema": 1, "critic_agent": cri,
                       "critic": [{"id": "o1", "verdict": "keep", "test": None,
                                   "why": "ties the hook to the one stuck item the audience must learn to spot"},
                                  {"id": "o2", "verdict": "reject", "test": "surprising-not-useful",
                                   "why": "rewinding is striking but delays the answer the viewer came for"},
                                  {"id": "o3", "verdict": "reject", "test": "known-default",
                                   "why": "a left to right rail is the usual pipeline picture with new paint"}],
                       "chosen": "o1", "why": "it makes the stuck item visible in the first seconds",
                       "needs_round": False}
        (build / "divergence" / "opening.options.json").write_text(json.dumps(options_file), encoding="utf-8")
        cp = run("scripts/design/seed.py", ["replay", str(build / "divergence" / "opening.options.json")],
                 env=env, cwd=td, timeout=60)
        s.check("generator's documented replay command confirms the recorded draws are real", cp.returncode == 0,
                (cp.stdout + cp.stderr)[-300:])

        def merged(opt, crit):
            d = {k: v for k, v in opt.items() if k != "schema"}
            d.update({"critic_agent": crit["critic_agent"], "critic": crit["critic"],
                      "chosen": crit["chosen"], "why": crit["why"]})
            return d

        def gate(decisions):
            doc = {"schema": 1, "decisions": decisions}
            (build / "divergence.json").write_text(json.dumps(doc), encoding="utf-8")
            r = run("scripts/design/divergence.py", ["check", str(build / "divergence.json"),
                                                     "--require", "pivotal-scene:opening", "--json"],
                    env=env, cwd=td, timeout=60)
            try:
                return r.returncode, json.loads(r.stdout)
            except Exception:  # noqa: BLE001
                return r.returncode, {"raw": r.stdout[-300:] + r.stderr[-300:]}

        look = dict(merged(options_file, critic_file), kind="look", subject=None, what="the look")
        spine = dict(merged(options_file, critic_file), kind="narrative-spine", subject=None, what="narrative spine")
        good = [merged(options_file, critic_file), look, spine]
        rc, out = gate(good)
        codes = [x.get("code") for x in out.get("findings", []) if x.get("blocking")]
        s.check("documented generator + critic files merge into a divergence.json the real gate passes",
                rc == 0 and not codes, f"rc={rc} blocking={codes} {str(out)[:300]}")
        all_rej = dict(critic_file, chosen=None, why="no survivor", needs_round=True,
                       critic=[dict(c, verdict="reject", test=c["test"] or "user-could-think-of-it")
                               for c in critic_file["critic"]])
        rc2, out2 = gate([merged(options_file, all_rej), look, spine])
        s.check("NEGATIVE: a critic that rejects every option forces a new round (gate refuses, never passes)",
                rc2 != 0 and (out2.get("needs_round") or any(
                    x.get("code") == "no-survivor" for x in out2.get("findings", []))), str(out2)[:300])
        same = dict(critic_file, critic_agent=gen)
        rc3, out3 = gate([merged(options_file, same), look, spine])
        s.check("NEGATIVE: the gate refuses a decision where the critic is the generator", rc3 != 0, str(out3)[:300])

    # ---------------------------------------------------------------- E6: frame reviewer <-> Stage V
    sys.path.insert(0, str(ROOT / "scripts" / "gates"))
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import stage_v  # noqa: E402
        schema_keys = set(stage_v.SCHEMA)
    except Exception as e:  # noqa: BLE001
        schema_keys = set()
        print(f"       stage_v import failed: {e}")
    fr_text = parsed.get("coordn8-frame-reviewer", ({}, "", ""))[2]
    out_sec = section(parsed.get("coordn8-frame-reviewer", ({}, "", ""))[1], "Output")
    block = out_sec.split("```")[1] if out_sec.count("```") >= 2 else ""
    keys_doc = set(re.findall(r'"([a-z0-9_]+)":', block)) - {"reviewer", "verdicts"}
    s.check("frame-reviewer answer schema is exactly the Stage V schema", schema_keys and keys_doc == schema_keys,
            f"doc-only {sorted(keys_doc - schema_keys)} schema-only {sorted(schema_keys - keys_doc)}")
    s.check("frame-reviewer refuses stale artefacts (freshness_problems) before reviewing",
            "freshness_problems" in fr_text and "stale" in fr_text)
    with tmpdir() as td:
        tmp = Path(td)
        base = {k: "false" for k in schema_keys}
        base.update({"frame": "f1.png", "sentence": "The cache fills as the request passes.",
                     "shows_what_is_said": "match", "well_made_or_low_effort": "well-made: one focal point",
                     "what_used_to_be_here": "same"})
        good_v = {"reviewer": "coordn8-frame-reviewer", "verdicts": [base]}
        bad_v = {"reviewer": "coordn8-frame-reviewer",
                 "verdicts": [base, dict(base, frame="f2.png", shows_what_is_said="contradicts")]}
        (tmp / "good.json").write_text(json.dumps(good_v), encoding="utf-8")
        (tmp / "bad.json").write_text(json.dumps(bad_v), encoding="utf-8")
        g = run("scripts/gates/stage_v.py", ["check", str(tmp / "good.json")], env=sandbox_env(tmp), cwd=td)
        b = run("scripts/gates/stage_v.py", ["check", str(tmp / "bad.json")], env=sandbox_env(tmp), cwd=td)
        s.check("a verdict file in the frame-reviewer's documented shape passes stage_v.py check",
                g.returncode == 0, (g.stdout + g.stderr)[-300:])
        s.check("NEGATIVE: a seeded contradicting frame in that shape blocks stage_v.py check",
                b.returncode != 0 and "BLOCKED" in b.stdout, (b.stdout + b.stderr)[-300:])

    # ---------------------------------------------------------------- seeded defects are caught
    with tmpdir() as td:
        tmp = Path(td)
        cases = {
            "frame-reviewer given Write is caught": ("coordn8-frame-reviewer",
                lambda t: t.replace("tools: Read\n", "tools: Read, Write\n", 1), "exceed Read"),
            "Bash(pattern) in tools is caught": ("coordn8-idea-generator",
                lambda t: t.replace("tools: Read, Write, Bash\n", "tools: Read, Write, Bash(seed.py)\n", 1),
                "Bash(pattern)"),
            "a one-word change to the Limits block is caught": ("coordn8-reader",
                lambda t: t.replace("never send text, audio", "never send text or audio", 1), "Limits block"),
            "a name containing ':' is caught": ("coordn8-storyboarder",
                lambda t: t.replace("name: coordn8-storyboarder", "name: \"coordn8:storyboarder\"", 1), "':' in name"),
            "a description over 1,536 chars is caught": ("coordn8-cartographer",
                lambda t: t.replace('description: "', 'description: "' + "x" * 1600, 1), "chars >"),
            "a critic missing one rejection test is caught": ("coordn8-idea-critic",
                lambda t: t.replace("**known-default**", "**the-usual**", 1), "rejection tests"),
            "a frame-reviewer asking for verification.json is caught": ("coordn8-frame-reviewer",
                lambda t: t.replace("## Inputs (only these)\n",
                                    "## Inputs (only these)\n- `<repo>/Coordn8/_build/verification.json`\n", 1),
                "builder claims"),
            "a generator output with a preferred field is caught": ("coordn8-idea-generator",
                lambda t: t.replace('"generator_agent":"coordn8-idea-generator",',
                                    '"generator_agent":"coordn8-idea-generator","preferred":"o2",', 1),
                "preference fields"),
            "a plugin-ignored key (permissionMode) is caught": ("coordn8-reader",
                lambda t: t.replace("color: blue\n", "color: blue\npermissionMode: plan\n", 1), "keys"),
            "a design agent without the freedom doctrine is caught": ("coordn8-palette-director",
                lambda t: t.replace("Freedom (H1)", "Catalog rule", 1), "freedom doctrine"),
            "a claims auditor reading claims first is caught": ("coordn8-claims-auditor",
                lambda t: t.replace("**Phase A — blind.**", "**Phase B — claims first.** Read the claim list.\n\n**Phase A — blind.**", 1),
                "Phase A"),
            "an undocumented script command without Bash is caught": ("coordn8-storyboarder",
                lambda t: t.replace("## Stop when", "```\npython3 \"${CLAUDE_PLUGIN_ROOT}/scripts/gates/visual_density.py\" x\n```\n## Stop when", 1),
                "no Bash tool"),
        }
        caught = {}
        for label, (name, mut, needle) in cases.items():
            d = tmp / label.replace(" ", "_").replace("'", "").replace(",", "")
            d.mkdir()
            probs = validate_agent(seeded(d, name, mut))
            caught[label] = any(needle in x for x in probs)
            s.check(f"NEGATIVE: {label}", caught[label], probs)
        # registry drift
        reg2 = json.loads(json.dumps(reg)) if reg else {"agents": []}
        if reg2.get("agents"):
            reg2["agents"][0]["tools"] = reg2["agents"][0]["tools"] + ["Edit"]
        s.check("NEGATIVE: data/agents.json drift (an extra tool) is caught",
                any("tools" in x for x in registry_problems(reg2, AGENTS)))
        # unknown reference
        d = tmp / "badref"
        d.mkdir()
        seeded(d, "coordn8-palette-director",
               lambda t: t.replace("scripts/gates/check_palette.py", "scripts/gates/check_nonexistent.py", 1))
        _, _, miss = reference_status(d, ROOT)
        s.check("NEGATIVE: a reference to a script that neither exists nor is in flight is caught",
                miss == ["scripts/gates/check_nonexistent.py"], miss)

    return s.finish()


if __name__ == "__main__":
    sys.exit(main())
