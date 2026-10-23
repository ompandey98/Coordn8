#!/usr/bin/env python3
"""
Coordn8 — CAPTION MARKS: colour-coded emphasis in the DISPLAY text only (E1),
plus the caption STYLE helpers the overlay and the gates share (E8).

Canonical syntax (one way to write it)
--------------------------------------
    The cache turns a ⟦bad:slow read⟧ into a ⟦good:fast one⟧ — the ⟦key:hit rate⟧ is what matters.

  ⟦name:text⟧   `name` is a mark name, `text` is the marked word or phrase (no nesting).

  The marks every kit understands, and the colour ROLE each carries:
      good -> positive      bad -> negative      key -> accent
  A kit may re-style them or declare more marks in `caption_style.marks`
  ({"role": "<palette role>", "styles": [...]}) and a mark name that IS a palette
  role (⟦warning:…⟧) is understood directly. Nothing else is required: these are
  starting points, never a fence.

  The closing-tag form `⟦key⟧text⟦/key⟧` is ACCEPTED as an alias (it is normalised
  and reported as a note) so an author who types it never leaks markup.

Where the marks go — and where they must never go
-------------------------------------------------
  * The CAPTION overlay (captions.js) and the page colour the marked span with the
    mark's role from the ACTIVE design system, plus a non-colour cue (underline,
    strike-through, box, weight…) so meaning never rides on hue alone.
  * The SPOKEN form: never. `strip(text)` removes every trace of the syntax; the
    pronunciation helper calls it before deriving `speech`, so the voice engine
    never reads a bracket.
  * The .srt: never (plain text). The .vtt may keep the colour as WebVTT cue
    classes (`<c.key>hit rate</c>`), which players style with ::cue(.key).
  * Subtitle fidelity compares UNMARKED text on both sides.

At most TWO marks per caption (a caption is one on-screen cue after readability
segmentation). More than that and nothing is emphasised; the gate
(`scripts/gates/check_caption_marks.py`) flags it.

Zero third-party dependencies; Python 3.9+.

CLI:
    python3 scripts/render/caption_marks.py strip "text with ⟦key:marks⟧"
    python3 scripts/render/caption_marks.py spans "text" [--kit kit.json|design_system.json] [--mode light|dark]
    python3 scripts/render/caption_marks.py styles [--kit ...]          # validate data/caption_styles.json
    python3 scripts/render/caption_marks.py style-js <style-id> [--kit ...] [--font-px N]
        -> `window.__C8_CAPTION_STYLE__ = {...};` (colours resolved from the active kit)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent
_PKG = _SCRIPTS.parent
for _p in (str(_HERE), str(_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

OPEN, CLOSE = "⟦", "⟧"            # ⟦ ⟧ (mathematical white square brackets)
MAX_MARKS_PER_CAPTION = 2
MIN_CAPTION_PX = 28                          # the 1080p legibility floor (PLAYBOOK 2.4a)
MAX_CHARS_PER_LINE = 42                      # same as lib.common.CAPTION_MAX_CHARS_PER_LINE
MAX_LINES = 2                                # same as lib.common.CAPTION_MAX_LINES
STYLES_PATH = _PKG / "data" / "caption_styles.json"

DEFAULT_MARKS = {
    "good": {"role": "positive", "styles": ["underline"]},
    "bad": {"role": "negative", "styles": ["strike-through"]},
    "key": {"role": "accent", "styles": ["box"]},
}
# The palette role vocabulary used when no kit is given (same names as
# scripts/design/design_system.py STANDARD_ROLES).
STANDARD_ROLES = ("paper", "ink", "muted", "accent", "link", "positive", "negative", "warning",
                  "deterministic", "model-judgement", "data-field", "vision")

_NAME = r"[A-Za-z][A-Za-z0-9_-]*"
# canonical ⟦name:text⟧  |  alias ⟦name⟧text⟦/name⟧
_MARK = re.compile(OPEN + r"(?:(" + _NAME + r"):([^" + OPEN + CLOSE + r"]*)" + CLOSE
                   + r"|(" + _NAME + r")" + CLOSE + r"([^" + OPEN + CLOSE + r"]*)" + OPEN + r"/\3" + CLOSE + r")")
# Fragments left by malformed or word-split markup ("⟦key:cache" / "miss⟧" / "⟦/key⟧").
_FRAGMENT = re.compile(OPEN + r"/?" + _NAME + r"(?::|" + CLOSE + r")|[" + OPEN + CLOSE + r"]")


# ============================================================================ parsing
def has_marks(text) -> bool:
    s = str(text)
    return OPEN in s or CLOSE in s


def parse(text) -> dict:
    """Split display text into plain text + mark spans.

    Returns {"plain", "spans": [{"index", "name", "text", "start", "end", "alias"}],
             "errors": [str], "notes": [str]} — `start`/`end` are character offsets
    into `plain`. Malformed syntax never survives into `plain` (it is removed and
    reported), so `plain` is always safe to speak or to put in a .srt."""
    s = str(text)
    if not has_marks(s):
        return {"plain": s, "spans": [], "errors": [], "notes": []}
    out, spans, errors, notes = [], [], [], []
    pos = 0
    n = 0

    def leftover(seg: str) -> str:
        if OPEN in seg or CLOSE in seg:
            frag = ", ".join(repr(m.group(0)) for m in _FRAGMENT.finditer(seg))
            errors.append(f"malformed mark syntax ({frag}) in {s[:80]!r} — write {OPEN}name:text{CLOSE}, "
                          f"no nesting, every {OPEN} closed by {CLOSE}")
            return _FRAGMENT.sub("", seg)
        return seg

    for m in _MARK.finditer(s):
        out.append(leftover(s[pos:m.start()]))
        alias = m.group(1) is None
        name = m.group(3) if alias else m.group(1)
        inner = m.group(4) if alias else m.group(2)
        if alias:
            notes.append(f"{OPEN}{name}{CLOSE}…{OPEN}/{name}{CLOSE} is the alias form; the canonical form is "
                         f"{OPEN}{name}:…{CLOSE}")
        if not inner.strip():
            errors.append(f"empty mark {OPEN}{name}:{CLOSE} in {s[:80]!r} — a mark must wrap a word")
        start = sum(len(x) for x in out)
        out.append(inner)
        spans.append({"index": n, "name": name, "text": inner, "start": start,
                      "end": start + len(inner), "alias": alias})
        n += 1
        pos = m.end()
    out.append(leftover(s[pos:]))
    return {"plain": "".join(out), "spans": spans, "errors": errors, "notes": notes}


def strip(text) -> str:
    """The text with every trace of mark syntax removed (identity when there is none).

    Used for the SPOKEN form, the .srt, and every fidelity comparison. Robust to a
    word-split fragment ("⟦key:cache" / "miss⟧"), so even a caller that strips one
    word at a time never passes a bracket to the voice engine."""
    s = str(text)
    if not has_marks(s):
        return s
    return parse(s)["plain"]


def tokens(text) -> list:
    """[(word, mark_index | None, mark_name | None)] over the whitespace-split plain text.

    A word is marked when any of its characters lies inside a mark. The word list is
    exactly `strip(text).split()`, so it lines up with every segmenter in Coordn8."""
    p = parse(text)
    plain = p["plain"]
    owner = [None] * len(plain)
    for sp in p["spans"]:
        for i in range(sp["start"], sp["end"]):
            owner[i] = sp["index"]
    names = {sp["index"]: sp["name"] for sp in p["spans"]}
    out = []
    for m in re.finditer(r"\S+", plain):
        idx = next((owner[i] for i in range(m.start(), m.end()) if owner[i] is not None), None)
        out.append((m.group(0), idx, names.get(idx)))
    return out


# ============================================================================ kit / roles
def load_kit(path) -> tuple:
    """(kit, skin) from a kit JSON or a design_system.json (kit_spec + palette)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data.get("kit_spec"), dict):
        pal = data.get("palette") if isinstance(data.get("palette"), dict) else None
        return data["kit_spec"], (pal if pal and pal.get("roles") else None)
    return data, None


