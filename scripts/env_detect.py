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


def tool_version(cmd: list[str]) -> str | None:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
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
                                  capture_output=True, text=True, timeout=10)
            page_size = int(page.stdout.strip() or 4096)
            vm = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=10)
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
    Mirrors PLAYBOOK.md PHASE R.3."""
    cpu_workers = max(1, min(cores - 2, math.ceil(cores * 0.75)))
    # A headless-Chrome frame worker can use ~600 MB; estimate high for safety.
    per_worker_mb = 600
    if free_mb is not None:
        mem_workers = max(1, free_mb // per_worker_mb)
        cpu_bound = min(cpu_workers, mem_workers)
    else:
        cpu_bound = cpu_workers
    return {
        "cpu_bound_workers": cpu_bound,
        "io_bound_workers": min(8, max(2, cores)),
        "per_worker_mb_estimate": per_worker_mb,
        "backoff_load_threshold": round(cores * 1.5, 1),
        "note": "Spawn at most cpu_bound_workers concurrent CPU workers; pause "
                "spawning if 1-min load exceeds backoff_load_threshold or free "
                "RAM drops below one worker's estimate.",
    }


def detect() -> dict:
    cores = cpu_count()
    free_mb = free_ram_mb()

    ffmpeg = tool_version(["ffmpeg", "-version"]) if have("ffmpeg") else None
    ffprobe = tool_version(["ffprobe", "-version"]) if have("ffprobe") else None
    chrome_path = find_chrome()
    chrome_ver = tool_version([chrome_path, "--version"]) if chrome_path else None

    tts = []
    if sys.platform == "darwin" and have("say"):
        tts.append("say (macOS)")
    if have("edge-tts"):
        tts.append("edge-tts")
    if have("piper"):
        tts.append("piper")
    if have("espeak") or have("espeak-ng"):
        tts.append("espeak")

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
        unlock.append("install a TTS engine (macOS `say` is built in; else `pip install edge-tts` or piper)")

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
