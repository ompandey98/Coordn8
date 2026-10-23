#!/usr/bin/env python3
"""Coordn8 — infer WHO an explanation is for, from the brief and its context.

Reads `data/audiences.json` (a menu, never a fence) and returns:

    {role, level, reason, confidence, alternatives, ask, stand_down, expert_in,
     subject_fields, keep_terms, simplify_fields, profile, signals}

Rules this script holds mechanically (each one is a test):

  * The audience is INFERRED from what the brief says — a stated audience, role words,
    the setting ("funding review", "science club"), the goal ("start contributing").
  * It never defaults to a child or to "age 5". A child audience needs an explicit child
    signal (an age under 13, "kids", "primary school"...). "Explain like I'm five" (or
    its four-letter abbreviation) is read as *keep it simple*, not as a five-year-old.
  * When the signals are too weak it returns `ask` — one plain question for the Phase-0
    readiness check — plus a best guess marked with low confidence.
  * Expert detection: when the audience is expert in the subject's own field it returns
    `stand_down: true` and the field terms to KEEP (talking down is a defect). An expert
    in a different field (a doctor asking about databases) is simple about the subject
    and normal about their own field.
  * The audience tailors DESIGN only (depth, entry point, register, pace). Nothing here
    produces a greeting; the deliverable never welcomes or role-plays the reader.
  * Freedom: `"audience_role": "invented: <name>"` (or a stated audience starting with
    "invented:") is accepted, with the caller's reason, and composed from the closest
    catalog profile.

Usage:
    python3 scripts/design/audience.py brief.json --json
    python3 scripts/design/audience.py brief.txt            # plain text brief
Brief JSON keys (all optional): audience, audience_role, audience_reason, brief, request,
context, setting, goal, subject, topic, notes.
Exit codes (scripts/lib/common.py): 0 decided · 1 decided but `ask` is set (review) ·
3 bad input.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))
from lib.common import EXIT_ERROR, EXIT_PASS, EXIT_REVIEW  # noqa: E402

PKG_ROOT = HERE.parents[2]
CATALOG = PKG_ROOT / "data" / "audiences.json"

TEXT_KEYS = ("audience", "brief", "request", "prompt", "context", "setting", "goal",
             "subject", "topic", "notes")
ZONE_WEIGHT = {"stated": 3.0, "clause": 2.0, "text": 1.0}

# "Explain like I'm five" (and its abbreviation, matched letter by letter) asks for simplicity,
# never for an audience of five-year-olds.
LIKE_FIVE = re.compile(
    r"\be[l]i\s?5\b|\bexplain(?: it| this)? like (?:i'?m|i am) (?:five|5|a (?:child|kid|five[- ]year[- ]old))"
    r"(?: years? old)?|\blike (?:i'?m|i am) (?:five|5)(?: years? old)?\b|\bdumb (?:it|this) down\b",
    re.IGNORECASE)
AGE_RANGE = re.compile(r"\bage[sd]?\s*(\d{1,2})\s*(?:[-–—]|to)\s*(\d{1,2})\b", re.IGNORECASE)
AGE_ONE = re.compile(r"\bage[sd]?\s*(\d{1,2})\b", re.IGNORECASE)
YEAR_OLDS = re.compile(r"\b(\d{1,2})(?:\s*(?:[-–—]|to)\s*(\d{1,2}))?[- ]years?[- ]olds?\b", re.IGNORECASE)
GRADE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)[- ]grade(?:rs?)?\b|\bgrade\s*(\d{1,2})\b", re.IGNORECASE)
TEEN = re.compile(r"\b(?:teen(?:ager)?s?|high[- ]school(?:ers)?|middle[- ]school(?:ers)?|secondary school)\b",
                  re.IGNORECASE)
LAY = re.compile(r"\bnon[- ]?technical\b|\bnot technical\b|\bno (?:technical|coding|programming|tech) "
                 r"background\b|\blay(?:people|men|person| audience)\b|\bgeneral public\b", re.IGNORECASE)
JUNIOR = re.compile(r"\b(?:junior|new grads?|interns?|entry[- ]level|first[- ]year)\b", re.IGNORECASE)
CLAUSES = [
    re.compile(r"\b(?:audience|viewers|readers|watchers|listeners)\s*(?:is|are|will be|:|=|-)\s*([^.;\n]{2,120})",
               re.IGNORECASE),
    re.compile(r"\bexplain(?:ed|ing)?(?: (?:this|it|that))? to (?:my |our |a |an |the |some )?([^.;,\n]{2,80})",
               re.IGNORECASE),
    re.compile(r"\b(?:for|aimed at|targeting|intended for|meant for) (?:my |our |the |a |an |some )?"
               r"([^.;,\n]{2,80})", re.IGNORECASE),
    re.compile(r"\b(?:tell|show|present (?:it |this )?to) (?:my |our |the )?([^.;,\n]{2,60})", re.IGNORECASE),
]
INVENTED = re.compile(r"^\s*invented\s*:\s*(.+)$", re.IGNORECASE)


def load_catalog(path: Path | None = None) -> dict:
    return json.loads(Path(path or CATALOG).read_text(encoding="utf-8"))


def _rx(fragment: str) -> re.Pattern:
    return re.compile(r"(?<![A-Za-z0-9])(?:" + fragment + r")(?![A-Za-z0-9])", re.IGNORECASE)


def _brief_dict(brief) -> dict:
    if isinstance(brief, dict):
        return brief
    return {"brief": str(brief or "")}


def _gather(b: dict) -> tuple[str, str, str]:
    """(stated audience text, subject text, all text)."""
    stated = str(b.get("audience") or "").strip()
    subject = " ".join(str(b.get(k) or "") for k in ("subject", "topic")).strip()
    everything = " \n".join(str(b.get(k) or "") for k in TEXT_KEYS if b.get(k))
    return stated, subject, everything


def _clauses(text: str) -> list[str]:
    out = []
    for rx in CLAUSES:
        out += [m.group(1) for m in rx.finditer(text)]
    return out


def _ages(text: str) -> list[int]:
    ages: list[int] = []
    for m in AGE_RANGE.finditer(text):
        ages += [int(m.group(1)), int(m.group(2))]
    for m in YEAR_OLDS.finditer(text):
        ages.append(int(m.group(1)))
        if m.group(2):
            ages.append(int(m.group(2)))
    if not ages:
        for m in AGE_ONE.finditer(text):
            ages.append(int(m.group(1)))
    for m in GRADE.finditer(text):
        g = int(m.group(1) or m.group(2))
        if 0 < g <= 12:
            ages.append(g + 5)
    return [a for a in ages if 3 <= a <= 99]


def _score_roles(cat: dict, zones: dict[str, list[str]]):
    """Score every role; a keyword match nested inside a longer match of another role
    ("engineers" inside "staff engineers") is dropped so specificity wins cleanly."""
    found = []  # (role, key, weight, zone, text_index, start, end, matched)
    groups = (("keywords", 1.0), ("setting_cues", 1.0), ("goal_cues", 0.75))
    for role in cat["roles"]:
        for key, factor in groups:
            for frag in role.get(key, []):
                rx = _rx(frag)
                best = None
                for zone in ("stated", "clause", "text"):
                    for ti, t in enumerate(zones[zone]):
                        m = rx.search(t)
                        if m:
                            w = (ZONE_WEIGHT[zone] if key == "keywords" else 1.0) * factor
                            best = (role["id"], key, w, zone, ti, m.start(), m.end(), m.group(0))
                            break
                    if best:
                        break
                if best:
                    found.append(best)
    kept = []
    for f in found:
        nested = any(g is not f and g[0] != f[0] and g[3] == f[3] and g[4] == f[4]
                     and g[5] <= f[5] and g[6] >= f[6] and (g[6] - g[5]) > (f[6] - f[5])
                     for g in found)
        same = any(k[0] == f[0] and k[3] == f[3] and k[4] == f[4] and k[5] == f[5] for k in kept)
        if not nested and not same:
            kept.append(f)
    scores: dict[str, float] = {}
    signals: list[dict] = []
    for role_id, key, w, zone, _ti, a, b, matched in kept:
        scores[role_id] = round(scores.get(role_id, 0.0) + w + 0.01 * (b - a), 3)
        signals.append({"role": role_id, "via": key.replace("_cues", ""), "zone": zone, "matched": matched})
    return scores, signals


def _fields_in(cat: dict, text: str, key: str) -> list[str]:
    hits = []
    for f in cat["fields"]:
        if any(_rx(c).search(text) for c in f.get(key, [])):
            hits.append(f["id"])
    return hits


def _nearest_level(cat: dict, want: str, allowed: list[str]) -> str:
    order = {lv["id"]: lv["order"] for lv in cat["levels"]}
    if want in allowed:
        return want
    return min(allowed, key=lambda a: (abs(order[a] - order[want]), -order[a]))


def profile(cat: dict, role_id: str, level_id: str, stand_down: bool = False,
            expert_in: list[str] | None = None) -> dict:
    roles = {r["id"]: r for r in cat["roles"]}
    levels = {lv["id"]: lv for lv in cat["levels"]}
    base_id = role_id.split(":", 1)[1].strip() if role_id.startswith("invented:") else role_id
    role = roles.get(base_id) or roles["general-public"]
    lv = levels[level_id]
    default = levels[role["default_level"]]
    register = role["register"]
    if level_id != role["default_level"]:
        register = f"{register}; {lv['register_shift']}"
    if lv["order"] <= 2 and default["order"] > lv["order"]:
        analogies = list(lv["analogy_sources"])
    else:
        analogies = list(role["allowed_analogy_sources"])
    jargon = lv["jargon"]
    if stand_down:
        analogies = ["their own field, structurally, or none"]
        jargon = levels["specialist"]["jargon"]
    elif expert_in:
        labels = [f["label"] for f in cat["fields"] if f["id"] in expert_in
                  and f["id"] not in role.get("expert_fields", [])]
        if labels:
            analogies = analogies + [f"structural analogies from their own field ({', '.join(labels)})"]
    return {
        "they_care_about": role["they_care_about"], "frame_around": role["frame_around"],
        "register": register, "entry_point": role["entry_point"] if lv["order"] > 1 else lv["entry_point"],
        "depth": role["depth"] if lv["order"] > 1 else lv["depth"], "video_pace": role["video_pace"],
        "allowed_analogy_sources": analogies, "avoid": role["avoid"], "business_lead": role["business_lead"],
        "sentence_words_max": lv["sentence_words_max"], "jargon": jargon,
        "design_only": "The audience tailors design (depth, entry point, register, pace); the deliverable "
                       "never greets, welcomes or role-plays the reader.",
    }


def infer(brief, cat: dict | None = None) -> dict:
    cat = cat or load_catalog()
    roles = {r["id"]: r for r in cat["roles"]}
    b = _brief_dict(brief)
    stated, subject, everything = _gather(b)

    simplicity = bool(LIKE_FIVE.search(everything))
    scrub = lambda s: LIKE_FIVE.sub(" ", s)  # noqa: E731 — the idiom never counts as an age or a child
    stated_s, everything_s = scrub(stated), scrub(everything)
    invented = None
    for src in (str(b.get("audience_role") or ""), stated):
        m = INVENTED.match(src)
        if m:
            invented = m.group(1).strip()
            break
    if invented and INVENTED.match(stated):
        stated_s = ""
    clause_texts = _clauses(everything_s)
    zones = {"stated": [stated_s] if stated_s else [], "clause": clause_texts, "text": [everything_s]}
    scores, signals = _score_roles(cat, zones)
    audience_text = " ".join([stated_s] + clause_texts) if (stated_s or clause_texts) else everything_s

    ages = _ages(everything_s)
    child_age = bool(ages) and max(ages) <= 12
    teen_age = bool(ages) and not child_age and max(ages) <= 17
    child_words = any(s["role"] == "child" for s in signals)
    child_signal = child_age or child_words
    if child_age:
        scores["child"] = scores.get("child", 0) + 3.0
        signals.append({"role": "child", "via": "age", "zone": "text", "matched": f"ages {min(ages)}-{max(ages)}"})
    if not child_signal:
        scores.pop("child", None)

    # field expertise named in the audience text (cues like "DBAs", "database team", "doctors")
    explicit_fields = _fields_in(cat, audience_text, "audience_cues") if audience_text.strip() else []
    subj_text = subject
    if not subj_text:
        subj_text = everything_s
        for c in sorted(clause_texts + ([stated_s] if stated_s else []), key=len, reverse=True):
            subj_text = subj_text.replace(c, " ")
    subject_fields = _fields_in(cat, subj_text, "subject_cues")
    for fid in explicit_fields:
        f = next(x for x in cat["fields"] if x["id"] == fid)
        if f.get("role_hint") and not scores:
            scores[f["role_hint"]] = scores.get(f["role_hint"], 0) + 1.5
            signals.append({"role": f["role_hint"], "via": "field", "zone": "clause", "matched": f["label"]})

    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    top = ranked[0][1] if ranked else 0.0
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    margin = top - second

    reason_bits = []
    ask = None
    no_signal = False
    if ranked:
        role_id = ranked[0][0]
        conf = 0.25 + 0.12 * min(top, 5.0) + 0.1 * min(margin, 2.0)
        mine, seen = [], set()
        for sg in sorted((x for x in signals if x["role"] == role_id), key=lambda x: -ZONE_WEIGHT.get(x["zone"], 1)):
            if sg["matched"].lower() not in seen:
                seen.add(sg["matched"].lower())
                mine.append(sg)
        where = {"stated": "the stated audience", "clause": "who the brief says it is for", "text": "the brief"}
        what = {"keywords": "role", "setting": "setting", "goal": "goal", "field": "field of expertise",
                "age": "age"}
        reason_bits.append("; ".join(f"{where[x['zone']]} names the {what.get(x['via'], x['via'])} "
                                     f"\"{x['matched']}\"" for x in mine[:3]))
    else:
        role_id = "general-public" if simplicity else "external-developer"
        conf = 0.2
        no_signal = True
        reason_bits.append("no audience signal in the brief; best guess is "
                           + ("a broad lay audience, because the brief asks for a simple explanation"
                              if simplicity else
                              "a smart reader with zero insider context (the shareable default)"))
    conf = round(min(conf, 0.95), 2)

    role = roles[role_id]
    # level
    if child_age:
        level = "child"
        reason_bits.append(f"ages {min(ages)}-{max(ages)} put the reader at child level")
    elif child_words and role_id == "child":
        level = "child"
    elif teen_age or (TEEN.search(everything_s) and role_id in ("student", "general-public")):
        level = "teen"
    elif no_signal:
        level = "lay-adult" if simplicity else "informed-adult"
    else:
        level = role["default_level"]
        markers = cat.get("expertise_markers", [])
        if audience_text and any(_rx(m).search(audience_text) for m in markers):
            level = "specialist"
            reason_bits.append("the audience is described as senior/expert")
        elif LAY.search(everything_s) or simplicity:
            level = "lay-adult"
        elif JUNIOR.search(audience_text or everything_s):
            level = "practitioner" if "practitioner" in role["levels"] else role["default_level"]
        for fid in explicit_fields:
            f = next(x for x in cat["fields"] if x["id"] == fid)
            if f.get("role_hint") == role_id and level == role["default_level"] and f.get("level_hint"):
                level = f["level_hint"]
    if level == "child" and not child_signal:  # hard guard: never default to child
        level = "lay-adult"
    level = _nearest_level(cat, level, role["levels"]) if level != "child" or role_id == "child" else level
    if role_id == "child" and level != "child":
        level = "child"
    if simplicity:
        reason_bits.append("'explain like I'm five' is read as 'keep it simple', not as a five-year-old audience")

    # expertise vs the subject's field (I5)
    audience_fields = list(dict.fromkeys(explicit_fields + list(role.get("expert_fields", []))))
    overlap = [f for f in subject_fields if f in explicit_fields]
    if level == "specialist":
        overlap = [f for f in subject_fields if f in audience_fields]
    stand_down = bool(overlap) and level in ("practitioner", "specialist")
    keep_terms = []
    for f in cat["fields"]:
        if f["id"] in audience_fields:
            keep_terms += [t["term"] for t in f["terms"]]
    simplify_fields = [f for f in subject_fields if f not in audience_fields]
    if stand_down:
        labels = ", ".join(f["label"] for f in cat["fields"] if f["id"] in overlap)
        reason_bits.append(f"the audience is expert in the subject's own field ({labels}): keep its terms, stand down")
    elif explicit_fields and simplify_fields:
        a = ", ".join(f["label"] for f in cat["fields"] if f["id"] in explicit_fields)
        s_ = ", ".join(f["label"] for f in cat["fields"] if f["id"] in simplify_fields)
        reason_bits.append(f"expert in {a} but not in {s_}: simple about {s_}, normal about {a}")

    if invented:
        role_out = f"invented:{invented}"
        reason_bits.insert(0, f"caller invented the audience '{invented}'"
                           + (f" because {b['audience_reason']}" if b.get("audience_reason") else "")
                           + f"; composed from the closest catalog profile ({role['label']})")
        conf = max(conf, 0.7)
    else:
        role_out = role_id

    alternatives = [{"role": r, "score": s} for r, s in ranked[1:4]]
    if conf < 0.5:
        ask = cat.get("phase0_question") or "Who will watch or read this, and how much do they already know?"
    elif ranked and len(ranked) > 1 and margin < 0.5 and not invented:
        ask = (f"Is this mainly for {roles[ranked[0][0]]['label']} or for {roles[ranked[1][0]]['label']}? "
               f"I will tailor depth and register to that.")
        conf = min(conf, 0.55)

    assert not (level == "child" and not child_signal), "child level without an explicit child signal"
    return {
        "role": role_out, "role_label": (invented or role["label"]), "level": level,
        "reason": "; ".join(x for x in reason_bits if x), "confidence": conf,
        "alternatives": alternatives, "ask": ask, "child_signal": child_signal,
        "simplicity_request": simplicity, "stand_down": stand_down,
        "expert_in": [f for f in audience_fields], "subject_fields": subject_fields,
        "keep_terms": keep_terms, "simplify_fields": simplify_fields,
        "profile": profile(cat, role_out, level, stand_down, explicit_fields),
        "signals": signals,
    }


def _read_brief(path: Path):
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("brief JSON must be an object")
        return data
    return {"brief": text}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Infer the audience of an explanation from a brief.")
    ap.add_argument("brief", help="brief.json (keys: audience, brief, context, setting, goal, subject...) or .txt")
    ap.add_argument("--json", action="store_true", help="print the full JSON result (default prints JSON too)")
    ap.add_argument("--catalog", help="alternate audiences.json")
    a = ap.parse_args(argv)
    try:
        brief = _read_brief(Path(a.brief))
        cat = load_catalog(Path(a.catalog) if a.catalog else None)
        res = infer(brief, cat)
    except (OSError, ValueError, KeyError) as e:
        print(json.dumps({"gate": "audience", "status": "ERROR", "summary": f"{type(e).__name__}: {e}"}))
        return EXIT_ERROR
    res = dict(res, gate="audience", status="REVIEW" if res["ask"] else "PASS",
               summary=f"{res['role']} / {res['level']} (confidence {res['confidence']}): {res['reason']}")
    print(json.dumps(res, ensure_ascii=False, indent=1 if a.json else None))
    return EXIT_REVIEW if res["ask"] else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