def _skins(kit: dict | None, skin: dict | None = None) -> dict:
    if not kit and not skin:
        return {"light": {}, "dark": {}}
    pal = dict((kit or {}).get("palette") or {})
    light = dict(pal.get("roles") or {})
    dark = dict(pal.get("dark") or {})
    if skin and skin.get("roles"):
        light.update(skin.get("roles") or {})
        dark.update(skin.get("dark") or skin.get("roles") or {})
    return {"light": light, "dark": dark}


def design_roles(kit: dict | None = None, skin: dict | None = None) -> set:
    """Every colour role the active design system declares (either skin)."""
    sk = _skins(kit, skin)
    roles = set(sk["light"]) | set(sk["dark"])
    return roles if roles else set(STANDARD_ROLES)


def mark_table(kit: dict | None = None) -> dict:
    """{mark name: {"role", "styles"}} — the kit's marks over the three defaults."""
    table = {k: dict(v) for k, v in DEFAULT_MARKS.items()}
    cs = (kit or {}).get("caption_style") or {}
    for name, m in (cs.get("marks") or {}).items():
        if isinstance(m, dict) and m.get("role"):
            table[name] = {"role": m["role"], "styles": list(m.get("styles") or ["underline"])}
    return table


def max_marks(kit: dict | None = None) -> int:
    cs = (kit or {}).get("caption_style") or {}
    v = cs.get("max_marks", MAX_MARKS_PER_CAPTION)
    if isinstance(v, int) and not isinstance(v, bool) and 0 <= v:
        return min(MAX_MARKS_PER_CAPTION, v)
    return MAX_MARKS_PER_CAPTION


