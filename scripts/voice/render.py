#!/usr/bin/env python3
"""Coordn8 — narration RENDER on a local engine, per line, plus voice QA.

    synth(text, speech=None, engine="auto", voice=None, speed=1.0, out_wav=...)

* The voice ALWAYS gets the spoken form. If `speech` is given it is used; else
  it is derived from the display text by scripts/render/pronounce.py
  (initialisms spelled letter by letter for the voice only; word-acronyms left
  alone). The display text that comes back is never spelled — captions and the
  page keep "SOP".
* SSML is used only where the engine supports it (none of the default engines
  does), otherwise plain spaced letters.
* Script markup (V3), stripped from the display text:
      *word*            emphasis on that word (or words)
      [pause 300]       silence of 300 ms (any 0..5000)
      [beat]            a beat pause, 300 ms (inside the 200-400 ms band)
      {slow}...{/slow}  slower delivery (x0.85) for the hard idea
      {fast}...{/fast}  quicker delivery (x1.1) for connective tissue
  Engines without SSML get engine parameters instead: per-segment speed and
  inserted silence; emphasis by forced stress (Kokoro) or `[[emph +]]` (say).
* Output: 24 kHz mono 16-bit wav. Returns {engine, voice, seconds,
  word_timings or None, timing_source, ...}.

Voice QA (V4): peak / clipping, silence gaps > 1.2 s inside a line (deliberate
marked pauses are excused), and a listening list of every glossary term whose
spoken form was spelled, with the engine that said it.
"""
from __future__ import annotations

import array
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "render"))
import engines  # noqa: E402
from engines import SR, EngineError  # noqa: E402

try:
    from pronounce import to_speech  # noqa: E402
except Exception:  # pragma: no cover - pronounce.py ships with the package
    to_speech = None
try:
    from caption_marks import strip as strip_marks  # noqa: E402  (display markup)
except Exception:  # pragma: no cover
    def strip_marks(s):
        return s

BEAT_MS = 300
SLOW, FAST = 0.85, 1.1
MAX_PAUSE_MS = 5000
GAP_FLAG_S = 1.2

_TOKEN = re.compile(r"\[pause\s+(\d+)\s*(?:ms)?\]|\[beat\]|\{(slow|fast)\}|\{/(slow|fast)\}"
                    r"|\*([^*\s](?:[^*]*[^*\s])?)\*", re.I)


