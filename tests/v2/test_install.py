#!/usr/bin/env python3
"""Install / update / uninstall safety (J1 J3 J5, plus the step order for J4 J7 J8).

Everything runs against throw-away COPIES of the package (VERSION set to 2.0.0 / 2.0.1) with
COORDN8_HOME, CLAUDE_HOME, HOME and npm_config_cache pointed into a temp dir. The later
install steps (deps, voices, mcp, refs, signin) are replaced in the copies by stub scripts
that log how they were called, so the suite is fast and hermetic. A stub `npm` and a stub
`claude` sit first on PATH: the real ones are never invoked. Nothing is written outside the
temp dir.

The proposed shared files (install.sh, bin/coordn8.js) are tested from the package when they
already carry the new installer, else from the stream's proposal dir (C8_PROPOSED_DIR,
default <package>/../Coordn8_v2_handoff/_working/w1s1/).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import PY, ROOT, Suite, sandbox_env, tmpdir  # noqa: E402

IGNORE = shutil.ignore_patterns(".git", "*.zip", "__pycache__", "node_modules", "Coordn8",
                                "_build", "_research", ".DS_Store")
STEP_SCRIPTS = {"deps": "scripts/setup_env.py", "voices": "scripts/voice/voices.py",
                "mcp": "scripts/mcp/install_mcp.py", "refs": "scripts/refs/refresh.py"}

STUB = r'''#!/usr/bin/env python3
# install-step stub for tests. Declares "--non-interactive" so the installer passes it.
import json, os, sys, time
STEP = %(step)r
args = sys.argv[1:]
if STEP == "mcp" and args[:1] == ["offer"]:
    STEP = "signin"
info = {"step": STEP, "argv": args, "t": time.time(),
        "stdin_tty": bool(sys.stdin and sys.stdin.isatty()),
        "noninteractive_env": os.environ.get("COORDN8_NONINTERACTIVE"),
        "installed_json_present": os.path.isfile(os.path.join(os.environ.get("COORDN8_HOME", "/nonexistent"),
                                                              "installed.json"))}
mode = os.environ.get("C8_STUB_MODE_" + STEP.upper(), "")
if sys.stdin is not None and not info["stdin_tty"]:
    info["stdin_bytes"] = len(sys.stdin.read())   # blocks forever if handed an open pipe
log = os.environ.get("C8_STUB_LOG")
if log:
    with open(log, "a") as f:
        f.write(json.dumps(info) + "\n")
if mode == "hang":
    time.sleep(600)
if mode == "record":
    root = os.environ["COORDN8_PKG_ROOT"]
    sys.path.insert(0, os.path.join(root, "scripts"))
    from lib import safe_write
    cfg = os.environ["C8_STUB_CFG"]
    safe_write.merge_json(cfg, "mcpServers.coordn8-fetch", {"command": "uvx", "args": ["x"]})
    print("COORDN8_RECORD " + json.dumps({"json_keys": [{"file": cfg, "key": "mcpServers.coordn8-fetch"}]}))
if mode.startswith("exit"):
    print("stub %%s says: simulated problem" %% STEP)
    sys.exit(int(mode[4:]))
print("stub %%s done" %% STEP)
'''

NPM_STUB = """#!/bin/sh
echo "$@" >> "$C8_NPM_LOG"
if [ "$1" = "config" ] && [ "$2" = "get" ] && [ "$3" = "cache" ] && [ -n "$C8_STUB_NPM_CACHE" ]; then
  echo "$C8_STUB_NPM_CACHE"; exit 0
fi
exit 1
"""
CLAUDE_STUB = """#!/bin/sh
echo "$@" >> "$C8_CLAUDE_LOG"
exit 0
"""


# ------------------------------------------------------------------- helpers
def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def snap(root: Path, skip=()) -> dict:
    out = {}
    if not root.exists():
        return out
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        if any(rel == s or rel.startswith(s + os.sep) for s in skip):
            continue
        if p.is_symlink():
            out[rel] = "symlink->" + os.readlink(str(p))
        elif p.is_file():
            out[rel] = sha(p)
    return out


def make_pkg(base: Path, name: str, version: str, extras: dict | None = None, drop=()) -> Path:
    dst = base / name
    shutil.copytree(str(ROOT), str(dst), ignore=IGNORE, symlinks=True)
    (dst / "VERSION").write_text(version + "\n")
    for step, rel in STEP_SCRIPTS.items():
        p = dst / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(STUB % {"step": step})
    for rel, text in (extras or {}).items():
        p = dst / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    for rel in drop:
        p = dst / rel
        if p.exists():
            p.unlink()
    return dst


def make_env(t: Path, extra: dict | None = None) -> dict:
    t.mkdir(parents=True, exist_ok=True)
    e = {k: v for k, v in os.environ.items()
         if not k.startswith(("COORDN8_", "C8_", "npm_config_", "NPM_CONFIG_", "CLAUDE_"))}
    e.update(sandbox_env(t))
    stub = t / "stubbin"
    stub.mkdir(exist_ok=True)
    for name, body in (("npm", NPM_STUB), ("claude", CLAUDE_STUB)):
        (stub / name).write_text(body)
        (stub / name).chmod(0o755)
    e.update({"PATH": f"{stub}{os.pathsep}{e.get('PATH', '')}", "npm_config_cache": str(t / "npmcache"),
              "C8_NPM_LOG": str(t / "npm.log"), "C8_CLAUDE_LOG": str(t / "claude.log"),
              "C8_STUB_LOG": str(t / "steps.log")})
    (t / "proj").mkdir(exist_ok=True)
    e.update(extra or {})
    return e


def inst(pkg: Path, args, env, cwd: Path, timeout=180, stdin=subprocess.DEVNULL):
    try:
        return subprocess.run([PY, str(pkg / "scripts" / "install" / "installer.py"), *map(str, args)],
                              capture_output=True, text=True, env=env, cwd=str(cwd), timeout=timeout,
                              stdin=stdin)
    except subprocess.TimeoutExpired as ex:
        return subprocess.CompletedProcess(ex.cmd, 124, str(ex.stdout or ""), f"TIMEOUT {timeout}s")


def out(r) -> str:
    return (r.stdout or "") + (r.stderr or "")


def installed(env) -> dict:
    p = Path(env["COORDN8_HOME"]) / "installed.json"
    return json.loads(p.read_text()) if p.exists() else {}


def steplog(env) -> list:
    p = Path(env["C8_STUB_LOG"])
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []


def seed_cache(cache: Path) -> dict:
    """A decoy npm cache: two Coordn8 entries, unrelated entries, _cacache, _logs, a symlink."""
    npx = cache / "_npx"
    (npx / "aaa111" / "node_modules" / "coordn8").mkdir(parents=True)
    (npx / "aaa111" / "node_modules" / "coordn8" / "package.json").write_text('{"name":"coordn8"}')
    (npx / "aaa111" / "package.json").write_text('{"dependencies":{"coordn8":"github:ompandey98/coordn8"}}')
    (npx / "bbb222").mkdir(parents=True)
    (npx / "bbb222" / "package.json").write_text('{"dependencies":{"x":"github:ompandey98/coordn8#main"}}')
    (npx / "ccc333" / "node_modules" / "left-pad").mkdir(parents=True)
    (npx / "ccc333" / "package.json").write_text('{"dependencies":{"left-pad":"^1.3.0"}}')
    (npx / "ccc333" / "node_modules" / "left-pad" / "index.js").write_text("module.exports=1\n")
    (npx / "ddd444").mkdir(parents=True)
    (npx / "ddd444" / "package.json").write_text('{"dependencies":{"coordn8-helper":"1.0.0"}}')
    (cache / "_cacache" / "index-v5" / "ab").mkdir(parents=True)
    (cache / "_cacache" / "index-v5" / "ab" / "cd").write_text("cache index entry\n")
    (cache / "_cacache" / "content-v2").mkdir(parents=True)
    (cache / "_cacache" / "content-v2" / "blob").write_bytes(os.urandom(256))
    (cache / "_logs").mkdir()
    (cache / "_logs" / "debug.log").write_text("log\n")
    outside = cache.parent / "outside_npx_target"
    (outside / "node_modules" / "coordn8").mkdir(parents=True)
    (outside / "node_modules" / "coordn8" / "keep.txt").write_text("not inside _npx\n")
    os.symlink(str(outside), str(npx / "eee555"))
    return {"keep": ["_npx/ccc333", "_npx/ddd444", "_npx/eee555", "_cacache", "_logs"],
            "gone": ["_npx/aaa111", "_npx/bbb222"], "outside": outside}


def cache_kept(cache: Path, before: dict) -> bool:
    now = snap(cache)
    keep = {k: v for k, v in before.items() if not k.startswith(("_npx/aaa111", "_npx/bbb222", "_npx/fff666"))}
    return all(now.get(k) == v for k, v in keep.items())


def one_block(text: str) -> bool:
    return text.count("<!-- COORDN8:BEGIN v") == 1 and text.count("<!-- COORDN8:END -->") == 1


def user_text_intact(text: str, user: str) -> bool:
    """The user's text must be byte-for-byte present, outside our block, exactly as written."""
    if one_block(text) or "<!-- COORDN8:BEGIN v" in text:
        head = text.split("<!-- COORDN8:BEGIN v", 1)[0]
        tail = text.split("<!-- COORDN8:END -->", 1)[1] if "<!-- COORDN8:END -->" in text else ""
        return (head + tail).rstrip("\n") == user.rstrip("\n") and head.startswith(user.rstrip("\n"))
    return text == user


