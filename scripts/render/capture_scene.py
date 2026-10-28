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
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parallel import PoolResult, run_pool  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.common import CHROME_QUIET, find_chrome, have, run  # noqa: E402
from telemetry import (Progress, ResourceSampler, Run,  # noqa: E402
                       fmt_duration, verdict)
from diskguard import (ABORT_FREE_MB, Budget, DiskBudgetError,  # noqa: E402
                      DiskWatchdog, DEFAULT_CAP_GB, RESERVE_MB, cleanup_on_exit,
                      dir_bytes, disk_free, human, preflight_summary, reclaim)


def _shot_args(chrome: str, url: str, dest: Path, settle_ms: int,
               size: str = "1920,1080") -> list[str]:
    return [chrome, *CHROME_QUIET, "--headless=new", "--disable-gpu", "--no-sandbox",
            "--hide-scrollbars", f"--virtual-time-budget={settle_ms}",
            f"--window-size={size}", f"--screenshot={dest}", url]


def _measure_frame(chrome: str, scene: Path, fps: int, out: Path,
                   settle_ms: int, ext: str) -> tuple[Path, int, str]:
    """Capture frame 0 and return (path, REAL byte size, chosen extension).

    The budget must be built from a MEASUREMENT, not a guess: guessing is precisely
    how a render once wrote 348 GB of frames nobody had accounted for.

    With ext="auto" this probes BOTH png and jpg and keeps whichever is actually
    smaller for THIS scene's content — because neither wins universally. Flat,
    synthetic frames (solid backgrounds, big type) compress better as PNG; frames
    with gradients, glow, photographs or screenshots compress far better as JPEG.
    Measuring costs one extra screenshot and removes the need to believe a rule of
    thumb.
    """
    out.mkdir(parents=True, exist_ok=True)
    url = f"{scene.resolve().as_uri()}?t=0.0000"

    def shoot(e: str) -> tuple[Path, int]:
        dest = out / f"frame_{0:06d}.{e}"
        if dest.exists() and dest.stat().st_size > 0:
            return dest, dest.stat().st_size
        cp = run(_shot_args(chrome, url, dest, settle_ms), timeout=120)
        if cp.returncode != 0 or not dest.exists():
            raise RuntimeError(f"probe frame ({e}) failed: {cp.stderr[:200]}")
        return dest, dest.stat().st_size

    if ext != "auto":
        p, n = shoot(ext)
        return p, n, ext

    cands: list[tuple[str, Path, int]] = []
    for e in ("png", "jpg"):
        try:
            p, n = shoot(e)
            cands.append((e, p, n))
        except RuntimeError:
            continue
    if not cands:
        raise RuntimeError("probe frame failed in every format")
    cands.sort(key=lambda c: c[2])
    win_e, win_p, win_n = cands[0]
    for e, p, _n in cands[1:]:
        p.unlink(missing_ok=True)      # never keep the loser
    if len(cands) > 1:
        loser = cands[1]
        sys.stderr.write(
            f"[capture] format probe: {win_e}={human(win_n)} vs "
            f"{loser[0]}={human(loser[2])} -> using {win_e} "
            f"({loser[2] / max(1, win_n):.1f}x smaller for this scene's content)\n")
    return win_p, win_n, win_e


