#!/usr/bin/env python3
"""Coordn8 — STYLE KIT picker, validator, blender (requirement A3, with H1 and H3).

A style kit is one coherent visual language for a build: palette family, type,
stage (background, grid, safe area, radius, strokes), props (card, chip, node,
connector, callout), charts, motion signature, transition vocabulary and caption
style, plus do / don't. The shipped kits in data/style_kits/*.json are MENUS OF
STARTING POINTS, never fences (doctrine H1):

  * any kit may be picked, two may be BLENDED, or a hand-defined / invented kit
    may be supplied — each recorded with a reason;
  * a kit is judged ONLY by outcome rules (legibility, contrast, valid easing,
    sane timing, distinguishable marks, declared colour roles). Nothing here
    checks whether a kit, a transition name, an entrance name or a background
    treatment appears in any catalog.

    python3 scripts/design/pick_kit.py <brief.json> [--build DIR] [--kit FILE]
                                       [--blend a,b] [--take motion,transitions]
                                       [--palette FILE] [--reason TEXT] [--json]
    python3 scripts/design/pick_kit.py --validate FILE      # outcome rules only

brief.json: {"subject": "...", "audience": "...", "tone": "...", "stated": "<what the
user said about the look, verbatim>", "seed": 123}. A stated look ALWAYS wins.

Writes into <DIR>/design_system.json (read-modify-write; every other key kept):
  kit, type, motion, transitions, caption_style, kit_spec, kit_choice
Exit codes: 0 picked · 3 error (bad input / kit failed the outcome rules).
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import random
import re
import sys
import tempfile
import zlib
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from lib.paths import PKG_ROOT  # noqa: E402

try:  # single source of truth for caption line limits
    from lib.common import CAPTION_MAX_LINES  # noqa: E402
except Exception:  # pragma: no cover
    CAPTION_MAX_LINES = 2

KITS_DIR = PKG_ROOT / "data" / "style_kits"
SCHEMA = 1

# The 1080p legibility floor — identical to MIN_LEGIBLE_PX in scene_runtime.js.
MIN_PX = 28
TEXT_MIN = 4.5          # WCAG AA body text
LARGE_MIN = 3.0         # WCAG AA large text / non-text graphics (>= 24 px)
ROLE_DELTA_E_MIN = 10.0  # CIEDE2000: below this two colours read as one
STAGGER_MAX_MS = 400
REPEAT_PENALTY = 0.35    # SPEC §4 anti-repetition weight
CLEAR_MARGIN = 0.35      # a lead this large means the subject clearly calls for the kit

BASE_ROLES = ("paper", "ink", "muted", "accent", "link", "positive", "negative", "warning",
              "deterministic", "model-judgement", "data-field", "vision")
TEXT_ROLES = ("ink", "muted", "link")
GRAPHIC_ROLES = ("accent", "positive", "negative", "warning",
                 "deterministic", "model-judgement", "data-field", "vision")
LEGEND_ROLES = ("deterministic", "model-judgement", "data-field", "vision")
REQUIRED_SECTIONS = ("theme", "stage", "props", "charts", "motion", "transitions",
                     "caption_style", "do", "dont")


# =====================================================================  colour
def _hex(c: str) -> tuple:
    s = str(c).strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if not re.fullmatch(r"[0-9a-fA-F]{6}", s):
        raise ValueError(f"not a hex colour: {c!r}")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def is_hex(c) -> bool:
    try:
        _hex(c)
        return True
    except Exception:
        return False


def to_hex(rgb) -> str:
    return "#" + "".join(f"{max(0, min(255, int(round(v)))):02x}" for v in rgb)


def mix(fg: str, bg: str, alpha: float) -> str:
    """fg composited over bg at alpha (straight alpha, sRGB space as browsers do)."""
    a, b = _hex(fg), _hex(bg)
    return to_hex(tuple(alpha * x + (1 - alpha) * y for x, y in zip(a, b)))


def _lin(v: float) -> float:
    v = v / 255.0
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def luminance(c: str) -> float:
    r, g, b = (_lin(x) for x in _hex(c))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _own_contrast(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _lab(c: str) -> tuple:
    r, g, b = (_lin(x) for x in _hex(c))
    x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047
    y = (0.2126729 * r + 0.7151522 * g + 0.0721750 * b) / 1.0
    z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / 1.08883

    def f(t):
        return t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116
    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def _own_delta_e(c1: str, c2: str) -> float:
    """CIEDE2000 (Sharma, Wu & Dalal 2005 formulation)."""
    L1, a1, b1 = _lab(c1)
    L2, a2, b2 = _lab(c2)
    C1, C2 = math.hypot(a1, b1), math.hypot(a2, b2)
    Cb = (C1 + C2) / 2
    G = 0.5 * (1 - math.sqrt(Cb ** 7 / (Cb ** 7 + 25 ** 7)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360
    h2p = math.degrees(math.atan2(b2, a2p)) % 360
    dLp, dCp = L2 - L1, C2p - C1p
    if C1p * C2p == 0:
        dhp = 0.0
    elif abs(h2p - h1p) <= 180:
        dhp = h2p - h1p
    elif h2p - h1p > 180:
        dhp = h2p - h1p - 360
    else:
        dhp = h2p - h1p + 360
    dHp = 2 * math.sqrt(C1p * C2p) * math.sin(math.radians(dhp / 2))
    Lbp, Cbp = (L1 + L2) / 2, (C1p + C2p) / 2
    if C1p * C2p == 0:
        hbp = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hbp = (h1p + h2p) / 2
    elif h1p + h2p < 360:
        hbp = (h1p + h2p + 360) / 2
    else:
        hbp = (h1p + h2p - 360) / 2
    T = (1 - 0.17 * math.cos(math.radians(hbp - 30)) + 0.24 * math.cos(math.radians(2 * hbp))
         + 0.32 * math.cos(math.radians(3 * hbp + 6)) - 0.20 * math.cos(math.radians(4 * hbp - 63)))
    dtheta = 30 * math.exp(-(((hbp - 275) / 25) ** 2))
    Rc = 2 * math.sqrt(Cbp ** 7 / (Cbp ** 7 + 25 ** 7))
    Sl = 1 + (0.015 * (Lbp - 50) ** 2) / math.sqrt(20 + (Lbp - 50) ** 2)
    Sc = 1 + 0.045 * Cbp
    Sh = 1 + 0.015 * Cbp * T
    Rt = -math.sin(math.radians(2 * dtheta)) * Rc
    return math.sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2
                     + Rt * (dCp / Sc) * (dHp / Sh))


def _probe(fn, a, b, expect, tol):
    try:
        v = float(fn(a, b))
        return abs(v - expect) <= tol
    except Exception:
        return False


def _bind_shared():
    """Use the palette stream's design.color when it is importable AND agrees with the
    reference values; otherwise the local implementation (identical maths)."""
    cr, de = _own_contrast, _own_delta_e
    try:
        from design import color as _c  # type: ignore
        for name in ("contrast_ratio", "contrast", "wcag_contrast"):
            f = getattr(_c, name, None)
            if f and _probe(f, "#000000", "#ffffff", 21.0, 0.05):
                cr = f
                break
        for name in ("delta_e", "delta_e2000", "ciede2000", "de2000"):
            f = getattr(_c, name, None)
            if f and _probe(f, "#ff0000", "#00ff00", _own_delta_e("#ff0000", "#00ff00"), 0.5):
                de = f
                break
    except Exception:
        pass
    return cr, de


contrast, delta_e = _bind_shared()


# =====================================================================  easing
_BEZ = re.compile(r"^\s*cubic-bezier\(\s*([^,()]+),([^,()]+),([^,()]+),([^,()]+)\)\s*$")


def parse_bezier(s) -> tuple | None:
    """'cubic-bezier(x1,y1,x2,y2)' -> floats, or None when it is not a valid CSS curve
    (x1/x2 must lie in [0, 1]; y may overshoot)."""
    m = _BEZ.match(str(s or ""))
    if not m:
        return None
    try:
        pts = tuple(float(v) for v in m.groups())
    except ValueError:
        return None
    if not all(math.isfinite(v) for v in pts):
        return None
    x1, _, x2, _ = pts
    if not (0.0 <= x1 <= 1.0 and 0.0 <= x2 <= 1.0):
        return None
    return pts


def bezier_y(pts, x: float) -> float:
    """Evaluate a CSS cubic-bezier at progress x (Newton + bisection). Deterministic."""
    x1, y1, x2, y2 = pts
    x = min(1.0, max(0.0, x))

    def bx(u):
        return 3 * (1 - u) ** 2 * u * x1 + 3 * (1 - u) * u * u * x2 + u ** 3

    def by(u):
        return 3 * (1 - u) ** 2 * u * y1 + 3 * (1 - u) * u * u * y2 + u ** 3

    def dbx(u):
        return 3 * (1 - u) ** 2 * x1 + 6 * (1 - u) * u * (x2 - x1) + 3 * u * u * (1 - x2)

    u = x
    for _ in range(8):
        d = dbx(u)
        if abs(d) < 1e-7:
            break
        u2 = u - (bx(u) - x) / d
        if not 0 <= u2 <= 1:
            break
        u = u2
    if abs(bx(u) - x) > 1e-5:
        lo, hi = 0.0, 1.0
        for _ in range(40):
            u = (lo + hi) / 2
            if bx(u) < x:
                lo = u
            else:
                hi = u
    return by(u)


def is_linear(pts) -> bool:
    x1, y1, x2, y2 = pts
    return abs(x1 - y1) < 1e-6 and abs(x2 - y2) < 1e-6


# =====================================================================  kits
def load_kits(directory: Path | None = None) -> list:
    d = Path(directory) if directory else KITS_DIR
    out = []
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.json")):
        try:
            k = json.loads(p.read_text(encoding="utf-8"))
        except Exception as ex:
            raise ValueError(f"{p.name}: not valid JSON ({ex})")
        k.setdefault("_file", p.name)
        out.append(k)
    return out


def kit_by_id(kid: str, kits: list) -> dict | None:
    for k in kits:
        if k.get("id") == kid:
            return k
    return None


def deep_merge(base, over):
    if isinstance(base, dict) and isinstance(over, dict):
        out = copy.deepcopy(base)
        for k, v in over.items():
            out[k] = deep_merge(out.get(k), v) if k in out else copy.deepcopy(v)
        return out
    return copy.deepcopy(over)


def resolve(kit: dict, kits: list | None = None) -> dict:
    """Apply an optional `extends: <kit id>` (a convenience for hand-defined kits —
    a starting point, never a requirement)."""
    base_id = kit.get("extends")
    if not base_id:
        return copy.deepcopy(kit)
    base = kit_by_id(base_id, kits if kits is not None else load_kits())
    if base is None:
        raise ValueError(f"extends: no kit with id {base_id!r} to start from")
    merged = deep_merge(resolve(base, kits), {k: v for k, v in kit.items() if k != "extends"})
    merged["extended_from"] = base_id
    return merged


def skins(kit: dict) -> dict:
    """{'light': roles, 'dark': roles}. Same shape as data/palettes.json: `roles` is the light
    skin, `dark` the dark skin. `base` says which one the video renders in."""
    pal = kit.get("palette") or {}
    return {"light": dict(pal.get("roles") or {}), "dark": dict(pal.get("dark") or {}),
            "_base": kit.get("base", "light")}


def with_skin(kit: dict, skin: dict | None) -> dict:
    """The kit as it will actually render once the Palette Director's result is applied:
    skin roles override the kit's own palette (one design system per build)."""
    if not skin or not skin.get("roles"):
        return kit
    k = copy.deepcopy(kit)
    pal = k.setdefault("palette", {})
    light = dict(skin.get("roles") or {})
    dark = dict(skin.get("dark") or {}) or light
    pal["roles"] = {**(pal.get("roles") or {}), **light}
    pal["dark"] = {**(pal.get("dark") or {}), **dark}
    pal["family"] = skin.get("family") or skin.get("id") or pal.get("family")
    pal["from_palette_director"] = True
    return k