def backups(env) -> list:
    b = Path(env["COORDN8_HOME"]) / "backups"
    return [p for p in b.rglob("*") if p.is_file()] if b.exists() else []


def find_tool(name: str) -> str | None:
    p = shutil.which(name)
    return os.path.realpath(p) if p else None


def nopython_path(t: Path) -> str | None:
    """A PATH holding node, bash and the coreutils the shell installer uses — and NO python."""
    d = t / "nopy_bin"
    d.mkdir(exist_ok=True)
    need = ["bash", "sh", "cat", "cp", "mkdir", "rm", "mv", "dirname", "basename", "date", "grep", "sed",
            "awk", "head", "tail", "tr", "cmp", "mktemp", "rmdir", "env", "chmod", "ls"]
    opt = ["shasum", "perl", "sha256sum"]
    for n in need + opt:
        src = find_tool(n)
        if src is None:
            if n in need:
                return None
            continue
        if not (d / n).exists():
            os.symlink(src, str(d / n))
    node = shutil.which("node")
    if node and not (d / "node").exists():
        os.symlink(os.path.realpath(node), str(d / "node"))
    for name, body in (("npm", NPM_STUB), ("claude", CLAUDE_STUB)):
        (d / name).write_text(body)
        (d / name).chmod(0o755)
    return str(d)


def proposed_files() -> tuple[Path | None, Path | None, str]:
    sh, js = ROOT / "install.sh", ROOT / "bin" / "coordn8.js"
    marker = "scripts/install/installer.py"
    if marker in sh.read_text() and marker in js.read_text():
        return sh, js, "package (merged)"
    d = Path(os.environ.get("C8_PROPOSED_DIR", str(ROOT.parent / "Coordn8_v2_handoff" / "_working" / "w1s1")))
    psh, pjs = d / "install.sh.proposed", d / "coordn8.js.proposed"
    if psh.is_file() and pjs.is_file():
        return psh, pjs, f"proposal dir {d}"
    return None, None, "no proposed or merged installer wrappers found"


V15_CASES = ["'init'", "'update'", "'upgrade'", "'uninstall'", "'print'", "'setup'", "'detect'", "'gates'",
             "'clean'", "'disk'", "'doctor'", "'perf'", "'quality'", "'orchestrate'", "'verify'", "'check'",
             "'version'", "'--version'", "'-v'", "'help'", "'--help'", "'-h'"]
V15_PY_COMMANDS = {"index": "scripts/index/build_index.py", "find": "scripts/index/query.py",
                   "memory": "scripts/memory/store.py", "palette": "scripts/design/palette.py",
                   "diagram": "scripts/design/pick_diagram.py", "distill": "scripts/design/distill.py",
                   "voices": "scripts/voice/voices.py", "mcp": "scripts/mcp/install_mcp.py",
                   "refs": "scripts/refs/refresh.py", "reflect": "scripts/reflect.py", "bench": "bench/run.py"}


