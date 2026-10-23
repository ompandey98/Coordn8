#!/usr/bin/env python3
"""Colour science for Coordn8 — pure Python, zero dependencies, Python 3.9+.

What is here, and the published method each part follows:

  * parse_color      CSS colour literals: #rgb #rgba #rrggbb #rrggbbaa rgb() rgba() and a few
                     keywords. Returns (r, g, b, a) with r,g,b in 0..255 floats and a in 0..1.
  * luminance        WCAG 2.x relative luminance (sRGB transfer curve, 0.04045 knee).
  * contrast         WCAG 2.x contrast ratio (L1 + 0.05) / (L2 + 0.05). A translucent foreground
                     is composited over its background first — a colour that passes at full
                     strength can fail at 0.5 opacity, so the ratio is always of what is painted.
  * OKLab / OKLCH    the OKLab perceptual space and its polar form (L 0..1, C, hue in degrees),
                     with gamut mapping by chroma reduction (hue and lightness are preserved).
  * CIELAB / ΔE2000  CIE 1976 L*a*b* (D65) and the CIEDE2000 colour difference, checked against
                     the published CIEDE2000 test pairs in tests/v2/test_palette.py.

Every function takes either a colour string or an (r, g, b[, a]) tuple.
"""
from __future__ import annotations

import math
import re
from typing import Iterable, Optional, Sequence, Tuple, Union

RGB = Tuple[float, float, float]
RGBA = Tuple[float, float, float, float]
ColorLike = Union[str, Sequence[float]]

_HEX = re.compile(r"^#([0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_FUNC = re.compile(r"^rgba?\(\s*([^)]*)\)$", re.IGNORECASE)
KEYWORDS = {
    "white": (255.0, 255.0, 255.0, 1.0),
    "black": (0.0, 0.0, 0.0, 1.0),
    "transparent": (0.0, 0.0, 0.0, 0.0),
}
# Values that are not colours at all (the gate ignores them rather than failing them).
NON_COLOURS = {"none", "currentcolor", "inherit", "initial", "unset", "revert", "auto", ""}

# Any colour literal inside a longer CSS value (used by the gate's HTML/SVG reader).
COLOR_TOKEN = re.compile(
    r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{4}|[0-9a-fA-F]{3})(?![0-9a-fA-F\w])"
    r"|rgba?\(\s*[0-9.%\s,/]+\)",
    re.IGNORECASE,
)


class ColorError(ValueError):
    """A string is not a colour this module can read."""


# --------------------------------------------------------------------------- parsing
def parse_color(value: ColorLike) -> RGBA:
    """Return (r, g, b, a): channels 0..255 floats, alpha 0..1."""
    if isinstance(value, (tuple, list)):
        if len(value) == 3:
            return (float(value[0]), float(value[1]), float(value[2]), 1.0)
        if len(value) == 4:
            return (float(value[0]), float(value[1]), float(value[2]), float(value[3]))
        raise ColorError(f"colour tuple must have 3 or 4 items: {value!r}")
    s = str(value).strip()
    low = s.lower()
    if low in KEYWORDS:
        return KEYWORDS[low]
    m = _HEX.match(s)
    if m:
        h = m.group(1)
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        a = int(h[6:8], 16) / 255.0 if len(h) == 8 else 1.0
        return (float(r), float(g), float(b), a)
    m = _FUNC.match(s)
    if m:
        parts = [p for p in re.split(r"[\s,/]+", m.group(1).strip()) if p]
        if len(parts) not in (3, 4):
            raise ColorError(f"cannot read {s!r}")
        ch = []
        for p in parts[:3]:
            if p.endswith("%"):
                ch.append(float(p[:-1]) * 2.55)
            else:
                ch.append(float(p))
        a = 1.0
        if len(parts) == 4:
            p = parts[3]
            a = float(p[:-1]) / 100.0 if p.endswith("%") else float(p)
        if any(c < 0 or c > 255 for c in ch) or not 0.0 <= a <= 1.0:
            raise ColorError(f"out-of-range colour {s!r}")
        return (ch[0], ch[1], ch[2], a)
    raise ColorError(f"not a colour: {s!r}")


