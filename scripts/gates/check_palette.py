#!/usr/bin/env python3
"""Palette and contrast gate (requirement A2). ADVISORY: exit 0 PASS, 1 REVIEW, 2 SKIPPED, 3 ERROR.

    python3 scripts/gates/check_palette.py <design_system.json> [--elements trace.json]
                                           [--html page.html|scene.svg ...] [--json]

It judges OUTCOMES only — what is painted, never where a palette came from. A palette the
agent invented passes exactly when its colours pass.

Palette checks (always, both skins):
  role-contrast        a text-carrying role below 4.5:1 on its own paper
  role-separation      two roles of DISTINCT meaning closer than 10 CIEDE2000 (they read as one)
  no-dark-skin         no dark role set, so contrast "in both skins" cannot hold
Scene checks (elements JSON and/or HTML/SVG):
  text-contrast        text below 4.5:1 on what it actually sits on (large text, >= 24 px or
                       >= 18.66 px bold, below 3:1); translucent colours composited first
  mark-contrast        an element marked essential:true (line, arrow, data point) below 3:1
  accent-budget        more than 2 accent elements in one frame (a focal_group counts once)
  off-palette          a colour that is not a declared role — role colours at any alpha, and
                       role colours flattened over the paper, are declared; ΔE < 2 is a match
  rainbow              more than 4 distinct chromatic hues on marks/fills in one frame
                       (the declared source-of-truth legend roles are one labelled system)
  hue-only-meaning     positive vs negative marks that differ ONLY by hue — same fill weight,
                       same shape/dash/icon, no sign or label difference (fails in greyscale/CVD)
  role-meaning-mismatch an element that declares a meaning but is painted in the colour of a
                       different meaning (roles have FIXED meanings)
  legend-tone-by-lightness  a legend naming a ramp by lightness ("darker is larger"): true on
                       one skin, false on the other; say "stronger"/"weaker"

Elements JSON: a list, {"elements": [...]}, or {"frames": [{"id", "elements": [...]}]}. Element:
  {id, kind: text|mark|fill, color, bg?, font_px?, bold?, accent?, frame?, skin?, meaning?,
   fill_weight? (0..1 | "solid" | "tint" | "hollow"), shape?, dash?, icon?, label?, text?,
   focal_group?, essential?}
`bg` may be a colour, a role name, or "role@alpha" (e.g. "accent@0.1").
HTML/SVG: inline style colours, fill/stroke/stop-color/color attributes, <style> blocks and
CSS custom properties (var(--x)) are read; data-frame, data-accent, data-meaning,
data-fill-weight, data-focal-group, data-label, data-icon, data-skin are honoured.
<style> blocks are read declaration by declaration: a custom property DECLARES a colour (judged for
membership only — declaring a palette paints nothing); a paint property is resolved through var()
and judged as the colour it paints. Declared derivations: a role at an alpha, color-mix() whose every
operand is a role or transparent. A literal fallback after a declared role variable, a named colour,
hsl(), or a colour function the gate cannot evaluate is judged too — never skipped.
Zero third-party deps; Python 3.9+.
"""
from __future__ import annotations

import argparse
import itertools
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPTS))
from lib.common import EXIT_ERROR, EXIT_PASS, EXIT_REVIEW, EXIT_SKIPPED, emit  # noqa: E402
from design import design_system as ds  # noqa: E402
from design.color import (COLOR_TOKEN, NON_COLOURS, ciede2000, composite, contrast,  # noqa: E402
                          rgb_to_lab, rgb_to_oklch, to_hex, try_parse)

GATE = "palette"
RAINBOW_MAX_HUES = 4
CHROMATIC_MIN = 0.05          # OKLCH chroma under this is a neutral (ink/grey), not a hue
HUE_CLUSTER_DE = 10.0
FILL_WEIGHT_DIFF = 0.15
LEGEND_ROLES = ("deterministic", "model-judgement", "data-field", "vision")
ALPHA_STEPS = [round(0.02 * i, 2) for i in range(1, 50)]

TONE_LUMINANCE = r"darker|darkest|deeper|deepest|lighter|lightest|paler|palest"
MAGNITUDE = (r"largest|larger|biggest|bigger|greatest|greater|highest|higher|longest|longer|most|more|"
             r"smallest|smaller|fewest|fewer|lowest|lower|shortest|shorter|lesser|least|less|value|share|count")
LEGEND_CLAIM = re.compile(r"\b(?:" + TONE_LUMINANCE + r")\b(?:\W+\w+){0,6}?\W+(?:" + MAGNITUDE + r")\b"
                          r"|\b(?:" + MAGNITUDE + r")\b(?:\W+\w+){0,6}?\W+(?:" + TONE_LUMINANCE + r")\b",
                          re.IGNORECASE)
SIGN = re.compile(r"[+−–\-↑↓▲▼]|\b(?:up|down|gain|loss|rise|fall|increase|decrease)\b",
                  re.IGNORECASE)


# ------------------------------------------------------------------ declared colours
class Declared:
    """Every colour the design system declares, and how to recognise it when painted."""

    def __init__(self, pal: dict):
        self.skins: Dict[str, dict] = {}
        for skin, key in (("light", "roles"), ("dark", "dark")):
            roles = pal.get(key)
            if isinstance(roles, dict):
                parsed = {}
                for name, val in roles.items():
                    c = try_parse(val) if isinstance(val, str) else None
                    if c is not None:
                        parsed[name] = c
                self.skins[skin] = parsed
        for sid, sec in (pal.get("sections") or {}).items():
            if not isinstance(sec, dict):
                continue
            for skin, k in (("light", "accent"), ("dark", "dark_accent")):
                c = try_parse(sec.get(k)) if sec.get(k) else None
                if c is not None and skin in self.skins:
                    self.skins[skin][f"section:{sid}"] = c
        self.raw: List[Tuple[str, str, tuple]] = []
        self.flat: List[Tuple[str, str, float, tuple]] = []
        for skin, roles in self.skins.items():
            paper = roles.get(ds.PAPER)
            for name, c in roles.items():
                self.raw.append((name, skin, rgb_to_lab(c[:3])))
                if paper is not None and name != ds.PAPER:
                    for a in ALPHA_STEPS:
                        self.flat.append((name, skin, a, rgb_to_lab(composite(c[:3], paper[:3], a))))
        self._cache: Dict[tuple, Optional[dict]] = {}

    def paper(self, skin: str) -> Optional[tuple]:
        return (self.skins.get(skin) or {}).get(ds.PAPER)

    def role(self, skin: str, name: str) -> Optional[tuple]:
        return (self.skins.get(skin) or {}).get(name)

    def match(self, c: tuple) -> Optional[dict]:
        """{'role','skin','alpha','de'} for the declared colour this paint is, or None."""
        key = tuple(round(v, 3) for v in c)
        if key in self._cache:
            return self._cache[key]
        lab = rgb_to_lab(c[:3])
        best = None
        for name, skin, rlab in self.raw:
            d = ciede2000(lab, rlab)
            if d < ds.NEAR_MATCH_DE and (best is None or d < best["de"]):
                best = {"role": name, "skin": skin, "alpha": c[3], "de": round(d, 2), "how": "role"}
        if best is None and c[3] >= 0.999:
            for name, skin, a, flab in self.flat:
                d = ciede2000(lab, flab)
                if d < ds.NEAR_MATCH_DE and (best is None or d < best["de"]):
                    best = {"role": name, "skin": skin, "alpha": a, "de": round(d, 2), "how": "flattened"}
        self._cache[key] = best
        return best


