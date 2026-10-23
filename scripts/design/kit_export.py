#!/usr/bin/env python3
"""Coordn8 — export the ACTIVE style kit to the two places that render it (A3).

One kit, two readers, the same values:

  to_css_vars(kit, skin)  ->  a CSS block of `--c8-*` custom properties for the page
                              template (light in :root; dark via prefers-color-scheme AND
                              [data-theme=dark]; durations zeroed under prefers-reduced-motion)
  to_runtime(kit, skin)   ->  a JSON object the scene runtime reads as window.__C8_KIT__
                              (colours resolved to hex/rgba, motion in seconds, the easing
                              curve as control points AND a sampled lookup table so a
                              frame stays a pure function of t)

`skin` is the Palette Director's result ({family, roles, dark}); when given it replaces the
kit's own palette so the page and the video share ONE design system. Switching kits is a
data change only — nothing in the template or the runtime needs editing.

    python3 scripts/design/kit_export.py css     <design_system.json | kit.json>
    python3 scripts/design/kit_export.py runtime <design_system.json | kit.json> [--js] [--mode light|dark]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

try:
    from design.pick_kit import (MIN_PX, _hex, bezier_y, parse_bezier, role_color, skins,  # noqa: E402
                                 validate_kit, with_skin)
except Exception:  # pragma: no cover - direct sibling import when run without the package dir
    sys.path.insert(0, str(_HERE))
    from pick_kit import (MIN_PX, _hex, bezier_y, parse_bezier, role_color, skins,  # type: ignore  # noqa: E402
                          validate_kit, with_skin)

EASE_SAMPLES = 32


# ----------------------------------------------------------------------------- helpers
def _rgba(roles: dict, ref, default="ink") -> str:
    """A role reference resolved to the colour a renderer should paint with."""
    if isinstance(ref, str):
        ref = {"role": ref}
    ref = ref or {"role": default}
    name = ref.get("role", default)
    if name not in roles:
        name = default
    a = float(ref.get("alpha", 1.0))
    if a >= 1:
        return roles[name]
    r, g, b = _hex(roles[name])
    return f"rgba({r},{g},{b},{round(a, 3)})"


def _flat(ref, roles):
    return role_color(roles, ref) or roles.get("ink")


def _kebab(s: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in str(s).lower()).strip("-")


def _active(kit: dict, skin: dict | None) -> dict:
    return with_skin(kit, skin) if skin and skin.get("roles") else kit


def load_active(path) -> tuple:
    """(kit, skin) from a design_system.json (kit_spec + palette) or a bare kit JSON."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data.get("kit_spec"), dict):
        pal = data.get("palette") if isinstance(data.get("palette"), dict) else None
        return data["kit_spec"], (pal if pal and pal.get("roles") else None)
    return data, None


# ----------------------------------------------------------------------------- CSS
def _role_vars(roles: dict) -> list:
    return [f"  --c8-{_kebab(k)}: {v};" for k, v in sorted(roles.items())]


def _derived_vars(kit: dict, roles: dict) -> list:
    """Colour-dependent vars (props/charts/caption) for one skin."""
    p = kit.get("props") or {}
    ch = kit.get("charts") or {}
    cs = kit.get("caption_style") or {}
    out = []
    card = p.get("card") or {}
    out += [f"  --c8-card-fill: {_rgba(roles, card.get('fill'), 'paper')};",
            f"  --c8-card-border: {_rgba(roles, card.get('border'), 'ink')};"]
    chip = p.get("chip") or {}
    out += [f"  --c8-chip-fill: {_rgba(roles, chip.get('fill'), 'paper')};",
            f"  --c8-chip-ink: {_rgba(roles, chip.get('text'), 'ink')};"]
    node = p.get("node") or {}
    out += [f"  --c8-node-fill: {_rgba(roles, node.get('fill'), 'paper')};",
            f"  --c8-node-border: {_rgba(roles, node.get('border'), 'ink')};"]
    conn = p.get("connector") or {}
    out += [f"  --c8-connector: {_rgba(roles, conn.get('stroke'), 'muted')};"]
    call = p.get("callout") or {}
    out += [f"  --c8-callout: {_rgba(roles, call.get('stroke'), 'accent')};"]
    ser = ch.get("series") or {}
    for i, a in enumerate(ser.get("steps") or [], 1):
        out.append(f"  --c8-chart-{i}: {_flat({'role': ser.get('role', 'ink'), 'alpha': a}, roles)};")
    out += [f"  --c8-chart-highlight: {_rgba(roles, ch.get('highlight'), 'accent')};",
            f"  --c8-gridline: {_rgba(roles, (ch.get('gridline') or {}).get('stroke'), 'muted')};"]
    scrim = cs.get("scrim") or {}
    out += [f"  --c8-caption-ink: {_rgba(roles, cs.get('text'), 'ink')};",
            f"  --c8-caption-scrim: {_rgba(roles, scrim if scrim.get('role') else None, 'paper')};"]
    for name, m in sorted((cs.get("marks") or {}).items()):
        out.append(f"  --c8-mark-{_kebab(name)}: {_rgba(roles, m, 'accent')};")
    return out


