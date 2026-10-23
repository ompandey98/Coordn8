#!/usr/bin/env python3
"""Coordn8 as a Claude Code plugin: manifests, layout, launcher, generator, install simulation.

Proves:
  * .claude-plugin/plugin.json and marketplace.json satisfy the documented rules, and the install
    lines `/plugin marketplace add ompandey98/coordn8` + `/plugin install coordn8@coordn8` resolve
    (marketplace "coordn8" lists plugin "coordn8" whose source is the repository root, ".").
  * scripts/install/plugin_layout.py lists the plugin files and catches seeded manifest defects.
  * bin/coordn8 (POSIX sh) runs the Node CLI by path, through a symlink and as a bare PATH command.
  * the generator, extended to also write skills/coordn8/SKILL.md + commands/coordn8.md, keeps
    --check green and the brain fence byte-identical (applied to a TEMP COPY until merged).
  * a sandboxed install simulation finds skill, command, agents and hooks.
  * the real `claude plugin validate` agrees (sandboxed HOME/config; skipped when absent).

Hermetic: every run happens in temp dirs with HOME, CLAUDE_CONFIG_DIR, CLAUDE_HOME and
COORDN8_HOME pointed into them. Nothing is written to the package or the real home.
A check that cannot run here prints `SKIP <name> — <reason>` and is not counted as passed.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import ROOT, Suite, sandbox_env, tmpdir  # noqa: E402

_spec = importlib.util.spec_from_file_location("plugin_layout", str(ROOT / "scripts/install/plugin_layout.py"))
pl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pl)

s = Suite("plugin")
SKIPPED: list = []
PATCH = Path(os.environ.get("C8_W5S1_PATCH", str(ROOT.parent / "Coordn8_v2_handoff" / "_working" / "w5s1" / "PROPOSED_generate_adapters.patch")))
# The package.json "files" list proposed for merge (see the stream's PROPOSED_package_json.md).
PROPOSED_FILES = ["engine/", "VERSION", "bin/", "adapters/", "scripts/", "build/", "examples/", "docs/",
                  "install.sh", "README.md", "LICENSE", ".claude-plugin/", "skills/", "commands/", "agents/",
                  "hooks/", "output-styles/", "data/", "templates/", "bench/"]
IGNORE = shutil.ignore_patterns(".git", "*.zip", "node_modules", "Coordn8", "_build", "_research",
                                "__pycache__", "*.pyc", ".claude", ".DS_Store")
BEGIN, END = "<!-- COORDN8:BRAIN:BEGIN -->", "<!-- COORDN8:BRAIN:END -->"
BRAIN = (ROOT / "engine/PLAYBOOK.md").read_text(encoding="utf-8")
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def skip(name: str, reason: str) -> None:
    SKIPPED.append({"name": name, "reason": reason})
    print(f"  SKIP {name} — {reason}")


def fence(text: str):
    try:
        a = text.index(BEGIN) + len(BEGIN) + 1
        return text[a:text.index(END)]
    except ValueError:
        return None


def sh(cmd, env=None, cwd=None, timeout=120):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=cwd, timeout=timeout,
                              stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as e:
        return subprocess.CompletedProcess(cmd, 124, e.stdout or "", f"TIMEOUT {timeout}s")
    except OSError as e:
        return subprocess.CompletedProcess(cmd, 127, "", str(e))


def senv(t: Path, extra=None) -> dict:
    e = dict(os.environ)
    e.update(sandbox_env(t))
    (t / "claude_config").mkdir(exist_ok=True)
    e["CLAUDE_CONFIG_DIR"] = str(t / "claude_config")
    e["npm_config_cache"] = str(t / "npmcache")
    e["npm_config_userconfig"] = str(t / "npmrc")
    if extra:
        e.update(extra)
    return e


def copy_pkg(dst: Path) -> Path:
    shutil.copytree(str(ROOT), str(dst), ignore=IGNORE, symlinks=True)
    return dst


def errs(pkg) -> list:
    return pl.errors(pl.validate_plugin(pkg))


def jload(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ A. manifests as shipped
pj_path, mj_path = ROOT / ".claude-plugin/plugin.json", ROOT / ".claude-plugin/marketplace.json"
try:
    pj = jload(pj_path)
except (OSError, ValueError) as e:
    pj = {}
    print("  plugin.json unreadable:", e)
try:
    mj = jload(mj_path)
except (OSError, ValueError) as e:
    mj = {}
    print("  marketplace.json unreadable:", e)

s.check("plugin.json parses and has a non-empty name (the only documented required field)",
        isinstance(pj, dict) and isinstance(pj.get("name"), str) and pj["name"].strip(), str(pj)[:200])
s.check("plugin.json name is kebab-case 'coordn8' (no space, @, colon or slash; not a reserved name)",
        pj.get("name") == "coordn8" and pl.KEBAB.match(pj.get("name", "")) is not None
        and not pl.plugin_name_problems(pj.get("name")), str(pl.plugin_name_problems(pj.get("name"))))
s.check("plugin.json version equals the VERSION file", pj.get("version") == VERSION,
        f"plugin.json {pj.get('version')!r} vs VERSION {VERSION!r}")
s.check("plugin.json carries description, author {name: pandeom}, homepage + repository URL, MIT licence, keywords",
        isinstance(pj.get("description"), str) and len(pj["description"]) > 20
        and pj.get("author") == {"name": "pandeom"}
        and pj.get("homepage") == "https://github.com/ompandey98/coordn8"
        and pj.get("repository") == "https://github.com/ompandey98/coordn8"
        and pj.get("license") == "MIT" and isinstance(pj.get("keywords"), list) and pj["keywords"]
        and all(isinstance(k, str) for k in pj["keywords"]), json.dumps(pj)[:300])
s.check("plugin.json has no unknown top-level field",
        not [k for k in pj if k not in pl.KNOWN_FIELDS], str([k for k in pj if k not in pl.KNOWN_FIELDS]))
entries = mj.get("plugins") if isinstance(mj.get("plugins"), list) else []
s.check("marketplace.json parses with name, owner.name and exactly one plugin entry {name, source, description}",
        isinstance(mj.get("name"), str) and isinstance((mj.get("owner") or {}).get("name"), str)
        and len(entries) == 1 and {"name", "source", "description"} <= set(entries[0]), json.dumps(mj)[:300])
s.check("marketplace owner is pandeom and the marketplace has a description",
        (mj.get("owner") or {}).get("name") == "pandeom" and isinstance(mj.get("description"), str), "")
s.check("marketplace entry points at the repository root with the documented self-reference '.'",
        bool(entries) and entries[0].get("source") == ".", str(entries[:1]))
s.check("install id is coordn8@coordn8 (entry name == plugin.json name == marketplace name)",
        bool(entries) and entries[0].get("name") == pj.get("name") == mj.get("name") == "coordn8",
        f"{entries[:1]} {pj.get('name')} {mj.get('name')}")
s.check("marketplace entry sets no version (plugin.json is the one version source)",
        bool(entries) and "version" not in entries[0], str(entries[:1]))
in_manifest_dir = sorted(p.name for p in (ROOT / ".claude-plugin").iterdir()) if (ROOT / ".claude-plugin").is_dir() else []
s.check("no component directory or file inside .claude-plugin/ (only the two manifests)",
        set(in_manifest_dir) <= pl.MANIFEST_ALLOWED and in_manifest_dir, str(in_manifest_dir))
ship_problems = pl.validate_plugin(ROOT)
s.check("local validator: zero errors on the package as it stands", not pl.errors(ship_problems),
        str(pl.errors(ship_problems))[:400])

# ------------------------------------------------------------------ B. plugin_tree
tree = pl.plugin_tree(ROOT)
s.check("plugin_tree lists both manifests",
        ".claude-plugin/plugin.json" in tree and ".claude-plugin/marketplace.json" in tree, str(tree[:5]))
s.check("plugin_tree lists bin/coordn8", "bin/coordn8" in tree, "")
s.check("plugin_tree lists output-styles/coordn8.md", "output-styles/coordn8.md" in tree, "")
s.check("plugin_tree never lists anything outside the documented component locations",
        all(t.split("/")[0] in pl.COMPONENT_DIRS or t.startswith(".claude-plugin/") or t in pl.COMPONENT_FILES
            for t in tree), str([t for t in tree if t.split("/")[0] not in pl.COMPONENT_DIRS][:5]))
if (ROOT / "agents").is_dir() and list((ROOT / "agents").glob("*.md")):
    want = sorted(f"agents/{p.name}" for p in (ROOT / "agents").glob("*.md"))
    s.check("plugin_tree lists every agents/*.md", all(w in tree for w in want),
            str([w for w in want if w not in tree]))
else:
    skip("plugin_tree lists every agents/*.md", "agents/ does not exist yet (created by a parallel stream)")
if (ROOT / "hooks/hooks.json").is_file():
    s.check("plugin_tree lists hooks/hooks.json", "hooks/hooks.json" in tree, "")
else:
    skip("plugin_tree lists hooks/hooks.json", "hooks/hooks.json does not exist yet (created by a parallel stream)")
rt = pl.plugin_tree(ROOT, runtime=True)
s.check("plugin_tree(runtime=True) adds the engine, scripts, data and templates the components call",
        "engine/PLAYBOOK.md" in rt and "bin/coordn8.js" in rt and any(x.startswith("scripts/") for x in rt)
        and any(x.startswith("data/") for x in rt) and any(x.startswith("templates/") for x in rt)
        and "VERSION" in rt, str(len(rt)))
s.check("plugin_tree(runtime=True) ships no test, CI or build-output file",
        not [x for x in rt if x.split("/")[0] in pl.DEV_ONLY_DIRS or x.endswith((".pyc", ".zip"))],
        str([x for x in rt if x.split("/")[0] in pl.DEV_ONLY_DIRS][:5]))

# ------------------------------------------------------------------ C. output style
os_text = (ROOT / "output-styles/coordn8.md").read_text(encoding="utf-8") if (ROOT / "output-styles/coordn8.md").is_file() else ""
fm = pl.frontmatter(os_text) or {}
s.check("output style parses with name, description and keep-coding-instructions: true",
        fm.get("name") == "coordn8" and fm.get("description") and str(fm.get("keep-coding-instructions")) == "true",
        str(fm))
s.check("output style is opt-in (force-for-plugin not set), so a user's own style is never overridden",
        "force-for-plugin" not in fm, str(fm))
s.check("output style makes every status end with what was not verified, and tags claims by evidence",
        "Not verified:" in os_text and "[verified-live]" in os_text and "[code-traced]" in os_text
        and "[untested]" in os_text, "")
s.check("output style carries the third-party-AI limit",
        "Never send text, audio or code to any non-Anthropic hosted AI service" in os_text, "")

# ------------------------------------------------------------------ D. bin/coordn8 launcher
launcher = ROOT / "bin/coordn8"
s.check("bin/coordn8 is executable with a POSIX sh shebang",
        launcher.is_file() and os.access(str(launcher), os.X_OK)
        and launcher.read_text(encoding="utf-8").startswith("#!/bin/sh\n"), "")
s.check("bin/coordn8 is valid POSIX sh (sh -n)", sh(["/bin/sh", "-n", str(launcher)]).returncode == 0, "")
with tmpdir() as d:
    t = Path(d)
    env = senv(t)
    r = sh([str(launcher), "--version"], env=env, cwd=str(t))
    s.check("bin/coordn8 --version prints VERSION (sandboxed HOME)",
            r.returncode == 0 and r.stdout.strip() == VERSION, (r.stdout + r.stderr)[-300:])
    r = sh([str(launcher), "help"], env=env, cwd=str(t))
    s.check("bin/coordn8 help prints the usage", r.returncode == 0 and "Usage" in r.stdout and VERSION in r.stdout,
            (r.stdout + r.stderr)[-300:])
    r = sh(["coordn8", "version"], env=dict(env, PATH=f"{ROOT / 'bin'}{os.pathsep}{env.get('PATH', '')}"),
           cwd=str(t))
    s.check("bin/coordn8 runs as a bare command with the plugin bin/ on PATH, from another directory",
            r.returncode == 0 and r.stdout.strip() == VERSION, (r.stdout + r.stderr)[-300:])
    link = t / "links" / "c8"
    link.parent.mkdir()
    os.symlink(str(launcher), str(link))
    r = sh([str(link), "version"], env=env, cwd=str(t))
    s.check("bin/coordn8 works through a symlink (finds coordn8.js next to the real file)",
            r.returncode == 0 and r.stdout.strip() == VERSION, (r.stdout + r.stderr)[-300:])
    r = sh([str(launcher), "no-such-command"], env=env, cwd=str(t))
    s.check("bin/coordn8 passes arguments through and keeps the CLI's exit code",
            r.returncode == 1 and "unknown command: no-such-command" in r.stderr, (r.stdout + r.stderr)[-300:])
    nonode = [p for p in ("/usr/bin", "/bin") if Path(p).is_dir()]
    if any((Path(p) / "node").exists() for p in nonode):
        skip("bin/coordn8 without node on PATH exits 127 with a plain message", "a node binary sits in /usr/bin or /bin")
    else:
        r = sh(["/bin/sh", str(launcher), "--version"], env=dict(env, PATH=os.pathsep.join(nonode)), cwd=str(t))
        s.check("bin/coordn8 without node on PATH exits 127 with a plain message",
                r.returncode == 127 and "Node.js was not found" in r.stderr and "Traceback" not in r.stderr,
                f"{r.returncode} {r.stderr[-200:]}")

# ------------------------------------------------- E-I. temp copy with the generator extension
gen_src = (ROOT / "build/generate_adapters.py").read_text(encoding="utf-8")
merged = "def gen_claude_plugin" in gen_src
with tmpdir() as d:
    t = Path(d)
    env = senv(t)
    pkg = copy_pkg(t / "pkg")
    before = {p: (pkg / p).read_bytes() for p in ("adapters/claude-code/SKILL.md",
                                                 "adapters/claude-code/commands/coordn8.md",
                                                 "adapters/codex/AGENTS.md", "adapters/cursor/coordn8.mdc",
                                                 "adapters/kiro/coordn8.md", "adapters/generic/COORDN8_PROMPT.md")}
    applied = merged
    if merged:
        print("  (generator already emits the plugin skill; no patch needed)")
    elif PATCH.is_file():
        r = sh(["patch", "-s", "-p1", "-i", str(PATCH)], cwd=str(pkg))
        applied = r.returncode == 0
        s.check("the proposed generator patch applies cleanly to a temp copy", applied, (r.stdout + r.stderr)[-300:])
    else:
        skip("the proposed generator patch applies cleanly to a temp copy", f"patch file not found: {PATCH}")
    if applied:
        r = sh([sys.executable, "build/generate_adapters.py"], env=env, cwd=str(pkg))
        s.check("extended generator writes skills/coordn8/SKILL.md and commands/coordn8.md",
                r.returncode == 0 and (pkg / "skills/coordn8/SKILL.md").is_file()
                and (pkg / "commands/coordn8.md").is_file(), (r.stdout + r.stderr)[-300:])
        r = sh([sys.executable, "build/generate_adapters.py", "--check"], env=env, cwd=str(pkg))
        s.check("extended generator --check passes on the temp copy", r.returncode == 0, (r.stdout + r.stderr)[-300:])
        r = sh([sys.executable, "-c", "import sys; sys.path.insert(0, 'build'); import generate_adapters as g; "
                "print('\\n'.join(sorted(str(p.relative_to(g.ROOT)) for p in g.build_all())))"], env=env, cwd=str(pkg))
        planned = r.stdout.split()
        s.check("--check covers the plugin skill and command (they are in build_all)",
                "skills/coordn8/SKILL.md" in planned and "commands/coordn8.md" in planned, str(planned))
        sk = (pkg / "skills/coordn8/SKILL.md").read_text(encoding="utf-8")
        cm = (pkg / "commands/coordn8.md").read_text(encoding="utf-8")
        s.check("skills/coordn8/SKILL.md brain fence is byte-identical to engine/PLAYBOOK.md", fence(sk) == BRAIN, "")
        s.check("commands/coordn8.md brain fence is byte-identical to engine/PLAYBOOK.md", fence(cm) == BRAIN, "")
        s.check("existing adapter files are byte-identical after the generator extension",
                all((pkg / p).read_bytes() == b for p, b in before.items()),
                str([p for p, b in before.items() if (pkg / p).read_bytes() != b]))
        ad_skill = before["adapters/claude-code/SKILL.md"].decode("utf-8")
        ad_cmd = before["adapters/claude-code/commands/coordn8.md"].decode("utf-8")
        s.check("plugin skill = the adapter skill + one plugin-root note outside the fence (nothing removed)",
                sk.split(BEGIN, 1)[1] == ad_skill.split(BEGIN, 1)[1]
                and all(line in sk.split(BEGIN)[0].splitlines() for line in ad_skill.split(BEGIN)[0].splitlines())
                and "${CLAUDE_PLUGIN_ROOT}" in sk.split(BEGIN)[0], "")
        s.check("plugin command = the adapter command + the same note outside the fence (nothing removed)",
                cm.split(BEGIN, 1)[1] == ad_cmd.split(BEGIN, 1)[1]
                and all(line in cm.split(BEGIN)[0].splitlines() for line in ad_cmd.split(BEGIN)[0].splitlines())
                and "${CLAUDE_PLUGIN_ROOT}" in cm.split(BEGIN)[0], "")
        sfm = pl.frontmatter(sk) or {}
        s.check("plugin skill front matter: name coordn8, description inside the 1,536-char listing cap",
                sfm.get("name") == "coordn8" and 0 < len(str(sfm.get("description", ""))) <= pl.SKILL_LISTING_CAP,
                str(len(str(sfm.get("description", "")))))
        s.check("local validator: zero errors on the patched temp copy", not errs(pkg), str(errs(pkg))[:400])

        # negative: --check must catch drift in the plugin files
        good = sk
        (pkg / "skills/coordn8/SKILL.md").write_text(good.replace("PHASE 0", "PHASE O", 1), encoding="utf-8")
        r = sh([sys.executable, "build/generate_adapters.py", "--check"], env=env, cwd=str(pkg))
        s.check("--check fails when skills/coordn8/SKILL.md drifts from the generator",
                r.returncode == 1 and "skills/coordn8/SKILL.md" in r.stdout, r.stdout[-300:])
        (pkg / "skills/coordn8/SKILL.md").write_text(good, encoding="utf-8")
        (pkg / "commands/coordn8.md").unlink()
        r = sh([sys.executable, "build/generate_adapters.py", "--check"], env=env, cwd=str(pkg))
        s.check("--check fails when commands/coordn8.md is missing",
                r.returncode == 1 and "MISSING: commands/coordn8.md" in r.stdout, r.stdout[-300:])
        (pkg / "commands/coordn8.md").write_text(cm, encoding="utf-8")

        # ---------------------------------------------- F. sandboxed install simulation
        cache = Path(env["CLAUDE_CONFIG_DIR"]) / "plugins" / "cache" / "coordn8" / "coordn8" / VERSION
        for rel in pl.plugin_tree(pkg, runtime=True):
            dst = cache / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(pkg / rel), str(dst))
        found = pl.discover(cache)
        s.check("simulated install: skill /coordn8:coordn8 is found", "coordn8:coordn8" in found["skills"], str(found))
        s.check("simulated install: command /coordn8:coordn8 is found", "coordn8:coordn8" in found["commands"], str(found))
        s.check("simulated install: output style coordn8:coordn8 and executable coordn8 are found",
                "coordn8:coordn8" in found["output_styles"] and "coordn8" in found["bin"], str(found))
        pkg_agents = sorted((pkg / "agents").glob("*.md")) if (pkg / "agents").is_dir() else []
        if pkg_agents:
            want = []
            for a in pkg_agents:
                afm = pl.frontmatter(a.read_text(encoding="utf-8")) or {}
                want.append("coordn8:" + (afm.get("name") or a.stem))
            s.check("simulated install: every package agent is found as coordn8:<name>",
                    all(w in found["agents"] for w in want), str([w for w in want if w not in found["agents"]]))
        else:
            skip("simulated install: every package agent is found as coordn8:<name>",
                 "agents/ does not exist yet (created by a parallel stream)")
        if (pkg / "hooks/hooks.json").is_file():
            hk = jload(pkg / "hooks/hooks.json").get("hooks") or {}
            s.check("simulated install: every hooks.json event is registered", sorted(hk) == found["hooks"] and hk,
                    str(found["hooks"]))
        else:
            skip("simulated install: every hooks.json event is registered",
                 "hooks/hooks.json does not exist yet (created by a parallel stream)")
        r = sh([str(cache / "bin/coordn8"), "--version"], env=env, cwd=str(t))
        s.check("simulated install: the cached bin/coordn8 runs and reports VERSION",
                r.returncode == 0 and r.stdout.strip() == VERSION, (r.stdout + r.stderr)[-300:])
        s.check("simulated install: the cached copy validates with zero errors", not errs(cache), str(errs(cache))[:300])

        # fixture agents + hooks, so discovery is proven even before the parallel streams land
        fx = t / "fx"
        shutil.copytree(str(cache), str(fx), symlinks=True)
        (fx / "agents").mkdir(exist_ok=True)
        (fx / "agents/coordn8-fixture.md").write_text(
            "---\nname: coordn8-fixture\ndescription: Fixture agent used only by the plugin test.\ntools: Read, Grep\n"
            "---\n\nRead only.\n", encoding="utf-8")
        (fx / "hooks").mkdir(exist_ok=True)
        (fx / "hooks/fixture_stop.py").write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
        (fx / "hooks/hooks.json").write_text(json.dumps({"hooks": {
            "Stop": [{"matcher": "", "hooks": [{"type": "command", "timeout": 10,
                                                 "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/hooks/fixture_stop.py\""}]}],
            "SessionStart": [{"matcher": "startup", "hooks": [{"type": "command",
                                                                 "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/hooks/fixture_stop.py\""}]}]}}),
            encoding="utf-8")
        ffound = pl.discover(fx)
        s.check("discovery finds a fixture agent and fixture hook events next to the skill and command",
                "coordn8:coordn8-fixture" in ffound["agents"] and ffound["hooks"] == ["SessionStart", "Stop"]
                and "coordn8:coordn8" in ffound["skills"] and "coordn8:coordn8" in ffound["commands"], str(ffound))
        s.check("local validator: zero errors with the fixture agent and hooks", not errs(fx), str(errs(fx))[:300])

        # ---------------------------------------------- G. seeded defects
        def seeded(label: str, mutate, needle: str):
            dd = t / ("bad-" + re.sub(r"[^a-z0-9]+", "-", label.lower())[:40])
            shutil.copytree(str(fx), str(dd), symlinks=True)
            mutate(dd)
            got = errs(dd)
            s.check(f"validate_plugin catches a seeded defect: {label}", any(needle in g for g in got), str(got)[:300])
            shutil.rmtree(str(dd), ignore_errors=True)

        def edit_json(rel, fn):
            def m(dd):
                p = dd / rel
                data = jload(p)
                fn(data)
                p.write_text(json.dumps(data), encoding="utf-8")
            return m

        seeded("plugin.json without a name", edit_json(".claude-plugin/plugin.json", lambda x: x.pop("name")),
               "plugin.json name: required")
        seeded("plugin name with a space", edit_json(".claude-plugin/plugin.json", lambda x: x.update(name="coord n8")),
               "cannot contain spaces")
        seeded("plugin name with a colon", edit_json(".claude-plugin/plugin.json", lambda x: x.update(name="coord:n8")),
               "cannot contain ':'")
        seeded("reserved plugin name", edit_json(".claude-plugin/plugin.json", lambda x: x.update(name="claude-tools")),
               "is reserved")
        seeded("homepage that is not a URL", edit_json(".claude-plugin/plugin.json", lambda x: x.update(homepage="ompandey98/coordn8")),
               "homepage: must parse as a URL")
        seeded("component directory inside .claude-plugin/",
               lambda dd: shutil.copytree(str(dd / "skills"), str(dd / ".claude-plugin/skills")),
               "component directory inside .claude-plugin/")
        seeded("settings.json inside .claude-plugin/",
               lambda dd: (dd / ".claude-plugin/settings.json").write_text("{}", encoding="utf-8"),
               "must sit at the plugin root")
        seeded("marketplace source not starting with ./",
               edit_json(".claude-plugin/marketplace.json", lambda x: x["plugins"][0].update(source="plugins/coordn8")),
               'must start with "./"')
        seeded("marketplace source escaping the root with ..",
               edit_json(".claude-plugin/marketplace.json", lambda x: x["plugins"][0].update(source="./../coordn8")),
               'Path contains ".."')
        seeded("marketplace entry name differs from the plugin name",
               edit_json(".claude-plugin/marketplace.json", lambda x: x["plugins"][0].update(name="coordn8-pro")),
               "differs from plugin.json name")
        seeded("marketplace without an owner", edit_json(".claude-plugin/marketplace.json", lambda x: x.pop("owner")),
               "owner: required")
        seeded("reserved marketplace name",
               edit_json(".claude-plugin/marketplace.json", lambda x: x.update(name="claude-plugins-official")),
               "is reserved")
        seeded("hooks.json without the top-level \"hooks\" key",
               lambda dd: (dd / "hooks/hooks.json").write_text(json.dumps({"Stop": []}), encoding="utf-8"),
               'top-level "hooks" key')
        seeded("hook command pointing at a script that does not exist",
               edit_json("hooks/hooks.json", lambda x: x["hooks"]["Stop"][0]["hooks"][0].update(
                   command="python3 \"${CLAUDE_PLUGIN_ROOT}/hooks/missing.py\"")),
               "hooks/missing.py does not exist")
        seeded("bin file that is not executable", lambda dd: os.chmod(str(dd / "bin/coordn8"), 0o644), "not executable")
        seeded("agent tools with a Bash(pattern) entry",
               lambda dd: (dd / "agents/coordn8-fixture.md").write_text(
                   "---\nname: coordn8-fixture\ndescription: x\ntools: Read, Bash(python3 x.py)\n---\nbody\n",
                   encoding="utf-8"), "Bash(...) are not supported")
        seeded("component path in plugin.json without ./",
               edit_json(".claude-plugin/plugin.json", lambda x: x.update(skills="skills")), "must start with './'")

        # ---------------------------------------------- H. package.json "files"
        gaps_now = pl.npm_files_gaps(pkg)
        print(f"  REPORT plugin::package.json files lists every runtime dir (expected to fail until supervisor "
              f"merges) — proposal only, not a check; missing today: {gaps_now}")
        s.check("runtime_dirs finds data/ and templates/ (read by shipped scripts) and the plugin directories",
                {"data", "templates", ".claude-plugin", "bin", "output-styles", "scripts", "engine"}
                <= set(pl.runtime_dirs(pkg)), str(pl.runtime_dirs(pkg)))
        pkj = jload(pkg / "package.json")
        pkj["files"] = PROPOSED_FILES
        (pkg / "package.json").write_text(json.dumps(pkj, indent=2) + "\n", encoding="utf-8")
        s.check("the proposed package.json files list leaves no runtime directory out (temp copy)",
                pl.npm_files_gaps(pkg) == [], str(pl.npm_files_gaps(pkg)))
        s.check("npm_files_gaps flags a list without data/ and templates/ (seeded)",
                {"data", "templates"} <= set(pl.npm_files_gaps(pkg, [f for f in PROPOSED_FILES
                                                                     if f not in ("data/", "templates/")])), "")
        npm = shutil.which("npm")
        if npm:
            r = sh([npm, "pack", "--dry-run", "--json", "--ignore-scripts"], env=env, cwd=str(pkg), timeout=120)
            try:
                packed = {f["path"] for f in json.loads(r.stdout)[0]["files"]}
            except (ValueError, KeyError, IndexError):
                packed = set()
            need = [".claude-plugin/plugin.json", ".claude-plugin/marketplace.json", "skills/coordn8/SKILL.md",
                    "commands/coordn8.md", "output-styles/coordn8.md", "bin/coordn8", "engine/PLAYBOOK.md"]
            need += [x for x in pl.plugin_tree(pkg, runtime=True) if x.startswith(("data/", "templates/"))][:6]
            s.check("npm pack --dry-run with the proposed list ships the plugin manifests, skill, data/ and templates/",
                    packed and all(n in packed for n in need), str([n for n in need if n not in packed])[:300])
        else:
            skip("npm pack --dry-run with the proposed list ships the plugin manifests, skill, data/ and templates/",
                 "npm is not on PATH")

        # ---------------------------------------------- I. the real validator (sandboxed)
        claude = shutil.which("claude")
        if not claude:
            for nm in ("real `claude plugin validate` passes on the patched temp copy (sandboxed home)",
                       "real validator reports zero errors, the same as the local validator",
                       "real validator rejects a seeded defect the local validator also rejects"):
                skip(nm, "the claude binary is not on PATH")
        else:
            r = sh([claude, "plugin", "validate", "--json", str(pkg)], env=env, cwd=str(t), timeout=150)
            try:
                rep = json.loads(r.stdout[r.stdout.index("{"):])
            except ValueError:
                rep = {}
            real_errors = [e for part in [rep.get("manifest") or {}] + list(rep.get("contents") or [])
                           for e in (part.get("errors") or [])]
            s.check("real `claude plugin validate` passes on the patched temp copy (sandboxed home)",
                    r.returncode == 0 and rep.get("success") is True, (r.stdout[-300:] + r.stderr[-200:]))
            s.check("real validator reports zero errors, the same as the local validator",
                    rep and not real_errors and not errs(pkg), str(real_errors)[:300])
            bad = t / "real-bad"
            shutil.copytree(str(pkg), str(bad), symlinks=True)
            edit_json(".claude-plugin/marketplace.json", lambda x: x["plugins"][0].update(source="plugins/coordn8"))(bad)
            r = sh([claude, "plugin", "validate", str(bad)], env=env, cwd=str(t), timeout=150)
            s.check("real validator rejects a seeded defect the local validator also rejects",
                    r.returncode == 1 and bool(errs(bad)), f"real={r.returncode} local={errs(bad)[:2]}")
    else:
        for nm in ("extended generator --check passes on the temp copy",
                   "skills/coordn8/SKILL.md brain fence is byte-identical to engine/PLAYBOOK.md",
                   "simulated install: skill /coordn8:coordn8 is found"):
            skip(nm, "generator extension not available (patch missing or failed)")


s.check("the package tree is unchanged by the suite (no generated plugin files leaked into it)",
        merged or not (ROOT / "commands/coordn8.md").exists(), "commands/coordn8.md appeared in the package")
if SKIPPED:
    print(f"  {len(SKIPPED)} check(s) skipped: " + "; ".join(x["name"] for x in SKIPPED))
sys.exit(s.finish())
