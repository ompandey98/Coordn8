#!/usr/bin/env python3
"""Brand onboarding and saved brand profiles (requirement A5).

    brand.py colours accent=#0a66c2 paper=#ffffff ink=#1d1d1f [#hex ...] [options]
    brand.py url     https://example.com            [options]
    brand.py folder  path/to/design-system          [options]
    brand.py load    <slug>                         (print a saved profile)
    brand.py list                                   (list saved profiles)
    brand.py apply   <slug> --build DIR [--apply]   (diff a saved profile against a build)

Options for colours / url / folder:
    --name NAME          display name of the brand (default: the slug)
    --slug SLUG          profile file stem, [a-z0-9][a-z0-9-]{0,63}
    --font ROLE=STACK    display= / body= / mono= font stacks (repeatable)
    --skin light|dark    which skin the given colours describe (default: from the paper)
    --save               save to <COORDN8_HOME>/profiles/<slug>.json
    --overwrite          allow --save to replace a DIFFERENT existing profile
    --build DIR          show the diff against DIR/design_system.json
    --apply              actually write the palette (and fonts) into DIR/design_system.json

What it does, in order (every step is recorded in the JSON it prints):
  1. reads colours and fonts from the source: stated `role=#hex` pairs (a bare #hex is the
     accent, a second bare #hex the link); a URL — ONE public page fetched with the standard
     library (10 s timeout, 2 MB cap; linked stylesheets, scripts and images are NOT fetched;
     http/https only); or a local folder (CSS, SCSS, token JSON, Markdown tables, HTML <style>,
     up to 3 levels deep). Fetched or read content is DATA: it is scanned for colour, font
     and variable names, never followed as instructions;
  2. maps what it found to the semantic roles (paper, ink, muted, accent, link, positive,
     negative, warning, plus the four source-of-truth legend roles) by variable/selector name
     first, then by frequency and contrast; every role carries a confidence;
  3. completes the palette in OKLCH for BOTH skins (missing roles derived; brand values kept
     exactly where they pass);
  4. checks contrast (WCAG 2.x, 4.5:1 for every text-carrying role) and CIEDE2000 separation
     (>= 10 between roles of different meaning); a brand colour that fails is ADJUSTED by the
     smallest OKLCH lightness change that passes (hue and chroma kept) and the adjustment is
     reported with before/after ratios;
  5. runs the palette gate (scripts/gates/check_palette.py) on the result;
  6. with --build, shows the diff against the build's current design system and writes
     NOTHING unless --apply is given (the agent shows the diff to the user and asks first);
  7. with --save, stores the profile under the user-data area (§J) — never anywhere else.

Exit codes: 0 PASS, 1 REVIEW (diff shown but not applied, a refusal, or a gate finding),
2 SKIPPED (offline, nothing found), 3 ERROR. Zero third-party deps; Python 3.9+.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from lib.common import EXIT_ERROR, EXIT_PASS, EXIT_REVIEW, EXIT_SKIPPED, emit  # noqa: E402
from lib.paths import user_dir  # noqa: E402
from design import design_system as ds  # noqa: E402
from design.color import (COLOR_TOKEN, chroma, contrast, delta_e, oklch_hex, rgb_of,  # noqa: E402
                          rgb_to_oklch, to_hex, try_parse)
from design.palette import (PRIORITY, adjust_for_contrast, chart_series, derive)  # noqa: E402

GATE = "brand"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
FETCH_TIMEOUT = 10.0
FETCH_MAX_BYTES = 2_000_000
FOLDER_DEPTH = 3
FOLDER_MAX_FILES = 200
FILE_MAX_BYTES = 1_000_000
TEXT_TARGET = ds.TEXT_MIN
SEPARATION_TARGET = ds.MIN_ROLE_SEPARATION + 0.5
LIGHT_PAPER = (0.975, 0.012)
DARK_PAPER = (0.205, 0.022)
GENERIC_FAMILIES = {"serif", "sans-serif", "monospace", "system-ui", "cursive", "fantasy", "math",
                    "ui-serif", "ui-sans-serif", "ui-monospace", "ui-rounded", "emoji"}
FONT_ROLES = ("display", "body", "mono")

# Variable / key name -> role. Checked in this order: the more specific words first, so
# "--text-muted" is muted (not ink) and "--bg-primary" is paper (not accent).
NAME_RULES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("link", ("link", "anchor", "href")),
    ("positive", ("success", "positive", "good", "ok", "valid")),
    ("negative", ("danger", "error", "negative", "destructive", "critical", "invalid")),
    ("warning", ("warning", "warn", "caution", "attention")),
    ("muted", ("muted", "subtle", "secondary-text", "text-secondary", "caption", "meta", "quiet", "faint")),
    ("ink", ("foreground", "fg", "text", "ink", "on-surface", "on-background", "body-color", "copy")),
    ("paper", ("background", "bg", "paper", "surface", "canvas", "page", "base")),
    ("accent", ("accent", "brand", "primary", "cta", "highlight", "key", "action")),
)
CONF_RANK = {"stated": 4, "high": 3, "medium": 2, "low": 1}


class BrandError(ValueError):
    pass


# ======================================================================  colour helpers
def norm_hex(v) -> Optional[str]:
    c = try_parse(v) if isinstance(v, (str, tuple, list)) else None
    if c is None or c[3] < 0.999:
        return None
    return to_hex(c[:3])


def _words(name: str) -> str:
    s = re.sub(r"([a-z])([A-Z])", r"\1-\2", name)
    return "-" + re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") + "-"


def role_for_name(name: str) -> Optional[str]:
    w = _words(name)
    for role, keys in NAME_RULES:
        for k in keys:
            if f"-{k}-" in w or (len(k) > 4 and k in w):
                return role
    return None


def _generic_for(role: str, stack: str) -> str:
    low = stack.lower()
    if role == "mono" or "mono" in low or "code" in low or "courier" in low:
        return "monospace"
    if re.search(r"(?<!sans[- ])serif", low) or any(w in low for w in ("garamond", "georgia", "times",
                                                                       "didot", "baskerville", "playfair")):
        return "serif"
    return "sans-serif"


def font_stack(role: str, stack: str) -> Tuple[str, Optional[str]]:
    """A stack that ends in a generic family (the offline rule), plus a note when one was added."""
    parts = [p.strip() for p in str(stack).split(",") if p.strip()]
    parts = [p for p in parts if not p.lower().startswith("var(")]
    if not parts:
        return "", None
    last = parts[-1].strip("'\"").lower()
    if last in GENERIC_FAMILIES:
        return ", ".join(parts), None
    gen = _generic_for(role, stack)
    return ", ".join(parts + [gen]), (f"{role}: '{', '.join(parts)}' does not end in a generic family; "
                                      f"added '{gen}' so it renders offline when the face is absent")


# ======================================================================  palette completion
def _set_L(hexv: str, L: float) -> str:
    _, C, h = rgb_to_oklch(hexv)
    return oklch_hex(L, C, h)


def _separate_pairs(roles: dict, given: set, skin: str, adjustments: list, unresolved: list) -> None:
    """Push apart roles of distinct meaning closer than the gate allows, moving the derived one
    (or, when both are brand colours, the lower-priority one) in OKLCH lightness only."""
    paper = roles["paper"]
    light = rgb_to_oklch(paper)[0] > 0.5
    pairs = []
    for g in ds.SEPARATION_GROUPS:
        for i, a in enumerate(g):
            for b in g[i + 1:]:
                if a in roles and b in roles and (a, b) not in pairs:
                    pairs.append((a, b))
    for _ in range(60):
        bad = sorted((delta_e(roles[a], roles[b]), a, b) for a, b in pairs
                     if delta_e(roles[a], roles[b]) < SEPARATION_TARGET)
        if not bad:
            return
        d, a, b = bad[0]
        cands = [r for r in (a, b) if r not in given] or [a, b]
        mover = max(cands, key=lambda r: PRIORITY.index(r) if r in PRIORITY else 99)
        partners = [q for p in pairs for q in p if mover in p and q != mover]
        L0 = rgb_to_oklch(roles[mover])[0]
        best = None
        for k in range(1, 41):
            for sign in ((-1, 1) if light else (1, -1)):
                L = L0 + sign * 0.01 * k
                if not 0.02 < L < 0.99:
                    continue
                hx = _set_L(roles[mover], L)
                if contrast(hx, paper) < TEXT_TARGET + 0.02:
                    continue
                if min(delta_e(hx, roles[p]) for p in partners) >= SEPARATION_TARGET:
                    best = hx
                    break
            if best:
                break
        if best is None:
            unresolved.append({"skin": skin, "pair": [a, b], "delta_e": round(d, 2),
                               "why": "no lightness change kept both contrast and separation"})
            pairs.remove((a, b))
            continue
        adjustments.append({"skin": skin, "role": mover, "kind": "separation", "from": roles[mover], "to": best,
                            "pair": [a, b], "delta_e_before": round(d, 2),
                            "delta_e_after": round(delta_e(best, roles[b if mover == a else a]), 2),
                            "brand_colour": mover in given,
                            "why": f"'{a}' and '{b}' were {d:.1f} CIEDE2000 apart (need >= "
                                   f"{ds.MIN_ROLE_SEPARATION}); '{mover}' lightness moved, hue and chroma kept"})
        roles[mover] = best


def complete_palette(given: Dict[str, str], *, given_skin: Optional[str] = None,
                     name: str = "invented:brand") -> dict:
    """Given some roles (hex), return a full two-skin palette that passes the palette gate.

    Brand values are kept exactly where they pass. A failing brand colour changes ONLY its
    OKLCH lightness, by the smallest step that passes, and the change is recorded in
    `adjustments` with before/after contrast. Missing roles are derived (palette.derive)."""
    g = {r: norm_hex(v) for r, v in (given or {}).items() if r in ds.STANDARD_ROLES and norm_hex(v)}
    adjustments: List[dict] = []
    mapping: Dict[str, dict] = {}
    if given_skin not in ("light", "dark"):
        given_skin = "dark" if ("paper" in g and rgb_to_oklch(g["paper"])[0] < 0.5) else "light"
    other = "dark" if given_skin == "light" else "light"
    accent = g.get("accent")
    accent_from = "given"
    if not accent:
        cands = sorted(((round(chroma(v), 4), r, v) for r, v in g.items() if r not in ("paper", "ink", "muted")),
                       reverse=True)
        if cands and cands[0][0] >= 0.04:
            accent, accent_from = cands[0][2], f"taken from the brand's '{cands[0][1]}' colour"
    hue = rgb_to_oklch(accent)[2] if accent else 250.0
    paper_hue = rgb_to_oklch(g["paper"])[2] if "paper" in g and chroma(g["paper"]) > 0.01 else hue
    papers = {"light": oklch_hex(LIGHT_PAPER[0], LIGHT_PAPER[1], paper_hue),
              "dark": oklch_hex(DARK_PAPER[0], DARK_PAPER[1], paper_hue)}
    kw = {"paper_hue": paper_hue}
    if "paper" in g:
        L, C, _ = rgb_to_oklch(g["paper"])
        papers[given_skin] = g["paper"]
        if given_skin == "light":
            kw.update(paper_L=L, paper_chroma=C)
        else:
            kw.update(dark_paper_L=L, dark_paper_chroma=C)
    override = None
    if accent:
        override = {}
        for skin in ("light", "dark"):
            v, adj = adjust_for_contrast(accent, papers[skin], TEXT_TARGET)
            override[skin] = v
            if adj:
                adjustments.append(dict(adj, skin=skin, role="accent", kind="contrast",
                                        brand_colour=skin == given_skin and accent_from == "given"))
    res = derive(hue, name=name, accent_override=override, link_hint=g.get("link"), **kw)
    skins = {"light": dict(res["roles"]), "dark": dict(res["dark"])}
    unresolved: List[dict] = []
    for skin in ("light", "dark"):
        roles = skins[skin]
        roles["paper"] = papers[skin]
        for r in ds.TEXT_ROLES:
            src = None
            if skin == given_skin and r in g and r != "accent":
                src = g[r]
            elif skin == other and r in g and r in ("link", "positive", "negative", "warning",
                                                     "deterministic", "model-judgement", "data-field", "vision"):
                src = g[r]          # hue/chroma carry to the other skin; lightness refits
            if src is None:
                if contrast(roles[r], roles["paper"]) < TEXT_TARGET:
                    v, adj = adjust_for_contrast(roles[r], roles["paper"], TEXT_TARGET)
                    roles[r] = v
                    if adj:
                        adjustments.append(dict(adj, skin=skin, role=r, kind="contrast", brand_colour=False))
                continue
            v, adj = adjust_for_contrast(src, roles["paper"], TEXT_TARGET)
            roles[r] = v
            if adj:
                adjustments.append(dict(adj, skin=skin, role=r, kind="contrast",
                                        brand_colour=skin == given_skin))
        brand_roles = {r for r in g if skin == given_skin} | ({"accent"} if accent else set())
        _separate_pairs(roles, brand_roles, skin, adjustments, unresolved)
        for r in ds.STANDARD_ROLES:
            if skin == given_skin and r in g:
                how = "brand (kept exactly)" if roles[r] == g[r] else "brand (adjusted)"
            elif r == "accent" and accent:
                how = f"brand accent ({accent_from}){'' if roles[r] == accent else ', lightness refit for this skin'}"
            elif skin == other and r in g:
                how = "brand hue, lightness refit for this skin"
            else:
                how = "derived"
            mapping.setdefault(skin, {})[r] = {"value": roles[r], "how": how}
    return {"roles": skins["light"], "dark": skins["dark"], "given": g, "given_skin": given_skin,
            "accent_from": accent_from if accent else "derived (no brand accent found)",
            "mapping": mapping, "adjustments": adjustments, "unresolved": unresolved + list(res.get("unresolved") or []),
            "accent_vs_paper_de": {s: round(delta_e(skins[s]["accent"], skins[s]["paper"]), 2) for s in skins}}


def _palette_gate():
    p = _SCRIPTS / "gates" / "check_palette.py"
    spec = importlib.util.spec_from_file_location("c8_check_palette", str(p))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore
    return mod


def gate_palette(pal: dict) -> dict:
    cp = _palette_gate()
    dec = cp.Declared(pal)
    findings, measured = cp.palette_findings(pal, dec)
    return {"gate": "palette", "status": "REVIEW" if findings else "PASS", "findings": findings,
            "measured": measured}


# ======================================================================  extraction
class _PageReader(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.css: List[str] = []
        self.inline: List[Tuple[str, str]] = []
        self.meta: Dict[str, str] = {}
        self.linked: List[str] = []
        self._in_style = False

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "style":
            self._in_style = True
        if tag == "meta" and a.get("name", "").lower() in ("theme-color", "msapplication-tilecolor"):
            self.meta[a["name"].lower()] = a.get("content", "")
        if tag == "link" and "stylesheet" in a.get("rel", "").lower():
            self.linked.append(a.get("href", ""))
        if a.get("style"):
            sel = tag + "".join("." + c for c in a.get("class", "").split())
            self.inline.append((sel, a["style"]))

    def handle_endtag(self, tag):
        if tag == "style":
            self._in_style = False

    def handle_data(self, data):
        if self._in_style:
            self.css.append(data)


def _strip_dark(css: str) -> Tuple[str, int]:
    """Remove dark-scheme blocks (they describe the other skin) — balanced-brace scan."""
    out, i, skipped = [], 0, 0
    pat = re.compile(r"@media[^{]*prefers-color-scheme\s*:\s*dark[^{]*\{|"
                     r"[^{}]*(?:\[data-theme=['\"]?dark['\"]?\]|\.dark\b|\.theme-dark\b)[^{}]*\{", re.I)
    while True:
        m = pat.search(css, i)
        if not m:
            out.append(css[i:])
            break
        out.append(css[i:m.start()])
        depth, j = 1, m.end()
        while j < len(css) and depth:
            depth += {"{": 1, "}": -1}.get(css[j], 0)
            j += 1
        skipped += 1
        i = j
    return "".join(out), skipped


class Found:
    """Candidate values for each role, with confidence and where each came from."""

    def __init__(self):
        self.roles: Dict[str, List[dict]] = {}
        self.fonts: Dict[str, List[dict]] = {}
        self.freq: Dict[str, int] = {}
        self.notes: List[str] = []

    def colour(self, role: Optional[str], value: str, conf: str, src: str):
        hx = norm_hex(value)
        if not hx:
            return
        self.freq[hx] = self.freq.get(hx, 0) + 1
        if role:
            self.roles.setdefault(role, []).append({"value": hx, "confidence": conf, "from": src})

    def font(self, role: str, stack: str, conf: str, src: str):
        stack = " ".join(str(stack).split()).strip().rstrip(";")
        if stack and not stack.lower().startswith(("inherit", "var(")):
            self.fonts.setdefault(role, []).append({"value": stack, "confidence": conf, "from": src})


def _resolve_vars(value: str, cssvars: Dict[str, str], depth: int = 0) -> str:
    m = re.search(r"var\(\s*(--[\w-]+)\s*(?:,\s*([^)]+))?\)", value)
    if not m or depth > 4:
        return value
    rep = cssvars.get(m.group(1), m.group(2) or "")
    return _resolve_vars(value[:m.start()] + rep + value[m.end():], cssvars, depth + 1)


def _first_colour(value: str) -> Optional[str]:
    v = value.strip()
    if norm_hex(v):
        return v
    m = COLOR_TOKEN.search(v)
    return m.group(0) if m else None


def scan_css(css: str, found: Found, src: str) -> None:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    css, nd = _strip_dark(css)
    if nd:
        found.notes.append(f"{src}: {nd} dark-scheme block(s) not used for the light roles")
    cssvars: Dict[str, str] = {}
    for m in re.finditer(r"(--[\w-]+)\s*:\s*([^;{}]+)", css):
        cssvars[m.group(1)] = m.group(2).strip()
    for m in re.finditer(r"(?m)^\s*\$([\w-]+)\s*:\s*([^;]+);", css):     # SCSS variables
        cssvars["--" + m.group(1)] = m.group(2).strip()
    for name, val in cssvars.items():
        val = _resolve_vars(val, cssvars)
        role = role_for_name(name)
        if "font" in name.lower() or "family" in name.lower():
            fr = "mono" if re.search(r"mono|code", name, re.I) else (
                "display" if re.search(r"head|display|title", name, re.I) else "body")
            found.font(fr, val, "high", f"{src} {name}")
            continue
        c = _first_colour(val)
        if c:
            found.colour(role, c, "high" if role else "low", f"{src} {name}")
    for m in re.finditer(r"([^{}@]+)\{([^{}]*)\}", css):
        sels = [s.strip().lower() for s in m.group(1).split(",")]
        body = m.group(2)
        decl = {}
        for d in body.split(";"):
            if ":" in d:
                k, v = d.split(":", 1)
                decl[k.strip().lower()] = _resolve_vars(v.strip(), cssvars)
        for sel in sels:
            last = re.split(r"[\s>+~]", sel)[-1] if sel else ""
            bg = decl.get("background-color") or decl.get("background")
            fg = decl.get("color")
            ff = decl.get("font-family")
            base = re.sub(r"[:.#\[].*$", "", last)
            if sel in ("body", "html", ":root", "main") or base in ("body", "html"):
                if bg and _first_colour(bg):
                    found.colour("paper", _first_colour(bg), "high", f"{src} {sel} background")
                if fg and _first_colour(fg):
                    found.colour("ink", _first_colour(fg), "high", f"{src} {sel} color")
                if ff:
                    found.font("body", ff, "high", f"{src} {sel} font-family")
            elif base in ("h1", "h2", "h3") or "title" in last or "heading" in last:
                if ff:
                    found.font("display", ff, "high", f"{src} {sel} font-family")
                if fg and _first_colour(fg):
                    found.colour(None, _first_colour(fg), "low", f"{src} {sel} color")
            elif base in ("code", "pre", "kbd", "samp"):
                if ff:
                    found.font("mono", ff, "high", f"{src} {sel} font-family")
            elif base == "a":
                if fg and _first_colour(fg):
                    found.colour("link", _first_colour(fg), "medium", f"{src} {sel} color")
            elif base == "button" or re.search(r"\b(btn|button|cta)\b", last.replace(".", " ")):
                if bg and _first_colour(bg):
                    found.colour("accent", _first_colour(bg), "medium", f"{src} {sel} background")
            elif re.search(r"muted|caption|meta|subtle|secondary", last):
                if fg and _first_colour(fg):
                    found.colour("muted", _first_colour(fg), "medium", f"{src} {sel} color")
            else:
                for v in (bg, fg):
                    if v and _first_colour(v):
                        found.colour(None, _first_colour(v), "low", f"{src} {sel}")


def scan_html(text: str, found: Found, src: str) -> None:
    rd = _PageReader()
    try:
        rd.feed(text)
    except Exception as e:  # malformed markup is data, not a crash
        found.notes.append(f"{src}: markup partly unreadable ({type(e).__name__})")
    for css in rd.css:
        scan_css(css, found, src)
    for sel, style in rd.inline:
        scan_css(f"{sel} {{{style}}}", found, src + " inline")
    tc = rd.meta.get("theme-color")
    if tc and norm_hex(tc):
        found.colour("accent", tc, "medium", f"{src} meta theme-color")
    if rd.linked:
        found.notes.append(f"{src}: {len(rd.linked)} linked stylesheet(s) were NOT fetched — only the given "
                           f"page is read")


def scan_tokens(obj, found: Found, src: str, path: Tuple[str, ...] = ()) -> None:
    if isinstance(obj, dict):
        if "value" in obj and isinstance(obj["value"], str) and len(path):
            scan_tokens(obj["value"], found, src, path)
            return
        for k, v in obj.items():
            scan_tokens(v, found, src, path + (str(k),))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            scan_tokens(v, found, src, path + (str(i),))
    elif isinstance(obj, str) and path:
        name = "-".join(path)
        if re.search(r"font|family|typeface", name, re.I):
            fr = "mono" if re.search(r"mono|code", name, re.I) else (
                "display" if re.search(r"head|display|title", name, re.I) else "body")
            found.font(fr, obj, "high", f"{src} {name}")
        elif norm_hex(obj):
            role = role_for_name(name)
            found.colour(role, obj, "high" if role else "low", f"{src} {name}")


def scan_markdown(text: str, found: Found, src: str) -> None:
    for line in text.splitlines():
        hexes = re.findall(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b", line)
        if not hexes:
            continue
        words = re.sub(r"`|\*|\|", " ", line.split(hexes[0])[0])
        role = role_for_name(words) if words.strip() else None
        found.colour(role, hexes[0], "medium" if role else "low", f"{src} markdown")


def read_folder(folder: Path, found: Found) -> int:
    n = 0
    files = []
    for p in sorted(folder.rglob("*")):
        rel = p.relative_to(folder)
        if len(rel.parts) > FOLDER_DEPTH or any(part.startswith(".") for part in rel.parts):
            continue
        if p.is_file() and p.suffix.lower() in (".css", ".scss", ".json", ".md", ".html", ".htm"):
            files.append(p)
    pri = re.compile(r"colou?r|token|brand|palette|style|theme", re.I)
    files.sort(key=lambda p: (0 if pri.search(p.name) else 1, len(p.relative_to(folder).parts), str(p)))
    for p in files[:FOLDER_MAX_FILES]:
        if p.stat().st_size > FILE_MAX_BYTES:
            found.notes.append(f"{p.name}: larger than {FILE_MAX_BYTES} bytes, not read")
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        src = str(p.relative_to(folder))
        suf = p.suffix.lower()
        if suf in (".css", ".scss"):
            scan_css(text, found, src)
        elif suf == ".json":
            try:
                scan_tokens(json.loads(text), found, src)
            except json.JSONDecodeError:
                found.notes.append(f"{src}: not valid JSON")
        elif suf == ".md":
            scan_markdown(text, found, src)
        else:
            scan_html(text, found, src)
        n += 1
    if len(files) > FOLDER_MAX_FILES:
        found.notes.append(f"{len(files) - FOLDER_MAX_FILES} more file(s) not read (cap {FOLDER_MAX_FILES})")
    return n


def fetch_page(url: str, timeout: float = FETCH_TIMEOUT) -> Tuple[str, str]:
    """Fetch ONE page (no assets) with the standard library. Returns (text, final_url)."""
    p = urllib.parse.urlparse(url)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise BrandError(f"only http(s) page URLs are read, not {url!r}")
    req = urllib.request.Request(url, headers={"User-Agent": "Coordn8-brand-intake/2 (one page, no assets)",
                                               "Accept": "text/html,text/css;q=0.9,*/*;q=0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec - user-supplied public page
        final = r.geturl()
        if urllib.parse.urlparse(final).scheme not in ("http", "https"):
            raise BrandError("redirected away from http(s); not read")
        data = r.read(FETCH_MAX_BYTES + 1)
        charset = r.headers.get_content_charset() or "utf-8"
        ctype = (r.headers.get_content_type() or "").lower()
    if len(data) > FETCH_MAX_BYTES:
        data = data[:FETCH_MAX_BYTES]
    text = data.decode(charset, errors="replace")
    return (text if ctype != "text/css" else f"<style>{text}</style>"), final


def choose_roles(found: Found) -> Tuple[Dict[str, str], List[dict]]:
    """Pick one value per role: highest confidence, then most frequent, then first seen.
    Roles with no named candidate are inferred from frequency and contrast (confidence low)."""
    picked: Dict[str, str] = {}
    detected: List[dict] = []
    for role in ds.STANDARD_ROLES:
        cands = found.roles.get(role) or []
        if not cands:
            continue
        best = max(enumerate(cands), key=lambda ic: (CONF_RANK[ic[1]["confidence"]],
                                                     found.freq.get(ic[1]["value"], 0), -ic[0]))[1]
        picked[role] = best["value"]
        detected.append({"role": role, **best,
                         "alternatives": sorted({c["value"] for c in cands} - {best["value"]})})
    pool = sorted(found.freq.items(), key=lambda kv: (-kv[1], kv[0]))
    if "paper" not in picked:
        light = [h for h, _ in pool if rgb_to_oklch(h)[0] > 0.9 and chroma(h) < 0.04]
        if light:
            picked["paper"] = light[0]
            detected.append({"role": "paper", "value": light[0], "confidence": "low",
                             "from": "inferred: most frequent near-white"})
    paper = picked.get("paper", "#ffffff")
    if "ink" not in picked:
        dark = [h for h, _ in pool if contrast(h, paper) >= 7 and chroma(h) < 0.06]
        if dark:
            picked["ink"] = dark[0]
            detected.append({"role": "ink", "value": dark[0], "confidence": "low",
                             "from": "inferred: most frequent high-contrast neutral"})
    if "accent" not in picked:
        chroms = [h for h, _ in pool if chroma(h) >= 0.08 and h not in picked.values()]
        if chroms:
            picked["accent"] = chroms[0]
            detected.append({"role": "accent", "value": chroms[0], "confidence": "low",
                             "from": "inferred: most frequent saturated colour"})
    return picked, detected


def choose_fonts(found: Found, stated: Dict[str, str]) -> Tuple[Dict[str, str], List[str]]:
    out: Dict[str, str] = {}
    notes: List[str] = []
    for role in FONT_ROLES:
        if role in stated:
            raw = stated[role]
        else:
            c = found.fonts.get(role) or []
            if not c:
                continue
            raw = max(enumerate(c), key=lambda ic: (CONF_RANK[ic[1]["confidence"]], -ic[0]))[1]["value"]
        st, note = font_stack(role, raw)
        if st:
            out[role] = st
            if note:
                notes.append(note)
    if out:
        notes.append("fonts are recorded by name only — nothing is downloaded; a face renders exactly only "
                     "where it is installed or vendored, otherwise the generic family renders")
    return out, notes


# ======================================================================  profile
def slugify(s: str) -> str:
    v = re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")[:64].strip("-")
    return v or "brand"


def valid_slug(slug: str) -> bool:
    return bool(SLUG_RE.match(str(slug or "")))


def profiles_dir() -> Path:
    return user_dir("profiles")


def profile_path(slug: str) -> Path:
    if not valid_slug(slug):
        raise BrandError(f"invalid profile slug {slug!r}: use [a-z0-9][a-z0-9-]{{0,63}} (no dots, slashes, spaces)")
    d = profiles_dir()
    p = d / f"{slug}.json"
    if p.resolve().parent != d.resolve():
        raise BrandError(f"profile path escapes the profile library: {p}")
    return p


def serialise(profile: dict) -> bytes:
    return (json.dumps(profile, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")


def save_profile(profile: dict, overwrite: bool = False) -> Tuple[Path, str]:
    p = profile_path(profile["slug"])
    data = serialise(profile)
    if p.exists():
        if p.read_bytes() == data:
            return p, "unchanged"
        if not overwrite:
            raise BrandError(f"a different profile '{profile['slug']}' already exists at {p}; "
                             f"pass --overwrite to replace it")
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".profile-", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return p, "written"


def load_profile(slug: str) -> dict:
    p = profile_path(slug)
    if not p.exists():
        raise BrandError(f"no saved profile '{slug}' in {profiles_dir()}")
    prof = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(prof, dict) or prof.get("kind") != "coordn8-brand-profile" or prof.get("slug") != slug:
        raise BrandError(f"{p} is not a valid brand profile for '{slug}'")
    return prof


def list_profiles() -> List[dict]:
    d = profiles_dir()
    out = []
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.json")):
        row = {"file": p.name, "slug": p.stem}
        try:
            prof = load_profile(p.stem)
            row.update(name=prof.get("name"), source=prof.get("source", {}).get("kind"),
                       accent=prof["palette"]["roles"].get("accent"), valid=True)
        except (BrandError, ValueError, KeyError) as e:
            row.update(valid=False, why=str(e))
        out.append(row)
    return out


def build_profile(given: Dict[str, str], *, name: str, slug: str, source: dict, detected: List[dict],
                  fonts: Dict[str, str], font_notes: List[str], notes: List[str],
                  given_skin: Optional[str] = None) -> dict:
    pal = complete_palette(given, given_skin=given_skin, name=f"invented:brand-{slug}")
    palette = {"family": f"invented:brand-{slug}", "roles": pal["roles"], "dark": pal["dark"],
               "chart_series": chart_series(pal["roles"], pal["dark"]),
               "source": f"brand-profile:{slug}"}
    gate = gate_palette(palette)
    return {
        "schema": 1, "kind": "coordn8-brand-profile", "slug": slug, "name": name, "source": source,
        "given": {"skin": pal["given_skin"], "colours": pal["given"], "fonts": dict(fonts)},
        "detected": detected, "palette": palette, "fonts": fonts,
        "mapping": pal["mapping"], "accent_from": pal["accent_from"],
        "adjustments": pal["adjustments"], "unresolved": pal["unresolved"],
        "font_notes": font_notes, "notes": notes,
        "checks": {"palette_gate": gate["status"], "findings": gate["findings"], "measured": gate["measured"],
                   "accent_vs_paper_delta_e": pal["accent_vs_paper_de"]},
    }


# ======================================================================  diff / apply
def diff(build: Path, profile: dict) -> dict:
    cur = ds.read(build)
    pal = cur.get("palette") if isinstance(cur.get("palette"), dict) else {}
    rows, lines = [], []
    for skin, key in (("light", "roles"), ("dark", "dark")):
        now = (pal or {}).get(key) or {}
        new = profile["palette"][key]
        for r in ds.STANDARD_ROLES:
            a, b = now.get(r), new.get(r)
            if a == b:
                continue
            de = round(delta_e(a, b), 2) if (a and b and norm_hex(a)) else None
            rows.append({"skin": skin, "role": r, "current": a, "proposed": b, "delta_e": de})
            if a:
                lines.append(f"- {skin}.{r:<16} {a}")
            lines.append(f"+ {skin}.{r:<16} {b}" + (f"   (ΔE {de})" if de is not None else ""))
    cur_type = cur.get("type") if isinstance(cur.get("type"), dict) else {}
    tkey = {"display": "display", "body": "family", "mono": "mono"}
    for role, stack in (profile.get("fonts") or {}).items():
        a = (cur_type or {}).get(tkey[role])
        if a != stack:
            rows.append({"skin": "type", "role": role, "current": a, "proposed": stack, "delta_e": None})
            if a:
                lines.append(f"- type.{role:<13} {a}")
            lines.append(f"+ type.{role:<13} {stack}")
    return {"design_system": str(ds.path_for(build)), "current_family": (pal or {}).get("family"),
            "changes": rows, "diff_text": "\n".join(lines) if lines else "(no change)"}


def apply_profile(build: Path, profile: dict) -> dict:
    cur = ds.read(build)
    values = {"palette": dict(profile["palette"], adjustments=profile.get("adjustments", [])),
              "brand": {"slug": profile["slug"], "name": profile["name"], "source": profile["source"],
                        "fonts": profile.get("fonts", {}), "adjustments": profile.get("adjustments", [])}}
    if profile.get("fonts"):
        t = dict(cur.get("type") or {}) if isinstance(cur.get("type"), dict) else {}
        f = profile["fonts"]
        if "body" in f:
            t["family"] = f["body"]
        if "display" in f:
            t["display"] = f["display"]
        if "mono" in f:
            t["mono"] = f["mono"]
        values["type"] = t
    ds.update_many(build, values)
    return {"written": str(ds.path_for(build)), "keys": sorted(values)}


# ======================================================================  CLI
def _parse_colours(tokens: List[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    bare: List[str] = []
    for t in tokens:
        for part in re.split(r"[,\s]+", t.strip()):
            if not part:
                continue
            if "=" in part:
                k, v = part.split("=", 1)
                k = k.strip().lower().replace("_", "-")
                if k == "model-judgment":
                    k = "model-judgement"
                if k not in ds.STANDARD_ROLES:
                    raise BrandError(f"unknown role {k!r}; roles are {', '.join(ds.STANDARD_ROLES)}")
                if not norm_hex(v):
                    raise BrandError(f"{k}: {v!r} is not an opaque colour")
                out[k] = norm_hex(v)
            else:
                if not norm_hex(part):
                    raise BrandError(f"{part!r} is not an opaque colour")
                bare.append(norm_hex(part))
    for role, v in zip(("accent", "link"), bare):
        out.setdefault(role, v)
    return out


def _fonts_arg(items: List[str]) -> Dict[str, str]:
    out = {}
    for it in items or []:
        if "=" not in it:
            raise BrandError(f"--font wants ROLE=STACK, got {it!r}")
        k, v = it.split("=", 1)
        k = k.strip().lower()
        if k not in FONT_ROLES:
            raise BrandError(f"font role must be one of {', '.join(FONT_ROLES)}")
        out[k] = v.strip()
    return out


def _finish(res: dict, profile: dict, a) -> int:
    code = EXIT_PASS if profile["checks"]["palette_gate"] == "PASS" and not profile["unresolved"] else EXIT_REVIEW
    res["adjustments"] = profile["adjustments"]
    res["adjusted_brand_colours"] = [f"{x['skin']}.{x['role']}: {x['from']} -> {x['to']} ({x.get('kind')}) — {x['why']}"
                                     for x in profile["adjustments"] if x.get("brand_colour")]
    if getattr(a, "save", False):
        p, how = save_profile(profile, overwrite=a.overwrite)
        res["profile"] = {"path": str(p), "status": how}
    if getattr(a, "build", None):
        res["diff"] = diff(Path(a.build), profile)
        if a.apply:
            res["applied"] = apply_profile(Path(a.build), profile)
        else:
            res["applied"] = None
            res["refused"] = "nothing written: the diff is shown for approval; re-run with --apply to write it"
            code = max(code, EXIT_REVIEW)
    res["status"] = {EXIT_PASS: "PASS", EXIT_REVIEW: "REVIEW"}[code]
    res["summary"] = (f"profile '{profile['slug']}': gate {profile['checks']['palette_gate']}, "
                      f"{len(res['adjusted_brand_colours'])} brand colour adjustment(s)"
                      + ("; " + res["refused"] if res.get("refused") else ""))
    res["profile_doc"] = profile if a.json_full else None
    emit(res)
    return code


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Coordn8 brand onboarding and profiles")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--name")
        p.add_argument("--slug")
        p.add_argument("--font", action="append", default=[])
        p.add_argument("--skin", choices=("light", "dark"))
        p.add_argument("--save", action="store_true")
        p.add_argument("--overwrite", action="store_true")
        p.add_argument("--build")
        p.add_argument("--apply", action="store_true")
        p.add_argument("--json-full", action="store_true", help="include the whole profile in the output")

    pc = sub.add_parser("colours", aliases=["colors"])
    pc.add_argument("values", nargs="+")
    common(pc)
    pu = sub.add_parser("url")
    pu.add_argument("url")
    pu.add_argument("--timeout", type=float, default=FETCH_TIMEOUT)
    common(pu)
    pf = sub.add_parser("folder")
    pf.add_argument("folder")
    common(pf)
    pl = sub.add_parser("load")
    pl.add_argument("slug")
    sub.add_parser("list")
    pa = sub.add_parser("apply")
    pa.add_argument("slug")
    pa.add_argument("--build", required=True)
    pa.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_usage(sys.stderr)
        return EXIT_ERROR
    try:
        if a.cmd == "list":
            emit({"gate": GATE, "status": "PASS", "summary": f"profiles in {profiles_dir()}",
                  "profiles": list_profiles()})
            return EXIT_PASS
        if a.cmd == "load":
            prof = load_profile(a.slug)
            print(serialise(prof).decode("utf-8"), end="")
            return EXIT_PASS
        if a.cmd == "apply":
            prof = load_profile(a.slug)
            res = {"gate": GATE, "slug": a.slug, "diff": diff(Path(a.build), prof)}
            if a.apply:
                res["applied"] = apply_profile(Path(a.build), prof)
                res.update(status="PASS", summary=f"profile '{a.slug}' written to {res['applied']['written']}")
                emit(res)
                return EXIT_PASS
            res.update(status="REVIEW", applied=None,
                       refused="nothing written: the diff is shown for approval; re-run with --apply to write it",
                       summary=f"refused without --apply; {len(res['diff']['changes'])} change(s) pending")
            emit(res)
            return EXIT_REVIEW
        stated_fonts = _fonts_arg(a.font)
        found = Found()
        notes: List[str] = []
        if a.cmd in ("colours", "colors"):
            given = _parse_colours(a.values)
            detected = [{"role": r, "value": v, "confidence": "stated", "from": "stated by the user"}
                        for r, v in given.items()]
            source = {"kind": "stated"}
            slug = a.slug or slugify(a.name or "brand")
        elif a.cmd == "url":
            try:
                text, final = fetch_page(a.url, a.timeout)
            except (urllib.error.URLError, OSError, ValueError) as e:
                if isinstance(e, BrandError):
                    raise
                emit({"gate": GATE, "status": "SKIPPED",
                      "summary": f"could not fetch {a.url} ({type(e).__name__}: {getattr(e, 'reason', e)}); "
                                 f"offline or unreachable — use a local folder or stated colours instead"})
                return EXIT_SKIPPED
            scan_html(text, found, "page")
            given, detected = choose_roles(found)
            source = {"kind": "url", "url": final, "pages_fetched": 1}
            slug = a.slug or slugify(a.name or urllib.parse.urlparse(final).hostname or "brand")
        else:
            folder = Path(a.folder)
            if not folder.is_dir():
                raise BrandError(f"not a folder: {folder}")
            n = read_folder(folder, found)
            given, detected = choose_roles(found)
            source = {"kind": "folder", "folder": folder.name, "files_read": n}
            slug = a.slug or slugify(a.name or folder.name)
        notes += found.notes
        if not given:
            emit({"gate": GATE, "status": "SKIPPED", "summary": "no colours found in the source", "notes": notes})
            return EXIT_SKIPPED
        if not valid_slug(slug):
            raise BrandError(f"invalid profile slug {slug!r}")
        fonts, font_notes = choose_fonts(found, stated_fonts)
        prof = build_profile(given, name=a.name or slug, slug=slug, source=source, detected=detected,
                             fonts=fonts, font_notes=font_notes, notes=notes, given_skin=a.skin)
        res = {"gate": GATE, "slug": slug, "source": source, "detected": detected,
               "palette": prof["palette"], "fonts": fonts, "font_notes": font_notes, "notes": notes,
               "checks": prof["checks"], "unresolved": prof["unresolved"]}
        return _finish(res, prof, a)
    except (BrandError, OSError, json.JSONDecodeError) as e:
        emit({"gate": GATE, "status": "ERROR", "summary": f"{type(e).__name__}: {e}"})
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