def _have_playwright() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def capture_fast(scene: Path, duration: float, fps: int, out: Path, *,
                 ext: str = "jpg", quality: int = 82,
                 abort_event=None, on_frame=None,
                 width: int = 1920, height: int = 1080) -> tuple[int, int]:
    """PERSISTENT-BROWSER capture: ONE browser, many frames. The speed fix.

    Cold-launching a browser per frame costs ~2.4s and, run in parallel, drives the
    1-minute load far above the core count — the worst of both worlds: painfully slow
    AND abusive to the host. Because a scene is a pure function of `t`, the page only
    needs to be loaded ONCE; each subsequent frame is a re-render plus a screenshot,
    which is milliseconds rather than seconds.

    Two ways the page can expose time, tried in order:
      1. `window.renderAt(t)` — the scene runtime's own hook (best: no navigation).
      2. re-navigating to `?t=` on the SAME page — still far cheaper than a new process.

    Returns (frames_written, browser_processes_used). Raises on a hard failure so the
    caller's cleanup/abort paths apply unchanged.
    """
    from playwright.sync_api import sync_playwright

    out.mkdir(parents=True, exist_ok=True)
    total = int(round(duration * fps))
    written = 0
    is_jpg = ext in ("jpg", "jpeg")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--force-color-profile=srgb",
                                           "--allow-file-access-from-files",
                                           "--hide-scrollbars", "--disable-gpu"])
        try:
            page = browser.new_page(viewport={"width": width, "height": height},
                                    device_scale_factor=1)
            page.goto(scene.resolve().as_uri() + "?t=0.0000")
            try:
                page.wait_for_function("() => document.title === 'READY' "
                                       "|| window.__ready === true", timeout=15000)
            except Exception:  # noqa: BLE001 — a scene may simply not signal
                page.wait_for_timeout(150)
            has_hook = bool(page.evaluate(
                "() => typeof window.renderAt === 'function' "
                "|| (window.Coordn8 && typeof window.renderFrame === 'function')"))

            for i in range(total):
                if abort_event is not None and abort_event.is_set():
                    break
                dest = out / f"frame_{i:06d}.{ext}"
                if dest.exists() and dest.stat().st_size > 0:
                    written += 1
                    if on_frame:
                        on_frame(i)
                    continue
                t = i / fps
                if has_hook:
                    page.evaluate(
                        "(t) => (window.renderAt ? window.renderAt(t) "
                        ": window.renderFrame(t))", t)
                else:
                    page.goto(f"{scene.resolve().as_uri()}?t={t:.4f}")
                opts = {"path": str(dest)}
                if is_jpg:
                    opts.update({"type": "jpeg", "quality": quality})
                page.screenshot(**opts)
                if not dest.exists() or dest.stat().st_size == 0:
                    raise RuntimeError(f"frame {i}: screenshot produced nothing")
                written += 1
                if on_frame:
                    on_frame(i)
        finally:
            browser.close()
    return written, 1