def role_color(roles: dict, ref) -> str | None:
    """A colour reference: {'role': name, 'alpha': a} or 'name'. Resolved over paper."""
    if isinstance(ref, str):
        ref = {"role": ref}
    if not isinstance(ref, dict):
        return None
    name = ref.get("role")
    if name not in roles or not is_hex(roles[name]):
        return None
    a = ref.get("alpha", 1.0)
    try:
        a = float(a)
    except (TypeError, ValueError):
        return None
    if a >= 1:
        return roles[name]
    return mix(roles[name], roles["paper"], max(0.0, a))


def _iter_role_refs(obj, path=""):
    """Every {'role': ...} reference anywhere in props/charts/caption/stage."""
    if isinstance(obj, dict):
        if "role" in obj and isinstance(obj["role"], str):
            yield path, obj
        for k, v in obj.items():
            yield from _iter_role_refs(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _iter_role_refs(v, f"{path}[{i}]")


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


# =====================================================================  validation
def validate_kit(kit: dict) -> list:
    """OUTCOME RULES ONLY. Returns a list of human-readable failures (empty = valid).

    Deliberately takes NO catalog argument: a kit, transition, entrance or background
    name that no catalog has ever seen is fine; only what a viewer would experience
    (unreadable text, low contrast, linear or broken easing, frantic stagger, marks that
    differ by hue alone, colours outside the declared roles) can fail it."""
    errs = []
    if not isinstance(kit, dict):
        return ["kit must be a JSON object"]
    if kit.get("schema") != SCHEMA:
        errs.append(f"schema must be {SCHEMA}")
    if not str(kit.get("id") or kit.get("name") or "").strip():
        errs.append("kit needs an id or a name")
    for sec in REQUIRED_SECTIONS:
        if sec not in kit:
            errs.append(f"missing section: {sec}")
    if errs:
        return errs

    # ---- palette: every base role present in both skins; text contrast
    sk = skins(kit)
    for mode in ("light", "dark"):
        roles = sk[mode]
        missing = [r for r in BASE_ROLES if r not in roles]
        if missing:
            errs.append(f"{mode} skin missing roles: {', '.join(missing)}")
            continue
        bad = [r for r, v in roles.items() if not is_hex(v)]
        if bad:
            errs.append(f"{mode} skin has non-hex roles: {', '.join(bad)}")
            continue
        paper = roles["paper"]
        for r in TEXT_ROLES:
            c = contrast(roles[r], paper)
            if c < TEXT_MIN:
                errs.append(f"{mode}: text role '{r}' {roles[r]} on paper {paper} is {c:.2f}:1 (< {TEXT_MIN}:1)")
        for r in GRAPHIC_ROLES:
            c = contrast(roles[r], paper)
            if c < LARGE_MIN:
                errs.append(f"{mode}: role '{r}' {roles[r]} on paper is {c:.2f}:1 (< {LARGE_MIN}:1 for "
                            f"large text and graphics)")
        for i, a in enumerate(LEGEND_ROLES):
            for b in LEGEND_ROLES[i + 1:]:
                d = delta_e(roles[a], roles[b])
                if d < ROLE_DELTA_E_MIN:
                    errs.append(f"{mode}: legend roles '{a}' and '{b}' are indistinguishable "
                                f"(CIEDE2000 {d:.1f} < {ROLE_DELTA_E_MIN})")
    # Palette failures do not stop the other outcome rules: every failure is reported at once.
    palette_ok = not errs and all(is_hex(v) for m in ("light", "dark") for v in sk[m].values())

    all_roles = set(sk["light"]) | set(sk["dark"])
    for sec in ("stage", "props", "charts", "caption_style"):
        for path, ref in _iter_role_refs(kit.get(sec)):
            if ref["role"] not in all_roles:
                errs.append(f"{sec}.{path}: colour role '{ref['role']}' is not declared in the palette")

    # ---- type: offline-safe stacks, scale at or above the legibility floor
    theme = kit.get("theme") or {}
    fam = theme.get("family") or {}
    if not isinstance(fam, dict) or not fam.get("body"):
        errs.append("theme.family.body (a font stack) is required")
    for slot, stack in (fam.items() if isinstance(fam, dict) else []):
        s = str(stack)
        if re.search(r"(https?:|//|url\()", s, re.I):
            errs.append(f"theme.family.{slot}: font stacks must be offline (no URL / CDN)")
        last = s.split(",")[-1].strip().strip("'\"").lower()
        if last not in ("serif", "sans-serif", "monospace", "system-ui", "cursive", "ui-sans-serif",
                        "ui-serif", "ui-monospace", "ui-rounded", "fantasy"):
            errs.append(f"theme.family.{slot}: stack must end in a generic family so it always renders")
    scale = theme.get("scale") or {}
    if not scale:
        errs.append("theme.scale is required")
    for k, v in scale.items():
        if not _num(v) or v < MIN_PX:
            errs.append(f"theme.scale.{k} = {v} px is below the {MIN_PX} px 1080p legibility floor")
    ps = theme.get("page_scale") or {}
    if ps and (not _num(ps.get("body")) or ps.get("body") < 16):
        errs.append("theme.page_scale.body must be at least 16 px")

    # ---- stage
    stage = kit.get("stage") or {}
    sa = stage.get("safe_area") or {}
    for ax in ("x_pct", "y_pct"):
        if not _num(sa.get(ax)) or sa.get(ax) < 5:
            errs.append(f"stage.safe_area.{ax} must be at least 5 %")
    for k, v in (stage.get("stroke") or {}).items():
        if not _num(v) or v <= 0:
            errs.append(f"stage.stroke.{k} must be a positive width")
    for k, v in (stage.get("radius") or {}).items():
        if not _num(v) or v < 0:
            errs.append(f"stage.radius.{k} must be >= 0")
    grid = stage.get("grid") or {}
    if grid and not (isinstance(grid.get("columns"), int) and 1 <= grid["columns"] <= 24):
        errs.append("stage.grid.columns must be an integer 1..24")

    # ---- props: connectors must be visible
    conn = (kit.get("props") or {}).get("connector") or {}
    if conn and (not _num(conn.get("width")) or conn["width"] < 2):
        errs.append("props.connector.width must be >= 2 px at 1080p (thinner disappears in compression)")

    # ---- charts: one ink at stepped opacity
    charts = kit.get("charts") or {}
    ser = charts.get("series") or {}
    steps = ser.get("steps") or []
    if not ser.get("role"):
        errs.append("charts.series.role (ONE ink for every series) is required")
    steps_ok = False
    if len(steps) < 2 or not all(_num(s) and 0.25 <= s <= 1 for s in steps):
        errs.append("charts.series.steps needs >= 2 opacities in [0.25, 1]")
    elif any(steps[i] - steps[i + 1] < 0.12 for i in range(len(steps) - 1)):
        errs.append("charts.series.steps must fall by >= 0.12 per step (adjacent series must be distinguishable)")
    else:
        steps_ok = True
    if ser.get("role") and steps_ok and palette_ok:
        for mode in ("light", "dark"):
            c1 = role_color(sk[mode], {"role": ser["role"], "alpha": steps[0]})
            if c1 and contrast(c1, sk[mode]["paper"]) < LARGE_MIN:
                errs.append(f"{mode}: first chart series is below {LARGE_MIN}:1 on paper")

    # ---- motion
    mo = kit.get("motion") or {}
    pts = parse_bezier(mo.get("ease"))
    if pts is None:
        errs.append(f"motion.ease {mo.get('ease')!r} is not a valid cubic-bezier(x1,y1,x2,y2) with x in [0,1]")
    elif is_linear(pts):
        errs.append("motion.ease is linear — motion must ease (PLAYBOOK 2.4b)")
    for k, lo, hi in (("enter_ms", 150, 1200), ("exit_ms", 100, 1000)):
        v = mo.get(k)
        if not _num(v) or not lo <= v <= hi:
            errs.append(f"motion.{k} = {v} must be {lo}..{hi} ms (nothing instant, nothing sluggish)")
    st = mo.get("stagger_ms")
    if not _num(st) or not 0 <= st <= STAGGER_MAX_MS:
        errs.append(f"motion.stagger_ms = {st} must be 0..{STAGGER_MAX_MS} ms")
    for k in ("entrance", "exit"):
        e = mo.get(k)
        if not isinstance(e, dict):
            errs.append(f"motion.{k} must describe the move (opacity / translate / scale / blur)")
            continue
        if _num(e.get("blur_px")) and e["blur_px"] > 24:
            errs.append(f"motion.{k}.blur_px > 24 smears text past recognition")

    # ---- transitions
    tr = kit.get("transitions")
    if not isinstance(tr, list) or not tr:
        errs.append("transitions must be a non-empty list")
    else:
        for i, t in enumerate(tr):
            if not isinstance(t, dict) or not t.get("id"):
                errs.append(f"transitions[{i}] needs an id")
                continue
            ms = t.get("ms")
            if not _num(ms) or not (ms == 0 or 150 <= ms <= 1500):
                errs.append(f"transition '{t['id']}' ms={ms}: a cut is 0, anything else 150..1500 ms")
            if t.get("ease") and parse_bezier(t["ease"]) is None:
                errs.append(f"transition '{t['id']}' ease is not a valid cubic-bezier")
        uses = {t.get("use") for t in tr if isinstance(t, dict)}
        if "section" not in uses:
            errs.append("transitions need one marked use='section' (a distinct signal for a new section)")

    # ---- caption style
    cs = kit.get("caption_style") or {}
    fpx = cs.get("font_px")
    if not _num(fpx) or fpx < MIN_PX:
        errs.append(f"caption_style.font_px = {fpx} is below the {MIN_PX} px floor (truncate, never shrink)")
    ml = cs.get("max_lines")
    if not isinstance(ml, int) or not 1 <= ml <= CAPTION_MAX_LINES:
        errs.append(f"caption_style.max_lines must be 1..{CAPTION_MAX_LINES}")
    if cs.get("overflow", "truncate") != "truncate":
        errs.append("caption_style.overflow must be 'truncate' (never shrink below the floor)")
    marks = cs.get("marks") or {}
    marks_ok = True
    for need in ("good", "bad", "key"):
        if need not in marks:
            errs.append(f"caption_style.marks.{need} is required")
            marks_ok = False
    if marks_ok:
        sigs = {}
        for name, m in marks.items():
            styles = m.get("styles") or []
            if not styles:
                errs.append(f"caption mark '{name}' has no non-colour cue — meaning never by hue alone")
            sigs[name] = tuple(sorted(styles))
        names = list(sigs)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                if sigs[a] == sigs[b]:
                    errs.append(f"caption marks '{a}' and '{b}' differ only by colour")
        for mode in (("light", "dark") if palette_ok else ()):
            roles = sk[mode]
            scrim = cs.get("scrim") or {}
            text_c = role_color(roles, cs.get("text") or {"role": "ink"})
            if not text_c:
                continue
            # Worst case: the caption sits over paper AND over solid ink content.
            behinds = [roles["paper"], roles["ink"]]
            for under in behinds:
                if scrim.get("role") and _num(scrim.get("alpha")) and scrim["alpha"] > 0:
                    bg = mix(roles[scrim["role"]], under, float(scrim["alpha"]))
                else:
                    bg = under
                c = contrast(text_c, bg)
                if c < TEXT_MIN:
                    errs.append(f"{mode}: caption text {text_c} on its scrim over {under} is "
                                f"{c:.2f}:1 (< {TEXT_MIN}:1)")
                for name, m in marks.items():
                    mc = role_color(roles, m)
                    if mc and contrast(mc, bg) < LARGE_MIN:
                        errs.append(f"{mode}: caption mark '{name}' {mc} on its scrim over {under} is "
                                    f"{contrast(mc, bg):.2f}:1 (< {LARGE_MIN}:1)")
    mm = cs.get("max_marks", 2)
    if not isinstance(mm, int) or not 0 <= mm <= 2:
        errs.append("caption_style.max_marks must be 0..2 per caption")
    if not isinstance(kit.get("do"), list) or not kit.get("do"):
        errs.append("do[] must list at least one rule")
    if not isinstance(kit.get("dont"), list) or not kit.get("dont"):
        errs.append("dont[] must list at least one rule")
    # De-duplicate while keeping order (worst-case loops can repeat a line).
    seen, out = set(), []
    for e in errs:
        if e not in seen:
            seen.add(e)
            out.append(e)
    return out


# =====================================================================  blend
def blend(a: dict, b: dict, take=("motion", "transitions"), reason: str | None = None) -> dict:
    """A new kit: the look of `a`, with the sections named in `take` from `b`."""
    take = [t.strip() for t in (take or ()) if t and t.strip()]
    bad = [t for t in take if t not in b]
    if bad:
        raise ValueError(f"blend: second kit has no section(s) {bad}")
    k = copy.deepcopy(a)
    for t in take:
        k[t] = copy.deepcopy(b[t])
    k["id"] = f"blend:{a.get('id')}+{b.get('id')}"
    k["name"] = f"{a.get('name', a.get('id'))} × {b.get('name', b.get('id'))}"
    k["parents"] = [a.get("id"), b.get("id")]
    k["took"] = {t: b.get("id") for t in take}
    k["reason"] = reason or (f"{a.get('id')} supplies the look (palette, type, stage, props, charts, captions); "
                             f"{b.get('id')} supplies {', '.join(take)}")
    k.pop("_file", None)
    return k


# =====================================================================  scoring
_STOP = set("""a an and are as at be by for from in into is it its of on or that the this to with
without about over under your our their his her very more most less least than then also just
some any all each per via vs who what when how why which can will should would could
""".split())


def _stem(w: str) -> str:
    for suf in ("ings", "ing", "ies", "es", "s", "ed"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)] + ("y" if suf == "ies" else "")
    return w


