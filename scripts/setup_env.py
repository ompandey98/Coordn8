#!/usr/bin/env python3
"""
Coordn8 — environment AUTO-SETUP (the "dependencies" step of install).

Unblock the machine so the FULL deliverable set (page + video) can be produced,
on almost any system, without asking.

This exists because "degrade honestly" was quietly turning into "give up early":
the agent would see a missing tool and declare a lower tier instead of just
installing the tool. That produced poor content and false "not supported here"
claims. This script closes that gap — it INSTALLS what's missing, prefers paths
that need NO root, and VERIFIES each install by re-probing (never claims a
success it didn't observe).

Where it sits in install. The installer runs, in order:
  package -> dependencies (THIS script) -> voices -> MCP servers -> references
  -> sign-in offers last.
This script does the dependency step only. Narration voices are the voice step's
job (`coordn8 voices install`, local open-source engines); this script never
installs a speech tool, and never installs any tool that sends text, audio or
code to a hosted AI service.

What it installs when missing (the full list):
  - ffmpeg + ffprobe   video encode + probes   (pkg manager; static build fallback hint)
  - headless Chromium   frame capture          (SUDO-FREE: pip playwright + `playwright install chromium`)
  - a TTS engine        narration              (DETECTED, not installed here: macOS `say`
                                                is built in; local voices come from the voice step)
  - Pillow              layout pixel-probe      (SUDO-FREE: pip)
  - numpy               misc render math        (SUDO-FREE: pip; best-effort)
  - Manim (CE)          math/geometry scenes    (pip manim; needs cairo/pango system libs)
  - vendored JS libs    crafted motion/graphs   (GSAP/D3/Rough/anime into _build/vendor)
  - layout library      deterministic diagrams  (dagre, pinned + sha256-checked, same vendor dir)
  - icon library        crisp inline-SVG icons  (open-licensed sets into _build/vendor/icons)
  - POS tagger          language checker        (OPTIONAL, detected only — see POS_TAGGER_NOTE)

TWO TIERS OF TOOL. ffmpeg, Chromium and a TTS engine are the BASE video pipeline —
without them a video cannot be made, so they gate the tier. Everything else is an
ENHANCEMENT: it makes the output dramatically better, but the base video still
ships without it. So a failed enhancement is reported as "enhancement
unavailable", NEVER as something that blocks the video.

NEVER STUCK (non-interactive by construction):
  - Every child process gets stdin=/dev/null, a NEW SESSION (no controlling
    terminal, so nothing — sudo included — can open the tty to prompt), a
    batch-mode environment, and a hard timeout. On timeout the whole process
    GROUP is killed, so a grandchild that keeps the output handle open cannot
    hang the run. Output goes to a temp file, never a pipe a descendant can hold.
  - sudo is NEVER prompted: it is used only as `sudo -n` and only after
    `sudo -n true` proves it is already non-interactive; otherwise the OS
    package-manager path is skipped with that reason and a one-line fix.
  - A whole-run time budget (--deadline) bounds the total; steps past it are
    skipped with their reason. A step that crashes is contained and skipped.
  - Every failure carries a specific `reason` and a one-line `fix`; the next step
    still runs; the exit code is always 0 (setup is advisory unblocking).
  - Idempotent: what is present is skipped and reported with its version.

Usage:
    python3 scripts/setup_env.py               # install everything missing, print JSON summary
    python3 scripts/setup_env.py --json        # JSON only (no human lines on stderr)
    python3 scripts/setup_env.py --dry-run     # show the plan; install nothing
    python3 scripts/setup_env.py --only chrome ffmpeg   # subset
    python3 scripts/setup_env.py --non-interactive      # explicit (default when stdin is not a TTY)
    python3 scripts/setup_env.py --timeout 120 --deadline 900   # cap each step / the whole run
"""
from __future__ import annotations

import argparse
import functools
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.common import find_chrome, have  # noqa: E402

TIMEOUT_QUICK = 60
TIMEOUT_INSTALL = 900  # browser/tool downloads can be large
DEFAULT_DEADLINE = 2100  # whole-run budget (seconds); steps past it are skipped.
# Kept below the installer's own deps-step timeout so this script always reports
# before anything outside it could kill it; an installer-provided step timeout
# (COORDN8_STEP_TIMEOUT_DEPS / COORDN8_STEP_TIMEOUT) tightens it to 90 % of that.

HERE = Path(__file__).resolve().parent
FETCH_LIBS = HERE / "render" / "fetch_visual_libs.py"
FETCH_ICONS = HERE / "render" / "fetch_icons.py"

# Optional local part-of-speech tagger: measured, then deliberately NOT installed.
# The smallest fully-local tagger found (an averaged-perceptron English tagger in
# an Apache-2.0 Python NLP toolkit, 3.9.x for Python 3.9) is a ~3.7 MB download
# but ~15.3 MB on disk before bytecode compilation (toolkit + 4 helper packages
# ~9.7 MB, model weights ~5.6 MB) — over the 15 MB budget for an optional extra.
# The language checker has a regex fallback, so the tagger is DETECTED if the
# user already has it and reported as skipped (with the one-line fix) otherwise.
POS_TAGGER_NOTE = ("skipped by size budget: the smallest fully-local tagger "
                   "measures ~15.3 MB on disk (> 15 MB); the language checker "
                   "uses its regex fallback")