def capture(scene: Path, duration: float, fps: int, out: Path,
            settle_ms: int = 120, *, fmt: str = "auto",
            max_bytes_gb: float = DEFAULT_CAP_GB,
            reserve_mb: int = RESERVE_MB,
            abort_free_mb: int = ABORT_FREE_MB,
            keep_on_abort: bool = False) -> int:
    """Capture frames to disk, with a HARD disk budget and a live watchdog.

    A render must never be able to fill the volume it runs on. Three guards, all
    active at once, because each alone has a hole:

      * PREFLIGHT — frame 0 is captured and MEASURED, the total is projected from
        that real number, and the run is refused up front if it would exceed the
        free space (minus a reserve) or the per-render cap. Refusing costs a flag;
        filling a disk costs the machine.
      * WATCHDOG — a background thread samples free space and the frame directory's
        size, and trips an abort the moment either crosses its limit. The pool
        honours that abort, so the run stops in seconds.
      * CLEANUP — on abort, failure or Ctrl-C, partial frames are deleted. An
        abandoned attempt must not cost the user disk. (Pass keep_on_abort=True to
        inspect them.)

    Prefer `capture_stream()`: it writes NO frames at all.
    """
    chrome = find_chrome()
    if not chrome:
        sys.stderr.write("[capture] no headless Chrome found — cannot capture. "
                         "Install Chrome/Chromium or Playwright's chromium.\n")
        return 2
    f = fmt.lower()
    ext = "auto" if f == "auto" else ("jpg" if f in ("jpg", "jpeg") else "png")
    total = int(round(duration * fps))
    if total <= 0:
        sys.stderr.write(f"[capture] nothing to do: duration={duration} fps={fps}\n")
        return 2
    cap_bytes = int(max_bytes_gb * 1024 ** 3)

    with cleanup_on_exit([out], enabled=not keep_on_abort) as guard:
        # --- 1. PREFLIGHT on a measured frame -----------------------------
        try:
            _first, frame_bytes, ext = _measure_frame(chrome, scene, fps, out,
                                                      settle_ms, ext)
        except RuntimeError as e:
            sys.stderr.write(f"[capture] {e}\n")
            return 1
        sys.stderr.write(f"[capture] {preflight_summary(out, total, frame_bytes, cap_bytes)}\n")
        budget = Budget(out_dir=out, frame_count=total,
                        measured_frame_bytes=frame_bytes,
                        reserve_bytes=reserve_mb * 1024 * 1024,
                        cap_bytes=cap_bytes)
        try:
            budget.check()
        except DiskBudgetError as e:
            # A refusal must leave NOTHING behind — including the probe frame we
            # just measured. "We refused but kept some bytes" is still accumulation.
            back = reclaim([out])
            sys.stderr.write(f"[capture] {e}\n")
            sys.stderr.write(f"[capture] nothing consumed — reclaimed the "
                             f"{back['freed']} probe frame\n")
            return 2   # a refusal, not a crash

        # --- 2. WATCHDOG + bounded pool, fully OBSERVABLE ------------------
        wd = DiskWatchdog(path=out, abort_free_bytes=abort_free_mb * 1024 * 1024,
                          cap_bytes=cap_bytes, watch_dir=out)
        # HARD frame cap: the index range is fixed, so a runaway loop is impossible.
        frames = list(range(total))

        # Observability is not optional on a job that can run for an hour: the user
        # must be able to see progress, the achieved rate, an ETA, and what the run
        # is costing the host — otherwise "it filled my disk" and "it was painfully
        # slow" are both invisible until it is too late.
        telem = Run(f"capture-{scene.stem}", out.parent / "_telemetry",
                    meta={"scene": scene.name, "frames": total, "fps": fps,
                          "format": ext, "frame_bytes": frame_bytes})
        prog = Progress(total=total, label="frames")
        prog.extra = lambda: (f"disk used {human(dir_bytes(out))} | "
                              f"free {human(disk_free(out))}")

        def shoot(i: int):
            if wd.abort.is_set():
                raise RuntimeError("aborted by disk watchdog")
            dest = out / f"frame_{i:06d}.{ext}"
            if dest.exists() and dest.stat().st_size > 0:
                prog.tick()
                return str(dest)      # resume: never re-shoot or duplicate
            url = f"{scene.resolve().as_uri()}?t={i / fps:.4f}"
            cp = run(_shot_args(chrome, url, dest, settle_ms), timeout=90)
            if cp.returncode != 0 or not dest.exists():
                telem.event("frame.error", index=i, reason=cp.stderr[:160])
                raise RuntimeError(f"frame {i} capture failed: {cp.stderr[:160]}")
            prog.tick()
            return str(dest)

        # THE SPEED DECISION, made explicitly and reported.
        # Cold-launching a browser per frame costs ~2.4s AND drives load far past the
        # core count (measured: 0.58 frames/s at load 37 on 14 cores — slow *and*
        # abusive). One persistent browser rendering many frames is ~35x faster on the
        # same machine using ONE process, so it is the default whenever Playwright is
        # present. The parallel cold-Chrome pool remains the fallback.
        fast = _have_playwright()
        with ResourceSampler(disk_path=out, watch_dir=out) as smp, wd:
            with telem.stage("capture"):
                if fast:
                    sys.stderr.write(
                        "[capture] engine: persistent browser (1 process, reuses the "
                        "loaded page — ~35x faster than a cold launch per frame)\n")
                    telem.event("engine.select", engine="playwright-persistent")
                    try:
                        written, procs = capture_fast(
                            scene, duration, fps, out, ext=ext,
                            abort_event=wd.abort, on_frame=lambda _i: prog.tick())
                        res = PoolResult()
                        res.done = [(i, None) for i in range(written)]
                        res.peak_workers = procs
                        if wd.abort.is_set():
                            res.aborted = True
                    except Exception as e:  # noqa: BLE001 — fall back, never fail here
                        sys.stderr.write(f"[capture] persistent browser failed ({e}); "
                                         f"falling back to the cold-launch pool\n")
                        telem.event("engine.fallback", reason=str(e)[:200])
                        fast = False
                if not fast:
                    sys.stderr.write(
                        "[capture] engine: cold-launch pool (Playwright not available "
                        "— install it for a ~35x speedup: pip install playwright && "
                        "python3 -m playwright install chromium)\n")
                    telem.event("engine.select", engine="cold-chrome-pool")
                    # Chrome is itself multi-process: a naive worker count drives load
                    # past the core ceiling. Budget it heavily so the host stays usable.
                    res = run_pool(frames, shoot, kind="cpu", per_worker_mb=1400,
                                   abort_event=wd.abort)

        used = dir_bytes(out)
        # LIVE QUALITY ASSESSMENT of the run itself: was this a sane speed, and if
        # not, WHICH constraint explains it? "Too slow" and "too aggressive" are both
        # defects, so the verdict names which one happened instead of just a number.
        v = verdict(prog, smp, peak_workers=getattr(res, "peak_workers", None),
                    worker_ceiling=None)
        telem.finish("aborted" if (wd.abort.is_set() or res.aborted)
                     else ("ok" if res.ok else "error"),
                     progress=prog.summary(), verdict=v, resources=smp.summary(),
                     bytes_on_disk=used)
        sys.stderr.write(
            f"[capture] perf: {prog.summary()['overall_rate_per_s'] or 0:.2f} frames/s "
            f"over {fmt_duration(prog.summary()['elapsed_s'])} — {v['status']}"
            + (f" ({v['summary'][:160]})" if v["status"] != "ok" else "") + "\n")
        sys.stderr.write(f"[capture] telemetry: {telem.summary_path}\n")
        if wd.abort.is_set() or res.aborted:
            sys.stderr.write(
                f"[capture] ABORTED: {wd.reason or 'disk watchdog tripped'}\n"
                f"[capture] {len(res.done)}/{total} frames existed ({human(used)}); "
                f"{'kept for inspection' if keep_on_abort else 'cleaning up now'}\n")
            return 1   # guard.__exit__ is not enough here — abort is not an exception
        if not res.ok:
            sys.stderr.write(f"[capture] {len(res.failed)} frame(s) failed; "
                             f"partial output will be cleaned up\n")
            return 1
        guard.keep()   # complete and wanted: do NOT delete
        sys.stderr.write(f"[capture] scene={scene.name} frames={len(res.done)}/{total} "
                         f"on disk={human(used)} fmt={ext}\n")
        return 0


