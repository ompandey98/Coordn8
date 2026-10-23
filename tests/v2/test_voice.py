#!/usr/bin/env python3
"""V1-V4 — local voice engines, casting, expressiveness, voice QA.

Hermetic: every check runs against a sandbox user home (COORDN8_HOME/HOME in a
temp dir). Engines are installed into the sandbox FROM THE SHARED CACHE (hard
links + an offline uv venv), so nothing is downloaded and nothing touches the
real ~/.coordn8. An engine whose cache is absent is skipped with the reason
recorded as a check; macOS `say` must always run.

Set C8_VOICE_CACHE to point at a different cache directory.
"""
from __future__ import annotations

import array
import json
import math
import os
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import ROOT, Suite, run, sandbox_env, tmpdir  # noqa: E402

CACHE = Path(os.environ.get("C8_VOICE_CACHE",
                            str(ROOT.parent / "Coordn8_v2_handoff" / "_working" / "cache" / "voices")))
VOICE = ROOT / "scripts" / "voice"
s = Suite("voice")
T0 = time.time()


REAL_UV = shutil.which("uv") or str(Path("~/.local/bin/uv").expanduser())


def real_python311():
    uv = REAL_UV
    if not Path(uv).exists():
        return None
    for req in ("3.11", ">=3.10,<3.14"):
        r = subprocess.run([uv, "python", "find", req], capture_output=True, text=True, timeout=30)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip().splitlines()[-1]
    return None


def wav_info(p):
    with wave.open(str(p), "rb") as w:
        return {"sr": w.getframerate(), "ch": w.getnchannels(), "sw": w.getsampwidth(),
                "seconds": w.getnframes() / w.getframerate()}


def write_tone(path, seconds, amp, sr=24000, gap=None, clip=False):
    """Sine at 220 Hz; optional silent gap (start, length); optional hard clipping."""
    a = array.array("h")
    for i in range(int(seconds * sr)):
        t = i / sr
        if gap and gap[0] <= t < gap[0] + gap[1]:
            a.append(0)
            continue
        x = amp * math.sin(2 * math.pi * 220 * t)
        if clip:
            x = max(-1.0, min(1.0, x * 3.0))
        a.append(int(max(-32767, min(32767, x * 32767))))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(a.tobytes())


PY311 = real_python311()

