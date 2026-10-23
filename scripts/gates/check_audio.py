#!/usr/bin/env python3
"""
Coordn8 gate — AUDIO QA (E5 + the voice half V4), advisory.

Measures the rendered sound, never assumes it:

  loudness     integrated loudness of the mix (ffmpeg ebur128, EBU R128 / BS.1770) within
               target +- tolerance (default -16 +- 1 LUFS)
  true_peak    4x-oversampled true peak <= ceiling (default -1 dBTP)
  clipping     samples at full scale: any run of >= 3, or >= 10 in total, is clipping
  sfx_audible  every placed effect, against the BED (voice + music stems) in the same
               window: per third-octave band, the effect must reach the bed's MASKED
               threshold (the bed's band levels spread to their neighbours: -10 dB per band
               upward, -25 dB per band downward) in at least one band where the effect itself
               has energy (within 20 dB of its own loudest band). Needs the stems (written by scripts/render/audio.py); without
               them this check is SKIPPED, never guessed.
  gaps         silence longer than 1.2 s INSIDE a narration line (not at its start or end);
               a gap that mostly lies in a deliberately marked pause ([pause N]) is excused
  voice_stats  (informational) pitch range in semitones and pauses per line, to compare
               takes of the same narration — never a pass/fail
  spoken_form  every glossary term in a line's display text was handed to the voice in its
               spoken form (scripts/render/pronounce.py: initialisms letter by letter); a
               term given as a bare word is flagged; every spelled term is listed with its
               time for a listening check (nothing here transcribes audio)

Inputs:
    check_audio.py MIX.wav [--plan audio_plan.json] [--stems DIR] [--lines lines.json]
        [--glossary SOP,API | --glossary-file terms.txt] [--target -16] [--tolerance 1]
        [--tp -1] [--gap-s 1.2]
  lines.json: [{"display_text"|"text", "spoken_text"?, "out_wav"|"wav"?, "start"?, "end"?,
               "pauses"?: [{"start","end"}], "word_timings"?}] — the voice renderer's results
  (scripts/voice/render.py) are accepted as they are.

Exit codes (shared protocol): 0 PASS, 1 REVIEW, 2 SKIPPED (no input), 3 ERROR.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "render"))
from lib.common import EXIT_ERROR, EXIT_PASS, EXIT_REVIEW, EXIT_SKIPPED, emit  # noqa: E402
import audio  # noqa: E402

try:
    from pronounce import to_speech  # noqa: E402
except Exception:  # pragma: no cover
    to_speech = None

GAP_S = 1.2
CLIP_LEVEL = 32735 / 32768.0


def _list(v):
    return v.tolist() if hasattr(v, "tolist") else list(v)


# --------------------------------------------------------------------------- measurements
def clipping(xs) -> dict:
    run = best = total = 0
    for x in xs:
        if abs(x) >= CLIP_LEVEL:
            total += 1
            run += 1
            best = max(best, run)
        else:
            run = 0
    return {"full_scale_samples": total, "longest_run": best, "clipped": best >= 3 or total >= 10}


def silence_gaps(xs, sr: int, frame_s: float = 0.02, rel_db: float = 40.0, floor_db: float = -50.0) -> list:
    hop = max(1, int(sr * frame_s))
    dbs = []
    for i in range(0, len(xs), hop):
        fr = xs[i:i + hop]
        if not fr:
            break
        r = math.sqrt(sum(x * x for x in fr) / len(fr))
        dbs.append(20 * math.log10(r) if r > 0 else -120.0)
    if not dbs:
        return []
    thr = max(floor_db, max(dbs) - rel_db)
    sil = [d < thr for d in dbs]
    gaps, i, n = [], 0, len(sil)
    while i < n:
        if sil[i]:
            j = i
            while j < n and sil[j]:
                j += 1
            if i > 0 and j < n:
                gaps.append({"start": round(i * hop / sr, 3), "end": round(j * hop / sr, 3),
                             "seconds": round((j - i) * hop / sr, 3)})
            i = j
        else:
            i += 1
    return gaps


def band_levels(xs, sr: int) -> list | None:
    """Third-octave band levels in dBFS (mean power of the signal inside each band, calibrated
    by Parseval for the Hann window; numpy FFT); None without numpy."""
    try:
        import numpy as np
    except Exception:
        return None
    x = np.asarray(xs, dtype=float)
    if not len(x):
        return None
    nfft = 1 << max(10, (len(x) - 1).bit_length())
    w = np.hanning(len(x))
    spec = np.abs(np.fft.rfft(x * w, nfft)) ** 2 * (2.0 / (nfft * float(np.sum(w * w)) + 1e-30))
    fr = np.fft.rfftfreq(nfft, 1.0 / sr)
    out = []
    for fc in audio.PROFILE_BANDS:
        m = (fr >= fc / 2 ** (1 / 6.0)) & (fr < fc * 2 ** (1 / 6.0))
        e = float(spec[m].sum()) if m.any() else 0.0
        out.append(10 * math.log10(e + 1e-20))
    return out


ROOM_FLOOR_DBFS = -70.0   # per third-octave band, for a mix at -16 LUFS played at ~76 dB SPL in a
                          # quiet room (~35 dB SPL of background noise, ~20 dB SPL per band)


def masked_threshold(bed_db: list, up_db: float = 10.0, down_db: float = 25.0,
                     floor_db: float = ROOM_FLOOR_DBFS) -> list:
    """Per band, the level below which an effect is not heard: the bed's own band levels
    spread to their neighbours (falling `up_db` per third-octave above a loud band — upward
    spread of masking — and `down_db` below it), and never below the room floor."""
    out = []
    for k in range(len(bed_db)):
        m = max(b - (up_db * (k - j) if k > j else down_db * (j - k)) for j, b in enumerate(bed_db))
        out.append(max(m, floor_db))
    return out


def sfx_audibility(sfx, bed, sr: int, at: float, end: float, window_s: float = 0.5) -> dict:
    a = max(0, int(at * sr))
    b = min(len(sfx), int(min(end, at + window_s) * sr))
    s_seg, b_seg = sfx[a:b], bed[a:b]
    if b - a < int(0.01 * sr):
        return {"status": "skipped", "reason": "effect window too short or outside the mix"}
    rms = lambda v: math.sqrt(sum(x * x for x in v) / max(1, len(v)))  # noqa: E731
    broadband = 20 * math.log10((rms(s_seg) + 1e-12) / (rms(b_seg) + 1e-12))
    sb, bb = band_levels(s_seg, sr), band_levels(b_seg, sr)   # dB of mean power per band
    if sb is None:
        margin = broadband
        method = "broadband RMS (numpy unavailable)"
    else:
        top = max(sb)
        live = [k for k, v in enumerate(sb) if v >= top - 20]
        thr = masked_threshold(bb)
        margin = max(sb[k] - thr[k] for k in live)
        method = ("third-octave bands where the effect is within 20 dB of its loudest band, against the bed's "
                  "masked threshold (spread 10 dB/band upward, 25 dB/band downward)")
    return {"margin_db": round(margin, 2), "broadband_db": round(broadband, 2), "method": method}


def voice_stats(xs, sr: int) -> dict:
    """How the narration is DELIVERED, measured (informational): pitch range in semitones
    (5th-95th percentile of the autocorrelation pitch track, 70-400 Hz) and pauses (runs of
    >= 0.15 s more than 26 dB under the loudest frame: count and mean length). A flat,
    breathless read shows as a small range and few pauses. Needs numpy; skipped without it."""
    try:
        import numpy as np
    except Exception:
        return {"status": "skipped", "reason": "numpy unavailable"}
    x = np.asarray(audio.B.tolist(audio._resample(xs, sr, 16000)), dtype=float)
    hop, win, fs = 160, 640, 16000
    if len(x) < win * 2:
        return {"status": "skipped", "reason": "too short"}
    frames = np.lib.stride_tricks.sliding_window_view(x, win)[::hop]
    en = np.sqrt((frames ** 2).mean(axis=1))
    top = float(en.max()) or 1e-9
    lo, hi = fs // 400, fs // 70
    f0 = []
    for fr, e in zip(frames, en):
        if e < 0.1 * top:
            continue
        fr = fr - fr.mean()
        ac = np.correlate(fr, fr, "full")[win - 1:]
        k = int(np.argmax(ac[lo:hi])) + lo
        if ac[k] > 0.4 * ac[0]:
            f0.append(fs / k)
    rng = None
    if len(f0) >= 5:
        st = 12 * np.log2(np.asarray(f0) / np.median(f0))
        rng = round(float(np.percentile(st, 95) - np.percentile(st, 5)), 2)
    quiet = en < top * 10 ** (-26 / 20)
    runs, c = [], 0
    for q in quiet:
        if q:
            c += 1
        else:
            if c:
                runs.append(c)
            c = 0
    pauses = [r * hop / fs for r in runs if r * hop / fs >= 0.15]
    return {"status": "info", "seconds": round(len(x) / fs, 2), "pitch_range_semitones": rng,
            "pauses": len(pauses), "mean_pause_s": round(float(np.mean(pauses)), 3) if pauses else 0.0,
            "voiced_frames": len(f0)}


def spoken_form_check(lines: list, glossary: list) -> dict:
    flags, listen = [], []
    for i, ln in enumerate(lines):
        disp = ln.get("display_text") or ln.get("text") or ""
        spoken = ln.get("spoken_text")
        for g in glossary:
            if not re.search(r"(?<![A-Za-z0-9])" + re.escape(g) + r"(?![A-Za-z0-9])", disp):
                continue
            want = to_speech(g) if to_speech else g
            if want == g:
                continue                     # a word-acronym or a plain word: said as written
            at = None
            for w in ln.get("word_timings") or []:
                if re.sub(r"[^A-Za-z0-9]", "", w.get("word", "")).upper().startswith(g.upper()):
                    at = w.get("start")
                    break
            if spoken is None:
                flags.append({"line": i, "term": g, "expected_spoken": want,
                              "issue": "no spoken text recorded for this line; cannot confirm the spoken form"})
                continue
            norm = lambda s: " ".join(s.split()).lower()  # noqa: E731
            if norm(want) not in norm(spoken):
                flags.append({"line": i, "term": g, "expected_spoken": want, "spoken_text": spoken,
                              "issue": "the voice was given the bare term; it will be read as a word"})
            else:
                listen.append({"line": i, "term": g, "spoken_form": want, "at_s": at,
                               "check": "listen: the voice should say the letters"})
    return {"flags": flags, "listening_list": listen}


# --------------------------------------------------------------------------- the gate
def run_checks(mix_path: Path, plan: dict | None = None, stems_dir: Path | None = None,
               lines: list | None = None, glossary: list | None = None, target: float = audio.TARGET_I,
               tolerance: float = 1.0, tp_max: float = audio.TARGET_TP, gap_s: float = GAP_S) -> dict:
    checks = {}
    sr = audio.SR
    m = audio.measure_file(mix_path)
    if m.get("status") == "ok":
        I, TP = m["integrated_lufs"], m["true_peak_dbtp"]
        checks["loudness"] = {"status": "pass" if abs(I - target) <= tolerance else "review",
                              "integrated_lufs": I, "target": target, "tolerance": tolerance}
        checks["true_peak"] = {"status": "pass" if TP <= tp_max + 1e-9 else "review",
                               "true_peak_dbtp": TP, "ceiling": tp_max}
    else:
        checks["loudness"] = checks["true_peak"] = {"status": "skipped", "reason": m.get("reason")}
    mix = _list(audio.read_audio(mix_path))
    c = clipping(mix)
    checks["clipping"] = {"status": "review" if c["clipped"] else "pass", **c}
    # ---- effects against the bed
    events = []
    if plan:
        rep = plan.get("report", plan)
        events = rep.get("sfx") or []
    if events:
        sd = stems_dir or (Path(plan["report"]["files"]["mix"]).parent / "stems"
                           if plan and plan.get("report", {}).get("files") else None)
        need = [sd / f"{k}.wav" for k in ("sfx", "voice")] if sd else []
        if sd and all(p.exists() for p in need):
            sfx = _list(audio.read_audio(sd / "sfx.wav"))
            bed = _list(audio.read_audio(sd / "voice.wav"))
            if (sd / "music.wav").exists():
                mu = _list(audio.read_audio(sd / "music.wav"))
                bed = [x + y for x, y in zip(bed, mu)]
            rows, bad = [], []
            for ev in events:
                r = sfx_audibility(sfx, bed, sr, float(ev["at"]), float(ev.get("end", ev["at"] + 0.5)))
                r.update({"name": ev.get("name"), "at": ev["at"]})
                rows.append(r)
                if r.get("margin_db") is not None and r["margin_db"] < 0:
                    bad.append(r)
            checks["sfx_audible"] = {"status": "review" if bad else "pass", "events": rows,
                                     "inaudible": [b["name"] for b in bad],
                                     "rule": "margin_db >= 0 in at least one live band"}
        else:
            checks["sfx_audible"] = {"status": "skipped",
                                     "reason": "stems (sfx.wav, voice.wav) not found; audibility needs them"}
    else:
        checks["sfx_audible"] = {"status": "skipped", "reason": "no sound effects placed"}
    # ---- narration lines: gaps + spoken forms
    if lines:
        rows, long_gaps, stats = [], [], []
        for i, ln in enumerate(lines):
            wav = ln.get("out_wav") or ln.get("wav")
            off = 0.0
            if wav and Path(wav).exists():
                xs = _list(audio.read_audio(wav))
            elif ln.get("start") is not None and ln.get("end") is not None:
                src = (stems_dir / "voice.wav") if stems_dir and (stems_dir / "voice.wav").exists() else mix_path
                full = _list(audio.read_audio(src))
                off = float(ln["start"])
                xs = full[int(off * sr):int(float(ln["end"]) * sr)]
            else:
                continue
            gaps = silence_gaps(xs, sr)
            vs = voice_stats(xs, sr)
            if vs.get("status") == "info":
                stats.append({"line": i, **{k: v for k, v in vs.items() if k != "status"}})
            pauses = ln.get("pauses") or []
            for g in gaps:
                g["intended"] = any(min(g["end"], p["end"]) - max(g["start"], p["start"]) >= 0.5 * g["seconds"]
                                    for p in pauses)
                if g["seconds"] > gap_s and not g["intended"]:
                    long_gaps.append({"line": i, "start": round(g["start"] + off, 3),
                                      "end": round(g["end"] + off, 3), "seconds": g["seconds"]})
            rows.append({"line": i, "gaps": gaps})
        checks["gaps"] = {"status": "review" if long_gaps else "pass", "long_internal_gaps": long_gaps,
                          "rule": f"no unmarked silence > {gap_s}s inside a line", "lines": len(rows)}
        checks["voice_stats"] = ({"status": "info", "lines": stats,
                                  "note": "delivery measurements for comparing takes; never a pass/fail"}
                                 if stats else {"status": "skipped", "reason": "no measurable voiced line"})
        if glossary:
            sf = spoken_form_check(lines, glossary)
            checks["spoken_form"] = {"status": "review" if sf["flags"] else "pass", **sf,
                                     "limits": "lists terms for a human listening check; nothing is transcribed"}
        else:
            checks["spoken_form"] = {"status": "skipped", "reason": "no glossary given"}
    else:
        checks["gaps"] = {"status": "skipped", "reason": "no narration lines given (--lines)"}
        checks["spoken_form"] = {"status": "skipped", "reason": "no narration lines given (--lines)"}
    review = [k for k, v in checks.items() if v.get("status") == "review"]
    return {"gate": "audio", "status": "REVIEW" if review else "PASS", "review": review, "checks": checks,
            "mix": str(mix_path),
            "summary": ("audio needs review: " + ", ".join(review)) if review else
                       "loudness, true peak, clipping, effects, gaps and spoken forms measured clean "
                       "(measurements only — not a judgement that the sound is good)"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Coordn8 audio QA gate (advisory).")
    ap.add_argument("mix")
    ap.add_argument("--plan")
    ap.add_argument("--stems")
    ap.add_argument("--lines")
    ap.add_argument("--glossary")
    ap.add_argument("--glossary-file")
    ap.add_argument("--target", type=float, default=audio.TARGET_I)
    ap.add_argument("--tolerance", type=float, default=1.0)
    ap.add_argument("--tp", type=float, default=audio.TARGET_TP)
    ap.add_argument("--gap-s", type=float, default=GAP_S)
    a = ap.parse_args(argv)
    mp = Path(a.mix)
    if not mp.exists():
        emit({"gate": "audio", "status": "SKIPPED", "summary": f"no mix at {mp}"})
        return EXIT_SKIPPED
    try:
        plan = json.loads(Path(a.plan).read_text(encoding="utf-8")) if a.plan else None
        lines = json.loads(Path(a.lines).read_text(encoding="utf-8")) if a.lines else None
        if isinstance(lines, dict):
            lines = lines.get("lines") or lines.get("results") or []
        gl = [g.strip() for g in (a.glossary or "").split(",") if g.strip()]
        if a.glossary_file:
            gl += [g.strip() for g in Path(a.glossary_file).read_text(encoding="utf-8").splitlines() if g.strip()]
        res = run_checks(mp, plan, Path(a.stems) if a.stems else None, lines, gl, a.target, a.tolerance,
                         a.tp, a.gap_s)
    except Exception as ex:  # noqa: BLE001
        emit({"gate": "audio", "status": "ERROR", "summary": f"{type(ex).__name__}: {ex}"[:300]})
        return EXIT_ERROR
    emit(res)
    return EXIT_REVIEW if res["status"] == "REVIEW" else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