def try_parse(value) -> Optional[RGBA]:
    try:
        return parse_color(value)
    except (ColorError, ValueError, TypeError):
        return None


def rgb_of(value: ColorLike) -> RGB:
    r, g, b, _ = parse_color(value)
    return (r, g, b)


def to_hex(rgb: Sequence[float]) -> str:
    """(r, g, b) 0..255 -> '#rrggbb' (rounded, clamped)."""
    return "#" + "".join(f"{max(0, min(255, int(round(c)))):02x}" for c in rgb[:3])


def composite(fg: ColorLike, bg: ColorLike, alpha: Optional[float] = None) -> RGB:
    """Flatten a (possibly translucent) foreground onto an opaque background."""
    fr, fgc, fb, fa = parse_color(fg)
    br, bgc, bb, _ = parse_color(bg)
    a = fa if alpha is None else fa * alpha
    return (fr * a + br * (1 - a), fgc * a + bgc * (1 - a), fb * a + bb * (1 - a))


# --------------------------------------------------------------------------- WCAG
def _lin(c: float) -> float:
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _unlin(c: float) -> float:
    v = 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055
    return v * 255.0


def luminance(color: ColorLike) -> float:
    r, g, b = rgb_of(color)
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast(fg: ColorLike, bg: ColorLike) -> float:
    """WCAG 2.x contrast ratio of what is painted: fg composited over an opaque bg."""
    bg_rgb = composite(bg, "#ffffff") if parse_color(bg)[3] < 1 else rgb_of(bg)
    fg_rgb = composite(fg, bg_rgb)
    l1, l2 = luminance(fg_rgb), luminance(bg_rgb)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


# --------------------------------------------------------------------------- OKLab / OKLCH
def rgb_to_oklab(color: ColorLike) -> Tuple[float, float, float]:
    r, g, b = (_lin(c) for c in rgb_of(color))
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = (math.copysign(abs(v) ** (1 / 3), v) for v in (l, m, s))
    return (0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
            1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
            0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_)


def oklab_to_linear(L: float, a: float, b: float) -> Tuple[float, float, float]:
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    return (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
            -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
            -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)


def rgb_to_oklch(color: ColorLike) -> Tuple[float, float, float]:
    """-> (L 0..1, C >= 0, hue degrees 0..360)."""
    L, a, b = rgb_to_oklab(color)
    C = math.hypot(a, b)
    h = math.degrees(math.atan2(b, a)) % 360.0
    return (L, C, h)


def _in_gamut(lin: Iterable[float], eps: float = 1e-7) -> bool:
    return all(-eps <= c <= 1 + eps for c in lin)


def oklch_to_rgb(L: float, C: float, h: float) -> Tuple[RGB, float]:
    """OKLCH -> sRGB (0..255). Out-of-gamut colours keep L and hue and lose chroma
    (bisection) until they fit. Returns (rgb, chroma_actually_used)."""
    L = max(0.0, min(1.0, L))

    def lin_at(c: float):
        hr = math.radians(h)
        return oklab_to_linear(L, c * math.cos(hr), c * math.sin(hr))

    lin = lin_at(C)
    used = C
    if not _in_gamut(lin):
        lo, hi = 0.0, C
        for _ in range(40):
            mid = (lo + hi) / 2
            if _in_gamut(lin_at(mid)):
                lo = mid
            else:
                hi = mid
        used = lo
        lin = lin_at(lo)
    rgb = tuple(_unlin(max(0.0, min(1.0, c))) for c in lin)
    return (rgb[0], rgb[1], rgb[2]), used


def oklch_hex(L: float, C: float, h: float) -> str:
    return to_hex(oklch_to_rgb(L, C, h)[0])


def hue_distance(h1: float, h2: float) -> float:
    d = abs(h1 - h2) % 360.0
    return min(d, 360.0 - d)