# ------------------------------------------------------------------ palette-level checks
def palette_findings(pal: dict, dec: Declared) -> Tuple[List[dict], dict]:
    out: List[dict] = []
    measured: Dict[str, dict] = {}
    if "dark" not in dec.skins:
        out.append({"check": "no-dark-skin", "detail": "the palette has no dark role set; contrast in both skins "
                                                        "cannot be shown"})
    for skin, roles in dec.skins.items():
        paper = roles.get(ds.PAPER)
        if paper is None:
            out.append({"check": "role-contrast", "skin": skin, "detail": f"{skin}: no paper role"})
            continue
        ratios = {}
        names = [r for r in ds.TEXT_ROLES if r in roles] + [r for r in roles if r.startswith("section:")]
        for r in names:
            ratio = contrast(roles[r], paper[:3])
            ratios[r] = round(ratio, 2)
            if ratio < ds.TEXT_MIN:
                out.append({"check": "role-contrast", "skin": skin, "role": r, "ratio": round(ratio, 2),
                            "detail": f"{skin}: role '{r}' {to_hex(roles[r][:3])} is {ratio:.2f}:1 on paper "
                                      f"{to_hex(paper[:3])}, below {ds.TEXT_MIN}:1"})
        seps = {}
        seen = set()
        for g in ds.SEPARATION_GROUPS:
            for a, b in itertools.combinations(g, 2):
                if a in roles and b in roles and (a, b) not in seen:
                    seen.add((a, b))
                    d = ciede2000(rgb_to_lab(roles[a][:3]), rgb_to_lab(roles[b][:3]))
                    seps[f"{a}~{b}"] = round(d, 2)
                    if d < ds.MIN_ROLE_SEPARATION:
                        out.append({"check": "role-separation", "skin": skin, "roles": [a, b],
                                    "delta_e": round(d, 2),
                                    "detail": f"{skin}: '{a}' and '{b}' carry different meanings but are only "
                                              f"{d:.1f} CIEDE2000 apart (need >= {ds.MIN_ROLE_SEPARATION})"})
        measured[skin] = {"min_text_contrast": min(ratios.values()) if ratios else None,
                          "min_separation": min(seps.values()) if seps else None}
    return out, measured


