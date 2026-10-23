#!/usr/bin/env python3
"""J7 / F7 — MCP servers installed automatically; sign-in ones only offered, never blocking.

Fully sandboxed: HOME, COORDN8_HOME and CLAUDE_HOME point into a temp dir; PATH holds only a
stub directory (a `claude` stub that logs its argv and can simulate failure / a hang / the real
CLI's config write, plus inert `npx`, `uvx`, `node` stubs) and /usr/bin:/bin. The real `claude`
CLI, the real ~/.claude.json and the network are never used.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import PY, ROOT, Suite, run, sandbox_env, tmpdir  # noqa: E402

SCRIPT = "scripts/mcp/install_mcp.py"
DATA = ROOT / "data" / "mcp_servers.json"

# Independent of the installer's own list: hosted AI models, hosted search, hosted web readers,
# hosted docs-AI and hosted 3-D generation services that must never appear in the catalog.
TEST_DENY = ["openai.com", "api.openai", "chatgpt", "generativelanguage.googleapis", "gemini", "mistral.ai",
             "cohere.com", "perplexity", "api.x.ai", "deepseek", "groq", "replicate.com", "openrouter",
             "together.ai", "fireworks.ai", "huggingface.co", "elevenlabs", "meshy", "hyper3d", "tripo3d",
             "hunyuan", "three.ws", "mcp-for-blender", "web-reader", "webreader", "tavily", "exa.ai",
             "context7", "firecrawl", "jina.ai", "search.brave", "serpapi", "githubcopilot"]

STUB_CLAUDE = r'''#!{py}
import json, os, sys, time
argv = sys.argv[1:]
try:
    devnull = os.path.samestat(os.fstat(0), os.stat(os.devnull))
except OSError:
    devnull = False
with open(os.environ["C8_STUB_LOG"], "a") as f:
    f.write(json.dumps({{"argv": argv, "stdin_devnull": devnull}}) + "\n")
sid = argv[4] if len(argv) > 4 and argv[0] == "mcp" and argv[1] in ("add", "remove") else None
if sid and sid in os.environ.get("C8_STUB_FAIL", "").split(","):
    sys.stderr.write("Error: simulated failure for " + sid + "\n"); sys.exit(1)
if sid and sid in os.environ.get("C8_STUB_HANG", "").split(","):
    time.sleep(60)
if sid and os.environ.get("C8_STUB_WRITE") == "1":
    p = os.path.join(os.path.expanduser("~"), ".claude.json")
    d = json.load(open(p)) if os.path.exists(p) else {{}}
    m = d.setdefault("mcpServers", {{}})
    if argv[1] == "add":
        m[sid] = {{"type": "stdio", "command": argv[6], "args": argv[7:], "env": {{}}}}
    else:
        if sid not in m:
            sys.stderr.write("No MCP server found with name: " + sid + "\n"); sys.exit(1)
        m.pop(sid)
    json.dump(d, open(p, "w"), indent=2)
print("Added stdio MCP server " + str(sid) if argv[1:2] == ["add"] else "ok")
'''


def catalog() -> dict:
    return json.loads(DATA.read_text(encoding="utf-8"))


def write_exec(p: Path, text: str) -> None:
    p.write_text(text, encoding="utf-8")
    p.chmod(0o755)


def make_env(tmp: Path, name: str, claude=True, runners=("npx", "uvx", "node"), blender=True,
             node="v24.1.0", extra=None) -> dict:
    stub = tmp / f"stub_{name}"
    stub.mkdir(parents=True, exist_ok=True)
    if claude:
        write_exec(stub / "claude", STUB_CLAUDE.format(py=PY))
    for r in runners:
        write_exec(stub / r, f'#!/bin/sh\necho "{node}"\n' if r == "node" else "#!/bin/sh\nexit 0\n")
    apps = tmp / f"apps_{name}"
    apps.mkdir(exist_ok=True)
    if blender:
        (apps / "Blender.app").mkdir(exist_ok=True)
    env = sandbox_env(tmp)
    env.update({"PATH": f"{stub}{os.pathsep}/usr/bin{os.pathsep}/bin", "COORDN8_RUNNER_DIRS": "",
                "COORDN8_APP_DIRS": str(apps), "C8_STUB_LOG": str(tmp / f"claude_{name}.log")})
    env.pop("CLAUDE_CONFIG_DIR", None)
    if extra:
        env.update(extra)
    return env


def stub_calls(env: dict) -> list:
    p = Path(env["C8_STUB_LOG"])
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def host_json(env: dict) -> dict:
    p = Path(env["HOME"]) / ".claude.json"
    return json.loads(p.read_text()) if p.exists() else {}


def install(env: dict, *args, timeout=120):
    return run(SCRIPT, ["install", *args], env=env, timeout=timeout)


def results(proc) -> dict:
    try:
        return {r["id"]: r for r in json.loads(proc.stdout)["results"]}
    except (ValueError, KeyError):
        return {}


def expected_argv(s: dict) -> list:
    return ["mcp", "add", "--scope", "user", s["id"], "--", s["command"], *s["args"]]


def expected_entry(s: dict) -> dict:
    return {"type": "stdio", "command": s["command"], "args": s["args"], "env": {}}


def tree_state(root: Path) -> dict:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            st = p.stat()
            out[str(p.relative_to(root))] = (st.st_size, st.st_mtime_ns)
    return out


def main() -> int:
    s = Suite("mcp")
    cat = catalog()
    servers = cat.get("servers", [])
    offers = cat.get("signin_offers", [])
    ids = [x["id"] for x in servers]

    # ------------------------------------------------------------ catalog
    s.check("catalog parses as schema 1 with servers and signin_offers",
            cat.get("schema") == 1 and servers and offers, list(cat))
    r = run(SCRIPT, ["validate", "--json"])
    s.check("installer validate reports the shipped catalog clean", r.returncode == 0 and
            json.loads(r.stdout or "{}").get("ok") is True, r.stdout + r.stderr)
    osi = {"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "MPL-2.0", "GPL-2.0-or-later",
           "GPL-3.0-or-later", "LGPL-3.0-or-later"}
    bad = [x["id"] for x in servers
           if not x.get("licence") or not all(t in osi for t in x["licence"].replace("(", " ").replace(")", " ")
                                                 .replace(" AND ", " ").replace(" OR ", " ").split())
           or not str(x.get("source_url", "")).startswith("https://github.com/")
           or not x.get("version_checked") or not x.get("licence_evidence") or not x.get("registry_url")]
    s.check("every server has a verified OSI licence, licence evidence, a registry url, a source_url and a checked version",
            not bad, bad)
    unpinned = [x["id"] for x in servers if not any(x["version_checked"] in str(a) for a in x["args"])]
    s.check("every server pins the version that was checked in its command", not unpinned, unpinned)
    s.check("catalog covers browser automation, fetch for current docs, filesystem and a 3-D app bridge",
            {"coordn8-browser", "coordn8-fetch", "coordn8-filesystem", "coordn8-blender"} <= set(ids), ids)
    s.check("every server needs no sign-in, names its runner and says what the pipeline uses it for",
            all(x["needs_signin"] is False and x["requires_tool"] in ("npx", "uvx") and
                x["runner"] in ("npx -y", "uvx") and len(x.get("purpose", "")) > 40 for x in servers), "")
    blender = next((x for x in servers if x["id"] == "coordn8-blender"), {})
    s.check("the Blender bridge declares requires_app Blender",
            (blender.get("requires_app") or {}).get("name") == "Blender", blender.get("requires_app"))
    blob = json.dumps({"servers": servers, "signin_offers": offers}).lower()
    hits = [d for d in TEST_DENY if d in blob]
    s.check("no hosted-AI service anywhere in servers or sign-in offers (test denylist)", not hits, hits)
    s.check("packages judged unsafe are recorded as not listed and are absent from servers",
            cat.get("not_listed") and not any(n["package"].split(" ")[0] == x["package"]
                                              for n in cat["not_listed"] for x in servers), "")
    # F7 part: sign-in offers
    s.check("every sign-in offer has a why, an exact command or link, steps, and is never auto-installed",
            all(o.get("why") and (o.get("command") or o.get("link")) and o.get("steps") and
                o.get("needs_signin") is True and o.get("auto_install") is False for o in offers), "")
    s.check("sign-in offers include claude.ai connectors at claude.ai/customize/connectors",
            any(o.get("link") == "https://claude.ai/customize/connectors" for o in offers), "")
    s.check("the Figma offer gives the exact user-scope claude mcp add command",
            any(o.get("command") == "claude mcp add --scope user --transport http figma https://mcp.figma.com/mcp"
                for o in offers), "")

    with tmpdir() as td:
        tmp = Path(td)
        seeded = dict(cat)
        seeded["servers"] = servers + [{
            "id": "seeded-hosted", "name": "seeded", "package": "openai-mcp-bridge", "version_checked": "1.0.0",
            "licence": "MIT", "source_url": "https://github.com/example/x", "runner": "npx -y",
            "requires_tool": "npx", "command": "npx", "args": ["-y", "openai-mcp-bridge@1.0.0"],
            "purpose": "seeded defect", "needs_signin": False}]
        seeded_p = tmp / "seeded.json"
        seeded_p.write_text(json.dumps(seeded))
        r = run(SCRIPT, ["--data", str(seeded_p), "validate", "--json"])
        probs = json.loads(r.stdout or "{}").get("problems", [])
        s.check("NEGATIVE: a seeded hosted-AI entry is flagged by validate",
                r.returncode == 1 and any("seeded-hosted" in p and "denylist" in p for p in probs), probs)
        nonosi = dict(cat)
        nonosi["servers"] = [dict(servers[0], licence="SSPL-1.0")] + servers[1:]
        p2 = tmp / "nonosi.json"
        p2.write_text(json.dumps(nonosi))
        r = run(SCRIPT, ["--data", str(p2), "validate", "--json"])
        s.check("NEGATIVE: a seeded non-OSI licence is flagged by validate",
                r.returncode == 1 and "OSI" in r.stdout, r.stdout)

        # ------------------------------------------------- stub claude CLI
        env = make_env(tmp / "cli", "cli", extra={"C8_STUB_WRITE": "1"})
        r = install(env, "--json")
        calls = stub_calls(env)
        adds = [c["argv"] for c in calls if c["argv"][:2] == ["mcp", "add"]]
        s.check("stub CLI: every no-sign-in server registered with the exact `claude mcp add --scope user` argv",
                sorted(map(json.dumps, adds)) == sorted(json.dumps(expected_argv(x)) for x in servers),
                json.dumps(adds)[:300])
        s.check("stub CLI: claude is always called with stdin closed (DEVNULL)",
                calls and all(c["stdin_devnull"] for c in calls), calls[:1])
        res = results(r)
        s.check("stub CLI: every server reported installed and the run exits 0",
                r.returncode == 0 and all(res.get(i, {}).get("action") == "installed" for i in ids), r.stdout[-400:])
        rec = json.loads((Path(env["COORDN8_HOME"]) / "installed_mcp.json").read_text())
        s.check("stub CLI: the ownership record lists exactly the installed ids with method claude-cli",
                sorted(rec["servers"]) == sorted(ids) and
                all(v["method"] == "claude-cli" for v in rec["servers"].values()), list(rec["servers"]))
        r2 = install(env)
        s.check("stub CLI: install prints one line per server and a summary line",
                all(any(ln.strip().startswith(i) for ln in r2.stdout.splitlines()) for i in ids) and
                "[coordn8:mcp] install:" in r2.stdout, r2.stdout)
        s.check("stub CLI: a second install reports present and calls claude mcp add no more",
                "present" in r2.stdout and len([c for c in stub_calls(env) if c["argv"][:2] == ["mcp", "add"]])
                == len(servers), r2.stdout)
        r3 = run(SCRIPT, ["uninstall", "--json"], env=env)
        rem = [c["argv"] for c in stub_calls(env) if c["argv"][:2] == ["mcp", "remove"]]
        s.check("stub CLI: uninstall calls `claude mcp remove --scope user` for exactly our ids",
                sorted(a[4] for a in rem) == sorted(ids) and all(a[2:4] == ["--scope", "user"] for a in rem),
                rem)
        s.check("stub CLI: after uninstall the record is empty and the host config has none of our servers",
                not json.loads((Path(env["COORDN8_HOME"]) / "installed_mcp.json").read_text())["servers"] and
                not set(host_json(env).get("mcpServers", {})) & set(ids), r3.stdout[-300:])

        fail_id = "coordn8-fetch"
        envf = make_env(tmp / "fail", "fail", extra={"C8_STUB_FAIL": fail_id})
        r = install(envf, "--json")
        res = results(r)
        s.check("simulated failure of one server (stub exits 1) is reported failed with its reason",
                res.get(fail_id, {}).get("action") == "failed" and
                "simulated failure" in res.get(fail_id, {}).get("reason", ""), res.get(fail_id))
        s.check("simulated failure leaves every other server installed and the run exits 0",
                r.returncode == 0 and all(res.get(i, {}).get("action") == "installed" for i in ids if i != fail_id),
                {k: v.get("action") for k, v in res.items()})
        s.check("the failed server is not recorded as owned",
                fail_id not in json.loads((Path(envf["COORDN8_HOME"]) / "installed_mcp.json").read_text())["servers"], "")

        envh = make_env(tmp / "hang", "hang", extra={"C8_STUB_HANG": "coordn8-browser"})
        t0 = time.time()
        r = install(envh, "--json", "--cli-timeout", "2")
        el = time.time() - t0
        res = results(r)
        s.check("a hanging `claude mcp add` is cut at the CLI timeout, reported, and the loop continues",
                res.get("coordn8-browser", {}).get("action") == "failed" and
                "timed out" in res.get("coordn8-browser", {}).get("reason", "") and
                all(res.get(i, {}).get("action") == "installed" for i in ids if i != "coordn8-browser") and el < 20,
                f"{el:.1f}s {res.get('coordn8-browser')}")

        # ------------------------------------------------- JSON fallback
        envj = make_env(tmp / "json", "json", claude=False)
        cfg = Path(envj["HOME"]) / ".claude.json"
        user_cfg = {"numStartups": 7, "theme": "dark", "oauthAccount": {"emailAddress": "someone@example.com"},
                    "projects": {"/work/app": {"mcpServers": {"proj-tool": {"command": "proj", "args": []}},
                                               "allowedTools": ["Read"]}},
                    "mcpServers": {"my-own": {"type": "stdio", "command": "my-tool", "args": ["--flag"], "env": {}}}}
        original_bytes = json.dumps(user_cfg, indent=4).encode()
        cfg.write_bytes(original_bytes)
        r = install(envj, "--json")
        after = host_json(envj)
        s.check("JSON fallback: every no-sign-in server written to mcpServers.<id> with the exact command and args",
                all(after.get("mcpServers", {}).get(x["id"]) == expected_entry(x) for x in servers),
                json.dumps(after.get("mcpServers"))[:400])
        kept = {k: after.get(k) for k in user_cfg if k != "mcpServers"}
        s.check("JSON fallback: every one of the user's own keys and servers is kept unchanged",
                kept == {k: v for k, v in user_cfg.items() if k != "mcpServers"} and
                after["mcpServers"].get("my-own") == user_cfg["mcpServers"]["my-own"], json.dumps(after)[:300])
        backups = list((Path(envj["COORDN8_HOME"]) / "backups").rglob("*claude.json*"))
        s.check("JSON fallback: a backup of the original ~/.claude.json exists, byte-identical",
                any(b.read_bytes() == original_bytes for b in backups), [str(b) for b in backups])
        s.check("JSON fallback: no claude CLI call was attempted and the run exits 0",
                not stub_calls(envj) and r.returncode == 0 and json.loads(r.stdout)["method"] == "json", r.stdout[:200])
        before2 = cfg.read_bytes()
        r = install(envj, "--json")
        s.check("JSON fallback: a second install changes nothing and reports every server present",
                cfg.read_bytes() == before2 and all(v["action"] == "present" for v in results(r).values()),
                {k: v["action"] for k, v in results(r).items()})

        # user edits one of ours, removes another, then uninstall
        d = host_json(envj)
        d["mcpServers"]["coordn8-browser"]["args"].append("--user-flag")
        d["mcpServers"].pop("coordn8-fetch")
        cfg.write_text(json.dumps(d, indent=2))
        r = install(envj, "--json")
        res = results(r)
        s.check("a server the user removed after install is not re-added on the next install",
                res.get("coordn8-fetch", {}).get("action") == "skipped" and
                "removed by you" in res["coordn8-fetch"]["reason"] and
                "coordn8-fetch" not in host_json(envj)["mcpServers"], res.get("coordn8-fetch"))
        s.check("an entry the user edited after install is left as they set it",
                "--user-flag" in host_json(envj)["mcpServers"]["coordn8-browser"]["args"] and
                res.get("coordn8-browser", {}).get("action") == "present", res.get("coordn8-browser"))
        r = install(envj, "--json", "--only", "coordn8-fetch")
        s.check("install --only <id> adds back a server the user had removed",
                results(r).get("coordn8-fetch", {}).get("action") == "installed" and
                host_json(envj)["mcpServers"].get("coordn8-fetch") ==
                expected_entry(next(x for x in servers if x["id"] == "coordn8-fetch")), r.stdout[-300:])
        r = run(SCRIPT, ["uninstall", "--json"], env=envj)
        ures = results(r)
        final = host_json(envj)
        s.check("uninstall removes only our keys: the user's keys and servers remain",
                {k: final.get(k) for k in user_cfg if k != "mcpServers"} ==
                {k: v for k, v in user_cfg.items() if k != "mcpServers"} and
                final["mcpServers"].get("my-own") == user_cfg["mcpServers"]["my-own"] and
                not ({"coordn8-fetch", "coordn8-filesystem", "coordn8-blender"} & set(final["mcpServers"])),
                json.dumps(final.get("mcpServers")))
        s.check("uninstall keeps an entry the user edited and reports it kept",
                ures.get("coordn8-browser", {}).get("action") == "kept" and "coordn8-browser" in final["mcpServers"],
                ures.get("coordn8-browser"))

        envz = make_env(tmp / "fresh", "fresh", claude=False)
        r = install(envz, "--json")
        s.check("JSON fallback on a machine with no ~/.claude.json creates it holding only our servers",
                host_json(envz) == {"mcpServers": {x["id"]: expected_entry(x) for x in servers}}, r.stdout[-300:])
        envk = make_env(tmp / "nokey", "nokey", claude=False)
        (Path(envk["HOME"]) / ".claude.json").write_text('{"theme": "dark", "numStartups": 2}')
        r = install(envk, "--json")
        got = host_json(envk)
        s.check("JSON fallback on a ~/.claude.json with no mcpServers key adds it and keeps the rest",
                got.get("theme") == "dark" and got.get("numStartups") == 2 and
                set(got.get("mcpServers", {})) == set(ids), r.stdout[-300:])

        envd = make_env(tmp / "dry", "dry", claude=False)
        cfgd = Path(envd["HOME"]) / ".claude.json"
        cfgd.write_text('{"theme": "light"}')
        r = install(envd, "--dry-run", "--json")
        s.check("dry-run writes nothing (host config, record, backups) and says what it would do",
                cfgd.read_text() == '{"theme": "light"}' and not (Path(envd["COORDN8_HOME"]) / "installed_mcp.json").exists()
                and not (Path(envd["COORDN8_HOME"]) / "backups").exists()
                and all(v["action"] == "would-install" for v in results(r).values()), r.stdout[-300:])

        envs = make_env(tmp / "same", "same", claude=False)
        cfgs = Path(envs["HOME"]) / ".claude.json"
        mine = {"mcpServers": {"coordn8-browser": {"command": "my-browser", "args": []},
                               "fetch": {"type": "stdio", "command": "uvx", "args": ["mcp-server-fetch"], "env": {}}}}
        cfgs.write_text(json.dumps(mine))
        r = install(envs, "--json")
        res = results(r)
        got = host_json(envs)["mcpServers"]
        s.check("a same-named server of the user's is left untouched and reported present",
                got["coordn8-browser"] == mine["mcpServers"]["coordn8-browser"] and
                res["coordn8-browser"]["action"] == "present", res.get("coordn8-browser"))
        s.check("the same package registered under another name is not duplicated",
                "coordn8-fetch" not in got and res["coordn8-fetch"]["action"] == "present" and
                "'fetch'" in res["coordn8-fetch"]["reason"], res.get("coordn8-fetch"))

        envb = make_env(tmp / "broken", "broken", claude=False)
        cfgb = Path(envb["HOME"]) / ".claude.json"
        cfgb.write_text("{not json")
        r = install(envb, "--json")
        s.check("an unreadable ~/.claude.json is never rewritten; servers are reported failed with the reason",
                cfgb.read_text() == "{not json" and r.returncode == 0 and
                all(v["action"] == "failed" and "unreadable" in v["reason"] for v in results(r).values()),
                r.stdout[-300:])

        # ------------------------------------------------- skips
        envn = make_env(tmp / "noapp", "noapp", blender=False)
        r = install(envn, "--json")
        res = results(r)
        s.check("requires_app absent: the Blender bridge is skipped with a reason naming Blender",
                res.get("coordn8-blender", {}).get("action") == "skipped" and
                "Blender is not installed" in res["coordn8-blender"]["reason"] and
                not any(c["argv"][4:5] == ["coordn8-blender"] for c in stub_calls(envn)), res.get("coordn8-blender"))
        s.check("requires_app absent: the other servers still install",
                all(res.get(i, {}).get("action") == "installed" for i in ids if i != "coordn8-blender"), "")
        envr = make_env(tmp / "nouvx", "nouvx", runners=("npx", "node"))
        res = results(install(envr, "--json"))
        s.check("a missing runner (uvx) skips its server with the reason; npx servers install",
                res.get("coordn8-fetch", {}).get("action") == "skipped" and "'uvx' not found" in res["coordn8-fetch"]["reason"]
                and res.get("coordn8-browser", {}).get("action") == "installed", res.get("coordn8-fetch"))
        envo = make_env(tmp / "oldnode", "oldnode", node="v22.23.3")
        res = results(install(envo, "--json"))
        s.check("node older than a server's min_node skips it with the version reason",
                res.get("coordn8-filesystem", {}).get("action") == "skipped" and
                "needs node >= 24, found v22" in res["coordn8-filesystem"]["reason"], res.get("coordn8-filesystem"))
        envx = make_env(tmp / "deny", "deny", claude=False)
        r = run(SCRIPT, ["--data", str(seeded_p), "install", "--json"], env=envx)
        res = results(r)
        s.check("NEGATIVE: the installer refuses a hosted-AI entry even when the catalog lists it; the rest install",
                res.get("seeded-hosted", {}).get("action") == "skipped" and "denylist" in res["seeded-hosted"]["reason"]
                and "seeded-hosted" not in host_json(envx).get("mcpServers", {})
                and all(res.get(i, {}).get("action") == "installed" for i in ids), res.get("seeded-hosted"))
        badcat = tmp / "bad.json"
        badcat.write_text("{oops")
        r = run(SCRIPT, ["--data", str(badcat), "install"], env=envx)
        s.check("an unreadable catalog exits 3 with the reason (the only non-zero exit)",
                r.returncode == 3 and "cannot read" in r.stderr, r.stderr)

        # ------------------------------------------------- status / doctor
        r = run(SCRIPT, ["status", "--json"], env=envj)
        st = json.loads(r.stdout or "{}")
        s.check("status reads the host config and reports a user's server and ours",
                st.get("host_config") == str(Path(envj["HOME"]) / ".claude.json") and
                any(x["id"] == "coordn8-browser" and x["registered"] for x in st.get("servers", [])) and
                "my-own" in st.get("other_registered", []), r.stdout[:300])
        before = tree_state(tmp / "json")
        # PYTHONDONTWRITEBYTECODE stops the *interpreter* caching .pyc files under the sandbox HOME;
        # anything else that changes on disk would be the probe writing.
        r = run("scripts/doctor_checks/mcp.py", env={**envj, "PYTHONDONTWRITEBYTECODE": "1"})
        pr = json.loads(r.stdout or "{}")
        s.check("doctor probe lists registered vs available servers",
                pr.get("registered") == ["coordn8-browser"] and
                sorted(pr.get("available", [])) == sorted(i for i in ids if i != "coordn8-browser") and
                "registered" in pr.get("summary", ""), r.stdout[:400])
        s.check("doctor probe writes nothing", tree_state(tmp / "json") == before, "")
        code = ("import importlib.util,json,sys;sp=importlib.util.spec_from_file_location('m',sys.argv[1]);"
                "m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m);print(json.dumps(m.probe(sys.argv[2])))")
        r = subprocess.run([PY, "-c", code, str(ROOT / "scripts/doctor_checks/mcp.py"), str(badcat)],
                           capture_output=True, text=True, env={**os.environ, **envj}, timeout=60)
        s.check("doctor probe never raises on a broken catalog",
                r.returncode == 0 and "could not run" in json.loads(r.stdout or "{}").get("summary", ""), r.stderr[-200:])

        # ------------------------------------------------- offer
        envq = make_env(tmp / "offer", "offer")

        def offer_proc(args, stdin, preexec=None):
            return subprocess.Popen([PY, str(ROOT / SCRIPT), "offer", *args], stdin=stdin, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, env={**os.environ, **envq},
                                    preexec_fn=preexec)

        t0 = time.time()
        p = offer_proc(["--timeout", "3", "--force-prompt", "--json"], subprocess.PIPE)
        out, _ = p.communicate(input="", timeout=30)  # stdin closed at once: EOF
        el = time.time() - t0
        s.check("offer with stdin closed returns within timeout+2 s and defaults to skip",
                el < 5 and '"answer": "skip"' in out, f"{el:.1f}s {out[-200:]}")
        t0 = time.time()
        p = offer_proc(["--timeout", "3", "--json"], None, preexec=lambda: os.close(0))
        out, _ = p.communicate(timeout=30)
        el = time.time() - t0
        s.check("offer with no stdin at all (fd 0 closed) returns within timeout+2 s and skips",
                el < 5 and '"answer": "skip"' in out and p.returncode == 0, f"{el:.1f}s {out[-200:]}")
        t0 = time.time()
        p = offer_proc(["--timeout", "3", "--force-prompt", "--json"], subprocess.PIPE)  # pipe kept open, never written
        try:
            p.wait(timeout=30)
            el = time.time() - t0
            out = p.stdout.read()
        finally:
            p.stdin.close()
            p.stdout.close()
            p.stderr.close()
        s.check("offer with a pipe that never writes times out within timeout+2 s and defaults to skip",
                2.5 <= el < 5 and '"timeout-or-eof"' in out, f"{el:.1f}s {out[-200:]}")
        t0 = time.time()
        p = offer_proc(["--timeout", "20", "--json"], subprocess.PIPE)  # non-TTY, default 20 s
        out, _ = p.communicate(timeout=30)
        el = time.time() - t0
        s.check("offer on a non-TTY prints and skips immediately (no 20 s wait)",
                el < 3 and '"non-interactive"' in out, f"{el:.1f}s")
        s.check("offer prints every sign-in offer with its exact command or link",
                all(o["name"] in out and (o.get("command") or o["link"]) in out for o in offers), out[:400])
        p = offer_proc(["--timeout", "5", "--force-prompt", "--json"], subprocess.PIPE)
        out, _ = p.communicate(input="y\n", timeout=30)
        s.check("offer answered y prints the steps and performs nothing itself (no claude call)",
                '"answer": "yes"' in out and all(o["steps"][0] in out for o in offers) and not stub_calls(envq),
                out[-300:])
        p = offer_proc(["--timeout", "5", "--force-prompt", "--json"], subprocess.PIPE)
        out, _ = p.communicate(input="n\n", timeout=30)
        s.check("offer answered anything but y skips", '"declined"' in out, out[-200:])

    s.check("the CLI wires `coordn8 mcp` to this installer",
            "mcp: 'scripts/mcp/install_mcp.py'" in (ROOT / "bin" / "coordn8.js").read_text(encoding="utf-8"), "")
    return s.finish()


if __name__ == "__main__":
    sys.exit(main())