def resolve_mark(name: str, kit: dict | None = None, skin: dict | None = None):
    """The {"role", "styles", "source"} a mark name renders with, or None if it cannot render."""
    table = mark_table(kit)
    if name in table:
        return dict(table[name], source="kit" if name in ((kit or {}).get("caption_style") or {}).get(
            "marks", {}) else "default")
    if name in design_roles(kit, skin):
        return {"role": name, "styles": ["underline"], "source": "role"}
    return None


def spans(text, kit: dict | None = None, skin: dict | None = None, mode: str | None = None) -> dict:
    """Marked display text -> plain text + spans carrying their ROLE and resolved COLOUR
    from the active kit (skin = the Palette Director's result, which replaces the kit's own)."""
    p = parse(text)
    sk = _skins(kit, skin)
    mode = mode or (kit or {}).get("base", "light")
    roles = sk.get(mode) or sk.get("light") or {}
    out = []
    problems = list(p["errors"])
    for sp in p["spans"]:
        r = resolve_mark(sp["name"], kit, skin)
        row = dict(sp)
        if r is None:
            row.update(role=None, color=None, styles=[])
            problems.append(f"unknown mark {sp['name']!r}: not a mark of the active kit and not a palette role")
        else:
            row.update(role=r["role"], styles=r["styles"], source=r["source"], color=roles.get(r["role"]))
            if roles and r["role"] not in roles:
                problems.append(f"mark {sp['name']!r} maps to role {r['role']!r}, which the active design "
                                f"system does not declare")
        out.append(row)
    return {"plain": p["plain"], "spans": out, "problems": problems, "notes": p["notes"], "mode": mode}


# ============================================================================ per-caption counting
def cue_mark_counts(text, start: float = 0.0, end: float = 3.0) -> list:
    """Segment ONE narration sentence exactly as subtitles.py does, and return per cue
    {"text", "marks": [names], "count"} — a caption is one on-screen cue."""
    import subtitles as _subs  # same directory; imported lazily (subtitles imports this module)
    toks = tokens(text)
    plain = " ".join(w for w, _i, _n in toks)
    if not plain:
        return []
    cues = _subs._segment_sentence(0, plain, float(start), float(end))
    out, k = [], 0
    for c in cues:
        words = c["text"].split()
        idxs, names = [], []
        for _w in words:
            if k < len(toks):
                _ww, idx, name = toks[k]
                if idx is not None and idx not in idxs:
                    idxs.append(idx)
                    names.append(name)
            k += 1
        out.append({"text": c["text"], "marks": names, "count": len(idxs)})
    return out


# ============================================================================ caption styles (E8)
def load_styles(path: Path | None = None) -> dict:
    return json.loads(Path(path or STYLES_PATH).read_text(encoding="utf-8"))


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf")


def style_box(style: dict, width: int = 1920, height: int = 1080, font_px: float | None = None) -> dict:
    """The caption box at its maximum size (max_lines) for a 1920x1080-equivalent frame."""
    pos = style.get("position") or {}
    fpx = max(MIN_CAPTION_PX, float(font_px if font_px is not None else style.get("font_px", MIN_CAPTION_PX)))
    lh = float(style.get("line_height", 1.25))
    pad = float(((style.get("scrim") or {}).get("pad_px")) or style.get("pad_px", 16))
    lines = int(style.get("max_lines", MAX_LINES))
    w = width * float(pos.get("width_pct", 72)) / 100.0
    h = lines * fpx * lh + 2 * pad
    inset = float(pos.get("inset_pct", 7)) / 100.0
    align = pos.get("align", "center")
    anchor = pos.get("anchor", "bottom")
    x = (width - w) / 2 if align == "center" else (width * inset if align == "left" else width - width * inset - w)
    y = (height - height * inset - h) if anchor == "bottom" else (height * inset if anchor == "top"
                                                                    else (height - h) / 2)
    return {"x": x, "y": y, "w": w, "h": h, "font_px": fpx, "pad": pad, "line_height": lh}