def capture_stream(scene: Path, duration: float, fps: int, out_mp4: Path,
                   settle_ms: int = 120, *, audio: Path | None = None,
                   crf: int = 18, abort_free_mb: int = ABORT_FREE_MB) -> int:
    """THE REAL FIX: capture straight into the encoder — frames never touch disk.

    Peak disk cost is the output video plus ONE temporary frame, instead of tens of
    thousands of PNGs. This is what makes filling a volume structurally impossible
    rather than merely guarded against, and it is the default the pipeline should
    reach for. Falls back to the guarded on-disk path only when something here is
    unavailable.
    """
    chrome = find_chrome()
    if not chrome:
        sys.stderr.write("[stream] no headless Chrome found — cannot capture.\n")
        return 2
    if not have("ffmpeg"):
        sys.stderr.write("[stream] ffmpeg not available — needed to encode the "
                         "streamed frames. Install ffmpeg, or use `capture` + "
                         "`encode` with the on-disk budget.\n")
        return 2
    total = int(round(duration * fps))
    if total <= 0:
        sys.stderr.write(f"[stream] nothing to do: duration={duration} fps={fps}\n")
        return 2

    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    tmpdir = Path(tempfile.mkdtemp(prefix="coordn8-stream-"))
    tmp_frame = tmpdir / "f.png"
    cmd = ["ffmpeg", "-y", "-f", "image2pipe", "-framerate", str(fps), "-i", "-"]
    if audio and audio.exists():
        cmd += ["-i", str(audio), "-c:a", "aac", "-shortest"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
            "-pix_fmt", "yuv420p", "-r", str(fps), str(out_mp4)]

    # Even streaming has a floor: if the VOLUME is already nearly full, the output
    # video itself cannot be written.
    if disk_free(out_mp4.parent) < abort_free_mb * 1024 * 1024:
        sys.stderr.write(f"[stream] REFUSING: only {human(disk_free(out_mp4.parent))} "
                         f"free, below the {human(abort_free_mb * 1024 * 1024)} floor. "
                         f"Run `coordn8 clean` or free space first.\n")
        shutil.rmtree(tmpdir, ignore_errors=True)
        return 2

    written = 0
    telem = Run(f"stream-{scene.stem}", out_mp4.parent / "_telemetry",
                meta={"scene": scene.name, "frames": total, "fps": fps, "crf": crf})
    prog = Progress(total=total, label="frames")
    prog.extra = lambda: f"free {human(disk_free(out_mp4.parent))}"
    smp = ResourceSampler(disk_path=out_mp4.parent).start()
    fast = _have_playwright()
    sys.stderr.write(
        "[stream] engine: persistent browser -> ffmpeg stdin (no frames on disk, "
        "one browser process)\n" if fast else
        "[stream] engine: cold-launch per frame (slow — install Playwright for a "
        "~35x speedup: pip install playwright && python3 -m playwright install "
        "chromium)\n")
    telem.event("engine.select",
                engine="playwright-persistent" if fast else "cold-chrome")

    with cleanup_on_exit([tmpdir, out_mp4]) as guard:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            def check_space(i: int) -> None:
                if disk_free(out_mp4.parent) < abort_free_mb * 1024 * 1024:
                    raise DiskBudgetError(
                        f"free space fell to {human(disk_free(out_mp4.parent))} while "
                        f"streaming — aborting at frame {i}/{total}")

            if fast:
                # ONE browser, many frames, straight down the pipe: fast AND
                # zero-footprint. The screenshot bytes go to ffmpeg without ever
                # becoming a file.
                from playwright.sync_api import sync_playwright
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(
                        args=["--force-color-profile=srgb",
                              "--allow-file-access-from-files",
                              "--hide-scrollbars", "--disable-gpu"])
                    try:
                        page = browser.new_page(
                            viewport={"width": 1920, "height": 1080},
                            device_scale_factor=1)
                        page.goto(scene.resolve().as_uri() + "?t=0.0000")
                        try:
                            page.wait_for_function(
                                "() => document.title === 'READY' "
                                "|| window.__ready === true", timeout=15000)
                        except Exception:  # noqa: BLE001
                            page.wait_for_timeout(150)
                        has_hook = bool(page.evaluate(
                            "() => typeof window.renderAt === 'function' "
                            "|| (window.Coordn8 && typeof window.renderFrame === "
                            "'function')"))
                        for i in range(total):
                            check_space(i)
                            t = i / fps
                            if has_hook:
                                page.evaluate(
                                    "(t) => (window.renderAt ? window.renderAt(t) "
                                    ": window.renderFrame(t))", t)
                            else:
                                page.goto(f"{scene.resolve().as_uri()}?t={t:.4f}")
                            # JPEG, not PNG: in-browser PNG encoding dominates the
                            # per-frame cost here, and the pipe is re-encoded by
                            # x264 anyway, so lossless intermediates buy nothing.
                            buf = page.screenshot(type="jpeg", quality=92)
                            assert proc.stdin is not None
                            proc.stdin.write(buf)     # never touches the filesystem
                            written += 1
                            prog.tick()
                    finally:
                        browser.close()
            else:
                for i in range(total):
                    check_space(i)
                    url = f"{scene.resolve().as_uri()}?t={i / fps:.4f}"
                    cp = run(_shot_args(chrome, url, tmp_frame, settle_ms), timeout=90)
                    if cp.returncode != 0 or not tmp_frame.exists():
                        raise RuntimeError(f"frame {i} capture failed: {cp.stderr[:160]}")
                    assert proc.stdin is not None
                    proc.stdin.write(tmp_frame.read_bytes())
                    tmp_frame.unlink(missing_ok=True)  # one frame, immediately reclaimed
                    written += 1
                    prog.tick()
        except (DiskBudgetError, RuntimeError, BrokenPipeError) as e:
            smp.stop()
            telem.event("stream.abort", reason=str(e)[:300], written=written)
            telem.finish("aborted", progress=prog.summary(), resources=smp.summary())
            sys.stderr.write(f"[stream] {e}\n")
            try:
                if proc.stdin:
                    proc.stdin.close()
            except OSError:
                pass
            proc.wait(timeout=60)
            shutil.rmtree(tmpdir, ignore_errors=True)
            return 1
        assert proc.stdin is not None
        proc.stdin.close()
        rc = proc.wait(timeout=900)
        shutil.rmtree(tmpdir, ignore_errors=True)
        if rc != 0 or not out_mp4.exists():
            err = (proc.stderr.read().decode("utf-8", "replace")[:300]
                   if proc.stderr else "")
            sys.stderr.write(f"[stream] encode failed: {err}\n")
            return 1
        guard.keep()
    smp.stop()
    v = verdict(prog, smp)
    telem.finish("ok", progress=prog.summary(), verdict=v, resources=smp.summary(),
                 output_bytes=out_mp4.stat().st_size)
    ps = prog.summary()
    sys.stderr.write(f"[stream] wrote {out_mp4} ({human(out_mp4.stat().st_size)}) "
                     f"from {written} streamed frames — ZERO frames left on disk\n")
    sys.stderr.write(f"[stream] perf: {ps['overall_rate_per_s'] or 0:.2f} frames/s over "
                     f"{fmt_duration(ps['elapsed_s'])} — {v['status']}"
                     + (f" ({v['summary'][:160]})" if v["status"] != "ok" else "") + "\n")
    sys.stderr.write(f"[stream] telemetry: {telem.summary_path}\n")
    return 0


