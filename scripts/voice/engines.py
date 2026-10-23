#!/usr/bin/env python3
"""Coordn8 — local voice ENGINES: install, detect, synthesize, probe.

Every engine here runs on this machine. Nothing is sent to a hosted speech
service. Model files are downloaded once (or copied from a shared cache) into
the user zone, ``<user_home>/voices/<engine>/``, which survives reinstall.

Engines, best quality first (the quality prior; the run-time probe gates it):

  kokoro     neural, 24 kHz, Kokoro-82M weights via the kokoro-onnx runtime in a
             dedicated Python >= 3.10 venv (our scripts stay on Python 3.9 and
             call it as a subprocess). Reports per-phoneme durations, from which
             word timings are built.
  piper      fast neural, 22.05 kHz (resampled to 24 kHz), several English
             voices, run through its own command line in its own venv (kept at
             arm's length: separately installed, never vendored).
  say        the macOS system voice. Always present on macOS; last fallback.
  espeak-ng  formant voice, used only if the user already has it on PATH.

This file is stdlib-only at import time on purpose: the same file is executed
by the Kokoro venv's interpreter as the synthesis worker (``_kokoro_worker``),
so it must not import package modules at the top level.
"""
from __future__ import annotations

import array
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG_ROOT = HERE.parents[1]
DATA_FILE = PKG_ROOT / "data" / "voices.json"

SR = 24000                      # every engine's output is normalised to this
ENGINE_ORDER = ["kokoro", "piper", "say", "espeak-ng"]
PROBE_TEXT = ("This short probe checks that the voice speaks clearly "
              "at a steady and natural pace.")
PROBE_WORDS = len(PROBE_TEXT.split())
UV_DEFAULT = str(Path("~/.local/bin/uv").expanduser())   # uv's standard per-user location


# --------------------------------------------------------------------------- paths
def _lib():
    """Import the shared path helpers lazily (the worker runs without them)."""
    scripts = str(PKG_ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from lib import paths  # noqa: E402
    return paths


def voices_root() -> Path:
    return _lib().user_home() / "voices"


def engine_dir(engine: str) -> Path:
    return voices_root() / engine


def venv_python(engine: str) -> Path:
    v = engine_dir(engine) / "venv"
    return v / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def catalog() -> dict:
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema": 1, "engines": {}, "voices": []}


def engine_spec(engine: str) -> dict:
    return (catalog().get("engines") or {}).get(engine, {})


def disabled() -> set:
    """Engines switched off for this process: COORDN8_VOICE_DISABLE=kokoro,piper."""
    raw = os.environ.get("COORDN8_VOICE_DISABLE", "")
    return {p.strip().lower() for p in raw.split(",") if p.strip()}


def prior(engine: str) -> float:
    return float(engine_spec(engine).get("quality_prior", {"kokoro": 3, "piper": 2,
                                                           "say": 1, "espeak-ng": 0.5}.get(engine, 0)))


# --------------------------------------------------------------------------- wav utils (stdlib)
def read_wav(path) -> tuple:
    """-> (samples as array('h'), sample_rate). Mono 16-bit only (what we write)."""
    with wave.open(str(path), "rb") as w:
        sr, ch, sw, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
        raw = w.readframes(n)
    if sw != 2:
        raise ValueError(f"{path}: expected 16-bit PCM, got {8 * sw}-bit")
    a = array.array("h")
    a.frombytes(raw)
    if sys.byteorder == "big":
        a.byteswap()
    if ch > 1:
        a = array.array("h", a[::ch])
    return a, sr


def write_wav(path, samples, sr: int = SR) -> None:
    a = samples if isinstance(samples, array.array) else array.array("h", samples)
    b = array.array("h", a)
    if sys.byteorder == "big":
        b.byteswap()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b.tobytes())


def resample_linear(a, src: int, dst: int):
    """Plain linear resampler — only used when ffmpeg is unavailable."""
    if src == dst or not a:
        return array.array("h", a)
    n = int(len(a) * dst / src)
    out = array.array("h", bytes(2 * n))
    step = src / dst
    last = len(a) - 1
    for i in range(n):
        x = i * step
        j = int(x)
        f = x - j
        k = j + 1 if j < last else last
        out[i] = int(a[j] + (a[k] - a[j]) * f)
    return out