def validate_style(style: dict, width: int = 1920, height: int = 1080, safe_pct=(5.0, 5.0)) -> list:
    """OUTCOME rules for a caption style (catalog or invented): legible size, readable
    line budget, inside the safe area. Never checks membership of any catalog."""
    errs = []
    sid = style.get("id") or style.get("name") or "?"
    f = style.get("font_px")
    if not _num(f) or f < MIN_CAPTION_PX:
        errs.append(f"{sid}: font_px={f} is below the {MIN_CAPTION_PX} px floor at 1080p — overflow is "
                    f"truncated or segmented, never shrunk")
    ml = style.get("max_lines")
    if not isinstance(ml, int) or not 1 <= ml <= MAX_LINES:
        errs.append(f"{sid}: max_lines must be 1..{MAX_LINES}")
    mc = style.get("max_chars_per_line")
    if not isinstance(mc, int) or not 12 <= mc <= MAX_CHARS_PER_LINE:
        errs.append(f"{sid}: max_chars_per_line must be 12..{MAX_CHARS_PER_LINE}")
    lh = style.get("line_height", 1.25)
    if not _num(lh) or not 1.05 <= lh <= 2.0:
        errs.append(f"{sid}: line_height {lh} must be 1.05..2.0")
    if style.get("overflow", "truncate") != "truncate":
        errs.append(f"{sid}: overflow must be 'truncate' (never shrink)")
    if not errs:
        b = style_box(style, width, height)
        sx, sy = width * safe_pct[0] / 100.0, height * safe_pct[1] / 100.0
        if b["x"] < sx - 0.5 or b["y"] < sy - 0.5 or b["x"] + b["w"] > width - sx + 0.5 \
                or b["y"] + b["h"] > height - sy + 0.5:
            errs.append(f"{sid}: caption box {round(b['x'])},{round(b['y'])} {round(b['w'])}x{round(b['h'])} "
                        f"leaves the {safe_pct[0]:g}%/{safe_pct[1]:g}% safe area")
        inner = b["w"] - 2 * b["pad"]
        # A line must hold a readable phrase at the floor size (deterministic 0.54 em estimate);
        # narrower styles simply wrap earlier — the overlay measures the real width per line.
        if inner < 12 * 0.54 * b["font_px"]:
            errs.append(f"{sid}: inner width {round(inner)} px cannot hold even 12 characters at "
                        f"{b['font_px']:g} px")
    hl = style.get("highlight") or {}
    if hl and hl.get("mode") not in (None, "none", "word", "phrase"):
        errs.append(f"{sid}: highlight.mode must be none, word or phrase")
    return errs


def _hex(c: str) -> tuple:
    c = c.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _lum(c: str) -> float:
    def lin(v):
        v = v / 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = _hex(c)
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(a: str, b: str) -> float:
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def mix(fg: str, bg: str, alpha: float) -> str:
    f, b = _hex(fg), _hex(bg)
    a = max(0.0, min(1.0, float(alpha)))
    return "#" + "".join(f"{round(f[i] * a + b[i] * (1 - a)):02x}" for i in range(3))


def style_contrast(style: dict, roles: dict) -> list:
    """WCAG contrast of the caption text (and its marks) against what is behind it,
    worst case over paper and over solid ink content. Returns findings (empty = fine)."""
    out = []
    if not roles or "paper" not in roles or "ink" not in roles:
        return ["no palette roles given — contrast not measured"]
    sid = style.get("id", "?")
    text_c = roles.get((style.get("text") or {}).get("role", "ink"))
    if not text_c:
        return [f"{sid}: text role is not in the palette"]
    scrim = style.get("scrim") or {}
    outline = style.get("outline") or {}
    for under_name in ("paper", "ink"):
        under = roles[under_name]
        if scrim.get("role") and _num(scrim.get("alpha")) and scrim["alpha"] > 0 and roles.get(scrim["role"]):
            bg = mix(roles[scrim["role"]], under, scrim["alpha"])
        elif outline.get("role") and roles.get(outline["role"]) and _num(outline.get("px")) and outline["px"] >= 2:
            bg = roles[outline["role"]]          # an opaque outline is what the glyph edge sits on
        else:
            bg = under
        c = contrast(text_c, bg)
        if c < 4.5:
            out.append(f"{sid}: caption text on {under_name} is {c:.2f}:1 (< 4.5:1)")
    return out


