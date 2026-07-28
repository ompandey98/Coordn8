#!/usr/bin/env python3
"""
Coordn8 — deterministic scene frame capture (video render primitive).

Renders a single scene as a PURE FUNCTION OF TIME `t`: a headless-Chrome page
that reads `?t=<seconds>` and draws the frame for that instant. This script
drives the browser to capture frames [0, duration) at the given fps into a
directory, using the bounded parallel pool so it never floods the machine, then
(optionally) encodes them to an intermediate mp4 with an explicit, fixed fps.

Because each frame is a pure function of t, a crashed/killed capture loses only
the in-flight frame — rerun and it fills the gaps. This is the crash-safe render
loop from PLAYBOOK.md 2.5 / R.3.

The scene HTML contract (you generate the scene file; this drives it):
  - Opening file:///scene.html?t=1.5333 must render EXACTLY the frame at t=1.5333s.
  - The page signals "ready" by setting document.title to "READY" (polled), or
    we fall back to a fixed settle delay.

Usage:
    # capture frames for one scene
    python3 scripts/render/capture_scene.py capture \
        --scene video/scene1.html --duration 8 --fps 30 --out _build/frames/s1

    # encode captured frames to an intermediate scene mp4 (explicit fps!)
    python3 scripts/render/capture_scene.py encode \
        --frames _build/frames/s1 --fps 30 --audio video/s1.wav --out video/s1.mp4

    # concat scene mp4s into the final silent video (serial; explicit fps)
    python3 scripts/render/capture_scene.py concat \
        --list video/scenes.txt --out video/video_noaudio.mp4
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parallel import run_pool  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.common import find_chrome, have, run  # noqa: E402


def capture(scene: Path, duration: float, fps: int, out: Path,
            settle_ms: int = 120) -> int:
    chrome = find_chrome()
    if not chrome:
        sys.stderr.write("[capture] no headless Chrome found — cannot capture. "
                         "Install Chrome/Chromium or Playwright's chromium.\n")
        return 2
    out.mkdir(parents=True, exist_ok=True)
    total = int(round(duration * fps))
    frames = list(range(total))

    def shoot(i: int):
        t = i / fps
        png = out / f"frame_{i:06d}.png"
        if png.exists() and png.stat().st_size > 0:
            return str(png)  # write-as-you-go: skip already-done frames
        url = f"{scene.resolve().as_uri()}?t={t:.4f}"
        cp = run([chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
                  "--hide-scrollbars", f"--virtual-time-budget={settle_ms}",
                  "--window-size=1920,1080",
                  f"--screenshot={png}", url], timeout=90)
        if cp.returncode != 0 or not png.exists():
            raise RuntimeError(f"frame {i} capture failed: {cp.stderr[:160]}")
        return str(png)

    res = run_pool(frames, shoot, kind="cpu", per_worker_mb=700)
    sys.stderr.write(f"[capture] scene={scene.name} frames={len(res.done)}/{total} "
                     f"failed={len(res.failed)}\n")
    return 0 if res.ok else 1


def encode(frames: Path, fps: int, out: Path, audio: Path | None) -> int:
    if not have("ffmpeg"):
        sys.stderr.write("[encode] ffmpeg not available.\n")
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-framerate", str(fps),
           "-i", str(frames / "frame_%06d.png")]
    if audio and audio.exists():
        cmd += ["-i", str(audio), "-c:a", "aac", "-shortest"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps), str(out)]
    cp = run(cmd, timeout=600)
    if cp.returncode != 0:
        sys.stderr.write(f"[encode] failed: {cp.stderr[:300]}\n")
        return 1
    sys.stderr.write(f"[encode] wrote {out}\n")
    return 0


def concat(list_file: Path, out: Path) -> int:
    if not have("ffmpeg"):
        sys.stderr.write("[concat] ffmpeg not available.\n")
        return 2
    cp = run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
              "-i", str(list_file), "-c", "copy", str(out)], timeout=600)
    if cp.returncode != 0:
        # fallback: re-encode if stream copy refuses (fps/codec mismatch)
        cp = run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                  "-i", str(list_file), "-c:v", "libx264", "-pix_fmt",
                  "yuv420p", str(out)], timeout=900)
    if cp.returncode != 0:
        sys.stderr.write(f"[concat] failed: {cp.stderr[:300]}\n")
        return 1
    sys.stderr.write(f"[concat] wrote {out}\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("capture")
    c.add_argument("--scene", required=True)
    c.add_argument("--duration", type=float, required=True)
    c.add_argument("--fps", type=int, default=30)
    c.add_argument("--out", required=True)

    e = sub.add_parser("encode")
    e.add_argument("--frames", required=True)
    e.add_argument("--fps", type=int, default=30)
    e.add_argument("--audio", default=None)
    e.add_argument("--out", required=True)

    k = sub.add_parser("concat")
    k.add_argument("--list", required=True)
    k.add_argument("--out", required=True)

    args = ap.parse_args()
    if args.cmd == "capture":
        return capture(Path(args.scene), args.duration, args.fps, Path(args.out))
    if args.cmd == "encode":
        return encode(Path(args.frames), args.fps, Path(args.out),
                      Path(args.audio) if args.audio else None)
    if args.cmd == "concat":
        return concat(Path(args.list), Path(args.out))
    return 1


if __name__ == "__main__":
    sys.exit(main())