def tokens(text) -> set:
    if isinstance(text, (list, tuple)):
        text = " ".join(str(t) for t in text)
    return {_stem(w) for w in re.findall(r"[a-z0-9]+", str(text or "").lower()) if w not in _STOP}


def _norm_id(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s or "").lower()).strip("-")


def _stated_kit(stated: str, kits: list):
    """A kit named in the user's own words (id or name)."""
    s = _norm_id(stated)
    if not s:
        return None
    for k in kits:
        for cand in (k.get("id"), k.get("name")):
            n = _norm_id(cand)
            if n and re.search(rf"(^|-){re.escape(n)}(-|$)", s):
                return k
    return None


def fit(brief: dict, kit: dict, palette: dict | None = None):
    """Fit of one kit to the brief, in (-1, 1), with the reasons that produced it."""
    sig = kit.get("signals") or {}
    pos = tokens(sig.get("for") or [])
    neg = tokens(sig.get("against") or [])
    mood = tokens(kit.get("mood") or [])
    use = tokens(kit.get("use_when") or [])
    subj = tokens(brief.get("subject")) | tokens(brief.get("audience"))
    tone = tokens(brief.get("tone"))
    stated = tokens(brief.get("stated"))
    everything = subj | tone
    words = {}
    for field in ("subject", "audience", "tone", "stated"):
        for w in re.findall(r"[a-z0-9]+", str(brief.get(field) or "").lower()):
            words.setdefault(_stem(w), w)

    def show(ts):
        return ", ".join(words.get(t, t) for t in ts)
    raw, why = 0.0, []
    hit = sorted(subj & pos)
    if hit:
        raw += 1.0 * len(hit)
        why.append("subject/audience signals: " + show(hit))
    th = sorted(tone & (mood | pos))
    if th:
        raw += 1.2 * len(th)
        why.append("tone fits mood: " + show(th))
    uh = sorted((everything - set(hit) - set(th)) & use)
    if uh:
        raw += 0.6 * len(uh)
        why.append("use_when overlap: " + show(uh))
    sh = sorted(stated & (mood | pos | tokens(kit.get("name")) | {kit.get("base", "")}))
    if sh:
        raw += 2.0 * len(sh)
        why.append("matches what the user stated: " + show(sh))
    against = sorted((everything | stated) & neg)
    if against:
        raw -= 1.5 * len(against)
        phrase = next((p for p in kit.get("avoid_when") or [] if tokens(p) & set(against)), None)
        why.append("avoid_when hit: " + show(against) + (f" (\"{phrase}\")" if phrase else ""))
    if palette:
        fam = palette.get("family") or palette.get("id")
        if fam and fam == kit.get("palette_family"):
            raw += 0.8
            why.append(f"palette family '{fam}' is this kit's own")
        roles = palette.get("roles") or {}
        if is_hex(roles.get("paper", "")):
            dark_paper = luminance(roles["paper"]) < 0.2
            if dark_paper == (kit.get("base") == "dark"):
                raw += 0.4
                why.append("palette base (light/dark) matches the kit's base")
            else:
                raw -= 0.4
                why.append("palette base (light/dark) is the kit's secondary mode")
    f = raw / (abs(raw) + 3.0)
    return round(f, 4), why