POS_TAGGER_FIX = ("optional: python3 -m pip install --user 'nltk>=3.9' && "
                  "python3 -m nltk.downloader averaged_perceptron_tagger_eng")

# Mutable run settings (set by main(); defaults keep import-time use safe).
_STEP_CAP: float | None = None      # --timeout: cap on any single child process
_DEADLINE_AT: float | None = None   # monotonic time after which nothing new starts
_VENDOR_DIR = Path("_build/vendor")
_QUIET = False


def log(msg: str) -> None:
    if not _QUIET:
        sys.stderr.write(f"[coordn8:setup] {msg}\n")
        sys.stderr.flush()


# A fully NON-INTERACTIVE environment for every child process. Package managers and
# pip must never stop at a [Y/n] prompt or an interactive pager when Coordn8 runs
# unattended (a CI shell, a headless agent, an npx one-liner). We force the
# batch/no-prompt mode of each tool through the environment, in addition to the
# explicit -y/--noconfirm flags on each command.
def _noninteractive_env() -> dict:
    env = dict(os.environ)
    env.update({
        "DEBIAN_FRONTEND": "noninteractive",   # apt: never prompt (tzdata etc.)
        "APT_LISTCHANGES_FRONTEND": "none",
        "NEEDRESTART_MODE": "a",               # apt: auto-restart services, no prompt
        "PIP_NO_INPUT": "1",                   # pip: never prompt
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PYTHONUNBUFFERED": "1",
        "HOMEBREW_NO_INSTALL_CLEANUP": "1",     # brew: no interactive cleanup
        "HOMEBREW_NO_AUTO_UPDATE": "1",
        "HOMEBREW_NO_ENV_HINTS": "1",
        "NONINTERACTIVE": "1",                  # brew/installer scripts: batch mode
        "CI": env.get("CI", "1"),               # many tools go quiet/non-interactive under CI
        "GIT_TERMINAL_PROMPT": "0",             # git: never prompt for creds
        "SUDO_ASKPASS": "/bin/false",           # belt and braces: no askpass helper
        "PLAYWRIGHT_SKIP_BROWSER_GC": "1",
    })
    return env


def _effective_timeout(timeout: float) -> float:
    t = float(timeout)
    if _STEP_CAP is not None:
        t = min(t, _STEP_CAP)
    if _DEADLINE_AT is not None:
        t = min(t, max(1.0, _DEADLINE_AT - time.monotonic()))
    return max(1.0, t)


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:  # pragma: no cover - Windows
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=5)
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError, subprocess.TimeoutExpired):
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def run(cmd: list[str], timeout: float = TIMEOUT_INSTALL,
        head: bool = False) -> tuple[int, str]:
    """Run a command NON-INTERACTIVELY and BOUNDED; return (exit_code, output_tail).

    This is the ONLY place this script starts a child process. Never raises on a
    normal failure — only records it:
      - stdin is /dev/null, and the child runs in a NEW SESSION with no controlling
        terminal, so a tool that ignores its no-prompt flags (or sudo, which reads
        the tty directly) gets EOF/fails fast instead of waiting forever;
      - output goes to a temp file, not a pipe, so a grandchild that inherits and
        holds the handle cannot block the wait;
      - on timeout the whole process group is killed (exit 124).
    Returns the last 500 chars of output (the first 500 with head=True).
    """
    t = _effective_timeout(timeout)
    with tempfile.TemporaryFile() as out:
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out,
                                    stderr=subprocess.STDOUT, env=_noninteractive_env(),
                                    start_new_session=(os.name == "posix"))
        except FileNotFoundError:
            return 127, f"{cmd[0]}: not found"
        except OSError as e:
            return 126, f"{cmd[0]}: {e}"
        timed_out = False
        start = time.monotonic()
        while True:
            try:
                proc.wait(timeout=min(20.0, max(0.1, t - (time.monotonic() - start))))
                break
            except subprocess.TimeoutExpired:
                waited = time.monotonic() - start
                if waited >= t:
                    _kill_tree(proc)
                    timed_out = True
                    break
                log(f"… still running: {' '.join(map(str, cmd[:4]))[:80]} ({waited:.0f}s of {t:g}s)")
        out.seek(0)
        text = out.read().decode("utf-8", errors="replace").strip()
    if timed_out:
        tail = f" | last output: {text[-200:]}" if text else ""
        return 124, f"{cmd[0]}: timed out after {t:g}s (killed){tail}"
    return proc.returncode, (text[:500] if head else text[-500:])


def _first_line(code_out: tuple[int, str]) -> str | None:
    code, out = code_out
    if code != 0 or not out:
        return None
    return out.splitlines()[0].strip()[:160] or None


# --- capability probes (source of truth for VERIFIED/pending) ---------------

def has_ffmpeg() -> bool:
    return have("ffmpeg") and have("ffprobe")


def ffmpeg_version() -> str | None:
    return _first_line(run(["ffmpeg", "-version"], timeout=15, head=True)) if have("ffmpeg") else None


