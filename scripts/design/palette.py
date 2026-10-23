#!/usr/bin/env python3
"""Palette Director (requirement A1) — subject -> mood -> candidate palettes -> one choice.

    python3 scripts/design/palette.py <brief.json> [--build DIR] [--seed N] [--json]
                                      [--history FILE | --no-history]
    python3 scripts/design/palette.py --rederive-catalog [--catalog data/palettes.json]

Brief (JSON object):
    subject        what the build explains (required)
    audience       who it is for
    tone           how it should feel ("calm", "playful", "precise", ...)
    brand_colours  ["#hex", ...]   first = accent, second = link; the rest are not used
                                   (no-rainbow rule) and the output says so
    stated         what the user said about colour or mood: a string (their quote), or
                   {"quote": "...", "words": ["teal", "calm"]}, or a list of either.
                   A stated colour or mood ALWAYS wins and the output records the quote.
    project        project name (seeds the draw; scopes anti-repetition)
    sections       [{"id": "...", "colour": "teal"|"#hex", "quote": "..."}]  declared
                   per-section accent shifts
    palette        an explicit choice the agent made — never refused:
                   {"family": "<catalog id>", "reason": "..."} or
                   {"invented": "<name>", "reason": "...", "anchor_hue": 200 | "accent": "#hex"} or
                   {"invented": "<name>", "reason": "...", "roles": {...}, "dark": {...}}

What it does, in order (every step is recorded in the output):
  1. reads mood from the subject, audience and tone with a keyword lexicon; stated words
     are weighed far above inferred ones;
  2. scores every catalog family (moods matched, use_when hits, avoid_when hits);
  3. derives at least one INVENTED palette in OKLCH from the leading mood's hue anchor
     (fixed lightness ladders, accent chroma >= 0.12, lightness nudged until every text role
     clears 4.5:1 on its paper in both skins, roles of distinct meaning pushed >= 12 CIEDE2000
     apart);
  4. keeps >= 3 genuinely different candidates (accents >= 20 CIEDE2000 apart);
  5. applies anti-repetition: -0.35 for a family used on the last 3 builds, unless the
     subject clearly calls for it (it still wins by more than the penalty) — reason recorded;
  6. picks the best candidate that obeys every stated colour/mood; rejects the others with
     written reasons;
  7. honours a brand colour as the accent — exactly, if it passes contrast; otherwise with
     the smallest lightness change that passes, and the change is reported;
  8. writes the result to <build>/design_system.json under "palette" (other keys kept).

The catalog is a menu of starting points, never a fence: an invented or agent-supplied
palette is accepted and judged only by outcomes (scripts/gates/check_palette.py).
Zero third-party deps; Python 3.9+.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import random
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from design.color import (contrast, delta_e, hue_distance, oklch_hex, oklch_to_rgb,  # noqa: E402
                          parse_color, rgb_to_oklch, to_hex, composite)
from design import design_system as ds  # noqa: E402

PKG_ROOT = Path(__file__).resolve().parents[2]
CATALOG = PKG_ROOT / "data" / "palettes.json"

DERIVE_TEXT_TARGET = 4.6        # derivation aims a little above the 4.5 bar (hex rounding margin)
DERIVE_SEPARATION = 12.0        # derivation aims a little above the gate's 10
CANDIDATE_MIN_DE = 20.0         # accents of candidates must be this far apart
MIN_ACCENT_CHROMA = 0.12        # OKLCH chroma floor for an invented accent
REPEAT_PENALTY = 0.35           # SPEC §4 anti-repetition weight
REPEAT_WINDOW = 3
STATED_HUE_TOLERANCE = 28.0     # degrees: a palette "is teal" if its accent hue is this close
EXCLUDED_HUE_TOLERANCE = 30.0
SERIES_STEPS = (1.0, 0.78, 0.58, 0.40, 0.26)

# Movement priority: earlier roles hold still, later roles move when two collide.
PRIORITY = ("paper", "ink", "accent", "muted", "negative", "positive", "warning", "link",
            "deterministic", "model-judgement", "data-field", "vision")
# Semantic hue homes (OKLCH degrees), first choice first. Roles never drift > 45° from home.
SEMANTIC_HUES = {
    "negative": (25, 10, 40, 355, 340),
    "positive": (150, 135, 165, 125, 180, 115, 195),
    "warning": (75, 88, 62, 100, 50, 110),
    "link": (255, 240, 270, 225, 285),
    "deterministic": (245, 230, 260, 215, 275),
    "model-judgement": (305, 290, 320, 280, 335),
    "data-field": (170, 155, 185, 140, 200),
    "vision": (85, 70, 100, 60, 115),
}
GROUP_ORDER = (("negative", "positive", "warning", "link"),
               ("deterministic", "model-judgement", "data-field", "vision"))

# ------------------------------------------------------------------ mood lexicon
# mood -> (hue anchor in OKLCH degrees, keywords). Keywords match whole words (plural "s" ok).
MOODS: Dict[str, Tuple[float, Tuple[str, ...]]] = {
    "trustworthy": (250, ("trust", "trustworthy", "bank", "banking", "insurance", "compliance",
                          "audit", "secure", "security", "reliable")),
    "precise": (235, ("precise", "precision", "accuracy", "accurate", "ledger", "accounting",
                      "measurement", "metric", "exact", "settlement", "reconciliation")),
    "growth": (155, ("growth", "revenue", "profit", "money", "invest", "investment", "investing",
                     "return", "earning", "fintech", "finance", "financial", "payment", "wallet",
                     "lending", "budget", "card transaction", "transaction")),
    "calm": (205, ("calm", "gentle", "soothing", "quiet", "mindful", "wellness", "relaxed",
                   "serene", "reassuring", "steady")),
    "clinical": (220, ("medical", "medicine", "clinical", "clinician", "patient", "health",
                       "healthcare", "hospital", "doctor", "nurse", "diagnosis", "diagnostic",
                       "pharma", "drug", "therapy", "radiology", "triage")),
    "strategic": (18, ("strategy", "strategic", "game theory", "equilibrium", "nash", "payoff",
                       "incentive", "negotiation", "auction", "chess", "opponent", "bargaining",
                       "mechanism design", "decision making")),
    "tense": (12, ("tension", "conflict", "dilemma", "prisoner", "adversarial", "threat", "war",
                   "stakes", "urgent")),
    "competitive": (25, ("competition", "competitive", "compete", "rival", "versus", "tournament",
                         "market share", "duel")),
    "intellectual": (285, ("theory", "theorem", "proof", "logic", "math", "mathematics",
                           "philosophy", "reasoning", "abstract", "academic", "research",
                           "curious")),
    "industrial": (62, ("infrastructure", "infra", "server", "datacenter", "data center", "network",
                        "networking", "cloud", "kubernetes", "cluster", "hardware", "factory",
                        "ops", "devops", "terraform", "container")),
    "robust": (58, ("reliability", "resilient", "resilience", "uptime", "sre", "incident",
                    "outage", "scaling", "autoscaling", "robust", "fault", "failover", "on-call",
                    "oncall", "direct")),
    "technical": (258, ("api", "software", "code", "codebase", "engineering", "engineer",
                        "developer", "backend", "architecture", "system design", "technical",
                        "compiler", "database")),
    "playful": (345, ("kid", "kids", "children", "child", "playful", "fun", "toy", "cartoon",
                      "school", "classroom", "pupil", "young learner", "primary school")),
    "friendly": (45, ("friendly", "welcoming", "beginner", "introduction", "intro", "learning",
                      "education", "educational", "student", "teach", "teaching", "tutorial",
                      "course")),
    "warm": (45, ("warm", "cozy", "family", "community", "human", "humane", "people", "care")),
    "energetic": (28, ("energetic", "exciting", "bold", "vibrant", "sport", "action", "launch",
                       "hype", "dynamic", "lively")),
    "deep": (285, ("space", "astronomy", "cosmos", "cosmic", "quantum", "physics", "universe",
                   "galaxy", "deep", "telescope")),
    "futuristic": (292, ("ai", "machine learning", "neural", "neural network", "llm", "model training",
                         "future", "futuristic", "robot", "robotics", "autonomous")),
    "editorial": (40, ("history", "historical", "culture", "humanities", "essay", "journalism",
                       "article", "editorial", "long-form", "story", "storytelling", "heritage",
                       "society")),
    "natural": (118, ("nature", "ecology", "climate", "agriculture", "farm", "farming", "food",
                      "plant", "forest", "sustainable", "sustainability", "environment",
                      "environmental", "biology", "photosynthesis", "sunlight")),
    "fresh": (192, ("fresh", "clean", "water", "ocean", "marine", "flow", "stream", "streaming",
                    "pipeline", "data", "analytics", "logistics", "etl")),
    "creative": (320, ("design", "art", "artist", "music", "creative", "fashion", "brand",
                       "branding", "ux", "typography", "illustration", "film", "photography")),
    "elegant": (88, ("elegant", "luxury", "premium", "refined", "sophisticated", "exclusive",
                     "gala")),
    "dramatic": (88, ("dramatic", "keynote", "executive", "board", "cinematic", "award",
                      "launch event", "investor")),
    "serious": (255, ("legal", "law", "regulation", "regulatory", "policy", "government",
                      "serious", "formal", "governance", "contract")),
    "authoritative": (258, ("authoritative", "official", "standard", "reference", "documentation",
                            "docs", "spec", "specification", "enterprise")),
}
SOURCE_WEIGHT = {"subject": 1.0, "audience": 0.7, "tone": 1.2, "stated": 4.0}

# colour words -> OKLCH hue
COLOUR_WORDS = {
    "red": 27, "crimson": 18, "scarlet": 30, "vermilion": 35, "coral": 38, "orange": 55,
    "amber": 72, "gold": 88, "golden": 88, "mustard": 95, "yellow": 100, "olive": 115,
    "lime": 130, "green": 148, "emerald": 160, "mint": 170, "teal": 190, "turquoise": 195,
    "aqua": 200, "cyan": 210, "sky": 232, "blue": 258, "cobalt": 262, "navy": 264,
    "indigo": 280, "violet": 295, "purple": 305, "lavender": 300, "plum": 325,
    "magenta": 335, "berry": 348, "pink": 352, "rose": 5, "brown": 50, "terracotta": 42,
    "rust": 40, "maroon": 15, "burgundy": 10, "forest": 145,
}
SKIN_WORDS = {"dark mode": "dark", "dark theme": "dark", "dark background": "dark", "night mode": "dark",
              "on black": "dark", "light mode": "light", "light theme": "light",
              "light background": "light", "on white": "light"}
NEGATORS = r"(?<![a-z])(?:no|not|never|avoid|without|hate|don't want|dont want|anything but)"
# adjectives a user says about mood -> mood id
MOOD_ADJECTIVES = {
    "calm": "calm", "gentle": "calm", "soothing": "calm", "quiet": "calm", "serene": "calm",
    "reassuring": "calm", "relaxed": "calm", "playful": "playful", "fun": "playful", "cheerful": "playful",
    "warm": "warm", "cozy": "warm", "serious": "serious", "formal": "serious", "sober": "serious",
    "precise": "precise", "crisp": "precise", "friendly": "friendly", "welcoming": "friendly",
    "elegant": "elegant", "luxurious": "elegant", "luxury": "elegant", "premium": "elegant",
    "dramatic": "dramatic", "cinematic": "dramatic", "energetic": "energetic", "bold": "energetic",
    "vibrant": "energetic", "lively": "energetic", "fresh": "fresh", "clean": "fresh",
    "creative": "creative", "artsy": "creative", "futuristic": "futuristic", "natural": "natural",
    "earthy": "natural", "organic": "natural", "technical": "technical", "techy": "technical",
    "trustworthy": "trustworthy", "deep": "deep", "mysterious": "deep", "tense": "tense",
    "edgy": "tense", "industrial": "industrial", "editorial": "editorial", "strategic": "strategic",
}


def _kw_re(kw: str) -> re.Pattern:
    return re.compile(r"(?<![a-z0-9])" + re.escape(kw) + r"(?:s|es)?(?![a-z0-9])")


_KW_CACHE: Dict[str, re.Pattern] = {}


def _has(text: str, kw: str) -> bool:
    p = _KW_CACHE.get(kw)
    if p is None:
        p = _KW_CACHE[kw] = _kw_re(kw)
    return bool(p.search(text))


# ------------------------------------------------------------------ derivation (OKLCH)
def _lch_hex(v) -> str:
    return oklch_hex(v[0], v[1], v[2])


def _fit_contrast(v, paper_hex: str, light: bool, target: float = DERIVE_TEXT_TARGET):
    L, C, h = v
    for _ in range(400):
        if contrast(oklch_hex(L, C, h), paper_hex) >= target:
            return [L, C, h]
        L += -0.004 if light else 0.004
        if L <= 0.03 or L >= 0.995:
            break
    return [max(0.03, min(0.995, L)), C, h]


def adjust_for_contrast(hex_value: str, paper_hex: str, target: float = ds.TEXT_MIN) -> Tuple[str, Optional[dict]]:
    """Keep a given colour if it passes; else change ONLY its OKLCH lightness, by the smallest
    step that passes, and return a record of the change."""
    if contrast(hex_value, paper_hex) >= target:
        return hex_value, None
    L, C, h = rgb_to_oklch(hex_value)
    light_paper = rgb_to_oklch(paper_hex)[0] > 0.5
    step = -0.002 if light_paper else 0.002
    L2 = L
    new = hex_value
    for _ in range(600):
        L2 += step
        if not 0.0 < L2 < 1.0:
            break
        new = oklch_hex(L2, C, h)
        if contrast(new, paper_hex) >= target + 0.02:
            break
    return new, {"from": hex_value, "to": new, "paper": paper_hex,
                 "from_ratio": round(contrast(hex_value, paper_hex), 2),
                 "to_ratio": round(contrast(new, paper_hex), 2),
                 "delta_L_oklch": round(L2 - L, 3), "hue_kept": round(h, 1),
                 "why": f"{hex_value} reads {contrast(hex_value, paper_hex):.2f}:1 on {paper_hex}, "
                        f"below {target}:1; lightness changed by {L2 - L:+.3f} (hue and chroma kept)"}


def _assign_hues(accent_h: float) -> Dict[str, float]:
    hues: Dict[str, float] = {}
    for group in GROUP_ORDER:
        taken: List[float] = []
        for role in group:
            alts = SEMANTIC_HUES[role]
            best = None
            for a in alts:
                if hue_distance(a, accent_h) >= 35 and all(hue_distance(a, t) >= 30 for t in taken):
                    best = a
                    break
            if best is None:
                best = max(alts, key=lambda a: min([hue_distance(a, accent_h)] + [hue_distance(a, t) for t in taken]))
            hues[role] = float(best)
            taken.append(float(best))
    return hues


def _pairs(roles: Sequence[str]):
    seen = set()
    for g in ds.SEPARATION_GROUPS:
        for a, b in itertools.combinations(g, 2):
            if a in roles and b in roles and (a, b) not in seen:
                seen.add((a, b))
                yield a, b


def _partners(role: str) -> List[str]:
    out: List[str] = []
    for g in ds.SEPARATION_GROUPS:
        if role in g:
            out += [r for r in g if r != role and r not in out]
    return out


def _separate(state: dict, fixed: set, homes: dict, paper_hex: str, light: bool, log: list,
              skin: str, target: float = DERIVE_SEPARATION) -> List[dict]:
    """Push apart every pair of roles of distinct meaning that read as one colour."""
    unresolved: List[dict] = []
    stuck: set = set()
    for _ in range(80):
        hexes = {r: _lch_hex(v) for r, v in state.items()}
        bad = []
        for a, b in _pairs(list(state)):
            d = delta_e(hexes[a], hexes[b])
            if d < target and (a, b) not in stuck:
                bad.append((d, a, b))
        if not bad:
            break
        bad.sort()
        d, a, b = bad[0]
        movers = sorted([r for r in (a, b) if r not in fixed and r not in ("paper", "ink")],
                        key=lambda r: -PRIORITY.index(r))
        moved = False
        for mover in movers:
            base = state[mover]

            def score(v):
                hx = _lch_hex(v)
                return min(delta_e(hx, _lch_hex(state[p])) for p in _partners(mover) if p in state)

            current = score(base)
            options = []
            for dh in (8, -8, 16, -16, 24, -24, 32, -32, 40, -40):
                h = (base[2] + dh) % 360
                if mover in homes and hue_distance(h, homes[mover]) > 45:
                    continue
                options.append([base[0], base[1], h])
            for dL in (-0.04, -0.08, -0.12, 0.04, 0.08, 0.12):
                options.append([base[0] + dL, base[1], base[2]])
            for dC in (0.03, -0.03, 0.06):
                options.append([base[0], max(0.0, base[1] + dC), base[2]])
            best, best_s = None, current
            for o in options:
                f = _fit_contrast(o, paper_hex, light)
                s = score(f)
                if s > best_s + 0.05:
                    best, best_s = f, s
            if best is not None:
                log.append({"skin": skin, "step": "separate", "role": mover, "from": _lch_hex(base),
                            "to": _lch_hex(best), "pair": [a, b], "delta_e_before": round(d, 2),
                            "min_delta_e_after": round(best_s, 2)})
                state[mover] = best
                moved = True
                break
        if not moved:
            stuck.add((a, b))
            unresolved.append({"skin": skin, "pair": [a, b], "delta_e": round(d, 2)})
    return unresolved


_MC_CACHE: Dict[tuple, Optional[list]] = {}


def _max_chroma_at(h: float, paper_hex: str, light: bool, target: float = DERIVE_TEXT_TARGET,
                   want: Optional[float] = None, near_L: Optional[float] = None):
    """At hue h, among lightnesses that clear the contrast target on paper: with `want`, the one
    closest to `near_L` whose gamut holds `want` chroma (taken at exactly `want`); otherwise the
    most saturated one. Returns [L, C, h] or None."""
    key = (round(h, 2), paper_hex, light, target, want, None if near_L is None else round(near_L, 3))
    if key in _MC_CACHE:
        return _MC_CACHE[key]
    best = None
    L = 0.30 if light else 0.60
    while (L < 0.66) if light else (L < 0.95):
        rgb, c = oklch_to_rgb(L, 0.4, h)
        if contrast(to_hex(rgb), paper_hex) >= target:
            if want is not None:
                if c >= want and contrast(oklch_hex(L, want, h), paper_hex) >= target:
                    if best is None or abs(L - (near_L or L)) < abs(best[0] - (near_L or best[0])):
                        best = [L, want, h]
            elif best is None or c > best[1]:
                best = [L, c, h]
        L += 0.005
    if want is not None and best is None:
        best = _max_chroma_at(h, paper_hex, light, target)
    _MC_CACHE[key] = best
    return best


def derive(anchor_hue: float, *, name: str = "invented", paper_hue: Optional[float] = None,
           paper_chroma: float = 0.012, paper_L: float = 0.975, dark_paper_L: float = 0.205,
           dark_paper_chroma: float = 0.022, ink_hue: Optional[float] = None, ink_chroma: float = 0.02,
           accent_chroma: float = 0.15, accent_L: float = 0.53, semantic_chroma: float = 0.13,
           accent_override: Optional[Dict[str, str]] = None,
           link_hint: Optional[str] = None, chroma_policy: str = "keep") -> dict:
    """Build a full light + dark palette mechanically in OKLCH from one hue anchor.

    Fixed lightness ladders (paper / ink / muted / accent / semantic), accent chroma at least
    0.12, semantic roles at their hue homes pushed away from the accent, then lightness nudged
    until every text role clears 4.5:1 on its paper and roles of distinct meaning are at least
    12 CIEDE2000 apart. Returns {"roles", "dark", "log", "unresolved", "accent_chroma", ...}.

    sRGB cannot hold 0.12 chroma at every hue at a lightness that passes 4.5:1 on light paper
    (yellows top out near 0.11). The light accent is therefore taken at the most saturated
    lightness that passes; if that is still under 0.12, chroma_policy="rotate" moves the anchor
    to the nearest hue that can carry 0.12 (used for mood-anchored invented palettes), and
    "keep" keeps the hue (a stated or brand colour) — either way `gamut_note` records it.
    """
    gamut_note = None
    if chroma_policy == "rotate" and not accent_override:
        probe_paper = oklch_hex(paper_L, paper_chroma, anchor_hue if paper_hue is None else float(paper_hue))
        mc = _max_chroma_at(float(anchor_hue) % 360, probe_paper, True)
        if mc is None or mc[1] < MIN_ACCENT_CHROMA:
            for step in range(1, 91):
                hits = []
                for cand in ((anchor_hue + step) % 360, (anchor_hue - step) % 360):
                    m2 = _max_chroma_at(cand, probe_paper, True)
                    if m2 and m2[1] >= MIN_ACCENT_CHROMA + 0.002:
                        hits.append(cand)
                if hits:
                    gamut_note = {"from_hue": round(float(anchor_hue) % 360, 1), "to_hue": round(hits[0], 1),
                                  "why": f"no sRGB colour at hue {float(anchor_hue) % 360:.0f}° carries "
                                         f"{MIN_ACCENT_CHROMA} chroma and 4.5:1 on light paper; anchor moved "
                                         f"{step}° to the nearest hue that does"}
                    anchor_hue = hits[0]
                    break
    anchor_hue = float(anchor_hue) % 360
    paper_hue = anchor_hue if paper_hue is None else float(paper_hue)
    ink_hue = anchor_hue if ink_hue is None else float(ink_hue)
    accent_chroma = max(MIN_ACCENT_CHROMA, float(accent_chroma))
    accent_override = accent_override or {}
    hues = _assign_hues(anchor_hue)
    if link_hint:
        hues["link"] = rgb_to_oklch(link_hint)[2]
    log: List[dict] = []
    out: dict = {"name": name, "anchor_hue": round(anchor_hue, 1), "hues": hues}
    unresolved: List[dict] = []
    ladders = {
        "light": {"paper": (paper_L, paper_chroma), "ink": (0.25, ink_chroma), "muted": (0.48, 0.03),
                  "accent": (accent_L, accent_chroma), "semantic": (0.50, semantic_chroma)},
        "dark": {"paper": (dark_paper_L, dark_paper_chroma), "ink": (0.94, ink_chroma * 0.6),
                 "muted": (0.77, 0.03), "accent": (0.76, accent_chroma * 0.92),
                 "semantic": (0.75, semantic_chroma * 0.95)},
    }
    for skin in ("light", "dark"):
        light = skin == "light"
        lad = ladders[skin]
        paper_hex = oklch_hex(lad["paper"][0], lad["paper"][1], paper_hue)
        state: Dict[str, list] = {}
        fixed = set()
        state["ink"] = [lad["ink"][0], lad["ink"][1], ink_hue]
        state["muted"] = [lad["muted"][0], lad["muted"][1], ink_hue]
        if skin in accent_override:
            L, C, h = rgb_to_oklch(accent_override[skin])
            state["accent"] = [L, C, h]
            fixed.add("accent")
        else:
            state["accent"] = [lad["accent"][0], lad["accent"][1], anchor_hue]
        for role, h in hues.items():
            state[role] = [lad["semantic"][0], lad["semantic"][1], h]
        for role in list(state):
            if role in fixed:
                continue
            before = _lch_hex(state[role])
            state[role] = _fit_contrast(state[role], paper_hex, light)
            if _lch_hex(state[role]) != before:
                log.append({"skin": skin, "step": "contrast", "role": role, "from": before,
                            "to": _lch_hex(state[role]),
                            "ratio": round(contrast(_lch_hex(state[role]), paper_hex), 2)})
        if "accent" not in fixed and light:
            acc_c = rgb_to_oklch(_lch_hex(state["accent"]))[1]
            if acc_c < MIN_ACCENT_CHROMA:
                mc = _max_chroma_at(state["accent"][2], paper_hex, light, want=MIN_ACCENT_CHROMA + 0.004,
                                    near_L=state["accent"][0])
                if mc and mc[1] > acc_c + 0.002:
                    log.append({"skin": skin, "step": "chroma", "role": "accent", "from": _lch_hex(state["accent"]),
                                "to": _lch_hex(mc), "chroma": round(mc[1], 4)})
                    state["accent"] = mc
        unresolved += _separate(state, fixed, hues, paper_hex, light, log, skin)
        roles = {"paper": paper_hex}
        for r in ds.TEXT_ROLES:
            if r in fixed:
                roles[r] = accent_override[skin]
            else:
                roles[r] = _lch_hex(state[r])
        out["roles" if light else "dark"] = roles
    out["log"] = log
    out["unresolved"] = unresolved
    out["accent_chroma"] = round(rgb_to_oklch(out["roles"]["accent"])[1], 4)
    if gamut_note is None and out["accent_chroma"] < MIN_ACCENT_CHROMA and not accent_override:
        gamut_note = {"from_hue": round(anchor_hue, 1), "to_hue": round(anchor_hue, 1),
                      "why": f"hue {anchor_hue:.0f}° kept as given; the most saturated colour there that clears "
                             f"4.5:1 on light paper has chroma {out['accent_chroma']} (sRGB limit)"}
    out["gamut_note"] = gamut_note
    out["measured"] = measure(out["roles"], out["dark"])
    return out


def measure(light: dict, dark: dict) -> dict:
    """Outcome numbers for a palette: min text contrast per skin, min role separation."""
    res = {}
    for skin, roles in (("light", light), ("dark", dark)):
        p = roles.get("paper")
        ratios = {r: round(contrast(roles[r], p), 2) for r in ds.TEXT_ROLES if r in roles and p}
        seps = {f"{a}~{b}": round(delta_e(roles[a], roles[b]), 2) for a, b in _pairs(list(roles))}
        res[skin] = {"min_text_contrast": min(ratios.values()) if ratios else None,
                     "contrast": ratios,
                     "min_separation": min(seps.values()) if seps else None,
                     "closest_pair": min(seps, key=seps.get) if seps else None}
    return res


def chart_series(roles: dict, dark: dict) -> dict:
    """One ink at stepped opacity (never one hue per series). Named by contrast, not lightness."""
    out = {"role": "ink", "opacities": list(SERIES_STEPS), "light": [], "dark": []}
    for skin, r in (("light", roles), ("dark", dark)):
        for a in SERIES_STEPS:
            flat = to_hex(composite(r["ink"], r["paper"], a))
            out[skin].append({"opacity": a, "flattened": flat,
                              "contrast_vs_paper": round(contrast(flat, r["paper"]), 2)})
    out["monotonic"] = all(
        all(s[i]["contrast_vs_paper"] > s[i + 1]["contrast_vs_paper"] for i in range(len(s) - 1))
        for s in (out["light"], out["dark"]))
    out["legend_wording"] = {"use": "stronger = more, weaker = less",
                             "never": "darker / lighter / paler",
                             "why": "the ink ramp darkens on light paper and lightens on dark paper; "
                                    "only contrast against the paper reads the same in both skins"}
    return out


# ------------------------------------------------------------------ catalog
def load_catalog(path: Optional[Path] = None) -> dict:
    p = Path(path) if path else CATALOG
    return json.loads(p.read_text(encoding="utf-8"))


def rederive_catalog(path: Optional[Path] = None) -> dict:
    """Recompute every family's roles from its recorded `derivation` (no hand-picked hex)."""
    p = Path(path) if path else CATALOG
    cat = load_catalog(p)
    for fam in cat["families"]:
        d = dict(fam["derivation"])
        hue = d.pop("anchor_hue")
        res = derive(hue, name=fam["id"], **d)
        if res["unresolved"]:
            raise RuntimeError(f"{fam['id']}: unresolved role separation {res['unresolved']}")
        fam["roles"] = res["roles"]
        fam["dark"] = res["dark"]
        fam["measured"] = res["measured"]
    p.write_text(json.dumps(cat, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return cat


def _family_hue(fam: dict) -> float:
    return rgb_to_oklch(fam["roles"]["accent"])[2]


# ------------------------------------------------------------------ brief reading
def _stated_items(stated) -> List[dict]:
    if not stated:
        return []
    items = stated if isinstance(stated, list) else [stated]
    out = []
    for it in items:
        if isinstance(it, str):
            out.append({"quote": it, "words": []})
        elif isinstance(it, dict):
            out.append({"quote": str(it.get("quote") or " ".join(it.get("words") or [])),
                        "words": [str(w) for w in (it.get("words") or [])]})
    return out


def analyse(brief: dict) -> dict:
    texts = {k: str(brief.get(k) or "").lower() for k in ("subject", "audience", "tone")}
    scores: Dict[str, float] = {}
    hits: Dict[str, List[str]] = {}
    for mood, (_h, kws) in MOODS.items():
        for src, t in texts.items():
            for kw in kws:
                if t and _has(t, kw):
                    scores[mood] = scores.get(mood, 0.0) + SOURCE_WEIGHT[src]
                    hits.setdefault(mood, []).append(f"{src}:{kw}")
    stated = []
    stated_moods: List[str] = []
    stated_hues: List[Tuple[str, float]] = []
    excluded: List[Tuple[str, float]] = []
    skin = None
    for it in _stated_items(brief.get("stated")):
        q = it["quote"].lower()
        text = " ".join([q] + [w.lower() for w in it["words"]])
        applied, unmatched = [], []
        for word, hue in COLOUR_WORDS.items():
            m = re.search(r"(?<![a-z])" + re.escape(word) + r"(?![a-z])", text)
            if not m:
                continue
            pre = text[max(0, m.start() - 24):m.start()]
            if re.search(NEGATORS + r"(?:\s+\w+){0,2}\s*$", pre):
                excluded.append((word, float(hue)))
                applied.append(f"exclude {word} (~{hue}°)")
            else:
                stated_hues.append((word, float(hue)))
                applied.append(f"accent {word} (~{hue}°)")
        found = set()
        for w in re.findall(r"[a-z][a-z-]+", text):
            m = MOOD_ADJECTIVES.get(w) or (w if w in MOODS else None)
            if m and m not in found:
                found.add(m)
                stated_moods.append(m)
                scores[m] = scores.get(m, 0.0) + SOURCE_WEIGHT["stated"]
                hits.setdefault(m, []).append(f"stated:{w}")
                applied.append(f"mood {m} (from '{w}')")
        for w, s in SKIN_WORDS.items():
            if re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", text):
                skin = s
                applied.append(f"primary skin {s}")
        for w in it["words"]:
            wl = w.lower()
            if wl not in COLOUR_WORDS and wl not in MOODS and wl not in SKIN_WORDS and \
                    wl not in MOOD_ADJECTIVES:
                unmatched.append(w)
        stated.append({"quote": it["quote"], "applied": applied, "unmatched_words": unmatched})
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return {"scores": dict(ranked), "hits": hits, "ranked": [m for m, _ in ranked],
            "stated": stated, "stated_moods": sorted(set(stated_moods)),
            "stated_hues": stated_hues, "excluded_hues": excluded, "skin": skin,
            "all_text": " ".join(texts.values())}


# ------------------------------------------------------------------ history
def _load_history(n: int = REPEAT_WINDOW) -> Tuple[List[dict], str]:
    try:
        from design.history import recent_history  # type: ignore  # written by a sibling stream
        return list(recent_history(n)), "design.history.recent_history"
    except Exception:
        pass
    try:
        from memory.store import Store  # type: ignore
        return list(Store(create=False).recent_choices(n)), "memory.store.Store.recent_choices"
    except Exception as e:  # pragma: no cover - degrade honestly
        return [], f"unavailable ({type(e).__name__})"


WARRANT_USE_HITS = 3   # a family whose use-when the subject hits this often is "clearly called for"


def _penalty(cid: str, history: List[dict], warranted: Optional[str] = None) -> Tuple[float, str]:
    """Anti-repetition via the shared history module when present (SPEC §4 formula), else inline."""
    try:
        from design.history import penalty as hp  # type: ignore
        try:
            v, reason = hp("palette_family", cid, history, window=REPEAT_WINDOW, warranted=warranted)
        except TypeError:
            v, reason = hp("palette_family", cid, history, window=REPEAT_WINDOW)
            if v and warranted:
                v, reason = 0.0, f"repeat allowed because the subject warrants it: {warranted}"
        return -abs(float(v)), str(reason or "")
    except ImportError:
        pass
    recent = [h.get("palette_family") for h in history[:REPEAT_WINDOW] if isinstance(h, dict)]
    n = sum(1 for f in recent if f == cid)
    if n and warranted:
        return 0.0, f"repeat allowed because the subject warrants it: {warranted}"
    if n:
        return -REPEAT_PENALTY, (f"used on {n} of the last {REPEAT_WINDOW} builds "
                                 f"(anti-repetition -{REPEAT_PENALTY})")
    return 0.0, ""


def _default_seed(brief: dict) -> Tuple[int, int]:
    project = str(brief.get("project") or brief.get("subject") or "coordn8")
    count = 0
    try:
        from memory.store import Store  # type: ignore
        rows = Store(create=False).recent_choices(10 ** 6)
        count = sum(1 for r in rows if r.get("project") in (project, None))
    except Exception:
        pass
    try:
        from design.seed import derive_seed  # type: ignore  # shared H3 seed (slug + build count)
        return int(derive_seed(project, count)), count
    except Exception:
        pass
    h = hashlib.sha256(f"{project}|{count}".encode()).hexdigest()
    return int(h[:8], 16), count


# ------------------------------------------------------------------ choosing
def _family_fit(fam: dict, an: dict) -> Tuple[float, List[str], List[str], List[str]]:
    moods = [m for m in fam["mood"] if an["scores"].get(m)]
    raw = sum(an["scores"][m] for m in moods)
    uses = [u for u in fam.get("use_when", []) if _has(an["all_text"], u.lower())]
    avoids = [a for a in fam.get("avoid_when", []) if _has(an["all_text"], a.lower())]
    raw += 0.6 * len(uses) - 1.5 * len(avoids)
    return raw, moods, uses, avoids


def _obeys(accent_hue: float, moods: Sequence[str], an: dict) -> Tuple[bool, str]:
    for word, h in an["excluded_hues"]:
        if hue_distance(accent_hue, h) <= EXCLUDED_HUE_TOLERANCE:
            return False, f"the user ruled out {word} and this accent sits at {accent_hue:.0f}° (~{word})"
    if an["stated_hues"]:
        ok = any(hue_distance(accent_hue, h) <= STATED_HUE_TOLERANCE for _w, h in an["stated_hues"])
        if not ok:
            words = ", ".join(f"{w} (~{h:.0f}°)" for w, h in an["stated_hues"])
            return False, f"does not obey the stated colour: accent hue {accent_hue:.0f}° vs {words}"
    if an["stated_moods"]:
        if not set(moods) & set(an["stated_moods"]):
            return False, f"does not carry the stated mood ({', '.join(an['stated_moods'])})"
    return True, ""


INVENTED_TRUST = 0.9   # an unproven palette scores 0.9x the fit of the moods it carries


def _invented(an: dict, rng: random.Random, draws: list, picks: List[dict], tone_fam: dict,
              at_stated_hue: bool) -> dict:
    """An invented candidate: accent anchored on the stated colour (when no catalog family obeys
    it) or on the leading mood's hue anchor; paper/ink tone borrowed from `tone_fam`; rotated off
    the anchor only as far as needed to stay >= 20 CIEDE2000 from every other candidate."""
    if at_stated_hue:
        mood_name, base = an["stated_hues"][0]
        jitter = 0.0
    elif an["ranked"]:
        mood_name = an["ranked"][0]
        base = MOODS[mood_name][0]
        jitter = rng.uniform(-10, 10)
    else:
        mood_name = "seeded"
        base = rng.uniform(0, 360)
        jitter = 0.0
    draws.append({"draw": "invented_anchor_jitter", "value": round(jitter, 3)})
    params = {k: v for k, v in (tone_fam.get("derivation") or {}).items()
              if k in ("paper_hue", "paper_chroma", "paper_L", "dark_paper_L", "dark_paper_chroma",
                       "ink_hue", "ink_chroma")}
    # the best-scoring family is never displaced by an invented candidate — except by one that sits
    # on the user's stated colour (no catalog family obeys it, so nothing there needs protecting)
    protected = [] if at_stated_hue else picks[:1]

    def gap_to(cand, group):
        return min([delta_e(cand["roles"]["accent"], p["roles"]["accent"]) for p in group] + [99.0])

    def make(off):
        hue = (base + jitter + off) % 360
        if any(hue_distance(hue, h) <= EXCLUDED_HUE_TOLERANCE for _w, h in an["excluded_hues"]):
            return None
        return derive(hue, name=f"invented:{mood_name}-{int(round(hue))}",
                      chroma_policy="keep" if at_stated_hue else "rotate", **params)

    near = (0,) if at_stated_hue else (0, 25, -25, 50, -50)
    far = () if at_stated_hue else (75, -75, 100, -100, 140, 180)
    res, used, partial, tried = None, 0, None, []
    for off in near:                       # 1) near the anchor and distinct from every pick
        cand = make(off)
        if cand is None:
            continue
        tried.append((off, cand))
        if gap_to(cand, picks) >= CANDIDATE_MIN_DE:
            res, used = cand, off
            break
        if partial is None and gap_to(cand, protected) >= CANDIDATE_MIN_DE:
            partial = (off, cand)
    if res is None and partial is not None:  # 2) near the anchor; lesser picks give way
        used, res = partial
    if res is None:                          # 3) only then rotate further
        for off in far:
            cand = make(off)
            if cand is None:
                continue
            tried.append((off, cand))
            if gap_to(cand, protected) >= CANDIDATE_MIN_DE:
                res, used = cand, off
                break
    if res is None and tried:
        used, res = max(tried, key=lambda t: gap_to(t[1], protected))
    if res is None:  # every offset excluded: fall back to the opposite hue
        hue = (base + 180) % 360
        res, used = derive(hue, name=f"invented:{mood_name}-{int(round(hue))}", **params), 180
    res["mood_name"] = mood_name
    res["stated_anchor"] = at_stated_hue
    res["rotated_by"] = used
    res["tone_from"] = tone_fam.get("id")
    return res


def choose(brief: dict, *, catalog: Optional[dict] = None, history: Optional[List[dict]] = None,
           history_source: str = "given", seed: Optional[int] = None) -> dict:
    if not isinstance(brief, dict) or not str(brief.get("subject") or "").strip():
        raise ValueError("brief must be a JSON object with a non-empty 'subject'")
    cat = catalog or load_catalog()
    fams = cat["families"]
    if history is None:
        history, history_source = _load_history()
    if seed is None:
        seed, _count = _default_seed(brief)
    rng = random.Random(int(seed))
    draws: List[dict] = []
    an = analyse(brief)
    explicit = brief.get("palette") if isinstance(brief.get("palette"), dict) else None

    # ---- score catalog families
    rows = []
    for fam in fams:
        raw, moods, uses, avoids = _family_fit(fam, an)
        hue = _family_hue(fam)
        ok, why_not = _obeys(hue, fam["mood"], an)
        rows.append({"fam": fam, "id": fam["id"], "source": "catalog", "raw": raw, "moods": moods,
                     "uses": uses, "avoids": avoids, "hue": hue, "obeys": ok, "why_not": why_not,
                     "roles": fam["roles"], "dark": fam["dark"]})
    max_raw = max([r["raw"] for r in rows] + [0.0])
    no_signal = max_raw <= 0
    for r in rows:
        r["fit"] = 0.5 if no_signal else max(0.0, r["raw"]) / max_raw
        r["jitter"] = rng.uniform(0, 0.01)
        draws.append({"draw": f"tiebreak:{r['id']}", "value": round(r["jitter"], 6)})
        warrant = None
        if r["obeys"] and (an["stated_hues"] or an["stated_moods"]):
            warrant = "the user's stated colour/mood selects this family"
        elif len(r["uses"]) >= WARRANT_USE_HITS:
            warrant = (f"the subject clearly calls for it — it matches {len(r['uses'])} of its use-when cases "
                       f"({', '.join(r['uses'][:4])})")
        pen, pen_why = _penalty(r["id"], history, warranted=warrant)
        if warrant and pen_why.startswith("repeat"):
            r["penalty_waived"] = warrant
        r["penalty"], r["penalty_why"] = pen, pen_why
        r["score"] = r["fit"] + pen + r["jitter"]
    rows.sort(key=lambda r: (-r["score"], r["id"]))

    # ---- candidate set: >= 3 catalog accents >= 20 dE apart, + an invented one
    picks: List[dict] = []
    eligible = [r for r in rows if r["obeys"]]
    if (an["stated_hues"] or an["stated_moods"] or an["excluded_hues"]) and eligible:
        picks.append(eligible[0])
    for r in rows:
        if len([p for p in picks if p["source"] == "catalog"]) >= 3:
            break
        if r in picks:
            continue
        if all(delta_e(r["roles"]["accent"], p["roles"]["accent"]) >= CANDIDATE_MIN_DE for p in picks):
            picks.append(r)
    # paper/ink tone for the invented palette: the best family carrying a stated mood, else the best fit
    tone_rows = [r for r in rows if set(r["fam"]["mood"]) & set(an["stated_moods"])] or rows
    tone_fam = tone_rows[0]["fam"]
    at_stated = bool(an["stated_hues"]) and not any(r["obeys"] for r in rows)
    inv = _invented(an, rng, draws, picks, tone_fam, at_stated)
    inv_hue = inv["anchor_hue"]
    # catalog picks (other than the protected top pick) that the invented one collides with give way
    top = [] if at_stated else picks[:1]
    picks = top + [p for p in picks[len(top):]
                   if delta_e(p["roles"]["accent"], inv["roles"]["accent"]) >= CANDIDATE_MIN_DE]
    for r in rows:
        if len(picks) >= 3:
            break
        accents = [p["roles"]["accent"] for p in picks] + [inv["roles"]["accent"]]
        if r not in picks and all(delta_e(r["roles"]["accent"], x) >= CANDIDATE_MIN_DE for x in accents):
            picks.append(r)
    inv_moods = sorted({m for m in tone_fam["mood"] if an["scores"].get(m)}
                       | ({inv["mood_name"]} if inv["mood_name"] in MOODS else set()))
    inv_ok, inv_why = _obeys(inv_hue, inv_moods, an)
    tone_row = next(r for r in rows if r["fam"] is tone_fam)
    inv_raw = max(tone_row["raw"], sum(an["scores"].get(m, 0.0) for m in inv_moods))
    rot = 1.0 - min(abs(inv["rotated_by"]), 90) / 360.0   # moved off its anchor: up to -25% fit
    if no_signal:
        inv_fit = 0.5 * INVENTED_TRUST
    else:
        inv_fit = INVENTED_TRUST * rot * max(0.0, inv_raw) / max_raw
    if at_stated:
        inv_fit = max(inv_fit, INVENTED_TRUST)   # the only palette that obeys the user's colour
    inv_jit = rng.uniform(0, 0.01)
    draws.append({"draw": f"tiebreak:{inv['name']}", "value": round(inv_jit, 6)})
    inv_pen, inv_pen_why = _penalty(inv["name"], history)
    inv_row = {"id": inv["name"], "source": "invented", "raw": inv_raw, "fit": inv_fit,
               "moods": inv_moods, "uses": [], "avoids": [], "hue": inv_hue, "obeys": inv_ok,
               "why_not": inv_why, "roles": inv["roles"], "dark": inv["dark"], "jitter": inv_jit,
               "penalty": inv_pen, "penalty_why": inv_pen_why,
               "score": inv_fit + inv_pen + inv_jit, "derivation": inv}
    cands = picks + [inv_row]

    # ---- winner
    chosen_because = ""
    winner = None
    if explicit:
        winner, chosen_because = _explicit_choice(explicit, fams, cands, an)
    if winner is None:
        pool = [c for c in cands if c["obeys"]]
        if not pool:  # nothing obeys (only possible with contradictory statements) -> invented
            pool = [inv_row]
        winner = max(pool, key=lambda c: (c["score"], c["id"]))
        chosen_because = _why_chosen(winner, cands, an)

    # anti-repetition record
    rep_notes = []
    for c in cands:
        if c.get("penalty"):
            rep_notes.append(f"{c['id']}: {c['penalty_why']}")
        if c.get("penalty_waived"):
            rep_notes.append(f"{c['id']}: repetition penalty waived — {c['penalty_waived']}")
    if winner.get("penalty"):
        alts = [c for c in cands if c is not winner and c["obeys"]]
        best_alt = max([c["score"] for c in alts] + [0.0])
        rep_notes.append(f"{winner['id']} kept despite recent use: even after the {winner['penalty']:+.2f} repetition "
                         f"penalty it scores {winner['score']:.3f} against the best alternative's {best_alt:.3f} "
                         f"(subject fit {winner['fit']:.2f}) — the subject clearly calls for it")

    roles, dark = dict(winner["roles"]), dict(winner["dark"])
    family = winner["id"]
    brand_rec = None
    if brief.get("brand_colours"):
        roles, dark, family, brand_rec = _apply_brand(brief["brand_colours"], winner, an)

    # ---- rejections
    rejected = []
    for c in cands:
        if c is winner:
            continue
        rejected.append({"id": c["id"], "source": c["source"], "why": _why_rejected(c, winner)})
    for r in rows:
        if r not in cands and not r["obeys"] and r["why_not"]:
            rejected.append({"id": r["id"], "source": "catalog", "why": r["why_not"]})

    sections = _sections(brief.get("sections") or [], roles, dark)
    quotes = []
    for s in an["stated"]:
        if s["applied"]:
            quotes.append(s["quote"])
    result = {
        "family": family,
        "source": "brand" if brand_rec else winner["source"],
        "invented": winner["source"] == "invented" or bool(brand_rec) or family.startswith("invented"),
        "based_on": winner["id"] if brand_rec else None,
        "mood": an["ranked"][:4],
        "mood_scores": an["scores"],
        "primary_skin": an["skin"] or (winner["fam"].get("primary_skin", "light") if winner.get("fam") else "light"),
        "roles": roles,
        "dark": dark,
        "chosen_because": chosen_because,
        "candidates": [_cand_summary(c) for c in cands],
        "rejected": rejected,
        "obeyed_user_quotes": quotes,
        "stated": an["stated"],
        "brand": brand_rec,
        "anti_repetition": {"history_source": history_source,
                            "recent_families": [h.get("palette_family") for h in history[:REPEAT_WINDOW]
                                                if isinstance(h, dict)],
                            "notes": rep_notes},
        "history_penalty_applied": any(c.get("penalty") for c in cands),
        "chart_series": chart_series(roles, dark),
        "accent_budget": ds.ACCENT_BUDGET,
        "sections": sections,
        "measured": measure(roles, dark),
        "seed": int(seed),
        "draws": draws,
    }
    if winner["source"] == "invented":
        d = winner["derivation"]
        result["derivation"] = {"method": "OKLCH ladders from a hue anchor", "anchor_hue": d["anchor_hue"],
                                "anchored_on": d["mood_name"], "accent_chroma": d["accent_chroma"],
                                "tone_from": d.get("tone_from"), "rotated_by": d.get("rotated_by", 0),
                                "steps": d["log"], "unresolved": d["unresolved"]}
    if explicit and explicit.get("reason"):
        result["agent_reason"] = str(explicit["reason"])
    return result


def _cand_summary(c: dict) -> dict:
    return {"id": c["id"], "source": c["source"], "accent": c["roles"]["accent"],
            "accent_dark": c["dark"]["accent"], "accent_hue": round(c["hue"], 1),
            "fit": round(c["fit"], 3), "penalty": round(c.get("penalty", 0.0), 3),
            "score": round(c["score"], 4), "moods_matched": c["moods"], "use_when_hits": c["uses"],
            "avoid_when_hits": c["avoids"], "obeys_stated": c["obeys"]}


def _why_chosen(w: dict, cands: List[dict], an: dict) -> str:
    bits = []
    if w["moods"]:
        bits.append("moods matched: " + ", ".join(w["moods"]))
    if w["uses"]:
        bits.append("subject fits its use-when: " + ", ".join(w["uses"]))
    if w["source"] == "invented":
        d = w["derivation"]
        bits.append(f"invented in OKLCH from the {d.get('mood_name')} hue anchor ({w['hue']:.0f}°)"
                    + (f", paper/ink tone from {d['tone_from']}" if d.get("tone_from") else ""))
    if an["stated_hues"] or an["stated_moods"]:
        bits.append("obeys what the user stated")
    if not an["ranked"]:
        bits.append("no mood signal in the brief — chosen by the recorded seed")
    bits.append(f"score {w['score']:.3f} (fit {w['fit']:.2f}, repetition {w.get('penalty', 0.0):+.2f})")
    return "; ".join(bits)


def _why_rejected(c: dict, w: dict) -> str:
    if not c["obeys"]:
        return c["why_not"]
    parts = [f"scored {c['score']:.3f} vs the chosen {w['score']:.3f}"]
    if c.get("penalty"):
        parts.append(c["penalty_why"])
    if c["avoids"]:
        parts.append("subject hits its avoid-when: " + ", ".join(c["avoids"]))
    if c["source"] == "catalog":
        missing = [m for m in w["moods"] if m not in c["moods"]]
        if missing:
            parts.append("does not carry " + ", ".join(missing))
        elif not c["moods"]:
            parts.append("none of its moods appear in the brief")
    else:
        parts.append("an invented palette is kept only when no proven family fits better")
    return "; ".join(parts)


def _explicit_choice(explicit: dict, fams: List[dict], cands: List[dict], an: dict):
    reason = str(explicit.get("reason") or "").strip() or "agent choice (no reason given)"
    if explicit.get("family"):
        fid = str(explicit["family"])
        for c in cands:
            if c["id"] == fid:
                return c, f"agent chose {fid}: {reason}"
        fam = next((f for f in fams if f["id"] == fid), None)
        if fam is not None:
            row = {"id": fid, "source": "catalog", "fam": fam, "roles": fam["roles"], "dark": fam["dark"],
                   "hue": _family_hue(fam), "fit": 0.0, "score": 0.0, "moods": [], "uses": [], "avoids": [],
                   "obeys": True, "why_not": "", "penalty": 0.0}
            cands.insert(0, row)
            return row, f"agent chose {fid}: {reason}"
        return None, ""
    name = str(explicit.get("invented") or "custom")
    name = name if name.startswith("invented:") else f"invented:{name}"
    if isinstance(explicit.get("roles"), dict) and isinstance(explicit.get("dark"), dict):
        roles, dark = dict(explicit["roles"]), dict(explicit["dark"])
        d = {"anchor_hue": round(rgb_to_oklch(roles.get("accent", "#888888"))[2], 1), "mood_name": "agent",
             "accent_chroma": round(rgb_to_oklch(roles.get("accent", "#888888"))[1], 4),
             "log": [{"step": "supplied", "note": "roles supplied by the agent; judged only by the gate"}],
             "unresolved": []}
    else:
        if explicit.get("accent"):
            hue = rgb_to_oklch(str(explicit["accent"]))[2]
        else:
            hue = float(explicit.get("anchor_hue", 200))
        res = derive(hue, name=name)
        roles, dark = res["roles"], res["dark"]
        d = res
        d["mood_name"] = "agent"
    row = {"id": name, "source": "invented", "roles": roles, "dark": dark, "hue": d["anchor_hue"],
           "fit": 0.0, "score": 0.0, "moods": [], "uses": [], "avoids": [], "obeys": True, "why_not": "",
           "penalty": 0.0, "derivation": d}
    cands.append(row)
    return row, f"agent invented {name}: {reason}"


def _apply_brand(brand: Sequence[str], base: dict, an: dict):
    colours = [str(c) for c in brand if parse_color(str(c))]
    accent = to_hex(parse_color(colours[0])[:3])
    link = to_hex(parse_color(colours[1])[:3]) if len(colours) > 1 else None
    fam = base.get("fam") or {}
    dparams = dict(fam.get("derivation") or {})
    dparams.pop("anchor_hue", None)
    for k in ("accent_chroma", "accent_L"):
        dparams.pop(k, None)
    hue = rgb_to_oklch(accent)[2]
    probe = derive(hue, name="probe", **dparams)
    adjustments = []
    light_acc, adj = adjust_for_contrast(accent, probe["roles"]["paper"])
    if adj:
        adjustments.append(dict(adj, skin="light", role="accent"))
    dark_acc, adj = adjust_for_contrast(accent, probe["dark"]["paper"])
    if adj:
        adjustments.append(dict(adj, skin="dark", role="accent"))
    res = derive(hue, name=f"invented:brand-{accent[1:]}", accent_override={"light": light_acc, "dark": dark_acc},
                 link_hint=link, **dparams)
    roles, dark = res["roles"], res["dark"]
    unused = colours[2:]
    rec = {"given": colours, "accent": {"given": accent, "light": light_acc, "dark": dark_acc,
                                         "kept_exactly": {"light": light_acc == accent, "dark": dark_acc == accent}},
           "link": {"given": link, "light": roles["link"], "dark": dark["link"]} if link else None,
           "adjustments": adjustments,
           "not_used": [{"colour": c, "why": "no-rainbow rule: one accent (+ link) carries the brand; "
                                             "further brand colours become muted variants or are left out"}
                        for c in unused],
           "base_family": base["id"],
           "anti_repetition": "not applied to the accent: a brand colour is mandated by the user",
           "unresolved": res["unresolved"]}
    if an["stated_hues"]:
        rec["stated_colour_note"] = "an explicit brand hex takes precedence over colour words"
    return roles, dark, f"invented:brand-{accent[1:]}", rec


def _sections(sections: Sequence[dict], roles: dict, dark: dict) -> dict:
    out = {}
    for s in sections:
        if not isinstance(s, dict) or not s.get("id"):
            continue
        col = str(s.get("colour") or s.get("color") or "").strip().lower()
        if col.startswith("#") or col.startswith("rgb"):
            L, C, hue = rgb_to_oklch(col)
            C = max(C, MIN_ACCENT_CHROMA)
        elif col in COLOUR_WORDS:
            hue, C = float(COLOUR_WORDS[col]), 0.14
        else:
            out[str(s["id"])] = {"error": f"unknown colour {col!r}", "declared": s.get("quote")}
            continue
        res = {}
        for skin, r, L0, light in (("light", roles, 0.52, True), ("dark", dark, 0.76, False)):
            v = _fit_contrast([L0, C, hue], r["paper"], light)
            hx = _lch_hex(v)
            # keep a declared section accent distinguishable from every page/status colour
            for _ in range(12):
                near = [k for k in ("link", "positive", "negative", "warning", "ink", "muted")
                        if delta_e(hx, r[k]) < DERIVE_SEPARATION]
                if not near:
                    break
                v = _fit_contrast([v[0] + (-0.04 if light else 0.04), v[1], v[2]], r["paper"], light)
                hx = _lch_hex(v)
            res[skin] = hx
        out[str(s["id"])] = {"accent": res["light"], "dark_accent": res["dark"], "declared": s.get("quote") or col}
    return out


# ------------------------------------------------------------------ CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Coordn8 palette director")
    ap.add_argument("brief", nargs="?")
    ap.add_argument("--build", help="build dir holding design_system.json (key 'palette' is written)")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--history", help="JSON file: list of past choices ({palette_family: ...}), newest first")
    ap.add_argument("--no-history", action="store_true")
    ap.add_argument("--catalog")
    ap.add_argument("--rederive-catalog", action="store_true")
    a = ap.parse_args(argv)
    if a.rederive_catalog:
        cat = rederive_catalog(Path(a.catalog) if a.catalog else None)
        print(json.dumps({"rederived": [f["id"] for f in cat["families"]]}))
        return 0
    if not a.brief:
        ap.print_usage(sys.stderr)
        return 2
    try:
        brief = json.loads(Path(a.brief).read_text(encoding="utf-8"))
        history = None
        src = "given"
        if a.no_history:
            history, src = [], "disabled"
        elif a.history:
            history = json.loads(Path(a.history).read_text(encoding="utf-8"))
            src = f"file:{a.history}"
        cat = load_catalog(Path(a.catalog)) if a.catalog else None
        res = choose(brief, catalog=cat, history=history, history_source=src, seed=a.seed)
    except (ValueError, OSError, json.JSONDecodeError) as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}))
        return 2
    if a.build:
        ds.update(a.build, "palette", res)
        res_path = str(ds.path_for(a.build))
    else:
        res_path = None
    if a.json:
        print(json.dumps(res, indent=1, ensure_ascii=False))
    else:
        print(f"palette: {res['family']}  ({res['chosen_because']})")
        for r in res["rejected"][:6]:
            print(f"  rejected {r['id']}: {r['why']}")
        if res["obeyed_user_quotes"]:
            print("  obeyed: " + " | ".join(res["obeyed_user_quotes"]))
        if res["brand"] and res["brand"]["adjustments"]:
            for adj in res["brand"]["adjustments"]:
                print(f"  brand adjusted ({adj['skin']}): {adj['why']}")
        if res_path:
            print(f"  wrote {res_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