def to_css_vars(kit: dict, skin: dict | None = None) -> str:
    k = _active(kit, skin)
    sk = skins(k)
    theme = k.get("theme") or {}
    fam = theme.get("family") or {}
    st = k.get("stage") or {}
    mo = k.get("motion") or {}
    cs = k.get("caption_style") or {}
    ent = mo.get("entrance") or {}
    ext = mo.get("exit") or {}
    static = [f"  --c8-kit: \"{k.get('id', 'unnamed')}\";",
              f"  --c8-base: {k.get('base', 'light')};"]
    for slot, stack in sorted(fam.items()):
        static.append(f"  --c8-font-{_kebab(slot)}: {stack};")
    for name, px in sorted((theme.get("scale") or {}).items()):
        static.append(f"  --c8-size-{_kebab(name)}: {px}px;")
    for name, px in sorted((theme.get("page_scale") or {}).items()):
        static.append(f"  --c8-page-size-{_kebab(name)}: {px}px;")
    for name, w in sorted((theme.get("weights") or {}).items()):
        static.append(f"  --c8-weight-{_kebab(name)}: {w};")
    if "line_height" in theme:
        static.append(f"  --c8-line-height: {theme['line_height']};")
    if "tracking_em" in theme:
        static.append(f"  --c8-tracking: {theme['tracking_em']}em;")
    for name, r in sorted((st.get("radius") or {}).items()):
        static.append(f"  --c8-radius-{_kebab(name)}: {r}px;")
    for name, w in sorted((st.get("stroke") or {}).items()):
        static.append(f"  --c8-stroke-{_kebab(name)}: {w}px;")
    sa = st.get("safe_area") or {}
    grid = st.get("grid") or {}
    static += [f"  --c8-safe-x: {sa.get('x_pct', 5)}%;", f"  --c8-safe-y: {sa.get('y_pct', 5)}%;",
               f"  --c8-grid-columns: {grid.get('columns', 12)};",
               f"  --c8-gutter: {grid.get('gutter_px', 32)}px;",
               f"  --c8-margin: {grid.get('margin_px', 96)}px;"]
    motion = [f"  --c8-ease: {mo.get('ease')};",
              f"  --c8-enter-ms: {mo.get('enter_ms')}ms;",
              f"  --c8-exit-ms: {mo.get('exit_ms')}ms;",
              f"  --c8-stagger-ms: {mo.get('stagger_ms')}ms;",
              f"  --c8-lead-ms: {mo.get('lead_ms', 120)}ms;",
              f"  --c8-enter-y: {ent.get('translate_y_px', 0)}px;",
              f"  --c8-enter-x: {ent.get('translate_x_px', 0)}px;",
              f"  --c8-enter-scale: {ent.get('scale_from', 1)};",
              f"  --c8-enter-blur: {ent.get('blur_px', 0)}px;",
              f"  --c8-enter-opacity: {ent.get('opacity_from', 0)};",
              f"  --c8-exit-y: {ext.get('translate_y_px', 0)}px;",
              f"  --c8-entrance: \"{ent.get('kind', 'fade')}\";",
              f"  --c8-exit: \"{ext.get('kind', 'fade')}\";"]
    trs = k.get("transitions") or []
    motion.append("  --c8-transitions: \"" + " ".join(str(t.get("id")) for t in trs) + "\";")
    for t in trs:
        motion.append(f"  --c8-transition-{_kebab(t.get('id'))}-ms: {t.get('ms', 0)}ms;")
    sec = next((t for t in trs if t.get("use") == "section"), None)
    if sec:
        motion.append(f"  --c8-transition-section: \"{sec.get('id')}\";")
    caption = [f"  --c8-caption-size: {max(MIN_PX, int(cs.get('font_px', MIN_PX)))}px;",
               f"  --c8-caption-weight: {cs.get('weight', 500)};",
               f"  --c8-caption-lines: {cs.get('max_lines', 2)};",
               f"  --c8-caption-width: {cs.get('width_pct', 72)}%;",
               f"  --c8-caption-radius: {(cs.get('scrim') or {}).get('radius_px', 8)}px;",
               f"  --c8-caption-pad: {(cs.get('scrim') or {}).get('pad_px', 16)}px;"]
    for name, m in sorted((cs.get("marks") or {}).items()):
        caption.append(f"  --c8-mark-{_kebab(name)}-style: \"{' '.join(m.get('styles') or [])}\";")

    light = _role_vars(sk["light"]) + _derived_vars(k, sk["light"])
    dark = _role_vars(sk["dark"]) + _derived_vars(k, sk["dark"])
    reduced = ["  --c8-enter-ms: 0ms;", "  --c8-exit-ms: 0ms;", "  --c8-stagger-ms: 0ms;",
               "  --c8-enter-y: 0px;", "  --c8-enter-x: 0px;", "  --c8-enter-scale: 1;", "  --c8-enter-blur: 0px;"]
    reduced += [f"  --c8-transition-{_kebab(t.get('id'))}-ms: 0ms;" for t in trs]
    head = (f"/* Coordn8 style kit: {k.get('name', k.get('id'))} ({k.get('id')}) — generated by "
            f"kit_export.py; edit the kit JSON, not this block. */")
    # The kit's base skin is the default; the other skin follows the reader's OS setting
    # unless the page pins one with data-theme.
    base = "dark" if k.get("base") == "dark" else "light"
    other = "light" if base == "dark" else "dark"
    skin_vars = {"light": light, "dark": dark}
    return "\n".join([
        head,
        ":root{", *static, *motion, *caption, *skin_vars[base], "}",
        f"@media (prefers-color-scheme: {other}){{", f" :root:not([data-theme={base}]){{",
        *[" " + ln for ln in skin_vars[other]], " }", "}",
        ":root[data-theme=dark]{", *dark, "}",
        ":root[data-theme=light]{", *light, "}",
        "@media (prefers-reduced-motion: reduce){", " :root{", *[" " + ln for ln in reduced], " }", "}",
        "",
    ])


