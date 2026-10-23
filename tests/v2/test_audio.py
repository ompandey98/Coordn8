#!/usr/bin/env python3
"""E3 · E4 · E5 (+ the V3 / V4 parts) — hit treatment, beat grid, local music and effects,
ducking, loudness, stems, audio QA.

Hermetic: every file is written under one temp dir that is removed at the end; subprocesses
run with a sandbox user home. Nothing is downloaded — the music, the effects and the
speech-like narration fixtures are all synthesized here from code.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import ROOT, Suite, run, sandbox_env, tmpdir  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts" / "render"))
sys.path.insert(0, str(ROOT / "scripts" / "gates"))
sys.path.insert(0, str(ROOT / "scripts" / "voice"))
import audio  # noqa: E402
import hits  # noqa: E402
import check_audio  # noqa: E402

FIX = ROOT / "tests" / "v2" / "fixtures" / "audio"
FPS = 30
T0 = time.time()
s = Suite("audio")
B = audio.B
SR = audio.SR


def sha(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def lvl(xs) -> float:
    xs = list(xs)
    return audio.db(math.sqrt(sum(x * x for x in xs) / max(1, len(xs))))


def speechlike(layout, seed: int = 3, f0: float = 118.0, amp: float = 0.32, swing: float = 0.04):
    """A deterministic speech-like narration: harmonic buzz through three formants, cut into
    syllables (~4.5/s) and words. layout = [(start_s, end_s), ...] voiced spans."""
    import random as _r
    rng = _r.Random(seed)
    end = max(e for _s, e in layout) + 0.6
    n = audio.secs(end)
    out = B.zeros(n)
    for (a, b) in layout:
        t = a
        while t < b - 0.12:
            syl = 0.14 + rng.random() * 0.1
            m = audio.secs(syl)
            tt = audio.taxis(m)
            f = f0 * (1 + swing * math.sin(t * 3.1))
            src = B.zeros(m)
            for k in range(1, 14):
                src = src + B.sin(tt * (2 * math.pi * f * k)) * (1.0 / k)
            env = B.sin(tt * (math.pi / syl)) ** 2
            v = audio.biquad(src, "bp", 700, 1.2) + audio.biquad(src, "bp", 1250, 1.5) * 0.6 + \
                audio.biquad(src, "bp", 2600, 2.0) * 0.3
            audio.mix_into(out, v * env, t, 1.0)
            t += syl + (0.07 if rng.random() < 0.3 else 0.0)
    return audio.normalize_peak(out, amp)


with tmpdir() as td:
    tmp = Path(td)
    env = sandbox_env(tmp)

    # ================================================================== E4 music
    secs_all = [[0, "light"], [2.2, "groove"], [4.4, "tense"], [6.6, "break"], [8.8, "end"]]
    m1, meta1 = audio.render_music(10.5, secs_all, seed=7, bpm=110)
    m2, _ = audio.render_music(10.5, secs_all, seed=7, bpm=110)
    m3, _ = audio.render_music(10.5, secs_all, seed=8, bpm=110)
    p1, p2, p3 = (audio.write_wav(tmp / f"m{i}.wav", v) for i, v in ((1, m1), (2, m2), (3, m3)))
    s.check("music: the same seed renders a byte-identical WAV", sha(p1) == sha(p2) and Path(p1).stat().st_size > 100000,
            f"{sha(p1)[:12]} vs {sha(p2)[:12]}")
    s.check("music: a different seed renders different bytes (the seed is really used)", sha(p1) != sha(p3))

    prof = {}
    levels = {}
    for st in ("light", "groove", "tense", "break", "end"):
        v, _m = audio.render_music(4.4, [[0, st]], seed=5, bpm=110, fade_out_s=0)
        prof[st] = audio.spectral_profile(v[: audio.secs(4.3)])
        levels[st] = lvl(B.tolist(v))
    dmin = min(audio.profile_distance(prof[a], prof[b]) for a in prof for b in prof if a < b)
    s.check("music: the five section styles (light, groove, tense, break, end) all render audible and differ",
            all(x > -45 for x in levels.values()) and dmin > 1.0, f"levels {levels} min distance {dmin}")

    def f0_of(v):
        import numpy as np  # the pitch measurement itself needs an FFT
        x = np.asarray(B.tolist(v))[: audio.secs(0.6)]
        nf = 1 << 20
        spec = np.abs(np.fft.rfft(x * np.hanning(len(x)), nf))
        fr = np.fft.rfftfreq(nf, 1.0 / SR)
        band = (fr > 200) & (fr < 3000)
        return float(fr[band][np.argmax(spec[band])])
    try:
        import random as _rr
        cents = {}
        ref_cents = {}
        for midi in (69, 84, 91):
            f = audio.mtof(midi)
            got = f0_of(audio.pluck(_rr.Random(1), f, 0.8, 0.5))
            cents[midi] = round(1200 * math.log2(got / f), 1)
            L_ref = max(2, round(SR / f))               # the plain period choice (no compensation)
            ref_cents[midi] = round(1200 * math.log2((SR / (L_ref - 0.5)) / f), 1)
        s.check("music: plucked strings are exactly in tune (string rendered at its own rate; within 2 cents up to MIDI 91)",
                all(abs(c) <= 2 for c in cents.values()), json.dumps(cents))
        s.check("music: tuning beats the integer-period string (which is > 10 cents off at high notes)",
                abs(cents[91]) < abs(ref_cents[91]) and max(abs(c) for c in ref_cents.values()) > 10,
                f"ours {cents} plain {ref_cents}")
    except ImportError:
        s.check("music: pitch checks SKIPPED — numpy unavailable for the FFT (recorded)", True)
        s.check("music: tuning comparison SKIPPED — numpy unavailable (recorded)", True)

    vi, mi = audio.render_music(4.0, [{"at": 0, "invented": "heartbeat-pulse", "why": "a waiting room feel",
                                        "params": {"strum": "none", "bass": "pulse", "drums": "shaker",
                                                   "bells": "sparkle"}}], seed=2)
    s.check("music: an invented section renders and is recorded as invented (styles are a menu, not a fence)",
            lvl(B.tolist(vi)) > -50 and mi["sections"][0]["invented"] and mi["sections"][0]["name"].startswith("invented:"),
            json.dumps(mi["sections"]))
    _va, ma = audio.render_music(8.0, [[0, "groove"]], seed=4, bpm=100, align_downbeat_to=3.37)
    s.check("music: align_downbeat_to puts a bar line exactly on the given time",
            any(abs(d - 3.37) < 1e-6 for d in ma["downbeats"]), json.dumps(ma["downbeats"]))

    # ================================================================== E4 effects
    cat = json.loads((ROOT / "data" / "sfx.json").read_text(encoding="utf-8"))
    ids = [e["id"] for e in cat["effects"]]
    rendered, bad = {}, []
    for i in ids:
        try:
            v, meta = audio.render_sfx(i)
            rendered[i] = v
            pk = B.maxabs(v)
            if not (0.5 < pk <= 0.9001) or lvl(B.tolist(v)) < -40:
                bad.append((i, pk))
        except Exception as ex:  # noqa: BLE001
            bad.append((i, str(ex)))
    s.check("sfx: at least 12 effects are listed in data/sfx.json and every one renders (audible, peak <= -0.9 dBFS)",
            len(ids) >= 12 and not bad and set(ids) == set(audio.SFX), f"{len(ids)} listed; bad {bad}")
    profs = {i: audio.spectral_profile(v) for i, v in rendered.items()}
    pairs = sorted((audio.profile_distance(profs[a], profs[b]), a, b) for a in profs for b in profs if a < b)
    s.check("sfx: every pair of effects is spectrally distinct (profile distance >= 3 dB)",
            pairs and pairs[0][0] >= 3.0, str(pairs[:3]))
    dup, _ = audio.render_sfx("pop", seed=999)
    s.check("sfx: the distinctness measure flags a duplicate (the same effect re-rendered with another seed < 1 dB)",
            audio.profile_distance(profs["pop"], audio.spectral_profile(dup)) < 1.0)
    e1, _ = audio.render_sfx("whoosh")
    e2, _ = audio.render_sfx("whoosh")
    s.check("sfx: the same seed renders identical effect bytes", audio.quantize16(e1) == audio.quantize16(e2))
    inv = {"invented": "soft-blip", "why": "a quieter pop for a dense list",
           "recipe": [{"kind": "tone", "f0": 1400, "f1": 700, "dur": 0.12, "decay": 30},
                      {"kind": "noise", "filter": "hp", "freq": 4000, "dur": 0.05, "decay": 80, "gain": 0.3}]}
    vinv, minv = audio.render_sfx(inv)
    s.check("sfx: an invented recipe effect renders (no catalog membership required)",
            minv["invented"] and lvl(B.tolist(vinv)) > -40 and B.maxabs(vinv) <= 0.9001, json.dumps(minv))

    # ================================================================== E4 mix: duck, loudness, stems
    layout = [(0.5, 2.9), (4.4, 6.9), (8.4, 9.6)]
    voice = speechlike(layout)
    n = len(voice)
    mus, mmeta = audio.render_music(n / SR, [[0, "light"], [2.0, "groove"], [6.5, "break"], [8.0, "groove"]],
                                    seed=3, bpm=112)
    events = [{"name": "pop", "at": 3.2}, dict(inv, at=3.7), {"name": "impact", "at": 7.1}, {"name": "chime", "at": 10.0}]
    rep = audio.mix(voice, tmp / "mixA", music=mus, music_meta=mmeta, sfx_events=events)
    meas = rep.get("measured", {})
    s.check("loudness: the mixed fixture measures -16 +- 1 LUFS integrated (ebur128 on the written file)",
            meas.get("status") == "ok" and abs(meas["integrated_lufs"] + 16) <= 1.0, json.dumps(meas))
    s.check("loudness: the mixed fixture's true peak is <= -1 dBTP (measured, 4x oversampled)",
            meas.get("status") == "ok" and meas["true_peak_dbtp"] <= -1.0, json.dumps(meas))
    s.check("loudness: two-pass loudnorm — pass 2 used the pass-1 MEASURED JSON and stayed linear",
            "input_i" in rep.get("loudnorm_pass1", {}) and rep.get("loudnorm_pass2", {}).get("normalization_type") == "linear",
            json.dumps(rep.get("loudnorm_pass2")))
    s.check("ducking: the report's measured duck under the voice is >= 8 dB", (rep["duck"].get("duck_db") or 0) >= 8,
            json.dumps(rep["duck"]))

    rep0 = audio.mix(voice, tmp / "mixB", music=mus, music_meta=mmeta, sfx_events=events, duck_db=0.0)
    envd = audio.voice_envelope(voice)
    mask = audio.voiced_mask(envd)
    hop = int(SR * audio.HOP_S)
    mA = B.tolist(audio.read_audio(tmp / "mixA" / "stems" / "music.wav"))
    mB = B.tolist(audio.read_audio(tmp / "mixB" / "stems" / "music.wav"))

    def region(xs, on):
        e = c = 0.0
        for k, flag in enumerate(mask):
            if flag == on:
                seg = xs[k * hop:(k + 1) * hop]
                e += sum(x * x for x in seg)
                c += len(seg)
        return 10 * math.log10(e / max(1, c) + 1e-20)
    gA = rep["linear_gain_db"]
    gB = rep0["linear_gain_db"]
    voiced_drop = (region(mB, True) - gB) - (region(mA, True) - gA)
    unvoiced_drop = (region(mB, False) - gB) - (region(mA, False) - gA)
    s.check("ducking: measured from the written stems, the music under the voice is >= 8 dB lower than the same "
            "passage with no ducking", voiced_drop >= 8.0, f"{voiced_drop:.2f} dB")
    s.check("ducking: passages with no voice are left (almost) untouched (< 2 dB) — the duck is keyed by the voice",
            unvoiced_drop < 2.0, f"{unvoiced_drop:.2f} dB")

    stem_files = {k: tmp / "mixA" / "stems" / f"{k}.wav" for k in ("voice", "music", "sfx")}
    have = all(p.exists() for p in stem_files.values())
    if have:
        st = {k: B.tolist(audio.read_audio(p)) for k, p in stem_files.items()}
        mx = B.tolist(audio.read_audio(tmp / "mixA" / "mix.wav"))
        nn = min(len(mx), *(len(v) for v in st.values()))
        res = [mx[i] - st["voice"][i] - st["music"][i] - st["sfx"][i] for i in range(nn)]
        rdb = lvl(res) - lvl(mx[:nn])
    s.check("stems: voice, music and effects stems are written and their sum matches the mix within -40 dB",
            have and rdb <= -40.0, f"have={have} residual={rdb if have else None} dB")

    # a hot, peaky narration: the limiter must act, on every stem alike
    hot = voice * 1.0
    for k in range(6):
        hot[audio.secs(0.8 + k * 1.3)] = 0.999
        hot[audio.secs(0.8 + k * 1.3) + 1] = -0.999
    repH = audio.mix(hot, tmp / "mixH", music=mus, music_meta=mmeta, sfx_events=events)
    sH = {k: B.tolist(audio.read_audio(tmp / "mixH" / "stems" / f"{k}.wav")) for k in ("voice", "music", "sfx")}
    mH = B.tolist(audio.read_audio(tmp / "mixH" / "mix.wav"))
    nH = min(len(mH), *(len(v) for v in sH.values()))
    rH = lvl([mH[i] - sH["voice"][i] - sH["music"][i] - sH["sfx"][i] for i in range(nH)]) - lvl(mH[:nH])
    s.check("loudness: a peaky input is limited before loudnorm (one gain envelope on every stem) and still "
            "lands at <= -1 dBTP with the stems summing to the mix",
            repH["limiter"].get("applied") and repH["measured"]["true_peak_dbtp"] <= -1.0 and rH <= -40,
            f"{repH['limiter']} {repH['measured']} residual {rH:.1f}")

    # ================================================================== E4 off by default + switches
    vpath = audio.write_wav(tmp / "voice.wav", voice)
    outD = tmp / "buildD"
    planD = audio.build(vpath, outD, storyboard={"scenes": []}, design_system={"kit": "x"})
    files = sorted(str(p.relative_to(outD)) for p in outD.rglob("*") if p.is_file())
    s.check("audio is OFF by default: a default build writes no music or effect track and returns the narration as the track",
            planD["enabled"] is False and planD["track"] == str(vpath) and files == ["audio_plan.json"],
            f"{files} {planD.get('switch')}")
    sw = audio.enabled({"audio": {"music": True}}, None)
    sw2 = audio.enabled({"audio": {"enabled": False}}, {"audio": {"enabled": True}})
    sw3 = audio.enabled(None, {"audio": {"sfx": [{"name": "pop", "at": 1}]}})
    s.check("audio switches: a storyboard flag turns music on; the storyboard can switch off what the design system "
            "turned on; a design-system effects list turns effects on",
            sw["music"] and not sw["sfx"] and not (sw2["music"] or sw2["sfx"]) and sw3["sfx"] and not sw3["music"],
            json.dumps([sw, sw2, sw3]))
    words = json.loads((FIX / "hit_words.json").read_text(encoding="utf-8"))
    sb = {"audio": {"music": {"sections": [[0, "light"], [4, "groove"]], "seed": 5, "bpm": 112,
                              "align_downbeat_to": {"line": "the-answer", "word": "cache"}},
                    "sfx": [{"name": "pop", "line": "the-question", "word": "request"}, {"name": "impact", "at": 7.0}]}}
    planE = audio.build(vpath, tmp / "buildE", storyboard=sb, words=words)
    rE = planE.get("report", {})
    pop = next((e for e in rE.get("sfx", []) if e["name"] == "pop"), {})
    s.check("audio on via the storyboard: music and effects stems are written and the mix is the track",
            planE["enabled"] and Path(planE["track"]).exists()
            and (tmp / "buildE" / "stems" / "music.wav").exists() and (tmp / "buildE" / "stems" / "sfx.wav").exists(),
            json.dumps(planE.get("switch")))
    s.check("build: an effect keyed to a word lands on that word's measured start, and a bar line lands on the key word",
            abs(pop.get("at", -1) - 1.8) < 1e-9 and any(abs(d - 5.837) < 1e-6 for d in rE.get("music", {}).get("downbeats", [])),
            json.dumps({"pop": pop, "downbeats": rE.get("music", {}).get("downbeats")}))
    try:
        audio.resolve_time({"line": "the-answer", "word": "database"}, words)
        raised = False
    except ValueError:
        raised = True
    s.check("build: an effect keyed to a word that is not in the line RAISES (no silent misplacement)", raised)

    # ================================================================== E3 hit treatment
    key = next(w for w in words["lines"][1]["words"] if w["w"].startswith("cache"))
    kt = key["start"]
    tl = hits.hit_timeline(kt, FPS)
    states = hits.timeline_frames(tl, 0, int(8 * FPS))
    first_dark = next(st["frame"] for st in states if st["dark"] > 0)
    release = next(st["frame"] for st in states if st["phase"] == "burst")
    s.check("hit: pre-darken starts 0.75 s +- 2 frames before the key word (first darkened frame)",
            abs(first_dark - (kt - 0.75) * FPS) <= 2 + 1e-9, f"first dark frame {first_dark} vs {(kt - 0.75) * FPS:.2f}")
    s.check("hit: the release lands on the key word +- 2 frames (first burst frame; darkness lifting, glow at peak)",
            abs(release - kt * FPS) <= 2 and states[release]["glow"] == 1.0 and states[release]["scale"] > 1.1,
            f"release {release} vs {kt * FPS:.2f}: {states[release]}")
    held = [st for st in states if st["frozen"]]
    s.check("hit: the last 3 frames before the release are held on one scene frame at full darkness",
            len(held) == 3 and len({h["scene_frame"] for h in held}) == 1 and held[-1]["frame"] == release - 1
            and all(h["dark"] == tl["opts"]["dark_alpha"] for h in held), json.dumps(held))
    node = shutil.which("node")
    if node:
        js = ROOT / "scripts" / "render" / "hits.js"
        prog = ("const H=require(%s);const W=JSON.parse(require('fs').readFileSync(%s,'utf8'));"
                "const tl=H.hitAtWord('the-answer','cache',{fps:30,words:W});const st=[];"
                "for(let f=0;f<=240;f++)st.push(H.hitState(tl,f));let thrown='';"
                "try{H.hitAtWord('the-answer','database',{fps:30,words:W});}catch(e){thrown=e.message;}"
                "const g=H.beatGrid(1.3,12.84,{approx_bar_s:2.3});"
                "console.log(JSON.stringify({tl,st,thrown,beats:[0,5,17,30].map(k=>H.gridBeat(g,k))}));") % (
            json.dumps(str(js)), json.dumps(str(FIX / "hit_words.json")))
        r = subprocess.run([node, "-e", prog], capture_output=True, text=True, timeout=60)
        try:
            J = json.loads(r.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            J = {}
        same = bool(J) and all(
            J["tl"][k] == tl[k] for k in ("dark_start_frame", "hold_start_frame", "release_frame", "burst_end_frame")) and all(
            abs(J["st"][f][k] - states[f][k]) < 1e-5 for f in range(241) for k in ("dark", "glow", "scale", "ring", "burst"))
        s.check("hit: hits.js (resolving the key word through the runtime's atWord) gives the same frames and states "
                "as hits.py on every frame", same, (r.stderr or "")[-300:])
        s.check("hit: hits.js hitAtWord THROWS when the key word is not in the line",
                "NOT in that line" in J.get("thrown", ""), J.get("thrown", "")[:200])
        gp = hits.beat_grid(1.3, 12.84, approx_bar_s=2.3)
        s.check("beat grid: hits.js predicts the same beats as hits.py",
                bool(J) and all(abs(a - hits.grid_beat(gp, k)) < 1e-9 for a, k in zip(J["beats"], (0, 5, 17, 30))),
                str(J.get("beats")))
    else:
        for nm in ("hit: hits.js cross-check SKIPPED — node not found (recorded)",
                   "hit: hits.js throw check SKIPPED — node not found (recorded)",
                   "beat grid: hits.js cross-check SKIPPED — node not found (recorded)"):
            s.check(nm, True)

    # ================================================================== E3 beat grid from measured downbeats
    gm, gmeta = audio.render_music(18.0, [{"at": 1.3, "style": "light"}, {"at": 5.9, "style": "groove"},
                                          {"at": 10.5, "style": "break"}, {"at": 12.8, "style": "groove"}],
                                   seed=11, bpm=104, align_downbeat_to=1.3)
    md = hits.measure_downbeats(gm, SR, approx_bar_s=gmeta["bar_s"])
    G = md["grid"]
    true_beats = [1.3 + k * gmeta["beat_s"] for k in range(80) if 1.3 + k * gmeta["beat_s"] < 18.0]
    later = [(k, b) for k, b in enumerate(true_beats) if b > md["d1_s"]]
    errs = [abs(hits.grid_beat(G, k) - b) * FPS for k, b in later]
    s.check("beat grid: two downbeats MEASURED from the audio predict every later beat within +-2 frames",
            later and max(errs) <= 2.0 and abs(md["d0_s"] - 1.3) * FPS <= 2,
            f"d0 {md['d0_s']} d1 {md['d1_s']} n={G['bars_between']} max err {max(errs) if errs else None:.2f} frames")
    wrong = hits.beat_grid(md["d0_s"], md["d1_s"], bars_between=G["bars_between"] + 1)
    werr = max(abs(hits.grid_beat(wrong, k) - b) * FPS for k, b in later)
    s.check("beat grid: negative — a grid with the wrong whole number of bars misses later beats by > 2 frames",
            werr > 2.0, f"{werr:.1f} frames")
    near = gmeta["downbeats"][5] + 0.11
    tlg = hits.hit_timeline(near, FPS, release="downbeat", grid=G)
    tlf = hits.hit_timeline(near + 1.0, FPS, release="downbeat", grid=G)
    s.check("hit: release='downbeat' snaps the release onto the measured bar line (+-2 frames of the true one) and "
            "keeps the word when no bar line is near",
            tlg["release_source"] == "downbeat" and abs(tlg["release_frame"] - gmeta["downbeats"][5] * FPS) <= 2
            and tlf["release_source"].startswith("word"), json.dumps([tlg["release_source"], tlf["release_source"]]))

    # ================================================================== E5 / V4 audio QA gate
    planA = {"report": rep}
    clean = check_audio.run_checks(tmp / "mixA" / "mix.wav", planA,
                                   lines=[{"display_text": "Follow the SOP first.", "spoken_text": "Follow the S O P first.",
                                           "start": a, "end": b} for a, b in layout][:1] +
                                         [{"display_text": "x", "spoken_text": "x", "start": a, "end": b} for a, b in layout[1:]],
                                   stems_dir=tmp / "mixA" / "stems", glossary=["SOP"])
    cs = {k: v["status"] for k, v in clean["checks"].items()}
    s.check("check_audio: a clean mixed fixture passes loudness, true peak, clipping, effects, gaps and spoken forms",
            clean["status"] == "PASS" and all(v == "pass" for k, v in cs.items() if k != "voice_stats"), json.dumps(cs))
    ev_inv = [e for e in clean["checks"]["sfx_audible"].get("events", []) if str(e.get("name", "")).startswith("invented:")]
    s.check("freedom: an invented effect placed in the mix is judged only on its outcome (audible over the bed) and passes",
            len(ev_inv) == 1 and ev_inv[0]["margin_db"] >= 0 and clean["checks"]["sfx_audible"]["status"] == "pass",
            json.dumps(ev_inv))
    clip = audio.normalize_peak(speechlike([(0.3, 2.5)]), 1.0) * 3.0
    clip = B.maximum(B.minimum(clip, 1.0), -1.0)
    audio.write_wav(tmp / "clipped.wav", clip, 2)
    rc = check_audio.run_checks(tmp / "clipped.wav")
    s.check("check_audio: flags a clipped fixture", rc["checks"]["clipping"]["status"] == "review",
            json.dumps(rc["checks"]["clipping"]))
    gap = speechlike([(0.3, 1.6), (3.1, 4.4)])          # 1.5 s silence inside one line
    audio.write_wav(tmp / "gap.wav", gap)
    rg = check_audio.run_checks(tmp / "mixA" / "mix.wav", lines=[{"display_text": "one line", "out_wav": str(tmp / "gap.wav")}])
    lg = rg["checks"]["gaps"]["long_internal_gaps"]
    s.check("check_audio: flags a 1.5 s silence inside a line",
            rg["checks"]["gaps"]["status"] == "review" and lg and 1.35 <= lg[0]["seconds"] <= 1.65, json.dumps(lg))
    ri = check_audio.run_checks(tmp / "mixA" / "mix.wav", lines=[{"display_text": "one line", "out_wav": str(tmp / "gap.wav"),
                                                                   "pauses": [{"start": 1.6, "end": 3.1, "ms": 1500}]}])
    short = speechlike([(0.3, 1.6), (2.1, 3.4)])        # a normal 0.5 s pause
    audio.write_wav(tmp / "short.wav", short)
    rs = check_audio.run_checks(tmp / "mixA" / "mix.wav", lines=[{"display_text": "one line", "out_wav": str(tmp / "short.wav")}])
    s.check("check_audio: a deliberately marked 1.5 s pause is excused, and a 0.5 s pause passes",
            ri["checks"]["gaps"]["status"] == "pass" and rs["checks"]["gaps"]["status"] == "pass",
            json.dumps([ri["checks"]["gaps"], rs["checks"]["gaps"]["long_internal_gaps"]])[:300])
    flat = speechlike([(0.3, 3.3)], swing=0.0)
    lively = speechlike([(0.3, 1.5), (1.9, 3.3)], swing=0.3)
    vf, vl = check_audio.voice_stats(B.tolist(flat), SR), check_audio.voice_stats(B.tolist(lively), SR)
    if vf.get("reason") == "numpy unavailable":
        s.check("check_audio: delivery stats SKIPPED — numpy unavailable for the pitch track (recorded)", True)
    else:
        s.check("check_audio: delivery stats (pitch range in semitones, pauses) separate a monotone read from an "
                "inflected one with a pause (informational, never pass/fail)",
                vf.get("status") == "info" and vl["pitch_range_semitones"] > (vf["pitch_range_semitones"] or 0) + 3
                and vl["pauses"] > vf["pauses"], json.dumps([vf, vl]))
    buried = [{"name": "tick", "at": 1.0, "gain_db": -55}, {"name": "impact", "at": 7.1}]
    repX = audio.mix(voice, tmp / "mixX", music=mus, music_meta=mmeta, sfx_events=buried)
    rx = check_audio.run_checks(tmp / "mixX" / "mix.wav", {"report": repX}, stems_dir=tmp / "mixX" / "stems")
    sa = rx["checks"]["sfx_audible"]
    s.check("check_audio: flags an inaudible effect buried under the bed (and passes the audible one)",
            sa["status"] == "review" and sa["inaudible"] == ["tick"], json.dumps(sa)[:400])
    audio.write_wav(tmp / "quiet.wav", voice * 0.05, 2)
    hotw = audio.normalize_peak(speechlike([(0.3, 3.0)]), 0.999)
    audio.write_wav(tmp / "hot.wav", hotw, 2)
    rq, rh = check_audio.run_checks(tmp / "quiet.wav"), check_audio.run_checks(tmp / "hot.wav")
    s.check("check_audio: flags a mix far off the loudness target and one whose true peak is over the ceiling",
            rq["checks"]["loudness"]["status"] == "review" and rh["checks"]["true_peak"]["status"] == "review",
            json.dumps([rq["checks"]["loudness"], rh["checks"]["true_peak"]]))
    rgl = check_audio.run_checks(tmp / "mixA" / "mix.wav", lines=[
        {"display_text": "Follow the SOP and call the API.", "spoken_text": "Follow the SOP and call the A P I.",
         "out_wav": str(tmp / "short.wav"), "word_timings": [{"word": "API.", "start": 2.2, "end": 2.6}]}],
        glossary=["SOP", "API", "JSON"])
    sf = rgl["checks"]["spoken_form"]
    s.check("check_audio: the glossary spot check flags a term handed to the voice as a bare word (SOP), lists a "
            "spelled one with its time for listening (API), and leaves a word-acronym alone (JSON)",
            sf["status"] == "review" and [f["term"] for f in sf["flags"]] == ["SOP"]
            and [x["term"] for x in sf["listening_list"]] == ["API"] and sf["listening_list"][0]["at_s"] == 2.2,
            json.dumps(sf)[:400])
    gate = "scripts/gates/check_audio.py"
    rcli = run(gate, [tmp / "clipped.wav"], env=env, timeout=180)
    rok = run(gate, [tmp / "mixA" / "mix.wav"], env=env, timeout=180)
    rmiss = run(gate, [tmp / "nope.wav"], env=env, timeout=60)
    s.check("check_audio CLI: exit 1 on review (clipped), 0 on a clean mix, 2 when the mix is missing",
            rcli.returncode == 1 and rok.returncode == 0 and rmiss.returncode == 2,
            f"{rcli.returncode} {rok.returncode} {rmiss.returncode} {rok.stderr[-200:]}")

    # ================================================================== pure-Python backend
    prog = ("import sys,json,hashlib;sys.path.insert(0,%r);import audio;"
            "v,m=audio.render_music(3.0,[[0,'groove']],seed=9,bpm=120);a=audio.quantize16(v);"
            "w,_=audio.render_music(3.0,[[0,'groove']],seed=9,bpm=120);"
            "e,_=audio.render_sfx('chime');audio.write_wav(%r,v);"
            "print(json.dumps({'b':audio.B.name,'same':a==audio.quantize16(w),'n':len(v),'sfx':len(e)}))") % (
        str(ROOT / "scripts" / "render"), str(tmp / "pure.wav"))
    rp = subprocess.run([sys.executable, "-c", prog], capture_output=True, text=True, timeout=170,
                        env=dict(os.environ, COORDN8_AUDIO_PURE="1", **env))
    try:
        P = json.loads(rp.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        P = {}
    vn, _ = audio.render_music(3.0, [[0, "groove"]], seed=9, bpm=120)
    close = bool(P) and (tmp / "pure.wav").exists() and audio.profile_distance(
        audio.spectral_profile(audio.read_audio(tmp / "pure.wav")), audio.spectral_profile(vn)) < 3.0
    s.check("pure-Python backend: music and effects render without numpy, deterministically, and sound like the "
            "numpy render (profile distance < 3 dB)",
            P.get("b") == "pure" and P.get("same") and P.get("n") == len(vn) and close,
            (rp.stderr or "")[-300:] + json.dumps({k: P.get(k) for k in ("b", "same", "n")}))

    # ================================================================== V3 markup -> engine parameters
    import engines  # noqa: E402
    import render as vrender  # noqa: E402
    calls = []
    orig_run = engines._run

    def fake_run(cmd, *, input_text=None, timeout=300, env=None):
        if cmd and cmd[0] == "say":
            txt = Path(cmd[cmd.index("-f") + 1]).read_text(encoding="utf-8")
            rate = int(cmd[cmd.index("-r") + 1])
            out = cmd[cmd.index("-o") + 1]
            nw = len(txt.split())
            secs_ = max(0.2, nw * 60.0 / rate)
            tone = B.sin(audio.taxis(audio.secs(secs_)) * (2 * math.pi * 220)) * 0.3
            audio.write_wav(out, tone)
            calls.append({"text": txt, "rate": rate})
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return orig_run(cmd, input_text=input_text, timeout=timeout, env=env)
    engines._run = fake_run
    try:
        plan = vrender._plan_line({"text": "The {slow}*hard* idea{/slow} [pause 300] then {fast}the easy glue{/fast} "
                                           "[beat] done."}, ssml=False, spell=None, word=None)
        outw = tmp / "v3.wav"
        resv = vrender._render_with("say", "Samantha", [plan], 1.0, [outw], 0, tmp / "v3work")[0]
    finally:
        engines._run = orig_run
    base = float(engines.engine_spec("say").get("base_wpm", 180))
    rates = [c["rate"] for c in calls]
    want = [int(round(base * x)) for x in (1.0, 0.85, 1.0, 1.1, 1.0)]
    gaps_v3 = check_audio.silence_gaps(B.tolist(audio.read_audio(outw)), SR)
    s.check("V3 markup -> engine parameters: {slow}/{fast} become the engine's speaking rate (x0.85 / x1.1), "
            "*word* becomes the engine's emphasis command on that word, [pause 300] and [beat] become 300 ms of "
            "inserted silence",
            rates == want and calls[1]["text"].startswith("[[emph +]]hard") and "[[emph" not in calls[0]["text"]
            and [p["ms"] for p in resv["pauses"]] == [300, 300]
            and sum(1 for g in gaps_v3 if 0.26 <= g["seconds"] <= 0.36) == 2,
            json.dumps({"rates": rates, "want": want, "calls": calls[:2], "pauses": resv["pauses"], "gaps": gaps_v3}))

    # ================================================================== engine word timing (W3 proposal 1-2)
    E = json.loads((FIX / "engine_word_timing.json").read_text(encoding="utf-8"))["items"]

    def spans(it, fixed: bool):
        wp = it["isolated_word_phonemes"]
        if fixed:
            wp = engines.in_context_word_phonemes(wp, it["groups"])
        gt = [[tuple(p) for p in g] for g in it["group_timings"]]
        b = engines.align_words(wp, it["groups"])
        sp = engines.word_spans_from(b, wp, gt)
        if fixed:
            sp = engines.refine_spans_with_silence(sp, [tuple(r) for r in it["measured_silence_runs"]])
        return sp
    art_old, art_new = [], []
    for it in E:
        ws = it["text"].split()
        so, sn = spans(it, False), spans(it, True)
        for i, w in enumerate(ws):
            if w.strip(",.;:").lower() == "a":
                art_old.append(so[i][1] - so[i][0])
                art_new.append(sn[i][1] - sn[i][0])
    s.check("voice timing: in-context phonemes keep the article 'A' short (neural-engine fixture: it was timed "
            "> 0.35 s, now every 'a' < 0.2 s)",
            max(art_old) > 0.35 and max(art_new) < 0.2, f"old {[round(x, 2) for x in art_old]} new {[round(x, 2) for x in art_new]}")

    def over_silence(it, sp):
        return [(w, a, b) for (a, b) in it["measured_silence_runs"] if b - a > 0.15
                for w, x in zip(it["text"].split(), sp) if x[0] < a + 0.03 and x[1] > b - 0.03]
    ov_old = sum(len(over_silence(it, spans(it, False))) for it in E)
    ov_new = sum(len(over_silence(it, spans(it, True))) for it in E)
    s.check("voice timing: after refinement no word span runs through a measured pause (fixture: before > 0)",
            ov_old > 0 and ov_new == 0, f"before {ov_old} after {ov_new}")
    r1 = engines.refine_spans_with_silence([[0.0, 0.5], [0.6, 1.4], [1.5, 2.0]], [(0.7, 1.2)])
    s.check("voice timing: a word start inside a silence moves to where the audio resumes; a span over a pause keeps "
            "its longer voiced side",
            r1 == [[0.0, 0.5], [1.2, 1.4], [1.5, 2.0]]
            and engines.refine_spans_with_silence([[0.1, 1.0]], [(0.5, 0.8)]) == [[0.1, 0.5]], json.dumps(r1))
    s.check("voice timing: the merged-function-word alignment is unchanged (in-context swap only when every group "
            "is one word)",
            engines.in_context_word_phonemes(["ðˈæt", "ðə", "vˈɔɪs"], ["ðætðə", "vˈɔɪs"]) == ["ðˈæt", "ðə", "vˈɔɪs"])

    # ================================================================== hygiene
    srcs = [ROOT / "scripts" / "render" / f for f in ("audio.py", "hits.py", "hits.js")] + [ROOT / "scripts" / "gates" / "check_audio.py"]
    net = [str(p.name) for p in srcs if re.search(r"urllib|http[s]?://|requests\.|socket\.|fetch\(", p.read_text(encoding="utf-8"))]
    s.check("nothing is downloaded: the audio, hit and audio-QA code has no network calls (music and effects are synthesized)",
            not net, str(net))
    tmp_audio = [str(p) for p in tmp.rglob("*.wav")]

leftover = [p for p in (ROOT / "scripts").rglob("*.wav")] + [p for p in (ROOT / "tests").rglob("*.wav")]
s.check("no temp audio left: the suite's temp dir (all its WAVs) is gone and no WAV sits in the package tree",
        not Path(td).exists() and not leftover and len(tmp_audio) > 5, f"{len(tmp_audio)} temp WAVs; left {leftover[:3]}")
s.check("suite runs inside the 3-minute budget", time.time() - T0 < 180, f"{time.time() - T0:.0f}s")
sys.exit(s.finish())
