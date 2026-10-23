#!/usr/bin/env python3
"""Coordn8 — register open-source MCP servers with the host, and OFFER the sign-in ones (J7, F7).

    install_mcp.py list      [--json]                      what the catalog holds
    install_mcp.py status    [--json]                      which are registered in the host config
    install_mcp.py install   [--only id,..] [--dry-run] [--json] [--cli-timeout 30]
    install_mcp.py uninstall [--only id,..] [--dry-run] [--json]
    install_mcp.py offer     [--timeout 20]                sign-in-only tools: print, ask ONCE, default skip
    install_mcp.py validate  [--json]                      catalog checks (licence, source, hosted-AI denylist)
    (every subcommand also takes --data <catalog.json>; default data/mcp_servers.json)

Rules this script keeps, every run:
  * Only servers that need NO sign-in are installed, and only when their runner (npx / uvx),
    minimum node version and required desktop app are present. Anything missing -> skipped
    with the reason. Any failure -> skipped with the reason. The loop never stops.
  * Registration: `claude mcp add --scope user <id> -- <command> <args...>` when a `claude`
    CLI is on PATH (30 s timeout, stdin closed); otherwise an additive, backed-up edit of the
    single key `mcpServers.<id>` in <HOME>/.claude.json via lib/safe_write.merge_json.
  * Never touches a server it did not add: a same-named entry of the user's, or the same
    package registered under another name, is reported "present" and left alone.
  * What it added is recorded in <user_home>/installed_mcp.json, so `uninstall` removes exactly
    those keys — and not even those if the user has since edited them.
  * A server the user removed after we added it is not re-added on the next install unless
    asked for by name (--only).
  * No entry may be, or proxy, a hosted non-Anthropic AI service; the installer refuses any
    entry that matches the built-in denylist even if the catalog lists it.
  * Sign-in offers are never performed: on "y" the steps are printed; the user signs in.
Exit code: 0 (even when servers were skipped or failed) unless the catalog cannot be read (3).
Python 3.9+, zero third-party deps.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from lib import safe_write  # noqa: E402
from lib.paths import PKG_ROOT, user_home  # noqa: E402

DEFAULT_DATA = PKG_ROOT / "data" / "mcp_servers.json"
RECORD_NAME = "installed_mcp.json"
CLI_TIMEOUT = 30
OFFER_TIMEOUT = 20
EXIT_OK, EXIT_ERROR = 0, 3

# Licences accepted as OSI-approved open source (SPDX ids). An expression joined by AND/OR
# passes only if every id in it is on this list.
OSI_LICENCES = {
    "MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "MPL-2.0", "0BSD", "Zlib",
    "GPL-2.0-only", "GPL-2.0-or-later", "GPL-3.0-only", "GPL-3.0-or-later",
    "LGPL-2.1-only", "LGPL-2.1-or-later", "LGPL-3.0-only", "LGPL-3.0-or-later",
    "AGPL-3.0-only", "AGPL-3.0-or-later", "EPL-2.0", "Unlicense", "PSF-2.0", "BSL-1.0",
}

# Hosted AI models / hosted search / hosted web readers / hosted docs-AI and 3-D generation
# services. Matched case-insensitively against every machine-meaningful field of an entry
# (package, command, args, urls). A hit means the entry is refused, catalog or not.
HOSTED_AI_DENYLIST = (
    "openai", "chatgpt", "generativelanguage", "aistudio.google", "gemini", "deepmind",
    "vertex-ai", "vertexai", "mistral.ai", "mistralai", "cohere.com", "cohere-ai",
    "perplexity", "api.x.ai", "x.ai/", "deepseek", "moonshot", "zhipu", "bigmodel",
    "together.ai", "together.xyz", "groq", "replicate.com", "openrouter", "fireworks.ai",
    "api-inference.huggingface", "huggingface.co/inference", "inference-api", "elevenlabs",
    "meshy", "hyper3d", "tripo3d", "hunyuan", "three.ws", "mcp-for-blender", "web-reader",
    "webreader", "tavily", "exa.ai", "exa-mcp", "context7", "deepwiki", "githubcopilot",
    "copilot", "ref.tools", "brave-search", "search.brave", "serpapi", "jina.ai", "firecrawl",
    "anthropic-proxy", "litellm",
)

RUNNER_FALLBACK_DIRS = ("~/.local/bin", "~/.cargo/bin", "/opt/homebrew/bin", "/usr/local/bin")


class CatalogError(RuntimeError):
    pass


# ------------------------------------------------------------------- catalog
def load_catalog(path: str | Path | None = None) -> dict:
    p = Path(path) if path else DEFAULT_DATA
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise CatalogError(f"cannot read MCP catalog {p}: {e}")
    if not isinstance(data, dict) or data.get("schema") != 1 or not isinstance(data.get("servers"), list):
        raise CatalogError(f"MCP catalog {p} is not schema 1 with a 'servers' list")
    data.setdefault("signin_offers", [])
    return data


def _licence_ids(expr: str) -> list[str]:
    return [t for t in re.split(r"\s+(?:AND|OR|WITH)\s+|[()]", expr or "") if t.strip()]


def licence_ok(expr: str) -> bool:
    ids = [t.strip() for t in _licence_ids(expr)]
    return bool(ids) and all(i in OSI_LICENCES for i in ids)


def _deny_fields(entry: dict) -> str:
    parts = [entry.get(k) or "" for k in ("package", "command", "source_url", "registry_url", "link", "browse")]
    parts += [str(a) for a in (entry.get("args") or [])]
    return " ".join(str(x) for x in parts).lower()


def denylist_hit(entry: dict) -> str | None:
    blob = _deny_fields(entry)
    for tok in HOSTED_AI_DENYLIST:
        if tok in blob:
            return tok
    return None


REQUIRED_SERVER = ("id", "name", "package", "version_checked", "licence", "source_url", "runner",
                   "requires_tool", "command", "args", "purpose", "needs_signin")
REQUIRED_OFFER = ("id", "name", "why", "link", "steps", "needs_signin")


def validate_entry(s: dict) -> list[str]:
    """Problems with one server entry (empty list = fine)."""
    sid = s.get("id", "?")
    out = [f"{sid}: missing '{k}'" for k in REQUIRED_SERVER if k not in s]
    if not re.match(r"^[A-Za-z0-9_-]+$", str(s.get("id", ""))):
        out.append(f"{sid}: id must be letters, digits, '-' or '_'")
    if not licence_ok(str(s.get("licence", ""))):
        out.append(f"{sid}: licence '{s.get('licence')}' is not an OSI-approved SPDX expression")
    if not str(s.get("source_url", "")).startswith("https://"):
        out.append(f"{sid}: source_url must be an https URL")
    if not isinstance(s.get("args", []), list):
        out.append(f"{sid}: args must be a list")
    if s.get("needs_signin") is not False:
        out.append(f"{sid}: a server that needs sign-in belongs in signin_offers, not servers")
    hit = denylist_hit(s)
    if hit:
        out.append(f"{sid}: matches the hosted-AI denylist ('{hit}')")
    return out


def validate_catalog(data: dict) -> list[str]:
    problems: list[str] = []
    ids = [s.get("id") for s in data.get("servers", [])]
    if len(ids) != len(set(ids)):
        problems.append("duplicate server ids")
    for s in data.get("servers", []):
        problems += validate_entry(s)
    for o in data.get("signin_offers", []):
        oid = o.get("id", "?")
        problems += [f"offer {oid}: missing '{k}'" for k in REQUIRED_OFFER if k not in o]
        if o.get("auto_install"):
            problems.append(f"offer {oid}: sign-in offers are never auto-installed")
        hit = denylist_hit(o)
        if hit:
            problems.append(f"offer {oid}: matches the hosted-AI denylist ('{hit}')")
    return problems


# ---------------------------------------------------------------- host side
def host_config_path() -> Path:
    """Claude Code keeps user-scope MCP servers at the top level of ~/.claude.json."""
    return Path(os.path.expanduser("~")) / ".claude.json"


def read_host_servers() -> tuple[dict, str | None]:
    """Read-only. Returns (mcpServers dict, error-or-None)."""
    p = host_config_path()
    if not p.exists():
        return {}, None
    try:
        data = json.loads(p.read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError) as e:
        return {}, f"cannot read {p}: {e}"
    if not isinstance(data, dict):
        return {}, f"{p} top level is not an object"
    servers = data.get("mcpServers") or {}
    return (servers if isinstance(servers, dict) else {}), None


def find_tool(name: str) -> str | None:
    """PATH first; then the usual per-user install dirs (the host may not share this PATH)."""
    hit = shutil.which(name)
    if hit:
        return hit
    env = os.environ.get("COORDN8_RUNNER_DIRS")  # sandboxes/tests pin the fallback list
    dirs = env.split(os.pathsep) if env is not None else list(RUNNER_FALLBACK_DIRS)
    for d in [x for x in dirs if x]:
        cand = Path(os.path.expanduser(d)) / name
        if cand.is_file() and os.access(str(cand), os.X_OK):
            return str(cand)
    return None


def node_major() -> int | None:
    node = find_tool("node")
    if not node:
        return None
    try:
        r = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10,
                           stdin=subprocess.DEVNULL)
        m = re.match(r"v?(\d+)", (r.stdout or "").strip())
        return int(m.group(1)) if m else None
    except (OSError, subprocess.SubprocessError):
        return None


def _app_dirs() -> list[Path]:
    env = os.environ.get("COORDN8_APP_DIRS")
    raw = env.split(os.pathsep) if env is not None else ["/Applications", "~/Applications"]
    return [Path(os.path.expanduser(d)) for d in raw if d]


def app_present(spec: dict) -> tuple[bool, str]:
    """Is the desktop app a server drives installed? Returns (present, where-or-why)."""
    for b in spec.get("bins") or []:
        hit = shutil.which(b)
        if hit:
            return True, hit
    mac_app = spec.get("mac_app")
    if mac_app:
        for d in _app_dirs():
            if (d / mac_app).exists():
                return True, str(d / mac_app)
    looked = ", ".join([f"'{b}' on PATH" for b in spec.get("bins") or []] +
                       ([str(d / mac_app) for d in _app_dirs()] if mac_app else []))
    return False, f"{spec.get('name', 'required app')} is not installed (looked for {looked or 'nothing'})"


def server_entry(s: dict, command: str | None = None) -> dict:
    return {"type": "stdio", "command": command or s["command"], "args": list(s.get("args") or []), "env": {}}


def _same_endpoint(a: dict, b: dict) -> bool:
    return isinstance(a, dict) and isinstance(b, dict) and \
        a.get("command") == b.get("command") and list(a.get("args") or []) == list(b.get("args") or [])


def _package_base(pkg: str) -> str:
    """'@scope/name@1.2' -> '@scope/name'; 'name==1.2' / 'name@1.2' -> 'name'."""
    pkg = pkg.split("==")[0]
    if pkg.startswith("@"):
        head, _, rest = pkg[1:].partition("@")
        return "@" + head
    return pkg.split("@")[0]


def _runs_package(entry: dict, package: str) -> bool:
    base = _package_base(package).lower()
    for a in entry.get("args") or []:
        a = str(a).lower()
        if a == base or _package_base(a) == base:
            return True
    return False


# ------------------------------------------------------------------- record
def record_path() -> Path:
    return user_home() / RECORD_NAME


def load_record() -> dict:
    p = record_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("servers"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"schema": 1, "servers": {}}


def save_record(rec: dict) -> None:
    p = record_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".coordn8-", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(rec, indent=2, ensure_ascii=False) + "\n")
        os.replace(tmp, str(p))
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# --------------------------------------------------------------- CLI calls
def _last_line(*texts: str) -> str:
    for t in texts:
        lines = [ln.strip() for ln in (t or "").splitlines() if ln.strip()]
        if lines:
            return lines[-1][:200]
    return ""


def run_claude(claude: str, args: list[str], timeout: int) -> tuple[bool, str]:
    try:
        r = subprocess.run([claude, *args], capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return False, f"`claude {' '.join(args[:2])}` timed out after {timeout} s"
    except OSError as e:
        return False, f"could not run claude: {e}"
    if r.returncode != 0:
        why = _last_line(r.stderr, r.stdout) or "no message"
        return False, f"`claude {' '.join(args[:2])}` exited {r.returncode}: {why}"
    return True, _last_line(r.stdout)


def _cmdline(entry: dict) -> str:
    return " ".join([entry["command"], *[str(a) for a in entry["args"]]])


# ------------------------------------------------------------------ install
def plan_one(s: dict, host: dict, rec: dict, only: set | None) -> dict:
    """Decide what to do with one catalog server. Pure apart from read-only probes."""
    sid = s.get("id", "?")
    res = {"id": sid, "package": s.get("package"), "action": None, "reason": "", "entry": None}
    problems = validate_entry(s)
    if problems:
        res.update(action="skipped", reason="refused: " + "; ".join(p.split(": ", 1)[-1] for p in problems))
        return res
    tool = s.get("requires_tool") or s.get("command")
    tool_path = find_tool(tool)
    if not tool_path:
        res.update(action="skipped", reason=f"runner '{tool}' not found (install it, then re-run `coordn8 mcp install`)")
        return res
    min_node = s.get("min_node")
    if min_node:
        have = node_major()
        if have is None or have < int(min_node):
            res.update(action="skipped",
                       reason=f"needs node >= {min_node}, found {'none' if have is None else 'v' + str(have)}")
            return res
    if s.get("requires_app"):
        ok, where = app_present(s["requires_app"])
        if not ok:
            res.update(action="skipped", reason=where)
            return res
    # Use the bare runner name when it is on PATH (portable); the absolute path when it was
    # found only in a fallback dir, because the host may start servers with a shorter PATH.
    command = s["command"] if (s["command"] != tool or shutil.which(tool)) else tool_path
    entry = server_entry(s, command)
    res["entry"] = entry
    owned = rec["servers"].get(sid)
    if sid in host:
        if owned and _same_endpoint(host[sid], owned.get("entry", {})):
            if _same_endpoint(host[sid], entry):
                res.update(action="present", reason="already registered")
            else:
                res.update(action="update", reason=f"catalog version changed -> {s.get('version_checked')}")
        elif owned:
            res.update(action="present", reason="registered, and edited by you since we added it — left as you set it")
        else:
            res.update(action="present", reason="you already have a server with this name — left untouched")
        return res
    for name, other in host.items():
        if isinstance(other, dict) and _runs_package(other, s["package"]):
            res.update(action="present", reason=f"already registered by you as '{name}' — not duplicated")
            return res
    if owned and (only is None or sid not in only):
        res.update(action="skipped",
                   reason=f"removed by you after Coordn8 added it — `coordn8 mcp install --only {sid}` adds it back")
        return res
    res.update(action="install", reason="")
    return res


def install(data: dict, only: set | None = None, dry_run: bool = False,
            cli_timeout: int = CLI_TIMEOUT) -> dict:
    host, host_err = read_host_servers()
    rec = load_record()
    claude = find_tool("claude")
    method = "claude-cli" if claude else "json"
    results = []
    for s in data.get("servers", []):
        sid = s.get("id", "?")
        if only is not None and sid not in only:
            continue
        try:
            r = plan_one(s, host, rec, only)
            if r["action"] in ("install", "update"):
                entry = r["entry"]
                if host_err and method == "json":
                    r.update(action="failed", reason=f"host config unreadable, not touched: {host_err}")
                elif dry_run:
                    r["reason"] = (f"would run: claude mcp add --scope user {sid} -- {_cmdline(entry)}"
                                   if claude else f"would add mcpServers.{sid} to {host_config_path()}")
                    r["action"] = "would-" + r["action"]
                else:
                    ok, why = _register(claude, sid, entry, update=(r["action"] == "update"), timeout=cli_timeout)
                    if ok:
                        rec["servers"][sid] = {"entry": entry, "method": method, "config": str(host_config_path()),
                                               "package": s.get("package"), "version": s.get("version_checked"),
                                               "installed": time.strftime("%Y-%m-%dT%H:%M:%S")}
                        save_record(rec)
                        r["action"] = "installed" if r["action"] == "install" else "updated"
                        r["reason"] = f"{'claude mcp add' if claude else 'added to ' + str(host_config_path())}: {_cmdline(entry)}"
                    else:
                        r.update(action="failed", reason=why)
        except Exception as e:  # one server must never stop the loop
            r = {"id": sid, "package": s.get("package"), "action": "failed", "reason": f"{type(e).__name__}: {e}"}
        results.append(r)
    return _summary(results, method, dry_run)


def _register(claude: str | None, sid: str, entry: dict, update: bool, timeout: int) -> tuple[bool, str]:
    if claude:
        if update:
            ok, why = run_claude(claude, ["mcp", "remove", "--scope", "user", sid], timeout)
            if not ok:
                return False, why
        return run_claude(claude, ["mcp", "add", "--scope", "user", sid, "--", entry["command"],
                                   *[str(a) for a in entry["args"]]], timeout)
    try:
        host, err = read_host_servers()
        if err:
            return False, f"host config unreadable, not touched: {err}"
        cfg = host_config_path()
        has_parent = False
        if cfg.exists():
            raw = json.loads(cfg.read_text(encoding="utf-8") or "{}")
            has_parent = isinstance(raw, dict) and isinstance(raw.get("mcpServers"), dict)
        # Set the single key mcpServers.<id>. When the file (or its mcpServers object) does not
        # exist yet, create the parent with just our entry — merge_json's verify step treats a
        # newly created parent object as a change to another key, so it is set in one step.
        if has_parent:
            res = safe_write.merge_json(cfg, f"mcpServers.{sid}", entry)
        else:
            res = safe_write.merge_json(cfg, "mcpServers", {sid: entry})
        return True, res.get("action", "")
    except (safe_write.SafeWriteError, OSError, ValueError) as e:
        return False, f"could not edit {host_config_path()}: {e}"


def _summary(results: list, method: str, dry_run: bool) -> dict:
    counts: dict = {}
    for r in results:
        key = r["action"].replace("would-", "") if dry_run else r["action"]
        counts[key] = counts.get(key, 0) + 1
    return {"method": method, "dry_run": dry_run, "host_config": str(host_config_path()),
            "record": str(record_path()), "results": results, "counts": counts}


# ---------------------------------------------------------------- uninstall
def uninstall(only: set | None = None, dry_run: bool = False, cli_timeout: int = CLI_TIMEOUT) -> dict:
    rec = load_record()
    host, host_err = read_host_servers()
    claude = find_tool("claude")
    results = []
    for sid, info in list(rec["servers"].items()):
        if only is not None and sid not in only:
            continue
        r = {"id": sid, "package": info.get("package"), "action": None, "reason": ""}
        try:
            cur = host.get(sid)
            via_cli = bool(claude) and info.get("method") == "claude-cli"
            if cur is not None and not _same_endpoint(cur, info.get("entry", {})):
                r.update(action="kept", reason="you edited this entry since Coordn8 added it — left in place")
                if not dry_run:
                    rec["servers"].pop(sid, None)
            elif dry_run:
                r.update(action="would-remove", reason=f"mcpServers.{sid}")
            elif via_cli:
                ok, why = run_claude(claude, ["mcp", "remove", "--scope", "user", sid], cli_timeout)
                if not ok and cur is not None:
                    ok, why = _remove_json(sid)
                if ok or cur is None:
                    r.update(action="removed", reason=why if ok else "not in the host config any more")
                    rec["servers"].pop(sid, None)
                else:
                    r.update(action="failed", reason=why)
            elif cur is None:
                r.update(action="removed", reason="not in the host config any more")
                rec["servers"].pop(sid, None)
            elif host_err:
                r.update(action="failed", reason=f"host config unreadable, not touched: {host_err}")
            else:
                ok, why = _remove_json(sid)
                if ok:
                    r.update(action="removed", reason=why)
                    rec["servers"].pop(sid, None)
                else:
                    r.update(action="failed", reason=why)
        except Exception as e:
            r.update(action="failed", reason=f"{type(e).__name__}: {e}")
        results.append(r)
    if not dry_run:
        save_record(rec)
    return _summary(results, "claude-cli" if claude else "json", dry_run)


def _remove_json(sid: str) -> tuple[bool, str]:
    try:
        res = safe_write.remove_json(host_config_path(), f"mcpServers.{sid}")
        return True, res.get("action", "")
    except (safe_write.SafeWriteError, OSError, ValueError) as e:
        return False, f"could not edit {host_config_path()}: {e}"


# ------------------------------------------------------------------- status
def status(data: dict) -> dict:
    host, host_err = read_host_servers()
    rec = load_record()
    rows = []
    for s in data.get("servers", []):
        sid = s["id"]
        if sid in host:
            state = "registered (by Coordn8)" if sid in rec["servers"] else "registered (your own entry)"
        else:
            other = next((n for n, e in host.items() if isinstance(e, dict) and _runs_package(e, s["package"])), None)
            state = f"registered as '{other}' (your own)" if other else "not registered"
        rows.append({"id": sid, "package": s["package"], "state": state, "registered": state != "not registered"})
    return {"host_config": str(host_config_path()), "host_error": host_err, "claude_cli": find_tool("claude"),
            "servers": rows, "owned": sorted(rec["servers"]),
            "other_registered": sorted(n for n in host if n not in {s["id"] for s in data.get("servers", [])})}


# -------------------------------------------------------------------- offer
def read_answer(stream, timeout: float) -> str | None:
    """One line from `stream` within `timeout` seconds; None on timeout, EOF or a closed stream."""
    if stream is None or getattr(stream, "closed", False):
        return None
    try:
        fd = stream.fileno()
    except (AttributeError, OSError, ValueError):
        fd = None
    if fd is not None and os.name == "posix":
        import select
        try:
            ready, _, _ = select.select([fd], [], [], max(0.0, timeout))
        except (OSError, ValueError):
            return None
        if not ready:
            return None
        try:
            chunk = os.read(fd, 1024)
        except OSError:
            return None
        return chunk.decode("utf-8", "replace").splitlines()[0] if chunk else None
    box: list = []

    def _rd():
        try:
            box.append(stream.readline())
        except Exception:
            box.append(None)
    t = threading.Thread(target=_rd, daemon=True)
    t.start()
    t.join(max(0.0, timeout))
    return (box[0] or None) if box else None


def offer(data: dict, timeout: float = OFFER_TIMEOUT, force_prompt: bool = False,
          stdin=None, out=None) -> dict:
    stdin = sys.stdin if stdin is None else stdin
    out = sys.stdout if out is None else out
    offers = [o for o in data.get("signin_offers", []) if not denylist_hit(o)]
    if not offers:
        return {"answer": "none", "reason": "no sign-in offers", "offers": []}
    w = out.write
    w("\n[coordn8:mcp] Optional — these need YOUR sign-in, so Coordn8 never sets them up itself:\n")
    for o in offers:
        w(f"  - {o['name']}\n      why : {o['why']}\n")
        if o.get("command"):
            w(f"      run : {o['command']}\n")
        w(f"      link: {o['link']}\n")
        if o.get("limits"):
            w(f"      note: {o['limits']}\n")
    ids = [o["id"] for o in offers]
    try:
        tty = bool(stdin) and not getattr(stdin, "closed", False) and stdin.isatty()
    except (AttributeError, ValueError, OSError):
        tty = False
    if not tty and not force_prompt:
        w("[coordn8:mcp] non-interactive run: skipped (run `coordn8 mcp offer` in a terminal to see this again).\n")
        out.flush()
        return {"answer": "skip", "reason": "non-interactive", "offers": ids}
    w(f"Set any of these up now? [y/N] (skips in {int(timeout)} s) ")
    out.flush()
    ans = read_answer(stdin, timeout)
    if ans is None:
        w("\n[coordn8:mcp] no answer — skipped (nothing else is affected).\n")
        out.flush()
        return {"answer": "skip", "reason": "timeout-or-eof", "offers": ids}
    if ans.strip().lower() not in ("y", "yes"):
        w("[coordn8:mcp] skipped.\n")
        out.flush()
        return {"answer": "skip", "reason": "declined", "offers": ids}
    w("\n[coordn8:mcp] Steps (you do these — Coordn8 does not sign in for you):\n")
    for o in offers:
        w(f"  {o['name']}\n")
        for i, step in enumerate(o.get("steps") or [], 1):
            w(f"    {i}. {step}\n")
    out.flush()
    return {"answer": "yes", "reason": "steps printed", "offers": ids}


# ---------------------------------------------------------------------- CLI
def _print_results(res: dict, verb: str) -> None:
    width = max([len(r["id"]) for r in res["results"]] + [8])
    for r in res["results"]:
        print(f"  {r['id']:<{width}}  {r['action']:<13} {r['reason']}")
    c = res["counts"]
    order = ["installed", "updated", "present", "removed", "kept", "install", "update", "remove", "skipped", "failed"]
    parts = [f"{c[k]} {('would ' + k) if res['dry_run'] and k in ('install', 'update', 'remove') else k}"
             for k in order if c.get(k)]
    via = "claude mcp" if res["method"] == "claude-cli" else f"edits to {res['host_config']}"
    print(f"[coordn8:mcp] {verb}: {', '.join(parts) or 'nothing to do'} (via {via})")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Coordn8 MCP servers: list, status, install, uninstall, offer")
    ap.add_argument("--data", default=None, help="catalog JSON (default data/mcp_servers.json)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("list", "status", "validate"):
        p = sub.add_parser(name)
        p.add_argument("--json", action="store_true")
    for name in ("install", "uninstall"):
        p = sub.add_parser(name)
        p.add_argument("--only", default=None, help="comma-separated server ids")
        p.add_argument("--dry-run", action="store_true")
        p.add_argument("--json", action="store_true")
        p.add_argument("--cli-timeout", type=int, default=CLI_TIMEOUT)
    p = sub.add_parser("offer")
    p.add_argument("--timeout", type=float, default=OFFER_TIMEOUT)
    p.add_argument("--force-prompt", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd == "uninstall":  # works from the record alone, even if the catalog is gone
        only = {x.strip() for x in a.only.split(",") if x.strip()} if a.only else None
        res = uninstall(only, a.dry_run, max(1, a.cli_timeout))
        print(json.dumps(res, indent=1)) if a.json else _print_results(res, "uninstall")
        return EXIT_OK
    try:
        data = load_catalog(a.data)
    except CatalogError as e:
        print(f"[coordn8:mcp] ERROR: {e}", file=sys.stderr)
        return EXIT_ERROR

    if a.cmd == "validate":
        probs = validate_catalog(data)
        if a.json:
            print(json.dumps({"ok": not probs, "problems": probs}, indent=1))
        else:
            print("\n".join(probs) if probs else "[coordn8:mcp] catalog OK")
        return 1 if probs else EXIT_OK
    if a.cmd == "list":
        if a.json:
            print(json.dumps({"servers": data["servers"], "signin_offers": data["signin_offers"]}, indent=1))
            return EXIT_OK
        print(f"{'id':<20} {'sign-in':<8} {'runner':<8} {'licence':<26} purpose")
        for s in data["servers"]:
            req = f" [needs {s['requires_app']['name']}]" if s.get("requires_app") else ""
            print(f"{s['id']:<20} {'no':<8} {s['runner']:<8} {s['licence']:<26} {s['purpose'][:70]}{req}")
        for o in data["signin_offers"]:
            print(f"{o['id']:<20} {'yes':<8} {'-':<8} {'-':<26} {o['why'][:70]}")
        return EXIT_OK
    if a.cmd == "status":
        st = status(data)
        if a.json:
            print(json.dumps(st, indent=1))
            return EXIT_OK
        print(f"[coordn8:mcp] host config: {st['host_config']}" + (f"  ({st['host_error']})" if st["host_error"] else ""))
        print(f"[coordn8:mcp] claude CLI: {st['claude_cli'] or 'not on PATH (JSON fallback would be used)'}")
        for r in st["servers"]:
            print(f"  {r['id']:<20} {r['state']}")
        return EXIT_OK
    if a.cmd == "install":
        only = {x.strip() for x in a.only.split(",") if x.strip()} if a.only else None
        res = install(data, only, a.dry_run, max(1, a.cli_timeout))
        print(json.dumps(res, indent=1)) if a.json else _print_results(res, "install")
        return EXIT_OK
    if a.cmd == "offer":
        res = offer(data, max(0.0, min(a.timeout, 300.0)), a.force_prompt)
        if a.json:
            print(json.dumps(res))
        return EXIT_OK
    return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