def parse_css_vars(css: str, block: str = ":root{") -> dict:
    """Read back the first `block` of --c8-* declarations (used by tests and the page)."""
    i = css.index(block) + len(block)
    j = css.index("}", i)
    out = {}
    for ln in css[i:j].splitlines():
        ln = ln.strip()
        if ln.startswith("--c8-") and ":" in ln:
            k, v = ln.split(":", 1)
            out[k.strip()] = v.strip().rstrip(";").strip()
    return out


# ----------------------------------------------------------------------------- runtime
def _resolved(kit: dict, roles: dict) -> dict:
    p = kit.get("props") or {}
    ch = kit.get("charts") or {}
    cs = kit.get("caption_style") or {}
    st = kit.get("stage") or {}
    card, chip, node = p.get("card") or {}, p.get("chip") or {}, p.get("node") or {}
    conn, call = p.get("connector") or {}, p.get("callout") or {}
    ser = ch.get("series") or {}
    scrim = cs.get("scrim") or {}
    radius = st.get("radius") or {}
    return {
        "colors": dict(roles),
        "card": {"fill": _rgba(roles, card.get("fill"), "paper"), "stroke": _rgba(roles, card.get("border"), "ink"),
                 "stroke_width": card.get("border_width", 1), "radius": card.get("radius", radius.get("card", 12)),
                 "pad": card.get("pad_px", 18), "shadow": card.get("shadow", "none")},
        "chip": {"fill": _rgba(roles, chip.get("fill"), "paper"), "color": _rgba(roles, chip.get("text"), "ink"),
                 "radius": chip.get("radius", radius.get("chip", 999)), "weight": chip.get("weight", 600),
                 "shape": chip.get("shape")},
        "node": {"fill": _rgba(roles, node.get("fill"), "paper"), "stroke": _rgba(roles, node.get("border"), "ink"),
                 "stroke_width": node.get("border_width", 2), "radius": node.get("radius", radius.get("node", 12)),
                 "text_size": max(MIN_PX, int(node.get("text_px", 30)))},
        "connector": {"color": _rgba(roles, conn.get("stroke"), "muted"), "width": conn.get("width", 3),
                      "dash": conn.get("dash", []), "arrow": conn.get("arrow", "triangle"),
                      "routing": conn.get("routing", "straight"), "draw_on": bool(conn.get("draw_on", False))},
        "callout": {"style": call.get("style"), "color": _rgba(roles, call.get("stroke"), "accent"),
                    "width": call.get("width", 2), "label": _rgba(roles, call.get("label"), "ink"),
                    "dim_rest": call.get("dim_rest", 0)},
        "chart": {"series": [_flat({"role": ser.get("role", "ink"), "alpha": a}, roles)
                             for a in ser.get("steps") or [1.0]],
                  "highlight": _rgba(roles, ch.get("highlight"), "accent"),
                  "gridline": {"color": _rgba(roles, (ch.get("gridline") or {}).get("stroke"), "muted"),
                               "width": (ch.get("gridline") or {}).get("width", 1),
                               "dash": (ch.get("gridline") or {}).get("dash", [])},
                  "axis": {"color": _rgba(roles, (ch.get("axis") or {}).get("stroke"), "muted"),
                           "width": (ch.get("axis") or {}).get("width", 1.5)},
                  "label": {"color": _rgba(roles, ch.get("label"), "muted"),
                            "size": max(MIN_PX, int((ch.get("label") or {}).get("size_px", MIN_PX)))},
                  "increase_decrease": ch.get("increase_decrease"), "draw_on": bool(ch.get("draw_on"))},
        "caption": {"size": max(MIN_PX, int(cs.get("font_px", MIN_PX))), "weight": cs.get("weight", 500),
                    "max_lines": cs.get("max_lines", 2), "max_marks": cs.get("max_marks", 2),
                    "overflow": "truncate", "position": cs.get("position", "bottom-center"),
                    "width_pct": cs.get("width_pct", 72), "color": _rgba(roles, cs.get("text"), "ink"),
                    "scrim": _rgba(roles, scrim if scrim.get("role") else None, "paper"),
                    "scrim_radius": scrim.get("radius_px", 8), "pad": scrim.get("pad_px", 16),
                    "reveal": cs.get("reveal"), "dim_previous": bool(cs.get("dim_previous")),
                    "marks": {n: {"color": _rgba(roles, m, "accent"), "styles": list(m.get("styles") or [])}
                              for n, m in (cs.get("marks") or {}).items()}},
        "background": {"color": roles.get("paper"), **{k: v for k, v in (st.get("background") or {}).items()
                                                        if k != "base_role"}},
    }


