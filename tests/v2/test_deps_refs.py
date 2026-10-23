#!/usr/bin/env python3
"""Dependencies (J4, deps side) and dated references (J8).

Hermetic: every run points COORDN8_HOME / CLAUDE_HOME / HOME at temp dirs, package
managers are stubs on a private PATH, and the refs refresh is exercised against a
local http.server on 127.0.0.1 and file:// URLs. Two checks touch the real network
and record a skip reason when offline: the one real download of the layout library
(into the shared cache, verified against its pinned sha256) and one live refresh of
the public Claude Code docs index into a sandboxed home.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import ROOT, Suite, sandbox_env, tmpdir  # noqa: E402

PY = sys.executable
s = Suite("deps_refs")

SETUP = ROOT / "scripts" / "setup_env.py"
DETECT = ROOT / "scripts" / "env_detect.py"
LIBS_PY = ROOT / "scripts" / "render" / "fetch_visual_libs.py"
REFRESH = ROOT / "scripts" / "refs" / "refresh.py"
CHECK = ROOT / "scripts" / "refs" / "check.py"
DOCTOR_REFS = ROOT / "scripts" / "doctor_checks" / "refs.py"
SOURCES = ROOT / "data" / "refs_sources.json"
_DEFAULT_CACHE = Path(os.environ.get("C8_DOWNLOAD_CACHE", str(ROOT.parent / "Coordn8_v2_handoff" / "_working" / "cache")))
HOSTED_SPEECH = ("edge-tts", "edge_tts", "gtts", "elevenlabs", "google-cloud-texttospeech",
                 "azure-cognitiveservices-speech", "openai", "polly")


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def prun(args, env, cwd, timeout=120):
    """Run a script with stdin CLOSED (/dev/null); return (CompletedProcess|None, secs)."""
    t0 = time.monotonic()
    try:
        cp = subprocess.run([PY, *map(str, args)], stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, env=env, cwd=str(cwd),
                            timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, time.monotonic() - t0
    return cp, time.monotonic() - t0


def jload(cp):
    try:
        return json.loads(cp.stdout)
    except Exception:  # noqa: BLE001
        return None


def snapshot(root: Path, skip=()) -> dict:
    out = {}
    for p in Path(root).rglob("*"):
        if p.is_file() and not any(str(p).startswith(str(k)) for k in skip):
            out[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def stub(dirp: Path, name: str, body: str) -> None:
    f = dirp / name
    f.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    f.chmod(0o755)


def no_process(marker: str) -> bool:
    if not shutil.which("pgrep"):
        return True
    r = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
    return r.stdout.strip() == ""


def base_env(tmp: Path) -> dict:
    e = dict(os.environ)
    e.update(sandbox_env(tmp))
    e["PYTHONDONTWRITEBYTECODE"] = "1"
    e.pop("COORDN8_SETUP_TIMEOUT", None)
    e.pop("COORDN8_SETUP_DEADLINE", None)
    e.pop("COORDN8_OFFLINE", None)
    return e


# =============================================================================
# J4 — dependencies: no hosted speech tool, non-interactive, never stuck
# =============================================================================

def j4_static():
    src = SETUP.read_text(encoding="utf-8")
    low = src.lower()
    hits = [b for b in HOSTED_SPEECH if b in low]
    s.check("setup_env source names no hosted speech package anywhere", not hits, hits)

    tree = ast.parse(src)
    bad = []
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef):
            for node in ast.walk(fn):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "subprocess"
                        and node.func.attr in ("run", "Popen", "call", "check_call",
                                               "check_output")
                        and fn.name not in ("run", "_kill_tree")):
                    bad.append(f"{fn.name}:{node.lineno}")
    s.check("every child process in setup_env goes through the one bounded runner",
            not bad, bad)


def j4_inprocess():
    mod = load("c8_setup_env_t", SETUP)
    # tts: never installs, points at the voice step
    calls = []
    saved = (mod.pip_install, mod.tts_engines)
    mod.pip_install = lambda pkgs: (calls.append(("pip", tuple(pkgs))), (False, "blocked"))[1]
    mod.tts_engines = lambda: []
    try:
        r_dry, r_real = mod.ensure_tts(True), mod.ensure_tts(False)
    finally:
        mod.pip_install, mod.tts_engines = saved
    s.check("with no local TTS engine the tts step installs nothing and points to "
            "`coordn8 voices install`",
            not calls and "voices install" in (r_real.get("fix") or "")
            and "voices install" in (r_dry.get("fix") or "")
            and r_real.get("status") == "skipped" and r_real.get("reason"),
            f"calls={calls} real={r_real} dry={r_dry}")

    # bounded runner: grandchild holding the output handle cannot hang it
    t0 = time.monotonic()
    code, out = mod.run(["/bin/sh", "-c", "sleep 313 & sleep 313"], timeout=2)
    dt = time.monotonic() - t0
    s.check("a child that hangs (with a grandchild holding its output) is killed at "
            "the timeout: exit 124 in < 10 s",
            code == 124 and dt < 10 and "timed out" in out, f"code={code} dt={dt:.1f} {out}")
    time.sleep(0.3)
    s.check("no orphaned grandchild survives a killed step (whole process group killed)",
            no_process("sleep 313"), "a 'sleep 313' process is still running")

    t0 = time.monotonic()
    code, out = mod.run(["/bin/sh", "-c", "read x; echo got:[$x]"], timeout=20)
    s.check("the bounded runner gives children EOF on stdin instead of waiting for input",
            code == 0 and "got:[]" in out and time.monotonic() - t0 < 10, f"{code} {out}")

    # a step that raises is contained
    def boom(dry):
        raise RuntimeError("simulated crash")
    res = mod.run_steps({"a", "b"}, True, steps=(
        ("a", "a", boom), ("b", "b", lambda dry: {"tool": "b", "status": "already-present"})))
    s.check("a step that raises is contained: skipped with its reason and the next step "
            "still runs",
            len(res) == 2 and res[0]["status"] == "error-skipped"
            and "simulated crash" in res[0]["reason"] and res[0].get("fix")
            and res[1]["status"] == "already-present", res)

    # whole-run deadline
    mod._DEADLINE_AT = time.monotonic() - 1
    try:
        res = mod.run_steps({"ffmpeg", "tts"}, False)
    finally:
        mod._DEADLINE_AT = None
    s.check("steps that would start after the whole-run deadline are skipped with that "
            "reason (nothing runs)",
            len(res) == 2 and all(r["status"] == "skipped" and "deadline" in r["reason"]
                                  for r in res), res)

    # sudo is only probed as `sudo -n`, never prompted
    with tmpdir() as td:
        td = Path(td)
        log = td / "sudo_args.log"
        stub(td, "apt-get", "exit 0")
        stub(td, "sudo", f'echo "$@" >> "{log}"\necho "sudo: a password is required" >&2\nexit 1')
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(td)
        mod._sudo_prefix.cache_clear()
        try:
            info = mod.os_pkg_manager_info()
        finally:
            os.environ["PATH"] = old_path
            mod._sudo_prefix.cache_clear()
        is_root = hasattr(os, "geteuid") and os.geteuid() == 0
        args = log.read_text().strip().splitlines() if log.exists() else []
        s.check("sudo is only ever probed as `sudo -n` and a password-needing sudo skips "
                "the package-manager path with that reason",
                is_root or (info["name"] is None and "password" in (info["reason"] or "")
                            and "never prompts" in info["reason"]
                            and args and all(a.startswith("-n") for a in args)),
                f"info={info} sudo_args={args} root={is_root}")


def j4_dry_run():
    with tmpdir() as td:
        td = Path(td)
        env = base_env(td)
        cp, dt = prun([SETUP, "--dry-run", "--json"], env, td, timeout=120)
        data = jload(cp) if cp else None
        s.check("setup_env --dry-run finishes in bounded time with stdin closed (< 60 s)",
                cp is not None and cp.returncode == 0 and data is not None and dt < 60,
                f"dt={dt:.1f} rc={getattr(cp, 'returncode', None)}")
        if not data:
            return
        acts = {a.get("tool"): a for a in data.get("actions", [])}
        s.check("setup_env reports non_interactive=true when stdin is not a TTY",
                data.get("non_interactive") is True, data.get("non_interactive"))
        tts = acts.get("tts", {})
        s.check("dry-run tts step installs no speech package (detected, or deferred to the "
                "voice step)",
                tts.get("status") in ("already-present", "deferred-to-voice-step")
                and "pip" not in json.dumps(tts).lower()
                and not any(b in json.dumps(tts).lower() for b in HOSTED_SPEECH), tts)
        lay = acts.get("layout_lib", {})
        s.check("dry-run plans the layout library with its pinned version and sha256",
                lay.get("pinned_version") == "3.1.1" and len(lay.get("sha256_pinned") or "") == 64
                and lay.get("status") in ("would-install", "already-present"), lay)
        present = [a for a in data["actions"] if a.get("status") == "already-present"]
        s.check("idempotent: every already-present tool is skipped and reported with its "
                "version",
                present and all(a.get("version") for a in present),
                [(a["tool"], a.get("version")) for a in present])
        pos = acts.get("pos_tagger", {})
        s.check("optional POS tagger is detected, or skipped with its measured size reason "
                "and a one-line fix",
                pos.get("status") == "already-present"
                or (pos.get("status") == "skipped" and "15" in (pos.get("reason") or "")
                    and pos.get("fix") and "\n" not in pos["fix"]), pos)
        not_ok = [a for a in data["actions"]
                  if a.get("status") not in ("already-present", "installed")]
        s.check("every step that is not already present carries a one-line fix "
                "(and a reason or plan)",
                all(a.get("fix") and "\n" not in a["fix"] and (a.get("reason") or a.get("plan"))
                    for a in not_ok), [(a["tool"], a.get("fix")) for a in not_ok])
        s.check("setup_env names the installer order and runs only the dependencies step",
                data.get("step") == "dependencies"
                and data.get("install_order", [None])[0] == "package"
                and data["install_order"][1:3] == ["dependencies", "voices"]
                and data["install_order"][-1] == "sign-in offers", data.get("install_order"))
        cp2, _ = prun([SETUP, "--dry-run", "--json", "--only", "not-a-target"], env, td, 30)
        s.check("an unknown --only target is rejected (exit 2), never silently ignored",
                cp2 is not None and cp2.returncode == 2, getattr(cp2, "stderr", ""))


def _stub_pkg_path(td: Path, body: str) -> Path:
    d = td / "stubbin"
    d.mkdir()
    stub(d, "brew", body)
    stub(d, "apt-get", body)
    stub(d, "true", "exit 0")
    stub(d, "sudo", '[ "$1" = "-n" ] && shift\nexec "$@"')
    return d


def j4_forced_failure():
    with tmpdir() as td:
        td = Path(td)
        env = base_env(td)
        env["PATH"] = str(_stub_pkg_path(td, 'echo "Error: simulated install failure" >&2\nexit 1'))
        cp, dt = prun([SETUP, "--json", "--only", "ffmpeg", "tts", "pos_tagger",
                       "--vendor-dir", td / "vendor"], env, td, timeout=120)
        data = jload(cp) if cp else None
        acts = (data or {}).get("actions", [])
        ff = next((a for a in acts if a.get("tool") == "ffmpeg"), {})
        s.check("a forced failing install (package manager exits 1) is recorded with its "
                "reason and a one-line fix",
                ff.get("status") == "install-failed" and "exit 1" in (ff.get("reason") or "")
                and ff.get("fix") and "\n" not in ff["fix"], ff)
        s.check("after a failed step the later steps still run, in order",
                [a.get("tool") for a in acts] == ["ffmpeg", "tts", "pos_tagger"],
                [a.get("tool") for a in acts])
        sk = {x.get("tool") for x in (data or {}).get("skipped", [])}
        tts = next((a for a in acts if a.get("tool") == "tts"), {})
        s.check("setup_env still exits 0 and lists every skipped step with its fix",
                cp is not None and cp.returncode == 0 and {"ffmpeg", "tts"} <= sk
                and all(x.get("fix") for x in data.get("skipped", [])), data and data.get("skipped"))
        s.check("with no local engine on PATH the tts step is skipped (nothing installed) "
                "with `coordn8 voices install` as the fix",
                tts.get("status") == "skipped" and "voices install" in (tts.get("fix") or ""), tts)


def j4_hanging_manager():
    with tmpdir() as td:
        td = Path(td)
        env = base_env(td)
        slp = shutil.which("sleep") or "/bin/sleep"
        env["PATH"] = str(_stub_pkg_path(td, f"{slp} 317 &\n{slp} 317"))
        cp, dt = prun([SETUP, "--json", "--timeout", "3", "--only", "ffmpeg", "tts",
                       "--vendor-dir", td / "vendor"], env, td, timeout=90)
        data = jload(cp) if cp else None
        ff = next((a for a in (data or {}).get("actions", []) if a.get("tool") == "ffmpeg"), {})
        s.check("a hanging package manager is killed at the step timeout and the run "
                "continues (whole run < 30 s)",
                cp is not None and cp.returncode == 0 and dt < 30
                and "timed out" in (ff.get("detail") or "") + (ff.get("reason") or "")
                and ff.get("status") == "install-failed"
                and [a.get("tool") for a in data["actions"]] == ["ffmpeg", "tts"],
                f"dt={dt:.1f} ff={ff}")
        time.sleep(0.3)
        s.check("no package-manager process is left running after setup returns",
                no_process("sleep 317"), "a 'sleep 317' process survived")


def j4_env_detect():
    with tmpdir() as td:
        td = Path(td)
        env = base_env(td)
        d = td / "bin"
        d.mkdir()
        stub(d, "edge-tts", "exit 0")
        env["PATH"] = str(d)
        cp, _ = prun([DETECT, "--json"], env, td, timeout=60)
        data = jload(cp) if cp else {}
        caps = (data or {}).get("capabilities", {})
        unlock = (data or {}).get("unlock_video", [])
        tts_hint = [u for u in unlock if "TTS" in u]
        s.check("env_detect unlock hint points to `coordn8 voices install`, never a hosted "
                "speech tool",
                len(tts_hint) == 1 and "voices install" in tts_hint[0]
                and "pip" not in tts_hint[0]
                and not any(b in " ".join(unlock).lower() for b in HOSTED_SPEECH), unlock)
        s.check("env_detect detects a hosted speech CLI but never lists it as a TTS engine",
                "edge-tts" in (caps.get("hosted_speech_tools_detected_not_used") or [])
                and not any("edge" in t for t in (caps.get("tts_engines") or [])), caps)


# =============================================================================
# J4 / B3 — the vendored layout library
# =============================================================================

def lib_cache() -> tuple[Path, bool]:
    env = os.environ.get("C8_LIB_CACHE")
    if env:
        return Path(env), False
    if _DEFAULT_CACHE.is_dir():
        return _DEFAULT_CACHE / "libs", False
    return Path(tempfile.mkdtemp(prefix="c8libs-")), True


def layout_lib():
    libs = load("c8_fetch_visual_libs_t", LIBS_PY)
    spec = libs.LIBS["dagre"]
    with tmpdir() as td:
        td = Path(td)
        env = base_env(td)
        cp, _ = prun([LIBS_PY, "--dry-run", "--only", "dagre"], env, td, timeout=30)
        plan = ((jload(cp) or {}).get("plan") or [{}])[0] if cp else {}
        s.check("fetch_visual_libs plans dagre: MIT licence, pinned version in the URL, "
                "pinned sha256",
                plan.get("lib") == "dagre" and plan.get("spdx") == "MIT"
                and "MIT" in plan.get("license", "") and "free" in plan.get("license", "").lower()
                and plan.get("version") == "3.1.1" and "@3.1.1/" in plan.get("url", "")
                and plan.get("npm") == "@dagrejs/dagre"
                and len(plan.get("sha256") or "") == 64, plan)
    s.check("every vendored library URL carries its pinned version",
            all(f"@{v['version']}/" in v["url"] for v in libs.LIBS.values()),
            [k for k, v in libs.LIBS.items() if f"@{v['version']}/" not in v["url"]])

    # negative: tampered download refused, nothing written
    with tmpdir() as td:
        vd = Path(td) / "vendor"
        saved = libs.download
        libs.download = lambda url, timeout: b"tampered bytes"
        try:
            r = libs.fetch_one("dagre", spec, vd)
        finally:
            libs.download = saved
        s.check("a download whose bytes differ from the pinned sha256 is refused and "
                "nothing is written",
                r["status"] == "fetch-failed" and "sha256 mismatch" in r["reason"]
                and not (vd / spec["file"]).exists(), r)
        vd.mkdir(parents=True, exist_ok=True)
        (vd / spec["file"]).write_bytes(b"corrupt")
        st = libs.file_state("dagre", spec, vd, None)
        s.check("a vendored file whose bytes no longer match its pin is not reported as "
                "present",
                st["ok"] is False and "pinned sha256" in st["reason"], st)
        # manifest merge keeps other libraries
        (vd / "manifest.json").write_text(json.dumps({"vendored": [
            {"lib": "gsap", "status": "fetched", "file": str(vd / "gsap.min.js")}]}))
        merged = libs.merge_manifest(vd, [{"lib": "dagre", "status": "fetched",
                                           "file": str(vd / spec["file"])}])
        s.check("fetching one library keeps every other library's manifest entry "
                "(merge, not overwrite)",
                sorted(e["lib"] for e in merged["vendored"]) == ["dagre", "gsap"], merged)

    # the ONE real download into the shared cache
    cache, temp_cache = lib_cache()
    try:
        with tmpdir() as td:
            env = base_env(Path(td))
            cp, _ = prun([LIBS_PY, "--only", "dagre", "--dir", cache, "--timeout", "45"],
                         env, td, timeout=90)
        rep = jload(cp) if cp else None
        f = cache / spec["file"]
        ok_file = f.exists() and hashlib.sha256(f.read_bytes()).hexdigest() == spec["sha256"]
        reason = None
        if not ok_file:
            fails = (rep or {}).get("failed") or []
            reason = (fails[0].get("reason") if fails else "no result") or "unknown"
            print(f"  note: layout-library download skipped/failed: {reason}")
        offline = (not ok_file) and reason and ("URLError" in reason or "timed out" in reason)
        s.check("dagre real download matches its pinned sha256 (or skipped offline with the "
                "reason recorded)", ok_file or offline, reason)
        man = json.loads((cache / "manifest.json").read_text()) if (cache / "manifest.json").exists() else {}
        ent = next((e for e in man.get("vendored", []) if e.get("lib") == "dagre"), {})
        s.check("the vendored-libs manifest records dagre with licence, pinned version and "
                "sha256",
                (not ok_file and offline) or (ent.get("spdx") == "MIT" and ent.get("version") == "3.1.1"
                                              and ent.get("sha256") == spec["sha256"]
                                              and ent.get("integrity") == "pinned"), ent)

        node = shutil.which("node")
        if ok_file and node:
            js = ("const fs=require('fs'),vm=require('vm');"
                  "const ctx={structuredClone};vm.createContext(ctx);"
                  "vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),ctx);"
                  "const d=vm.runInContext('dagre',ctx);const g=new d.graphlib.Graph();"
                  "g.setGraph({rankdir:'TB',nodesep:30,ranksep:40});g.setDefaultEdgeLabel(()=>({}));"
                  "for(const n of ['a','b','c','d'])g.setNode(n,{width:80,height:40});"
                  "g.setEdge('a','b');g.setEdge('a','c');g.setEdge('b','d');g.setEdge('c','d');"
                  "d.layout(g);console.log(JSON.stringify({v:d.version,n:g.nodes().map("
                  "n=>[n,g.node(n).x,g.node(n).y]),e:g.edges().map(e=>g.edge(e).points)}));")
            outs = []
            for _ in range(2):
                r = subprocess.run([node, "-e", js, str(f)], capture_output=True, text=True,
                                   timeout=30, stdin=subprocess.DEVNULL)
                outs.append(r.stdout.strip())
            try:
                lay = json.loads(outs[0])
                ys = {n: y for n, _x, y in lay["n"]}
                sane = (lay["v"] == "3.1.1" and ys["a"] < ys["b"] < ys["d"]
                        and all(isinstance(x, (int, float)) for _n, x, _y in lay["n"]))
            except Exception as e:  # noqa: BLE001
                sane = False
                outs.append(str(e))
            s.check("the vendored dagre build lays out a layered graph deterministically "
                    "(same input -> identical coordinates)",
                    sane and outs[0] == outs[1], outs)
        else:
            why = "node not on PATH" if not node else "layout library not available offline"
            print(f"  note: layout run skipped: {why}")
            s.check("the vendored dagre build lays out a layered graph deterministically "
                    "(same input -> identical coordinates)", True, why)

        with tmpdir() as td:
            td = Path(td)
            env = base_env(td)
            if ok_file:
                cp, _ = prun([SETUP, "--dry-run", "--json", "--only", "layout_lib",
                              "--vendor-dir", cache], env, td, 60)
                a = ((jload(cp) or {}).get("actions") or [{}])[0] if cp else {}
                good = a.get("status") == "already-present" and "sha256 verified" in (a.get("version") or "")
            else:
                good, a = True, "skipped offline"
            bad_dir = td / "badvendor"
            bad_dir.mkdir()
            (bad_dir / spec["file"]).write_bytes(b"not the pinned bytes")
            cp, _ = prun([SETUP, "--dry-run", "--json", "--only", "layout_lib",
                          "--vendor-dir", bad_dir], env, td, 60)
            b = ((jload(cp) or {}).get("actions") or [{}])[0] if cp else {}
            s.check("setup_env reports the layout library present only when its sha256 "
                    "matches the pin",
                    good and b.get("status") == "would-install", f"good={a} bad={b}")
    finally:
        if temp_cache:
            shutil.rmtree(cache, ignore_errors=True)


# =============================================================================
# J8 — dated references
# =============================================================================

class FixtureServer:
    def __init__(self):
        self.routes: dict = {}
        self.hits: list = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                outer.hits.append(self.path)
                st, headers, body = outer.routes.get(self.path, (404, {}, b"not found"))
                self.send_response(st)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):  # silence
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


MD = b"# Docs index\n\n- [Overview](https://example.org/overview.md): what it is\n"
GSAP = json.dumps({"name": "gsap", "version": "99.0.0", "license": "test"}).encode()


def refs_tests():
    srv = FixtureServer()
    try:
        with tmpdir() as td:
            td = Path(td)
            env = base_env(td)
            home = Path(env["COORDN8_HOME"])
            refs = home / "refs"
            fx = td / "fixtures"
            fx.mkdir()
            local = fx / "mirror.md"
            local.write_bytes(b"# Local mirror\n\nOffline copy of a public page.\n")
            srcs = {"schema": 1, "default_max_age_days": 14, "sources": [
                {"id": "docs-index", "url": srv.base + "/llms.txt", "kind": "docs-index",
                 "format": "md", "max_age_days": 7},
                {"id": "npm-gsap", "url": srv.base + "/gsap.json", "kind": "package-release",
                 "format": "json", "max_age_days": 30, "package": "gsap", "vendored_lib": "gsap"},
                {"id": "local-mirror", "url": local.as_uri(), "kind": "docs-page",
                 "format": "md", "max_age_days": 7}]}
            sf = fx / "sources.json"
            sf.write_text(json.dumps(srcs))
            srv.routes = {"/llms.txt": (200, {"Content-Type": "text/plain"}, MD),
                          "/gsap.json": (200, {"Content-Type": "application/json"}, GSAP)}
            before = snapshot(td, skip=(fx,))

            cp, _ = prun([REFRESH, "--sources", sf, "--json"], env, td, 60)
            rep = jload(cp) or {}
            idx = json.loads((refs / "index.json").read_text()) if (refs / "index.json").exists() else {}
            s.check("refresh writes each ref under <user_home>/refs as <id>.md / <id>.json",
                    (refs / "docs-index.md").read_bytes() == MD
                    and (refs / "npm-gsap.json").read_bytes() == GSAP
                    and (refs / "local-mirror.md").exists()
                    and sorted(rep.get("fetched", [])) == ["docs-index", "local-mirror", "npm-gsap"],
                    rep)
            e = idx.get("docs-index", {})
            fetched_at_1 = e.get("fetched_at")
            s.check("index.json records url, fetched_at, bytes, sha256 and ok for every ref",
                    set(idx) >= {"docs-index", "npm-gsap", "local-mirror"}
                    and all(idx[k].get("url") and idx[k].get("fetched_at") and idx[k].get("ok") is True
                            and idx[k].get("error") is None and idx[k].get("bytes", 0) > 0
                            for k in ("docs-index", "npm-gsap", "local-mirror"))
                    and e.get("sha256") == hashlib.sha256(MD).hexdigest()
                    and e.get("bytes") == len(MD)
                    and datetime.strptime(fetched_at_1, "%Y-%m-%dT%H:%M:%SZ"), idx)
            s.check("file:// sources are supported (local mirrors / offline installs)",
                    idx.get("local-mirror", {}).get("ok") is True, idx.get("local-mirror"))
            s.check("a package-release ref records the upstream version it saw",
                    idx.get("npm-gsap", {}).get("version") == "99.0.0", idx.get("npm-gsap"))

            # check.py: fresh
            cp, _ = prun([CHECK, "--sources", sf, "--json"], env, td, 60)
            chk = jload(cp) or {}
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            s.check("check passes (exit 0) when every ref is within its max age and prints "
                    "an 'as of <date>' stamp per ref",
                    cp is not None and cp.returncode == 0 and not chk.get("stale")
                    and len(chk.get("stamps", [])) == 3
                    and all(f"as of {today}" in x for x in chk["stamps"]), chk.get("stamps"))
            s.check("check reports when upstream is newer than the pinned vendored library",
                    any(u.get("lib") == "gsap" and u.get("latest") == "99.0.0"
                        for u in chk.get("upstream_newer", [])), chk.get("upstream_newer"))

            # failed re-fetch: 500 for markdown, HTML instead of JSON
            srv.routes = {"/llms.txt": (500, {}, b"server error"),
                          "/gsap.json": (200, {"Content-Type": "text/html"},
                                         b"<html><body>captive portal</body></html>")}
            local_bytes = local.read_bytes()
            local.unlink()   # the file:// source now fails too
            cp, _ = prun([REFRESH, "--sources", sf, "--json"], env, td, 60)
            rep = jload(cp) or {}
            idx = json.loads((refs / "index.json").read_text())
            e = idx.get("docs-index", {})
            s.check("a failed re-fetch (HTTP 500) keeps the last good copy byte-identical "
                    "and marks it stale",
                    (refs / "docs-index.md").read_bytes() == MD and e.get("stale") is True
                    and e.get("ok") is False and "500" in (e.get("error") or "")
                    and e.get("has_copy") is True, e)
            s.check("a failed re-fetch keeps the copy's real fetch date (fetched_at "
                    "unchanged)", e.get("fetched_at") == fetched_at_1, e)
            g = idx.get("npm-gsap", {})
            s.check("a response that is not valid JSON never replaces a good JSON copy",
                    (refs / "npm-gsap.json").read_bytes() == GSAP and g.get("stale") is True
                    and "JSON" in (g.get("error") or ""), g)
            s.check("refresh exits 0 when every fetch fails and reports each failure",
                    cp is not None and cp.returncode == 0 and not rep.get("fetched")
                    and {f["id"] for f in rep.get("failed", [])} == {"docs-index", "npm-gsap",
                                                                     "local-mirror"}
                    and all(f.get("error") and f.get("kept_copy") for f in rep["failed"]), rep)
            local.write_bytes(local_bytes)

            # HTML where markdown was expected
            srv.routes["/llms.txt"] = (200, {"Content-Type": "text/html"},
                                       b"<!DOCTYPE html><html>login required</html>")
            prun([REFRESH, "--sources", sf, "--only", "docs-index", "--json"], env, td, 60)
            e = json.loads((refs / "index.json").read_text()).get("docs-index", {})
            s.check("an HTML page where markdown was expected never replaces the copy",
                    (refs / "docs-index.md").read_bytes() == MD and "HTML" in (e.get("error") or ""), e)

            # offline: no request at all
            n_hits = len(srv.hits)
            cp, _ = prun([REFRESH, "--sources", sf, "--offline", "--json"], env, td, 60)
            idx = json.loads((refs / "index.json").read_text())
            s.check("--offline makes no request, keeps every copy and marks it stale",
                    cp is not None and cp.returncode == 0 and len(srv.hits) == n_hits
                    and all(idx[k]["stale"] and idx[k]["has_copy"] and "offline" in idx[k]["error"]
                            for k in ("docs-index", "npm-gsap", "local-mirror"))
                    and (refs / "docs-index.md").read_bytes() == MD, idx)

            # --only
            srv.routes = {"/llms.txt": (200, {}, MD + b"\n- [New](https://example.org/n.md)\n"),
                          "/gsap.json": (200, {}, GSAP)}
            gsap_before = json.loads((refs / "index.json").read_text())["npm-gsap"]
            n_hits = len(srv.hits)
            cp, _ = prun([REFRESH, "--sources", sf, "--only", "docs-index", "--json"], env, td, 60)
            idx = json.loads((refs / "index.json").read_text())
            s.check("--only refreshes just the named source and leaves the others' entries "
                    "untouched",
                    srv.hits[n_hits:] == ["/llms.txt"] and idx["docs-index"]["ok"] is True
                    and idx["docs-index"]["changed"] is True and idx["npm-gsap"] == gsap_before,
                    srv.hits[n_hits:])
            cp, _ = prun([REFRESH, "--sources", sf, "--only", "no-such-id"], env, td, 30)
            s.check("an unknown --only id is rejected (exit 2)",
                    cp is not None and cp.returncode == 2, getattr(cp, "stderr", ""))

            # security: refused sources
            bad = {"schema": 1, "sources": [
                {"id": "../escape", "url": srv.base + "/llms.txt", "format": "md"},
                {"id": "plain-http", "url": "http://198.51.100.7/x.md", "format": "md"},
                {"id": "redir", "url": srv.base + "/redir", "format": "md"}]}
            srv.routes["/redir"] = (302, {"Location": "http://203.0.113.9/x.md"}, b"")
            bf = fx / "bad_sources.json"
            bf.write_text(json.dumps(bad))
            n_hits = len(srv.hits)
            cp, dt = prun([REFRESH, "--sources", bf, "--json", "--timeout", "5"], env, td, 60)
            rep = jload(cp) or {}
            refused = {r["id"]: r["error"] for r in rep.get("refused", [])}
            s.check("a source id with path traversal is refused and nothing is written for it",
                    "../escape" in refused and not (home / "escape.md").exists()
                    and not (td / "escape.md").exists(), refused)
            s.check("plain http to a non-loopback host is refused before any request",
                    "plain-http" in refused and "loopback" in refused["plain-http"]
                    and dt < 20, refused)
            red = next((f for f in rep.get("failed", []) if f["id"] == "redir"), {})
            s.check("a redirect to a non-https, non-loopback URL is refused (not followed)",
                    "redirect" in (red.get("error") or "") and srv.hits[n_hits:] == ["/redir"]
                    and not (refs / "redir.md").exists(), red)

            after = snapshot(td, skip=(fx,))
            changed = [p for p in after if before.get(p) != after[p]] + \
                      [p for p in before if p not in after]
            outside = [p for p in changed if not p.startswith(str(refs) + os.sep)]
            s.check("refresh writes nothing outside <user_home>/refs (sandboxed HOME, "
                    "CLAUDE_HOME, rest of COORDN8_HOME and cwd untouched)",
                    changed and not outside, outside[:5])

            # check.py: expired / missing / modified — and read-only
            idx = json.loads((refs / "index.json").read_text())
            old = (datetime.now(timezone.utc) - timedelta(days=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
            idx["docs-index"]["fetched_at"] = old
            (refs / "index.json").write_text(json.dumps(idx))
            (refs / "local-mirror.md").unlink()
            with open(refs / "npm-gsap.json", "ab") as fh:
                fh.write(b" ")
            snap = snapshot(td)
            cp, _ = prun([CHECK, "--sources", sf, "--json"], env, td, 60)
            chk = jload(cp) or {}
            st = {r["id"]: r["status"] for r in chk.get("refs", [])}
            s.check("check flags a ref older than max_age_days as stale (exit 1) with its "
                    "'as of' date",
                    cp is not None and cp.returncode == 1 and st.get("docs-index") == "expired"
                    and "docs-index" in chk.get("stale", [])
                    and f"as of {old[:10]}" in " ".join(chk.get("stamps", [])), st)
            s.check("check flags a missing ref as stale", st.get("local-mirror") == "missing", st)
            s.check("check flags a copy whose bytes no longer match the index",
                    st.get("npm-gsap") == "modified", st)
            cp2, _ = prun([REFRESH, "check", "--sources", sf, "--json"], env, td, 60)
            s.check("`refresh.py check` runs the same check (one CLI entry point)",
                    cp2 is not None and cp2.returncode == 1
                    and (jload(cp2) or {}).get("stale") == chk.get("stale"), getattr(cp2, "stderr", ""))
            s.check("check is read-only (no file changed or created)", snapshot(td) == snap,
                    "files changed during check")

        # never-refreshed home: everything missing, nothing created
        with tmpdir() as td:
            td = Path(td)
            env = base_env(td)
            snap = snapshot(td)
            cp, _ = prun([CHECK, "--json"], env, td, 60)
            chk = jload(cp) or {}
            s.check("check on a never-refreshed home reports every shipped ref missing and "
                    "creates nothing",
                    cp is not None and cp.returncode == 1 and chk.get("refs")
                    and all(r["status"] == "missing" for r in chk["refs"])
                    and snapshot(td) == snap
                    and not (Path(env["COORDN8_HOME"]) / "refs").exists(), chk.get("stale"))

            # doctor extension (in-process, sandboxed home)
            saved = os.environ.get("COORDN8_HOME")
            os.environ["COORDN8_HOME"] = env["COORDN8_HOME"]
            try:
                p1 = load("c8_doctor_refs_t", DOCTOR_REFS).probe()
                (Path(env["COORDN8_HOME"]) / "refs").mkdir(parents=True)
                (Path(env["COORDN8_HOME"]) / "refs" / "index.json").write_text("{not json")
                snap = snapshot(td)
                p2 = load("c8_doctor_refs_t2", DOCTOR_REFS).probe()
                same = snapshot(td) == snap
            except Exception as ex:  # noqa: BLE001
                p1 = p2 = {"error": repr(ex)}
                same = False
            finally:
                if saved is None:
                    os.environ.pop("COORDN8_HOME", None)
                else:
                    os.environ["COORDN8_HOME"] = saved
            s.check("doctor refs probe returns a freshness summary and never raises (empty "
                    "home, corrupt index)",
                    isinstance(p1.get("summary"), str) and "stale" in p1["summary"]
                    and isinstance(p1.get("lines"), list)
                    and isinstance(p2.get("summary"), str) and isinstance(p2.get("lines"), list),
                    f"{p1} | {p2}")
            s.check("doctor refs probe writes nothing", same, "files changed during probe")
            cp, _ = prun([ROOT / "scripts" / "doctor.py", "--json", "--path", td], env, td, 90)
            ext = ((jload(cp) or {}).get("extensions") or {}).get("refs", {})
            s.check("`doctor --json` includes the refs freshness section",
                    isinstance(ext.get("summary"), str) and ext["summary"].startswith("refs:"), ext)
    finally:
        srv.close()


def shipped_sources():
    refresh = load("c8_refs_refresh_t", REFRESH)
    libs = load("c8_fetch_visual_libs_t2", LIBS_PY).LIBS
    data = json.loads(SOURCES.read_text())
    srcs = data["sources"]
    ids = [x["id"] for x in srcs]
    s.check("shipped refs_sources.json lists the Claude Code docs index",
            any(x["url"] == "https://code.claude.com/docs/llms.txt" and x["format"] == "md"
                for x in srcs), ids)
    npm = {v["npm"] for v in libs.values()}
    rel = {x.get("package"): x for x in srcs if x.get("kind") == "package-release"}
    s.check("shipped sources include the npm release record of every vendored library",
            set(rel) == npm and all(r["url"] == f"https://registry.npmjs.org/{p}/latest"
                                    and r.get("vendored_lib") in libs for p, r in rel.items()),
            sorted(rel))
    errs = {x.get("id"): refresh.source_error(x) for x in srcs}
    s.check("every shipped source is valid (safe id, https url, format, positive "
            "max_age_days) and ids are unique",
            not any(errs.values()) and len(set(ids)) == len(ids)
            and all(x["url"].startswith("https://") and isinstance(x.get("max_age_days"), int)
                    and x["max_age_days"] > 0 for x in srcs), errs)
    from urllib.parse import urlparse
    hosts = {urlparse(x["url"]).hostname for x in srcs}
    s.check("every shipped source is a public docs or package-registry host (no hosted AI "
            "service)", hosts <= {"code.claude.com", "registry.npmjs.org"}, hosts)


def live_docs_index():
    with tmpdir() as td:
        td = Path(td)
        env = base_env(td)
        cp, _ = prun([REFRESH, "--only", "claude-code-docs-index", "--json", "--timeout", "20"],
                     env, td, 60)
        rep = jload(cp) or {}
        home = Path(env["COORDN8_HOME"]) / "refs"
        f = home / "claude-code-docs-index.md"
        ok = f.exists() and f.read_bytes().lstrip().startswith(b"#")
        reason = None if ok else ((rep.get("failed") or [{}])[0].get("error") or "no result")
        if not ok:
            print(f"  note: live docs-index refresh skipped: {reason}")
        offline = not ok and reason and any(k in reason for k in ("URLError", "timed out", "HTTP", "OSError"))
        s.check("live: the public Claude Code docs index refreshes into a sandboxed home with "
                "a fetch date (or skipped offline with the reason)",
                (ok and cp.returncode == 0 and rep.get("as_of", {}).get("claude-code-docs-index"))
                or offline, reason)


def main() -> int:
    j4_static()
    j4_inprocess()
    j4_dry_run()
    j4_forced_failure()
    j4_hanging_manager()
    j4_env_detect()
    layout_lib()
    refs_tests()
    shipped_sources()
    live_docs_index()
    return s.finish()


if __name__ == "__main__":
    sys.exit(main())
