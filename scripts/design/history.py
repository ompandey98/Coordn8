#!/usr/bin/env python3
"""Anti-repetition across builds (H3): the user-level choice history and its penalty.

CONTRACT (other streams import this — keep the signatures):

    from design.history import recent_history, penalty, pair_repeats, record_build
    recent_history(n=3) -> list[dict]                     # newest first, user zone
    penalty(kind, candidate_id, history, window=3) -> (float, reason)
    pair_repeats(history, a="palette_family", b="opening_scene") -> list[dict]
    record_build(choices: dict) -> dict                   # appends one row

The formula (SPEC §4):

    score(candidate) = fit − 0.35·[value ∈ the last `window` builds]   (palette_family, kit, spine, voice)
                           − 0.20·[same opening_scene as the LAST build]
    ties broken by the recorded seed (rank(), pick_pair()).

A repeat is allowed when the subject warrants it: pass `warranted="<reason>"` and the penalty
becomes 0.0 with the reason recorded — never silently. History lives in
~/.coordn8/memory/choices.jsonl (override COORDN8_HOME), written append-only by
memory.store.Store; this module never edits or deletes a line.

pick_pair() adds one rule on top of the formula: a (palette family, opening scene) PAIR that
any earlier build already used is skipped while an unused pair exists. The formula alone
lets a family return once it leaves the 3-build window, which can rebuild the same pair
(test_divergence proves that case); the pair rule is what makes "5 consecutive builds never
repeat a pair" hold.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

KINDS = ("palette_family", "kit", "opening_scene", "spine", "voice")
WINDOW_KINDS = ("palette_family", "kit", "spine", "voice")
WINDOW_PENALTY = 0.35
OPENING_PENALTY = 0.2
DEFAULT_WINDOW = 3


def _store():
    from memory.store import Store  # scripts/ is on sys.path (see above)
    return Store()


def recent_history(n: int = 3) -> list:
    """The last n recorded builds across all projects, newest first."""
    return _store().recent_choices(n)


def record_build(choices: dict) -> dict:
    """Append one build's choices (project, build, palette_family, kit, opening_scene, spine,
    voice, seed, reasons…) to the user-level history. Returns the stored row."""
    if not isinstance(choices, dict):
        raise TypeError("choices must be a dict")
    return _store().append_choice(choices)


def _value(row, kind: str):
    v = row.get(kind) if isinstance(row, dict) else None
    if isinstance(v, dict):
        v = v.get("id") or v.get("family") or v.get("name")
    if v is None:
        return None
    v = str(v).strip()
    return v or None


def _norm(v) -> str:
    return str(v).strip().lower()


def _label(row) -> str:
    if not isinstance(row, dict):
        return "an earlier build"
    p, b = row.get("project"), row.get("build")
    if p and b is not None:
        return f"{p} build {b}"
    return str(p or row.get("ts") or "an earlier build")


def penalty(kind: str, candidate_id, history, window: int = DEFAULT_WINDOW, warranted=None):
    """(penalty, reason). palette_family/kit/spine/voice: −0.35 if candidate_id is the value of
    that kind in any of the last `window` builds; opening_scene: −0.2 if it equals the LAST
    build's opening scene. (0.0, "") when there is no repeat. `warranted` (optional, a reason
    string) waives a real repeat and records why."""
    if kind not in KINDS:
        raise ValueError(f"unknown choice kind {kind!r}; expected one of {KINDS}")
    hist = [r for r in (history or []) if isinstance(r, dict)]
    cand = _norm(candidate_id)
    amount, reason = 0.0, ""
    if kind == "opening_scene":
        if hist:
            last = _value(hist[0], kind)
            if last is not None and _norm(last) == cand:
                amount = -OPENING_PENALTY
                reason = (f"opening_scene {candidate_id!r} is the same as the last build's "
                          f"({_label(hist[0])})")
    else:
        for i, row in enumerate(hist[:max(0, int(window))]):
            v = _value(row, kind)
            if v is not None and _norm(v) == cand:
                amount = -WINDOW_PENALTY
                reason = (f"{kind} {candidate_id!r} was used {i + 1} build(s) ago "
                          f"({_label(row)}), inside the last {int(window)}")
                break
    if amount and warranted:
        return 0.0, f"repeat of {kind} {candidate_id!r} allowed because the subject warrants it: {warranted}"
    return amount, reason


def pair_repeats(history, a: str = "palette_family", b: str = "opening_scene") -> list:
    """Every (a, b) pair that occurs in more than one recorded build: [{pair, count, builds}]."""
    seen: dict = {}
    order: list = []
    for row in history or []:
        va, vb = _value(row, a), _value(row, b)
        if va is None or vb is None:
            continue
        key = (_norm(va), _norm(vb))
        if key not in seen:
            seen[key] = {"pair": [va, vb], "count": 0, "builds": []}
            order.append(key)
        seen[key]["count"] += 1
        seen[key]["builds"].append(_label(row))
    out = [seen[k] for k in order if seen[k]["count"] > 1]
    out.sort(key=lambda r: -r["count"])
    return out


def tiebreak(seed, candidate_id) -> float:
    """Deterministic per-seed tiebreak in [0, 1): equal scores order by this, lowest first."""
    h = hashlib.sha256(f"coordn8-tie|{int(seed)}|{_norm(candidate_id)}".encode("utf-8")).hexdigest()
    return int(h[:12], 16) / float(16 ** 12)


def _cands(candidates):
    out = []
    for c in candidates:
        if isinstance(c, dict):
            out.append({"id": c["id"], "fit": float(c.get("fit", 0.0)), "warranted": c.get("warranted")})
        else:
            cid, fit = c[0], c[1]
            out.append({"id": cid, "fit": float(fit), "warranted": None})
    return out


def rank(kind: str, candidates, history, seed, window: int = DEFAULT_WINDOW) -> list:
    """Score candidates ({id, fit[, warranted]} or (id, fit)) with the penalty; best first.
    Equal scores are ordered by the seeded tiebreak, so the order is reproducible."""
    rows = []
    for c in _cands(candidates):
        pen, why = penalty(kind, c["id"], history, window, warranted=c["warranted"])
        rows.append({"id": c["id"], "fit": c["fit"], "penalty": pen, "reason": why,
                     "score": round(c["fit"] + pen, 9), "tiebreak": tiebreak(seed, c["id"])})
    rows.sort(key=lambda r: (-r["score"], r["tiebreak"]))
    return rows


def pick_pair(a_candidates, b_candidates, history, seed, a: str = "palette_family",
              b: str = "opening_scene", window: int = DEFAULT_WINDOW) -> dict:
    """Best joint (a, b) choice: score = score_a + score_b (formula above), skipping any pair
    an earlier build in `history` already used while an unused pair exists. Pass a longer
    history (e.g. recent_history(50)) than the window — the penalty only reads the first
    `window` rows; the pair rule reads all of them."""
    ra = {r["id"]: r for r in rank(a, a_candidates, history, seed, window)}
    rb = {r["id"]: r for r in rank(b, b_candidates, history, seed, window)}
    used = set()
    for row in history or []:
        va, vb = _value(row, a), _value(row, b)
        if va is not None and vb is not None:
            used.add((_norm(va), _norm(vb)))
    pairs = []
    for x in ra.values():
        for y in rb.values():
            pairs.append({"a": x["id"], "b": y["id"], "score": round(x["score"] + y["score"], 9),
                          "tiebreak": tiebreak(seed, f"{x['id']}|{y['id']}"),
                          "a_penalty": x["penalty"], "a_reason": x["reason"],
                          "b_penalty": y["penalty"], "b_reason": y["reason"],
                          "used_before": (_norm(x["id"]), _norm(y["id"])) in used})
    if not pairs:
        raise ValueError("no candidates")
    pairs.sort(key=lambda p: (-p["score"], p["tiebreak"]))
    fresh = [p for p in pairs if not p["used_before"]]
    best = dict(fresh[0] if fresh else pairs[0])
    skipped = len([p for p in pairs if p["used_before"] and p["score"] > best["score"]])
    if fresh:
        best["pair_rule"] = (f"skipped {skipped} higher-scoring pair(s) an earlier build already used"
                             if skipped else "")
    else:
        best["pair_rule"] = "every candidate pair was already used; repeating the best one, recorded"
    best["seed"] = int(seed)
    return best