def _history_penalty(kid: str, history):
    """(magnitude, reason). Shared module first; inline −0.35 rule otherwise."""
    try:
        from design.history import penalty as _pen  # type: ignore
        p, why = _pen("kit", kid, history, window=3)
        return abs(float(p)), str(why)
    except Exception:
        pass
    recent = [h.get("kit") for h in (history or [])[:3] if isinstance(h, dict)]
    if kid in recent:
        return REPEAT_PENALTY, f"kit '{kid}' was used in one of the last 3 builds (-{REPEAT_PENALTY})"
    return 0.0, ""


def recent_history(n: int = 3) -> list:
    try:
        from design.history import recent_history as _rh  # type: ignore
        return list(_rh(n))
    except Exception:
        pass
    try:
        from memory.store import Store  # type: ignore
        return Store().recent_choices(n)
    except Exception:
        return []


def _seed(brief: dict) -> int:
    if isinstance(brief.get("seed"), int):
        return brief["seed"]
    key = json.dumps([brief.get("subject"), brief.get("audience"), brief.get("tone")], sort_keys=True)
    return zlib.crc32(key.encode("utf-8")) & 0x7FFFFFFF


def rank(brief: dict, kits: list, palette: dict | None = None, history=None) -> list:
    rng = random.Random(_seed(brief))
    rows = []
    for k in kits:
        f, why = fit(brief, k, palette)
        pen, pwhy = _history_penalty(k.get("id"), history)
        composite = with_skin(k, palette) if palette and palette.get("roles") else k
        fails = validate_kit(composite) if composite is not k else []
        rows.append({"id": k.get("id"), "name": k.get("name"), "fit": f, "penalty": round(pen, 4),
                     "score": round(f - pen - (10.0 if fails else 0.0), 4), "reasons": why,
                     "penalty_reason": pwhy, "outcome_failures": fails[:4],
                     "_tie": rng.random()})
    rows.sort(key=lambda r: (-r["score"], r["_tie"]))
    for r in rows:
        r.pop("_tie", None)
    return rows