def resolve_style(style: dict, kit: dict | None = None, skin: dict | None = None, mode: str | None = None,
                  font_px: float | None = None) -> dict:
    """A style with every role resolved to a colour from the active kit — what captions.js reads
    as window.__C8_CAPTION_STYLE__. A font override below the floor is CLAMPED here too (and the
    clamp recorded in `findings`), never shrunk."""
    sk = _skins(kit, skin)
    mode = mode or (kit or {}).get("base", "light")
    roles = sk.get(mode) or sk.get("light") or {}
    s = json.loads(json.dumps(style))
    findings = []
    want = font_px if font_px is not None else s.get("font_px", MIN_CAPTION_PX)
    if not _num(want) or want < MIN_CAPTION_PX:
        findings.append({"code": "font_clamped", "from": want, "to": MIN_CAPTION_PX,
                         "message": f"caption font {want}px is below the {MIN_CAPTION_PX}px floor; clamped "
                                    f"(overflow is truncated, never shrunk)"})
        want = MIN_CAPTION_PX
    s["font_px"] = want

    def col(ref, default=None):
        if not ref:
            return default
        name = ref.get("role") if isinstance(ref, dict) else ref
        c = roles.get(name)
        if c and isinstance(ref, dict) and _num(ref.get("alpha")) and ref["alpha"] < 1:
            r, g, b = _hex(c)
            return f"rgba({r},{g},{b},{round(float(ref['alpha']), 3)})"
        return c or default

    s["colors"] = {
        "text": col(s.get("text"), "#f2f4f8"),
        "scrim": col(s.get("scrim")) if (s.get("scrim") or {}).get("role") else None,
        "outline": col({"role": (s.get("outline") or {}).get("role")}) if (s.get("outline") or {}).get("role")
        else None,
        "highlight": col((s.get("highlight") or {}).get("role") and {"role": s["highlight"]["role"]}),
    }
    s["marks"] = {n: {"role": m["role"], "styles": m["styles"], "color": roles.get(m["role"])}
                  for n, m in mark_table(kit).items()}
    s["max_marks"] = max_marks(kit)
    s["findings"] = findings
    s["mode"] = mode
    return s


# ============================================================================ CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Coordn8 caption marks + caption styles.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("strip")
    a.add_argument("text", nargs="?")
    b = sub.add_parser("spans")
    b.add_argument("text")
    b.add_argument("--kit")
    b.add_argument("--mode", choices=["light", "dark"])
    c = sub.add_parser("styles")
    c.add_argument("--kit")
    d = sub.add_parser("style-js")
    d.add_argument("style_id")
    d.add_argument("--kit")
    d.add_argument("--mode", choices=["light", "dark"])
    d.add_argument("--font-px", type=float)
    args = ap.parse_args(argv)
    kit = skin = None
    if getattr(args, "kit", None):
        kit, skin = load_kit(args.kit)
    if args.cmd == "strip":
        text = args.text if args.text is not None else sys.stdin.read()
        sys.stdout.write(strip(text) + ("" if text.endswith("\n") else "\n"))
        return 0
    if args.cmd == "spans":
        print(json.dumps(spans(args.text, kit, skin, args.mode), ensure_ascii=False, indent=1))
        return 0
    data = load_styles()
    if args.cmd == "styles":
        errs = []
        for st in data.get("styles", []):
            errs += validate_style(st)
        print(json.dumps({"styles": [s.get("id") for s in data.get("styles", [])], "errors": errs},
                         ensure_ascii=False, indent=1))
        return 1 if errs else 0
    st = next((s for s in data.get("styles", []) if s.get("id") == args.style_id), None)
    if st is None:
        print(f"ERROR: no caption style {args.style_id!r} in {STYLES_PATH.name} (an invented style may be "
              f"passed as JSON to resolve_style())", file=sys.stderr)
        return 3
    out = resolve_style(st, kit, skin, args.mode, args.font_px)
    sys.stdout.write("window.__C8_CAPTION_STYLE__ = " + json.dumps(out, ensure_ascii=False) + ";\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
