#!/usr/bin/env python3
"""
Coordn8 — DOCTOR: the preflight answer to "will this run, how long will it take, and
what will it cost my machine?"

WHY THIS EXISTS
---------------
Two real incidents, both invisible until it was too late:

  * A render filled a 1 TB disk and killed the machine. Nobody knew it would need
    ~30 GB per scene, because nothing ever said so before starting.
  * Renders were "painfully slow" while the host sat idle — and separately, a naive
    capture drove the 1-minute load to 37 on 14 cores. Slow AND abusive at once.

Both are answerable BEFORE you commit an hour to a render, and both answers come from
MEASUREMENT, not estimation. `doctor` probes the actual machine: it detects the render
engine, times real frames, and projects time and disk for the job you describe. It
then gives a plain verdict — go, or don't, and exactly what to change.

Every number it reports is measured here or computed from something measured here. It
never guesses a rate, because a guessed rate is what made the disk incident possible.

Usage:
    python3 scripts/doctor.py                                   # capability + capacity
    python3 scripts/doctor.py --probe                           # + time a real frame
    python3 scripts/doctor.py --probe --duration 300 --fps 30 --scenes 11
    python3 scripts/doctor.py --json
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "render"))
from lib.common import find_chrome, have  # noqa: E402
from diskguard import (ABORT_FREE_MB, DEFAULT_CAP_GB, RESERVE_MB,  # noqa: E402
                       disk_free, disk_total, human)
from parallel import (_free_ram_mb, _load_1m, compute_worker_ceiling,  # noqa: E402
                      compute_workers)
from telemetry import fmt_duration  # noqa: E402

PROBE_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0;width:1920px;height:1080px;color:#fff;font:600 56px system-ui;
background:radial-gradient(circle at 25% 30%,#7b5cff,transparent 60%),
radial-gradient(circle at 75% 70%,#00d4ff,transparent 55%),
linear-gradient(135deg,#0b1220,#241a4a 45%,#0b3a4a);}
#t{position:absolute;left:80px;top:60px}</style></head><body>
<div id="t"></div><canvas id="c" width="1920" height="1080"></canvas><script>
function renderAt(t){document.getElementById('t').textContent='t='+t.toFixed(2)+'s';
const x=document.getElementById('c').getContext('2d');
x.clearRect(0,0,1920,1080);
for(let i=0;i<1500;i++){const a=(i*0.618+t)%1;
x.fillStyle=`rgba(${(a*255)|0},${((1-a)*200)|0},${((a*137)%255)|0},0.30)`;
x.beginPath();x.arc((i*97+t*40)%1920,(i*57+t*25)%1080,2+(i%6),0,6.28);x.fill();}}
window.renderAt=renderAt;
renderAt(parseFloat(new URLSearchParams(location.search).get('t')||'0'));
document.title='READY';</script></body></html>"""


