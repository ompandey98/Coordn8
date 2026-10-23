#!/usr/bin/env python3
"""doctor check — local voice engines. Read-only: never synthesizes, never writes,
never raises. Reports installed engines and voices, the best engine (from the last
recorded quality probe when there is one, else by quality prior among installed
engines, labelled as such), and model directory sizes."""
from __future__ import annotations

import sys
from pathlib import Path

_VOICE = Path(__file__).resolve().parents[1] / "voice"


def probe() -> dict:
    try:
        if str(_VOICE) not in sys.path:
            sys.path.insert(0, str(_VOICE))
        import engines  # noqa: E402
        det = engines.detect()
        sizes = {e: engines.dir_size(engines.engine_dir(e)) for e in ("kokoro", "piper")}
        avail = [e for e in engines.ENGINE_ORDER if det[e]["installed"] and not det[e]["disabled"]]
        pr = engines.cached_probe()
        ranked = [e for e in (pr or {}).get("ranking", []) if e in avail] if pr else []
        if ranked:
            best, basis = ranked[0], "last quality probe"
        elif avail:
            best, basis = avail[0], "quality prior only (no probe recorded yet: run voices.py probe)"
        else:
            best, basis = None, "no engine installed"
        lines = []
        for e in engines.ENGINE_ORDER:
            d = det[e]
            state = "installed" if d["installed"] else "missing"
            if d["disabled"]:
                state += ", disabled"
            sz = f", {sizes[e] / 1e6:.0f} MB" if sizes.get(e) else ""
            rtf = ""
            if pr and e in pr.get("engines", {}) and pr["engines"][e].get("rtf_wall") is not None:
                rtf = f", probe rtf {pr['engines'][e]['rtf_wall']}"
            lines.append(f"{e}: {state}, {len(d['voices'])} voices{sz}{rtf}"
                         + (f" — {d['reason']}" if d["reason"] else ""))
        total_voices = sum(len(det[e]["voices"]) for e in avail)
        summary = (f"voices: best engine {best} ({basis}); {len(avail)} engine(s), {total_voices} voices; "
                   f"models {sum(sizes.values()) / 1e6:.0f} MB in {engines.voices_root()}")
        return {"summary": summary, "lines": lines, "best": best, "best_basis": basis,
                "engines": {e: {"installed": det[e]["installed"], "disabled": det[e]["disabled"],
                                "voices": det[e]["voices"], "reason": det[e]["reason"]} for e in det},
                "model_dir_bytes": sizes, "probe_ts": (pr or {}).get("ts")}
    except Exception as ex:  # a doctor check never raises
        return {"summary": f"voices: check could not run ({type(ex).__name__}: {ex})", "lines": [],
                "best": None, "engines": {}, "model_dir_bytes": {}}


if __name__ == "__main__":
    import json
    print(json.dumps(probe(), indent=1))
