#!/usr/bin/env python3
"""Coordn8 — local voices CLI.

    voices.py install [--engines kokoro,piper,say] [--cache DIR] [--timeout S]
    voices.py list [--json]
    voices.py test [--engine E] [--voice V] [--out WAV]
    voices.py cast <brief.json> [--build DIR] [--no-record]
    voices.py probe [--json]
    voices.py qa <results.json> [--glossary SOP,API]
    voices.py profile [--engines ...] [--out FILE]     # measure each voice's sound (build-time)

Everything runs locally; nothing is sent to a hosted speech service.
Exit codes follow the package convention: 0 ok, 1 review, 2 skipped, 3 error.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import engines  # noqa: E402

FIXTURE_LINE = "Follow the SOP before you merge; the checks finish in about two seconds."
PROFILE_TEXT = ("Here is how the system works. A request arrives, the checker reads it, "
                "and the answer comes back in under a second. That is the whole idea.")


def cmd_install(a) -> int:
    eng = [e.strip() for e in a.engines.split(",")] if a.engines else None
    rep = engines.install(eng, cache=a.cache, timeout=a.timeout)
    print(json.dumps(rep, indent=1))
    st = [r["status"] for r in rep["engines"].values()]
    return 0 if all(s in ("installed", "present") for s in st) else (1 if any(
        s in ("installed", "present") for s in st) else 2)


def listing() -> dict:
    det = engines.detect()
    cat = {v["id"]: v for v in engines.catalog().get("voices", [])}
    rows = []
    for e, d in det.items():
        for v in d["voices"]:
            vid = f"{e}:{v}"
            c = cat.get(vid, {})
            rows.append({"id": vid, "engine": e, "voice": v, "catalogued": bool(c),
                         "accent": c.get("accent"), "description": c.get("description"),
                         "best_for": c.get("best_for", []), "licence": c.get("licence")})
    sizes = {e: engines.dir_size(engines.engine_dir(e)) for e in ("kokoro", "piper")}
    return {"engines": det, "voices": rows, "model_dir_bytes": sizes, "probe": engines.cached_probe()}


def cmd_list(a) -> int:
    L = listing()
    if a.json:
        print(json.dumps(L, indent=1))
        return 0
    for e, d in L["engines"].items():
        state = "installed" if d["installed"] else "missing"
        if d["disabled"]:
            state += " (disabled)"
        size = L["model_dir_bytes"].get(e)
        sz = f", {size / 1e6:.0f} MB" if size else ""
        print(f"{e:10s} {state:22s} {len(d['voices']):3d} voices{sz}  {d['reason']}")
    print(f"\n{len(L['voices'])} installed voices "
          f"({sum(1 for v in L['voices'] if v['catalogued'])} catalogued)")
    return 0


def cmd_probe(a) -> int:
    p = engines.probe()
    if a.json:
        print(json.dumps(p, indent=1))
    else:
        for e, r in p["engines"].items():
            print(f"{e:10s} ok={r['ok']!s:5s} rtf_wall={r.get('rtf_wall')} wpm={r.get('wpm')} "
                  f"peak={r.get('peak')} timings={r.get('word_timings')} {r.get('reason', '')}")
        print("best:", p["best"], "ranking:", p["ranking"])
    return 0 if p["best"] else 2


def cmd_test(a) -> int:
    from render import synth
    out = a.out or str(Path(tempfile.mkdtemp(prefix="c8voice-test-")) / "test.wav")
    r = synth(FIXTURE_LINE, engine=a.engine or "auto", voice=a.voice, out_wav=out)
    print(json.dumps({k: r[k] for k in ("engine", "voice", "seconds", "display_text", "spoken_text",
                                        "timing_source", "out_wav", "fallback_from")}, indent=1))
    return 0


def cmd_cast(a) -> int:
    import cast
    brief = json.loads(Path(a.brief).read_text(encoding="utf-8"))
    res = cast.cast(brief, build_dir=a.build, record=not a.no_record)
    print(json.dumps(res, indent=1))
    return 0


def cmd_qa(a) -> int:
    from render import voice_qa
    results = json.loads(Path(a.results).read_text(encoding="utf-8"))
    if isinstance(results, dict):
        results = results.get("lines") or [results]
    gl = [g.strip() for g in a.glossary.split(",")] if a.glossary else None
    out = voice_qa(results, gl)
    print(json.dumps(out, indent=1))
    return 0 if out["voice_qa"]["verdict"] == "pass" else 1


# --------------------------------------------------------------------------- profile (build-time)
def measure(path) -> dict:
    """Acoustic features of one rendered passage (numpy; build-time only).

    f0 by autocorrelation on 40 ms frames (voiced when the normalised peak > 0.45),
    spectral centroid on voiced frames, loudness variation, speaking rate."""
    import numpy as np
    a, sr = engines.read_wav(path)
    x = np.asarray(a, dtype=np.float64) / 32768.0
    n, hop = int(0.04 * sr), int(0.01 * sr)
    f0s, cents, dbs = [], [], []
    lo, hi = int(sr / 400), int(sr / 70)
    win = np.hanning(n)
    peak_rms = 1e-9
    frames = [x[i:i + n] for i in range(0, len(x) - n, hop)]
    rmss = [float(np.sqrt(np.mean(f * f))) for f in frames]
    if rmss:
        peak_rms = max(rmss)
    for f, r in zip(frames, rmss):
        if r < 0.05 * peak_rms:
            continue
        fw = f * win
        spec = np.fft.rfft(fw, 2 * n)
        ac = np.fft.irfft(np.abs(spec) ** 2)[:n]
        if ac[0] <= 0:
            continue
        seg = ac[lo:hi]
        k = int(np.argmax(seg))
        if seg[k] / ac[0] < 0.45:
            continue
        f0s.append(sr / (lo + k))
        mag = np.abs(np.fft.rfft(fw))
        freqs = np.fft.rfftfreq(len(fw), 1.0 / sr)
        m = freqs <= 8000
        if mag[m].sum() > 0:
            cents.append(float((freqs[m] * mag[m]).sum() / mag[m].sum()))
        dbs.append(20 * np.log10(r + 1e-12))
    secs = len(x) / sr
    words = len(PROFILE_TEXT.split())
    if not f0s:
        return {"seconds": round(secs, 2), "voiced_frames": 0}
    st = 12 * np.log2(np.asarray(f0s) / np.median(f0s))
    return {"seconds": round(secs, 2), "wpm": round(words / secs * 60, 1),
            "f0_median_hz": round(float(np.median(f0s)), 1),
            "f0_spread_st": round(float(np.percentile(st, 75) - np.percentile(st, 25)), 2),
            "centroid_hz": round(float(np.median(cents)), 0) if cents else None,
            "loudness_var_db": round(float(np.std(dbs)), 2), "voiced_frames": len(f0s)}


def cmd_profile(a) -> int:
    det = engines.detect()
    want = [e.strip() for e in a.engines.split(",")] if a.engines else [e for e in det if det[e]["installed"]]
    out = {"text": PROFILE_TEXT, "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "voices": {}}
    with tempfile.TemporaryDirectory(prefix="c8voice-prof-") as td:
        for e in want:
            vs = det[e]["voices"]
            if e == "kokoro":
                segs = [{"text": PROFILE_TEXT, "speed": 1.0}]
                for v in vs:
                    try:
                        r = engines.synth_segments(e, v, segs, Path(td) / e / v)[0]
                        out["voices"][f"{e}:{v}"] = measure(r["wav"])
                    except Exception as ex:
                        out["voices"][f"{e}:{v}"] = {"error": str(ex)[:200]}
            else:
                for v in vs:
                    try:
                        r = engines.synth_segments(e, v, [{"text": PROFILE_TEXT}], Path(td) / e / v)[0]
                        out["voices"][f"{e}:{v}"] = measure(r["wav"])
                    except Exception as ex:
                        out["voices"][f"{e}:{v}"] = {"error": str(ex)[:200]}
    txt = json.dumps(out, indent=1)
    if a.out:
        Path(a.out).write_text(txt + "\n", encoding="utf-8")
    print(txt)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Coordn8 local voices")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("install")
    p.add_argument("--engines")
    p.add_argument("--cache")
    p.add_argument("--timeout", type=float, default=60.0)
    p = sub.add_parser("list")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("test")
    p.add_argument("--engine")
    p.add_argument("--voice")
    p.add_argument("--out")
    p = sub.add_parser("cast")
    p.add_argument("brief")
    p.add_argument("--build")
    p.add_argument("--no-record", action="store_true")
    p = sub.add_parser("probe")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("qa")
    p.add_argument("results")
    p.add_argument("--glossary")
    p = sub.add_parser("profile")
    p.add_argument("--engines")
    p.add_argument("--out")
    a = ap.parse_args(argv)
    fn = {"install": cmd_install, "list": cmd_list, "probe": cmd_probe, "test": cmd_test,
          "cast": cmd_cast, "qa": cmd_qa, "profile": cmd_profile}[a.cmd]
    try:
        return fn(a)
    except engines.EngineError as ex:
        print(json.dumps({"status": "error", "error": str(ex)}))
        return 3


if __name__ == "__main__":
    sys.exit(main())