with tmpdir() as td:
    tmp = Path(td)
    env = sandbox_env(tmp)
    env.update({"COORDN8_VOICE_PYTHON": PY311 or "", "COORDN8_UV": REAL_UV if Path(REAL_UV).exists() else "",
                "UV_OFFLINE": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    real_home = Path.home()
    os.environ.update(env)                      # in-process imports see the sandbox too
    sys.path.insert(0, str(VOICE))
    sys.path.insert(0, str(ROOT / "scripts"))
    import engines  # noqa: E402
    import render  # noqa: E402
    import cast  # noqa: E402

    s.check("sandbox: user home is a temp dir, not the real ~/.coordn8",
            str(engines.voices_root()).startswith(str(tmp)) and ".coordn8" not in str(engines.voices_root()),
            str(engines.voices_root()))

    # ------------------------------------------------------------------ V1 install from cache
    have_cache = {e: all((CACHE / e / f["name"]).exists() for f in engines.engine_spec(e).get("files", []))
                  for e in ("kokoro", "piper")}
    r = run("scripts/voice/voices.py", ["install", "--engines", "kokoro,piper,say", "--cache", str(CACHE),
                                        "--timeout", "20"], env=env, timeout=900)
    try:
        inst = json.loads(r.stdout)
    except ValueError:
        inst = {"engines": {}}
    for e in ("kokoro", "piper"):
        row = inst["engines"].get(e, {})
        if have_cache[e]:
            acts = [f.get("action", "") for f in row.get("files", [])]
            s.check(f"install {e} from the shared cache: installed, every model file from cache, none downloaded",
                    row.get("status") == "installed" and acts and all(a.startswith("from-cache") for a in acts),
                    json.dumps(row)[:400])
        else:
            s.check(f"install {e}: SKIPPED here — shared cache absent at {CACHE / e} (recorded reason)", True)
    s.check("install say: present as the OS fallback (nothing downloaded)",
            inst["engines"].get("say", {}).get("status") == "present", json.dumps(inst["engines"].get("say")))
    s.check("install writes models under <user_home>/voices/<engine>/ (user zone)",
            all((Path(env["COORDN8_HOME"]) / "voices" / e / "installed.json").exists()
                for e in ("kokoro", "piper") if have_cache[e]))

    det = engines.detect()
    have = {e: det[e]["installed"] for e in det}

    # ------------------------------------------------------------------ V1 doctor + list
    r = subprocess.run([sys.executable, "-c",
                        "import sys,json; sys.path.insert(0,'scripts'); "
                        "from doctor_checks import voices; print(json.dumps(voices.probe()))"],
                       cwd=str(ROOT), env={**os.environ, **env}, capture_output=True, text=True, timeout=120)
    try:
        doc = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        doc = {"engines": {}, "summary": r.stderr[-300:]}
    s.check("doctor: lists every engine with its installed voices (kokoro, piper, say)",
            all(e in doc.get("engines", {}) for e in ("kokoro", "piper", "say"))
            and len(doc["engines"]["say"]["voices"]) >= 1
            and all(len(doc["engines"][e]["voices"]) >= 1 for e in ("kokoro", "piper") if have[e]),
            doc.get("summary"))
    s.check("doctor: reports model directory sizes and a best engine, and never writes",
            doc.get("best") in ("kokoro", "piper", "say") and set(doc.get("model_dir_bytes", {})) >= {"kokoro", "piper"}
            and not (Path(env["COORDN8_HOME"]) / "voices" / "probe.json").exists(), doc.get("summary"))
    r = run("scripts/voice/voices.py", ["list", "--json"], env=env, timeout=120)
    try:
        L = json.loads(r.stdout)
    except ValueError:
        L = {"voices": []}
    ids = {v["id"] for v in L["voices"]}
    s.check("list --json: shows engines and installed voices with descriptions from the catalog",
            any(i.startswith("say:") for i in ids)
            and all(any(i.startswith(e + ":") for i in ids) for e in ("kokoro", "piper") if have[e])
            and sum(1 for v in L["voices"] if v.get("catalogued") and v.get("description")) >= 5,
            f"{len(ids)} voices")

    # ------------------------------------------------------------------ V1 probe
    r = run("scripts/voice/voices.py", ["probe", "--json"], env=env, timeout=600)
    try:
        P = json.loads(r.stdout)
    except ValueError:
        P = {"engines": {}, "ranking": []}
    top_expected = "kokoro" if have["kokoro"] else ("piper" if have["piper"] else "say")
    if have["kokoro"]:
        site = next((engines.engine_dir("kokoro") / "venv").glob("lib/python3*/site-packages/espeakng_loader"), None)
        s.check("kokoro runs from a user home whose path is longer than espeak-ng's data-path buffer",
                site is not None and len(str(site / "espeak-ng-data")) > 100 and P["engines"]["kokoro"]["ok"],
                f"len={len(str(site)) if site else None} kokoro={P['engines'].get('kokoro')}")
    s.check("probe: every available engine produced audio with a measured real-time factor",
            all(P["engines"].get(e, {}).get("rtf_wall") for e in ("kokoro", "piper", "say") if have[e]),
            json.dumps({e: (v.get("ok"), v.get("rtf_wall"), v.get("reason")) for e, v in P["engines"].items()}))
    s.check("probe: ranks by quality prior gated by success -> best is the top installed engine",
            P.get("best") == top_expected and all(P["engines"][e]["ok"] for e in P.get("ranking", [])),
            f"best={P.get('best')} ranking={P.get('ranking')}")
    s.check("probe: no engine clips (peak < 0.99) and durations are sane for the word count",
            all(v.get("checks", {}).get("no_clipping") and v.get("checks", {}).get("duration_sane")
                for e, v in P["engines"].items() if have[e]),
            json.dumps({e: (v.get("peak"), v.get("wpm")) for e, v in P["engines"].items()}))

    # ------------------------------------------------------------------ V1 render on the top engine (+ V3 line, one load)
    out = tmp / "out"
    fixture = {"id": "fixture", "text": "Follow the SOP before you merge; the checks finish in about two seconds."}
    v3line = {"id": "v3", "text": "First the request arrives. [pause 300] {slow}Then the *checker* decides.{/slow}"}
    try:
        R = render.synth_lines([fixture, v3line], out, engine="auto")
        rf, rv = R[0], R[1]
    except Exception as ex:
        rf = rv = {"engine": None, "out_wav": str(out / "missing.wav"), "error": str(ex)}
    ok_wav = Path(rf["out_wav"]).exists()
    wi = wav_info(rf["out_wav"]) if ok_wav else {}
    s.check("render: fixture narration line renders on the top engine to a valid 24 kHz mono wav",
            rf.get("engine") == top_expected and wi.get("sr") == 24000 and wi.get("ch") == 1
            and wi.get("sw") == 2 and wi.get("seconds", 0) > 1.5, json.dumps({k: rf.get(k) for k in ("engine", "error")}) + str(wi))
    if rf.get("engine") == "kokoro":
        wt = rf.get("word_timings") or []
        s.check("render (kokoro): word timings for every display word, monotonic, inside the audio",
                [w["word"] for w in wt] == fixture["text"].split()
                and all(wt[i]["start"] <= wt[i]["end"] <= wt[i + 1]["start"] + 0.06 for i in range(len(wt) - 1))
                and wt[-1]["end"] <= rf["seconds"] + 0.01, json.dumps(wt)[:300])
    else:
        s.check("render: word timings SKIPPED — top engine here is not kokoro (recorded)", True, rf.get("engine"))

    # ------------------------------------------------------------------ spoken form
    s.check("spoken form: the voice is fed 'S O P' while the returned display text stays 'SOP'",
            "S O P" in (rf.get("spoken_text") or "") and "SOP" in (rf.get("display_text") or "")
            and "S O P" not in (rf.get("display_text") or ""),
            f"{rf.get('spoken_text')} | {rf.get('display_text')}")
    sp_map = [w for w in (rf.get("word_timings") or []) if w["word"].startswith("SOP")]
    s.check("spoken form: the word timing for display 'SOP' covers the spelled letters 'S O P'",
            (sp_map and sp_map[0]["spoken"] == "S O P") or rf.get("engine") != "kokoro", json.dumps(sp_map))
    s.check("spoken form: word-acronyms are not spelled (NASA and JSON stay words for the voice)",
            render.spoken_words("NASA uses JSON for the SOP".split(), ssml=False)[0]
            == ["NASA", "uses", "JSON", "for", "the", "S", "O", "P"])

    # ------------------------------------------------------------------ V3 markup
    pm = render.parse_markup("Wait [pause 300] then *go* now. [beat] {slow}Hard idea.{/slow} {fast}easy bit{/fast}")
    s.check("V3 markup: stripped from the display text",
            pm["display"] == "Wait then go now. Hard idea. easy bit", pm["display"])
    segs = pm["segments"]
    s.check("V3 markup: pauses (300 ms, beat = 300 ms) and pace (slow 0.85, fast 1.1) become segment parameters",
            [x["pause_before_ms"] for x in segs] == [0, 300, 300, 0]
            and [x["speed_mult"] for x in segs] == [1.0, 1.0, 0.85, 1.1], json.dumps(segs))
    s.check("V3 markup: *word* marks emphasis on that word's index in its segment",
            segs[1]["emphasis"] == [1] and segs[1]["text"] == "then go now.", json.dumps(segs[1]))
    if rv.get("out_wav") and Path(rv["out_wav"]).exists():
        q = render.qa_wav(rv["out_wav"], rv.get("pauses"))
        p0 = (rv.get("pauses") or [{}])[0]
        hit = [g for g in q["gaps"] if g["seconds"] >= 0.2 and p0 and g["start"] <= p0.get("end", 0)
               and g["end"] >= p0.get("start", 0)]
        s.check("V3 measurable pause on the top engine: silence >= 200 ms where [pause 300] is marked",
                bool(hit), json.dumps({"engine": rv.get("engine"), "pauses": rv.get("pauses"), "gaps": q["gaps"]})[:400])
        s.check("V3: display text of the rendered line carries no markup",
                rv.get("display_text") == "First the request arrives. Then the checker decides.", rv.get("display_text"))
        s.check("V3: the {slow} span renders slower (speed 0.85 recorded on its segment)",
                any(abs(sg["speed"] - 0.85) < 1e-6 for sg in rv.get("segments", [])), json.dumps(rv.get("segments")))
    else:
        s.check("V3 measurable pause: render failed", False, json.dumps(rv)[:300])
    rs = render.synth("One. [pause 300] Two.", engine="say", out_wav=tmp / "say_v3.wav")
    qs = render.qa_wav(rs["out_wav"], rs["pauses"])
    s.check("V3 measurable pause on say: silence >= 200 ms where marked",
            any(g["seconds"] >= 0.2 and g["intended"] for g in qs["gaps"]), json.dumps(qs["gaps"]))

    # ------------------------------------------------------------------ V4 QA
    write_tone(tmp / "clip.wav", 2.0, 0.9, clip=True)
    write_tone(tmp / "gap.wav", 4.0, 0.5, gap=(1.2, 1.5))
    write_tone(tmp / "clean.wav", 2.0, 0.5)
    qc, qg, qn = render.qa_wav(tmp / "clip.wav"), render.qa_wav(tmp / "gap.wav"), render.qa_wav(tmp / "clean.wav")
    s.check("V4 QA: flags a synthetic clipped wav (negative)", qc["clipped"] and qc["status"] == "review",
            json.dumps({k: qc[k] for k in ("peak", "clipped_samples")}))
    s.check("V4 QA: flags a 1.5 s silence gap inside a line (negative)",
            qg["long_internal_gaps"] and abs(qg["long_internal_gaps"][0]["seconds"] - 1.5) < 0.05, json.dumps(qg["gaps"]))
    s.check("V4 QA: a clean line passes (no clip, no long gap)", qn["status"] == "pass" and not qn["clipped"])
    qi = render.qa_wav(tmp / "gap.wav", [{"start": 1.2, "end": 2.7, "ms": 1500}])
    s.check("V4 QA: a gap the script marked as a pause is excused", not qi["long_internal_gaps"], json.dumps(qi["gaps"]))
    card = render.voice_qa([rf] if ok_wav else [], glossary=["SOP"])
    lst = card["voice_qa"]["listening_list"]
    s.check("V4 QA: scorecard section voice_qa lists spelled glossary terms with the engine that said them",
            "voice_qa" in card and any(x["term"] == "SOP" and x["engine"] == rf.get("engine")
                                       and x["spoken_form"] == "S O P" for x in lst), json.dumps(lst))
    fake = dict(rf, out_wav=str(tmp / "clip.wav"), pauses=[])
    s.check("V4 QA: a clipped line turns the scorecard verdict to review",
            render.voice_qa([fake])["voice_qa"]["verdict"] == "review")

    # ------------------------------------------------------------------ V2 casting
    briefs = {
        "kids science": {"audience": "kids aged 8-10", "register": "kids", "subject": "why the sky is blue",
                         "tone": "playful, warm", "length": "3 minutes"},
        "executive brief": {"audience": "leadership / VP", "register": "executive brief",
                            "subject": "quarterly reliability of the payments pipeline", "tone": "confident, calm",
                            "length": "2 minutes"},
        "deep technical tutorial": {"audience": "senior engineers", "register": "technical deep-dive",
                                    "subject": "how the query planner picks an index", "tone": "precise",
                                    "length": "8 minutes"},
        "documentary": {"audience": "general public", "register": "documentary",
                        "subject": "the history of the transatlantic cable", "tone": "reflective, warm",
                        "length": "6 minutes"},
        "newcomer onboarding explainer": {"audience": "new hires in their first week", "register": "explainer",
                                          "subject": "how a request travels through our service",
                                          "tone": "friendly", "length": "4 minutes"},
    }
    casts = {}
    for name, b in briefs.items():
        casts[name] = cast.cast(b, record=False, recent=[])
    for name, c in casts.items():
        real = [r for r in c["rejected"] if "novelty" not in r.get("why", "") and r["id"] != c["chosen"]["id"]]
        s.check(f"cast '{name}': recorded choice with a reasoned score and >= 2 written rejections",
                c["chosen"]["id"] and c["score"]["fit"] > 0 and len(real) >= 2
                and all(r.get("why") and isinstance(r.get("score"), float) for r in real[:2]) and c["target_why"],
                json.dumps({"chosen": c["chosen"]["id"], "rej": c["rejected"][:2]})[:300])
    if (ROOT / "data" / "audiences.json").exists():
        s.check("cast: reads data/audiences.json — the audience's role, level and pace are recorded in the reasons",
                any("audiences.json: role 'child'" in w for w in casts["kids science"]["target_why"])
                and any("audiences.json: role 'newcomer'" in w
                        for w in casts["newcomer onboarding explainer"]["target_why"]),
                json.dumps(casts["kids science"]["target_why"]))
    else:
        s.check("cast: audiences.json integration SKIPPED — data/audiences.json not present (recorded)", True)
    chosen = [c["chosen"]["id"] for c in casts.values()]
    s.check("cast: the five fixture briefs do not all get the same voice", len(set(chosen)) >= 3, json.dumps(chosen))
    cat = {v["id"]: v for v in engines.catalog()["voices"]}

    def attr(n, k):
        return float(cat.get(casts[n]["chosen"]["id"], {}).get(k, 0.5))
    s.check("cast is sensible: the kids voice is more energetic than the executive-brief voice",
            attr("kids science", "energy") > attr("executive brief", "energy"),
            f"{attr('kids science', 'energy')} vs {attr('executive brief', 'energy')}")
    s.check("cast is sensible: pace is slower for the documentary than for the newcomer explainer",
            casts["documentary"]["settings"]["speed"] < casts["newcomer onboarding explainer"]["settings"]["speed"])
    s.check("cast: every voice in the catalog has a gender-neutral description",
            all(not __import__("re").search(r"\b(female|male|woman|man|girl|boy|her|his|she|he)\b",
                                            v.get("description", ""), __import__("re").I)
                for v in cat.values()), "")
    top_kids = casts["kids science"]["chosen"]["id"]
    pref_voice = next((v for v in ("kokoro:bm_george", "piper:en_GB-cori-medium", "say:Daniel")
                       if v in cat and cast.candidates() and any(c["id"] == v for c in cast.candidates())), None)
    if pref_voice:
        name = pref_voice.split(":", 1)[1]
        cp = cast.cast(dict(briefs["kids science"], stated_preference=f"please use {name} for this one"),
                       record=False, recent=[])
        s.check("cast: a stated preference naming a voice wins absolutely and its quote is recorded",
                cp["chosen"]["id"] == pref_voice and cp["obeyed_user_quotes"] == [f"please use {name} for this one"]
                and cp["stated_preference"]["honoured"], json.dumps(cp["chosen"]))
    cb = cast.cast(dict(briefs["executive brief"], stated_preference="a British voice"), record=False, recent=[])
    s.check("cast: an attribute preference ('a British voice') is a hard filter",
            cb["chosen"]["accent"] == "British English", json.dumps(cb["chosen"]))
    rep = cast.cast(briefs["kids science"], record=False, recent=[top_kids] * 3)
    s.check("cast: anti-repetition changes the pick when the history repeats it 3 times",
            rep["chosen"]["id"] != top_kids and rep["history_penalty_applied"],
            f"{top_kids} -> {rep['chosen']['id']}")
    rep2 = cast.cast(dict(briefs["kids science"], stated_preference=f"use {top_kids.split(':', 1)[1]}"),
                     record=False, recent=[top_kids] * 3)
    s.check("cast: a stated preference overrides anti-repetition", rep2["chosen"]["id"] == top_kids,
            rep2["chosen"]["id"])
    # history through the real store / jsonl, three builds in a row
    for _i in range(3):
        cast.cast(dict(briefs["kids science"], stated_preference=f"use {top_kids.split(':', 1)[1]}"), record=True)
    rec = cast.cast(briefs["kids science"], record=False)
    s.check("cast: choice history is read back from the user store (3 recorded builds change the 4th pick)",
            rec["history"]["recent_voices"][:3] == [top_kids] * 3 and rec["chosen"]["id"] != top_kids,
            json.dumps(rec["history"]))
    # design_system.json read-modify-write
    build = tmp / "proj" / "Coordn8" / "_build"
    build.mkdir(parents=True)
    pre = {"schema": 1, "kit": "editorial", "palette": {"family": "editorial-light", "roles": {"ink": "#1f2430"}},
           "motion": {"ease": "cubic-bezier(.2,.8,.2,1)"}, "voice": {"old": True}}
    (build / "design_system.json").write_text(json.dumps(pre), encoding="utf-8")
    cd = cast.cast(briefs["documentary"], build_dir=build, record=False, recent=[])
    ds = json.loads((build / "design_system.json").read_text(encoding="utf-8"))
    s.check("design_system.json: pre-existing keys are preserved exactly; only 'voice' is replaced",
            all(ds[k] == pre[k] for k in ("schema", "kit", "palette", "motion")) and ds["voice"].get("old") is None
            and ds["voice"]["chosen"]["id"] == cd["chosen"]["id"], json.dumps(list(ds)))
    s.check("design_system.json: voice block carries choice, candidates, rejections and pace/pitch settings",
            all(k in ds["voice"] for k in ("chosen", "candidates", "rejected", "settings"))
            and {"speed", "pitch_semitones"} <= set(ds["voice"]["settings"]), json.dumps(ds["voice"]["settings"]))
    (build / "design_system.json").write_text("[1, 2]", encoding="utf-8")
    try:
        cast.cast(briefs["documentary"], build_dir=build, record=False, recent=[])
        refused = False
    except ValueError:
        refused = True
    s.check("design_system.json: a file whose top level is not an object is refused, not overwritten (negative)",
            refused and (build / "design_system.json").read_text() == "[1, 2]")

    # ------------------------------------------------------------------ V1 fallback
    env_off = dict(env, COORDN8_VOICE_DISABLE=top_expected)
    second = next((e for e in ("kokoro", "piper", "say") if have[e] and e != top_expected), None)
    r = subprocess.run([sys.executable, str(VOICE / "voices.py"), "test", "--out", str(tmp / "fb.wav")],
                       env={**os.environ, **env_off}, capture_output=True, text=True, timeout=600)
    try:
        fb = json.loads(r.stdout)
    except ValueError:
        fb = {"engine": None, "err": r.stderr[-300:]}
    s.check("fallback: disabling the top engine (COORDN8_VOICE_DISABLE) renders cleanly on the next engine",
            fb.get("engine") == second and Path(tmp / "fb.wav").exists() and wav_info(tmp / "fb.wav")["seconds"] > 1,
            json.dumps(fb)[:300])
    if have["kokoro"]:
        mf = engines._kokoro_files()["model"]
        mf.unlink()                                # a hard link into the cache: unlinking is safe
        mf.write_bytes(b"not a model")             # new inode in the sandbox only
        try:
            rr = render.synth("A broken model must not stop the narration.", engine="auto",
                              out_wav=tmp / "broken.wav")
        except Exception as ex:
            rr = {"engine": None, "fallback_from": [], "error": str(ex)}
        s.check("fallback: a top engine that fails at run time falls back to the next and records why",
                rr.get("engine") == "piper" or (rr.get("engine") in ("say",) and not have["piper"]),
                json.dumps({k: rr.get(k) for k in ("engine", "error")}))
        s.check("fallback: the failure of the top engine is recorded in fallback_from",
                any(f.get("engine") == "kokoro" for f in rr.get("fallback_from") or []), json.dumps(rr.get("fallback_from")))
        mf.unlink()
        shutil.rmtree(engines.engine_dir("kokoro"))    # delete the engine dir entirely
        best, order, _p = engines.best_engine()
        s.check("fallback: deleting the top engine's directory drops it from detection and the ranking",
                not engines.detect()["kokoro"]["installed"] and "kokoro" not in order and best == second,
                f"best={best} order={order}")
    else:
        s.check("fallback: runtime-failure and deleted-directory checks SKIPPED — kokoro not installed (recorded)", True)
    env_os = dict(env, COORDN8_VOICE_DISABLE="kokoro,piper")
    r = subprocess.run([sys.executable, str(VOICE / "voices.py"), "test", "--out", str(tmp / "os.wav")],
                       env={**os.environ, **env_os}, capture_output=True, text=True, timeout=300)
    try:
        osr = json.loads(r.stdout)
    except ValueError:
        osr = {}
    s.check("fallback: with both neural engines off, macOS say renders the line (last fallback runs)",
            osr.get("engine") == "say" and Path(tmp / "os.wav").exists(), r.stdout[-300:] + r.stderr[-200:])

    # ------------------------------------------------------------------ install safety (no network)
    spec_backup = engines.engine_spec

    def fake_spec(e):
        if e != "fake":
            return spec_backup(e)
        return {"licence": "test", "files": [
            {"name": "m.bin", "sha256": "0" * 64, "url": "http://127.0.0.1:9/m.bin"},
            {"name": "keep.bin", "sha256": "1" * 64, "url": "http://127.0.0.1:9/keep.bin"}],
            "pip": [], "import_check": []}
    engines.engine_spec = fake_spec
    fcache = tmp / "fcache" / "fake"
    fcache.mkdir(parents=True)
    (fcache / "m.bin").write_bytes(b"wrong bytes")
    (engines.engine_dir("fake")).mkdir(parents=True, exist_ok=True)
    (engines.engine_dir("fake") / "keep.bin").write_bytes(b"user file")
    rep_i = engines.install(["fake"], cache=tmp / "fcache", timeout=2)["engines"]["fake"]
    engines.engine_spec = spec_backup
    acts = {f["name"]: f for f in rep_i["files"]}
    s.check("install: a cache file with the wrong sha256 is not used, the failed download skips the engine with a reason",
            rep_i["status"] == "skipped" and acts["m.bin"]["action"] == "failed" and rep_i.get("reason")
            and not (engines.engine_dir("fake") / "m.bin").exists(), json.dumps(rep_i)[:400])
    s.check("install: an existing file in the user zone is never overwritten",
            acts["keep.bin"]["action"] == "conflict-left-untouched"
            and (engines.engine_dir("fake") / "keep.bin").read_bytes() == b"user file")

    # a successful download (localhost only): sha-verified, stored in the cache, linked into the user zone
    import hashlib
    import http.server
    import socketserver
    import threading
    srv_dir = tmp / "srv"
    srv_dir.mkdir()
    payload = b"model bytes " * 1000
    (srv_dir / "ok.bin").write_bytes(payload)

    class _Quiet(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(srv_dir), **k)

        def log_message(self, *a):
            pass
    httpd = socketserver.TCPServer(("127.0.0.1", 0), _Quiet)
    port = httpd.server_address[1]
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()

    def ok_spec(e):
        if e != "dl":
            return spec_backup(e)
        return {"licence": "test", "pip": [], "import_check": [], "files": [
            {"name": "ok.bin", "sha256": hashlib.sha256(payload).hexdigest(), "url": f"http://127.0.0.1:{port}/ok.bin"}]}
    engines.engine_spec = ok_spec
    orig_venv = engines._make_venv
    engines._make_venv = lambda *a, **k: {"status": "present"}
    try:
        rep_d = engines.install(["dl"], cache=tmp / "dlcache", timeout=5)["engines"]["dl"]
    finally:
        engines.engine_spec = spec_backup
        engines._make_venv = orig_venv
        httpd.shutdown()
        httpd.server_close()
    got = engines.engine_dir("dl") / "ok.bin"
    s.check("install: a real download is sha256-verified, kept in the cache and linked into the user zone",
            rep_d["status"] == "installed" and rep_d["files"][0]["action"].startswith("downloaded-to-cache")
            and got.exists() and got.read_bytes() == payload and (tmp / "dlcache" / "dl" / "ok.bin").exists(),
            json.dumps(rep_d)[:300])

    # ------------------------------------------------------------------ word alignment unit
    blocks = engines.align_words(["ðˈæt", "ðə", "vˈɔɪs"], ["ðætðə", "vˈɔɪs"])
    s.check("alignment: merged function-word groups map many words to one group",
            blocks == [(0, 2, 0, 1), (2, 3, 1, 2)], json.dumps(blocks))
    sp = engines.word_spans_from(blocks, ["ðˈæt", "ðə", "vˈɔɪs"],
                                 [[("ð", 0.0, 0.1), ("æ", 0.1, 0.2), ("t", 0.2, 0.3), ("ð", 0.3, 0.4), ("ə", 0.4, 0.5)],
                                  [("v", 0.6, 0.7), ("ɔ", 0.7, 0.8), ("ɪ", 0.8, 0.9), ("s", 0.9, 1.0)]])
    s.check("alignment: a merged group is split at the real phoneme boundary",
            sp == [[0.0, 0.3], [0.3, 0.5], [0.6, 1.0]], json.dumps(sp))

    s.check("real user home untouched: no voices written under the real ~/.coordn8 by this suite",
            not (real_home / ".coordn8" / "voices" / "probe.json").exists()
            or (real_home / ".coordn8" / "voices" / "probe.json").stat().st_mtime < T0)
    s.check("suite runs inside the 3-minute budget", time.time() - T0 < 180, f"{time.time() - T0:.0f}s")

# the Kokoro worker may leave a hard-linked short copy of the espeak data under /tmp
# for a long sandbox path; remove the ones this run created
for d in Path("/tmp").glob("c8-espeak-*"):
    try:
        if d.stat().st_mtime >= T0 - 1:
            shutil.rmtree(str(d), ignore_errors=True)
    except OSError:
        pass
sys.exit(s.finish())