def _clean(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return re.sub(r"\s+([.,;:!?])", r"\1", s)


# --------------------------------------------------------------------------- V3 markup
def parse_markup(text: str) -> dict:
    """Split authored text into segments with speed, emphasis and pauses.

    Returns {"display": str, "segments": [{"text", "speed_mult", "emphasis",
    "pause_before_ms"}], "had_markup": bool}. "emphasis" holds word indexes
    inside the segment's own text."""
    segs = []
    cur = {"parts": [], "speed_mult": 1.0, "emph_spans": [], "pause_before_ms": 0}
    mode = 1.0
    pending_pause = 0
    had = False

    def close():
        nonlocal cur, pending_pause
        txt = _clean("".join(cur["parts"]))
        if txt:
            words = txt.split()
            emph = []
            for (a, b) in cur["emph_spans"]:
                emph.extend(range(a, min(b, len(words))))
            segs.append({"text": txt, "speed_mult": cur["speed_mult"], "emphasis": sorted(set(emph)),
                         "pause_before_ms": cur["pause_before_ms"]})
            cur = {"parts": [], "speed_mult": mode, "emph_spans": [], "pause_before_ms": 0}
        else:
            # nothing spoken yet in this segment: carry the pause forward
            cur = {"parts": [], "speed_mult": mode, "emph_spans": [],
                   "pause_before_ms": cur["pause_before_ms"]}

    pos = 0
    for m in _TOKEN.finditer(text):
        cur["parts"].append(text[pos:m.start()])
        pos = m.end()
        had = True
        if m.group(1) is not None or m.group(0).lower() == "[beat]":
            ms = BEAT_MS if m.group(1) is None else max(0, min(MAX_PAUSE_MS, int(m.group(1))))
            close()
            cur["pause_before_ms"] += ms
        elif m.group(2):
            close()
            mode = SLOW if m.group(2).lower() == "slow" else FAST
            cur["speed_mult"] = mode
        elif m.group(3):
            close()
            mode = 1.0
            cur["speed_mult"] = mode
        elif m.group(4):
            before = _clean("".join(cur["parts"]))
            n0 = len(before.split()) if before else 0
            inner = m.group(4)
            cur["parts"].append(" " + inner + " ")
            cur["emph_spans"].append((n0, n0 + len(inner.split())))
    cur["parts"].append(text[pos:])
    trailing = cur["pause_before_ms"] if not _clean("".join(cur["parts"])) else 0
    close()
    display = _clean(" ".join(s["text"] for s in segs))
    return {"display": display, "segments": segs, "had_markup": had, "trailing_pause_ms": trailing}


def strip_markup(text: str) -> str:
    return parse_markup(text)["display"]


# --------------------------------------------------------------------------- spoken form
def spoken_words(display_words: list, *, ssml: bool, spell=None, word=None) -> tuple:
    """Per display word -> its spoken words. Returns (spoken_words, mapping, spelled)."""
    out, mapping, spelled = [], [], []
    for w in display_words:
        if to_speech is None:
            sp = strip_marks(w)          # never hand a bracket to the engine
        else:
            sp = to_speech(w, ssml=ssml, spell=set(spell or ()), word=set(word or ()))
        parts = sp.split() if not ssml else [sp]
        mapping.append((len(out), len(out) + len(parts)))
        out.extend(parts)
        shown = strip_marks(w)           # a caption mark is display markup, not a spelling
        if sp != shown:
            core = re.sub(r"[^A-Za-z0-9]", "", shown)
            spelled.append({"term": core or shown, "display": shown, "spoken": sp})
    return out, mapping, spelled


# --------------------------------------------------------------------------- audio assembly
def _trim(a, thresh: int = 100, pad: int = int(0.02 * SR)):
    """Strip leading/trailing near-silence (|x| <= ~-50 dBFS); returns (trimmed, head_samples)."""
    n = len(a)
    i = 0
    while i < n and abs(a[i]) <= thresh:
        i += 1
    if i == n:
        return array.array("h"), 0
    j = n - 1
    while j > i and abs(a[j]) <= thresh:
        j -= 1
    s, e = max(0, i - pad), min(n, j + 1 + pad)
    return a[s:e], s


def _zeros(ms: float):
    return array.array("h", bytes(2 * int(SR * ms / 1000.0)))


def _resolve_voice(engine: str, voice):
    if not voice:
        return engines.default_voice(engine), None
    v = str(voice)
    if ":" in v:
        e, name = v.split(":", 1)
        if e != engine:
            return engines.default_voice(engine), f"voice {v} belongs to {e}; used {engine}'s default"
        v = name
    if v not in engines.detect()[engine]["voices"]:
        return engines.default_voice(engine), f"voice {v} not installed for {engine}; used the default"
    return v, None


def _plan_line(line: dict, *, ssml: bool, spell, word) -> dict:
    text = line.get("text", "")
    pm = parse_markup(text)
    speech = line.get("speech")
    exact = True
    segments = []
    spelled_all = []
    if speech:
        sp_pm = parse_markup(speech)
        derived = None
        if to_speech is not None:
            derived = " ".join(spoken_words(pm["display"].split(), ssml=False, spell=spell, word=word)[0])
        if derived is not None and _clean(sp_pm["display"]) == _clean(derived):
            speech = None                       # same as derivation: use the precise path
        else:
            exact = False
            for s in sp_pm["segments"]:
                segments.append({"display": s["text"], "spoken": s["text"], "speed_mult": s["speed_mult"],
                                 "emphasis": s["emphasis"], "pause_before_ms": s["pause_before_ms"],
                                 "map": None})
    if not speech:
        for s in pm["segments"]:
            dw = s["text"].split()
            sw, mp, spl = spoken_words(dw, ssml=ssml, spell=spell, word=word)
            spelled_all.extend(spl)
            emph_spoken = sorted({k for i in s["emphasis"] if i < len(mp) for k in range(*mp[i])})
            segments.append({"display": s["text"], "spoken": " ".join(sw), "speed_mult": s["speed_mult"],
                             "emphasis": emph_spoken, "pause_before_ms": s["pause_before_ms"], "map": mp})
    return {"display": pm["display"], "segments": segments, "exact_map": exact,
            "spelled": spelled_all, "had_markup": pm["had_markup"],
            "trailing_pause_ms": pm.get("trailing_pause_ms", 0)}


def _render_with(engine: str, voice: str, plans: list, speed: float, outs: list, tail_ms: float,
                 workdir: Path) -> list:
    segs, owners = [], []
    for li, p in enumerate(plans):
        for si, s in enumerate(p["segments"]):
            segs.append({"text": s["spoken"], "speed": speed * s["speed_mult"], "emphasis": s["emphasis"],
                         "ssml": False})
            owners.append((li, si))
    rendered = engines.synth_segments(engine, voice, segs, workdir) if segs else []
    by_line = {}
    for (li, si), r in zip(owners, rendered):
        by_line.setdefault(li, []).append((si, r))
    results = []
    for li, p in enumerate(plans):
        audio = array.array("h")
        wt, seg_rows, pauses = [], [], []
        timings_ok = True
        for si, r in by_line.get(li, []):
            s = p["segments"][si]
            if s["pause_before_ms"]:
                st = len(audio) / SR
                audio.extend(_zeros(s["pause_before_ms"]))
                pauses.append({"start": round(st, 3), "end": round(len(audio) / SR, 3),
                               "ms": s["pause_before_ms"]})
            a, _sr = engines.read_wav(r["wav"])
            a, head = _trim(a)
            off = len(audio) / SR
            audio.extend(a)
            seg_rows.append({"start": round(off, 3), "end": round(len(audio) / SR, 3), "text": s["display"],
                             "spoken": s["spoken"], "speed": round(speed * s["speed_mult"], 3),
                             "pause_before_ms": s["pause_before_ms"], "emphasis": s["emphasis"]})
            spans = r.get("word_spans")
            sw = s["spoken"].split()
            if not spans or len(spans) != len(sw):
                timings_ok = False
                continue
            shift = off - head / SR
            if s["map"] is not None and p["exact_map"]:
                for di, dword in enumerate(s["display"].split()):
                    a0, a1 = s["map"][di]
                    if a1 <= a0:
                        continue
                    wt.append({"word": dword, "spoken": " ".join(sw[a0:a1]),
                               "start": round(max(0.0, spans[a0][0] + shift), 3),
                               "end": round(max(0.0, spans[a1 - 1][1] + shift), 3)})
            else:
                for k, w in enumerate(sw):
                    wt.append({"word": w, "spoken": w, "start": round(max(0.0, spans[k][0] + shift), 3),
                               "end": round(max(0.0, spans[k][1] + shift), 3)})
        trail = (p.get("trailing_pause_ms") or 0) + (tail_ms or 0)
        if trail:
            st = len(audio) / SR
            audio.extend(_zeros(trail))
            pauses.append({"start": round(st, 3), "end": round(len(audio) / SR, 3), "ms": trail})
        engines.write_wav(outs[li], audio, SR)
        total = len(audio) / SR
        for w in wt:
            w["end"] = round(min(w["end"], total), 3)
            w["start"] = round(min(w["start"], w["end"]), 3)
        has_t = timings_ok and bool(wt)
        src = None
        if has_t:
            src = (f"{engine}: model phoneme durations grouped into words"
                   + ("" if p["exact_map"] else " (spoken-form words; display mapping not derivable)"))
        else:
            src = f"none: {engine} gives no word timings here; use the E2 fallback aligner"
        results.append({"engine": engine, "voice": voice, "seconds": round(len(audio) / SR, 3),
                        "word_timings": wt if has_t else None, "timing_source": src,
                        "display_text": p["display"], "spoken_text": " ".join(s["spoken"] for s in p["segments"]),
                        "segments": seg_rows, "pauses": pauses, "out_wav": str(outs[li]), "sample_rate": SR,
                        "speed": speed, "spelled": p["spelled"], "ssml_used": False,
                        "ssml_note": ("engine has no SSML support; spaced letters + engine parameters used"
                                      if not engines.capabilities(engine).get("ssml") else
                                      "SSML-capable engine; plain path used for exact word mapping"),
                        "had_markup": p["had_markup"]})
    return results


def synth_lines(lines: list, out_dir, engine: str = "auto", voice=None, speed: float = 1.0, *,
                spell=None, word=None, tail_pause_ms: float = 0, fallback: bool | None = None) -> list:
    """Render several lines (each {text, speech?, id?}) to one wav each. One engine
    process per call, so a neural model loads once for the whole batch."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    outs = [out_dir / f"{(ln.get('id') or f'line{i:03d}')}.wav" for i, ln in enumerate(lines)]
    if engine == "auto":
        _best, order, _pr = engines.best_engine()
        if not order:
            raise EngineError("no local voice engine passed the probe on this machine")
        fallback = True if fallback is None else fallback
    else:
        if engine not in engines.available():
            if not fallback:
                d = engines.detect().get(engine, {})
                raise EngineError(f"engine {engine} not available: {d.get('reason') or 'disabled'}")
            order = [engine] + [e for e in engines.best_engine()[1] if e != engine]
        else:
            order = [engine] + ([e for e in engines.best_engine()[1] if e != engine] if fallback else [])
    tried = []
    for e in order:
        if e not in engines.available():
            tried.append({"engine": e, "error": "not available"})
            continue
        v, note = _resolve_voice(e, voice)
        plans = [_plan_line(ln, ssml=False, spell=spell, word=word) for ln in lines]
        with tempfile.TemporaryDirectory(prefix="c8voice-") as td:
            try:
                res = _render_with(e, v, plans, float(speed), outs, tail_pause_ms, Path(td))
            except EngineError as ex:
                tried.append({"engine": e, "error": str(ex)[:300]})
                if not fallback:
                    raise
                continue
        for r in res:
            r["fallback_from"] = tried
            if note:
                r["voice_note"] = note
        return res
    raise EngineError(f"every engine failed: {tried}")


def synth(text: str, speech: str | None = None, engine: str = "auto", voice=None, speed: float = 1.0,
          out_wav=None, *, spell=None, word=None, tail_pause_ms: float = 0, fallback: bool | None = None) -> dict:
    """Render ONE narration line. See module docstring."""
    out_wav = Path(out_wav) if out_wav else Path(tempfile.mkdtemp(prefix="c8voice-out-")) / "line.wav"
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="c8voice-line-") as td:
        line = {"text": text, "id": "line"}
        if speech:
            line["speech"] = speech
        res = synth_lines([line], td, engine=engine, voice=voice, speed=speed, spell=spell, word=word,
                          tail_pause_ms=tail_pause_ms, fallback=fallback)[0]
        shutil.move(res["out_wav"], str(out_wav))
    res["out_wav"] = str(out_wav)
    return res


# --------------------------------------------------------------------------- V4 QA
def silence_gaps(path, frame_ms: float = 20.0, rel_db: float = 40.0, floor_db: float = -50.0) -> list:
    """Silent runs inside the audio (not touching its start or end).
    A frame is silent when its RMS is below max(floor, loudest frame - rel_db)."""
    a, sr = engines.read_wav(path)
    hop = max(1, int(sr * frame_ms / 1000.0))
    import math
    dbs = []
    for i in range(0, len(a), hop):
        fr = a[i:i + hop]
        if not fr:
            break
        rms = (sum(x * x for x in fr) / len(fr)) ** 0.5
        dbs.append(20 * math.log10(rms / 32768.0) if rms > 0 else -120.0)
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


def qa_wav(path, intended_pauses=None, gap_flag_s: float = GAP_FLAG_S) -> dict:
    st = engines.wav_stats(path)
    gaps = silence_gaps(path)
    intended = intended_pauses or []
    for g in gaps:
        # a gap is deliberate when at least half of it lies inside a marked pause
        g["intended"] = any(min(g["end"], p["end"]) - max(g["start"], p["start"]) >= 0.5 * g["seconds"]
                            for p in intended)
    long_gaps = [g for g in gaps if g["seconds"] > gap_flag_s and not g["intended"]]
    clipped = st["peak"] >= 0.99 or st["clipped_samples"] >= 3
    return {"wav": str(path), "seconds": round(st["seconds"], 3), "peak": st["peak"],
            "clipped_samples": st["clipped_samples"], "clipped": clipped,
            "gaps": gaps, "long_internal_gaps": long_gaps,
            "status": "review" if (clipped or long_gaps) else "pass"}


def voice_qa(results: list, glossary=None) -> dict:
    """Scorecard section `voice_qa` over rendered lines (synth results)."""
    lines, listen = [], []
    gl = [g for g in (glossary or []) if g]
    for i, r in enumerate(results):
        q = qa_wav(r["out_wav"], r.get("pauses"))
        q.update({"line": i, "engine": r.get("engine"), "voice": r.get("voice"),
                  "display_text": r.get("display_text")})
        lines.append(q)
        seen = set()
        for sp in r.get("spelled") or []:
            if gl and sp["term"].upper() not in {g.upper() for g in gl}:
                continue
            key = sp["term"].upper()
            if key in seen:
                continue
            seen.add(key)
            at = None
            for w in r.get("word_timings") or []:
                if re.sub(r"[^A-Za-z0-9]", "", w["word"]).upper().startswith(key):
                    at = w["start"]
                    break
            listen.append({"term": sp["term"], "spoken_form": sp["spoken"], "line": i,
                           "engine": r.get("engine"), "voice": r.get("voice"), "at_s": at,
                           "check": "listen: the voice should say the letters"})
        for g in gl:
            if g.upper() not in seen and re.search(r"\b" + re.escape(g) + r"\b", r.get("display_text") or ""):
                sp = to_speech(g) if to_speech else g
                if sp != g:
                    listen.append({"term": g, "spoken_form": sp, "line": i, "engine": r.get("engine"),
                                   "voice": r.get("voice"), "at_s": None,
                                   "check": "listen: spelled form expected but not recorded for this line"})
    summary = {"lines": len(lines), "clipped_lines": sum(1 for q in lines if q["clipped"]),
               "lines_with_long_gaps": sum(1 for q in lines if q["long_internal_gaps"]),
               "listening_checks": len(listen),
               "engines": sorted({q["engine"] for q in lines if q["engine"]})}
    verdict = "review" if (summary["clipped_lines"] or summary["lines_with_long_gaps"]) else "pass"
    return {"voice_qa": {"verdict": verdict, "summary": summary, "lines": lines, "listening_list": listen,
                         "limits": "spelled glossary terms are listed for a human listening check; "
                                   "nothing here transcribes the audio"}}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Render one narration line on a local voice engine.")
    ap.add_argument("text")
    ap.add_argument("--speech")
    ap.add_argument("--engine", default="auto")
    ap.add_argument("--voice")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    print(json.dumps(synth(a.text, a.speech, a.engine, a.voice, a.speed, a.out), indent=1))