# --------------------------------------------------------------------------- CIELAB / CIEDE2000
_WHITE_D65 = (0.95047, 1.0, 1.08883)


def rgb_to_lab(color: ColorLike) -> Tuple[float, float, float]:
    r, g, b = (_lin(c) for c in rgb_of(color))
    x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / _WHITE_D65[0]
    y = (0.2126729 * r + 0.7151522 * g + 0.0721750 * b) / _WHITE_D65[1]
    z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / _WHITE_D65[2]

    def f(t: float) -> float:
        return t ** (1 / 3) if t > (6 / 29) ** 3 else t / (3 * (6 / 29) ** 2) + 4 / 29

    fx, fy, fz = f(x), f(y), f(z)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def ciede2000(lab1: Sequence[float], lab2: Sequence[float],
              kL: float = 1.0, kC: float = 1.0, kH: float = 1.0) -> float:
    """CIEDE2000 colour difference between two CIELAB colours."""
    L1, a1, b1 = lab1
    L2, a2, b2 = lab2
    C1 = math.hypot(a1, b1)
    C2 = math.hypot(a2, b2)
    Cbar = (C1 + C2) / 2
    G = 0.5 * (1 - math.sqrt(Cbar ** 7 / (Cbar ** 7 + 25 ** 7))) if Cbar > 0 else 0.0
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360 if (a1p or b1) else 0.0
    h2p = math.degrees(math.atan2(b2, a2p)) % 360 if (a2p or b2) else 0.0
    dLp = L2 - L1
    dCp = C2p - C1p
    if C1p * C2p == 0:
        dhp = 0.0
    else:
        dhp = h2p - h1p
        if dhp > 180:
            dhp -= 360
        elif dhp < -180:
            dhp += 360
    dHp = 2 * math.sqrt(C1p * C2p) * math.sin(math.radians(dhp / 2))
    Lbp = (L1 + L2) / 2
    Cbp = (C1p + C2p) / 2
    if C1p * C2p == 0:
        hbp = h1p + h2p
    else:
        hbp = (h1p + h2p) / 2
        if abs(h1p - h2p) > 180:
            hbp = hbp + 180 if (h1p + h2p) < 360 else hbp - 180
    T = (1 - 0.17 * math.cos(math.radians(hbp - 30)) + 0.24 * math.cos(math.radians(2 * hbp))
         + 0.32 * math.cos(math.radians(3 * hbp + 6)) - 0.20 * math.cos(math.radians(4 * hbp - 63)))
    dtheta = 30 * math.exp(-(((hbp - 275) / 25) ** 2))
    Rc = 2 * math.sqrt(Cbp ** 7 / (Cbp ** 7 + 25 ** 7)) if Cbp > 0 else 0.0
    Sl = 1 + (0.015 * (Lbp - 50) ** 2) / math.sqrt(20 + (Lbp - 50) ** 2)
    Sc = 1 + 0.045 * Cbp
    Sh = 1 + 0.015 * Cbp * T
    Rt = -math.sin(math.radians(2 * dtheta)) * Rc
    return math.sqrt((dLp / (kL * Sl)) ** 2 + (dCp / (kC * Sc)) ** 2 + (dHp / (kH * Sh)) ** 2
                     + Rt * (dCp / (kC * Sc)) * (dHp / (kH * Sh)))


def delta_e(c1: ColorLike, c2: ColorLike) -> float:
    """CIEDE2000 between two sRGB colours (alpha ignored: compare the inks)."""
    return ciede2000(rgb_to_lab(c1), rgb_to_lab(c2))


def chroma(color: ColorLike) -> float:
    return rgb_to_oklch(color)[1]


__all__ = [
    "ColorError", "parse_color", "try_parse", "rgb_of", "to_hex", "composite", "luminance",
    "contrast", "rgb_to_oklab", "rgb_to_oklch", "oklch_to_rgb", "oklch_hex", "hue_distance",
    "rgb_to_lab", "ciede2000", "delta_e", "chroma", "COLOR_TOKEN", "NON_COLOURS",
]