def to_24k_mono(src, dst) -> None:
    """Normalise any engine output to 24 kHz mono 16-bit PCM wav."""
    ff = shutil.which("ffmpeg")
    if ff:
        r = subprocess.run([ff, "-v", "error", "-y", "-i", str(src), "-ac", "1", "-ar", str(SR),
                            "-c:a", "pcm_s16le", str(dst)], capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            return
    a, sr = read_wav(src)
    write_wav(dst, resample_linear(a, sr, SR), SR)


def wav_stats(path) -> dict:
    """Peak (0..1 of full scale), RMS, duration, clipped-sample count."""
    a, sr = read_wav(path)
    n = len(a)
    if not n:
        return {"seconds": 0.0, "peak": 0.0, "rms": 0.0, "clipped_samples": 0, "sample_rate": sr}
    peak = max(abs(min(a)), abs(max(a))) / 32768.0
    step = max(1, n // 200000)              # bounded cost on long files
    sub = a[::step]
    rms = (sum(x * x for x in sub) / len(sub)) ** 0.5 / 32768.0
    clipped = sum(1 for x in a if x >= 32766 or x <= -32767)
    return {"seconds": n / sr, "peak": round(peak, 4), "rms": round(rms, 5),
            "clipped_samples": clipped, "sample_rate": sr}


# --------------------------------------------------------------------------- detect / list
def _say_voices() -> list:
    if sys.platform != "darwin" or not shutil.which("say"):
        return []
    try:
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    res = []
    for line in out.splitlines():
        m = re.match(r"^(.+?)\s+([a-z]{2,3}_[A-Z0-9]{2,3})\s+#", line)
        if m and m.group(2).startswith("en_") and m.group(1).strip() not in {r["name"] for r in res}:
            res.append({"name": m.group(1).strip(), "locale": m.group(2)})
    return res


def _espeak_bin() -> str | None:
    return shutil.which("espeak-ng") or shutil.which("espeak")


def _kokoro_files() -> dict:
    spec = engine_spec("kokoro")
    names = {f.get("role"): f["name"] for f in spec.get("files", [])}
    d = engine_dir("kokoro")
    return {"model": d / names.get("model", "kokoro-v1.0.fp16.onnx"),
            "voices": d / names.get("voices", "voices-v1.0.bin")}


def _kokoro_voice_names() -> list:
    import zipfile
    f = _kokoro_files()["voices"]
    try:
        with zipfile.ZipFile(str(f)) as z:
            return sorted(n[:-4] for n in z.namelist() if n.endswith(".npy"))
    except (OSError, zipfile.BadZipFile):
        return []


def _piper_voice_files() -> list:
    d = engine_dir("piper") / "voices"
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("*.onnx") if (p.parent / (p.name + ".json")).exists())


def _venv_has(engine: str, module_dir: str) -> bool:
    py = venv_python(engine)
    if not py.exists():
        return False
    site = list((engine_dir(engine) / "venv").glob(f"lib/python3*/site-packages/{module_dir}"))
    site += list((engine_dir(engine) / "venv").glob(f"Lib/site-packages/{module_dir}"))
    return bool(site)


def detect() -> dict:
    """What is installed RIGHT NOW (no synthesis, no writes, never raises)."""
    off = disabled()
    res = {}
    for e in ENGINE_ORDER:
        info = {"engine": e, "installed": False, "disabled": e in off, "reason": "",
                "dir": str(engine_dir(e)) if e in ("kokoro", "piper") else None, "voices": []}
        try:
            if e == "kokoro":
                f = _kokoro_files()
                if not venv_python(e).exists():
                    info["reason"] = "kokoro venv missing (run: voices.py install --engines kokoro)"
                elif not _venv_has(e, "kokoro_onnx"):
                    info["reason"] = "kokoro-onnx not installed in its venv"
                elif not (f["model"].exists() and f["voices"].exists()):
                    info["reason"] = "kokoro model or voices file missing"
                else:
                    cat_ids = {v["engine_voice"] for v in catalog().get("voices", []) if v.get("engine") == e}
                    names = _kokoro_voice_names()
                    info["voices"] = [n for n in names if n in cat_ids] or [n for n in names if n[:1] in "ab"]
                    info["installed"] = bool(info["voices"])
                    info["other_language_voices"] = len(names) - len(info["voices"])
                    if not info["installed"]:
                        info["reason"] = "voices file unreadable"
            elif e == "piper":
                vf = _piper_voice_files()
                if not venv_python(e).exists():
                    info["reason"] = "piper venv missing (run: voices.py install --engines piper)"
                elif not _venv_has(e, "piper"):
                    info["reason"] = "piper-tts not installed in its venv"
                elif not vf:
                    info["reason"] = "no piper voice model (.onnx + .onnx.json) installed"
                else:
                    info["voices"] = [p.name[:-5] for p in vf]
                    info["installed"] = True
            elif e == "say":
                v = _say_voices()
                info["voices"] = [x["name"] for x in v]
                info["installed"] = bool(v)
                info["reason"] = "" if v else "macOS `say` not available on this system"
            elif e == "espeak-ng":
                b = _espeak_bin()
                info["installed"] = bool(b)
                info["voices"] = ["en-us", "en-gb"] if b else []
                info["reason"] = "" if b else "espeak-ng not on PATH (optional)"
        except Exception as ex:  # never raise from detection
            info["reason"] = f"detection error: {ex}"
        if info["disabled"] and info["installed"]:
            info["reason"] = "disabled by COORDN8_VOICE_DISABLE"
        res[e] = info
    return res


def available() -> list:
    """Installed and not disabled, in quality-prior order."""
    d = detect()
    return [e for e in ENGINE_ORDER if d[e]["installed"] and not d[e]["disabled"]]


def default_voice(engine: str) -> str | None:
    spec = engine_spec(engine)
    d = detect()[engine]
    want = spec.get("default_voice")
    if want and want in d["voices"]:
        return want
    return d["voices"][0] if d["voices"] else None


def capabilities(engine: str) -> dict:
    return dict(engine_spec(engine).get("supports", {}))


# --------------------------------------------------------------------------- synthesis
class EngineError(RuntimeError):
    pass


def _run(cmd, *, input_text=None, timeout=300, env=None):
    try:
        return subprocess.run(cmd, input=input_text, capture_output=True, text=True,
                              timeout=timeout, env=env)
    except subprocess.TimeoutExpired as ex:
        raise EngineError(f"timeout after {timeout}s: {cmd[0]}") from ex
    except OSError as ex:
        raise EngineError(f"cannot run {cmd[0]}: {ex}") from ex


def _kokoro_lang(voice: str) -> str:
    v = next((x for x in catalog().get("voices", []) if x.get("engine") == "kokoro"
              and x.get("engine_voice") == voice), None)
    if v and v.get("lang"):
        return v["lang"]
    return "en-gb" if voice[:1] == "b" else "en-us"


def synth_segments(engine: str, voice: str, segments: list, workdir) -> list:
    """Render each segment {text, speed, emphasis:[word idx]} to its own 24 kHz wav.

    Returns, per segment: {wav, seconds, word_spans or None, synth_s, peak_raw}.
    word_spans are (start, end) seconds per whitespace word of the segment text.
    """
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    out = []
    if engine == "kokoro":
        f = _kokoro_files()
        items = []
        for i, s in enumerate(segments):
            items.append({"text": s["text"], "voice": voice, "lang": _kokoro_lang(voice),
                          "speed": max(0.5, min(2.0, float(s.get("speed", 1.0)))),
                          "emphasis": list(s.get("emphasis") or []),
                          "out": str(workdir / f"seg{i:03d}.wav")})
        job = {"model": str(f["model"]), "voices": str(f["voices"]), "items": items}
        t0 = time.time()
        r = _run([str(venv_python("kokoro")), "-I", str(Path(__file__).resolve()), "_kokoro_worker"],
                 input_text=json.dumps(job), timeout=120 + 60 * len(items))
        if r.returncode != 0:
            raise EngineError(f"kokoro worker failed: {(r.stderr or '')[-400:]}")
        try:
            res = json.loads(r.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError) as ex:
            raise EngineError(f"kokoro worker gave no result: {(r.stderr or '')[-300:]}") from ex
        wall = time.time() - t0
        for it, rr in zip(items, res["items"]):
            out.append({"wav": it["out"], "seconds": rr["seconds"], "word_spans": rr.get("word_spans"),
                        "synth_s": rr.get("synth_s"), "peak_raw": rr.get("peak"),
                        "phonemes": rr.get("phonemes"), "load_s": res.get("load_s"), "wall_s": wall,
                        "espeak_short_dir": res.get("espeak_short_dir")})
        return out
    for i, s in enumerate(segments):
        dst = workdir / f"seg{i:03d}.wav"
        raw = workdir / f"seg{i:03d}.raw.wav"
        speed = max(0.5, min(2.0, float(s.get("speed", 1.0))))
        t0 = time.time()
        if engine == "piper":
            model = engine_dir("piper") / "voices" / f"{voice}.onnx"
            if not model.exists():
                raise EngineError(f"piper voice not installed: {voice}")
            try:
                cfg = json.loads((model.parent / (model.name + ".json")).read_text(encoding="utf-8"))
                base_ls = float((cfg.get("inference") or {}).get("length_scale", 1.0))
            except (OSError, ValueError):
                base_ls = 1.0
            r = _run([str(venv_python("piper")), "-I", "-m", "piper", "-m", str(model),
                      "-c", str(model) + ".json", "-f", str(raw),
                      "--length-scale", f"{base_ls / speed:.3f}", "--sentence-silence", "0",
                      "--volume", str(engine_spec("piper").get("volume", 0.8))],
                     input_text=s["text"] + "\n", timeout=180)
            if r.returncode != 0 or not raw.exists():
                raise EngineError(f"piper failed: {(r.stderr or '')[-300:]}")
        elif engine == "say":
            words = s["text"].split()
            for idx in sorted(set(s.get("emphasis") or []), reverse=True):
                if 0 <= idx < len(words):
                    words[idx] = "[[emph +]]" + words[idx]
            txt = workdir / f"seg{i:03d}.txt"
            txt.write_text(" ".join(words), encoding="utf-8")
            rate = int(round(float(engine_spec("say").get("base_wpm", 180)) * speed))
            r = _run(["say", "-v", voice, "-r", str(rate), "-o", str(raw), "--file-format=WAVE",
                      f"--data-format=LEI16@{SR}", "-f", str(txt)], timeout=180)
            if r.returncode != 0 or not raw.exists():
                raise EngineError(f"say failed: {(r.stderr or '').strip()[-300:]}")
        elif engine == "espeak-ng":
            b = _espeak_bin()
            if not b:
                raise EngineError("espeak-ng not installed")
            wpm = int(round(175 * speed))
            cmd = [b, "-v", voice or "en-us", "-s", str(wpm), "-w", str(raw)]
            if s.get("ssml"):
                cmd.append("-m")
            r = _run(cmd + [s["text"]], timeout=120)
            if r.returncode != 0 or not raw.exists():
                raise EngineError(f"espeak-ng failed: {(r.stderr or '')[-300:]}")
        else:
            raise EngineError(f"unknown engine: {engine}")
        to_24k_mono(raw, dst)
        try:
            raw.unlink()
        except OSError:
            pass
        st = wav_stats(dst)
        out.append({"wav": str(dst), "seconds": st["seconds"], "word_spans": None,
                    "synth_s": time.time() - t0, "peak_raw": st["peak"]})
    return out


# --------------------------------------------------------------------------- kokoro worker
_IPA_VOWELS = set("aeiouyæɑɒɔəɚɛɜɝɪʊʌɐᵻøœɨʉɯɤ")


def _stress(group: str) -> str:
    """Force primary stress on one word's phonemes (emphasis on engines with no SSML)."""
    if "ˈ" in group:
        return group
    if "ˌ" in group:
        return group.replace("ˌ", "ˈ", 1)
    for i, ch in enumerate(group):
        if ch in _IPA_VOWELS:
            return group[:i] + "ˈ" + group[i:]
    return group


_STRIP = re.compile(r"[ˈˌ.,;:!?…\"'()\[\]{}—–-]")


def _plain(ph: str) -> str:
    """Phonemes without stress marks or punctuation — what alignment compares."""
    return _STRIP.sub("", ph)


def align_words(word_phs: list, group_strs: list, max_merge: int = 5) -> list | None:
    """Monotonic alignment of per-word phoneme strings onto the space-separated
    groups of the whole-sentence phonemization.

    The phonemizer merges function words ("that the" -> one group) and can split
    one written word into several groups (numbers). Blocks are (words a, groups b)
    with a == 1 or b == 1; cost = relative length mismatch. Returns a list of
    (w0, w1, g0, g1) half-open blocks, or None when no alignment exists."""
    W, G = len(word_phs), len(group_strs)
    wl = [len(_plain(x)) for x in word_phs]
    gl = [len(_plain(x)) for x in group_strs]
    INF = float("inf")
    dp = [[INF] * (G + 1) for _ in range(W + 1)]
    back = [[None] * (G + 1) for _ in range(W + 1)]
    dp[0][0] = 0.0
    for i in range(W + 1):
        for j in range(G + 1):
            c0 = dp[i][j]
            if c0 == INF:
                continue
            if i < W and wl[i] == 0:                      # punctuation-only word: zero width
                if c0 < dp[i + 1][j]:
                    dp[i + 1][j], back[i + 1][j] = c0, (i, j)
                continue
            moves = [(a, 1) for a in range(1, max_merge + 1)] + [(1, b) for b in range(2, max_merge + 1)]
            for a, bb in moves:
                i2, j2 = i + a, j + bb
                if i2 > W or j2 > G:
                    continue
                lw, lg = sum(wl[i:i2]), sum(gl[j:j2])
                cost = abs(lw - lg) / max(1, lg) + (0.05 if (a, bb) != (1, 1) else 0.0)
                if c0 + cost < dp[i2][j2]:
                    dp[i2][j2], back[i2][j2] = c0 + cost, (i, j)
    if dp[W][G] == INF:
        return None
    blocks, i, j = [], W, G
    while (i, j) != (0, 0):
        pi, pj = back[i][j]
        blocks.append((pi, i, pj, j))
        i, j = pi, pj
    return list(reversed(blocks))


def in_context_word_phonemes(word_phs: list, group_strs: list) -> list:
    """Per-word phonemes IN CONTEXT where the sentence phonemization proves them.

    A word phonemized ALONE can differ from the same word inside the sentence: the
    article "A" alone is the letter name ("eɪ", two phonemes) but a schwa in context.
    The length mismatch made the block alignment shift ("A" timed over half a second,
    the next words late). When the whole-sentence phonemization keeps one group per
    sounded word (no merges, no splits), those groups ARE the in-context phonemes of
    the words, so they replace the isolated ones. Otherwise the isolated forms are kept
    and the length-based alignment handles the merges."""
    sounded = [i for i, w in enumerate(word_phs) if len(_plain(w))]
    if not sounded or len(sounded) != len(group_strs):
        return list(word_phs)
    out = list(word_phs)
    for k, i in enumerate(sounded):
        out[i] = group_strs[k]
    return out


def silence_runs(samples, sr: int, frame_s: float = 0.01, min_s: float = 0.12,
                 rel_db: float = 38.0, floor_db: float = -50.0) -> list:
    """Internal silent runs [(start, end) seconds] of a mono signal in [-1, 1] floats
    (not touching its start or end). Silent = frame RMS below max(floor, loudest-rel)."""
    import math
    hop = max(1, int(sr * frame_s))
    dbs = []
    for i in range(0, len(samples) - hop + 1, hop):
        acc = 0.0
        for x in samples[i:i + hop]:
            acc += x * x
        r = math.sqrt(acc / hop)
        dbs.append(20 * math.log10(r) if r > 1e-9 else -180.0)
    if not dbs:
        return []
    thr = max(floor_db, max(dbs) - rel_db)
    runs, i, n = [], 0, len(dbs)
    while i < n:
        if dbs[i] < thr:
            j = i
            while j < n and dbs[j] < thr:
                j += 1
            if (j - i) * frame_s >= min_s and i > 0 and j < n:
                runs.append((round(i * frame_s, 3), round(j * frame_s, 3)))
            i = j
        else:
            i += 1
    return runs


def refine_spans_with_silence(spans: list, runs: list) -> list:
    """Move word edges out of MEASURED silence (the pause a comma or a breath causes).

    The engine attributes a pause to a neighbouring phoneme, so a word's span can run
    through a silence, or the next word can start before the audio resumes. Rules, per
    silent run (a, b): a span that contains the whole run keeps its longer voiced side
    (end -> a, or start -> b); a start inside the run moves to b; an end inside the run
    moves to a. Spans never reverse."""
    out = [list(s) if s is not None else None for s in spans]
    for a, b in runs:
        for s in out:
            if s is None:
                continue
            st, en = s
            if st < a and en > b:
                if (a - st) >= (en - b):
                    s[1] = a
                else:
                    s[0] = b
            elif a <= st < b and en > b:
                s[0] = b
            elif st < a < en <= b:
                s[1] = a
    for s in out:
        if s is not None and s[1] < s[0]:
            s[1] = s[0]
    return [[round(s[0], 3), round(s[1], 3)] if s is not None else None for s in out]


def word_spans_from(blocks: list, word_phs: list, group_timings: list) -> list:
    """Per-word (start, end) seconds. group_timings[g] = [(phoneme, start, end), ...].
    A group holding several words is split at the phoneme where each word's share
    of the group's phonemes ends (real phoneme times, not a guess at even spacing)."""
    spans = [None] * len(word_phs)
    last_end = 0.0
    punct = set(".,;:!?…—–-\"'()[]{}")

    def _voiced(tl):
        # a word ends at its last sounded phoneme, not at trailing punctuation (silence)
        core = [t for t in tl if t[0] not in punct]
        return core or tl
    group_timings = [_voiced(g) for g in group_timings]
    for w0, w1, g0, g1 in blocks:
        if g0 == g1:                                       # zero-width (punctuation-only word)
            for w in range(w0, w1):
                spans[w] = [round(last_end, 3), round(last_end, 3)]
            continue
        if w1 - w0 == 1:
            st, en = group_timings[g0][0][1], group_timings[g1 - 1][-1][2]
            spans[w0] = [round(st, 3), round(en, 3)]
            last_end = en
            continue
        tl = [t for t in group_timings[g0] if t[0] not in "ˈˌ"] or group_timings[g0]
        lens = [max(1, len(_plain(word_phs[w]))) for w in range(w0, w1)]
        tot, acc, prev = sum(lens), 0, 0
        for k, w in enumerate(range(w0, w1)):
            acc += lens[k]
            cut = len(tl) if k == len(lens) - 1 else max(prev + 1, min(len(tl) - 1, round(acc / tot * len(tl))))
            seg = tl[prev:cut] or tl[max(0, cut - 1):cut]
            spans[w] = [round(seg[0][1], 3), round(seg[-1][2], 3)]
            prev = cut
        last_end = tl[-1][2]
    return spans


def _kokoro_worker() -> int:  # pragma: no cover - runs inside the Kokoro venv
    job = json.loads(sys.stdin.read())
    t0 = time.time()
    import onnxruntime as ort
    ort.set_default_logger_severity(3)
    import soundfile as sf
    from kokoro_onnx import Kokoro
    cfg, short_dir = None, None
    try:
        # espeak-ng keeps its data path in a fixed-size buffer: from a long user-home
        # path it silently falls back to a compiled-in default and phonemization
        # fails. The phonemizer resolves symlinks, so hand it a REAL short directory:
        # a hard-linked copy of the data under /tmp (no extra disk on one volume).
        import espeakng_loader
        from kokoro_onnx.config import EspeakConfig
        dp = espeakng_loader.get_data_path()
        if len(dp) > 100:
            base = "/tmp" if os.path.isdir("/tmp") else tempfile.gettempdir()
            short_dir = os.path.join(base, "c8-espeak-" + hashlib.sha256(dp.encode()).hexdigest()[:10])
            if not os.path.exists(os.path.join(short_dir, "phontab")):
                tmp_dir = short_dir + f".tmp{os.getpid()}"

                def _link(src, dst):
                    try:
                        os.link(src, dst)
                    except OSError:
                        shutil.copy2(src, dst)
                shutil.copytree(dp, tmp_dir, copy_function=_link)
                try:
                    os.rename(tmp_dir, short_dir)
                except OSError:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
            if os.path.exists(os.path.join(short_dir, "phontab")):
                cfg = EspeakConfig(data_path=short_dir)
    except Exception:
        cfg = None
    k = Kokoro(job["model"], job["voices"], espeak_config=cfg)
    load_s = time.time() - t0
    results = []
    for it in job["items"]:
        t1 = time.time()
        lang = it.get("lang", "en-us")
        words = it["text"].split()
        ph = " ".join(k.tokenizer.phonemize(it["text"], lang).split())
        groups = ph.split(" ") if ph else []
        word_phs = [" ".join(k.tokenizer.phonemize(w, lang).split()).replace(" ", "") for w in words]
        word_phs = in_context_word_phonemes(word_phs, groups)
        blocks = align_words(word_phs, groups)
        emph_applied = []
        if blocks and it.get("emphasis"):
            for w0, w1, g0, g1 in blocks:
                for wi in it["emphasis"]:
                    if w0 <= wi < w1 and g1 > g0:
                        groups[g0] = _stress(groups[g0])
                        emph_applied.append(wi)
            ph = " ".join(groups)
        audio, sr, tm = k.create_timed(ph, it["voice"], float(it.get("speed", 1.0)), lang,
                                       is_phonemes=True)
        gtim, cur = [], []
        for x in tm:
            if x.phoneme == " ":
                if cur:
                    gtim.append(cur)
                cur = []
                continue
            cur.append((x.phoneme, float(x.start), float(x.end)))
        if cur:
            gtim.append(cur)
        word_spans = None
        if blocks is not None and len(gtim) == len(groups):
            word_spans = word_spans_from(blocks, word_phs, gtim)
            try:
                word_spans = refine_spans_with_silence(word_spans, silence_runs(audio.tolist(), sr))
            except Exception:
                pass
        peak = float(abs(audio).max()) if len(audio) else 0.0
        sf.write(it["out"], audio, sr, subtype="PCM_16")
        results.append({"seconds": len(audio) / sr, "peak": round(peak, 4), "sample_rate": sr,
                        "synth_s": round(time.time() - t1, 3), "word_spans": word_spans,
                        "phonemes": ph, "n_phoneme_timings": len(tm), "emphasis_applied": emph_applied})
    sys.stdout.write(json.dumps({"load_s": round(load_s, 3), "items": results,
                                 "espeak_short_dir": short_dir}) + "\n")
    return 0


# --------------------------------------------------------------------------- probe
def _probe_cache() -> Path:
    return voices_root() / "probe.json"


def _install_stamp(engine: str) -> float:
    p = engine_dir(engine) / "installed.json"
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def probe(engines=None, write_cache: bool = True) -> dict:
    """Short quality probe per available engine: real audio, sane duration for the
    word count, no clipping, measured real-time factor. Rank = quality prior,
    gated by probe success."""
    det = detect()
    cand = engines or [e for e in ENGINE_ORDER if det[e]["installed"] and not det[e]["disabled"]]
    rows = {}
    with tempfile.TemporaryDirectory(prefix="c8voice-probe-") as td:
        for e in cand:
            row = {"engine": e, "ok": False, "prior": prior(e)}
            try:
                v = default_voice(e)
                t0 = time.time()
                seg = synth_segments(e, v, [{"text": PROBE_TEXT, "speed": 1.0}], Path(td) / e)[0]
                wall = time.time() - t0
                st = wav_stats(seg["wav"])
                wpm = PROBE_WORDS / st["seconds"] * 60 if st["seconds"] else 0
                row.update({"voice": v, "seconds": round(st["seconds"], 3), "peak": st["peak"],
                            "rms": st["rms"], "wpm": round(wpm, 1),
                            "rtf_wall": round(wall / st["seconds"], 3) if st["seconds"] else None,
                            "rtf_synth": (round(seg["synth_s"] / st["seconds"], 3)
                                          if seg.get("synth_s") and st["seconds"] else None),
                            "word_timings": seg.get("word_spans") is not None})
                checks = {"audio": st["seconds"] > 0.5 and st["rms"] > 0.003,
                          "duration_sane": 90 <= wpm <= 260,
                          "no_clipping": st["peak"] < 0.99}
                row["checks"] = checks
                row["ok"] = all(checks.values())
                if not row["ok"]:
                    row["reason"] = "failed: " + ",".join(k for k, ok in checks.items() if not ok)
            except Exception as ex:
                row["reason"] = f"error: {ex}"[:300]
            rows[e] = row
    ranking = sorted([e for e in rows if rows[e]["ok"]], key=lambda e: -rows[e]["prior"])
    res = {"ts": time.time(), "engines": rows, "ranking": ranking, "best": ranking[0] if ranking else None,
           "load_average_1m": (os.getloadavg()[0] if hasattr(os, "getloadavg") else None)}
    if write_cache:
        try:
            voices_root().mkdir(parents=True, exist_ok=True)
            _probe_cache().write_text(json.dumps(res, indent=1), encoding="utf-8")
        except OSError:
            pass
    return res


def cached_probe() -> dict | None:
    try:
        return json.loads(_probe_cache().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def best_engine(max_age_s: float = 7 * 86400) -> tuple:
    """-> (engine or None, ranking list, probe dict). Uses a fresh cached probe when
    nothing was (re)installed since; disabled or deleted engines are always excluded."""
    avail = available()
    pr = cached_probe()
    fresh = bool(pr) and (time.time() - pr.get("ts", 0) < max_age_s) and all(
        e in pr.get("engines", {}) and _install_stamp(e) <= pr.get("ts", 0) for e in avail)
    if not fresh:
        pr = probe(avail)
    ranking = [e for e in pr.get("ranking", []) if e in avail]
    return (ranking[0] if ranking else None), ranking, pr


# --------------------------------------------------------------------------- install
def _sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dst: Path, sha: str, timeout: float) -> str:
    import urllib.request
    part = dst.with_name(dst.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "coordn8-voice-installer"})
    h = hashlib.sha256()
    deadline = time.time() + max(timeout * 20, 600)
    with urllib.request.urlopen(req, timeout=timeout) as r, open(part, "wb") as f:
        while True:
            if time.time() > deadline:
                raise TimeoutError(f"download exceeded {int(deadline - time.time())}s budget")
            chunk = r.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
            f.write(chunk)
    if sha and h.hexdigest() != sha:
        part.unlink()
        raise ValueError(f"sha256 mismatch for {url}")
    part.rename(dst)
    return h.hexdigest()