def _reject_reason(row: dict, chosen: dict) -> str:
    if row["outcome_failures"]:
        return "fails the outcome rules with this build's palette: " + row["outcome_failures"][0]
    bits = []
    av = [w for w in row["reasons"] if w.startswith("avoid_when")]
    if av:
        bits.append(av[0])
    if row["penalty"]:
        bits.append(row["penalty_reason"])
    bits.append(f"lower score {row['score']:.2f} vs {chosen['score']:.2f} for '{chosen['id']}'"
                + (f" (it matched: {'; '.join(r for r in row['reasons'] if not r.startswith('avoid'))})"
                   if any(not r.startswith("avoid") for r in row["reasons"]) else " (no brief signal matched it)"))
    return "; ".join(bits)


# =====================================================================  pick
def pick(brief: dict, palette: dict | None = None, kits: list | None = None, history=None,
         invented: dict | None = None, blend_ids=None, take=("motion", "transitions"),
         reason: str | None = None) -> dict:
    """Choose the active kit. Returns the full record (raises ValueError on a bad input)."""
    kits = load_kits() if kits is None else kits
    if history is None:
        history = recent_history(3)
    stated = (brief.get("stated") or "").strip()
    ranked = rank(brief, kits, palette, history) if kits else []
    record = {"ranked": ranked, "seed": _seed(brief), "obeyed_user_quote": None,
              "history_penalty_applied": False}

    if invented is not None:
        spec = resolve(invented, kits)
        why = reason or spec.get("reason")
        if not why:
            raise ValueError("an invented / hand-defined kit must carry a reason (kit.reason or --reason)")
        name = spec.get("name") or spec.get("id") or "unnamed"
        spec["id"] = "invented:" + _norm_id(name)
        spec["reason"] = why
        active = with_skin(spec, palette) if palette and palette.get("roles") else spec
        errs = validate_kit(active)
        if errs:
            raise ValueError("invented kit fails the outcome rules: " + " | ".join(errs))
        record.update(mode="invented", kit=spec["id"], reason=why, spec=spec)
    elif blend_ids:
        a_id, b_id = blend_ids
        a, b = kit_by_id(a_id, kits), kit_by_id(b_id, kits)
        if a is None or b is None:
            raise ValueError(f"blend: unknown kit id(s): {[i for i, k in ((a_id, a), (b_id, b)) if k is None]}")
        spec = blend(a, b, take, reason)
        active = with_skin(spec, palette) if palette and palette.get("roles") else spec
        errs = validate_kit(active)
        if errs:
            raise ValueError("blend fails the outcome rules: " + " | ".join(errs))
        record.update(mode="blend", kit=spec["id"], reason=spec["reason"], spec=spec,
                      parents=spec["parents"], took=spec["took"])
    else:
        if not ranked:
            raise ValueError("no kits available and none supplied (use --kit FILE)")
        named = _stated_kit(stated, kits) if stated else None
        if named is not None:
            chosen = next(r for r in ranked if r["id"] == named["id"])
            record.update(mode="stated", obeyed_user_quote=stated,
                          reason=f"the user asked for it: \"{stated}\"")
        else:
            chosen = ranked[0]
            if chosen["score"] <= -5:
                raise ValueError("every kit fails the outcome rules with this palette; "
                                 "supply or invent a kit that passes")
            lead = chosen["fit"] - (ranked[1]["fit"] if len(ranked) > 1 else -1)
            record.update(mode="catalog", reason="; ".join(chosen["reasons"]) or "best overall fit")
            if stated:
                record["obeyed_user_quote"] = stated
            # Anti-repetition (H3): if the top-fit kit lost its place ONLY to the penalty, say so.
            top_fit = max(ranked, key=lambda r: r["fit"])
            if top_fit["id"] != chosen["id"] and top_fit["penalty"] > 0:
                record["history_penalty_applied"] = True
                record["reason"] += (f"; '{top_fit['id']}' fit slightly better but was used recently "
                                     f"({top_fit['penalty_reason']})")
            elif chosen["penalty"] > 0:
                record["history_penalty_applied"] = True
                record["reason"] += (f"; repeated despite recent use because the subject clearly "
                                     f"calls for it (lead {lead:.2f} >= {CLEAR_MARGIN})"
                                     if lead >= CLEAR_MARGIN else "; repeated (no better candidate)")
        spec = copy.deepcopy(kit_by_id(chosen["id"], kits))
        spec.pop("_file", None)
        record.update(kit=chosen["id"], spec=spec)

    chosen_row = next((r for r in ranked if r["id"] == record["kit"]),
                      {"id": record["kit"], "score": 1.0, "reasons": []})
    others = [r for r in ranked if r["id"] != record["kit"]]
    # The strongest runners-up (why they lost) plus every kit the brief argues against.
    picked = others[:3] + [r for r in others[3:] if any(w.startswith("avoid_when") for w in r["reasons"])
                           or r["outcome_failures"]][:3]
    rejected = [{"id": r["id"], "why": _reject_reason(r, chosen_row)} for r in picked]
    record["rejected"] = rejected
    record["active"] = with_skin(record["spec"], palette) if palette and palette.get("roles") else record["spec"]
    return record


