#!/usr/bin/env python3
"""
Coordn8 — deterministic scene frame capture (video render primitive).

The video is a MULTI-ENGINE blend on ONE uniform timeline. The best tool renders
each scene, and every engine's output lands as the SAME `frame_%06d.png` sequence
so the identical `encode` + `concat` path joins them (same fps, resolution, pixel
format) — no special-casing downstream. Two engines feed this timeline:

  capture  — a headless-Chrome page rendered as a PURE FUNCTION OF TIME `t`:
             open scene.html?t=<seconds> and it draws the frame for that instant.
             Best for software/architecture/flow scenes (HTML/CSS/SVG/Canvas, and
             the vendored libs — GSAP eased timelines, D3 graphs, Rough.js warmth).
  manim    — render a Manim (3Blue1Brown's engine) scene to its mp4, then ffmpeg
             re-extracts frames into the SAME frame model. Best for math, precise
             geometry, and continuous transforms — the "3b1b look".

Because each Chrome frame is a pure function of t, a crashed/killed capture loses
only the in-flight frame — rerun and it fills the gaps. This is the crash-safe
render loop from PLAYBOOK.md 2.5 / R.3. Manim renders atomically per scene, then
re-extraction is likewise idempotent.

The scene HTML contract (you generate the scene file; this drives it):
  - Opening file:///scene.html?t=1.5333 must render EXACTLY the frame at t=1.5333s.
  - The page signals "ready" by setting document.title to "READY" (polled), or
    we fall back to a fixed settle delay.

Usage:
    # capture frames for one HTML/Canvas scene
    python3 scripts/render/capture_scene.py capture \
        --scene video/scene1.html --duration 8 --fps 30 --out _build/frames/s1

    # render a Manim scene into the SAME frame timeline (math/geometry scenes)
    python3 scripts/render/capture_scene.py manim \
        --scene video/eqs.py --name FieldLines --fps 30 --out _build/frames/s2 \
        --resolution 1920,1080

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


def _manim_bin() -> list[str] | None:
    """Return an argv prefix that runs Manim, or None if unavailable.
    Prefer the console script; fall back to `python -m manim` so a pip-installed
    Manim without a PATH shim still works."""
    if have("manim"):
        return ["manim"]
    cp = run([sys.executable, "-c", "import manim"], timeout=60)
    if cp.returncode == 0:
        return [sys.executable, "-m", "manim"]
    return None


def _find_manim_mp4(media_dir: Path, name: str) -> Path | None:
    """Locate the mp4 Manim wrote for scene `name` under its media tree.
    Manim nests output as media/videos/<file>/<quality>/<Scene>.mp4; we glob for
    the scene-named file and take the most recently modified match."""
    hits = [p for p in media_dir.rglob(f"{name}.mp4")]
    if not hits:
        hits = list(media_dir.rglob("*.mp4"))
    if not hits:
        return None
    return max(hits, key=lambda p: p.stat().st_mtime)


def manim_render(scene: Path, name: str, fps: int, out: Path,
                 resolution: str = "1920,1080") -> int:
    """Render a Manim scene, then re-extract its frames into the SAME
    frame_%06d.png timeline the rest of the pipeline consumes.

    This is what lets Manim math/geometry scenes blend seamlessly with the
    Chrome-captured software scenes: after this returns, `out` holds exactly the
    frame model `encode` expects, at the same fps/resolution.
    """
    # Validate the INPUT WE WERE GIVEN before blaming the environment. Checking
    # tool availability first means a mistyped path gets reported as "Manim not
    # installed", sending the caller off to install something they already have —
    # a wrong diagnosis is worse than no diagnosis.
    if not scene.exists():
        sys.stderr.write(f"[manim] scene file not found: {scene} — check the "
                         f"--scene path (this is a bad input, not a missing tool).\n")
        return 2
    mbin = _manim_bin()
    if not mbin:
        sys.stderr.write(
            "[manim] Manim not installed — cannot render this scene here. "
            "Install with `pip install manim` (+ system deps: macOS "
            "`brew install cairo pango pkg-config`, Debian `apt install "
            "libcairo2-dev libpango1.0-dev`), or render this scene with the "
            "HTML/Canvas engine instead. Skipping is honest; faking is not.\n")
        return 2
    if not have("ffmpeg"):
        sys.stderr.write("[manim] ffmpeg not available — needed to re-extract "
                         "Manim frames into the unified timeline.\n")
        return 2

    out.mkdir(parents=True, exist_ok=True)
    try:
        w, h = (int(x) for x in resolution.split(","))
    except ValueError:
        sys.stderr.write(f"[manim] bad --resolution {resolution!r}; want W,H\n")
        return 2

    media_dir = out.parent / f"_manim_{name}"
    media_dir.mkdir(parents=True, exist_ok=True)
    # -qh high quality; pin fps + resolution so it matches the rest of the video.
    cmd = mbin + ["render", "-qh", "--fps", str(fps),
                  "--resolution", f"{w},{h}",
                  "--media_dir", str(media_dir),
                  str(scene), name]
    cp = run(cmd, timeout=1800)  # complex scenes can be slow
    if cp.returncode != 0:
        sys.stderr.write(f"[manim] render failed: {cp.stderr[:400]}\n")
        return 1

    mp4 = _find_manim_mp4(media_dir, name)
    if not mp4:
        sys.stderr.write("[manim] render reported success but no mp4 was found "
                         f"under {media_dir} — treating as failure.\n")
        return 1

    # Re-extract into the unified frame model at the pipeline fps.
    cp = run(["ffmpeg", "-y", "-i", str(mp4), "-r", str(fps),
              str(out / "frame_%06d.png")], timeout=900)
    n = len(list(out.glob("frame_*.png")))
    if cp.returncode != 0 or n == 0:
        sys.stderr.write(f"[manim] frame re-extraction failed: {cp.stderr[:300]}\n")
        return 1
    sys.stderr.write(f"[manim] scene={name} rendered -> {n} frames in {out} "
                     f"(unified {w}x{h}@{fps}, from {mp4.name})\n")
    return 0


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

    m = sub.add_parser("manim")
    m.add_argument("--scene", required=True, help="python file with the Manim scene")
    m.add_argument("--name", required=True, help="Scene class name to render")
    m.add_argument("--fps", type=int, default=30)
    m.add_argument("--out", required=True, help="frame dir (unified frame_%%06d.png)")
    m.add_argument("--resolution", default="1920,1080", help="W,H")

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
    if args.cmd == "manim":
        return manim_render(Path(args.scene), args.name, args.fps,
                            Path(args.out), args.resolution)
    if args.cmd == "encode":
        return encode(Path(args.frames), args.fps, Path(args.out),
                      Path(args.audio) if args.audio else None)
    if args.cmd == "concat":
        return concat(Path(args.list), Path(args.out))
    return 1


if __name__ == "__main__":
    sys.exit(main())