def _link_or_copy(src: Path, dst: Path) -> str:
    try:
        os.link(str(src), str(dst))
        return "hardlink"
    except OSError:
        shutil.copy2(str(src), str(dst))
        return "copy"


def _find_python(uv: str | None, timeout: float) -> str | None:
    env_py = os.environ.get("COORDN8_VOICE_PYTHON")
    if env_py and Path(env_py).exists():
        return env_py
    if uv:
        for req in ("3.11", ">=3.10,<3.14"):
            r = subprocess.run([uv, "python", "find", req], capture_output=True, text=True,
                               timeout=timeout)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip().splitlines()[-1]
        r = subprocess.run([uv, "python", "install", "3.11"], capture_output=True, text=True,
                           timeout=max(timeout, 600))
        if r.returncode == 0:
            r = subprocess.run([uv, "python", "find", "3.11"], capture_output=True, text=True,
                               timeout=timeout)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip().splitlines()[-1]
    for name in ("python3.12", "python3.11", "python3.13", "python3.10"):
        p = shutil.which(name)
        if p:
            return p
    return None


def _uv() -> str | None:
    env = os.environ.get("COORDN8_UV")
    if env and Path(env).exists():
        return env
    return shutil.which("uv") or (UV_DEFAULT if Path(UV_DEFAULT).exists() else None)


