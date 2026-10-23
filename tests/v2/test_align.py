#!/usr/bin/env python3
"""E2 (alignment half) — word-level timing from local signals, plus the gate.

Hermetic: temp dirs only; the neural voice is installed into a SANDBOX user home
from the shared cache (hard links, offline venv) and skipped with a recorded
reason when the cache is absent; macOS `say` drives the measured-silence
fixture. Nothing is sent anywhere — a network-blocking shim runs the aligner.

Measured numbers are printed on a final `MEASUREMENTS {json}` line (and written
to $C8_ALIGN_MEASURE_OUT when set).
"""
from __future__ import annotations

import array
import json
import math
import os
import random
import shutil
import statistics
import subprocess
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import ROOT, Suite, run, sandbox_env, tmpdir  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts" / "render"))
import align  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "align"
SENT = json.loads((FIX / "sentences.json").read_text(encoding="utf-8"))
CACHE = Path(os.environ.get("C8_VOICE_CACHE",
                            str(ROOT.parent / "Coordn8_v2_handoff" / "_working" / "cache" / "voices")))
REAL_UV = shutil.which("uv") or str(Path("~/.local/bin/uv").expanduser())
s = Suite("align")
M = {}


def real_python311():
    """Located BEFORE the sandbox swaps HOME (uv keeps its interpreters under the real home)."""
    if not Path(REAL_UV).exists():
        return None
    for req in ("3.11", ">=3.10,<3.14"):
        r = subprocess.run([REAL_UV, "python", "find", req], capture_output=True, text=True, timeout=30)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip().splitlines()[-1]
    return None


PY311 = real_python311()


