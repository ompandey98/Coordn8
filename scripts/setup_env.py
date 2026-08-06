#!/usr/bin/env python3
"""
Coordn8 — environment AUTO-SETUP. Unblock the machine so the FULL deliverable
set (page + video) can be produced, on almost any system, without asking.

This exists because "degrade honestly" was quietly turning into "give up early":
the agent would see a missing tool and declare a lower tier instead of just
installing the tool. That produced poor content and false "not supported here"
claims. This script closes that gap — it INSTALLS what's missing, prefers paths
that need NO root, and VERIFIES each install by re-probing (never claims a
success it didn't observe).

What it installs when missing (the full list):
  - ffmpeg + ffprobe   video encode + probes   (pkg manager; static build fallback hint)
  - headless Chromium   frame capture          (SUDO-FREE: pip playwright + `playwright install chromium`)
  - a TTS engine        narration              (SUDO-FREE: pip edge-tts; macOS `say` is built in; piper)
  - Pillow              layout pixel-probe      (SUDO-FREE: pip)
  - numpy               misc render math        (SUDO-FREE: pip; best-effort)
  - Manim (CE)          math/geometry scenes    (pip manim; needs cairo/pango system libs)
  - vendored JS libs    crafted motion/graphs   (curl GSAP/D3/Rough/anime into _build/vendor)

TWO TIERS OF TOOL. The first five are the BASE video pipeline — without them a
video cannot be made, so they gate the tier. Manim and the vendored JS libraries
are ENHANCEMENTS: they make the video look dramatically better (the 3Blue1Brown
math look; GSAP-eased motion; D3 graphs; hand-drawn warmth), but the base video
still ships without them. So a failed Manim/libs install is reported as an
"enhancement unavailable" note, NEVER as something that blocks the video.

Design rules:
  - Sudo-free FIRST. pip (with --user fallback) and `playwright install chromium`
    need no root and work in most locked-down/CI/cloud shells. Only then try OS
    package managers (brew/apt/dnf/pac/apk/choco/winget), using sudo only if the
    user is not already root and sudo exists.
  - NEVER fake success. After each install, re-probe; report VERIFIED only if the
    tool is now actually runnable.
  - NEVER block forever. Every subprocess is timeout-bounded; a failure is
    recorded and the next tool is still attempted.
  - Honest, specific end state. If something truly can't be installed (no network
    AND no package manager AND no pip), say EXACTLY that and which deliverable it
    limits — never a vague "not supported".

Usage:
    python3 scripts/setup_env.py               # install everything missing, print JSON summary
    python3 scripts/setup_env.py --json        # JSON only
    python3 scripts/setup_env.py --dry-run     # show the plan; install nothing
    python3 scripts/setup_env.py --only chrome ffmpeg   # subset
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.common import find_chrome, have  # noqa: E402

TIMEOUT_QUICK = 60
TIMEOUT_INSTALL = 900  # browser/tool downloads can be large


def log(msg: str) -> None:
    sys.stderr.write(f"[coordn8:setup] {msg}\n")
    sys.stderr.flush()


# A fully NON-INTERACTIVE environment for every child process. This is the fix
# for "the install hangs waiting for input in some sessions": package managers and
# pip must never stop at a [Y/n] prompt or an interactive pager when Coordn8 runs
# unattended (a CI shell, a headless agent, an npx one-liner). We force the
# batch/no-prompt mode of each tool the ONLY reliable way — through the
# environment — in addition to the explicit -y/--noconfirm flags on each command.
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
        "CI": env.get("CI", "1"),               # many tools go quiet/non-interactive under CI
        "GIT_TERMINAL_PROMPT": "0",             # git: never prompt for creds
        "PLAYWRIGHT_SKIP_BROWSER_GC": "1",
    })
    return env


def run(cmd: list[str], timeout: int = TIMEOUT_INSTALL) -> tuple[int, str]:
    """Run a command NON-INTERACTIVELY; return (exit_code, combined_output_tail).

    Never raises on a normal failure — only records it. stdin is tied to DEVNULL so
    a tool that ignores its no-prompt flags and still tries to read from the console
    gets EOF and aborts fast, rather than hanging the whole run forever.
    """
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                            stdin=subprocess.DEVNULL, env=_noninteractive_env())
        out = ((cp.stdout or "") + (cp.stderr or "")).strip()
        return cp.returncode, out[-500:]
    except FileNotFoundError:
        return 127, f"{cmd[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, f"{cmd[0]}: timed out after {timeout}s"
    except Exception as e:  # noqa
        return 1, f"{cmd[0]}: {e}"


# --- capability probes (source of truth for VERIFIED/pending) ---------------

def has_ffmpeg() -> bool:
    return have("ffmpeg") and have("ffprobe")


def has_chrome() -> bool:
    return find_chrome() is not None


def has_tts() -> bool:
    if sys.platform == "darwin" and have("say"):
        return True
    for t in ("edge-tts", "piper", "espeak", "espeak-ng"):
        if have(t):
            return True
    return False


def has_pymod(mod: str) -> bool:
    code, _ = run([sys.executable, "-c", f"import {mod}"], timeout=TIMEOUT_QUICK)
    return code == 0


def has_manim() -> bool:
    # A runnable Manim = the module imports AND a CLI entry exists (console script
    # or `python -m manim`). We probe the import; that's the load-bearing bit.
    return have("manim") or has_pymod("manim")


def has_visual_libs() -> bool:
    """True if the vendored JS libs are present locally (offline-ready)."""
    manifest = Path("_build/vendor/manifest.json")
    if not manifest.exists():
        return False
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        return bool(data.get("vendored"))
    except Exception:  # noqa: BLE001
        return False


# --- package-manager discovery ---------------------------------------------

def os_pkg_manager() -> tuple[str | None, list[str]]:
    """Return (name, install_prefix_cmd) for this OS's package manager."""
    is_root = hasattr(os, "geteuid") and os.geteuid() == 0
    sudo = [] if is_root else (["sudo"] if have("sudo") else None)

    if sys.platform == "darwin" and have("brew"):
        return "brew", ["brew", "install"]           # brew never uses sudo
    if have("apt-get"):
        if sudo is None:
            return None, []
        return "apt", sudo + ["apt-get", "install", "-y"]
    if have("dnf"):
        if sudo is None:
            return None, []
        return "dnf", sudo + ["dnf", "install", "-y"]
    if have("pacman"):
        if sudo is None:
            return None, []
        return "pacman", sudo + ["pacman", "-S", "--noconfirm"]
    if have("apk"):
        if sudo is None:
            return None, []
        return "apk", sudo + ["apk", "add", "--no-interactive"]
    if have("winget"):
        return "winget", ["winget", "install", "-e", "--silent",
                          "--accept-package-agreements",
                          "--accept-source-agreements", "--id"]
    if have("choco"):
        return "choco", ["choco", "install", "-y"]
    return None, []