def _manim_bin() -> list[str] | None:
    """How to run Manim here: the console script, `python -m manim` for this
    interpreter, or a Manim installed for ANOTHER local Python (pipx, uv tool,
    Homebrew python…). None when no working Manim exists on the machine."""
    if have("manim"):
        return ["manim"]
    cp = run([sys.executable, "-c", "import manim"], timeout=60)
    if cp.returncode == 0:
        return [sys.executable, "-m", "manim"]
    try:
        from lib.common import find_manim
    except Exception:  # noqa: BLE001
        return None
    return find_manim()

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
    # Manim keeps its own media tree (partial-movie files, caches, a duplicate mp4).
    # Once frames are re-extracted, that tree is pure waste — and leaving it behind is
    # exactly the accumulate-forever behaviour that filled a 1 TB volume. Reclaim it.
    med = reclaim([media_dir])
    sys.stderr.write(f"[manim] scene={name} rendered -> {n} frames in {out} "
                     f"(unified {w}x{h}@{fps}, from {mp4.name}); "
                     f"reclaimed {med['freed']} of Manim media cache\n")
    return 0


def encode(frames: Path, fps: int, out: Path, audio: Path | None,
           *, cleanup: bool = True) -> int:
    """Encode a frame directory to mp4, then RECLAIM THE FRAMES.

    Cleanup defaults to ON. The original pipeline left every frame on disk forever,
    so a finished render still held tens of GB and a re-run simply added more — the
    direct cause of a 1 TB volume filling up. Once the mp4 exists the frames are
    redundant by construction (each is a pure function of t and can be re-derived),
    so keeping them is pure cost. Pass cleanup=False / --keep-frames to inspect them.
    """
    if not have("ffmpeg"):
        sys.stderr.write("[encode] ffmpeg not available.\n")
        return 2
    if not frames.is_dir():
        sys.stderr.write(f"[encode] no frame directory at {frames}\n")
        return 2
    # Match whatever the capture actually wrote (jpg by default, png legacy).
    ext = None
    for cand in ("png", "jpg", "jpeg"):
        if any(frames.glob(f"frame_*.{cand}")):
            ext = cand
            break
    if ext is None:
        sys.stderr.write(f"[encode] no frame_*.png/.jpg files in {frames}\n")
        return 2

    before = dir_bytes(frames)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-framerate", str(fps),
           "-i", str(frames / f"frame_%06d.{ext}")]
    if audio and audio.exists():
        cmd += ["-i", str(audio), "-c:a", "aac", "-shortest"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps), str(out)]
    cp = run(cmd, timeout=1800)
    if cp.returncode != 0 or not out.exists():
        sys.stderr.write(f"[encode] failed: {cp.stderr[:300]}\n")
        return 1   # frames are KEPT on failure so the run can be resumed

    msg = f"[encode] wrote {out} ({human(out.stat().st_size)})"
    if cleanup:
        res = reclaim([frames])
        msg += f"; reclaimed {res['freed']} of frames"
    else:
        msg += f"; kept {human(before)} of frames (--keep-frames)"
    sys.stderr.write(msg + "\n")
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

    c = sub.add_parser("capture", help="frames to disk, with a hard disk budget")
    c.add_argument("--scene", required=True)
    c.add_argument("--duration", type=float, required=True)
    c.add_argument("--fps", type=int, default=30)
    c.add_argument("--out", required=True)
    c.add_argument("--format", default="auto", choices=("auto", "jpg", "png"),
                   help="frame format. 'auto' (default) probes BOTH png and jpg on "
                        "frame 0 and keeps whichever is genuinely smaller for this "
                        "scene's content — neither wins universally (flat synthetic "
                        "frames favour png; gradients/screenshots favour jpg)")
    c.add_argument("--max-bytes-gb", type=float, default=DEFAULT_CAP_GB,
                   help="hard cap on intermediate frames for THIS render (default "
                        f"{DEFAULT_CAP_GB} GB). Exceeding it refuses up front")
    c.add_argument("--reserve-mb", type=int, default=RESERVE_MB,
                   help="free space never consumed (default %(default)s MB)")
    c.add_argument("--abort-free-mb", type=int, default=ABORT_FREE_MB,
                   help="abort the run if free space falls below this (default "
                        "%(default)s MB)")
    c.add_argument("--keep-on-abort", action="store_true",
                   help="keep partial frames after an abort (default: delete them)")

    st = sub.add_parser("stream",
                        help="capture DIRECTLY into the encoder — writes NO frames "
                             "to disk (preferred)")
    st.add_argument("--scene", required=True)
    st.add_argument("--duration", type=float, required=True)
    st.add_argument("--fps", type=int, default=30)
    st.add_argument("--out", required=True, help="output mp4")
    st.add_argument("--audio", default=None)
    st.add_argument("--crf", type=int, default=18)
    st.add_argument("--abort-free-mb", type=int, default=ABORT_FREE_MB)

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
    e.add_argument("--keep-frames", action="store_true",
                   help="do NOT delete the frames after a successful encode "
                        "(default: reclaim them — they are re-derivable)")

    cl = sub.add_parser("clean",
                        help="reclaim render intermediates left by earlier runs")
    cl.add_argument("paths", nargs="+")
    cl.add_argument("--dry-run", action="store_true",
                    help="report what would be freed without deleting")

    k = sub.add_parser("concat")
    k.add_argument("--list", required=True)
    k.add_argument("--out", required=True)

    args = ap.parse_args()
    if args.cmd == "capture":
        return capture(Path(args.scene), args.duration, args.fps, Path(args.out),
                       fmt=args.format, max_bytes_gb=args.max_bytes_gb,
                       reserve_mb=args.reserve_mb,
                       abort_free_mb=args.abort_free_mb,
                       keep_on_abort=args.keep_on_abort)
    if args.cmd == "stream":
        return capture_stream(Path(args.scene), args.duration, args.fps,
                              Path(args.out),
                              audio=Path(args.audio) if args.audio else None,
                              crf=args.crf, abort_free_mb=args.abort_free_mb)
    if args.cmd == "manim":
        return manim_render(Path(args.scene), args.name, args.fps,
                            Path(args.out), args.resolution)
    if args.cmd == "encode":
        return encode(Path(args.frames), args.fps, Path(args.out),
                      Path(args.audio) if args.audio else None,
                      cleanup=not args.keep_frames)
    if args.cmd == "concat":
        return concat(Path(args.list), Path(args.out))
    if args.cmd == "clean":
        import json as _json
        res = reclaim([Path(x) for x in args.paths], dry_run=args.dry_run)
        print(_json.dumps(res, indent=2))
        sys.stderr.write(f"[clean] {'would free' if args.dry_run else 'freed'} "
                         f"{res['freed']} across {len(res['removed'])} path(s)\n")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