def gate(args, env=None):
    r = run("scripts/gates/check_word_timing.py", args, env=env, timeout=120)
    try:
        return r.returncode, json.loads(r.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return r.returncode, {"status": "?", "findings": [r.stderr[-300:]]}


def stats(errs):
    e = sorted(errs)
    if not e:
        return {}
    return {"n": len(e), "mean_ms": round(statistics.mean(e), 1), "median_ms": round(statistics.median(e), 1),
            "p90_ms": round(e[min(len(e) - 1, int(0.9 * len(e)))], 1), "max_ms": round(e[-1], 1),
            "within_80ms": round(sum(1 for x in e if x <= 80) / len(e), 3)}


def bursts(path, durs, gaps, sr=24000, seed=7):
    """Harmonic voiced-like bursts with known starts (the true word onsets)."""
    rnd = random.Random(seed)
    a = array.array("h", [0] * int(0.05 * sr))
    starts = []
    for k, (d, g) in enumerate(zip(durs, gaps)):
        starts.append(len(a) / sr)
        f0 = 140 + 20 * (k % 3)
        nsyl = max(1, round(d / 0.18))
        for i in range(int(d * sr)):
            t = i / sr
            env = min(1.0, t / 0.012) * min(1.0, (d - t) / 0.03)
            env *= 0.55 + 0.45 * math.cos(2 * math.pi * t / max(0.12, d / nsyl))
            v = env * sum(math.sin(2 * math.pi * f0 * h * t) / h for h in (1, 2, 3, 4))
            a.append(int(9000 * v + rnd.gauss(0, 30)))
        a.extend(int(rnd.gauss(0, 30)) for _ in range(int(g * sr)))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(a.tobytes())
    return starts


def silence_starts(path, thr=32, min_gap=0.15):
    """TRUE word starts of a `[[slnc N]]` render: first sample above the noise
    floor, then every sample that ends a >= min_gap run of near-silence."""
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        a = array.array("h")
        a.frombytes(w.readframes(w.getnframes()))
    i, n = 0, len(a)
    while i < n and abs(a[i]) <= thr:
        i += 1
    out, quiet = [i / sr], 0
    for j in range(i, n):
        if abs(a[j]) <= thr:
            quiet += 1
        else:
            if quiet >= int(min_gap * sr):
                out.append(j / sr)
            quiet = 0
    return out


with tmpdir() as td:
    tmp = Path(td)
    env = sandbox_env(tmp)

    # ============================================================ consumer (atWord mirror) + format
    sample = json.loads((FIX / "words_sample.json").read_text(encoding="utf-8"))
    W = align.Words(sample)
    s.check("consumer: at_word returns the timed start of a word in its line (case and edge punctuation ignored)",
            W.at_word("intro", "SOP") == 1.02 and W.at_word("intro", "merge.") == 2.12
            and W.at_word("checks", "SECONDS") == 4.52, "")
    s.check("consumer: at_line returns the line start; at_word_frame converts with the file's fps",
            W.at_line("checks") == 3.0 and W.at_word_frame("intro", "SOP") == round(1.02 * 30), "")
    s.check("consumer: a phrase returns its first word; the n-th occurrence is addressable",
            W.at_word("checks", "two seconds") == 4.3 and W.at_word("checks", "checks", 1) == 5.52, "")
    cut = json.loads(json.dumps(sample))
    cut["lines"][0]["words"] = [w for w in cut["lines"][0]["words"] if w["w"] != "SOP"]
    try:
        align.Words(cut).at_word("intro", "SOP")
        threw = False
    except align.WordTimingError:
        threw = True
    s.check("consumer: removing a word from a line in words.json makes the lookup of that word THROW", threw, "")
    errs = []
    for call in (lambda: W.at_line("nope"), lambda: W.at_word("intro", "checks"),
                 lambda: W.at_word("checks", "checks", 2), lambda: W.at_word("intro", "the SOP merge")):
        try:
            call()
            errs.append(False)
        except align.WordTimingError:
            errs.append(True)
    s.check("consumer: an unknown line, a word from another line, a missing occurrence and a broken phrase all THROW",
            all(errs), str(errs))

    # ============================================================ text handling
    s.check("markup stripped: V3 pauses/speed/emphasis, colour-role marks and engine commands leave clean words",
            align.display_words("First [pause 300] {slow}the *checker*{/slow} ⟦key⟧decides⟦/key⟧ "
                                "⟦bad:fast⟧ [[slnc 200]] — done.")
            == ["First", "the", "checker", "decides", "fast", "done"],
            str(align.display_words("First [pause 300] {slow}the *checker*{/slow} ⟦key⟧decides⟦/key⟧ ⟦bad:fast⟧ done.")))
    syl = {w: align.syllables(w) for w in ("the", "parallel", "request", "checked", "makes", "S", "W", "42", "seconds")}
    s.check("syllable heuristic (vowel groups): the 1, parallel 3, request 2, checked 1, makes 1, S 1, W 3, 42 3, seconds 2",
            syl == {"the": 1, "parallel": 3, "request": 2, "checked": 1, "makes": 1, "S": 1, "W": 3, "42": 3,
                    "seconds": 2}, str(syl))

    # ============================================================ engine source (synthetic timings)
    disp = align.display_words("Follow the SOP; it has 42 steps.")
    spoken = [{"word": w, "start": a, "end": b} for w, a, b in (
        ("Follow", 0.10, 0.40), ("the", 0.42, 0.52), ("S", 0.55, 0.70), ("O", 0.72, 0.86), ("P", 0.88, 1.10),
        ("it", 1.30, 1.38), ("has", 1.40, 1.55), ("forty", 1.58, 1.85), ("two", 1.87, 2.05), ("steps.", 2.08, 2.50))]
    sp = align.engine_spans(disp, spoken)
    s.check("engine spoken-form tokens 'S','O','P' map to the ONE display word 'SOP' (start of S, end of P)",
            sp is not None and len(sp) == len(disp) and disp[2] == "SOP" and sp[2] == (0.55, 1.10), str(sp))
    s.check("engine spoken-form 'forty','two' map to the one display word '42'",
            sp is not None and disp[5] == "42" and sp[5] == (1.58, 2.05), str(sp))
    rendered = [{"word": "Follow", "spoken": "Follow", "start": 0.1, "end": 0.4},
                {"word": "the", "spoken": "the", "start": 0.42, "end": 0.52},
                {"word": "SOP;", "spoken": "S O P;", "start": 0.55, "end": 1.1}]
    sp2 = align.engine_spans(["Follow", "the", "SOP"], rendered)
    s.check("engine timings already keyed by display word (render.py exact map) are used as they are",
            sp2 == [(0.1, 0.4), (0.42, 0.52), (0.55, 1.1)], str(sp2))

    # audio for the CLI tests: two synthetic lines
    aud = tmp / "audio"
    aud.mkdir()
    text = SENT["synthetic"]
    sw = align.display_words(text)
    wts = align.word_weights(sw, [[w] for w in sw])
    st_sep = bursts(aud / "sep.wav", wts, [0.16] * len(wts))
    st_cont = bursts(aud / "cont.wav", wts, [0.035] * len(wts), seed=1)
    with wave.open(str(aud / "sep.wav")) as w:
        dur_sep = w.getnframes() / w.getframerate()
    with wave.open(str(aud / "cont.wav")) as w:
        dur_cont = w.getnframes() / w.getframerate()
    eng_t = [{"word": w, "start": round(a, 3), "end": round(a + d, 3)} for w, a, d in zip(sw, st_sep, wts)]
    bm = {"fps": 30, "sentences": [
        {"idx": 1, "name": "sep", "text": text, "start": 1.0, "end": round(1.0 + dur_sep, 3), "word_timings": eng_t},
        {"idx": 2, "name": "cont", "text": text, "start": round(1.4 + dur_sep, 3),
         "end": round(1.4 + dur_sep + dur_cont, 3)}]}
    (tmp / "bm.json").write_text(json.dumps(bm), encoding="utf-8")

    # network-blocking shim: any socket use in the aligner process raises
    shim = tmp / "shim"
    shim.mkdir()
    (shim / "sitecustomize.py").write_text(
        "import socket\n"
        "def _blocked(*a, **k):\n    raise OSError('network blocked by test_align')\n"
        "socket.socket = _blocked\nsocket.create_connection = _blocked\nsocket.getaddrinfo = _blocked\n",
        encoding="utf-8")
    nenv = dict(env, PYTHONPATH=str(shim))
    probe = subprocess.run([sys.executable, "-c", "import socket\ntry:\n socket.socket()\n print('open')\n"
                            "except OSError:\n print('blocked')"], env={**os.environ, **nenv},
                           capture_output=True, text=True, timeout=30)
    r = run("scripts/render/align.py", [tmp / "bm.json", "--audio-dir", aud, "--out", tmp / "words.json", "--js",
                                        "--report", tmp / "rep.json"], env=nenv, timeout=300)
    try:
        WJ = json.loads((tmp / "words.json").read_text(encoding="utf-8"))
        rep = json.loads((tmp / "rep.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        WJ, rep = {"lines": []}, {"lines": []}
    s.check("nothing is sent anywhere: the aligner runs to completion with every socket call blocked",
            probe.stdout.strip() == "blocked" and r.returncode == 0 and len(WJ["lines"]) == 2,
            probe.stdout + r.stderr[-300:])
    srcs = ["scripts/render/align.py", "scripts/gates/check_word_timing.py"]
    import ast
    bad = []
    for f in srcs:
        tree = ast.parse((ROOT / f).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            bad += [f"{f}:{n}" for n in names if n.split(".")[0] in
                    {"socket", "urllib", "http", "requests", "ssl", "ftplib", "smtplib", "xmlrpc", "asyncio",
                     "subprocess", "httpx", "aiohttp", "websocket"}]
    s.check("nothing is sent anywhere: align.py and check_word_timing.py import no network or process modules",
            not bad, str(bad))

    keys_ok = (set(WJ) == {"lines", "source", "fps"} and WJ.get("fps") == 30 and all(
        set(ln) == {"name", "text", "start", "end", "words"} and all(set(w) == {"w", "start", "end", "i"}
                                                                     for w in ln["words"]) for ln in WJ["lines"]))
    s.check("words.json loads in the exact format {lines:[{name,text,start,end,words:[{w,start,end,i}]}],source,fps}",
            keys_ok, json.dumps(WJ)[:300])
    js = (tmp / "words.js").read_text(encoding="utf-8") if (tmp / "words.js").exists() else ""
    s.check("--js emits `window.__C8_WORDS__ = {...};` carrying the same data (and the consumer loads it)",
            js.startswith("window.__C8_WORDS__ = ") and js.rstrip().endswith(";")
            and align.Words.load(tmp / "words.js").data == WJ, js[:120])
    l_sep = next((ln for ln in WJ["lines"] if ln["name"] == "sep"), {"words": []})
    l_cont = next((ln for ln in WJ["lines"] if ln["name"] == "cont"), {"words": []})
    s.check("engine timings present -> used for that line, offset by the line's beat-map start",
            [w["start"] for w in l_sep["words"]] == [round(1.0 + a, 3) for a in st_sep]
            and rep["lines"][0]["source"] == "engine", str(l_sep["words"][:3]))
    s.check("a line without engine timings falls back to the onset aligner; the file's source is 'mixed'",
            rep["lines"][1]["source"] == "onset" and WJ.get("source") == "mixed", str(rep["lines"])[:300])

    def contract(ln):
        ws = ln["words"]
        return ([w["w"] for w in ws] == align.display_words(ln["text"]) and all(
            ws[k]["start"] <= ws[k + 1]["start"] for k in range(len(ws) - 1))
            and all(ln["start"] <= w["start"] <= w["end"] <= ln["end"] for w in ws)
            and all(w["end"] - w["start"] <= 2.0 for w in ws) and [w["i"] for w in ws] == list(range(len(ws))))
    s.check("fallback (numpy): a timing for every display word, monotonic, inside the line, none over 2 s",
            align.np is not None and contract(l_cont), str(l_cont["words"][:4]))
    e_cont = [abs(w["start"] - (bm["sentences"][1]["start"] + t)) * 1000 for w, t in zip(l_cont["words"], st_cont)]
    s.check("fallback on known-onset bursts (continuous, 35 ms dips): every start within ±80 ms of the truth",
            e_cont and max(e_cont) <= 80, str(stats(e_cont)))
    M["synthetic_bursts_continuous_numpy"] = stats(e_cont)

    rp = run("scripts/render/align.py", [tmp / "bm.json", "--audio-dir", aud, "--out", tmp / "words_py.json",
                                         "--prefer", "onset", "--report", tmp / "rep_py.json"],
             env=dict(nenv, C8_ALIGN_NO_NUMPY="1"), timeout=300)
    try:
        WP = json.loads((tmp / "words_py.json").read_text(encoding="utf-8"))
        rpp = json.loads((tmp / "rep_py.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        WP, rpp = {"lines": []}, {"numpy": True, "lines": []}
    s.check("--prefer onset ignores engine timings: every line from the fallback, source 'onset'",
            WP.get("source") == "onset" and all(x["source"] == "onset" for x in rpp["lines"]), rp.stderr[-200:])
    s.check("fallback (pure Python, numpy disabled): the same contract holds on every line",
            rpp.get("numpy") is False and len(WP["lines"]) == 2 and all(contract(ln) for ln in WP["lines"]),
            str(rpp)[:300])
    lp_sep = WP["lines"][0]["words"] if WP["lines"] else []
    e_sep = [abs(w["start"] - (1.0 + t)) * 1000 for w, t in zip(lp_sep, st_sep)]
    gaps = [(1.0 + st_sep[k] + wts[k] + 0.03, 1.0 + st_sep[k + 1] - 0.03) for k in range(len(wts) - 1)]
    s.check("silence-aware (pure Python): no word starts inside a long silence; separated starts within ±80 ms",
            e_sep and max(e_sep) <= 80 and not any(g0 < w["start"] < g1 for w in lp_sep for g0, g1 in gaps),
            str(stats(e_sep)))
    M["synthetic_bursts_separated_pure_python"] = stats(e_sep)

    # joint solver uses the onsets: jittered durations, vs plain proportional spreading
    rj = random.Random(3)
    ej, ep, eg = [], [], []
    for trial in range(3):
        durs = [w * rj.uniform(0.6, 1.5) for w in wts]
        st = bursts(tmp / "j.wav", durs, [0.035] * len(wts), seed=trial)
        ln = align.align_line({"text": text, "start": 0, "end": 99}, 0, wav=tmp / "j.wav")
        lg = align.align_line({"text": text, "start": 0, "end": 99}, 0, wav=tmp / "j.wav", method="greedy")
        r0, r1 = ln["_diag"]["regions"][0]
        ej += [abs(w["start"] - t) * 1000 for w, t in zip(ln["words"], st)]
        eg += [abs(w["start"] - t) * 1000 for w, t in zip(lg["words"], st)]
        ep += [abs(a - t) * 1000 for a, t in zip(align._spread(r0, r1, wts), st)]
    M["synthetic_jittered"] = {"joint": stats(ej), "greedy_nearest_onset": stats(eg), "proportional_only": stats(ep)}
    s.check("onsets are used: on jittered word lengths the joint solver beats syllable-proportional spreading "
            "and greedy snapping, mean error <= 40 ms",
            statistics.mean(ej) < statistics.mean(ep) and statistics.mean(ej) < statistics.mean(eg)
            and statistics.mean(ej) <= 40, json.dumps(M["synthetic_jittered"]))

    # engine timings vs the audio: no start inside a long silence
    fixed, n = align.silence_correct([(0.0, 0.4), (0.55, 0.9), (1.1, 1.5)], [(0.0, 0.5), (0.8, 1.6)])
    s.check("engine timing that starts inside a long silence is moved to the silence end (and counted)",
            fixed[1] == (0.8, 0.9) and fixed[0] == (0.0, 0.4) and n == 1, str(fixed))

    bm_bad = {"fps": 30, "sentences": [{"idx": 1, "name": "ghost", "text": "No audio here.", "start": 0, "end": 1}]}
    (tmp / "bm_bad.json").write_text(json.dumps(bm_bad), encoding="utf-8")
    rb = run("scripts/render/align.py", [tmp / "bm_bad.json", "--audio-dir", aud, "--out", tmp / "w_bad.json"],
             env=nenv, timeout=60)
    s.check("a line with neither engine timings nor audio fails loudly: exit 3 and the line is named",
            rb.returncode == 3 and "ghost" in rb.stderr, rb.stderr[-200:])

    # ============================================================ gate
    good = tmp / "good.json"
    good.write_text(json.dumps(sample), encoding="utf-8")
    rc, out = gate([good])
    s.check("gate: a complete, monotonic, in-window words.json PASSES", rc == 0 and out.get("status") == "PASS",
            str(out.get("findings")))
    rc, out = gate([tmp / "words.json", "--beatmap", tmp / "bm.json"])
    s.check("gate: the aligner's own output passes against its beat map (every display word timed)",
            rc == 0 and out.get("status") == "PASS", str(out.get("findings")))
    nm = json.loads(json.dumps(sample))
    nm["lines"][0]["words"][3]["start"] = 0.9
    (tmp / "nm.json").write_text(json.dumps(nm), encoding="utf-8")
    rc, out = gate([tmp / "nm.json"])
    s.check("gate catches a NON-MONOTONIC word timing (REVIEW, names the word)",
            rc == 1 and any("not monotonic" in f and "before" in f for f in out.get("findings", [])),
            str(out.get("findings")))
    (tmp / "cut.json").write_text(json.dumps(cut), encoding="utf-8")
    rc, out = gate([tmp / "cut.json"])
    s.check("gate catches a MISSING word (REVIEW, names it)",
            rc == 1 and any("missing ['SOP']" in f for f in out.get("findings", [])), str(out.get("findings")))
    ow = json.loads(json.dumps(sample))
    ow["lines"][1]["words"][10]["end"] = 6.9
    ow["lines"][1]["words"][6]["end"] = 7.2
    (tmp / "ow.json").write_text(json.dumps(ow), encoding="utf-8")
    rc, out = gate([tmp / "ow.json"])
    fl = out.get("findings", [])
    s.check("gate catches a word outside its line window and a word longer than 2 s",
            rc == 1 and any("outside the line window" in f for f in fl) and any("> 2s" in f for f in fl), str(fl))
    ref = json.loads(json.dumps(sample))
    for ln in ref["lines"]:
        for w in ln["words"]:
            w["start"] = round(w["start"] + 0.03, 3)
    (tmp / "ref.json").write_text(json.dumps(ref), encoding="utf-8")
    rc, out = gate([good, "--reference", tmp / "ref.json"])
    acc = out.get("accuracy") or {}
    s.check("gate accuracy vs a reference: reports mean/max ms and PASSES when every word is within ±80 ms",
            rc == 0 and acc.get("compared") == 17 and acc.get("max_ms") == 30.0 and acc.get("mean_ms") == 30.0,
            str(acc)[:200])
    ref["lines"][0]["words"][2]["start"] = 1.25
    (tmp / "ref2.json").write_text(json.dumps(ref), encoding="utf-8")
    rc, out = gate([good, "--reference", tmp / "ref2.json"])
    s.check("gate accuracy: one word 230 ms off fails the ±80 ms check (REVIEW) with the measured error",
            rc == 1 and (out.get("accuracy") or {}).get("max_ms") == 230.0
            and any("±80 ms" in f for f in out.get("findings", [])), str(out.get("findings")))

    # ============================================================ say + [[slnc]]: measured true word starts
    if sys.platform == "darwin" and shutil.which("say"):
        errs_say, okc = [], True
        for k, line in enumerate(SENT["say_slnc"]):
            dw = align.display_words(line)
            speech = " [[slnc 300]] ".join(" ".join(align._spoken_of_display(w)) for w in dw)
            wav = tmp / f"say{k}.wav"
            subprocess.run(["say", "-v", "Samantha", "-r", "180", "-o", str(wav), "--file-format=WAVE",
                            "--data-format=LEI16@24000", speech], capture_output=True, timeout=60)
            if not wav.exists():
                subprocess.run(["say", "-r", "180", "-o", str(wav), "--file-format=WAVE",
                                "--data-format=LEI16@24000", speech], capture_output=True, timeout=60)
            truth = silence_starts(wav) if wav.exists() else []
            if len(truth) != len(dw):
                okc = False
                continue
            ln = align.align_line({"text": line, "speech": speech, "start": 0, "end": 99}, k, wav=wav)
            errs_say += [abs(w["start"] - t) * 1000 for w, t in zip(ln["words"], truth)]
        M["say_slnc_measured_truth"] = stats(errs_say)
        s.check("say fixture ([[slnc 300]] between words, truth measured at sample level): every fallback word "
                "start within ±80 ms", okc and errs_say and max(errs_say) <= 80, json.dumps(M["say_slnc_measured_truth"]))
    else:
        s.check("say fixture: SKIPPED — macOS `say` not available here (recorded reason)", True)

    # ============================================================ neural voice: engine timings vs fallback
    os.environ.update(env)
    py311 = PY311
    os.environ.update({"COORDN8_VOICE_PYTHON": py311 or "", "COORDN8_UV": REAL_UV if Path(REAL_UV).exists() else "",
                       "UV_OFFLINE": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    have_cache = (CACHE / "kokoro").is_dir()
    espeak_before = set(Path("/tmp").glob("c8-espeak-*")) if Path("/tmp").is_dir() else set()
    k_ok = False
    if have_cache and py311:
        ri = run("scripts/voice/voices.py", ["install", "--engines", "kokoro", "--cache", str(CACHE), "--timeout", "20"],
                 env=dict(os.environ), timeout=900)
        sys.path.insert(0, str(ROOT / "scripts" / "voice"))
        try:
            import engines  # noqa: E402
            import render  # noqa: E402
            k_ok = "kokoro" in engines.available()
        except Exception as ex:  # noqa: BLE001
            k_ok = False
            ri = subprocess.CompletedProcess([], 1, "", str(ex))
    if k_ok:
        lines = [{"id": f"k{i}", "text": t} for i, t in enumerate(SENT["kokoro"])]
        try:
            R = render.synth_lines(lines, tmp / "kokoro", engine="kokoro", fallback=False)
        except Exception as ex:  # noqa: BLE001
            R = []
            s.check("kokoro render for the timing fixture", False, str(ex)[:300])
        if R:
            t0, sents, res = 0.3, [], []
            for i, r in enumerate(R):
                sents.append({"idx": i + 1, "name": f"k{i}", "text": SENT["kokoro"][i], "start": round(t0, 3),
                              "end": round(t0 + r["seconds"], 3)})
                res.append({"out_wav": r["out_wav"], "word_timings": r["word_timings"]})
                t0 += r["seconds"] + 0.3
            kb = {"fps": 30, "sentences": sents}
            (tmp / "kbm.json").write_text(json.dumps(kb), encoding="utf-8")
            (tmp / "kres.json").write_text(json.dumps(res), encoding="utf-8")
            re1 = run("scripts/render/align.py", [tmp / "kbm.json", "--audio-dir", tmp / "kokoro", "--results",
                                                  tmp / "kres.json", "--out", tmp / "k_engine.json"], env=nenv, timeout=300)
            re2 = run("scripts/render/align.py", [tmp / "kbm.json", "--audio-dir", tmp / "kokoro", "--prefer", "onset",
                                                  "--out", tmp / "k_onset.json"], env=nenv, timeout=300)
            try:
                KE = json.loads((tmp / "k_engine.json").read_text(encoding="utf-8"))
                KO = json.loads((tmp / "k_onset.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                KE, KO = {"lines": []}, {"lines": []}
            first = KE["lines"][0]["words"] if KE["lines"] else []
            sop = next((w for w in first if w["w"] == "SOP"), None)
            s.check("kokoro: the engine's own timings are used (source engine) and the spelled 'S O P' is ONE "
                    "display word 'SOP' with its own span",
                    KE.get("source") == "engine" and sop is not None and sop["end"] - sop["start"] >= 0.3
                    and [w["w"] for w in first] == align.display_words(SENT["kokoro"][0]), str(first[:4]))
            rc1, g1 = gate([tmp / "k_onset.json", "--beatmap", tmp / "kbm.json"])
            s.check("kokoro: fallback timings on natural speech pass the structural gate (every word, monotonic, "
                    "in window, none over 2 s)", rc1 == 0, str(g1.get("findings"))[:300])
            rc2, g2 = gate([tmp / "k_onset.json", "--reference", tmp / "k_engine.json", "--criterion", "median"])
            acc = g2.get("accuracy") or {}
            acc.pop("per_line", None)
            M["kokoro_natural_speech_fallback_vs_engine"] = acc
            s.check("kokoro natural speech: fallback median start error <= 80 ms vs the engine's own timings "
                    "(mean/p90/max recorded — NOT every word is within 80 ms)",
                    rc2 == 0 and acc.get("median_ms", 999) <= 80, json.dumps(acc))
    else:
        why = ("shared voice cache absent at " + str(CACHE / "kokoro")) if not have_cache else \
              ("no Python >= 3.10 for the voice venv" if not py311 else "kokoro did not install in the sandbox")
        s.check(f"kokoro timing fixture: SKIPPED — {why} (recorded reason)", True)

    # the voice worker leaves a hard-linked espeak data copy keyed by the (temp) venv path: remove ours
    for d in (set(Path("/tmp").glob("c8-espeak-*")) if Path("/tmp").is_dir() else set()) - espeak_before:
        shutil.rmtree(d, ignore_errors=True)

print("MEASUREMENTS " + json.dumps(M))
if os.environ.get("C8_ALIGN_MEASURE_OUT"):
    Path(os.environ["C8_ALIGN_MEASURE_OUT"]).write_text(json.dumps(M, indent=1), encoding="utf-8")
sys.exit(s.finish())