# ======================================================================= suite
def main() -> int:
    s = Suite("install")
    t0 = time.time()
    with tmpdir() as d:
        base = Path(d)
        pkgA = make_pkg(base, "pkgA", "2.0.0", extras={
            "skills/coordn8-frame-review/SKILL.md": "---\nname: coordn8-frame-review\n---\nreview\n",
            "agents/coordn8-reader.md": "---\nname: coordn8-reader\n---\nreader\n",
            "agents/not-ours.md": "not a coordn8 agent\n"})
        pkgB = make_pkg(base, "pkgB", "2.0.1", extras={
            "skills/coordn8-frame-review/SKILL.md": "---\nname: coordn8-frame-review\n---\nreview v2\n"},
            drop=("agents/coordn8-reader.md",))

        # ---------------------------------------------------------- J1: versions + cache
        t = base / "j1"
        env = make_env(t)
        ch, proj, cache = Path(env["CLAUDE_HOME"]), t / "proj", Path(env["npm_config_cache"])
        (ch / "skills" / "coordn8").mkdir(parents=True)
        (ch / "skills" / "coordn8" / "PLAYBOOK.md").write_text("stale split-layout brain\n")
        (ch / "commands").mkdir(parents=True)
        (ch / "commands" / "coordn8-brain.md").write_text("stale\n")
        (ch / "commands" / "mine.md").write_text("the user's own command\n")
        (ch / "skills" / "myskill").mkdir(parents=True)
        (ch / "skills" / "myskill" / "SKILL.md").write_text("the user's own skill\n")
        user_claude = snap(ch, skip=("skills/coordn8", "commands/coordn8.md", "commands/coordn8-brain.md",
                                     "skills/coordn8-frame-review", "agents"))
        seeded = seed_cache(cache)
        cache_before = snap(cache)
        outside_before = snap(seeded["outside"])
        r1 = inst(pkgA, ["init", "--target", "claude,codex", "--steps", "package", "--project-dir", proj], env, proj)
        o1 = out(r1)
        s.check("init: first install exits 0", r1.returncode == 0, o1[-600:])
        s.check("init: first install prints 'installed (none) → v2.0.0'", "installed (none) → v2.0.0" in o1, o1[-400:])
        rec = installed(env)
        s.check("installed.json: records version, commit, date and a manifest",
                rec.get("version") == "2.0.0" and isinstance(rec.get("commit"), str) and rec.get("commit")
                and re.match(r"\d{4}-\d\d-\d\dT", str(rec.get("date"))) and len(rec.get("manifest") or {}) >= 4,
                json.dumps({k: rec.get(k) for k in ("version", "commit", "date")}))
        man = rec.get("manifest") or {}
        s.check("installed.json: every manifest sha256 matches the installed file",
                man and all(Path(p).is_file() and sha(Path(p)) == h for p, h in man.items()),
                str([p for p, h in man.items() if not Path(p).is_file() or sha(Path(p)) != h][:3]))
        s.check("installed.json: the manifest lists package-owned files only (no user file, no merged AGENTS.md)",
                all("mine.md" not in p and "myskill" not in p and not p.endswith("AGENTS.md") for p in man),
                str(list(man)[:8]))
        skill = ch / "skills" / "coordn8" / "SKILL.md"
        s.check("claude: skill + command installed byte-identical to the package adapters",
                skill.is_file() and skill.read_bytes() == (pkgA / "adapters/claude-code/SKILL.md").read_bytes()
                and (ch / "commands/coordn8.md").read_bytes()
                == (pkgA / "adapters/claude-code/commands/coordn8.md").read_bytes(), "")
        s.check("claude: installed skill embeds the full brain (monolithic, not a pointer)",
                skill.is_file() and "COORDN8:BRAIN:BEGIN" in skill.read_text()
                and skill.stat().st_size > 100_000, str(skill.stat().st_size if skill.exists() else 0))
        s.check("claude: stale split-layout leftovers are removed",
                not (ch / "skills/coordn8/PLAYBOOK.md").exists() and not (ch / "commands/coordn8-brain.md").exists(), "")
        s.check("claude: package skills/ tree and agents/coordn8-*.md are installed; other agents are not",
                (ch / "skills/coordn8-frame-review/SKILL.md").is_file() and (ch / "agents/coordn8-reader.md").is_file()
                and not (ch / "agents/not-ours.md").exists(), "")
        s.check("claude: the user's own skills and commands are untouched",
                snap(ch, skip=("skills/coordn8", "commands/coordn8.md", "commands/coordn8-brain.md",
                               "skills/coordn8-frame-review", "agents")) == user_claude, "")
        s.check("npm cache: only the Coordn8 _npx entries are removed (node_modules/coordn8 and a coordn8 dep)",
                not (cache / "_npx/aaa111").exists() and not (cache / "_npx/bbb222").exists(),
                str(sorted(os.listdir(cache / "_npx"))))
        s.check("npm cache: unrelated _npx entries, _cacache and _logs are byte-identical",
                cache_kept(cache, cache_before), "")
        s.check("npm cache: a look-alike package name (coordn8-helper) is kept",
                (cache / "_npx/ddd444/package.json").is_file(), "")
        s.check("npm cache: a symlinked _npx entry is neither followed nor removed",
                (cache / "_npx/eee555").is_symlink() and snap(seeded["outside"]) == outside_before, "")
        npm_log = Path(env["C8_NPM_LOG"]).read_text() if Path(env["C8_NPM_LOG"]).exists() else ""
        s.check("npm cache: the global 'npm cache clean' is never invoked", "clean" not in npm_log, npm_log)
        # seeded defect: the v1.5.0 behaviour (wipe the whole cache) is caught by the same predicate
        fake = base / "fakecache"
        seed_cache(fake)
        fb = snap(fake)
        shutil.rmtree(str(fake / "_cacache"))
        shutil.rmtree(str(fake / "_npx" / "ccc333"))
        s.check("negative: a global cache wipe (v1.5.0 'npm cache clean --force') is detected",
                not cache_kept(fake, fb), "")

        # second init from a NEWER package copy, with a fresh Coordn8 cache entry to remove
        (cache / "_npx" / "fff666" / "node_modules" / "coordn8").mkdir(parents=True)
        r2 = inst(pkgB, ["update", "--target", "claude,codex", "--steps", "package", "--project-dir", proj], env, proj)
        o2 = out(r2)
        s.check("update: second install from a newer copy prints '2.0.0 → 2.0.1'",
                r2.returncode == 0 and "installed v2.0.0 → v2.0.1" in o2, o2[-500:])
        rec2 = installed(env)
        s.check("installed.json: says 2.0.1 after the update (previous_version 2.0.0)",
                rec2.get("version") == "2.0.1" and rec2.get("previous_version") == "2.0.0", str(rec2.get("version")))
        s.check("update: package-owned files are overwritten with the new version",
                "Coordn8 v2.0.1" in skill.read_text() and
                (ch / "skills/coordn8-frame-review/SKILL.md").read_text().endswith("review v2\n"), "")
        s.check("update: a package file the new version no longer ships is removed",
                not (ch / "agents/coordn8-reader.md").exists(), "")
        s.check("update: the new Coordn8 cache entry is removed, everything else still byte-identical",
                not (cache / "_npx/fff666").exists() and cache_kept(cache, cache_before), "")
        r3 = inst(pkgB, ["init", "--target", "claude", "--steps", "package"], env, proj)
        s.check("reinstall of the same version says so and succeeds",
                r3.returncode == 0 and "installed v2.0.1 → v2.0.1 (reinstalled" in out(r3), out(r3)[-300:])
        # cache dir discovered through `npm config get cache` when the env var is absent
        env_nc = dict(env)
        env_nc.pop("npm_config_cache")
        alt = t / "altcache"
        (alt / "_npx" / "zzz" / "node_modules" / "coordn8").mkdir(parents=True)
        (alt / "_npx" / "yyy").mkdir(parents=True)
        env_nc["C8_STUB_NPM_CACHE"] = str(alt)
        r4 = inst(pkgB, ["init", "--target", "claude", "--steps", "package"], env_nc, proj)
        s.check("npm cache: located via `npm config get cache` when npm_config_cache is unset",
                r4.returncode == 0 and not (alt / "_npx/zzz").exists() and (alt / "_npx/yyy").is_dir(), out(r4)[-300:])

        # ---------------------------------------------- J2: user data byte-identical
        t = base / "j2"
        env = make_env(t)
        uh, proj = Path(env["COORDN8_HOME"]), t / "proj"
        entry = {"id": "m-1", "type": "preference", "text": "teal", "date": "2026-10-01", "scope": "global",
                 "confidence": "high", "source": "chat", "source_quote": "teal please", "supersedes": None,
                 "source_hash": None, "source_path": None}
        seed = {"memory/entries.jsonl": json.dumps(entry) + "\n",
                "profiles/x.json": '{"brand":"x"}\n', "library/inventions.jsonl": '{"name":"ribbon"}\n',
                "schema.json": '{"schema": 2, "created": "seeded", "migrated": [], "mine": true}\n',
                "refs/index.json": '{"llms.txt":"2026-10-01"}\n', "voices/v/model.bin": "MODEL" * 50,
                "telemetry/run.jsonl": '{"run":1}\n'}
        for rel, txt in seed.items():
            (uh / rel).parent.mkdir(parents=True, exist_ok=True)
            (uh / rel).write_text(txt)
        pc = proj / "Coordn8"
        for rel, txt in {"demo.html": "<html>mine</html>\n", "_research/decisions.md": "# decisions\n",
                         "_build/plan.json": "{}\n", "video/s1.html": "<canvas></canvas>\n"}.items():
            (pc / rel).parent.mkdir(parents=True, exist_ok=True)
            (pc / rel).write_text(txt)
        uh0, pc0 = snap(uh), snap(pc)

        def user_same():
            return snap(uh, skip=("installed.json", "backups")) == uh0 and snap(pc) == pc0

        ra = inst(pkgA, ["init", "--steps", "package", "--project-dir", proj], env, proj)
        s.check("user data: memory/profiles/library/schema + project Coordn8/ byte-identical after init",
                ra.returncode == 0 and user_same(), out(ra)[-300:])
        mig = installed(env).get("migration") or {}
        s.check("migration: a current-schema user home is left alone (no-op, no backup)",
                mig.get("status") == "current" and not backups(env), json.dumps(mig))
        rb = inst(pkgB, ["update", "--steps", "package", "--project-dir", proj], env, proj)
        s.check("user data: byte-identical after update", rb.returncode == 0 and user_same(), out(rb)[-300:])
        rc = inst(pkgB, ["uninstall"], env, proj)
        s.check("user data: byte-identical after uninstall", rc.returncode == 0 and user_same(), out(rc)[-300:])
        s.check("user data: no file in the user home was added or changed except installed.json and backups/",
                set(snap(uh, skip=("installed.json", "backups"))) == set(uh0), "")
        (pc / "demo.html").write_text("<html>changed</html>\n")
        s.check("negative: the byte-identity comparator does detect a changed user file", not user_same(), "")

        # schema-1 fixture home -> migrated to schema 2 by init, backup first (J6 wiring)
        t = base / "j6"
        env = make_env(t)
        uh = Path(env["COORDN8_HOME"])
        (uh / "memory").mkdir(parents=True, exist_ok=True)
        legacy_prefs = ["prefer teal accents", "keep videos under four minutes"]
        (uh / "memory" / "preferences.json").write_text(json.dumps(legacy_prefs) + "\n")
        (uh / "schema.json").write_text('{"schema": 1}\n')
        r = inst(pkgA, ["init", "generic", "--steps", "package"], env, t / "proj")
        schema_now = json.loads((uh / "schema.json").read_text())
        mig = installed(env).get("migration") or {}
        bdirs = [p for p in (uh / "backups").iterdir() if p.is_dir() and "pre-migrate-v1-to-v2" in p.name] \
            if (uh / "backups").exists() else []
        ents = [json.loads(x) for x in (uh / "memory" / "entries.jsonl").read_text().splitlines() if x.strip()] \
            if (uh / "memory" / "entries.jsonl").exists() else []
        s.check("migration: init migrates a schema-1 home to schema 2 with a backup present",
                r.returncode == 0 and schema_now.get("schema") == 2 and mig.get("status") == "migrated" and bdirs
                and {e.get("source_quote") for e in ents} >= set(legacy_prefs)
                and (uh / "memory" / "preferences.json").is_file(), f"{json.dumps(mig)[:300]} {out(r)[-300:]}")
        env = make_env(base / "j6b", {"COORDN8_FAIL_STEP": "migrate"})
        uh = Path(env["COORDN8_HOME"])
        (uh / "memory").mkdir(parents=True, exist_ok=True)
        (uh / "memory" / "preferences.json").write_text(json.dumps(legacy_prefs) + "\n")
        (uh / "schema.json").write_text('{"schema": 1}\n')
        before = snap(uh)
        r = inst(pkgA, ["init", "generic", "--steps", "package"], env, base / "j6b" / "proj")
        s.check("migration: a failed migration is reported and skipped, the install still succeeds, data untouched",
                r.returncode == 0 and "migration skipped" in out(r)
                and snap(uh, skip=("installed.json", "backups")) == before, out(r)[-300:])

        # ----------------------------------------------------------- J3: AGENTS.md merge
        t = base / "j3"
        env = make_env(t)
        proj = t / "proj"
        user_agents = "# My project rules\n\nAlways run `make test` before committing.\nNever touch vendor/.\n"
        ag = proj / "AGENTS.md"
        ag.write_text(user_agents)
        r = inst(pkgA, ["init", "codex", "--steps", "package"], env, proj)
        txt = ag.read_text()
        s.check("codex: install keeps the user's AGENTS.md content intact",
                r.returncode == 0 and user_text_intact(txt, user_agents), out(r)[-300:])
        s.check("codex: install leaves exactly one Coordn8 block in AGENTS.md", one_block(txt), "")
        s.check("codex: the Coordn8 block carries the full brain", "COORDN8:BRAIN:BEGIN" in txt and len(txt) > 100_000, "")
        bks = backups(env)
        s.check("codex: a backup of the user's original AGENTS.md exists",
                any(b.read_text() == user_agents for b in bks), str([str(b) for b in bks]))
        r = inst(pkgB, ["init", "codex", "--steps", "package"], env, proj)
        txt = ag.read_text()
        s.check("codex: reinstall keeps user content intact with exactly one (updated) block",
                r.returncode == 0 and user_text_intact(txt, user_agents) and one_block(txt)
                and "<!-- COORDN8:BEGIN v2.0.1 -->" in txt and txt.count("COORDN8:BRAIN:BEGIN") == 1, "")
        r = inst(pkgB, ["uninstall"], env, proj)
        s.check("codex: uninstall restores the user's AGENTS.md exactly", ag.read_text() == user_agents,
                repr(ag.read_text()[:200]))
        # no trailing newline + CRLF-free odd ending
        user2 = "line one\nline two without newline"
        ag.write_text(user2)
        inst(pkgA, ["init", "codex", "--steps", "package"], env, proj)
        inst(pkgA, ["uninstall"], env, proj)
        s.check("codex: a user AGENTS.md without a trailing newline also round-trips exactly",
                ag.read_text() == user2, repr(ag.read_text()[-60:]))
        ag.unlink()
        inst(pkgA, ["init", "codex", "--steps", "package"], env, proj)
        created = ag.is_file() and one_block(ag.read_text())
        inst(pkgA, ["uninstall"], env, proj)
        s.check("codex: an AGENTS.md created by install is removed entirely by uninstall",
                created and not ag.exists(), "")
        # a whole-file v1.5.0 AGENTS.md (ours, no user text) is converted, not doubled
        legacy = (pkgA / "adapters/codex/AGENTS.md").read_text().replace("Coordn8 v2.0.0", "Coordn8 v1.5.0")
        ag.write_text(legacy)
        inst(pkgA, ["init", "codex", "--steps", "package"], env, proj)
        txt = ag.read_text()
        s.check("codex: a v1.5.0 whole-file AGENTS.md becomes one block (brain not embedded twice), backed up",
                one_block(txt) and txt.count("COORDN8:BRAIN:BEGIN") == 1
                and any(b.read_text() == legacy for b in backups(env)), "")
        inst(pkgA, ["uninstall"], env, proj)
        s.check("codex: that converted file is removed by uninstall", not ag.exists(), "")
        # cursor / kiro / generic: a user file at our path is merged, not replaced
        cur = proj / ".cursor" / "rules" / "coordn8.mdc"
        cur.parent.mkdir(parents=True)
        user_mdc = "---\ndescription: my own rule that happens to share the name\n---\nbe nice\n"
        cur.write_text(user_mdc)
        r = inst(pkgA, ["init", "cursor,kiro,generic", "--steps", "package"], env, proj)
        s.check("cursor: an existing user file is block-merged, not overwritten",
                r.returncode == 0 and user_text_intact(cur.read_text(), user_mdc) and one_block(cur.read_text()), "")
        kiro = proj / ".kiro/steering/coordn8.md"
        s.check("kiro/generic: absent targets are created as package-owned files",
                kiro.is_file() and kiro.read_bytes() == (pkgA / "adapters/kiro/coordn8.md").read_bytes()
                and (proj / "COORDN8_PROMPT.md").is_file(), "")
        inst(pkgA, ["uninstall"], env, proj)
        s.check("cursor: uninstall restores the user's rule file exactly; kiro/generic files are gone",
                cur.read_text() == user_mdc and not kiro.exists() and not (proj / "COORDN8_PROMPT.md").exists()
                and not (proj / ".kiro").exists(), "")
        # seeded defect: the v1.5.0 codex install (`cp adapters/codex/AGENTS.md ./AGENTS.md`)
        ag.write_text(user_agents)
        shutil.copyfile(str(pkgA / "adapters/codex/AGENTS.md"), str(ag))
        s.check("negative: a v1.5.0-style overwrite of AGENTS.md is detected as data loss",
                not user_text_intact(ag.read_text(), user_agents), "")
        ag.unlink()

        # ----------------------------------------------------------------- J5: uninstall
        t = base / "j5"
        env = make_env(t)
        uh, ch, proj = Path(env["COORDN8_HOME"]), Path(env["CLAUDE_HOME"]), t / "proj"
        (ch / "commands").mkdir(parents=True)
        (ch / "commands" / "mine.md").write_text("mine\n")
        (proj / "README.md").write_text("project readme\n")
        r = inst(pkgA, ["init", "claude,generic", "--steps", "package"], env, proj)
        man = installed(env).get("manifest") or {}
        gen = proj / "COORDN8_PROMPT.md"
        gen.write_text(gen.read_text() + "\nmy note added after install\n")
        edited = gen.read_text()
        r = inst(pkgA, ["uninstall"], env, proj)
        s.check("uninstall: removes every package-owned file in the manifest",
                r.returncode == 0 and man and not any(Path(p).exists() for p in man), out(r)[-300:])
        s.check("uninstall: leaves the user's own files (CLAUDE_HOME/commands/mine.md, project README)",
                (ch / "commands/mine.md").read_text() == "mine\n" and (proj / "README.md").is_file(), "")
        s.check("uninstall: a package file the user edited is backed up before removal",
                any(b.read_text() == edited for b in backups(env)), "")
        s.check("uninstall: installed.json is removed and the user home itself is kept",
                not (uh / "installed.json").exists() and (uh / "memory").is_dir(), "")
        s.check("uninstall: asks the MCP step to remove its own server keys (install_mcp.py uninstall)",
                any(x["step"] == "mcp" and x["argv"] == ["uninstall"] and x.get("stdin_tty") is False
                    for x in steplog(env)), str(steplog(env))[:300])
        env_f = dict(env, C8_STUB_MODE_MCP="exit3")
        inst(pkgA, ["init", "generic", "--steps", "package"], env_f, proj)
        r = inst(pkgA, ["uninstall"], env_f, proj)
        s.check("uninstall: a failing MCP uninstall is reported, not fatal; package files still removed",
                r.returncode == 0 and "MCP servers:" in out(r) and "simulated problem" in out(r)
                and not (proj / "COORDN8_PROMPT.md").exists(), out(r)[-300:])
        inst(pkgA, ["init", "claude", "--steps", "package"], env, proj)
        (uh / "memory" / "entries.jsonl").write_text('{"id":"m-9"}\n')
        before_home, before_ch = snap(uh), snap(ch)
        r = inst(pkgA, ["uninstall", "--purge"], env, proj)
        o = out(r)
        s.check("uninstall --purge without --yes deletes nothing (user home and package files identical)",
                snap(uh) == before_home and snap(ch) == before_ch, o[-300:])
        s.check("uninstall --purge without --yes lists what it would delete and asks for --yes",
                r.returncode == 2 and str(uh) in o and f"{uh / 'memory'}/" in o and "--purge --yes" in o
                and "nothing was deleted" in o,
                o[-400:])
        r = inst(pkgA, ["uninstall", "--purge", "--yes"], env, proj)
        s.check("uninstall --purge --yes deletes the user home and the package files",
                r.returncode == 0 and not uh.exists() and not (ch / "skills/coordn8/SKILL.md").exists()
                and (ch / "commands/mine.md").is_file(), out(r)[-300:])
        envh = dict(env)
        home = Path(env["HOME"])
        (home / "precious.txt").write_text("do not delete\n")
        (home / "schema.json").write_text("{}\n")
        envh["COORDN8_HOME"] = str(home)
        r = inst(pkgA, ["uninstall", "--purge", "--yes"], envh, proj)
        s.check("negative: --purge refuses when the user home is the home directory itself",
                r.returncode == 2 and (home / "precious.txt").is_file() and "refusing" in out(r), out(r)[-300:])
        # legacy (pre-installed.json) uninstall only removes provably-Coordn8 files
        t = base / "j5b"
        env = make_env(t)
        ch, proj = Path(env["CLAUDE_HOME"]), t / "proj"
        (ch / "skills/coordn8").mkdir(parents=True)
        shutil.copyfile(str(pkgA / "adapters/claude-code/SKILL.md"), str(ch / "skills/coordn8/SKILL.md"))
        (ch / "commands").mkdir(parents=True)
        (ch / "commands/coordn8.md").write_text("a user's own file that happens to be called coordn8.md\n")
        (proj / "AGENTS.md").write_text(user_agents)
        r = inst(pkgA, ["uninstall", "--project-dir", proj], env, proj)
        s.check("uninstall (no installed.json): removes the v1.5.0 skill, keeps look-alike and user files",
                not (ch / "skills/coordn8/SKILL.md").exists() and (ch / "commands/coordn8.md").is_file()
                and (proj / "AGENTS.md").read_text() == user_agents, out(r)[-300:])

        # ------------------------------------------------------- step order and failure
        t = base / "steps"
        env = make_env(t, {"COORDN8_FAIL_STEP": "voices"})
        proj = t / "proj"
        r = inst(pkgA, ["init", "generic"], env, proj)
        o = out(r)
        rows = installed(env).get("steps") or []
        st = {x["step"]: x for x in rows}
        s.check("steps: COORDN8_FAIL_STEP=voices reports voices failed with its reason",
                st.get("voices", {}).get("status") == "failed" and "COORDN8_FAIL_STEP" in st.get("voices", {}).get("detail", ""),
                json.dumps(st.get("voices")))
        log = steplog(env)
        ran = [x["step"] for x in log]
        s.check("steps: later steps still run after a failed step (mcp, refs, signin)",
                ran == ["deps", "mcp", "refs", "signin"], str(ran))
        s.check("steps: the run exits 0 when only a non-package step failed", r.returncode == 0, o[-300:])
        s.check("steps: summary lists package, deps, voices, mcp, refs, signin in that order",
                [x["step"] for x in rows] == ["package", "deps", "voices", "mcp", "refs", "signin"]
                and re.search(r"package \|.*\n.*deps +\|.*\n.*voices +\|.*\n.*mcp +\|.*\n.*refs +\|.*\n.*signin +\|", o)
                is not None, str([x["step"] for x in rows]))
        s.check("steps: every step ran after the package was installed (installed.json written first)",
                log and all(x.get("installed_json_present") is True for x in log), str(log)[:300])
        dep = next((x for x in log if x["step"] == "deps"), {})
        s.check("steps: deps runs setup_env.py with its non-interactive flag",
                "--non-interactive" in dep.get("argv", []), str(dep))
        sig = next((x for x in log if x["step"] == "signin"), {})
        mcp = next((x for x in log if x["step"] == "mcp"), {})
        s.check("steps: mcp runs 'install' and the sign-in offer runs last as 'offer --timeout 20'",
                mcp.get("argv") == ["install"] and sig.get("argv") == ["offer", "--timeout", "20"]
                and ran[-1] == "signin", f"{mcp} {sig}")
        s.check("steps: no step is handed a terminal stdin, and each is told it is non-interactive",
                log and all(x.get("stdin_tty") is False and x.get("noninteractive_env") == "1" for x in log), str(log)[:300])
        s.check("steps: the real claude CLI is never invoked", not Path(env["C8_CLAUDE_LOG"]).exists(), "")
        # disabled / missing / exit codes / hang
        env = make_env(base / "steps2", {"C8_STUB_MODE_MCP": "exit3", "C8_STUB_MODE_REFS": "hang",
                                         "COORDN8_STEP_TIMEOUT_REFS": "2"})
        proj = base / "steps2" / "proj"
        (pkgA / STEP_SCRIPTS["voices"]).rename(pkgA / "scripts/voice/voices.py.off")
        t1 = time.time()
        r = inst(pkgA, ["init", "generic", "--no-deps"], env, proj, timeout=120)
        el = time.time() - t1
        (pkgA / "scripts/voice/voices.py.off").rename(pkgA / STEP_SCRIPTS["voices"])
        st = {x["step"]: x for x in installed(env).get("steps") or []}
        s.check("steps: --no-deps disables the deps step", st.get("deps", {}).get("status") == "disabled", str(st.get("deps")))
        s.check("steps: a step whose script is absent is reported 'not in this build' and skipped",
                st.get("voices", {}).get("status") == "skipped" and "not in this build" in st.get("voices", {}).get("detail", ""),
                str(st.get("voices")))
        s.check("steps: a step exiting non-zero is reported failed with its own reason, run continues",
                st.get("mcp", {}).get("status") == "failed" and "simulated problem" in st.get("mcp", {}).get("detail", ""),
                str(st.get("mcp")))
        s.check("steps: a hanging step is stopped at its timeout and the run continues",
                st.get("refs", {}).get("status") == "timeout" and st.get("signin", {}).get("status") == "ok"
                and r.returncode == 0 and el < 40, f"{st.get('refs')} {el:.1f}s")
        # a step that merges a JSON key records it; uninstall removes exactly that key
        env = make_env(base / "steps3", {"C8_STUB_MODE_MCP": "record"})
        proj = base / "steps3" / "proj"
        cfg = base / "steps3" / "home" / ".claude.json"
        cfg.write_text(json.dumps({"theme": "dark", "mcpServers": {"theirs": {"command": "x"}}}, indent=2))
        env["C8_STUB_CFG"] = str(cfg)
        r = inst(pkgA, ["init", "generic", "--no-deps", "--no-voices", "--no-refs"], env, proj)
        jk = installed(env).get("json_keys") or []
        s.check("steps: a JSON key a step inserted is recorded in installed.json",
                any(j.get("key") == "mcpServers.coordn8-fetch" for j in jk), str(jk))
        inst(pkgA, ["uninstall"], env, proj)
        data = json.loads(cfg.read_text())
        s.check("uninstall: removes the recorded MCP key and keeps every other key",
                data.get("theme") == "dark" and data.get("mcpServers") == {"theirs": {"command": "x"}}, json.dumps(data))
        # package failure
        env = make_env(base / "steps4", {"COORDN8_FAIL_STEP": "package"})
        proj = base / "steps4" / "proj"
        (Path(env["npm_config_cache"]) / "_npx" / "q" / "node_modules" / "coordn8").mkdir(parents=True)
        r = inst(pkgA, ["init", "generic", "--steps", "package,deps"], env, proj)
        s.check("negative: a failed package step exits non-zero, writes no installed.json, leaves the npm cache",
                r.returncode != 0 and not installed(env) and (Path(env["npm_config_cache"]) / "_npx/q").is_dir()
                and not (proj / "COORDN8_PROMPT.md").exists(), out(r)[-300:])

        # ------------------------------------------------------------------- stdin
        env = make_env(base / "stdin")
        proj = base / "stdin" / "proj"
        t1 = time.time()
        # stdin is a pipe that stays OPEN and silent for the whole run (communicate() would close
        # it, which would hide a step that inherits stdin and waits on it)
        with open(base / "stdin" / "run.out", "wb") as fo:
            p = subprocess.Popen([PY, str(pkgA / "scripts/install/installer.py"), "init", "generic"],
                                 stdin=subprocess.PIPE, stdout=fo, stderr=subprocess.STDOUT,
                                 env=env, cwd=str(proj), start_new_session=True)
            try:
                rcp = p.wait(timeout=60)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, 9)
                p.wait()
                rcp = 124
            finally:
                p.stdin.close()
        el = time.time() - t1
        log = steplog(env)
        s.check("stdin: a run whose stdin is an open, never-written pipe finishes in bounded time",
                rcp == 0 and el < 45 and len(log) == 5, f"rc={rcp} {el:.1f}s steps={len(log)}")
        s.check("stdin: every step got an empty, non-terminal stdin (none could wait for input)",
                log and all(x.get("stdin_tty") is False and x.get("stdin_bytes") == 0 for x in log), str(log)[:300])
        env = make_env(base / "stdin2")
        t1 = time.time()
        r = subprocess.run([PY, str(pkgA / "scripts/install/installer.py"), "init", "generic"],
                           capture_output=True, text=True, env=env, cwd=str(base / "stdin2" / "proj"),
                           timeout=90, preexec_fn=lambda: os.close(0))
        el = time.time() - t1
        s.check("stdin: a run with stdin closed finishes within a bounded time",
                r.returncode == 0 and el < 45 and len(steplog(env)) == 5, f"rc={r.returncode} {el:.1f}s {out(r)[-200:]}")

        # ---------------------------------------- the shared wrappers (install.sh / coordn8.js)
        psh, pjs, src = proposed_files()
        s.check("wrappers: install.sh and coordn8.js carrying the python installer are available", psh and pjs, src)
        if psh and pjs:
            pkgC = make_pkg(base, "pkgC", "2.0.0")
            shutil.copyfile(str(psh), str(pkgC / "install.sh"))
            (pkgC / "install.sh").chmod(0o755)
            shutil.copyfile(str(pjs), str(pkgC / "bin" / "coordn8.js"))
            jstext = (pkgC / "bin" / "coordn8.js").read_text()
            s.check("install.sh: parses (bash -n)",
                    subprocess.run(["bash", "-n", str(pkgC / "install.sh")], capture_output=True).returncode == 0, "")
            node = shutil.which("node")
            if node:
                ck = subprocess.run([node, "--check", str(pkgC / "bin" / "coordn8.js")], capture_output=True, text=True)
                s.check("coordn8.js: parses (node --check)", ck.returncode == 0, ck.stderr[-300:])
            s.check("coordn8.js: never calls the global 'npm cache clean'",
                    re.search(r"""['"]cache['"]\s*,\s*['"]clean['"]""", jstext) is None, "")
            def py_table(text):
                m = re.search(r"const PY_COMMANDS = \{(.*?)\};", text, re.S)
                return dict(re.findall(r"(\w+): '([^']+)'", m.group(1))) if m else {}
            table, current = py_table(jstext), py_table((ROOT / "bin" / "coordn8.js").read_text())
            s.check("coordn8.js: the PY_COMMANDS table is exactly the package's current table (v1.5.0 entries kept)",
                    table == current and all(table.get(k) == v for k, v in V15_PY_COMMANDS.items()),
                    f"proposed={table} current={current}")
            missing = [c for c in V15_CASES if f"case {c}:" not in jstext]
            s.check("coordn8.js: every v1.5.0 command is still dispatched", not missing, str(missing))

            # install.sh with python -> python installer, AGENTS.md block-merged
            env = make_env(base / "w_sh")
            proj = base / "w_sh" / "proj"
            (proj / "AGENTS.md").write_text(user_agents)
            r = subprocess.run(["bash", str(pkgC / "install.sh"), "codex", "--steps", "package"], capture_output=True,
                               text=True, env=env, cwd=str(proj), stdin=subprocess.DEVNULL, timeout=120)
            txt = (proj / "AGENTS.md").read_text()
            s.check("install.sh: with python it runs the python installer (block-merge, version line)",
                    r.returncode == 0 and "installed (none) → v2.0.0" in out(r) and user_text_intact(txt, user_agents)
                    and one_block(txt), out(r)[-300:])
            r = subprocess.run(["bash", str(pkgC / "install.sh"), "--steps", "package"], capture_output=True, text=True,
                               env=env, cwd=str(proj), stdin=subprocess.DEVNULL, timeout=120)
            s.check("install.sh: no target and no terminal -> installs all without waiting",
                    r.returncode == 0 and "non-interactive shell detected" in out(r)
                    and (proj / "COORDN8_PROMPT.md").is_file() and one_block((proj / "AGENTS.md").read_text()), out(r)[-300:])
            r = subprocess.run(["bash", str(pkgC / "install.sh"), "uninstall"], capture_output=True, text=True,
                               env=env, cwd=str(proj), stdin=subprocess.DEVNULL, timeout=120)
            s.check("install.sh: uninstall restores the user's AGENTS.md exactly",
                    r.returncode == 0 and (proj / "AGENTS.md").read_text() == user_agents, out(r)[-300:])

            # bash fallback: no python on PATH
            nop = nopython_path(base)
            if nop:
                env = make_env(base / "w_nopy")
                env["PATH"] = nop
                proj = base / "w_nopy" / "proj"
                (proj / "AGENTS.md").write_text(user_agents)
                r = subprocess.run(["bash", str(pkgC / "install.sh"), "codex"], capture_output=True, text=True,
                                   env=env, cwd=str(proj), stdin=subprocess.DEVNULL, timeout=60)
                txt = (proj / "AGENTS.md").read_text()
                s.check("install.sh fallback (no python): AGENTS.md block-merged, user text intact",
                        r.returncode == 0 and "python3" in out(r) and user_text_intact(txt, user_agents) and one_block(txt),
                        out(r)[-400:])
                s.check("install.sh fallback (no python): the user's AGENTS.md is backed up first",
                        any(b.read_text() == user_agents for b in backups(env)), "")
                r = subprocess.run(["bash", str(pkgC / "install.sh"), "codex"], capture_output=True, text=True,
                                   env=env, cwd=str(proj), stdin=subprocess.DEVNULL, timeout=60)
                txt = (proj / "AGENTS.md").read_text()
                s.check("install.sh fallback (no python): reinstall keeps exactly one block, user text intact",
                        r.returncode == 0 and one_block(txt) and user_text_intact(txt, user_agents), out(r)[-300:])
                rec = installed(env)
                s.check("install.sh fallback (no python): writes a valid installed.json and prints the version line",
                        rec.get("version") == "2.0.0" and "installed v2.0.0 → v2.0.0" in out(r), out(r)[-300:])
                # python uninstall understands the bash-written block exactly
                envp = dict(env)
                envp["PATH"] = make_env(base / "w_nopy_py")["PATH"]
                for k in ("COORDN8_HOME", "CLAUDE_HOME", "HOME"):
                    envp[k] = env[k]
                inst(pkgC, ["uninstall"], envp, proj)
                s.check("install.sh fallback block is removed exactly by the python uninstall",
                        (proj / "AGENTS.md").read_text() == user_agents, repr((proj / "AGENTS.md").read_text()[:120]))
                # bash fallback uninstall restores exactly too
                cur = proj / ".cursor/rules/coordn8.mdc"
                cur.parent.mkdir(parents=True, exist_ok=True)
                cur.write_text(user_mdc)
                subprocess.run(["bash", str(pkgC / "install.sh"), "all"], capture_output=True, env=env, cwd=str(proj),
                               stdin=subprocess.DEVNULL, timeout=60)
                merged_ok = one_block(cur.read_text()) and user_text_intact(cur.read_text(), user_mdc)
                r = subprocess.run(["bash", str(pkgC / "install.sh"), "uninstall"], capture_output=True, text=True,
                                   env=env, cwd=str(proj), stdin=subprocess.DEVNULL, timeout=60)
                s.check("install.sh fallback (no python): a user rule file is merged, and bash uninstall restores "
                        "AGENTS.md and it exactly",
                        merged_ok and (proj / "AGENTS.md").read_text() == user_agents and cur.read_text() == user_mdc
                        and not (Path(env["CLAUDE_HOME"]) / "skills/coordn8/SKILL.md").exists(), out(r)[-300:])
                # coordn8.js without python -> install.sh fallback + targeted JS cache cleanup
                if node:
                    cache = Path(env["npm_config_cache"])
                    seed_cache(cache)
                    cb = snap(cache)
                    r = subprocess.run([str(Path(nop) / "node"), str(pkgC / "bin/coordn8.js"), "init", "codex"],
                                       capture_output=True, text=True, env=env, cwd=str(proj),
                                       stdin=subprocess.DEVNULL, timeout=60)
                    s.check("coordn8.js without python: falls back to install.sh and removes only Coordn8 _npx entries",
                            r.returncode == 0 and "installed/updated to v2.0.0" in out(r)
                            and not (cache / "_npx/aaa111").exists() and not (cache / "_npx/bbb222").exists()
                            and cache_kept(cache, cb) and one_block((proj / "AGENTS.md").read_text()), out(r)[-400:])
            else:
                s.check("install.sh fallback (no python): environment for the no-python PATH available", False,
                        "a required coreutil was not found")

            if node:
                env = make_env(base / "w_js")
                proj = base / "w_js" / "proj"
                (proj / "AGENTS.md").write_text(user_agents)
                cache = Path(env["npm_config_cache"])
                seed_cache(cache)
                cb = snap(cache)
                r = subprocess.run([node, str(pkgC / "bin/coordn8.js"), "init", "codex", "--steps", "package"],
                                   capture_output=True, text=True, env=env, cwd=str(proj), stdin=subprocess.DEVNULL,
                                   timeout=120)
                o = out(r)
                npm_log = Path(env["C8_NPM_LOG"]).read_text() if Path(env["C8_NPM_LOG"]).exists() else ""
                s.check("coordn8.js: init runs the python installer and prints next steps",
                        r.returncode == 0 and "installed (none) → v2.0.0" in o and "installed/updated to v2.0.0" in o, o[-400:])
                s.check("coordn8.js: init removes only Coordn8's cached copies and never runs 'npm cache clean'",
                        not (cache / "_npx/aaa111").exists() and cache_kept(cache, cb) and "clean" not in npm_log, npm_log)
                s.check("coordn8.js: init leaves the user's AGENTS.md content intact with one block",
                        user_text_intact((proj / "AGENTS.md").read_text(), user_agents)
                        and one_block((proj / "AGENTS.md").read_text()), "")
                rv = subprocess.run([node, str(pkgC / "bin/coordn8.js"), "version"], capture_output=True, text=True, env=env)
                s.check("coordn8.js: version still works", rv.stdout.strip() == "2.0.0", rv.stdout)
                rs = subprocess.run([node, str(pkgC / "bin/coordn8.js"), "status"], capture_output=True, text=True,
                                    env=env, cwd=str(proj))
                s.check("coordn8.js: status reports the installed version", rs.returncode == 0 and "v2.0.0" in out(rs),
                        out(rs)[-200:])
                uh = Path(env["COORDN8_HOME"])
                hb = snap(uh)
                rp = subprocess.run([node, str(pkgC / "bin/coordn8.js"), "uninstall", "--purge"], capture_output=True,
                                    text=True, env=env, cwd=str(proj), stdin=subprocess.DEVNULL)
                s.check("coordn8.js: uninstall --purge without --yes deletes nothing",
                        rp.returncode == 2 and snap(uh) == hb, out(rp)[-200:])
                ru = subprocess.run([node, str(pkgC / "bin/coordn8.js"), "uninstall"], capture_output=True, text=True,
                                    env=env, cwd=str(proj), stdin=subprocess.DEVNULL)
                s.check("coordn8.js: uninstall restores the user's AGENTS.md exactly",
                        ru.returncode == 0 and (proj / "AGENTS.md").read_text() == user_agents, out(ru)[-200:])
            else:
                s.check("coordn8.js: node available to exercise the CLI", False, "node not on PATH")

        s.check("suite: finished in under 3 minutes", time.time() - t0 < 180, f"{time.time() - t0:.1f}s")
    s.check("suite: left nothing on disk (temp dir removed)", not Path(d).exists(), d)
    return s.finish()


if __name__ == "__main__":
    sys.exit(main())
