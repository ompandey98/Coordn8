#!/usr/bin/env python3
"""Shared helpers for Coordn8 gate + render scripts. Zero third-party deps."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

# ---- advisory result protocol ---------------------------------------------
# Gates are ADVISORY. Exit codes communicate a signal to the agent; they never
# override the agent's own project-fitted judgment.
#   0  PASS      — nothing to review
#   1  REVIEW    — findings the agent should look at (NOT a hard failure)
#   2  SKIPPED   — could not run here (missing tool / missing input); degrade honestly
#   3  ERROR     — the gate itself broke (bug in input or script)
EXIT_PASS = 0
EXIT_REVIEW = 1
EXIT_SKIPPED = 2
EXIT_ERROR = 3

# ---- caption/subtitle readability limits (single source of truth) ----------
# A subtitle cue must be READABLE at a glance — never a screen-filling paragraph.
# The builder (render/subtitles.py) segments long narration sentences to these
# limits; the gate (gates/subtitle_check.py) enforces them, so a regression that
# lets a wall-of-text cue ship is caught. Values follow broadcast norms
# (BBC/Netflix-style): ~42 chars/line, at most 2 lines, a bounded on-screen time,
# and a reading-speed ceiling in characters-per-second.
CAPTION_MAX_CHARS_PER_LINE = 42   # a comfortable single-line reading width
CAPTION_MAX_LINES = 2             # never more than two stacked lines
CAPTION_MAX_CHARS = CAPTION_MAX_CHARS_PER_LINE * CAPTION_MAX_LINES  # 84 / cue
CAPTION_MAX_CUE_SECONDS = 7.0     # no cue lingers longer than this
CAPTION_MAX_CPS = 20.0            # reading-speed ceiling (chars per second)


# Flags for EVERY direct headless Chrome launch. On macOS a Chrome started without
# them reaches for the login keychain ("Chrome Safe Storage") and pops a password /
# "keychain not found" dialog on every run. Playwright adds the first two itself.
CHROME_QUIET = ["--use-mock-keychain", "--password-store=basic", "--no-first-run",
                "--no-default-browser-check", "--disable-sync"]


def emit(result: dict) -> None:
    """Print a machine-readable result line the agent can parse, plus a human line."""
    print(json.dumps(result, ensure_ascii=False))
    status = result.get("status", "?")
    gate = result.get("gate", "gate")
    msg = result.get("summary", "")
    sys.stderr.write(f"[coordn8:{gate}] {status}: {msg}\n")


def have(tool: str) -> bool:
    return shutil.which(tool) is not None


def find_chrome() -> str | None:
    """Locate a headless-capable Chrome/Chromium across platforms.

    Order: PATH → OS default app locations → a Playwright-installed chromium
    (the sudo-free install path used by setup_env.py). Finding the Playwright
    browser is what lets `pip install playwright && playwright install chromium`
    promote a machine from Tier B to Tier A without any root access.
    """
    for name in ("google-chrome", "google-chrome-stable", "chromium",
                 "chromium-browser", "chrome"):
        p = shutil.which(name)
        if p:
            return p
    mac = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
           "/Applications/Chromium.app/Contents/MacOS/Chromium")
    for p in mac:
        if Path(p).exists():
            return p
    pw = find_playwright_chromium()
    if pw:
        return pw
    return None


def _playwright_cache_dirs() -> list[Path]:
    """Where `playwright install chromium` puts browsers, across platforms.
    Honors PLAYWRIGHT_BROWSERS_PATH if the user/agent set it."""
    import os
    dirs: list[Path] = []
    env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env and env not in ("0",):
        dirs.append(Path(env))
    home = Path.home()
    dirs += [
        home / ".cache" / "ms-playwright",                       # Linux
        home / "Library" / "Caches" / "ms-playwright",           # macOS
        home / "AppData" / "Local" / "ms-playwright",            # Windows
    ]
    return [d for d in dirs if d.exists()]


def find_playwright_chromium() -> str | None:
    """Return an executable path to a Playwright-installed chromium, or None."""
    # Candidate executable names inside each browser dir, per platform.
    patterns = (
        "chromium-*/chrome-linux/chrome",
        "chromium-*/chrome-linux/headless_shell",
        "chromium_headless_shell-*/chrome-linux/headless_shell",
        "chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium",
        "chromium-*/chrome-win/chrome.exe",
    )
    for base in _playwright_cache_dirs():
        for pat in patterns:
            hits = sorted(base.glob(pat))
            for h in hits:
                if h.exists():
                    return str(h)
    return None


def load_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    """Run a command and ALWAYS return a CompletedProcess — never raise.

    Every gate and render step shells out through here, and a gate must degrade
    HONESTLY rather than crash: a hung headless browser, a missing binary, or a
    permission error has to become a reportable "could not do this here, and why",
    not an unhandled traceback the caller cannot classify.

    A timeout or spawn failure is surfaced as a non-zero returncode with the reason
    on stderr, which is exactly what the callers already branch on. (A real
    TimeoutExpired escaping from here once turned a hung Chrome into a stack trace
    instead of an honest skip.)
    """
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        out = e.stdout or ""
        err = e.stderr or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        return subprocess.CompletedProcess(
            cmd, 124, out,
            f"{err}\n[coordn8] TIMEOUT after {timeout}s: {' '.join(map(str, cmd))[:300]} "
            f"— the tool started but did not finish here. Treat this as 'could not "
            f"verify', never as a pass.".strip())
    except (OSError, ValueError) as e:
        # Missing binary, bad path, unreadable cwd, bad argv — all reportable.
        return subprocess.CompletedProcess(
            cmd, 127, "",
            f"[coordn8] COULD NOT RUN {cmd[0] if cmd else '?'}: "
            f"{type(e).__name__}: {e}")


def find_manim(timeout: float = 20.0) -> list | None:
    """Locate a WORKING Manim anywhere on this machine, not just in the current Python.

    Order: the `manim` console script on PATH; common user-level tool dirs (pipx, uv
    tool, ~/.local/bin); `python -m manim` for this interpreter and for every other
    python3.x on PATH or in the usual Homebrew/system locations. Returns the argv
    prefix to run it (e.g. ["manim"] or ["/opt/homebrew/bin/python3.12", "-m", "manim"]),
    or None. Each probe is bounded and never raises."""
    import os
    import glob
    home = Path.home()
    cands: list = []
    w = shutil.which("manim")
    if w:
        cands.append([w])
    for c in (home / ".local/bin/manim", home / ".local/share/uv/tools/manim/bin/manim",
              home / ".local/pipx/venvs/manim/bin/manim", home / ".local/share/pipx/venvs/manim/bin/manim"):
        if c.exists():
            cands.append([str(c)])
    pys = [sys.executable]
    for name in ("python3", "python3.13", "python3.12", "python3.11", "python3.10", "python3.9"):
        w = shutil.which(name)
        if w:
            pys.append(w)
    for pat in ("/opt/homebrew/bin/python3*", "/usr/local/bin/python3*",
                str(home / ".pyenv/versions/*/bin/python3")):
        pys += sorted(glob.glob(pat))
    seen = set()
    for py in pys:
        rp = os.path.realpath(py)
        if rp in seen or not os.access(py, os.X_OK) or py.endswith("-config"):
            continue
        seen.add(rp)
        cands.append([py, "-m", "manim"])
    for argv in cands:
        cp = run(list(argv) + ["--version"], timeout=timeout)
        if cp.returncode == 0 and "manim" in (cp.stdout + cp.stderr).lower():
            return list(argv)
    return None