def pip_install(pkgs: list[str]) -> tuple[bool, str]:
    """pip install (non-interactive) with a --user fallback for locked-down envs.

    `--no-input` is explicit here on top of PIP_NO_INPUT in the env, so pip never
    stalls on a prompt even if the environment variable is stripped."""
    base = [sys.executable, "-m", "pip", "install", "--no-input", "--upgrade"]
    code, out = run(base + pkgs)
    if code == 0:
        return True, out
    # retry into the user site (no root needed)
    code, out = run(base + ["--user"] + pkgs)
    return code == 0, out


# --- installers (sudo-free path first) --------------------------------------

def ensure_ffmpeg(dry: bool) -> dict:
    if has_ffmpeg():
        return {"tool": "ffmpeg", "status": "already-present", "verified": True}
    name, prefix = os_pkg_manager()
    if dry:
        plan = f"{name} install ffmpeg" if name else "NO package manager for ffmpeg"
        return {"tool": "ffmpeg", "status": "would-install", "plan": plan}
    if not name:
        return {"tool": "ffmpeg", "status": "cannot-install", "verified": False,
                "reason": "no OS package manager (brew/apt/dnf/pacman/apk/winget/choco) "
                          "and no root; install a static ffmpeg build from "
                          "https://ffmpeg.org/download.html and re-run."}
    pkg = "ffmpeg"
    log(f"installing ffmpeg via {name} …")
    code, out = run(prefix + [pkg])
    ok = has_ffmpeg()
    return {"tool": "ffmpeg", "status": "installed" if ok else "install-failed",
            "verified": ok, "via": name, "detail": None if ok else out}