def to_runtime(kit: dict, skin: dict | None = None, mode: str | None = None) -> dict:
    k = _active(kit, skin)
    sk = skins(k)
    mode = mode or k.get("base", "light")
    if mode not in ("light", "dark"):
        raise ValueError("mode must be light or dark")
    theme = k.get("theme") or {}
    fam = theme.get("family") or {}
    mo = k.get("motion") or {}
    st = k.get("stage") or {}
    pts = parse_bezier(mo.get("ease"))
    if pts is None:
        raise ValueError(f"kit motion.ease is not a valid cubic-bezier: {mo.get('ease')!r}")
    ms = lambda v, d=0: round(float(v if v is not None else d) / 1000.0, 4)  # noqa: E731
    trs = []
    for t in k.get("transitions") or []:
        row = {kk: vv for kk, vv in t.items() if kk not in ("ms",)}
        row["s"] = ms(t.get("ms", 0))
        if t.get("via_role"):
            row["via"] = sk[mode].get(t["via_role"])
        tp = parse_bezier(t.get("ease")) if t.get("ease") else pts
        row["ease"] = list(tp)
        trs.append(row)
    out = {
        "schema": 1,
        "kit": k.get("id"),
        "name": k.get("name"),
        "mode": mode,
        "palette_family": (k.get("palette") or {}).get("family", k.get("palette_family")),
        "min_legible_px": MIN_PX,
        "text": {"font": fam.get("body"), "display_font": fam.get("display", fam.get("body")),
                 "mono_font": fam.get("mono"), "math_font": fam.get("math"),
                 "sizes": {n: max(MIN_PX, int(v)) for n, v in (theme.get("scale") or {}).items()},
                 "weights": theme.get("weights") or {}, "line_height": theme.get("line_height", 1.4),
                 "tracking_em": theme.get("tracking_em", 0), "color": sk[mode].get("ink")},
        "stage": {"safe_area": st.get("safe_area"), "grid": st.get("grid"), "radius": st.get("radius"),
                  "stroke": st.get("stroke"), "depth": st.get("depth")},
        "motion": {"ease": list(pts), "ease_css": mo.get("ease"),
                   "ease_samples": [round(bezier_y(pts, i / EASE_SAMPLES), 5) for i in range(EASE_SAMPLES + 1)],
                   "enter_s": ms(mo.get("enter_ms")), "exit_s": ms(mo.get("exit_ms")),
                   "stagger_s": ms(mo.get("stagger_ms")), "lead_s": ms(mo.get("lead_ms"), 120),
                   "hold_min_s": ms(mo.get("hold_min_ms"), 0),
                   # Names the current runtime's beatAlpha() options use, so a scene can pass them as-is.
                   "fadeIn": ms(mo.get("enter_ms")), "fadeOut": ms(mo.get("exit_ms")),
                   "lead": ms(mo.get("lead_ms"), 120),
                   "entrance": mo.get("entrance"), "exit": mo.get("exit")},
        "transitions": trs,
        "continuity": k.get("continuity"),
        "modes": {m: _resolved(k, sk[m]) for m in ("light", "dark")},
    }
    out.update(_resolved(k, sk[mode]))
    return out