# ------------------------------------------------------------------ elements
def _num(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _weight(v) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().lower()
    return {"solid": 1.0, "filled": 1.0, "full": 1.0, "tint": 0.15, "light": 0.15, "hollow": 0.0,
            "none": 0.0, "outline": 0.0, "empty": 0.0}.get(s, _num(s))


def _resolve_bg(bg, dec: Declared, skin: str) -> Optional[tuple]:
    paper = dec.paper(skin)
    if bg is None or str(bg).strip() == "":
        return paper
    s = str(bg).strip()
    m = re.match(r"^([a-z][a-z0-9:_-]*)(?:@([0-9.]+))?$", s)
    if m and dec.role(skin, m.group(1)) is not None:
        c = dec.role(skin, m.group(1))
        a = float(m.group(2)) if m.group(2) else 1.0
        if paper is None:
            return c
        return tuple(composite(c[:3], paper[:3], a * c[3])) + (1.0,)
    c = try_parse(s)
    if c is None:
        return paper
    if c[3] < 1 and paper is not None:
        return tuple(composite(c[:3], paper[:3], c[3])) + (1.0,)
    return c


def _infer_skin(el: dict, c: Optional[tuple], dec: Declared, doc_skin: Optional[str]) -> str:
    s = str(el.get("skin") or "").lower()
    if s in dec.skins:
        return s
    if doc_skin in dec.skins:
        return doc_skin  # type: ignore
    if c is not None:
        m = dec.match(c)
        if m and m["role"] != ds.PAPER:
            return m["skin"]
    bgc = try_parse(el.get("bg")) if el.get("bg") else None
    if bgc is not None and "dark" in dec.skins:
        return "dark" if rgb_to_oklch(bgc[:3])[0] < 0.5 else "light"
    return "light" if "light" in dec.skins else next(iter(dec.skins), "light")


def element_findings(elements: List[dict], dec: Declared, doc_skin: Optional[str] = None) -> Tuple[List[dict], dict]:
    out: List[dict] = []
    frames: Dict[str, List[dict]] = {}
    unresolved: List[dict] = []
    for i, el in enumerate(elements):
        if not isinstance(el, dict):
            continue
        el = dict(el)
        el.setdefault("id", f"el{i}")
        el["frame"] = str(el.get("frame") or "frame")
        kind = str(el.get("kind") or "mark").lower()
        el["kind"] = kind
        raw = el.get("color")
        text = str(el.get("text") or el.get("label") or "")
        if kind == "text" and text and LEGEND_CLAIM.search(text):
            out.append({"check": "legend-tone-by-lightness", "id": el["id"], "frame": el["frame"],
                        "detail": f"{el['id']}: '{text[:80]}' names the ramp by lightness, which inverts between "
                                  f"the light and dark skins; say 'stronger'/'weaker' (contrast with the paper)"})
        if raw is None or str(raw).strip().lower() in NON_COLOURS:
            frames.setdefault(el["frame"], []).append(el)
            continue
        c = parse_colour(str(raw))
        if c is None:
            if el.get("opaque_colour"):
                # a colour function the gate cannot evaluate (lab(), oklch(), color(), ...): it cannot be
                # shown to be a role, so it is not one
                out.append({"check": "off-palette", "id": el["id"], "frame": el["frame"], "color": str(raw),
                            "detail": f"{el['id']}: {raw} is a colour the gate cannot resolve to a declared role; "
                                      f"paint with var(--c8-<role>) or a color-mix of roles"})
            else:
                unresolved.append({"id": el["id"], "color": str(raw)})
            continue
        el["_c"] = c
        if c[3] <= 0.0:
            continue
        skin = _infer_skin(el, c, dec, doc_skin)
        el["_skin"] = skin
        mix = el.get("mix_of")
        if mix:
            # a documented derivation: color-mix of declared roles (or of a role and transparent) is
            # declared exactly when EVERY operand is — the mixed result itself is never trusted alone
            bad_ops, roles = [], []
            for op in mix:
                oc = parse_colour(str(op))
                if oc is not None and oc[3] <= 0.0:
                    roles.append("transparent")
                    continue
                om = dec.match(oc) if oc is not None else None
                if om is None:
                    bad_ops.append(str(op))
                else:
                    roles.append(om["role"])
            m = None if bad_ops else (dec.match(c) or {"role": "mix(" + "+".join(roles) + ")", "skin": skin,
                                                        "alpha": c[3], "de": 0.0, "how": "mix"})
        else:
            bad_ops = []
            m = dec.match(c)
        el["_match"] = m
        if m is None:
            what = (f"mixes {', '.join(bad_ops)}, which is not a declared role colour" if bad_ops else
                    f"is not a declared role colour (nor a role at some alpha, nor within {ds.NEAR_MATCH_DE} "
                    f"CIEDE2000 of one)")
            out.append({"check": "off-palette", "id": el["id"], "frame": el["frame"], "color": str(raw),
                        "detail": f"{el['id']}: {el.get('source_value') or raw} {what}"})
        bg = _resolve_bg(el.get("bg"), dec, skin)
        if kind == "text" and bg is not None:
            ratio = contrast(c, bg[:3])
            px = _num(el.get("font_px"), 16.0)
            bold = bool(el.get("bold")) or str(el.get("weight") or "") in ("bold", "600", "700", "800", "900")
            large = px >= ds.LARGE_PX or (bold and px >= ds.LARGE_BOLD_PX)
            need = ds.LARGE_TEXT_MIN if large else ds.TEXT_MIN
            if ratio < need:
                out.append({"check": "text-contrast", "id": el["id"], "frame": el["frame"], "ratio": round(ratio, 2),
                            "need": need, "font_px": px, "bold": bold,
                            "detail": f"{el['id']}: text {raw} on {to_hex(bg[:3])} is {ratio:.2f}:1, below "
                                      f"{need}:1 ({'large' if large else 'body'} text, {px:g}px{' bold' if bold else ''})"})
        if kind in ("mark", "fill") and el.get("essential") and bg is not None:
            ratio = contrast(c, bg[:3])
            if ratio < ds.LARGE_TEXT_MIN:
                out.append({"check": "mark-contrast", "id": el["id"], "frame": el["frame"], "ratio": round(ratio, 2),
                            "detail": f"{el['id']}: essential mark {raw} is {ratio:.2f}:1 against "
                                      f"{to_hex(bg[:3])}, below 3:1"})
        meaning = str(el.get("meaning") or "").strip().lower()
        if meaning and meaning in ds.MEANING_ROLE and m is not None:
            want = ds.MEANING_ROLE[meaning]
            got = m["role"]
            semantic = set(ds.MEANING_ROLE.values())
            if got in semantic and got != want:
                want_c = dec.role(m["skin"], want)
                same = want_c is not None and ciede2000(rgb_to_lab(want_c[:3]), rgb_to_lab(c[:3])) < ds.NEAR_MATCH_DE
                if not same:
                    out.append({"check": "role-meaning-mismatch", "id": el["id"], "frame": el["frame"],
                                "detail": f"{el['id']}: declares '{meaning}' but is painted in the '{got}' colour; "
                                          f"roles keep fixed meanings — use the '{want}' role or a neutral"})
        frames.setdefault(el["frame"], []).append(el)
    counts = {}
    for fid, els in frames.items():
        out += _frame_findings(fid, els, dec)
        counts[fid] = len(els)
    return out, {"frames": counts, "unresolved_colours": unresolved}


def _is_accent(el: dict) -> bool:
    if el.get("accent") is True:
        return True
    m = el.get("_match")
    return bool(m and (m["role"] == "accent" or m["role"].startswith("section:")))


def _polarity(el: dict) -> int:
    meaning = str(el.get("meaning") or "").strip().lower()
    role = ds.MEANING_ROLE.get(meaning)
    if role in ds.POLARITY:
        return ds.POLARITY[role]
    m = el.get("_match")
    if m and m["role"] in ds.POLARITY:
        return ds.POLARITY[m["role"]]
    return 0


def _frame_findings(fid: str, els: List[dict], dec: Declared) -> List[dict]:
    out: List[dict] = []
    # accent budget
    keys = []
    for el in els:
        if _is_accent(el):
            k = str(el.get("focal_group") or el["id"])
            if k not in keys:
                keys.append(k)
    if len(keys) > ds.ACCENT_BUDGET:
        out.append({"check": "accent-budget", "frame": fid, "count": len(keys), "ids": keys,
                    "detail": f"frame '{fid}': {len(keys)} accent elements ({', '.join(keys[:6])}); the accent "
                              f"marks at most {ds.ACCENT_BUDGET} focal things per frame"})
    # rainbow
    clusters: List[tuple] = []
    for el in els:
        c = el.get("_c")
        if c is None or el["kind"] not in ("mark", "fill"):
            continue
        m = el.get("_match")
        if m and m["role"] in LEGEND_ROLES:
            continue
        if rgb_to_oklch(c[:3])[1] < CHROMATIC_MIN:
            continue
        lab = rgb_to_lab(c[:3])
        if not any(ciede2000(lab, x) < HUE_CLUSTER_DE for x in clusters):
            clusters.append(lab)
    if len(clusters) > RAINBOW_MAX_HUES:
        out.append({"check": "rainbow", "frame": fid, "hues": len(clusters),
                    "detail": f"frame '{fid}': {len(clusters)} distinct hues on marks/fills; carry series with one "
                              f"ink at stepped opacity (stronger/weaker) and keep hue for meaning"})
    # meaning by hue alone
    pos = [e for e in els if e["kind"] in ("mark", "fill") and _polarity(e) > 0]
    neg = [e for e in els if e["kind"] in ("mark", "fill") and _polarity(e) < 0]
    bad_pairs = []
    for p in pos:
        for n in neg:
            if not _distinct_beyond_hue(p, n):
                bad_pairs.append([p["id"], n["id"]])
    if bad_pairs:
        out.append({"check": "hue-only-meaning", "frame": fid, "pairs": bad_pairs[:10],
                    "detail": f"frame '{fid}': positive vs negative told apart by hue alone ({bad_pairs[0][0]} vs "
                              f"{bad_pairs[0][1]}); add fill weight (solid vs hollow), a sign/label, an icon, "
                              f"a shape or a dash so it survives greyscale and colour-vision deficiency"})
    return out


def _distinct_beyond_hue(a: dict, b: dict) -> bool:
    wa, wb = _weight(a.get("fill_weight")), _weight(b.get("fill_weight"))
    if wa is None:
        wa = a.get("_c", (0, 0, 0, 1.0))[3]
    if wb is None:
        wb = b.get("_c", (0, 0, 0, 1.0))[3]
    if abs(wa - wb) >= FILL_WEIGHT_DIFF:
        return True
    for k in ("shape", "dash", "icon"):
        if str(a.get(k) or "") != str(b.get(k) or ""):
            return True
    la, lb = str(a.get("label") or ""), str(b.get("label") or "")
    if (SIGN.search(la) or SIGN.search(lb)) and la != lb:
        return True
    return False


# ------------------------------------------------------------------ HTML / SVG reader
_VOID = {"br", "img", "input", "meta", "link", "hr", "area", "base", "col", "embed", "source", "track", "wbr"}
_TEXT_TAGS = {"text", "tspan", "textpath"}
_SHAPES = {"rect", "circle", "ellipse", "path", "polygon", "polyline", "line"}
_CSS_COLOUR_PROPS = ("color", "fill", "stroke", "background", "background-color", "border", "border-color",
                     "stop-color", "outline-color", "border-top-color", "border-bottom-color",
                     "border-left-color", "border-right-color", "text-decoration-color", "caret-color")
_VAR = re.compile(r"var\(\s*(--[\w-]+)\s*(?:,\s*([^)]+))?\)")


def _style_map(s: str) -> Dict[str, str]:
    out = {}
    for part in (s or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip().lower()] = v.strip()
    return out


class _Reader(HTMLParser):
    def __init__(self, cssvars: Dict[str, str]):
        super().__init__(convert_charrefs=True)
        self.stack: List[dict] = []
        self.elements: List[dict] = []
        self.texts: List[Tuple[str, str]] = []
        self.cssvars = cssvars
        self.svg_count = 0
        self.n = 0
        self.skin: Optional[str] = None
        self.in_script = 0
        self.style_text: List[str] = []
        self.in_style = 0

    def _resolve(self, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        for _ in range(5):
            m = _VAR.search(v)
            if not m:
                break
            rep = self.cssvars.get(m.group(1), m.group(2) or "")
            v = v[:m.start()] + rep + v[m.end():]
        return v.strip()

    def handle_starttag(self, tag, attrs):
        self._open(tag, dict(attrs), void=tag in _VOID)

    def handle_startendtag(self, tag, attrs):
        self._open(tag, dict(attrs), void=True)

    def _open(self, tag: str, a: dict, void: bool):
        tag = tag.lower()
        if tag == "script":
            self.in_script += 1
            if not void:
                self.stack.append({"tag": tag, "skip": True})
            return
        if tag == "style":
            self.in_style += 1
            if not void:
                self.stack.append({"tag": tag, "skip": True})
            return
        parent = self.stack[-1] if self.stack else {}
        st = _style_map(a.get("style", ""))
        for k, v in st.items():
            if k.startswith("--"):
                self.cssvars[k] = v
        cls = (a.get("class") or "").lower().split()
        if tag in ("html", "body", "svg") and self.skin is None:
            if (a.get("data-skin") or "").lower() in ("dark", "light"):
                self.skin = a["data-skin"].lower()
            elif "dark" in cls or "skin-dark" in cls:
                self.skin = "dark"
        if tag == "svg" and not any(n.get("tag") == "svg" for n in self.stack):
            self.svg_count += 1
        frame = a.get("data-frame") or parent.get("frame")
        if frame is None and tag == "svg" and not any(n.get("tag") == "svg" for n in self.stack):
            frame = f"svg#{self.svg_count}"
        self.n += 1
        node = {"tag": tag, "id": a.get("id") or f"{tag}#{self.n}", "frame": frame or parent.get("frame"),
                "attrs": a, "style": st, "text": "", "parent": parent,
                "fill": self._resolve(st.get("fill") or a.get("fill")) or parent.get("fill"),
                "color": self._resolve(st.get("color") or a.get("color")) or parent.get("color"),
                "font_px": self._font(st.get("font-size") or a.get("font-size")) or parent.get("font_px"),
                "bold": self._bold(st.get("font-weight") or a.get("font-weight"), parent.get("bold")),
                "bg": self._bg(st) or parent.get("bg"),
                "meaning": a.get("data-meaning") or parent.get("meaning"),
                "fill_weight": a.get("data-fill-weight"),
                "focal_group": a.get("data-focal-group") or parent.get("focal_group")}
        if void:
            self._close(node)
        else:
            self.stack.append(node)

    @staticmethod
    def _font(v):
        if not v:
            return None
        m = re.match(r"\s*([0-9.]+)\s*(px|pt|em|rem)?", str(v))
        if not m:
            return None
        n = float(m.group(1))
        unit = m.group(2) or "px"
        return n * {"px": 1.0, "pt": 4 / 3, "em": 16.0, "rem": 16.0}[unit]

    @staticmethod
    def _bold(v, inherited):
        if v is None:
            return bool(inherited)
        v = str(v).strip().lower()
        return v in ("bold", "bolder") or (v.isdigit() and int(v) >= 600)

    def _bg(self, st):
        for k in ("background-color", "background"):
            v = self._resolve(st.get(k))
            if v:
                m = COLOR_TOKEN.search(v)
                if m:
                    return m.group(0)
        return None

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("script", "style"):
            if tag == "script":
                self.in_script = max(0, self.in_script - 1)
            else:
                self.in_style = max(0, self.in_style - 1)
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]["tag"] == tag:
                while len(self.stack) > i:
                    n = self.stack.pop()
                    if not n.get("skip"):
                        self._close(n)
                return

    def handle_data(self, data):
        if self.in_style and self.stack and self.stack[-1].get("tag") == "style":
            self.style_text.append(data)
            return
        if self.in_script:
            return
        if self.stack and not self.stack[-1].get("skip"):
            self.stack[-1]["text"] += data

    def _close(self, n: dict):
        a, st = n["attrs"], n["style"]
        tag = n["tag"]
        text = re.sub(r"\s+", " ", n["text"]).strip()
        if text:
            self.texts.append((n["id"], text))
            # visible text bubbles up so a <g> label also counts for its parent legend
        base = {"id": n["id"], "frame": n["frame"] or "page", "meaning": n["meaning"],
                "fill_weight": n["fill_weight"], "focal_group": n["focal_group"],
                "label": a.get("data-label") or (text if tag not in ("svg", "g") else ""),
                "icon": a.get("data-icon"), "shape": tag if tag in _SHAPES else None,
                "dash": st.get("stroke-dasharray") or a.get("stroke-dasharray"),
                "accent": str(a.get("data-accent") or "").lower() == "true",
                "bg": n["bg"]}
        if tag in _TEXT_TAGS and text:
            col = self._resolve(st.get("fill") or a.get("fill")) or n["fill"] or n["color"]
            if col:
                self.elements.append(dict(base, kind="text", color=col, font_px=n["font_px"] or 16.0,
                                          bold=n["bold"], text=text))
            return
        own_color = self._resolve(st.get("color") or a.get("color"))
        if text and tag not in _SHAPES and tag not in ("svg", "g", "html", "body") and (own_color or n["color"]):
            self.elements.append(dict(base, kind="text", color=own_color or n["color"],
                                      font_px=n["font_px"] or 16.0, bold=n["bold"], text=text))
        # one fill weight per shape, shared by its fill AND its stroke: a hollow bar is hollow whatever
        # colour its outline is (fill none / fill-opacity 0 -> 0.0; a painted fill -> its opacity)
        weight = n["fill_weight"]
        if weight is None and tag in _SHAPES:
            fv = self._resolve(st.get("fill") or a.get("fill"))
            if fv is not None and fv.lower() == "none":
                weight = 0.0
            elif fv is not None:
                weight = _num(st.get("fill-opacity") or a.get("fill-opacity"), 1.0)
        for prop, kind in (("fill", "fill"), ("stroke", "mark"), ("stop-color", "fill")):
            v = self._resolve(st.get(prop) or a.get(prop))
            if not v or v.startswith("url(") or v.lower() in NON_COLOURS:
                continue
            el = dict(base, kind=kind, color=v)
            if weight is not None:
                el["fill_weight"] = weight
            self.elements.append(el)
        for prop in ("background-color", "background", "border", "border-color", "outline-color"):
            v = self._resolve(st.get(prop))
            if not v:
                continue
            for m in COLOR_TOKEN.finditer(v):
                self.elements.append(dict(base, kind="fill", color=m.group(0), id=f"{n['id']}:{prop}"))


# ------------------------------------------------------------------ stylesheet reader
# A <style> block is read declaration by declaration, not as a bag of colour tokens:
#   * a custom property (--x: ...) DECLARES a colour; it is held to palette membership only (it paints
#     nothing until a rule uses it, so it never counts as a mark in a frame);
#   * a paint property (color, fill, stroke, background, border, outline, shadow, ...) is resolved through
#     var() — a fallback after a comma is used only when the variable is not declared anywhere in the
#     document — and every colour it ends up painting is judged: literals, named colours, hsl(), and
#     color-mix() (declared exactly when every operand is a declared role or transparent);
#   * every other property's literal colours are still judged for membership (nothing is skipped).
# Stylesheet colours form the frame 'stylesheet' with one focal group: rules apply to many elements, so
# a stylesheet is not a frame of co-present marks (accent budget per frame is judged on real frames).
_NAMED = {
    "aliceblue": "#f0f8ff", "antiquewhite": "#faebd7", "aqua": "#00ffff", "aquamarine": "#7fffd4", "azure": "#f0ffff",
    "beige": "#f5f5dc", "bisque": "#ffe4c4", "black": "#000000", "blanchedalmond": "#ffebcd", "blue": "#0000ff",
    "blueviolet": "#8a2be2", "brown": "#a52a2a", "burlywood": "#deb887", "cadetblue": "#5f9ea0",
    "chartreuse": "#7fff00", "chocolate": "#d2691e", "coral": "#ff7f50", "cornflowerblue": "#6495ed",
    "cornsilk": "#fff8dc", "crimson": "#dc143c", "cyan": "#00ffff", "darkblue": "#00008b", "darkcyan": "#008b8b",
    "darkgoldenrod": "#b8860b", "darkgray": "#a9a9a9", "darkgreen": "#006400", "darkgrey": "#a9a9a9",
    "darkkhaki": "#bdb76b", "darkmagenta": "#8b008b", "darkolivegreen": "#556b2f", "darkorange": "#ff8c00",
    "darkorchid": "#9932cc", "darkred": "#8b0000", "darksalmon": "#e9967a", "darkseagreen": "#8fbc8f",
    "darkslateblue": "#483d8b", "darkslategray": "#2f4f4f", "darkslategrey": "#2f4f4f", "darkturquoise": "#00ced1",
    "darkviolet": "#9400d3", "deeppink": "#ff1493", "deepskyblue": "#00bfff", "dimgray": "#696969",
    "dimgrey": "#696969", "dodgerblue": "#1e90ff", "firebrick": "#b22222", "floralwhite": "#fffaf0",
    "forestgreen": "#228b22", "fuchsia": "#ff00ff", "gainsboro": "#dcdcdc", "ghostwhite": "#f8f8ff",
    "gold": "#ffd700", "goldenrod": "#daa520", "gray": "#808080", "green": "#008000", "greenyellow": "#adff2f",
    "grey": "#808080", "honeydew": "#f0fff0", "hotpink": "#ff69b4", "indianred": "#cd5c5c", "indigo": "#4b0082",
    "ivory": "#fffff0", "khaki": "#f0e68c", "lavender": "#e6e6fa", "lavenderblush": "#fff0f5",
    "lawngreen": "#7cfc00", "lemonchiffon": "#fffacd", "lightblue": "#add8e6", "lightcoral": "#f08080",
    "lightcyan": "#e0ffff", "lightgoldenrodyellow": "#fafad2", "lightgray": "#d3d3d3", "lightgreen": "#90ee90",
    "lightgrey": "#d3d3d3", "lightpink": "#ffb6c1", "lightsalmon": "#ffa07a", "lightseagreen": "#20b2aa",
    "lightskyblue": "#87cefa", "lightslategray": "#778899", "lightslategrey": "#778899",
    "lightsteelblue": "#b0c4de", "lightyellow": "#ffffe0", "lime": "#00ff00", "limegreen": "#32cd32",
    "linen": "#faf0e6", "magenta": "#ff00ff", "maroon": "#800000", "mediumaquamarine": "#66cdaa",
    "mediumblue": "#0000cd", "mediumorchid": "#ba55d3", "mediumpurple": "#9370db", "mediumseagreen": "#3cb371",
    "mediumslateblue": "#7b68ee", "mediumspringgreen": "#00fa9a", "mediumturquoise": "#48d1cc",
    "mediumvioletred": "#c71585", "midnightblue": "#191970", "mintcream": "#f5fffa", "mistyrose": "#ffe4e1",
    "moccasin": "#ffe4b5", "navajowhite": "#ffdead", "navy": "#000080", "oldlace": "#fdf5e6", "olive": "#808000",
    "olivedrab": "#6b8e23", "orange": "#ffa500", "orangered": "#ff4500", "orchid": "#da70d6",
    "palegoldenrod": "#eee8aa", "palegreen": "#98fb98", "paleturquoise": "#afeeee", "palevioletred": "#db7093",
    "papayawhip": "#ffefd5", "peachpuff": "#ffdab9", "peru": "#cd853f", "pink": "#ffc0cb", "plum": "#dda0dd",
    "powderblue": "#b0e0e6", "purple": "#800080", "rebeccapurple": "#663399", "red": "#ff0000",
    "rosybrown": "#bc8f8f", "royalblue": "#4169e1", "saddlebrown": "#8b4513", "salmon": "#fa8072",
    "sandybrown": "#f4a460", "seagreen": "#2e8b57", "seashell": "#fff5ee", "sienna": "#a0522d", "silver": "#c0c0c0",
    "skyblue": "#87ceeb", "slateblue": "#6a5acd", "slategray": "#708090", "slategrey": "#708090", "snow": "#fffafa",
    "springgreen": "#00ff7f", "steelblue": "#4682b4", "tan": "#d2b48c", "teal": "#008080", "thistle": "#d8bfd8",
    "tomato": "#ff6347", "turquoise": "#40e0d0", "violet": "#ee82ee", "wheat": "#f5deb3", "white": "#ffffff",
    "whitesmoke": "#f5f5f5", "yellow": "#ffff00", "yellowgreen": "#9acd32",
}
_NAMED_RE = re.compile(r"(?<![\w#.-])(" + "|".join(sorted(_NAMED, key=len, reverse=True)) + r")(?![\w-])",
                       re.IGNORECASE)
_HSL_RE = re.compile(r"hsla?\(\s*([^)]*)\)", re.IGNORECASE)
_OPAQUE_FN = re.compile(r"\b(?:hwb|lab|lch|oklab|oklch|color)\(\s*[^()]*\)", re.IGNORECASE)
_TEXT_PROPS = {"color", "-webkit-text-fill-color", "-webkit-text-stroke-color", "caret-color",
               "text-decoration-color", "text-emphasis-color"}
_FILL_PROPS = {"fill", "background", "background-color", "stop-color", "flood-color", "lighting-color",
               "box-shadow", "accent-color", "scrollbar-color"}
_PAINT_PREFIXES = ("border", "outline", "column-rule", "text-decoration", "text-shadow", "stroke", "-webkit-text")


def _is_paint(prop: str) -> bool:
    return prop in _TEXT_PROPS or prop in _FILL_PROPS or prop.startswith(_PAINT_PREFIXES)


def _hsl(args: str) -> Optional[tuple]:
    parts = [x for x in re.split(r"[\s,/]+", args.strip()) if x]
    if len(parts) not in (3, 4):
        return None
    try:
        h = float(re.sub(r"deg$", "", parts[0])) % 360 / 360.0
        sat = float(parts[1].rstrip("%")) / 100.0
        lig = float(parts[2].rstrip("%")) / 100.0
        a = 1.0 if len(parts) == 3 else (float(parts[3][:-1]) / 100 if parts[3].endswith("%") else float(parts[3]))
    except ValueError:
        return None

    def f(n):
        k = (n + h * 12) % 12
        return lig - sat * min(lig, 1 - lig) * max(-1.0, min(k - 3, 9 - k, 1.0))
    return (f(0) * 255, f(8) * 255, f(4) * 255, a)


def parse_colour(v: str) -> Optional[tuple]:
    """A painted colour as (r, g, b, a): literals, every CSS named colour, hsl(); else None."""
    v = (v or "").strip()
    c = try_parse(v)
    if c is not None:
        return c
    low = v.lower()
    if low in _NAMED:
        return try_parse(_NAMED[low])
    m = _HSL_RE.fullmatch(v)
    return _hsl(m.group(1)) if m else None


def _strip_important(v: str) -> str:
    return re.sub(r"\s*!\s*important\s*$", "", v.strip(), flags=re.IGNORECASE)


def _resolve_vars(v: str, cssvars: Dict[str, str], depth: int = 0) -> str:
    """Replace every var(--x[, fallback]) by --x's declared value, the fallback only when --x is not
    declared anywhere in the document (that is when a browser paints it)."""
    out, i = [], 0
    while True:
        j = v.find("var(", i)
        if j < 0 or depth > 8:
            out.append(v[i:])
            break
        out.append(v[i:j])
        k, level = j + 4, 1
        while k < len(v) and level:
            level += {"(": 1, ")": -1}.get(v[k], 0)
            k += 1
        inner = v[j + 4:k - 1]
        name, _, fb = inner.partition(",")
        name = name.strip()
        rep = cssvars.get(name)
        rep = _strip_important(rep) if rep is not None else fb.strip()
        out.append(_resolve_vars(rep, cssvars, depth + 1))
        i = k
    return "".join(out)


def _split_top(s: str, sep: str = ",") -> List[str]:
    parts, level, cur = [], 0, []
    for ch in s:
        if ch == "(":
            level += 1
        elif ch == ")":
            level -= 1
        if ch == sep and level == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return parts


def _eval_mix(inner: str) -> Tuple[Optional[tuple], List[str]]:
    """color-mix(in <space>, A [p%], B [q%]) -> (mixed rgba, [operand A, operand B]). The mix is computed
    in sRGB with premultiplied alpha (exact for 'in srgb'; an approximation for other spaces, which only
    affects contrast — membership is decided by the operands)."""
    parts = [x.strip() for x in _split_top(inner)]
    if len(parts) != 3 or not parts[0].lower().startswith("in "):
        return None, []
    ops, pcts = [], []
    for part in parts[1:]:
        m = re.match(r"^(.*?)(?:\s+([0-9.]+)%)?$", part, re.DOTALL)
        col, pct = m.group(1).strip(), m.group(2)
        m2 = re.match(r"^([0-9.]+)%\s+(.*)$", col, re.DOTALL)   # the percentage may come first
        if m2 and pct is None:
            pct, col = m2.group(1), m2.group(2).strip()
        if col.lower().startswith("color-mix("):
            sub, _ = _eval_mix(col[len("color-mix("):-1])
            col = to_hex(sub[:3]) if sub is not None and sub[3] >= 0.999 else col
        ops.append(col)
        pcts.append(float(pct) / 100 if pct is not None else None)
    p1, p2 = pcts
    if p1 is None and p2 is None:
        p1 = p2 = 0.5
    elif p1 is None:
        p1 = 1 - p2
    elif p2 is None:
        p2 = 1 - p1
    total = p1 + p2
    if total <= 0:
        return None, ops
    scale = min(1.0, total)
    p1, p2 = p1 / total, p2 / total
    a_, b_ = parse_colour(ops[0]), parse_colour(ops[1])
    if a_ is None or b_ is None:
        return None, ops
    alpha = p1 * a_[3] + p2 * b_[3]
    if alpha <= 0:
        return (0.0, 0.0, 0.0, 0.0), ops
    rgb = tuple((p1 * a_[3] * a_[i] + p2 * b_[3] * b_[i]) / alpha for i in range(3))
    return rgb + (alpha * scale,), ops


def _colours_in(value: str, named: bool) -> List[dict]:
    """Every colour a resolved CSS value paints: color-mix() first (removed from the string so its
    operands are not counted twice), then literals, hsl(), unresolvable colour functions, named colours."""
    found: List[dict] = []
    v = re.sub(r"url\([^)]*\)", " ", value, flags=re.IGNORECASE)
    v = re.sub(r"\"[^\"]*\"|'[^']*'", " ", v)
    while True:
        j = v.lower().find("color-mix(")
        if j < 0:
            break
        k, level = j + len("color-mix("), 1
        while k < len(v) and level:
            level += {"(": 1, ")": -1}.get(v[k], 0)
            k += 1
        expr = v[j:k]
        c, ops = _eval_mix(v[j + len("color-mix("):k - 1])
        if c is None:
            found.append({"color": expr, "opaque_colour": True})
        else:
            found.append({"color": "rgba({:.0f}, {:.0f}, {:.0f}, {:.3f})".format(*c), "mix_of": ops,
                          "source_value": expr})
        v = v[:j] + " " + v[k:]
    for m in COLOR_TOKEN.finditer(v):
        found.append({"color": m.group(0)})
    v = COLOR_TOKEN.sub(" ", v)
    for m in _HSL_RE.finditer(v):
        found.append({"color": m.group(0)})
    v = _HSL_RE.sub(" ", v)
    for m in _OPAQUE_FN.finditer(v):
        found.append({"color": m.group(0), "opaque_colour": True})
    if named:
        for m in _NAMED_RE.finditer(_OPAQUE_FN.sub(" ", v)):
            found.append({"color": m.group(1).lower()})
    return found


def _css_declarations(css: str):
    """(selector, property, value) for every declaration, through @media and other nested blocks."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    stack: List[str] = []
    buf: List[str] = []
    quote = ""
    for ch in css:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch == "{":
            stack.append("".join(buf).strip())
            buf = []
        elif ch in ";}":
            text = "".join(buf).strip()
            buf = []
            if ":" in text and stack and not stack[-1].startswith("@media") and not stack[-1].startswith("@supports"):
                prop, _, val = text.partition(":")
                yield stack[-1], prop.strip().lower(), val.strip()
            if ch == "}" and stack:
                stack.pop()
        else:
            buf.append(ch)


def _fallback_colours(val: str, cssvars: Dict[str, str]) -> List[dict]:
    """Colours written as the fallback of a DECLARED variable (var(--c8-ink, #111)). The browser does not
    paint them today, but they are hard-coded colours the palette did not declare and would paint the day
    the variable goes missing — so they are judged for membership, never skipped."""
    out: List[dict] = []
    i = 0
    while True:
        j = val.find("var(", i)
        if j < 0:
            return out
        k, level = j + 4, 1
        while k < len(val) and level:
            level += {"(": 1, ")": -1}.get(val[k], 0)
            k += 1
        name, comma, fb = val[j + 4:k - 1].partition(",")
        if comma and name.strip() in cssvars and fb.strip():
            for col in _colours_in(_resolve_vars(fb, cssvars), named=True):
                col["source_value"] = f"the fallback in var({name.strip()}, {fb.strip()})"
                out.append(col)
            out += _fallback_colours(fb, cssvars)
        i = j + 4


def read_stylesheet(css: str, cssvars: Dict[str, str]) -> List[dict]:
    recs: List[dict] = []
    rules: Dict[str, Dict[str, str]] = {}
    decls = list(_css_declarations(css))
    for sel, prop, val in decls:
        rules.setdefault(sel, {})[prop] = val
    for sel, prop, val in decls:
        base = {"frame": "stylesheet", "focal_group": "stylesheet", "selector": sel, "property": prop}
        rid = f"stylesheet {sel[:60]} {{{prop}}}"
        for col in _fallback_colours(val, cssvars):
            recs.append(dict(base, id=rid, kind="declaration", **col))
        if prop.startswith("--"):
            raw = _resolve_vars(_strip_important(val), cssvars)
            cols = _colours_in(raw, named=False)
            if not cols and raw.strip().lower() in _NAMED:
                cols = [{"color": raw.strip().lower()}]
            for col in cols:
                recs.append(dict(base, id=rid, kind="declaration", **col))
            continue
        paint = _is_paint(prop)
        resolved = _resolve_vars(val, cssvars) if paint else val
        kind = "text" if prop in _TEXT_PROPS else "fill" if prop in _FILL_PROPS else "mark"
        bg = None
        if kind == "text":
            own = rules.get(sel, {})
            for bp in ("background-color", "background"):
                if bp in own:
                    bcs = _colours_in(_resolve_vars(own[bp], cssvars), named=True)
                    if bcs and not bcs[0].get("opaque_colour"):
                        bg = bcs[0]["color"]
                        break
        for col in _colours_in(resolved, named=paint):
            rec = dict(base, id=rid, kind=kind if paint else "fill", **col)
            if bg is not None:
                rec["bg"] = bg
            recs.append(rec)
    return recs


def read_markup(text: str) -> Tuple[List[dict], Optional[str], List[dict]]:
    """-> (elements, document skin, stylesheet colour records)."""
    cssvars: Dict[str, str] = {}
    blocks = re.findall(r"<style\b[^>]*>(.*?)</style\s*>", text, re.IGNORECASE | re.DOTALL)
    for b in blocks:
        for m in re.finditer(r"(--[\w-]+)\s*:\s*([^;}]+)", b):
            cssvars[m.group(1)] = m.group(2).strip()
    r = _Reader(cssvars)
    r.feed(text)
    r.close()
    sheet = []
    for b in blocks:
        sheet += read_stylesheet(b, cssvars)
    els = r.elements
    # visible text of non-coloured containers still makes legend claims
    for nid, t in r.texts:
        if LEGEND_CLAIM.search(t) and not any(e["id"] == nid and e["kind"] == "text" for e in els):
            els.append({"id": nid, "frame": "page", "kind": "text", "color": None, "text": t})
    return els, r.skin, sheet


# ------------------------------------------------------------------ main
def _load_elements(path: Path) -> List[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("elements"), list):
        return data["elements"]
    if isinstance(data, dict) and isinstance(data.get("frames"), list):
        out = []
        for f in data["frames"]:
            for e in f.get("elements") or []:
                e = dict(e)
                e.setdefault("frame", f.get("id"))
                out.append(e)
        return out
    raise ValueError("elements JSON must be a list, {elements: [...]}, or {frames: [...]}")


def check(design_path: Path, elements_path: Optional[Path] = None, html_paths=()) -> Tuple[int, dict]:
    if not design_path.exists():
        return EXIT_SKIPPED, {"gate": GATE, "status": "SKIPPED", "summary": f"no design system at {design_path}"}
    try:
        doc = json.loads(design_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return EXIT_ERROR, {"gate": GATE, "status": "ERROR", "summary": f"cannot read {design_path}: {e}"}
    pal = doc.get("palette") if isinstance(doc, dict) and isinstance(doc.get("palette"), dict) else doc
    if not isinstance(pal, dict) or not isinstance(pal.get("roles"), dict):
        return EXIT_SKIPPED, {"gate": GATE, "status": "SKIPPED",
                              "summary": "design system has no palette roles to judge"}
    dec = Declared(pal)
    findings, measured = palette_findings(pal, dec)
    info: dict = {"measured": measured, "inputs": {"design": str(design_path)}}
    try:
        if elements_path:
            els = _load_elements(elements_path)
            f, meta = element_findings(els, dec)
            findings += f
            info["elements"] = {"count": len(els), **meta}
            info["inputs"]["elements"] = str(elements_path)
        for hp in html_paths:
            text = Path(hp).read_text(encoding="utf-8", errors="replace")
            els, skin, sheet = read_markup(text)
            f, meta = element_findings(els + sheet, dec, doc_skin=skin)
            for x in f:
                x["file"] = str(hp)
            findings += f
            info.setdefault("markup", []).append({"file": str(hp), "elements": len(els), "stylesheet_colours": len(sheet),
                                                  "skin": skin or "inferred", **meta})
    except (OSError, ValueError, json.JSONDecodeError) as e:
        return EXIT_ERROR, {"gate": GATE, "status": "ERROR", "summary": f"bad scene input: {e}"}
    by = {}
    for f in findings:
        by[f["check"]] = by.get(f["check"], 0) + 1
    status = "REVIEW" if findings else "PASS"
    summary = ("palette and scene pass every outcome check" if not findings else
               "; ".join(f"{k} x{v}" for k, v in sorted(by.items())))
    res = {"gate": GATE, "status": status, "summary": summary, "counts": by, "findings": findings, **info,
           "measures": "contrast (WCAG 2.x, composited), CIEDE2000 role separation, accent count per frame, "
                       "declared-colour membership, hue count per frame, polarity channels; a PASS is not a "
                       "judgement of taste"}
    return (EXIT_REVIEW if findings else EXIT_PASS), res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Coordn8 palette and contrast gate (advisory)")
    ap.add_argument("design", help="design_system.json (or a palette JSON with roles/dark)")
    ap.add_argument("--elements", help="trace/elements JSON")
    ap.add_argument("--html", nargs="*", default=[], help="HTML or SVG files to read colours from")
    ap.add_argument("--json", action="store_true", help="(output is always JSON; kept for symmetry)")
    a = ap.parse_args(argv)
    code, res = check(Path(a.design), Path(a.elements) if a.elements else None, a.html)
    emit(res)
    return code


if __name__ == "__main__":
    sys.exit(main())