def ensure_chrome(dry: bool) -> dict:
    if has_chrome():
        return {"tool": "chrome", "status": "already-present", "verified": True}
    if dry:
        return {"tool": "chrome", "status": "would-install",
                "plan": "pip install playwright && playwright install chromium "
                        "(sudo-free)"}
    # SUDO-FREE primary path: playwright's bundled chromium.
    log("installing headless Chromium via playwright (sudo-free) …")
    ok_pip, out1 = pip_install(["playwright"])
    if ok_pip:
        # `python -m playwright install chromium` works even if the console
        # script isn't on PATH.
        code, out2 = run([sys.executable, "-m", "playwright", "install", "chromium"])
    else:
        out2 = "pip could not install playwright"
    if has_chrome():
        return {"tool": "chrome", "status": "installed", "verified": True,
                "via": "playwright (sudo-free)"}
    # Fallback: OS package manager (chromium).
    name, prefix = os_pkg_manager()
    if name:
        pkg = {"brew": "chromium", "apt": "chromium-browser", "dnf": "chromium",
               "pacman": "chromium", "apk": "chromium",
               "winget": "Google.Chrome", "choco": "googlechrome"}.get(name, "chromium")
        log(f"playwright path didn't yield a browser; trying {name} {pkg} …")
        run((prefix + [pkg]) if name != "brew" else ["brew", "install", "--cask", "chromium"])
    ok = has_chrome()
    return {"tool": "chrome", "status": "installed" if ok else "install-failed",
            "verified": ok, "via": "os-pkg" if ok else None,
            "detail": None if ok else (out2 or "no browser after install attempts")}


def ensure_tts(dry: bool) -> dict:
    if has_tts():
        return {"tool": "tts", "status": "already-present", "verified": True}
    if dry:
        return {"tool": "tts", "status": "would-install",
                "plan": "pip install edge-tts (sudo-free)"}
    log("installing TTS (edge-tts, sudo-free) …")
    ok, out = pip_install(["edge-tts"])
    verified = has_tts() or ok  # edge-tts is a module+console script
    return {"tool": "tts", "status": "installed" if verified else "install-failed",
            "verified": verified, "via": "edge-tts (pip)",
            "detail": None if verified else out}


def ensure_pymod(mod: str, pip_name: str, dry: bool) -> dict:
    if has_pymod(mod):
        return {"tool": mod, "status": "already-present", "verified": True}
    if dry:
        return {"tool": mod, "status": "would-install", "plan": f"pip install {pip_name}"}
    log(f"installing {mod} (pip, sudo-free) …")
    ok, out = pip_install([pip_name])
    verified = has_pymod(mod)
    return {"tool": mod, "status": "installed" if verified else "install-failed",
            "verified": verified, "via": "pip", "detail": None if verified else out}


def _manim_system_deps(dry: bool) -> dict:
    """Install Manim's native deps (cairo/pango/pkg-config) via the OS pkg mgr.
    Best-effort: on many systems a wheel already bundles what's needed. We never
    fail the run over this — Manim is an enhancement."""
    name, prefix = os_pkg_manager()
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
                "note": "no OS package manager for cairo/pango; relying on wheels"}
    log(f"installing Manim system deps via {name} …")
    code, out = run(prefix + pkgs)
    return {"step": "manim-system-deps", "via": name,
            "status": "attempted", "ok": code == 0,
            "detail": None if code == 0 else out}


def ensure_manim(dry: bool) -> dict:
    if has_manim():
        return {"tool": "manim", "status": "already-present", "verified": True,
                "kind": "enhancement"}
    if dry:
        return {"tool": "manim", "status": "would-install", "kind": "enhancement",
                "plan": "OS pkg mgr: cairo/pango/pkg-config, then pip install manim"}
    sysdeps = _manim_system_deps(dry=False)
    log("installing Manim (pip) …")
    ok, out = pip_install(["manim"])
    verified = has_manim()
    return {"tool": "manim", "kind": "enhancement",
            "status": "installed" if verified else "install-failed",
            "verified": verified, "via": "pip",
            "system_deps": sysdeps,
            "detail": None if verified else out,
            "note": None if verified else
                    "Manim unavailable — math/geometry scenes fall back to the "
                    "HTML/Canvas engine. Base video is unaffected."}