def to_runtime_js(kit: dict, skin: dict | None = None, mode: str | None = None) -> str:
    return "window.__C8_KIT__ = " + json.dumps(to_runtime(kit, skin, mode), ensure_ascii=False) + ";\n"


# ----------------------------------------------------------------------------- CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Export the active style kit as CSS vars or runtime JSON.")
    ap.add_argument("what", choices=["css", "runtime"])
    ap.add_argument("source", help="design_system.json (uses kit_spec + palette) or a kit JSON")
    ap.add_argument("--js", action="store_true", help="runtime: emit `window.__C8_KIT__ = {...};`")
    ap.add_argument("--mode", choices=["light", "dark"], help="runtime: which skin (default: the kit's base)")
    ap.add_argument("--out", help="write to this file instead of stdout")
    a = ap.parse_args(argv)
    try:
        kit, skin = load_active(a.source)
        errs = validate_kit(_active(kit, skin))
        if errs:
            print("ERROR: the active kit fails the outcome rules:\n  - " + "\n  - ".join(errs), file=sys.stderr)
            return 3
        if a.what == "css":
            text = to_css_vars(kit, skin)
        elif a.js:
            text = to_runtime_js(kit, skin, a.mode)
        else:
            text = json.dumps(to_runtime(kit, skin, a.mode), indent=1, ensure_ascii=False) + "\n"
    except (OSError, ValueError, json.JSONDecodeError) as ex:
        print(f"ERROR: {ex}", file=sys.stderr)
        return 3
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