# =====================================================================  design_system.json
def _local_update(build_dir, key, value):
    """Fallback with the same behaviour as design.design_system.update: read-modify-write
    one key, every other key preserved, atomic replace."""
    d = Path(build_dir)
    d.mkdir(parents=True, exist_ok=True)
    p = d / "design_system.json"
    data = {}
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as ex:
            raise ValueError(f"{p} is not valid JSON; refusing to overwrite it ({ex})")
    data.setdefault("schema", 1)
    data[key] = value
    fd, tmp = tempfile.mkstemp(dir=str(d), prefix=".design_system.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, p)
    return data


def _updater():
    try:
        from design.design_system import update as _u  # type: ignore
        return _u
    except Exception:
        return _local_update


def design_fields(kit: dict) -> dict:
    """The SPEC §3.2 fields this stream owns, derived from one resolved kit."""
    theme = kit.get("theme") or {}
    fam = theme.get("family") or {}
    mo = kit.get("motion") or {}
    return {
        "type": {"family": fam.get("body"), "display": fam.get("display", fam.get("body")),
                 "mono": fam.get("mono"), "scale": theme.get("scale"),
                 "page_scale": theme.get("page_scale"), "weights": theme.get("weights"),
                 "vendorable": theme.get("vendorable")},
        "motion": {k: mo.get(k) for k in ("ease", "enter_ms", "exit_ms", "stagger_ms", "lead_ms",
                                          "entrance", "exit", "hold_min_ms") if k in mo},
        "transitions": [t.get("id") for t in kit.get("transitions") or []],
        "caption_style": kit.get("caption_style"),
    }


def write_design_system(build_dir, record: dict) -> Path:
    up = _updater()
    spec = record["spec"]
    up(build_dir, "kit", record["kit"])
    for k, v in design_fields(spec).items():
        up(build_dir, k, v)
    up(build_dir, "kit_spec", spec)
    choice = {k: record.get(k) for k in ("mode", "reason", "obeyed_user_quote", "seed",
                                         "history_penalty_applied", "parents", "took") if k in record}
    choice["candidates"] = [{k: r[k] for k in ("id", "fit", "penalty", "score", "reasons")}
                            for r in record["ranked"]]
    choice["rejected"] = record["rejected"]
    up(build_dir, "kit_choice", choice)
    return Path(build_dir) / "design_system.json"


# =====================================================================  CLI
def _load_json(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Pick, blend or accept a style kit (outcome-gated).")
    ap.add_argument("brief", nargs="?", help="brief.json {subject, audience, tone, stated?}")
    ap.add_argument("--build", help="directory holding design_system.json (written)")
    ap.add_argument("--kit", help="a hand-defined / invented kit JSON to use")
    ap.add_argument("--blend", help="two kit ids: a,b (look of a, --take sections from b)")
    ap.add_argument("--take", default="motion,transitions", help="sections the second blend parent gives")
    ap.add_argument("--palette", help="Palette Director result (design_system.json or palette JSON)")
    ap.add_argument("--reason", help="why (required for an invented kit; optional for a blend)")
    ap.add_argument("--kits-dir", help="alternative kit directory (default data/style_kits)")
    ap.add_argument("--validate", metavar="FILE", help="only run the outcome rules on one kit")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        kits = load_kits(Path(a.kits_dir) if a.kits_dir else None)
        if a.validate:
            k = resolve(_load_json(a.validate), kits)
            errs = validate_kit(k)
            out = {"kit": k.get("id") or k.get("name"), "valid": not errs, "failures": errs}
            print(json.dumps(out, indent=1) if a.json else
                  ("VALID " + str(out["kit"]) if not errs else "INVALID\n  - " + "\n  - ".join(errs)))
            return 0 if not errs else 3
        if not a.brief:
            ap.error("a brief.json is required")
        brief = _load_json(a.brief)
        palette = None
        if a.palette:
            pj = _load_json(a.palette)
            palette = pj.get("palette") if isinstance(pj.get("palette"), dict) else pj
        elif a.build and (Path(a.build) / "design_system.json").exists():
            ds = _load_json(Path(a.build) / "design_system.json")
            if isinstance(ds.get("palette"), dict) and ds["palette"].get("roles"):
                palette = ds["palette"]
        invented = _load_json(a.kit) if a.kit else None
        blend_ids = None
        if a.blend:
            parts = [p.strip() for p in a.blend.split(",") if p.strip()]
            if len(parts) != 2:
                ap.error("--blend needs exactly two kit ids: a,b")
            blend_ids = parts
        rec = pick(brief, palette=palette, kits=kits, invented=invented, blend_ids=blend_ids,
                   take=a.take.split(","), reason=a.reason)
        if a.build:
            rec["written"] = str(write_design_system(a.build, rec))
    except (ValueError, OSError, json.JSONDecodeError) as ex:
        msg = {"status": "ERROR", "error": str(ex)}
        print(json.dumps(msg) if a.json else f"ERROR: {ex}")
        return 3
    if a.json:
        slim = {k: v for k, v in rec.items() if k not in ("spec", "active")}
        print(json.dumps(slim, indent=1, ensure_ascii=False))
    else:
        print(f"kit: {rec['kit']}  ({rec['mode']})\nwhy: {rec['reason']}")
        for r in rec["rejected"]:
            print(f"  rejected {r['id']}: {r['why']}")
        if rec.get("written"):
            print(f"wrote {rec['written']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
