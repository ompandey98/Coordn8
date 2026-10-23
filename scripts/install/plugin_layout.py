#!/usr/bin/env python3
"""Coordn8 as a Claude Code plugin: what the plugin is made of, and a local check of the
documented manifest rules.

The whole package is the plugin: `.claude-plugin/marketplace.json` lists one plugin whose
source is "." (the marketplace root), so `/plugin marketplace add ompandey98/coordn8` then
`/plugin install coordn8@coordn8` installs this repository as-is. Claude Code then scans the
standard layout at the package root:

    .claude-plugin/plugin.json    manifest (only `name` is required)
    skills/<name>/SKILL.md        skills          commands/*.md      commands (older format)
    agents/**/*.md                subagents       hooks/hooks.json   hooks
    output-styles/*.md            output styles   bin/*              on the Bash tool PATH
    .mcp.json  settings.json  monitors/monitors.json  themes/  workflows/

Pure functions, no third-party dependencies, Python 3.9:

    plugin_tree(pkg)              -> sorted package-relative paths of every plugin component file
    plugin_tree(pkg, runtime=True)-> the same plus every file the components need at run time
    discover(plugin_dir)          -> what an installed copy exposes (skills, commands, agents,
                                     hook events, output styles, executables)
    validate_plugin(pkg)          -> problems, each "error: ..." or "warning: ..." (empty = clean)
    runtime_dirs(pkg)             -> top-level directories the shipped code reads at run time
    npm_files_gaps(pkg, files)    -> runtime directories a package.json "files" list leaves out

`validate_plugin` re-implements the rules published in the Claude Code plugin docs (manifest
reference, marketplace reference, plugin components) so the layout can be checked on any machine,
with or without the `claude` binary. Where `claude plugin validate` exists, it stays the authority;
the test suite runs both and requires them to agree.

CLI:  python3 scripts/install/plugin_layout.py validate|tree|discover|files [PKG]
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

PKG_ROOT = Path(__file__).resolve().parents[2]

# Standard plugin layout (default locations). Directories are scanned; files are read.
COMPONENT_DIRS = ("skills", "commands", "agents", "hooks", "output-styles", "bin",
                  "monitors", "themes", "workflows")
COMPONENT_FILES = (".mcp.json", ".lsp.json", "settings.json")
MANIFEST_DIR = ".claude-plugin"
MANIFEST_ALLOWED = {"plugin.json", "marketplace.json"}

# plugin.json top-level fields that hold component paths, and how each path must look.
PATH_FIELDS = ("skills", "commands", "agents", "hooks", "mcpServers", "lspServers", "outputStyles",
               "workflows")
KNOWN_FIELDS = {"$schema", "name", "displayName", "version", "description", "author", "homepage",
                "repository", "license", "keywords", "metadata", "icon", "documentationUrl",
                "supportUrl", "privacyPolicyUrl", "termsOfServiceUrl", "defaultEnabled",
                "dependencies", "settings", "userConfig", "types", "channels", "experimental",
                "themes", "monitors"} | set(PATH_FIELDS)
ENTRY_KNOWN = {"name", "source", "description", "version", "category", "tags", "strict", "relevance",
               "dependencies", "defaultEnabled", "displayName", "metadata", "headers", "headersHelper",
               "author", "homepage", "repository", "license", "keywords", "commands", "agents",
               "skills", "hooks", "outputStyles", "themes", "mcpServers", "lspServers", "userConfig",
               "channels", "settings", "experimental"}
MARKET_KNOWN = {"$schema", "name", "owner", "plugins", "description", "version", "metadata",
                "forceRemoveDeletedPlugins", "allowCrossMarketplaceDependenciesOn", "renames"}

# Plugin agents: fields the docs list as ignored inside a plugin.
AGENT_IGNORED = ("permissionMode", "hooks", "mcpServers", "initialPrompt")
SKILL_LISTING_CAP = 1536  # description + when_to_use are truncated here in the skill listing

ID_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")      # each half of plugin@marketplace
KEBAB = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
CTRL_BIDI = re.compile("[\x00-\x1f\x7f\u200e\u200f\u202a-\u202e\u2066-\u2069]")
RESERVED_MARKETS = {
    "claude-code-marketplace", "claude-code-plugins", "claude-plugins-official", "anthropic-marketplace",
    "anthropic-plugins", "agent-skills", "anthropic-agent-skills", "life-sciences",
    "knowledge-work-plugins", "claude-for-legal", "claude-for-financial-services",
    "financial-services-plugins", "first-party-plugins", "claude-tag-plugins", "claude-community",
    "claude-plugins-community", "healthcare", "anthropic-plugin-directory", "claude-plugin-directory",
    "inline", "builtin", "skills-dir", "synced", "claude-plugin-test", "npm", "pip", "uv", "cargo",
    "github", "gh", "constructor", "hasownproperty", "isprototypeof", "propertyisenumerable",
    "tolocalestring", "tostring", "valueof"}
RESERVED_PLUGIN_EXACT = {"claude", "anthropic", "anthropics", "claude-code", "claude-mods"}
RESERVED_PLUGIN_PREFIX = ("claude-", "anthropic-", "anthropics-", "cc-plugin-")

SKIP_NAMES = {".DS_Store", "Thumbs.db"}
SKIP_DIRS = {"__pycache__", ".git", "node_modules"}

# Top-level directories that never ship as runtime (development, CI, build output).
DEV_ONLY_DIRS = {"tests", ".git", ".github", "node_modules", "Coordn8", "_build", "_research",
                 "_proof", "_telemetry", "__pycache__"}
# Files whose text is read to find runtime references (code + the instructions agents follow).
RUNTIME_SOURCE_GLOBS = ("bin/*", "install.sh", "scripts/**/*.py", "scripts/**/*.js", "scripts/**/*.sh",
                        "hooks/**/*", "build/*.py", "engine/*.md", "skills/**/*.md", "agents/**/*.md",
                        "commands/*.md", ".claude-plugin/*.json", "package.json")


# ----------------------------------------------------------------------------- helpers
def _rel(p: Path, root: Path) -> str:
    return p.relative_to(root).as_posix()


def _files_under(d: Path):
    if not d.is_dir():
        return
    for f in sorted(d.rglob("*")):
        parts = f.relative_to(d).parts
        if f.is_file() and f.name not in SKIP_NAMES and not f.name.endswith(".pyc") \
                and not any(p in SKIP_DIRS for p in parts):
            yield f


def load_json(p: Path):
    """(data, error). Never raises."""
    try:
        return json.loads(Path(p).read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, "file not found"
    except (OSError, UnicodeDecodeError) as e:
        return None, f"unreadable: {e}"
    except ValueError as e:
        return None, f"invalid JSON: {e}"


def frontmatter(text: str) -> dict | None:
    """Minimal YAML front matter: `key: value` scalars and `- item` lists. None when absent."""
    if not text.startswith("---"):
        return None
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    out: dict = {}
    key = None
    for line in lines[1:]:
        if line.strip() == "---":
            return out
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if m and not line.startswith((" ", "\t")):
            key, val = m.group(1), m.group(2).strip()
            if val == "":
                out[key] = []
            elif len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                out[key] = val[1:-1]
            else:
                out[key] = val
            continue
        m = re.match(r"^\s+-\s*(.*)$", line)
        if m and key is not None and isinstance(out.get(key), list):
            out[key].append(m.group(1).strip().strip("\"'"))
    return None  # no closing fence


def _is_url(s) -> bool:
    if not isinstance(s, str):
        return False
    u = urlparse(s)
    return bool(u.scheme and u.netloc)


def plugin_name_problems(name, where: str = "plugin.json name") -> list:
    """Rules for a plugin `name` (manifest reference)."""
    out = []
    if not isinstance(name, str) or not name.strip():
        return [f"error: {where}: required and must be a non-empty string"]
    if " " in name:
        out.append(f"error: {where}: cannot contain spaces; use kebab-case (got {name!r})")
    for ch, label in (("@", "'@'"), (":", "':'"), ("/", "a path separator"), ("\\", "a path separator")):
        if ch in name:
            out.append(f"error: {where}: cannot contain {label} (got {name!r})")
    if CTRL_BIDI.search(name):
        out.append(f"error: {where}: cannot contain control or bidirectional-formatting characters")
    low = re.sub(r"[-_.\s]+", "-", name.lower())
    if low in RESERVED_PLUGIN_EXACT or low.startswith(RESERVED_PLUGIN_PREFIX) or \
            re.search(r"(^|-)official(-|$)", low) and re.search(r"(^|-)(claude|anthropic)(-|$)", low):
        out.append(f"error: {where}: {name!r} is reserved: it passes as one of Anthropic's own")
    elif re.search(r"(^|-)(claude|anthropic|anthropics)(-|$)", low):
        out.append(f"warning: {where}: {name!r} reads as one of Anthropic's own")
    if not out and not KEBAB.match(name):
        out.append(f"warning: {where}: {name!r} is not kebab-case")
    return out


def _component_path_problems(field: str, value, pkg: Path) -> list:
    """Every component path in plugin.json starts with ./ (skills may also be "."), stays inside the
    plugin root, and exists. Inline objects (hooks/mcpServers/lspServers/commands maps) are allowed."""
    out = []
    vals = value if isinstance(value, list) else [value]
    for v in vals:
        if isinstance(v, dict):
            if field in ("hooks", "mcpServers", "lspServers", "commands"):
                continue
            out.append(f"error: plugin.json {field}: inline objects are not accepted here")
            continue
        if not isinstance(v, str):
            out.append(f"error: plugin.json {field}: expected a path string, got {type(v).__name__}")
            continue
        if field == "mcpServers" and v.startswith("https://"):
            continue
        if not (v.startswith("./") or (field == "skills" and v == ".")):
            out.append(f"error: plugin.json {field}: path must start with './' (got {v!r})")
            continue
        if ".." in Path(v).parts:
            out.append(f"error: plugin.json {field}: Path contains '..' which could be a path traversal "
                       f"attempt ({v!r})")
            continue
        target = (pkg / v).resolve()
        try:
            target.relative_to(pkg.resolve())
        except ValueError:
            out.append(f"error: plugin.json {field}: path escapes the plugin directory ({v!r})")
            continue
        if not target.exists():
            out.append(f"error: plugin.json {field}: Path not found ({v!r})")
        elif field == "agents" and not (target.is_file() and target.suffix == ".md"):
            out.append(f"error: plugin.json agents: entries must be .md files ({v!r})")
        elif field == "skills" and not target.is_dir():
            out.append(f"error: plugin.json skills: entries must be directories ({v!r})")
    return out


# ------------------------------------------------------------------ manifest checks
def check_plugin_json(pkg: Path) -> list:
    p = pkg / MANIFEST_DIR / "plugin.json"
    if not p.exists():
        return ["warning: .claude-plugin/plugin.json missing (optional, but then the plugin has no "
                "name, version or metadata of its own)"]
    data, err = load_json(p)
    if err:
        return [f"error: plugin.json: {err}"]
    if not isinstance(data, dict):
        return ["error: plugin.json: the top level must be a JSON object"]
    out = plugin_name_problems(data.get("name"))
    for k in sorted(set(data) - KNOWN_FIELDS):
        out.append(f"warning: plugin.json: unknown field {k!r} (Claude Code strips it at load time)")
    for k in ("version", "description"):
        if k not in data:
            out.append(f"warning: plugin.json: no {k!r}")
        elif not isinstance(data[k], str) or not data[k].strip():
            out.append(f"error: plugin.json {k}: must be a non-empty string")
    if "author" not in data:
        out.append("warning: plugin.json: no 'author'")
    else:
        a = data["author"]
        if not isinstance(a, dict):
            out.append("error: plugin.json author: must be an object with a 'name'")
        elif not isinstance(a.get("name"), str) or not a.get("name", "").strip():
            out.append("error: plugin.json author.name: required and must be a non-empty string")
    if "homepage" in data and not _is_url(data["homepage"]):
        out.append(f"error: plugin.json homepage: must parse as a URL (got {data['homepage']!r})")
    for k in ("repository", "license", "displayName"):
        if k in data and not isinstance(data[k], str):
            out.append(f"error: plugin.json {k}: must be a string")
    if "keywords" in data and not (isinstance(data["keywords"], list)
                                   and all(isinstance(x, str) for x in data["keywords"])):
        out.append("error: plugin.json keywords: must be an array of strings")
    if "defaultEnabled" in data and not isinstance(data["defaultEnabled"], bool):
        out.append("error: plugin.json defaultEnabled: must be a boolean")
    for field in PATH_FIELDS:
        if field in data:
            out.extend(_component_path_problems(field, data[field], pkg))
    return out


def check_marketplace_json(pkg: Path) -> list:
    p = pkg / MANIFEST_DIR / "marketplace.json"
    if not p.exists():
        return []
    data, err = load_json(p)
    if err:
        return [f"error: marketplace.json: {err}"]
    if not isinstance(data, dict):
        return ["error: marketplace.json: the top level must be a JSON object"]
    out = []
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        out.append("error: marketplace.json name: Marketplace must have a name")
    else:
        if " " in name:
            out.append("error: marketplace.json name: cannot contain spaces; use kebab-case")
        if "/" in name or "\\" in name or ".." in name or name == ".":
            out.append("error: marketplace.json name: cannot contain path separators or '..', or be '.'")
        if CTRL_BIDI.search(name):
            out.append("error: marketplace.json name: cannot contain control or bidirectional-formatting "
                       "characters")
        if not ID_PART.match(name):
            out.append(f"error: marketplace.json name: {name!r} is not a valid plugin-id part "
                       "(letters, digits, '.', '_', '-', starting with a letter or digit)")
        norm = re.sub(r"[^a-z0-9_]+", "-", name.lower().rstrip("."))
        if name.lower() in RESERVED_MARKETS or norm in RESERVED_MARKETS or name.lower().startswith("claudeai-"):
            out.append(f"error: marketplace.json name: {name!r} is reserved")
        if any(ord(c) > 127 for c in name):
            out.append("error: marketplace.json name: non-ASCII names impersonate an official marketplace")
    owner = data.get("owner")
    if not isinstance(owner, dict):
        out.append("error: marketplace.json owner: required object with a 'name'")
    elif not isinstance(owner.get("name"), str) or not owner.get("name", "").strip():
        out.append("error: marketplace.json owner.name: Author name cannot be empty")
    if "description" not in data and "description" not in (data.get("metadata") or {}):
        out.append("warning: marketplace.json: no marketplace description")
    for k in sorted(set(data) - MARKET_KNOWN):
        out.append(f"warning: marketplace.json: unknown field {k!r}")
    plugins = data.get("plugins")
    if not isinstance(plugins, list):
        out.append("error: marketplace.json plugins: required array")
        return out
    if not plugins:
        out.append("warning: marketplace.json: Marketplace has no plugins defined")
    root_meta = (data.get("metadata") or {}).get("pluginRoot") if isinstance(data.get("metadata"), dict) else None
    seen = set()
    manifest, _ = load_json(pkg / MANIFEST_DIR / "plugin.json")
    for i, e in enumerate(plugins):
        where = f"marketplace.json plugins[{i}]"
        if not isinstance(e, dict):
            out.append(f"error: {where}: must be an object")
            continue
        en = e.get("name")
        out.extend(x for x in plugin_name_problems(en, where + ".name") if x.startswith("error"))
        if isinstance(en, str):
            if en and not ID_PART.match(en):
                out.append(f"error: {where}.name: {en!r} is not a valid plugin-id part")
            if en in seen:
                out.append(f"error: {where}.name: Duplicate plugin name {en!r} found in marketplace")
            seen.add(en)
        for k in sorted(set(e) - ENTRY_KNOWN):
            out.append(f"warning: {where}: unknown field {k!r}")
        src = e.get("source")
        if "source" not in e:
            out.append(f"error: {where}.source: required")
        elif isinstance(src, str):
            bare = root_meta and "/" not in src and src not in (".", "")
            if not (src == "." or src.startswith("./") or bare):
                out.append(f"error: {where}.source: Invalid string: must start with \"./\" (got {src!r})")
            elif ".." in src.split("/"):
                out.append(f"error: {where}.source: Path contains \"..\": {src}")
            elif "\\" in src[2:]:
                out.append(f"error: {where}.source: backslash in a relative path ({src!r})")
            else:
                rel = (Path(root_meta) / src) if bare else Path(src)
                target = pkg / rel
                if not target.is_dir():
                    out.append(f"warning: {where}.source: {src!r} does not exist yet "
                               "(validate passes; install fails with 'Source path does not exist')")
                elif target.resolve() == pkg.resolve() and isinstance(manifest, dict) \
                        and isinstance(en, str) and manifest.get("name") not in (None, en):
                    out.append(f"error: {where}.name: entry name {en!r} differs from plugin.json name "
                               f"{manifest.get('name')!r} (install by the manifest name then fails)")
        elif isinstance(src, dict):
            if not isinstance(src.get("source"), str):
                out.append(f"error: {where}.source: Invalid input (object without a 'source' type)")
        else:
            out.append(f"error: {where}.source: Invalid input")
        if isinstance(e.get("hooks"), (str, list)):
            out.append(f"error: {where}.hooks: file-path or array hooks are not yet supported in a "
                       "marketplace entry (write an inline object or use hooks/hooks.json)")
        if isinstance(manifest, dict) and isinstance(e.get("version"), str) and \
                isinstance(manifest.get("version"), str) and e["version"] != manifest["version"]:
            out.append(f"warning: {where}.version: entry says {e['version']!r} but plugin.json says "
                       f"{manifest['version']!r}; plugin.json wins at install")
    return out


def check_manifest_dir(pkg: Path) -> list:
    """Only the manifests live in .claude-plugin/; every component sits at the plugin root."""
    d = pkg / MANIFEST_DIR
    out = []
    if not d.is_dir():
        return out
    for child in sorted(d.iterdir()):
        if child.name in SKIP_NAMES:
            continue
        if child.is_dir() and (child.name in COMPONENT_DIRS):
            out.append(f"error: .claude-plugin/{child.name}/ is a component directory inside "
                       ".claude-plugin/; it must sit at the plugin root or it is never loaded")
        elif child.name in COMPONENT_FILES:
            out.append(f"error: .claude-plugin/{child.name} must sit at the plugin root")
        elif child.name not in MANIFEST_ALLOWED:
            out.append(f"warning: .claude-plugin/{child.name}: not a manifest; Claude Code does not read it")
    return out


def check_hooks(pkg: Path) -> list:
    p = pkg / "hooks" / "hooks.json"
    if not p.exists():
        return []
    data, err = load_json(p)
    if err:
        return [f"error: hooks/hooks.json: {err}"]
    if not isinstance(data, dict) or not isinstance(data.get("hooks"), dict):
        return ["error: hooks/hooks.json: the event map must sit under a top-level \"hooks\" key"]
    out = []
    for event, groups in data["hooks"].items():
        if not isinstance(groups, list):
            out.append(f"error: hooks/hooks.json {event}: must be an array of matcher groups")
            continue
        for gi, g in enumerate(groups):
            if not isinstance(g, dict) or not isinstance(g.get("hooks"), list) or not g["hooks"]:
                out.append(f"error: hooks/hooks.json {event}[{gi}]: needs a non-empty 'hooks' array")
                continue
            if "matcher" in g and not isinstance(g["matcher"], str):
                out.append(f"error: hooks/hooks.json {event}[{gi}].matcher: must be a string")
            for hi, h in enumerate(g["hooks"]):
                if not isinstance(h, dict) or not isinstance(h.get("type"), str):
                    out.append(f"error: hooks/hooks.json {event}[{gi}].hooks[{hi}]: needs a 'type'")
                    continue
                if h["type"] == "command":
                    cmd = h.get("command")
                    if not isinstance(cmd, str) or not cmd.strip():
                        out.append(f"error: hooks/hooks.json {event}[{gi}].hooks[{hi}]: command hook "
                                   "without a 'command'")
                        continue
                    if "timeout" in h and not isinstance(h["timeout"], (int, float)):
                        out.append(f"error: hooks/hooks.json {event}[{gi}].hooks[{hi}].timeout: "
                                   "must be a number of seconds")
                    if "args" not in h and re.search(r'(?<!")\$\{CLAUDE_PLUGIN_ROOT\}', cmd) and \
                            not re.search(r'"[^"]*\$\{CLAUDE_PLUGIN_ROOT\}[^"]*"', cmd):
                        out.append(f"warning: hooks/hooks.json {event}[{gi}].hooks[{hi}]: "
                                   "${CLAUDE_PLUGIN_ROOT} unquoted in a shell-form command")
                    for m in re.finditer(r"\$\{CLAUDE_PLUGIN_ROOT\}/([^\s\"']+)", cmd):
                        if not (pkg / m.group(1)).exists():
                            out.append(f"error: hooks/hooks.json {event}[{gi}].hooks[{hi}]: "
                                       f"{m.group(1)} does not exist in the plugin")
    return out


def check_agents(pkg: Path) -> list:
    out = []
    for f in _files_under(pkg / "agents"):
        if f.suffix != ".md":
            continue
        rel = _rel(f, pkg)
        fm = frontmatter(f.read_text(encoding="utf-8", errors="replace"))
        if fm is None:
            out.append(f"warning: {rel}: front matter missing or unparsable (the agent loads with every "
                       "field ignored)")
            continue
        name = fm.get("name")
        if isinstance(name, str) and ":" in name:
            out.append(f"error: {rel}: agent name cannot contain ':'")
        if not fm.get("description"):
            out.append(f"error: {rel}: 'description' is required")
        tools = fm.get("tools")
        tl = tools if isinstance(tools, list) else [t.strip() for t in str(tools or "").split(",")]
        if any("(" in t for t in tl if t):
            out.append(f"error: {rel}: tools: patterns such as Bash(...) are not supported in an agent's "
                       "tools field; list 'Bash' or omit it")
        for k in AGENT_IGNORED:
            if k in fm:
                out.append(f"warning: {rel}: {k!r} is ignored in plugin agents")
    return out


def check_skills(pkg: Path) -> list:
    out = []
    d = pkg / "skills"
    if not d.is_dir():
        return out
    for sd in sorted(x for x in d.iterdir() if x.is_dir() and x.name not in SKIP_DIRS):
        sk = sd / "SKILL.md"
        if not sk.is_file():
            out.append(f"warning: skills/{sd.name}/: no SKILL.md, so it is not a skill")
            continue
        fm = frontmatter(sk.read_text(encoding="utf-8", errors="replace"))
        if fm is None:
            out.append(f"error: skills/{sd.name}/SKILL.md: front matter missing or unparsable")
            continue
        if not fm.get("description"):
            out.append(f"error: skills/{sd.name}/SKILL.md: needs a 'description' so Claude knows when to use it")
        listed = len(str(fm.get("description") or "")) + len(str(fm.get("when_to_use") or ""))
        if listed > SKILL_LISTING_CAP:
            out.append(f"warning: skills/{sd.name}/SKILL.md: description + when_to_use is {listed} chars; "
                       f"the listing truncates at {SKILL_LISTING_CAP}")
    return out


def check_output_styles(pkg: Path) -> list:
    out = []
    for f in _files_under(pkg / "output-styles"):
        if f.suffix != ".md":
            continue
        fm = frontmatter(f.read_text(encoding="utf-8", errors="replace"))
        rel = _rel(f, pkg)
        if fm is None:
            out.append(f"warning: {rel}: no front matter (loads under its file name with no fields set)")
            continue
        for k in ("name", "description"):
            if not fm.get(k):
                out.append(f"warning: {rel}: no {k!r}")
        for k in ("keep-coding-instructions", "force-for-plugin"):
            if k in fm and str(fm[k]).lower() not in ("true", "false"):
                out.append(f"error: {rel}: {k} must be true or false")
    return out


def check_bin(pkg: Path) -> list:
    out = []
    d = pkg / "bin"
    if not d.is_dir():
        return out
    for f in sorted(d.iterdir()):
        if not f.is_file() or f.name in SKIP_NAMES:
            continue
        rel = _rel(f, pkg)
        if os.name != "nt" and not os.access(str(f), os.X_OK):
            out.append(f"error: {rel}: not executable (chmod +x), so it cannot run from the Bash PATH")
        try:
            head = f.read_bytes()[:2]
        except OSError:
            head = b""
        if head != b"#!":
            out.append(f"error: {rel}: no #! line, so it cannot run as a bare command")
    return out


def check_root(pkg: Path) -> list:
    out = []
    if (pkg / "CLAUDE.md").is_file():
        out.append("warning: CLAUDE.md at the plugin root is not loaded as project context")
    return out


def validate_plugin(pkg) -> list:
    """Every documented manifest/layout rule this module knows, as 'error: ...'/'warning: ...'."""
    pkg = Path(pkg)
    out = []
    for fn in (check_manifest_dir, check_plugin_json, check_marketplace_json, check_hooks, check_agents,
               check_skills, check_output_styles, check_bin, check_root):
        try:
            out.extend(fn(pkg))
        except Exception as e:  # a checker bug must surface as a problem, never a crash
            out.append(f"error: {fn.__name__} crashed: {e}")
    return out


def errors(problems: list) -> list:
    return [p for p in problems if p.startswith("error")]


# ------------------------------------------------------------------- the plugin tree
def plugin_tree(pkg, runtime: bool = False) -> list:
    """Package-relative paths of every plugin component file, sorted. With runtime=True, also every
    file under the directories those components read at run time (see runtime_dirs)."""
    pkg = Path(pkg)
    files = set()
    for name in sorted(MANIFEST_ALLOWED):
        if (pkg / MANIFEST_DIR / name).is_file():
            files.add(f"{MANIFEST_DIR}/{name}")
    for d in COMPONENT_DIRS:
        for f in _files_under(pkg / d):
            files.add(_rel(f, pkg))
    for name in COMPONENT_FILES:
        if (pkg / name).is_file():
            files.add(name)
    if runtime:
        for d in runtime_dirs(pkg):
            p = pkg / d
            if p.is_dir():
                for f in _files_under(p):
                    files.add(_rel(f, pkg))
        for name in ("VERSION", "LICENSE", "package.json", "install.sh"):
            if (pkg / name).is_file():
                files.add(name)
    return sorted(files)


def discover(plugin_dir) -> dict:
    """What an installed plugin copy exposes, by the documented default scan. Names are as the user
    sees them: skills /<plugin>:<name>, commands /<plugin>:<file>, agents <plugin>:<name>."""
    root = Path(plugin_dir)
    manifest, _ = load_json(root / MANIFEST_DIR / "plugin.json")
    pname = manifest.get("name") if isinstance(manifest, dict) and manifest.get("name") else root.name
    res = {"plugin": pname, "skills": [], "commands": [], "agents": [], "hooks": [], "output_styles": [],
           "bin": []}
    sd = root / "skills"
    if sd.is_dir():
        for d in sorted(x for x in sd.iterdir() if (x / "SKILL.md").is_file()):
            fm = frontmatter((d / "SKILL.md").read_text(encoding="utf-8", errors="replace")) or {}
            res["skills"].append(f"{pname}:{fm.get('name') or d.name}")
    for f in sorted((root / "commands").glob("**/*.md")) if (root / "commands").is_dir() else []:
        rel = f.relative_to(root / "commands").with_suffix("")
        res["commands"].append(pname + ":" + ":".join(rel.parts))
    for f in _files_under(root / "agents"):
        if f.suffix == ".md":
            fm = frontmatter(f.read_text(encoding="utf-8", errors="replace")) or {}
            sub = f.relative_to(root / "agents").parent.parts
            res["agents"].append(":".join((pname,) + tuple(sub) + (fm.get("name") or f.stem,)))
    hooks, _ = load_json(root / "hooks" / "hooks.json")
    if isinstance(hooks, dict) and isinstance(hooks.get("hooks"), dict):
        res["hooks"] = sorted(hooks["hooks"])
    for f in _files_under(root / "output-styles"):
        if f.suffix == ".md":
            fm = frontmatter(f.read_text(encoding="utf-8", errors="replace")) or {}
            res["output_styles"].append(f"{pname}:{fm.get('name') or f.stem}")
    bd = root / "bin"
    if bd.is_dir():
        res["bin"] = sorted(f.name for f in bd.iterdir() if f.is_file() and os.access(str(f), os.X_OK))
    return res


# ------------------------------------------------------- what an npm install must carry
def _source_files(pkg: Path):
    seen = set()
    for g in RUNTIME_SOURCE_GLOBS:
        for f in pkg.glob(g):
            if f.is_file() and f not in seen and not any(p in SKIP_DIRS for p in f.parts):
                seen.add(f)
                yield f


def runtime_dirs(pkg) -> list:
    """Top-level directories the shipped code needs at run time: every plugin component directory
    present, plus every top-level directory that shipped code or agent instructions reference as a
    path (a quoted 'dir/...' or "dir", `ROOT / "dir"`, path.join(ROOT, 'dir'), ${CLAUDE_PLUGIN_ROOT}/dir).
    A directory's own files do not count as a reference to it."""
    pkg = Path(pkg)
    tops = sorted(p.name for p in pkg.iterdir() if p.is_dir() and p.name not in DEV_ONLY_DIRS
                  and p.name not in SKIP_DIRS and (not p.name.startswith(".") or p.name == MANIFEST_DIR))
    found = {d for d in tops if d in COMPONENT_DIRS or d == MANIFEST_DIR}
    texts = []
    for f in _source_files(pkg):
        try:
            texts.append((f.relative_to(pkg).parts[0], f.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            continue
    for d in tops:
        if d in found:
            continue
        e = re.escape(d)
        pat = re.compile(r"[\"'`]" + e + r"(?:/|[\"'`])"            # 'data/x.json'  "data"  `data/`
                         + r"|/\s*[\"']" + e + r"[\"']"               # ROOT / "data"
                         + r"|\$\{CLAUDE_PLUGIN_ROOT\}/" + e + r"/")  # ${CLAUDE_PLUGIN_ROOT}/data/
        if any(top != d and pat.search(t) for top, t in texts):
            found.add(d)
    return sorted(found)


def npm_files_gaps(pkg, files=None) -> list:
    """Runtime directories a package.json "files" list leaves out (empty list = complete). `files`
    defaults to the list in pkg/package.json."""
    pkg = Path(pkg)
    if files is None:
        data, err = load_json(pkg / "package.json")
        files = (data or {}).get("files") if isinstance(data, dict) else None
    if not isinstance(files, list):
        return ["package.json has no 'files' list"]
    entries = {str(x).strip().rstrip("/").lstrip("./") if not str(x).startswith(".claude")
               else str(x).strip().rstrip("/") for x in files}
    entries = {re.sub(r"/\*\*?(/\*)?$", "", e) for e in entries}
    return [d for d in runtime_dirs(pkg) if d not in entries]


# -------------------------------------------------------------------------------- CLI
def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "validate"
    pkg = Path(argv[1]).resolve() if len(argv) > 1 else PKG_ROOT
    if cmd == "validate":
        probs = validate_plugin(pkg)
        for p in probs:
            print(p)
        n = len(errors(probs))
        print(f"{'FAILED' if n else 'OK'}: {n} error(s), {len(probs) - n} warning(s) in {pkg}")
        return 1 if n else 0
    if cmd == "tree":
        print("\n".join(plugin_tree(pkg, runtime="--runtime" in argv)))
        return 0
    if cmd == "discover":
        print(json.dumps(discover(pkg), indent=1))
        return 0
    if cmd == "files":
        gaps = npm_files_gaps(pkg)
        print(json.dumps({"runtime_dirs": runtime_dirs(pkg), "missing_from_files": gaps}, indent=1))
        return 1 if gaps else 0
    print("usage: plugin_layout.py validate|tree [--runtime]|discover|files [PKG]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