def has_chrome() -> bool:
    return find_chrome() is not None


def chrome_version() -> str | None:
    p = find_chrome()
    if not p:
        return None
    return _first_line(run([p, "--version"], timeout=15, head=True)) or p


def local_voice_engines() -> list[str]:
    """Local voice models already installed by the voice step (read-only probe)."""
    try:
        from lib.paths import user_home  # noqa: WPS433 — optional helper
        vdir = user_home() / "voices"
    except Exception:  # noqa: BLE001
        return []
    if not vdir.is_dir():
        return []
    found = []
    for d in sorted(vdir.iterdir()):
        if d.is_dir() and not d.name.startswith((".", "_")) and any(d.rglob("*")):
            found.append(d.name)
    return found


def tts_engines() -> list[str]:
    """Locally-running TTS engines. A tool that sends text to a hosted service is
    not a TTS engine for Coordn8 and is never counted here."""
    engines = []
    if sys.platform == "darwin" and have("say"):
        engines.append("say (macOS)")
    for t in ("piper", "espeak-ng", "espeak"):
        if have(t):
            engines.append(t)
    engines += [f"local voice: {e}" for e in local_voice_engines()]
    return engines


def has_tts() -> bool:
    return bool(tts_engines())


def pymod_version(mod: str) -> str | None:
    """Version string if `import mod` works in THIS interpreter, else None."""
    code, out = run([sys.executable, "-c",
                     f"import {mod} as m; print(getattr(m, '__version__', 'present'))"],
                    timeout=TIMEOUT_QUICK)
    return (out.strip().splitlines()[-1][:60] if out.strip() else "present") if code == 0 else None


def has_pymod(mod: str) -> bool:
    return pymod_version(mod) is not None


def has_manim() -> bool:
    # A runnable Manim anywhere on this machine counts: the console script on PATH,
    # a pipx / uv-tool / ~/.local install, or `python -m manim` for ANY local python3.
    # (Checking only this interpreter re-installed Manim on machines that had it.)
    if have("manim") or has_pymod("manim"):
        return True
    try:
        from lib.common import find_manim
    except Exception:  # noqa: BLE001
        return False
    return find_manim() is not None


def _vendor_manifest() -> dict:
    manifest = _VENDOR_DIR / "manifest.json"
    if not manifest.exists():
        return {}
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _vendored(names: tuple[str, ...] | None = None) -> list[dict]:
    rows = [e for e in _vendor_manifest().get("vendored", []) if isinstance(e, dict)]
    out = []
    for e in rows:
        lib = e.get("lib")
        if names is not None and lib not in names:
            continue
        f = e.get("file")
        if f and not Path(f).exists():
            continue
        out.append(e)
    return out