def probe_engine_and_rate(n_frames: int = 12) -> dict:
    """MEASURE the render engine and its real frames/second on this machine.

    Times the persistent-browser path when Playwright is present, and separately
    times ONE cold browser launch so the difference is a measured fact rather than a
    claim. Also records the real byte size of a representative frame in both formats,
    since that is what the disk budget must be built from.
    """
    out: dict = {"engine": None, "frames_per_s": None, "cold_launch_s": None,
                 "frame_bytes": {}, "notes": []}
    chrome = find_chrome()
    if not chrome:
        out["notes"].append("no headless browser found — cannot render video here; "
                            "install Chrome/Chromium or `pip install playwright && "
                            "python3 -m playwright install chromium`")
        return out

    tmp = Path(tempfile.mkdtemp(prefix="coordn8-doctor-"))
    try:
        scene = tmp / "probe.html"
        scene.write_text(PROBE_HTML, encoding="utf-8")

        # --- cold launch cost (the naive path) --------------------------
        dest = tmp / "cold.jpg"
        t0 = time.monotonic()
        subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
                        "--hide-scrollbars", "--virtual-time-budget=120",
                        "--window-size=1920,1080", f"--screenshot={dest}",
                        scene.resolve().as_uri() + "?t=1.0"],
                       capture_output=True, timeout=120)
        out["cold_launch_s"] = round(time.monotonic() - t0, 2)

        # --- persistent browser (the fast path) -------------------------
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                br = pw.chromium.launch(args=["--force-color-profile=srgb",
                                              "--allow-file-access-from-files",
                                              "--hide-scrollbars", "--disable-gpu"])
                try:
                    pg = br.new_page(viewport={"width": 1920, "height": 1080},
                                     device_scale_factor=1)
                    pg.goto(scene.resolve().as_uri() + "?t=0")
                    pg.wait_for_function("() => document.title === 'READY'",
                                         timeout=15000)
                    # size both formats on a representative frame
                    for ext, kw in (("png", {"type": "png"}),
                                    ("jpg", {"type": "jpeg", "quality": 82})):
                        f = tmp / f"m.{ext}"
                        pg.evaluate("(t)=>window.renderAt(t)", 1.0)
                        pg.screenshot(path=str(f), **kw)
                        out["frame_bytes"][ext] = f.stat().st_size
                    t0 = time.monotonic()
                    for i in range(n_frames):
                        pg.evaluate("(t)=>window.renderAt(t)", i / 30.0)
                        pg.screenshot(type="jpeg", quality=82)
                    dt = time.monotonic() - t0
                    out["engine"] = "playwright-persistent"
                    out["frames_per_s"] = round(n_frames / dt, 1) if dt > 0 else None
                finally:
                    br.close()
        except Exception as e:  # noqa: BLE001
            out["engine"] = "cold-chrome-pool"
            out["notes"].append(
                f"Playwright unavailable ({type(e).__name__}) — the pipeline would "
                f"cold-launch a browser per frame at ~{out['cold_launch_s']}s each. "
                f"Install it for a large speedup: pip install playwright && "
                f"python3 -m playwright install chromium")
            if out["cold_launch_s"]:
                out["frames_per_s"] = round(1.0 / out["cold_launch_s"], 2)
            if dest.exists():
                out["frame_bytes"]["jpg"] = dest.stat().st_size
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Coordn8 preflight doctor.")
    ap.add_argument("--probe", action="store_true",
                    help="MEASURE the render engine and real frames/second (a few seconds)")
    ap.add_argument("--duration", type=float, default=None,
                    help="seconds per scene, to project time and disk")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--scenes", type=int, default=1)
    ap.add_argument("--path", default=".", help="volume to report on")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    path = Path(args.path)
    cores = __import__("os").cpu_count() or 1
    ram = _free_ram_mb()
    load = _load_1m()
    free = disk_free(path)
    total = disk_total(path)

    tools = {
        "browser": find_chrome(),
        "ffmpeg": shutil.which("ffmpeg"),
        "ffprobe": shutil.which("ffprobe"),
        "node": shutil.which("node"),
        "manim": shutil.which("manim"),
        "playwright": None,
    }
    try:
        import playwright  # noqa: F401
        tools["playwright"] = "importable"
    except Exception:  # noqa: BLE001
        pass

    tier = ("A" if (tools["browser"] and tools["ffmpeg"]) else
            "B" if tools["browser"] or tools["ffmpeg"] else "C")

    report: dict = {
        "tier": tier,
        "tools": tools,
        "capacity": {
            "cores": cores,
            "load_1m": load,
            "load_per_core": round(load / cores, 2) if load is not None else None,
            "free_ram_mb": ram,
            "pool_start_workers": compute_workers("cpu", 700),
            "pool_ceiling_workers": compute_worker_ceiling("cpu", 700),
        },
        "volume": {
            "path": str(path.resolve()),
            "free": human(free), "total": human(total),
            "free_bytes": free,
            "reserve": human(RESERVE_MB * 1024 * 1024),
            "abort_floor": human(ABORT_FREE_MB * 1024 * 1024),
            "per_render_cap": f"{DEFAULT_CAP_GB} GB",
        },
        "findings": [],
        "verdict": "ok",
    }

    if args.probe:
        report["probe"] = probe_engine_and_rate()

    # --- projection for a described job, from MEASURED numbers only ------
    if args.duration:
        frames_per_scene = int(round(args.duration * args.fps))
        total_frames = frames_per_scene * max(1, args.scenes)
        proj: dict = {"frames_per_scene": frames_per_scene,
                      "total_frames": total_frames,
                      "scenes": args.scenes, "fps": args.fps}
        pr = report.get("probe") or {}
        rate = pr.get("frames_per_s")
        if rate:
            proj["measured_frames_per_s"] = rate
            proj["render_seconds"] = round(total_frames / rate, 1)
            proj["render_time"] = fmt_duration(total_frames / rate)
        else:
            proj["render_time"] = "unknown (run with --probe to measure it)"

        fb = pr.get("frame_bytes") or {}
        if fb:
            best_ext = min(fb, key=lambda k: fb[k])
            proj["frame_bytes_measured"] = {k: human(v) for k, v in fb.items()}
            proj["best_format"] = best_ext
            per_scene = fb[best_ext] * frames_per_scene
            proj["disk_per_scene_ondisk"] = human(per_scene)
            proj["disk_all_scenes_ondisk"] = human(per_scene * max(1, args.scenes))
            proj["disk_streaming"] = "~0 (frames never touch disk)"
            if per_scene > DEFAULT_CAP_GB * 1024 ** 3:
                report["findings"].append(
                    f"on-disk capture of ONE scene would hold "
                    f"{human(per_scene)}, over the {DEFAULT_CAP_GB} GB per-render cap "
                    f"— use `stream` (writes no frames) or lower --fps.")
            if per_scene * max(1, args.scenes) > (free - RESERVE_MB * 1024 * 1024):
                report["findings"].append(
                    f"keeping frames for all {args.scenes} scene(s) would need "
                    f"{human(per_scene * args.scenes)} but only {human(free)} is free "
                    f"— encode and reclaim EACH scene before capturing the next, or "
                    f"use `stream`.")
        report["projection"] = proj

    # --- health findings ------------------------------------------------
    if not tools["browser"]:
        report["findings"].append("no headless browser — no video is possible here")
    if not tools["ffmpeg"]:
        report["findings"].append("no ffmpeg — frames could be captured but not encoded")
    if not tools["playwright"]:
        report["findings"].append(
            "Playwright is NOT installed. The pipeline would cold-launch a browser per "
            "frame, which is both far slower and much harder on the host. Install it: "
            "pip install playwright && python3 -m playwright install chromium")
    if free < ABORT_FREE_MB * 1024 * 1024:
        report["findings"].append(
            f"CRITICAL: only {human(free)} free — below the "
            f"{human(ABORT_FREE_MB * 1024 * 1024)} abort floor. Reclaim space "
            f"(`coordn8 clean <build>`) before rendering.")
        report["verdict"] = "blocked"
    if ram is not None and ram < 1024:
        report["findings"].append(f"only {ram} MB RAM available — workers will be capped")
    if load is not None and load > cores * 1.5:
        report["findings"].append(
            f"the machine is already busy (load {load:.1f} on {cores} cores) — a render "
            f"now will be slow and will compete with whatever else is running")

    if report["verdict"] != "blocked":
        report["verdict"] = "ok" if not report["findings"] else "review"

    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return 0 if report["verdict"] == "ok" else 1

    # human report
    print("Coordn8 doctor")
    print("=" * 66)
    print(f"  tier            {tier}")
    for k, v in tools.items():
        print(f"  {k:<15} {v or '— missing'}")
    c = report["capacity"]
    print(f"  cores           {c['cores']} (load {c['load_1m']}, "
          f"{c['load_per_core']}/core)")
    print(f"  free RAM        {c['free_ram_mb']} MB")
    print(f"  worker budget   start {c['pool_start_workers']} → "
          f"ceiling {c['pool_ceiling_workers']}")
    v = report["volume"]
    print(f"  volume          {v['free']} free of {v['total']}  "
          f"(reserve {v['reserve']}, abort below {v['abort_floor']})")
    if report.get("probe"):
        p = report["probe"]
        print("-" * 66)
        print(f"  engine          {p['engine']}")
        print(f"  measured rate   {p['frames_per_s']} frames/s")
        print(f"  cold launch     {p['cold_launch_s']}s per frame "
              f"(the naive path, for comparison)")
        if p.get("frame_bytes"):
            print("  frame size      "
                  + ", ".join(f"{k}={human(x)}" for k, x in p["frame_bytes"].items()))
        for n in p.get("notes", []):
            print(f"  note            {n}")
    if report.get("projection"):
        pr = report["projection"]
        print("-" * 66)
        print(f"  job             {pr['scenes']} scene(s) x {pr['frames_per_scene']} "
              f"frames @ {pr['fps']}fps = {pr['total_frames']:,} frames")
        print(f"  render time     {pr.get('render_time')}")
        if pr.get("best_format"):
            print(f"  best format     {pr['best_format']} "
                  f"({pr['frame_bytes_measured']})")
            print(f"  disk (on-disk)  {pr['disk_per_scene_ondisk']} per scene, "
                  f"{pr['disk_all_scenes_ondisk']} for all")
            print(f"  disk (stream)   {pr['disk_streaming']}")
    print("=" * 66)
    if report["findings"]:
        print(f"VERDICT: {report['verdict'].upper()}")
        for f in report["findings"]:
            print(f"  - {f}")
    else:
        print("VERDICT: OK — tooling present, capacity sane, space sufficient.")
    return 0 if report["verdict"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
