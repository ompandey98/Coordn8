#!/usr/bin/env python3
"""
Coordn8 — environment capability detector & tier selector.

Probes what THIS machine can physically do and prints the richest supported
tier, plus a bounded parallelism budget the render pipeline should respect so it
never crashes the host. This is what makes Coordn8 platform-agnostic: the brain
reads this output and degrades honestly.

Tiers (see PLAYBOOK.md PHASE 0.5):
  A  full local agent:  shell + file write + ffmpeg + headless Chrome  -> webpage + video + all gates
  B  shell + file write, no ffmpeg/Chrome                              -> webpage + data/content gates (video skipped)
  C  file write only (no shell)                                        -> single-file HTML only
  D  chat only (no file write)                                         -> HTML in a code block

Because this script runs at all, we are AT LEAST Tier B (we have shell + files).
Tiers C/D are declared by the host harness, not detectable from inside a shell;
they are documented so the brain can select them when it knows shell is absent.

Usage:
    python3 scripts/env_detect.py          # human + JSON summary
    python3 scripts/env_detect.py --json   # JSON only
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.common import find_chrome, have  # noqa: E402

# Speech CLIs that send text to a hosted service. Detected only, so the operator
# can see they are present; never used, never recommended.
HOSTED_SPEECH_TOOLS = ("edge-tts", "edge-playback", "gtts-cli")


def local_voice_engines() -> list[str]:
    """Local voice models installed by `coordn8 voices install` (read-only probe)."""
    try:
        from lib.paths import user_home  # noqa: WPS433
        vdir = user_home() / "voices"
        if not vdir.is_dir():
            return []
        return [d.name for d in sorted(vdir.iterdir())
                if d.is_dir() and not d.name.startswith((".", "_")) and any(d.rglob("*"))]
    except Exception:  # noqa: BLE001
        return []


def tool_version(cmd: list[str]) -> str | None:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15,
                             stdin=subprocess.DEVNULL)
        line = (out.stdout or out.stderr).splitlines()
        return line[0].strip() if line else "present"
    except Exception:
        return None


def cpu_count() -> int:
    return os.cpu_count() or 1


def free_ram_mb() -> int | None:
    """Best-effort free RAM in MB, cross-platform, no third-party deps."""
    # Linux
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        try:
            for line in meminfo.read_text().splitlines():
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
        except Exception:
            pass
    # macOS: available ~= free + speculative + inactive + purgeable (reclaimable
    # pages count as available — matching Activity Monitor's "available"; counting
    # only truly-free pages wildly under-reports and throttles workers to 1).
    if have("vm_stat") and have("sysctl"):
        try:
            page = subprocess.run(["sysctl", "-n", "hw.pagesize"],
                                  capture_output=True, text=True, timeout=10,
                                  stdin=subprocess.DEVNULL)
            page_size = int(page.stdout.strip() or 4096)
            vm = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=10,
                                stdin=subprocess.DEVNULL)
            pages = {"Pages free": 0, "Pages speculative": 0,
                     "Pages inactive": 0, "Pages purgeable": 0}
            for line in vm.stdout.splitlines():
                for key in pages:
                    if line.startswith(key):
                        pages[key] = int(line.split(":")[1].strip().rstrip("."))
                        break
            return sum(pages.values()) * page_size // (1024 * 1024)
        except Exception:
            pass
    return None


def load_avg() -> float | None:
    try:
        return os.getloadavg()[0]
    except (OSError, AttributeError):
        return None


def parallel_budget(cores: int, free_mb: int | None) -> dict:
    """Bounded worker counts so parallel renders never swamp the machine.
    Mirrors PLAYBOOK.md PHASE R.3.

    Reports BOTH a conservative starting point (cpu_bound_workers) and the hard
    ceiling the adaptive controller (render/parallel.py) may ramp UP to when the
    machine has spare headroom (cpu_ceiling_workers). The pool self-resizes live
    between 1 and the ceiling — grow on slack, shrink fast under load — so renders
    finish as quickly as the host allows without ever crashing it."""
    cpu_workers = max(1, min(cores - 2, math.ceil(cores * 0.75)))
    cpu_ceiling = max(1, cores - 1)  # near-full utilization when the box is idle
    # A headless-Chrome frame worker can use ~600 MB; estimate high for safety.
    per_worker_mb = 600
    if free_mb is not None:
        mem_workers = max(1, free_mb // per_worker_mb)
        cpu_bound = min(cpu_workers, mem_workers)
        cpu_ceiling = min(cpu_ceiling, mem_workers)
    else:
        cpu_bound = cpu_workers
    cpu_ceiling = max(cpu_bound, cpu_ceiling)
    return {
        "cpu_bound_workers": cpu_bound,
        "cpu_ceiling_workers": cpu_ceiling,
        "io_bound_workers": min(8, max(2, cores)),
        "per_worker_mb_estimate": per_worker_mb,
        "backoff_load_threshold": round(cores * 1.5, 1),
        "adaptive": True,
        "note": "Start near cpu_bound_workers; the adaptive pool ramps live "
                "between 1 and cpu_ceiling_workers — growing when 1-min load and "
                "free RAM have slack, shrinking fast when either is over the "
                "threshold. It never floods the host and never stays "
                "under-utilized when capacity frees up.",
    }


def detect() -> dict:
    cores = cpu_count()
    free_mb = free_ram_mb()

    ffmpeg = tool_version(["ffmpeg", "-version"]) if have("ffmpeg") else None
    ffprobe = tool_version(["ffprobe", "-version"]) if have("ffprobe") else None
    chrome_path = find_chrome()
    chrome_ver = tool_version([chrome_path, "--version"]) if chrome_path else None

    # Local TTS engines only. A speech tool that sends text to a hosted service is
    # DETECTED (so the operator knows it is there) but never listed as an engine
    # and never recommended: Coordn8 sends no text to any hosted AI service.
    tts = []
    if sys.platform == "darwin" and have("say"):
        tts.append("say (macOS)")
    if have("piper"):
        tts.append("piper")
    if have("espeak") or have("espeak-ng"):
        tts.append("espeak")
    tts += [f"local voice: {e}" for e in local_voice_engines()]
    hosted_tts_present = [t for t in HOSTED_SPEECH_TOOLS if have(t)]

    can_write = os.access(".", os.W_OK)
    has_video = bool(ffmpeg and ffprobe and chrome_path)

    if not can_write:
        tier = "D"  # extremely unlikely from a shell; documented for completeness
    elif has_video:
        tier = "A"
    else:
        tier = "B"

    deliverables = {
        "A": {"webpage": True, "video": True, "all_gates": True},
        "B": {"webpage": True, "video": False, "all_gates": "data/content/structure only"},
        "C": {"webpage": True, "video": False, "all_gates": "in-model only"},
        "D": {"webpage": "code-block only", "video": False, "all_gates": "in-model only"},
    }[tier]

    unlock = []
    if not ffmpeg:
        unlock.append("install ffmpeg (brew install ffmpeg / apt install ffmpeg) to enable video encode")
    if not ffprobe:
        unlock.append("install ffprobe (bundled with ffmpeg) to enable media probes")
    if not chrome_path:
        unlock.append("install Google Chrome/Chromium, or `pip install playwright && playwright install chromium`")
    if not tts:
        unlock.append("install a local TTS engine: run `coordn8 voices install` (local "
                      "open-source voices; macOS `say` is built in)")

    return {
        "gate": "env_detect",
        "tier": tier,
        "tier_reason": (
            "shell+files+ffmpeg+chrome -> full video pipeline" if tier == "A"
            else "shell+files but missing ffmpeg/chrome -> webpage only, video skipped" if tier == "B"
            else "no file write detected"
        ),
        "deliverables": deliverables,
        "capabilities": {
            "python": sys.version.split()[0],
            "shell": True,
            "file_write": can_write,
            "ffmpeg": ffmpeg,
            "ffprobe": ffprobe,
            "chrome": chrome_ver or (chrome_path if chrome_path else None),
            "tts_engines": tts or None,
            "hosted_speech_tools_detected_not_used": hosted_tts_present or None,
        },
        "machine": {
            "platform": sys.platform,
            "cpu_cores": cores,
            "free_ram_mb": free_mb,
            "load_avg_1m": load_avg(),
        },
        "parallel_budget": parallel_budget(cores, free_mb),
        "unlock_video": unlock,
        "summary": f"Tier {tier} — " + (
            "full experience (webpage + video)" if tier == "A"
            else "webpage only; video skipped, see unlock_video" if tier == "B"
            else "degraded"
        ),
    }


def main() -> int:
    result = detect()
    json_only = "--json" in sys.argv
    if json_only:
        print(json.dumps(result, indent=2))
        return 0
    print(json.dumps(result, indent=2))
    r = result
    sys.stderr.write(
        f"\n[coordn8:env_detect] {r['summary']}\n"
        f"  cores={r['machine']['cpu_cores']} "
        f"free_ram_mb={r['machine']['free_ram_mb']} "
        f"cpu_workers={r['parallel_budget']['cpu_bound_workers']}\n"
    )
    if r["unlock_video"]:
        sys.stderr.write("  to unlock video:\n")
        for u in r["unlock_video"]:
            sys.stderr.write(f"    - {u}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