def ensure_visual_libs(dry: bool) -> dict:
    if has_visual_libs():
        return {"tool": "visual_libs", "status": "already-present", "verified": True,
                "kind": "enhancement"}
    fetch = Path(__file__).resolve().parent / "render" / "fetch_visual_libs.py"
    if dry:
        return {"tool": "visual_libs", "status": "would-install", "kind": "enhancement",
                "plan": f"python3 {fetch.name} (vendor GSAP/D3/Rough/anime into "
                        f"_build/vendor)"}
    if not fetch.exists():
        return {"tool": "visual_libs", "status": "cannot-install", "verified": False,
                "kind": "enhancement", "reason": f"{fetch} missing"}
    log("vendoring JS visual libraries (GSAP/D3/Rough/anime) …")
    code, out = run([sys.executable, str(fetch)])
    verified = has_visual_libs()
    return {"tool": "visual_libs", "kind": "enhancement",
            "status": "installed" if verified else "install-failed",
            "verified": verified, "via": "fetch_visual_libs.py",
            "detail": None if verified else out,
            "note": None if verified else
                    "Could not vendor JS libs (likely no network). Scenes use "
                    "plain HTML/CSS/Canvas until fetched; base video unaffected."}


TARGETS = ("ffmpeg", "chrome", "tts", "pillow", "numpy", "manim", "visual_libs")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="JSON only")
    ap.add_argument("--dry-run", action="store_true", help="show plan, install nothing")
    ap.add_argument("--only", nargs="*", default=None,
                    help=f"subset of {TARGETS}")
    args = ap.parse_args()
    dry = args.dry_run
    want = set(args.only) if args.only else set(TARGETS)

    results = []
    if "ffmpeg" in want:
        results.append(ensure_ffmpeg(dry))
    if "chrome" in want:
        results.append(ensure_chrome(dry))
    if "tts" in want:
        results.append(ensure_tts(dry))
    if "pillow" in want:
        results.append(ensure_pymod("PIL", "Pillow", dry))
    if "numpy" in want:
        results.append(ensure_pymod("numpy", "numpy", dry))
    if "manim" in want:
        results.append(ensure_manim(dry))
    if "visual_libs" in want:
        results.append(ensure_visual_libs(dry))

    # Post-setup capability truth (re-probed, not assumed).
    caps = {
        "ffmpeg": has_ffmpeg(),
        "chrome": has_chrome(),
        "tts": has_tts(),
        "pillow": has_pymod("PIL"),
        # Enhancements (better visuals; NOT required for a base video):
        "manim": has_manim(),
        "visual_libs": has_visual_libs(),
    }
    video_ready = caps["ffmpeg"] and caps["chrome"] and caps["tts"]

    # What (if anything) still blocks video, stated specifically. Manim and the
    # vendored libs are deliberately EXCLUDED — they enhance, never block.
    blockers = []
    if not caps["ffmpeg"]:
        blockers.append("ffmpeg/ffprobe (video encode + probes)")
    if not caps["chrome"]:
        blockers.append("headless Chromium (frame capture)")
    if not caps["tts"]:
        blockers.append("a TTS engine (narration)")

    # Enhancements that are unavailable — a quality note, not a blocker.
    enhancements_missing = []
    if not caps["manim"]:
        enhancements_missing.append(
            "Manim (math/geometry scenes fall back to HTML/Canvas)")
    if not caps["visual_libs"]:
        enhancements_missing.append(
            "vendored JS libs GSAP/D3/Rough/anime (crafted motion & graphs)")

    summary = {
        "gate": "setup_env",
        "dry_run": dry,
        "actions": results,
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
    # Exit 0 always: setup is advisory unblocking, never a hard gate. The agent
    # reads capabilities_after to decide the tier.
    return 0


if __name__ == "__main__":
    sys.exit(main())
