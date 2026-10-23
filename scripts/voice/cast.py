#!/usr/bin/env python3
"""Coordn8 — VOICE CASTING: pick the voice, pace and pitch for a narrative.

    cast(brief, build_dir=None, record=True) -> dict

brief = {audience, register, subject, tone, length, stated_preference?}

Transparent score per installed voice (every term is written into the result):

    score = fit                      1 - weighted distance between the voice's
                                     measured warmth/energy/clarity and the
                                     target for this register + audience + tone
          + 0.15 * best_for          the catalog lists this register for it
          + 0.25 * engine quality    engine prior / 3 (neural > fast neural > OS)
          - repetition               0.35 + 0.15 * (n - 1) when the voice was
                                     used n times in the last 3 builds
                                     (never applied when the owner stated a
                                     preference)
          + seeded tie-break         < 1e-6, recorded

A stated preference always wins: a named voice is cast outright; attribute words
("British", "deep", "calm", "neural", ...) become hard filters. The quote is
recorded. At least two voices are rejected with a written reason. The result is
written under the single top-level key "voice" of <build>/design_system.json;
every other key in that file is preserved.

The catalog is a menu, not a fence: an installed voice that is not catalogued is
still a candidate (described as uncatalogued), and a brief may name any voice.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import engines  # noqa: E402

REGISTERS = ("explainer", "executive brief", "kids", "documentary", "tutorial", "technical deep-dive")

# target sound per register: energy / warmth / clarity in 0..1, speed multiplier
TARGETS = {
    "explainer":           {"energy": 0.55, "warmth": 0.60, "clarity": 0.80, "speed": 1.03},
    "executive brief":     {"energy": 0.35, "warmth": 0.45, "clarity": 0.85, "speed": 0.97},
    "kids":                {"energy": 0.85, "warmth": 0.75, "clarity": 0.80, "speed": 0.95},
    "documentary":         {"energy": 0.30, "warmth": 0.75, "clarity": 0.75, "speed": 0.92},
    "tutorial":            {"energy": 0.50, "warmth": 0.50, "clarity": 0.85, "speed": 1.00},
    "technical deep-dive": {"energy": 0.40, "warmth": 0.40, "clarity": 0.90, "speed": 0.95},
}
WEIGHTS = {"energy": 0.35, "warmth": 0.30, "clarity": 0.35}

AUDIENCE_HINTS = [
    (r"\b(kid|kids|child|children|pupil|school|age \d+|young)\b", "kids"),
    (r"\b(exec|executive|leader|leadership|vp|board|director|stakeholder)s?\b", "executive brief"),
    (r"\b(newcomer|new hire|onboard\w*|beginner|junior|first week)\b", "explainer"),
    (r"\b(engineer|developer|expert|sme|specialist|maintainer|architect)s?\b", "technical deep-dive"),
    (r"\b(general public|viewers|curious)\b", "documentary"),
]
TONE_ADJ = [
    (r"\b(playful|fun|energetic|excited|lively|upbeat)\b", {"energy": +0.20}),
    (r"\b(calm|measured|serious|sober|reflective|gentle)\b", {"energy": -0.15}),
    (r"\b(warm|friendly|kind|encouraging)\b", {"warmth": +0.15}),
    (r"\b(authoritative|crisp|precise|confident)\b", {"clarity": +0.08, "warmth": -0.05}),
    (r"\b(brisk|fast|quick|punchy)\b", {"speed": +0.05}),
    (r"\b(slow|unhurried|patient)\b", {"speed": -0.05}),
]
TECH_SUBJECT = re.compile(r"\b(api|pipeline|database|kubernetes|algorithm|compiler|protocol|schema|"
                          r"distributed|latency|cache|query|kernel|network|concurrency|model)s?\b", re.I)

PREF_FILTERS = [
    (r"\bbritish|\buk\b|english accent|\bengland\b", ("accent", "British English")),
    (r"\bamerican|\bu\.?s\.?a?\.? (accent|english|voice)", ("accent", "American English")),
    (r"\baustralian", ("accent", "Australian English")),
    (r"\birish", ("accent", "Irish English")),
    (r"\bindian", ("accent", "Indian English")),
    (r"south african", ("accent", "South African English")),
    (r"\b(deep|low)(er)?[- ](voice|pitch|pitched|register|tone)", ("pitch", "low")),
    (r"\b(high|higher)[- ](voice|pitch|pitched|register)|\bbright(er)? voice", ("pitch", "high")),
    (r"\bwarm(er)?\b", ("min", ("warmth", 0.6))),
    (r"\b(calm|soft|gentle)\b", ("max", ("energy", 0.5))),
    (r"\b(energetic|lively|upbeat)\b", ("min", ("energy", 0.55))),
    (r"\bneural\b|\bkokoro\b", ("engine", "kokoro")),
    (r"\bpiper\b", ("engine", "piper")),
    (r"\b(system voice|macos voice|built-in voice)\b", ("engine", "say")),
    (r"\b(fast|faster|quicker)\b", ("speed", +0.08)),
    (r"\b(slow|slower)\b", ("speed", -0.08)),
]


# --------------------------------------------------------------------------- history
def _history_recent(n: int = 3) -> tuple:
    """-> (list of recent voice ids, source). Uses the memory store when importable."""
    try:
        from memory.store import Store  # noqa: E402
        rows = Store().recent_choices(n)
        return [r.get("voice") for r in rows if r.get("voice")], "memory.store"
    except Exception:
        pass
    p = engines._lib().user_home() / "memory" / "choices.jsonl"
    rows = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("voice"):
                rows.append(r["voice"])
    except OSError:
        pass
    return list(reversed(rows))[:n], "choices.jsonl"


def _history_append(row: dict) -> str:
    try:
        from memory.store import Store  # noqa: E402
        Store().append_choice(row)
        return "memory.store"
    except Exception:
        p = engines._lib().user_home() / "memory" / "choices.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
        rec.update(row)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return "choices.jsonl"


# --------------------------------------------------------------------------- inputs
def _register(brief: dict) -> tuple:
    reg = str(brief.get("register") or "").strip().lower()
    alias = {"exec": "executive brief", "executive": "executive brief", "brief": "executive brief",
             "deep-dive": "technical deep-dive", "technical": "technical deep-dive",
             "deep dive": "technical deep-dive", "kid": "kids", "children": "kids",
             "onboarding": "explainer"}
    reg = alias.get(reg, reg)
    aud = str(brief.get("audience") or "").lower()
    ap = _audience_profile(aud)
    hint = (ap or {}).get("register_hint") or next((r for pat, r in AUDIENCE_HINTS if re.search(pat, aud)), None)
    if reg not in REGISTERS:
        return (hint or "explainer"), hint, (f"register '{brief.get('register')}' not in the menu; "
                                             f"used {'audience hint' if hint else 'explainer'}"
                                             if brief.get("register") else None)
    return reg, hint, None


ROLE_REGISTER = {"child": "kids", "executive": "executive brief", "director": "executive brief",
                 "manager": "executive brief", "senior-sme": "technical deep-dive", "engineer": "technical deep-dive",
                 "newcomer": "explainer", "student": "tutorial", "external-developer": "tutorial",
                 "general-public": "documentary", "non-technical-family": "explainer"}
PACE_SHIFT = [("slow", -0.04), ("medium-fast", +0.02), ("fast", +0.04)]


def _audience_profile(audience: str):
    """Match the brief's audience against data/audiences.json (roles -> level), when that
    catalog exists. Returns {"role", "level", "video_pace", "register_hint"} or None.
    Tolerant of schema drift: anything unexpected means 'no match', never an error."""
    f = engines.PKG_ROOT / "data" / "audiences.json"
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    a = (audience or "").lower()
    best, best_len = None, 0
    for role in data.get("roles") or []:
        if not isinstance(role, dict):
            continue
        for kw in role.get("keywords") or []:
            try:
                m = re.search(r"(?<![\w-])(?:" + str(kw) + r")(?![\w-])", a)
            except re.error:
                continue
            if m and len(m.group(0)) > best_len:
                best, best_len = role, len(m.group(0))
    if not best:
        return None
    levels = {lv.get("id"): lv for lv in data.get("levels") or [] if isinstance(lv, dict)}
    lv = levels.get(best.get("default_level")) or {}
    return {"role": best.get("id"), "level": lv.get("id"), "video_pace": lv.get("video_pace"),
            "register_hint": ROLE_REGISTER.get(best.get("id"))}


def target_for(brief: dict) -> dict:
    reg, hint, note = _register(brief)
    t = dict(TARGETS[reg])
    why = [f"register {reg}"]
    if note:
        why.append(note)
    if hint and hint != reg:
        h = TARGETS[hint]
        for k in ("energy", "warmth", "clarity", "speed"):
            t[k] = round(0.7 * t[k] + 0.3 * h[k], 3)
        why.append(f"audience leans {hint} (30% blend)")
    ap = _audience_profile(str(brief.get("audience") or ""))
    if ap:
        why.append(f"audiences.json: role '{ap['role']}', level '{ap['level']}', pace '{ap['video_pace']}'")
        pace = str(ap.get("video_pace") or "").lower()
        for word, d in PACE_SHIFT:
            if pace.startswith(word):
                t["speed"] = round(t["speed"] + d, 3)
                why.append(f"audience pace '{word}' -> speed {d:+}")
                break
    tone = str(brief.get("tone") or "").lower()
    for pat, adj in TONE_ADJ:
        if re.search(pat, tone):
            for k, d in adj.items():
                t[k] = round(t[k] + d, 3)
            why.append(f"tone '{re.search(pat, tone).group(0)}' -> " +
                       ", ".join(f"{k} {d:+}" for k, d in adj.items()))
    subj = str(brief.get("subject") or "")
    if TECH_SUBJECT.search(subj):
        t["clarity"] = round(t["clarity"] + 0.05, 3)
        t["speed"] = round(t["speed"] - 0.03, 3)
        why.append(f"technical subject ('{TECH_SUBJECT.search(subj).group(0)}') -> clarity +0.05, speed -0.03")
    length = str(brief.get("length") or "").lower()
    m = re.search(r"(\d+(?:\.\d+)?)\s*(min|minute)", length)
    long_form = bool(m and float(m.group(1)) >= 6) or "long" in length
    if long_form:
        t["energy"] = round(t["energy"] - 0.05, 3)
        why.append("long narration -> energy -0.05 (listening fatigue)")
    for k in ("energy", "warmth", "clarity"):
        t[k] = max(0.0, min(1.0, t[k]))
    t["speed"] = max(0.8, min(1.2, t["speed"]))
    return {"register": reg, "target": t, "why": why, "long_form": long_form}


# --------------------------------------------------------------------------- candidates
def candidates(installed_only: bool = True) -> list:
    """Every catalogued voice, plus every installed voice that is not catalogued."""
    cat = engines.catalog().get("voices", [])
    det = engines.detect()
    avail = set(engines.available())
    out = []
    seen = set()
    for v in cat:
        e = v.get("engine")
        inst = e in avail and v.get("engine_voice") in det.get(e, {}).get("voices", [])
        row = dict(v)
        row["installed"] = inst
        seen.add(v["id"])
        if inst or not installed_only:
            out.append(row)
    for e in avail:
        for name in det[e]["voices"]:
            vid = f"{e}:{name}"
            if vid in seen:
                continue
            out.append({"id": vid, "engine": e, "engine_voice": name, "installed": True,
                        "uncatalogued": True, "description": "installed voice not in the catalog (unmeasured)",
                        "warmth": 0.5, "energy": 0.5, "clarity": 0.4, "pitch": "unknown",
                        "accent": None, "best_for": [], "pace_range": [0.85, 1.15],
                        "licence": engines.engine_spec(e).get("licence")})
    return out


def _parse_preference(pref: str, pool: list) -> dict:
    p = (pref or "").strip()
    if not p:
        return {}
    low = p.lower()
    for v in pool:
        names = {v["id"].lower(), str(v.get("engine_voice", "")).lower()}
        names |= {n.lower() for n in v.get("aliases", [])}
        for n in names:
            if n and re.search(r"(?<![\w-])" + re.escape(n) + r"(?![\w-])", low):
                return {"voice": v["id"], "how": f"named voice '{n}'"}
    filters, speed = [], 0.0
    for pat, f in PREF_FILTERS:
        if re.search(pat, low):
            if f[0] == "speed":
                speed += f[1]
            else:
                filters.append(f)
    return {"filters": filters, "speed": speed,
            "how": "attribute filters: " + (", ".join(f"{a}={b}" for a, b in filters) or "none")
                   + (f"; speed {speed:+.2f}" if speed else "")}


def _passes(v: dict, filters: list) -> bool:
    for kind, val in filters:
        if kind == "accent" and v.get("accent") != val:
            return False
        if kind == "pitch" and v.get("pitch") != val:
            return False
        if kind == "engine" and v.get("engine") != val:
            return False
        if kind == "min" and float(v.get(val[0], 0)) < val[1]:
            return False
        if kind == "max" and float(v.get(val[0], 1)) > val[1]:
            return False
    return True


def _score(v: dict, tgt: dict, reg: str, recent: list, use_history: bool, seed: int) -> dict:
    t = tgt["target"]
    dist = sum(WEIGHTS[k] * abs(float(v.get(k, 0.5)) - t[k]) for k in WEIGHTS)
    fit = 1.0 - dist
    bf = 0.15 if reg in (v.get("best_for") or []) else 0.0
    eq = 0.25 * engines.prior(v["engine"]) / 3.0
    n = recent.count(v["id"]) if use_history else 0
    rep = (0.35 + 0.15 * (n - 1)) if n else 0.0
    tie = int(hashlib.sha256(f"{seed}:{v['id']}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF * 1e-6
    total = fit + bf + eq - rep + tie
    return {"total": round(total, 6), "fit": round(fit, 4), "best_for_bonus": bf,
            "engine_quality": round(eq, 4), "repetition_penalty": round(rep, 4),
            "repetition_count_last3": n, "tie_break": round(tie, 9)}


def _reject_reason(v: dict, s: dict, win: dict, ws: dict, tgt: dict, reg: str) -> str:
    t = tgt["target"]
    bits = []
    for k in ("energy", "warmth", "clarity"):
        dv, dw = abs(float(v.get(k, 0.5)) - t[k]), abs(float(win.get(k, 0.5)) - t[k])
        if dv > dw + 0.08:
            bits.append(f"{k} {float(v.get(k, 0.5)):.2f} is further from the {reg} target {t[k]:.2f} "
                        f"than the chosen voice ({float(win.get(k, 0.5)):.2f})")
    if s["engine_quality"] < ws["engine_quality"]:
        bits.append(f"lower-quality engine ({v['engine']} vs {win['engine']})")
    if ws["best_for_bonus"] and not s["best_for_bonus"]:
        bits.append(f"not catalogued as suited to {reg}")
    if s["repetition_penalty"]:
        bits.append(f"used in {s['repetition_count_last3']} of the last 3 builds")
    if v.get("uncatalogued"):
        bits.append("uncatalogued: its sound has not been measured")
    if not bits:
        parts = [f"{k} {s[k]:.3f} vs {ws[k]:.3f}" for k in ("fit", "best_for_bonus", "engine_quality",
                                                            "repetition_penalty") if abs(s[k] - ws[k]) > 1e-9]
        bits.append(f"close runner-up: total {s['total']:.3f} vs {ws['total']:.3f} for the chosen voice"
                    + (f" ({'; '.join(parts)})" if parts else " (seeded tie-break)"))
    return "; ".join(bits)


# --------------------------------------------------------------------------- main
def cast(brief: dict, build_dir=None, record: bool = True, seed=None, recent=None) -> dict:
    tgt = target_for(brief)
    reg = tgt["register"]
    seed = int(seed if seed is not None else int(hashlib.sha256(
        json.dumps(brief, sort_keys=True).encode()).hexdigest()[:8], 16))
    pool_all = candidates(installed_only=False)
    pool = [v for v in pool_all if v.get("installed")]
    if recent is None:
        recent, hist_src = _history_recent(3)
    else:
        hist_src = "argument"
    pref_q = str(brief.get("stated_preference") or "").strip()
    pref = _parse_preference(pref_q, pool_all) if pref_q else {}
    use_history = not pref_q
    rejected, excluded, notes = [], [], []

    # hard rejections first: novelty / character voices are never cast unasked
    work = []
    for v in pool:
        if v.get("novelty") and not (pref.get("voice") == v["id"]):
            excluded.append({"id": v["id"], "why": "character / novelty voice — unsuitable for narration"})
        else:
            work.append(v)
    chosen_id = None
    needs_install = False
    if pref.get("voice"):
        chosen_id = pref["voice"]
        cv = next(v for v in pool_all if v["id"] == chosen_id)
        if not cv.get("installed"):
            needs_install = True
            notes.append(f"stated preference names {chosen_id}, whose engine is not installed here; "
                         f"cast anyway (preference wins) — install it or render will fall back")
            work.append(cv)
    elif pref.get("filters"):
        kept = [v for v in work if _passes(v, pref["filters"])]
        for v in work:
            if v not in kept:
                rejected.append({"id": v["id"], "why": f"does not match the stated preference "
                                                       f"({pref['how']})"})
        if not kept:
            outside = [v for v in pool_all if _passes(v, pref["filters"])]
            notes.append("no installed voice matches the stated preference; "
                         + ("cast the best catalogued match, which needs its engine installed"
                            if outside else "nothing in the catalog matches either; best overall kept"))
            if outside:
                needs_install = True
                kept = outside
            else:
                kept = work
        work = kept
    if not work:
        raise engines.EngineError("no voice available to cast (no engine installed)")

    scored = []
    for v in work:
        s = _score(v, tgt, reg, recent, use_history, seed)
        scored.append((s["total"], v, s))
    scored.sort(key=lambda x: -x[0])
    if chosen_id:
        top = next(x for x in scored if x[1]["id"] == chosen_id)
    else:
        top = scored[0]
    _t, win, ws = top
    runners = [x for x in scored if x[1]["id"] != win["id"]]
    for _tt, v, s in runners[:3]:
        rejected.append({"id": v["id"], "score": s["total"],
                         "why": ("the stated preference names another voice; " if chosen_id else "")
                                + _reject_reason(v, s, win, ws, tgt, reg)})
    # guarantee >= 2 written rejections even from a tiny pool
    if len(rejected) < 2:
        for v in pool_all:
            if v["id"] != win["id"] and v["id"] not in {r["id"] for r in rejected}:
                rejected.append({"id": v["id"], "why": "engine not installed here" if not v.get("installed")
                                 else "lower score"})
            if len(rejected) >= 2:
                break

    pr = win.get("pace_range") or [0.85, 1.15]
    speed = tgt["target"]["speed"] + float(pref.get("speed") or 0.0)
    speed = round(max(pr[0], min(pr[1], speed)), 3)
    caps = engines.capabilities(win["engine"])
    result = {
        "schema": 1,
        "chosen": {"id": win["id"], "engine": win["engine"], "engine_voice": win.get("engine_voice"),
                   "description": win.get("description"), "accent": win.get("accent"),
                   "pitch": win.get("pitch"), "licence": win.get("licence"),
                   "installed": bool(win.get("installed")), "needs_install": needs_install},
        "settings": {"speed": speed, "pace_range": pr,
                     "pitch_semitones": 0,
                     "pitch_control": ("engine has no pitch control; the voice's native pitch is used"
                                       if not caps.get("pitch") else "available, left at 0"),
                     "beat_pause_ms": 300, "section_pause_ms": 400},
        "register": reg, "target": tgt["target"], "target_why": tgt["why"],
        "score": ws,
        "candidates": [{"id": v["id"], "score": s["total"], "fit": s["fit"],
                        "repetition_penalty": s["repetition_penalty"]} for _t2, v, s in scored[:8]],
        "rejected": rejected,
        "excluded": excluded,
        "stated_preference": ({"quote": pref_q, "honoured": True, "how": pref.get("how")} if pref_q else None),
        "obeyed_user_quotes": [pref_q] if pref_q else [],
        "history": {"recent_voices": recent, "source": hist_src,
                    "penalty_applied": bool(ws["repetition_penalty"]) or any(
                        s["repetition_penalty"] for _t3, _v, s in scored),
                    "skipped_because_preference": bool(pref_q)},
        "history_penalty_applied": any(s["repetition_penalty"] for _t4, _v, s in scored),
        "seed": seed, "notes": notes,
        "brief": {k: brief.get(k) for k in ("audience", "register", "subject", "tone", "length",
                                            "stated_preference")},
        "cast_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "method": "score = fit + 0.15*best_for + 0.25*engine_quality - repetition (+ seeded tie-break)",
    }
    if build_dir:
        result["design_system"] = write_design_system(build_dir, result)
    if record:
        result["history"]["recorded_to"] = _history_append(
            {"voice": win["id"], "build": str(build_dir) if build_dir else None, "kind": "voice"})
    return result


def write_design_system(build_dir, voice_block: dict) -> str:
    """Read-modify-write <build>/design_system.json: set ONLY the top-level "voice"
    key; every other key is preserved byte-for-byte in value. Atomic replace."""
    d = Path(build_dir)
    d.mkdir(parents=True, exist_ok=True)
    f = d / "design_system.json"
    data = {}
    if f.exists():
        try:
            data = json.loads(f.read_text(encoding="utf-8") or "{}")
        except ValueError as ex:
            raise ValueError(f"refusing to touch {f}: not valid JSON ({ex})")
        if not isinstance(data, dict):
            raise ValueError(f"refusing to touch {f}: top level is not an object")
    before = {k: v for k, v in data.items() if k != "voice"}
    block = {k: v for k, v in voice_block.items() if k != "design_system"}
    data["voice"] = block
    after = {k: v for k, v in data.items() if k != "voice"}
    if json.dumps(before, sort_keys=True) != json.dumps(after, sort_keys=True):
        raise RuntimeError("internal check failed: a key other than 'voice' would change")
    fd, tmp = tempfile.mkstemp(prefix=".design_system.", dir=str(d))
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, str(f))
    return str(f)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Cast a local voice for a brief.")
    ap.add_argument("brief")
    ap.add_argument("--build")
    ap.add_argument("--no-record", action="store_true")
    a = ap.parse_args()
    print(json.dumps(cast(json.loads(Path(a.brief).read_text(encoding="utf-8")), a.build,
                          record=not a.no_record), indent=1))