def _libs_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("c8_fetch_visual_libs", FETCH_LIBS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def has_visual_libs() -> bool:
    """True if the vendored motion/graph JS libs are present locally (offline-ready).
    The layout library is reported separately and does not count here."""
    layout = ("dagre",)
    return any(e.get("lib") not in layout for e in _vendored())


def has_layout_lib() -> bool:
    """True if the layout library is vendored AND its bytes match the pinned sha256."""
    try:
        libs = _libs_module().LIBS
    except Exception:  # noqa: BLE001
        return False
    spec = libs.get("dagre")
    if not spec:
        return False
    f = _VENDOR_DIR / spec["file"]
    if not f.exists():
        return False
    import hashlib
    return hashlib.sha256(f.read_bytes()).hexdigest() == spec.get("sha256")


def has_icons() -> bool:
    """True if an open-licensed icon set is vendored locally (offline-ready)."""
    manifest = _VENDOR_DIR / "icons" / "icons_manifest.json"
    if not manifest.exists():
        return False
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        return bool(data.get("vendored"))
    except Exception:  # noqa: BLE001
        return False


def pos_tagger_version() -> str | None:
    """Detect (never install) a local POS tagger: the toolkit imports AND its
    English tagger model is on disk."""
    code, out = run([sys.executable, "-c",
                     "import nltk; nltk.data.find('taggers/averaged_perceptron_tagger_eng'); "
                     "print(nltk.__version__)"], timeout=TIMEOUT_QUICK)
    return out.strip().splitlines()[-1][:40] if code == 0 and out.strip() else None


# --- package-manager discovery ---------------------------------------------

@functools.lru_cache(maxsize=1)
def _sudo_prefix() -> tuple[tuple[str, ...] | None, str | None]:
    """(prefix, reason). Root needs no prefix. Otherwise sudo is used ONLY when it
    is already non-interactive (`sudo -n true` succeeds); it is never prompted."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return (), None
    if not have("sudo"):
        return None, "not running as root and no sudo on PATH"
    code, out = run(["sudo", "-n", "true"], timeout=10)
    if code == 0:
        return ("sudo", "-n"), None
    return None, ("sudo needs a password and this run never prompts "
                  f"(`sudo -n true` exit {code})")


def os_pkg_manager_info() -> dict:
    """{name, prefix, reason}: this OS's package manager and how to call it without
    any prompt, or name=None with the specific reason it cannot be used."""
    if sys.platform == "darwin" and have("brew"):
        return {"name": "brew", "prefix": ["brew", "install"], "reason": None}  # brew never uses sudo
    for name, tool, args in (("apt", "apt-get", ["install", "-y"]),
                             ("dnf", "dnf", ["install", "-y"]),
                             ("pacman", "pacman", ["-S", "--noconfirm"]),
                             ("apk", "apk", ["add", "--no-interactive"])):
        if have(tool):
            sudo, why = _sudo_prefix()
            if sudo is None:
                return {"name": None, "prefix": [], "manager": name,
                        "reason": f"{name} found but {why}"}
            return {"name": name, "prefix": list(sudo) + [tool] + args, "reason": None}
    if have("winget"):
        return {"name": "winget", "prefix": ["winget", "install", "-e", "--silent",
                                             "--accept-package-agreements",
                                             "--accept-source-agreements", "--id"],
                "reason": None}
    if have("choco"):
        return {"name": "choco", "prefix": ["choco", "install", "-y"], "reason": None}
    return {"name": None, "prefix": [],
            "reason": "no OS package manager (brew/apt/dnf/pacman/apk/winget/choco) on PATH"}


def os_pkg_manager() -> tuple[str | None, list[str]]:
    """Return (name, install_prefix_cmd) for this OS's package manager."""
    info = os_pkg_manager_info()
    return info["name"], info["prefix"]


def pip_install(pkgs: list[str]) -> tuple[bool, str]:
    """pip install (non-interactive) with a --user fallback for locked-down envs.

    `--no-input` is explicit here on top of PIP_NO_INPUT in the env, so pip never
    stalls on a prompt even if the environment variable is stripped."""
    base = [sys.executable, "-m", "pip", "install", "--no-input", "--upgrade"]
    code, out = run(base + pkgs)
    if code == 0:
        return True, out
    if code == 124:
        return False, out  # timed out: don't spend a second timeout on --user
    # retry into the user site (no root needed)
    code, out = run(base + ["--user"] + pkgs)
    return code == 0, out


def _ffmpeg_fix(name: str | None) -> str:
    return {"brew": "brew install ffmpeg",
            "apt": "sudo apt-get install -y ffmpeg",
            "dnf": "sudo dnf install -y ffmpeg",
            "pacman": "sudo pacman -S --noconfirm ffmpeg",
            "apk": "sudo apk add ffmpeg",
            "winget": "winget install -e --id Gyan.FFmpeg",
            "choco": "choco install -y ffmpeg"}.get(
        name or "", "download a static ffmpeg build from https://ffmpeg.org/download.html "
                    "and put ffmpeg + ffprobe on PATH")


# --- installers (sudo-free path first) --------------------------------------

def ensure_ffmpeg(dry: bool) -> dict:
    if has_ffmpeg():
        return {"tool": "ffmpeg", "status": "already-present", "verified": True,
                "version": ffmpeg_version()}
    info = os_pkg_manager_info()
    name, prefix = info["name"], info["prefix"]
    if dry:
        plan = (f"{name} install ffmpeg" if name else
                f"NO usable package manager for ffmpeg ({info['reason']})")
        return {"tool": "ffmpeg", "status": "would-install", "plan": plan,
                "fix": _ffmpeg_fix(name or info.get("manager"))}
    if not name:
        return {"tool": "ffmpeg", "status": "cannot-install", "verified": False,
                "reason": info["reason"],
                "fix": _ffmpeg_fix(info.get("manager"))}
    log(f"installing ffmpeg via {name} …")
    code, out = run(prefix + ["ffmpeg"])
    ok = has_ffmpeg()
    return {"tool": "ffmpeg", "status": "installed" if ok else "install-failed",
            "verified": ok, "via": name, "version": ffmpeg_version() if ok else None,
            "detail": None if ok else out,
            "reason": None if ok else f"{name} install ffmpeg failed (exit {code})",
            "fix": None if ok else _ffmpeg_fix(name)}


CHROME_FIX = ("python3 -m pip install --user playwright && "
              "python3 -m playwright install chromium")


def ensure_chrome(dry: bool) -> dict:
    if has_chrome():
        return {"tool": "chrome", "status": "already-present", "verified": True,
                "version": chrome_version()}
    if dry:
        return {"tool": "chrome", "status": "would-install",
                "plan": "pip install playwright && playwright install chromium "
                        "(sudo-free)", "fix": CHROME_FIX}
    # SUDO-FREE primary path: playwright's bundled chromium.
    log("installing headless Chromium via playwright (sudo-free) …")
    ok_pip, out1 = pip_install(["playwright"])
    if ok_pip:
        # `python -m playwright install chromium` works even if the console
        # script isn't on PATH.
        code, out2 = run([sys.executable, "-m", "playwright", "install", "chromium"])
    else:
        out2 = f"pip could not install playwright: {out1[-200:]}"
    if has_chrome():
        return {"tool": "chrome", "status": "installed", "verified": True,
                "via": "playwright (sudo-free)", "version": chrome_version()}
    # Fallback: OS package manager (chromium).
    info = os_pkg_manager_info()
    name, prefix = info["name"], info["prefix"]
    if name:
        pkg = {"brew": "chromium", "apt": "chromium-browser", "dnf": "chromium",
               "pacman": "chromium", "apk": "chromium",
               "winget": "Google.Chrome", "choco": "googlechrome"}.get(name, "chromium")
        log(f"playwright path didn't yield a browser; trying {name} {pkg} …")
        run((prefix + [pkg]) if name != "brew" else ["brew", "install", "--cask", "chromium"])
    ok = has_chrome()
    return {"tool": "chrome", "status": "installed" if ok else "install-failed",
            "verified": ok, "via": "os-pkg" if ok else None,
            "version": chrome_version() if ok else None,
            "detail": None if ok else (out2 or "no browser after install attempts"),
            "reason": None if ok else ("no headless browser after the playwright path"
                                       + (f" and {name}" if name else
                                          f" (OS package path unavailable: {info['reason']})")),
            "fix": None if ok else CHROME_FIX}


TTS_FIX = "coordn8 voices install   (local open-source voices; runs after this step)"


def ensure_tts(dry: bool) -> dict:
    """DETECT a local TTS engine; never install one here.

    Narration voices belong to the voice step that runs after dependencies
    (`coordn8 voices install`, local open-source engines, nothing sent out).
    macOS `say` is built in and keeps the base video pipeline unblocked."""
    engines = tts_engines()
    if engines:
        return {"tool": "tts", "status": "already-present", "verified": True,
                "version": ", ".join(engines), "engines": engines}
    if dry:
        return {"tool": "tts", "status": "deferred-to-voice-step",
                "plan": "no speech tool is installed by this step; local voices are "
                        "installed by the voice step (`coordn8 voices install`)",
                "fix": TTS_FIX}
    return {"tool": "tts", "status": "skipped", "verified": False,
            "reason": "no local TTS engine found; this step never installs a speech "
                      "tool (the voice step does, with local open-source engines)",
            "fix": TTS_FIX}


def _pip_fix(pip_name: str) -> str:
    return f"python3 -m pip install --user {pip_name}"


def ensure_pymod(mod: str, pip_name: str, dry: bool) -> dict:
    ver = pymod_version(mod)
    if ver:
        return {"tool": mod, "status": "already-present", "verified": True, "version": ver}
    if dry:
        return {"tool": mod, "status": "would-install", "plan": f"pip install {pip_name}",
                "fix": _pip_fix(pip_name)}
    log(f"installing {mod} (pip, sudo-free) …")
    ok, out = pip_install([pip_name])
    ver = pymod_version(mod)
    return {"tool": mod, "status": "installed" if ver else "install-failed",
            "verified": bool(ver), "via": "pip", "version": ver,
            "detail": None if ver else out,
            "reason": None if ver else f"pip install {pip_name} did not make `import {mod}` work",
            "fix": None if ver else _pip_fix(pip_name)}


def _manim_system_deps(dry: bool) -> dict:
    """Install Manim's native deps (cairo/pango/pkg-config) via the OS pkg mgr.
    Best-effort: on many systems a wheel already bundles what's needed. We never
    fail the run over this — Manim is an enhancement."""
    info = os_pkg_manager_info()
    name, prefix = info["name"], info["prefix"]
    pkgs = {
        "brew": ["cairo", "pango", "pkg-config"],
        "apt": ["build-essential", "python3-dev", "libcairo2-dev",
                "libpango1.0-dev", "pkg-config"],
        "dnf": ["cairo-devel", "pango-devel", "pkgconf-pkg-config", "python3-devel"],
        "pacman": ["cairo", "pango", "pkgconf"],
        "apk": ["cairo-dev", "pango-dev", "pkgconfig", "python3-dev"],
    }.get(name)
    if dry:
        return {"step": "manim-system-deps",
                "plan": f"{name} install {' '.join(pkgs)}" if pkgs
                        else "no OS pkg mgr for cairo/pango (may already be present)"}
    if not name or not pkgs:
        return {"step": "manim-system-deps", "status": "skipped",
                "note": "no usable OS package manager for cairo/pango "
                        f"({info['reason'] or name}); relying on wheels"}
    log(f"installing Manim system deps via {name} …")
    code, out = run(prefix + pkgs)
    return {"step": "manim-system-deps", "via": name,
            "status": "attempted", "ok": code == 0,
            "detail": None if code == 0 else out}


MANIM_FIX = ("python3 -m pip install --user manim   (needs cairo + pango; macOS: "
             "brew install cairo pango pkg-config)")


MANIM_BUDGET = int(os.environ.get("COORDN8_MANIM_BUDGET", "180"))


def ensure_manim(dry: bool) -> dict:
    if has_manim():
        return {"tool": "manim", "status": "already-present", "verified": True,
                "kind": "enhancement", "version": pymod_version("manim") or "present"}
    if dry:
        return {"tool": "manim", "status": "would-install", "kind": "enhancement",
                "plan": "OS pkg mgr: cairo/pango/pkg-config, then pip install manim",
                "fix": MANIM_FIX}
    if os.environ.get("COORDN8_NO_MANIM") or "--no-manim" in sys.argv:
        return {"tool": "manim", "status": "skipped", "kind": "enhancement",
                "reason": "skipped on request (--no-manim / COORDN8_NO_MANIM)", "fix": MANIM_FIX}
    # Manim is an ENHANCEMENT: it must never hold the install for long. No OS-package
    # builds here, wheels only (a source build of its native parts can take tens of
    # minutes), one bounded attempt, no second --user try after a timeout.
    sysdeps = {"step": "manim-system-deps", "status": "skipped",
               "note": "not attempted during setup (slow); see fix"}
    log(f"installing Manim (pip, prebuilt wheels only, at most {MANIM_BUDGET}s) …")
    code, out = run([sys.executable, "-m", "pip", "install", "--no-input", "--only-binary=:all:",
                     "--user", "manim"], timeout=MANIM_BUDGET)
    ok = code == 0
    verified = has_manim()
    return {"tool": "manim", "kind": "enhancement",
            "status": "installed" if verified else "install-failed",
            "verified": verified, "via": "pip",
            "system_deps": sysdeps,
            "detail": None if verified else out,
            "reason": None if verified else "pip install manim did not yield a working Manim",
            "fix": None if verified else MANIM_FIX,
            "note": None if verified else
                    "Manim unavailable — math/geometry scenes fall back to the "
                    "HTML/Canvas engine. Base video is unaffected."}


def _libs_fix(only: str = "") -> str:
    return (f"python3 scripts/render/fetch_visual_libs.py --dir {_VENDOR_DIR}"
            + (f" --only {only}" if only else "") + "   (needs network)")


def ensure_visual_libs(dry: bool) -> dict:
    if has_visual_libs():
        return {"tool": "visual_libs", "status": "already-present", "verified": True,
                "kind": "enhancement",
                "version": ", ".join(f"{e.get('lib')}@{e.get('version', '?')}"
                                     for e in _vendored() if e.get("lib") != "dagre")}
    if dry:
        return {"tool": "visual_libs", "status": "would-install", "kind": "enhancement",
                "plan": f"python3 {FETCH_LIBS.name} (vendor GSAP/D3/Rough/anime + the "
                        f"layout library into {_VENDOR_DIR})", "fix": _libs_fix()}
    if not FETCH_LIBS.exists():
        return {"tool": "visual_libs", "status": "cannot-install", "verified": False,
                "kind": "enhancement", "reason": f"{FETCH_LIBS} missing",
                "fix": "reinstall Coordn8 (the vendoring script is part of the package)"}
    log("vendoring JS visual libraries (GSAP/D3/Rough/anime + layout) …")
    code, out = run([sys.executable, str(FETCH_LIBS), "--dir", str(_VENDOR_DIR)])
    verified = has_visual_libs()
    return {"tool": "visual_libs", "kind": "enhancement",
            "status": "installed" if verified else "install-failed",
            "verified": verified, "via": "fetch_visual_libs.py",
            "detail": None if verified else out[-300:],
            "reason": None if verified else f"vendoring failed (exit {code}; likely no network)",
            "fix": None if verified else _libs_fix(),
            "note": None if verified else
                    "Could not vendor JS libs (likely no network). Scenes use "
                    "plain HTML/CSS/Canvas until fetched; base video unaffected."}


def ensure_layout_lib(dry: bool) -> dict:
    """The layered-graph layout library (dagre, MIT) — pinned version, sha256-checked."""
    try:
        spec = _libs_module().LIBS["dagre"]
    except Exception as e:  # noqa: BLE001
        return {"tool": "layout_lib", "status": "cannot-install", "verified": False,
                "kind": "enhancement", "reason": f"layout library spec unavailable: {e}",
                "fix": "reinstall Coordn8"}
    base = {"tool": "layout_lib", "kind": "enhancement", "lib": "dagre",
            "npm": spec["npm"], "pinned_version": spec["version"],
            "sha256_pinned": spec["sha256"], "license": spec["license"]}
    if has_layout_lib():
        return dict(base, status="already-present", verified=True,
                    version=f"{spec['npm']}@{spec['version']} (sha256 verified)")
    if dry:
        return dict(base, status="would-install",
                    plan=f"python3 {FETCH_LIBS.name} --only dagre (pinned "
                         f"{spec['version']}, sha256-checked) into {_VENDOR_DIR}",
                    fix=_libs_fix("dagre"))
    log("vendoring the layout library (dagre, pinned + sha256-checked) …")
    code, out = run([sys.executable, str(FETCH_LIBS), "--only", "dagre",
                     "--dir", str(_VENDOR_DIR)])
    ok = has_layout_lib()
    return dict(base, status="installed" if ok else "install-failed", verified=ok,
                via="fetch_visual_libs.py",
                version=f"{spec['npm']}@{spec['version']} (sha256 verified)" if ok else None,
                detail=None if ok else out[-300:],
                reason=None if ok else f"layout library not vendored (exit {code}; likely no network)",
                fix=None if ok else _libs_fix("dagre"),
                note=None if ok else "Diagrams fall back to hand-placed coordinates "
                                     "until the layout library is vendored.")


def ensure_icons(dry: bool) -> dict:
    if has_icons():
        return {"tool": "icons", "status": "already-present", "verified": True,
                "kind": "enhancement", "version": "vendored"}
    icon_dir = _VENDOR_DIR / "icons"
    fix = f"python3 scripts/render/fetch_icons.py --dir {icon_dir}   (needs network)"
    if dry:
        return {"tool": "icons", "status": "would-install", "kind": "enhancement",
                "plan": f"python3 {FETCH_ICONS.name} (vendor open-licensed Lucide/Tabler "
                        f"icon sets into {icon_dir})", "fix": fix}
    if not FETCH_ICONS.exists():
        return {"tool": "icons", "status": "cannot-install", "verified": False,
                "kind": "enhancement", "reason": f"{FETCH_ICONS} missing",
                "fix": "reinstall Coordn8 (the icon script is part of the package)"}
    log("vendoring open-licensed icon library (Lucide/Tabler) …")
    code, out = run([sys.executable, str(FETCH_ICONS), "--dir", str(icon_dir)])
    verified = has_icons()
    return {"tool": "icons", "kind": "enhancement",
            "status": "installed" if verified else "install-failed",
            "verified": verified, "via": "fetch_icons.py",
            "detail": None if verified else out[-300:],
            "reason": None if verified else f"icon vendoring failed (exit {code}; likely no network)",
            "fix": None if verified else fix,
            "note": None if verified else
                    "Could not vendor icons (likely no network). Scenes/pages use "
                    "drawn shapes/vendored JS until fetched; base video unaffected."}


def ensure_pos_tagger(dry: bool) -> dict:
    """OPTIONAL local POS tagger: detected, never installed (see POS_TAGGER_NOTE)."""
    ver = pos_tagger_version()
    if ver:
        return {"tool": "pos_tagger", "status": "already-present", "verified": True,
                "kind": "optional", "version": ver}
    return {"tool": "pos_tagger", "status": "skipped", "verified": False,
            "kind": "optional", "reason": POS_TAGGER_NOTE, "fix": POS_TAGGER_FIX}


# Ordered steps: (target name, result tool name, function(dry) -> dict).
STEPS = (
    ("ffmpeg", "ffmpeg", ensure_ffmpeg),
    ("chrome", "chrome", ensure_chrome),
    ("tts", "tts", ensure_tts),
    ("pillow", "PIL", lambda dry: ensure_pymod("PIL", "Pillow", dry)),
    ("numpy", "numpy", lambda dry: ensure_pymod("numpy", "numpy", dry)),
    ("manim", "manim", ensure_manim),
    ("visual_libs", "visual_libs", ensure_visual_libs),
    ("layout_lib", "layout_lib", ensure_layout_lib),
    ("icons", "icons", ensure_icons),
    ("pos_tagger", "pos_tagger", ensure_pos_tagger),
)
TARGETS = tuple(s[0] for s in STEPS)
OK_STATUSES = ("already-present", "installed")


def run_steps(want: set[str], dry: bool, steps=STEPS) -> list[dict]:
    """Run each wanted step in order. A step that raises is contained and skipped
    with its reason; a step that would start after the run's deadline is skipped
    with that reason. The next step always still runs."""
    results = []
    for target, tool, fn in steps:
        if target not in want:
            continue
        if _DEADLINE_AT is not None and time.monotonic() >= _DEADLINE_AT:
            results.append({"tool": tool, "status": "skipped", "verified": False,
                            "reason": "setup time budget (--deadline) exhausted before this step",
                            "fix": f"coordn8 setup --only {target}"})
            continue
        t0 = time.monotonic()
        try:
            r = fn(dry)
        except Exception as e:  # noqa: BLE001 — one broken step never stops the rest
            r = {"tool": tool, "status": "error-skipped", "verified": False,
                 "reason": f"step crashed: {type(e).__name__}: {e}",
                 "fix": f"coordn8 setup --only {target}   (report the error if it repeats)"}
        r.setdefault("seconds", round(time.monotonic() - t0, 2))
        results.append(r)
    return results


def _default_deadline() -> float:
    d = float(DEFAULT_DEADLINE)
    for key in ("COORDN8_STEP_TIMEOUT_DEPS", "COORDN8_STEP_TIMEOUT"):
        v = os.environ.get(key)
        if v:
            try:
                f = float(v)
            except ValueError:
                continue
            if f > 0:
                return min(d, max(1.0, f * 0.9))
    return d


def main(argv: list[str] | None = None) -> int:
    global _STEP_CAP, _DEADLINE_AT, _VENDOR_DIR, _QUIET
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="JSON only")
    ap.add_argument("--dry-run", action="store_true", help="show plan, install nothing")
    ap.add_argument("--only", nargs="*", default=None,
                    help=f"subset of {TARGETS}")
    ap.add_argument("--non-interactive", action="store_true",
                    help="never prompt (default when stdin is not a TTY; setup never "
                         "prompts in any mode — this makes it explicit)")
    ap.add_argument("--timeout", type=float, default=None,
                    help="cap, in seconds, on any single install command "
                         "(env COORDN8_SETUP_TIMEOUT)")
    ap.add_argument("--deadline", type=float, default=None,
                    help=f"whole-run time budget in seconds (default {DEFAULT_DEADLINE}, or 90%% of an "
                         "installer step timeout; env COORDN8_SETUP_DEADLINE)")
    ap.add_argument("--vendor-dir", default="_build/vendor",
                    help="where JS libraries / the layout library / icons are vendored")
    args = ap.parse_args(argv)
    dry = args.dry_run
    _QUIET = args.json
    want = set(args.only) if args.only else set(TARGETS)
    unknown = sorted(want - set(TARGETS))
    if unknown:
        sys.stderr.write(f"[coordn8:setup] unknown target(s) {unknown}; known: {list(TARGETS)}\n")
        return 2

    try:
        stdin_tty = sys.stdin.isatty()
    except (AttributeError, ValueError):
        stdin_tty = False
    non_interactive = bool(args.non_interactive or not stdin_tty
                           or os.environ.get("COORDN8_NONINTERACTIVE", "") not in ("", "0"))

    cap = args.timeout if args.timeout is not None else os.environ.get("COORDN8_SETUP_TIMEOUT")
    _STEP_CAP = float(cap) if cap not in (None, "") else None
    dl = args.deadline if args.deadline is not None else os.environ.get("COORDN8_SETUP_DEADLINE")
    deadline_s = float(dl) if dl not in (None, "") else _default_deadline()
    _DEADLINE_AT = time.monotonic() + deadline_s
    _VENDOR_DIR = Path(args.vendor_dir)

    t0 = time.monotonic()
    results = run_steps(want, dry)

    # Post-setup capability truth (re-probed, not assumed).
    caps = {
        "ffmpeg": has_ffmpeg(),
        "chrome": has_chrome(),
        "tts": has_tts(),
        "pillow": has_pymod("PIL"),
        # Enhancements (better visuals; NOT required for a base video):
        "manim": has_manim(),
        "visual_libs": has_visual_libs(),
        "layout_lib": has_layout_lib(),
        "icons": has_icons(),
        "pos_tagger": pos_tagger_version() is not None,
    }
    video_ready = caps["ffmpeg"] and caps["chrome"] and caps["tts"]

    # What (if anything) still blocks video, stated specifically. The enhancements
    # are deliberately EXCLUDED — they enhance, never block.
    blockers = []
    if not caps["ffmpeg"]:
        blockers.append("ffmpeg/ffprobe (video encode + probes)")
    if not caps["chrome"]:
        blockers.append("headless Chromium (frame capture)")
    if not caps["tts"]:
        blockers.append("a TTS engine (narration) — run `coordn8 voices install`")

    # Enhancements that are unavailable — a quality note, not a blocker.
    enhancements_missing = []
    if not caps["manim"]:
        enhancements_missing.append(
            "Manim (math/geometry scenes fall back to HTML/Canvas)")
    if not caps["visual_libs"]:
        enhancements_missing.append(
            "vendored JS libs GSAP/D3/Rough/anime (crafted motion & graphs)")
    if not caps["layout_lib"]:
        enhancements_missing.append(
            "layout library (deterministic diagram placement)")
    if not caps["icons"]:
        enhancements_missing.append(
            "vendored icon library Lucide/Tabler (crisp inline-SVG icons)")

    not_ok = [r for r in results if r.get("status") not in OK_STATUSES
              and r.get("status") not in ("would-install", "deferred-to-voice-step")]
    skipped = [{"tool": r.get("tool"), "status": r.get("status"),
                "reason": r.get("reason") or r.get("note") or r.get("detail"),
                "fix": r.get("fix")} for r in not_ok]

    summary = {
        "gate": "setup_env",
        "dry_run": dry,
        "non_interactive": non_interactive,
        "step": "dependencies",
        "install_order": ["package", "dependencies", "voices", "mcp servers",
                          "references", "sign-in offers"],
        "budget": {"step_timeout_cap_s": _STEP_CAP, "deadline_s": deadline_s,
                   "elapsed_s": round(time.monotonic() - t0, 2)},
        "actions": results,
        "skipped": skipped,
        "capabilities_after": caps,
        "video_ready": video_ready,
        "still_blocking_video": blockers,
        "enhancements_missing": enhancements_missing,
        "guidance": (
            ("Video pipeline is fully unblocked — proceed at Tier A."
             if video_ready else
             "Some BASE tools could not be installed here. Proceed at the richest "
             "tier you CAN (the page is always full quality). Do NOT write any "
             "'not supported' claim into the deliverables — a skipped video is an "
             "operator status line only, with this exact unlock list: "
             + "; ".join(blockers))
            + (
                "" if not enhancements_missing else
                " | Visual ENHANCEMENTS unavailable (video still ships, just less "
                "fancy for those scene types): " + "; ".join(enhancements_missing))
        ),
    }
    print(json.dumps(summary, indent=2))
    if not args.json:
        log(summary["guidance"])
        for s in skipped:
            log(f"skipped {s['tool']}: {s['reason']}  ->  fix: {s['fix']}")
    # Exit 0 always: setup is advisory unblocking, never a hard gate. The agent
    # reads capabilities_after to decide the tier.
    return 0


if __name__ == "__main__":
    sys.exit(main())