def _venv_ok(engine: str, modules: list) -> bool:
    py = venv_python(engine)
    if not py.exists():
        return False
    r = subprocess.run([str(py), "-I", "-c", "import " + ",".join(modules)], capture_output=True,
                       text=True, timeout=120)
    return r.returncode == 0


def _venv_packages(engine: str) -> dict:
    py = venv_python(engine)
    code = ("import importlib.metadata as m, json; "
            "print(json.dumps({d.metadata['Name']: d.version for d in m.distributions()}))")
    try:
        r = subprocess.run([str(py), "-I", "-c", code], capture_output=True, text=True, timeout=60)
        return json.loads(r.stdout) if r.returncode == 0 else {}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}


def _make_venv(engine: str, pip: list, modules: list, cache: Path | None, timeout: float) -> dict:
    if _venv_ok(engine, modules):
        return {"status": "present"}
    uv = _uv()
    py = _find_python(uv, timeout)
    if not py:
        return {"status": "skipped", "reason": "no Python >= 3.10 found and uv unavailable to fetch one"}
    vdir = engine_dir(engine) / "venv"
    env = dict(os.environ)
    if cache:
        env["UV_CACHE_DIR"] = str(cache / "uv")
    if uv:
        if not venv_python(engine).exists():
            r = subprocess.run([uv, "venv", "-q", "--python", py, str(vdir)], capture_output=True,
                               text=True, timeout=timeout, env=env)
            if r.returncode != 0:
                return {"status": "skipped", "reason": f"uv venv failed: {r.stderr[-300:]}"}
        r = subprocess.run([uv, "pip", "install", "-q", "--python", str(venv_python(engine))] + pip,
                           capture_output=True, text=True, timeout=max(timeout, 900), env=env)
        if r.returncode != 0:
            return {"status": "skipped", "reason": f"uv pip install failed: {r.stderr[-300:]}"}
    else:
        if not venv_python(engine).exists():
            r = subprocess.run([py, "-m", "venv", str(vdir)], capture_output=True, text=True, timeout=timeout)
            if r.returncode != 0:
                return {"status": "skipped", "reason": f"venv failed: {r.stderr[-300:]}"}
        r = subprocess.run([str(venv_python(engine)), "-m", "pip", "install", "-q"] + pip,
                           capture_output=True, text=True, timeout=max(timeout, 900))
        if r.returncode != 0:
            return {"status": "skipped", "reason": f"pip install failed: {r.stderr[-300:]}"}
    if not _venv_ok(engine, modules):
        return {"status": "skipped", "reason": "venv created but the engine does not import"}
    return {"status": "installed", "python": py}


def install(engines=None, cache=None, timeout: float = 60.0) -> dict:
    """Install engines into the user zone. Non-interactive; every download has a
    timeout and a sha256 check; a failure skips that engine with its reason.
    Existing files are never overwritten (user zone rule): a file that is present
    with an unexpected hash is left untouched and reported."""
    cache = Path(cache).expanduser() if cache else None
    want = engines or ["kokoro", "piper", "say"]
    report = {"engines": {}, "cache": str(cache) if cache else None}
    for e in want:
        spec = engine_spec(e)
        row = {"engine": e, "status": "skipped", "files": [], "licence": spec.get("licence")}
        try:
            if e in ("say", "espeak-ng"):
                d = detect()[e]
                row["status"] = "present" if d["installed"] else "skipped"
                row["reason"] = d["reason"] or "system voice; nothing to download"
                report["engines"][e] = row
                continue
            if not spec:
                row["reason"] = "no install spec in data/voices.json"
                report["engines"][e] = row
                continue
            dest_root = engine_dir(e)
            dest_root.mkdir(parents=True, exist_ok=True)
            ok_files = True
            for f in spec.get("files", []):
                sub = f.get("subdir", "")
                dst = dest_root / sub / f["name"] if sub else dest_root / f["name"]
                dst.parent.mkdir(parents=True, exist_ok=True)
                fr = {"name": f["name"], "sha256": f.get("sha256")}
                if dst.exists():
                    have = _sha256(dst)
                    fr["action"] = "present" if have == f.get("sha256") else "conflict-left-untouched"
                    if fr["action"] != "present":
                        ok_files = False
                        fr["reason"] = "existing file has an unexpected sha256; not modified"
                    row["files"].append(fr)
                    continue
                src = None
                if cache:
                    for c in (cache / e / f["name"], cache / e / sub / f["name"]):
                        if c.exists() and _sha256(c) == f.get("sha256"):
                            src = c
                            break
                try:
                    if src is None:
                        if cache:
                            (cache / e).mkdir(parents=True, exist_ok=True)
                            tgt = cache / e / f["name"]
                            _download(f["url"], tgt, f.get("sha256"), timeout)
                            fr["action"] = "downloaded-to-cache+" + _link_or_copy(tgt, dst)
                        else:
                            _download(f["url"], dst, f.get("sha256"), timeout)
                            fr["action"] = "downloaded"
                    else:
                        fr["action"] = "from-cache:" + _link_or_copy(src, dst)
                except Exception as ex:
                    ok_files = False
                    fr["action"] = "failed"
                    fr["reason"] = f"{type(ex).__name__}: {ex}"[:300]
                row["files"].append(fr)
            v = _make_venv(e, spec.get("pip", []), spec.get("import_check", []), cache, timeout)
            row["venv"] = v
            if v["status"] in ("installed", "present") and ok_files:
                row["status"] = "installed"
                stamp = {"engine": e, "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                         "licence": spec.get("licence"), "source": spec.get("source"),
                         "files": [{"name": f["name"], "sha256": f.get("sha256"), "url": f.get("url"),
                                    "licence": f.get("licence")} for f in spec.get("files", [])],
                         "packages": _venv_packages(e)}
                st = dest_root / "installed.json"
                st.write_text(json.dumps(stamp, indent=1) + "\n", encoding="utf-8")
            else:
                row["reason"] = v.get("reason") or "one or more model files failed (see files)"
        except Exception as ex:
            row["status"] = "skipped"
            row["reason"] = f"{type(ex).__name__}: {ex}"[:300]
        report["engines"][e] = row
    return report


def dir_size(p: Path) -> int:
    """Apparent size in bytes; hard-linked or cloned files are counted at full size."""
    total = 0
    if not p.exists():
        return 0
    for root, _dirs, files in os.walk(str(p)):
        for f in files:
            fp = os.path.join(root, f)
            try:
                if not os.path.islink(fp):
                    total += os.path.getsize(fp)
            except OSError:
                pass
    return total


if __name__ == "__main__":
    if sys.argv[1:2] == ["_kokoro_worker"]:
        sys.exit(_kokoro_worker())
    print(json.dumps(detect(), indent=1))
