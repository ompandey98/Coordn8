#!/usr/bin/env python3
"""
Coordn8 gate — CHART HONESTY (B5). Advisory, like every Coordn8 gate.

A chart can lie while rendering perfectly: a bar drawn 20 % too long, a pie slice a few degrees
too wide, one line point nudged, an area that closes above zero, a printed number that no data
backs. None of that errors. This gate holds the DRAWING to the DATA it declares.

THE DATA CONTRACT (data-* attributes, readable in any SVG or HTML):
  container   any element with data-chart="<family>"   (data-chart-id / id names it)
  bar         <rect data-value>             horizontal: width  = k*|value| from one baseline
  column      <rect data-value>             vertical:   height = k*|value| from one baseline
  stacked-bar / stacked-column
              <rect data-value data-stack>  segments contiguous from the baseline, one shared k;
                                            optional data-total on the stack (or a mark) = sum
  line        <polyline|path data-values [data-xs]> and/or <circle data-x data-y>
                                            x and y each on one shared linear scale
  scatter     <circle data-x data-y [data-size]>    same; area (r^2) proportional to size
  area        <path|polygon data-values [data-xs] [data-baseline]>
                                            first N points = the top edge on the shared scales;
                                            the closing points sit where data-baseline (0) maps
  pie / donut <path data-value>             sweep angle = 360 deg * value / total, slices
                                            contiguous around one shared centre and radius
  ticks       any element with data-tick="x|y" data-value   drawn where the scale puts it
  scenery     data-role="axis|grid|background|legend|title|hole|..."  is not data
  printed numbers  every number printed inside a chart must equal a declared data-* number (or,
              in a pie/donut, a slice share recomputed from the data). A number that is genuinely
              not data carries data-unbound="<reason>".

ROBUST SCALE FIT. The scale is never taken from one anchor mark (one bad anchor would condemn every
honest mark). Length/area families take the MEDIAN of per-mark ratios (pixels per unit) and reject
outliers by the MEDIAN ABSOLUTE DEVIATION (3.5 x 1.4826 x MAD, floor 1 %); position families take
the median of pairwise slopes, reject by the MAD of residuals, and refit on the inliers. So one bad
point cannot move the scale: it is flagged, and every honest mark still passes. Two marks that
disagree cannot be told apart and are both reported (fail closed); one mark with no tick is
unverifiable and is reported, never passed silently.

Transforms: translate() on an ancestor is resolved; any other transform (scale, rotate, matrix,
skew, a CSS transform) makes the mark's drawn geometry unverifiable and is reported.

Freedom (H1): an unknown or invented family (data-chart="invented: radial-bars") is never failed
for being unknown — its printed numbers are still checked against its data, and the report notes
that no geometry checker exists for it.

Usage:
  python3 scripts/gates/check_chart_honesty.py <file.svg|file.html|dir> [more ...] [--json OUT]
Exit: 0 PASS · 1 REVIEW (findings) · 2 SKIPPED (no chart found) · 3 ERROR (unreadable input)
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.common import EXIT_ERROR, EXIT_PASS, EXIT_REVIEW, EXIT_SKIPPED, emit  # noqa: E402

GATE = "chart_honesty"
POS_TOL = 1.0          # px: coordinates ship rounded to <= 0.5 px
REL_TOL = 0.01         # 1 % of the expected length, for long marks
TICK_TOL = 6.0         # px: a text tick sits beside its gridline (baseline offset)
ANGLE_TOL = 0.75       # degrees
CENTRE_TOL = 1.5       # px
MAD_K = 3.5            # outlier threshold in robust standard deviations
MAD_SIGMA = 1.4826     # MAD -> standard deviation for normal noise

FAMILY_ALIASES = {
    "bar": "bar", "hbar": "bar", "horizontal-bar": "bar", "bars": "bar",
    "column": "column", "vbar": "column", "vertical-bar": "column", "columns": "column",
    "stacked-bar": "stacked-bar", "stacked": "stacked-bar", "stacked-hbar": "stacked-bar",
    "stacked-column": "stacked-column", "stacked-vbar": "stacked-column",
    "line": "line", "lines": "line", "sparkline": "line",
    "scatter": "scatter", "dot": "scatter", "bubble": "scatter",
    "area": "area",
    "pie": "pie", "donut": "donut", "doughnut": "donut", "ring": "donut",
}
KNOWN = sorted(set(FAMILY_ALIASES.values()))
SKIP_TEXT_TAGS = {"title", "desc", "style", "script", "metadata", "defs"}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
        "source", "track", "wbr"}
NUM_RE = re.compile(r"(?<![\w.])([+\-−]?)(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s?([kKMB]|%)?(?![\w])")
FLOAT_RE = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
TRANSLATE_RE = re.compile(r"^\s*translate\(\s*([-+0-9.eE]+)(?:[\s,]+([-+0-9.eE]+))?\s*\)\s*$")
CSS_TRANSFORM_RE = re.compile(r"(?:^|;)\s*(?:transform|translate|rotate|scale)\s*:", re.I)


# ----------------------------------------------------------------------------------- parsing
class El:
    __slots__ = ("tag", "attrs", "parent", "children", "text", "line", "dx", "dy", "untrusted")

    def __init__(self, tag, attrs, parent, line):
        self.tag, self.attrs, self.parent, self.line = tag, attrs, parent, line
        self.children: list = []
        self.text: list = []
        pdx, pdy, pu = (parent.dx, parent.dy, parent.untrusted) if parent is not None else (0.0, 0.0, None)
        self.dx, self.dy, self.untrusted = pdx, pdy, pu
        tf = attrs.get("transform")
        if tf is not None and tf.strip():
            m = TRANSLATE_RE.match(tf)
            if m:
                self.dx += float(m.group(1))
                self.dy += float(m.group(2) or 0.0)
            else:
                self.untrusted = self.untrusted or f"transform={tf.strip()!r} on <{tag}>"
        st = attrs.get("style") or ""
        if CSS_TRANSFORM_RE.search(st):
            self.untrusted = self.untrusted or f"a CSS transform in style={st!r} on <{tag}>"

    def iter(self):
        yield self
        for c in self.children:
            yield from c.iter()


class Tree(HTMLParser):
    """Element tree from the stdlib HTML tokenizer: first attribute wins (as in a browser), quoted
    '>' stays inside its value, comments and <script>/<style> bodies are never live markup."""

    def __init__(self, source: str):
        super().__init__(convert_charrefs=True)
        self.root = El("#root", {}, None, 0)
        self.stack = [self.root]
        self.feed(source)
        self.close()

    @staticmethod
    def _attrs(attrs):
        out = {}
        for k, v in attrs:
            out.setdefault(k.lower(), "" if v is None else v)
        return out

    def handle_starttag(self, tag, attrs):
        el = El(tag, self._attrs(attrs), self.stack[-1], self.getpos()[0])
        self.stack[-1].children.append(el)
        if tag not in VOID:
            self.stack.append(el)

    def handle_startendtag(self, tag, attrs):
        el = El(tag, self._attrs(attrs), self.stack[-1], self.getpos()[0])
        self.stack[-1].children.append(el)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].text.append(data)


def num(v):
    if v is None:
        return None
    try:
        x = float(str(v).strip().replace(",", ""))
    except ValueError:
        return None
    return x if math.isfinite(x) else None


def num_list(v):
    if v is None:
        return None
    parts = [p for p in re.split(r"[\s,;]+", str(v).strip()) if p]
    out = [num(p) for p in parts]
    return out if out and all(x is not None for x in out) else None


def poly_points(s: str):
    vals = [float(x) for x in FLOAT_RE.findall(s or "")]
    return [(vals[i], vals[i + 1]) for i in range(0, len(vals) - 1, 2)]


def path_points(d: str):
    """Vertices of an SVG path (absolute and relative M/L/H/V/C/S/Q/T/A — the END point of each
    segment). Returns (points, curved?)."""
    toks = re.findall(r"[MmLlHhVvCcSsQqTtAaZz]|" + FLOAT_RE.pattern, d or "")
    pts, cur, start, cmd, i, curved = [], (0.0, 0.0), (0.0, 0.0), None, 0, False
    arity = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0}
    while i < len(toks):
        t = toks[i]
        if re.match(r"[A-Za-z]", t):
            cmd = t
            i += 1
            if cmd in "Zz":
                cur = start
                continue
        if cmd is None:
            break
        n = arity[cmd.upper()]
        args = toks[i:i + n]
        if len(args) < n or any(re.match(r"[A-Za-z]", a) for a in args):
            break
        a = [float(x) for x in args]
        i += n
        rel = cmd.islower()
        C = cmd.upper()
        if C in "CSQTA":
            curved = curved or C != "T"
        if C == "H":
            p = (cur[0] + a[0] if rel else a[0], cur[1])
        elif C == "V":
            p = (cur[0], cur[1] + a[0] if rel else a[0])
        else:
            ex, ey = a[-2], a[-1]
            p = (cur[0] + ex, cur[1] + ey) if rel else (ex, ey)
        pts.append(p)
        cur = p
        if C == "M":
            start = p
            cmd = "l" if rel else "L"
    return pts, curved


def arcs_of(d: str):
    """Every elliptical-arc segment of a path as (x1, y1, rx, ry, rot, large, sweep, x2, y2)."""
    toks = re.findall(r"[MmLlHhVvCcSsQqTtAaZz]|" + FLOAT_RE.pattern, d or "")
    out, cur, start, cmd, i = [], (0.0, 0.0), (0.0, 0.0), None, 0
    arity = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0}
    while i < len(toks):
        t = toks[i]
        if re.match(r"[A-Za-z]", t):
            cmd = t
            i += 1
            if cmd in "Zz":
                cur = start
                continue
        if cmd is None:
            break
        n = arity[cmd.upper()]
        args = toks[i:i + n]
        if len(args) < n or any(re.match(r"[A-Za-z]", x) for x in args):
            break
        a = [float(x) for x in args]
        i += n
        rel, C = cmd.islower(), cmd.upper()
        if C == "H":
            p = (cur[0] + a[0] if rel else a[0], cur[1])
        elif C == "V":
            p = (cur[0], cur[1] + a[0] if rel else a[0])
        else:
            p = (cur[0] + a[-2], cur[1] + a[-1]) if rel else (a[-2], a[-1])
        if C == "A":
            out.append((cur[0], cur[1], abs(a[0]), abs(a[1]), a[2], int(a[3]), int(a[4]), p[0], p[1]))
        cur = p
        if C == "M":
            start = p
            cmd = "l" if rel else "L"
    return out


def arc_centre(x1, y1, r, large, sweep, x2, y2):
    """SVG endpoint -> centre parametrisation (SVG 1.1 F.6.5, circular arc, no rotation).
    Returns (cx, cy, start_angle, signed_sweep) in radians; the sweep is what a browser draws."""
    xp, yp = (x1 - x2) / 2.0, (y1 - y2) / 2.0
    if r <= 0 or (xp == 0 and yp == 0):
        return None
    lam = (xp * xp + yp * yp) / (r * r)
    if lam > 1:
        r *= math.sqrt(lam)
    num_ = r * r * r * r - r * r * yp * yp - r * r * xp * xp
    den = r * r * yp * yp + r * r * xp * xp
    coef = math.sqrt(max(0.0, num_ / den)) if den else 0.0
    if large == sweep:
        coef = -coef
    cxp, cyp = coef * yp, -coef * xp
    cx, cy = cxp + (x1 + x2) / 2.0, cyp + (y1 + y2) / 2.0
    ux, uy = (xp - cxp) / r, (yp - cyp) / r
    vx, vy = (-xp - cxp) / r, (-yp - cyp) / r
    th1 = math.atan2(uy, ux)
    dth = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
    if sweep == 0 and dth > 0:
        dth -= 2 * math.pi
    elif sweep == 1 and dth < 0:
        dth += 2 * math.pi
    return cx, cy, th1, dth, r


# ------------------------------------------------------------------------------- robust fits
def median(xs):
    s = sorted(xs)
    n = len(s)
    if not n:
        return None
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def robust_ratio(samples):
    """samples = [(key, ratio)]. Median of per-mark ratios, MAD outlier rejection, median of the
    inliers. Returns {scale, median, mad, inliers, outliers, ambiguous}."""
    rs = [r for _, r in samples]
    if not rs:
        return None
    med = median(rs)
    if len(rs) == 2:
        a, b = rs
        spread = abs(a - b) / max(abs(med), 1e-12)
        return {"scale": med, "median": med, "mad": abs(a - b) / 2, "inliers": [k for k, _ in samples],
                "outliers": [], "ambiguous": spread > REL_TOL * 2}
    mad = median([abs(r - med) for r in rs])
    thr = max(MAD_K * MAD_SIGMA * mad, REL_TOL * abs(med))
    inl = [(k, r) for k, r in samples if abs(r - med) <= thr]
    out = [k for k, r in samples if abs(r - med) > thr]
    scale = median([r for _, r in inl]) if inl else med
    return {"scale": scale, "median": med, "mad": mad, "inliers": [k for k, _ in inl], "outliers": out,
            "ambiguous": False}


def theil_sen(pts):
    slopes = [(cb - ca) / (vb - va) for i, (va, ca) in enumerate(pts) for vb, cb in pts[i + 1:] if vb != va]
    if not slopes:
        return None
    a = median(slopes)
    return a, median([c - a * v for v, c in pts])


def robust_affine(points):
    """points = [(key, value, coord)]. coord = a*value + b. Median of pairwise slopes, MAD of the
    residuals for outlier rejection, refit on the inliers. Returns dict or None (degenerate)."""
    pts = [(v, c) for _, v, c in points]
    if len({v for v, _ in pts}) < 2:
        return None
    fit = theil_sen(pts)
    if fit is None:
        return None
    a, b = fit
    res = [c - (a * v + b) for v, c in pts]
    mres = median(res)
    mad = median([abs(r - mres) for r in res])
    thr = max(MAD_K * MAD_SIGMA * mad, POS_TOL)
    inl = [p for p, r in zip(points, res) if abs(r - mres) <= thr]
    out = [p[0] for p, r in zip(points, res) if abs(r - mres) > thr]
    if len({v for _, v, _ in inl}) >= 2:
        refit = theil_sen([(v, c) for _, v, c in inl])
        if refit is not None:
            a, b = refit
    return {"a": a, "b": b, "mad": mad, "inliers": [p[0] for p in inl], "outliers": out}


def tol_for(expected_len: float) -> float:
    return max(POS_TOL, REL_TOL * abs(expected_len))


# ----------------------------------------------------------------------------------- checker
class Checker:
    def __init__(self, name: str):
        self.name = name
        self.findings: list = []
        self.notes: list = []
        self.charts: list = []

    def find(self, chart, family, kind, mark, detail):
        self.findings.append({"file": self.name, "chart": chart, "family": family, "kind": kind,
                              "mark": mark, "detail": detail})

    # -- helpers ------------------------------------------------------------------------
    @staticmethod
    def chart_members(chart: El):
        """Descendants of a chart that are not inside a nested chart."""
        out = []

        def walk(el):
            for c in el.children:
                if "data-chart" in c.attrs:
                    continue
                out.append(c)
                walk(c)
        walk(chart)
        return out

    @staticmethod
    def scenery(el: El, chart: El) -> bool:
        cur = el
        while cur is not None and cur is not chart:
            if "data-role" in cur.attrs or "data-tick" in cur.attrs:
                return True
            cur = cur.parent
        return False

    @staticmethod
    def mark_name(el: El, i: int) -> str:
        return el.attrs.get("data-name") or el.attrs.get("id") or f"{el.tag}#{i + 1}@line{el.line}"

    def geom_ok(self, el, cid, fam, mname) -> bool:
        if el.untrusted:
            self.find(cid, fam, "transform", mname, f"{mname} sits under {el.untrusted}; its drawn geometry "
                      "cannot be verified — bake the movement into the coordinates")
            return False
        return True

    # -- printed numbers ------------------------------------------------------------------
    def declared_numbers(self, members, chart):
        vals = []
        for el in [chart] + members:
            for k, v in el.attrs.items():
                if not k.startswith("data-") or k in ("data-chart", "data-role", "data-name", "data-stack",
                                                      "data-tick", "data-unbound", "data-chart-id"):
                    continue
                lst = num_list(v)
                if lst:
                    vals.extend(lst)
        return vals

    def check_printed(self, chart, cid, fam, members, shares):
        declared = self.declared_numbers(members, chart)
        texts = []

        def collect(el, unbound):
            if el.tag in SKIP_TEXT_TAGS:
                return
            if "data-chart" in el.attrs and el is not chart:
                return
            ub = unbound
            if "data-unbound" in el.attrs:
                if not el.attrs["data-unbound"].strip():
                    self.find(cid, fam, "unbound-reason", el.tag, f"<{el.tag}> at line {el.line} declares "
                              "data-unbound with no reason; say why this number is not data")
                else:
                    ub = el.attrs["data-unbound"].strip()
            body = "".join(el.text)
            if body.strip():
                texts.append((el, body, ub))
            for c in el.children:
                collect(c, ub)
        collect(chart, None)
        bound = unbound_n = 0
        for el, body, ub in texts:
            for m in NUM_RE.finditer(body):
                sign, ip, fp, suf = m.group(1), m.group(2), m.group(3) or "", m.group(4) or ""
                v = float(ip.replace(",", "") + fp)
                if sign in ("-", "−"):
                    v = -v
                dec = len(fp) - 1 if fp else 0
                half = 0.5 * 10 ** (-dec)
                mult = {"k": 1e3, "K": 1e3, "M": 1e6, "B": 1e9}.get(suf, 1.0)
                cands = [v * mult]
                tol = half * mult + 1e-9 * max(1.0, abs(v * mult))
                ok = any(abs(c - d) <= tol for c in cands for d in declared)
                if not ok and suf == "%" and shares:
                    ok = any(abs(v - s) <= half + 1e-9 for s in shares)
                if not ok and ub:
                    self.notes.append(f"{self.name}:{cid}: printed '{m.group(0).strip()}' is declared not-data ({ub})")
                    continue
                if ok:
                    bound += 1
                else:
                    unbound_n += 1
                    self.find(cid, fam, "unbound-number", m.group(0).strip(),
                              f"printed number '{m.group(0).strip()}' (line {el.line}) matches no data-* value "
                              "in this chart — bind it with data-value, or say why it is not data with data-unbound")
        return bound, unbound_n

    # -- ticks ---------------------------------------------------------------------------
    def ticks(self, members, axis):
        out = []
        for el in members:
            if el.attrs.get("data-tick", "").strip().lower() != axis:
                continue
            v = num(el.attrs.get("data-value"))
            if v is None:
                continue
            if el.tag == "line":
                c = num(el.attrs.get("x1" if axis == "x" else "y1"))
                precise = True
            else:
                c = num(el.attrs.get("x" if axis == "x" else "y"))
                precise = False
            if c is None:
                continue
            c += el.dx if axis == "x" else el.dy
            out.append((el, v, c, precise))
        return out

    # -- families ------------------------------------------------------------------------
    def check_lengths(self, cid, fam, members, chart):
        horizontal = fam in ("bar", "stacked-bar")
        stacked = fam.startswith("stacked")
        marks = []
        for i, el in enumerate(members):
            if el.tag != "rect" or self.scenery(el, chart):
                continue
            mname = self.mark_name(el, i)
            v = num(el.attrs.get("data-value"))
            if v is None:
                self.find(cid, fam, "unbound-mark", mname, f"{mname} is a drawn bar with no data-value; every "
                          "mark must carry the number it draws (or data-role if it is scenery)")
                continue
            if not self.geom_ok(el, cid, fam, mname):
                continue
            x, y = num(el.attrs.get("x")) or 0.0, num(el.attrs.get("y")) or 0.0
            w, h = num(el.attrs.get("width")) or 0.0, num(el.attrs.get("height")) or 0.0
            x, y = x + el.dx, y + el.dy
            length = w if horizontal else h
            # the end that sits on the baseline: bar -> left (positive) / right (negative);
            # column -> bottom (positive) / top (negative)
            if horizontal:
                base, far = (x, x + w) if v >= 0 else (x + w, x)
            else:
                base, far = (y + h, y) if v >= 0 else (y, y + h)
            stack = None
            if stacked:
                cur = el
                while cur is not None and cur is not chart and stack is None:
                    stack = cur.attrs.get("data-stack")
                    cur = cur.parent
                stack = stack or "(unstacked)"
            marks.append({"el": el, "name": mname, "v": v, "len": length, "base": base, "far": far,
                          "lo": min(x, x + w) if horizontal else min(y, y + h),
                          "hi": max(x, x + w) if horizontal else max(y, y + h), "stack": stack})
        if not marks:
            self.find(cid, fam, "no-data", cid, "a chart that declares a length family but no mark declares "
                      "data-value: nothing here is verifiable (fail closed)")
            return {}
        if stacked and any(m["v"] < 0 for m in marks):
            self.notes.append(f"{self.name}:{cid}: negative stacked segments are not geometry-checked")
            marks = [m for m in marks if m["v"] >= 0]
        # baseline: robust (median); stacked -> the start of each stack's first segment
        if stacked:
            stacks = {}
            for m in marks:
                stacks.setdefault(m["stack"], []).append(m)
            if horizontal:
                firsts = [min(ms, key=lambda q: q["lo"])["lo"] for ms in stacks.values()]
            else:
                firsts = [max(ms, key=lambda q: q["hi"])["hi"] for ms in stacks.values()]
            baseline = median(firsts)
        else:
            baseline = median([m["base"] for m in marks if m["v"] != 0] or [m["base"] for m in marks])
        samples = [(m["name"], m["len"] / abs(m["v"])) for m in marks if m["v"] != 0]
        axis = "x" if horizontal else "y"
        tks = self.ticks(members, axis)
        for el, v, c, precise in tks:
            if precise and v != 0:
                samples.append((f"tick {v:g}", abs(c - baseline) / abs(v)))
        fit = robust_ratio(samples)
        if fit is None:
            self.find(cid, fam, "unverifiable", cid, "every mark declares zero: no scale can be derived")
            return {}
        k = fit["scale"]
        real = [s for s in samples if not s[0].startswith("tick ")]
        if len(samples) == 1:
            self.find(cid, fam, "unverifiable", real[0][0] if real else cid,
                      "one mark and no precise tick: its length defines the scale instead of being checked by it "
                      "(add a second mark or a <line data-tick> on the value axis)")
        if fit["ambiguous"]:
            names = ", ".join(n for n, _ in samples)
            self.find(cid, fam, "ambiguous-scale", names, f"two scale witnesses ({names}) disagree by more than "
                      f"{REL_TOL * 200:.0f} %; with only two the honest one cannot be told apart — add a tick")
        sign = 1.0 if horizontal else -1.0
        for m in marks:
            exp_len = k * abs(m["v"])
            if not stacked and abs(m["base"] - baseline) > POS_TOL:
                self.find(cid, fam, "baseline", m["name"], f"{m['name']} starts at {m['base']:.1f} px but the "
                          f"shared baseline is {baseline:.1f} px — a bar must start at zero")
            d = m["len"] - exp_len
            if abs(d) > tol_for(exp_len):
                pct = (100.0 * d / exp_len) if exp_len else float("inf")
                self.find(cid, fam, "length", m["name"],
                          f"{m['name']} declares {m['v']:g} and draws {m['len']:.1f} px, but the shared scale "
                          f"({k:.4g} px/unit, fitted from its peers) puts {exp_len:.1f} px — {pct:+.1f} %")
        if stacked:
            for sid, ms in sorted(stacks.items()):
                ms = [m for m in ms if m["v"] >= 0]
                if horizontal:
                    ms.sort(key=lambda q: q["lo"])
                    pos = baseline
                    for m in ms:
                        if abs(m["lo"] - pos) > POS_TOL:
                            self.find(cid, fam, "stack-gap", m["name"], f"stack '{sid}': {m['name']} starts at "
                                      f"{m['lo']:.1f} px, the previous segment ended at {pos:.1f} px")
                        pos = m["hi"]
                    drawn_total = pos - baseline
                else:
                    ms.sort(key=lambda q: -q["hi"])
                    pos = baseline
                    for m in ms:
                        if abs(m["hi"] - pos) > POS_TOL:
                            self.find(cid, fam, "stack-gap", m["name"], f"stack '{sid}': {m['name']} starts at "
                                      f"{m['hi']:.1f} px, the previous segment ended at {pos:.1f} px")
                        pos = m["lo"]
                    drawn_total = baseline - pos
                total = sum(m["v"] for m in ms)
                declared_total = None
                for m in ms:
                    cur = m["el"]
                    while cur is not None and cur is not chart and declared_total is None:
                        if cur.attrs.get("data-stack") == sid or cur is m["el"]:
                            declared_total = num(cur.attrs.get("data-total"))
                        cur = cur.parent
                    if declared_total is not None:
                        break
                if declared_total is not None and abs(declared_total - total) > 1e-9 * max(1.0, abs(total)):
                    self.find(cid, fam, "stack-total", sid, f"stack '{sid}' declares total {declared_total:g} but "
                              f"its segments sum to {total:g}")
                if abs(drawn_total - k * total) > tol_for(k * total):
                    self.find(cid, fam, "stack-length", sid, f"stack '{sid}' draws {drawn_total:.1f} px for "
                              f"{total:g}; the shared scale puts {k * total:.1f} px")
        for el, v, c, precise in tks:
            expect = baseline + sign * k * v
            tol = POS_TOL if precise else TICK_TOL
            if abs(c - expect) > tol:
                self.find(cid, fam, "tick", f"tick {v:g}", f"tick '{v:g}' is drawn at {c:.1f} px but the bars' "
                          f"scale puts {v:g} at {expect:.1f} px — the axis is relabelled")
        return {"scale_px_per_unit": round(k, 6), "baseline_px": round(baseline, 3), "marks": len(marks),
                "inliers": fit["inliers"], "outliers": fit["outliers"], "mad": round(fit["mad"], 6)}

    def points_for(self, cid, fam, members, chart):
        """(key, xval, yval, cx, cy) for every data point; plus series records (for area)."""
        pts, series = [], []
        for i, el in enumerate(members):
            if self.scenery(el, chart):
                continue
            mname = self.mark_name(el, i)
            if el.tag == "circle" and ("data-x" in el.attrs or "data-y" in el.attrs):
                xv, yv = num(el.attrs.get("data-x")), num(el.attrs.get("data-y"))
                cx, cy = num(el.attrs.get("cx")), num(el.attrs.get("cy"))
                if None in (xv, yv, cx, cy):
                    self.find(cid, fam, "unbound-mark", mname, f"{mname} lacks a numeric data-x/data-y/cx/cy")
                    continue
                if not self.geom_ok(el, cid, fam, mname):
                    continue
                pts.append((mname, xv, yv, cx + el.dx, cy + el.dy, el))
            elif el.tag in ("polyline", "polygon", "path"):
                ys = num_list(el.attrs.get("data-values"))
                if ys is None:
                    if fam in ("line", "area"):
                        self.find(cid, fam, "unbound-mark", mname, f"{mname} is a drawn series with no data-values")
                    continue
                if not self.geom_ok(el, cid, fam, mname):
                    continue
                xs = num_list(el.attrs.get("data-xs")) or list(range(len(ys)))
                if len(xs) != len(ys):
                    self.find(cid, fam, "count", mname, f"{mname}: data-xs has {len(xs)} values, data-values {len(ys)}")
                    continue
                if el.tag == "path":
                    raw, _curved = path_points(el.attrs.get("d", ""))
                else:
                    raw = poly_points(el.attrs.get("points", ""))
                raw = [(px + el.dx, py + el.dy) for px, py in raw]
                n = len(ys)
                closing = raw[n:]
                if fam == "line" and len(raw) != n:
                    self.find(cid, fam, "count", mname, f"{mname} draws {len(raw)} points for {n} declared values")
                    continue
                if fam == "area" and len(raw) < n + 2:
                    self.find(cid, fam, "count", mname, f"{mname} draws {len(raw)} points; an area needs its {n} "
                              "value points plus at least two closing points on the baseline")
                    continue
                for j in range(n):
                    pts.append((f"{mname}[{j}]", xs[j], ys[j], raw[j][0], raw[j][1], el))
                series.append({"name": mname, "el": el, "closing": closing,
                               "baseline": num(el.attrs.get("data-baseline")) or 0.0})
            elif el.tag == "circle" and fam == "scatter":
                self.find(cid, fam, "unbound-mark", mname, f"{mname} is a drawn point with no data-x/data-y")
        return pts, series

    def check_positions(self, cid, fam, members, chart):
        pts, series = self.points_for(cid, fam, members, chart)
        if not pts:
            self.find(cid, fam, "no-data", cid, "no point declares its data: nothing here is verifiable (fail closed)")
            return {}
        info = {"points": len(pts)}
        for axis, vi, ci in (("x", 1, 3), ("y", 2, 4)):
            obs = [(p[0], p[vi], p[ci]) for p in pts]
            tks = self.ticks(members, axis)
            obs_fit = obs + [(f"tick {v:g}", v, c) for _el, v, c, precise in tks if precise]
            if len(obs_fit) < 3 or len({o[1] for o in obs_fit}) < 2:
                self.find(cid, fam, "unverifiable", cid, f"the {axis} axis has fewer than three points (or one "
                          "distinct value): its scale is defined by the marks instead of checking them")
                continue
            fit = robust_affine(obs_fit)
            if fit is None:
                self.find(cid, fam, "unverifiable", cid, f"the {axis} axis has no derivable scale")
                continue
            a, b = fit["a"], fit["b"]
            if a == 0:
                self.find(cid, fam, "unverifiable", cid, f"the {axis} axis maps distinct values to one position")
                continue
            for key, v, c in obs:
                exp = a * v + b
                if abs(c - exp) > POS_TOL:
                    self.find(cid, fam, "position", key, f"{key} declares {axis}={v:g} and is drawn at "
                              f"{c:.1f} px, but the shared {axis} scale fitted from its peers puts it at {exp:.1f} px "
                              f"(off by {abs(c - exp):.1f} px)")
            for el, v, c, precise in tks:
                exp = a * v + b
                if abs(c - exp) > (POS_TOL if precise else TICK_TOL):
                    self.find(cid, fam, "tick", f"tick {v:g}", f"{axis} tick '{v:g}' is drawn at {c:.1f} px but the "
                              f"data's scale puts it at {exp:.1f} px — the axis is relabelled")
            info[axis] = {"a": round(a, 6), "b": round(b, 4), "inliers": len(fit["inliers"]),
                          "outliers": fit["outliers"]}
            if axis == "y" and fam == "area":
                for s in series:
                    want = a * s["baseline"] + b
                    for (px, py) in s["closing"]:
                        if abs(py - want) > POS_TOL:
                            self.find(cid, fam, "area-baseline", s["name"], f"{s['name']} closes at y={py:.1f} px "
                                      f"but value {s['baseline']:g} maps to y={want:.1f} px — an area must close at its "
                                      "baseline or its filled height exaggerates every value")
                            break
        if fam == "scatter":
            sized = [(p[0], p[5]) for p in pts if num(p[5].attrs.get("data-size")) is not None]
            if sized:
                smp = []
                for key, el in sized:
                    s, r = num(el.attrs.get("data-size")), num(el.attrs.get("r"))
                    if s and s > 0 and r is not None:
                        smp.append((key, r * r / s, r, s))
                fit = robust_ratio([(k, q) for k, q, _, _ in smp])
                if fit:
                    K = fit["scale"]
                    for key, _q, r, s in smp:
                        exp = math.sqrt(K * s)
                        if abs(r - exp) > POS_TOL:
                            self.find(cid, fam, "area", key, f"{key} declares size {s:g} and draws r={r:g}, but the "
                                      f"shared area scale puts r={exp:.1f} (area, never radius, carries the value)")
        return info

    def check_pie(self, cid, fam, members, chart):
        slices = []
        for i, el in enumerate(members):
            if el.tag not in ("path", "circle") or self.scenery(el, chart):
                continue
            mname = self.mark_name(el, i)
            v = num(el.attrs.get("data-value"))
            if v is None:
                self.find(cid, fam, "unbound-mark", mname, f"{mname} is a drawn slice with no data-value")
                continue
            if v < 0:
                self.find(cid, fam, "negative", mname, f"{mname} declares {v:g}: a part of a whole cannot be negative")
                continue
            if not self.geom_ok(el, cid, fam, mname):
                continue
            arcs = arcs_of(el.attrs.get("d", "")) if el.tag == "path" else []
            if not arcs:
                slices.append({"name": mname, "v": v, "sweep": None, "el": el})
                continue
            outer = max(arcs, key=lambda q: q[2])
            g = arc_centre(outer[0] + el.dx, outer[1] + el.dy, outer[2], outer[5], outer[6],
                           outer[7] + el.dx, outer[8] + el.dy)
            if g is None:
                self.find(cid, fam, "unverifiable", mname, f"{mname}: its outer arc is degenerate")
                continue
            cx, cy, th1, dth, r = g
            slices.append({"name": mname, "v": v, "sweep": abs(math.degrees(dth)), "start": th1, "dth": dth,
                           "cx": cx, "cy": cy, "r": r, "el": el})
        if not slices:
            self.find(cid, fam, "no-data", cid, "no slice declares data-value: nothing here is verifiable")
            return {}, []
        total = sum(s["v"] for s in slices)
        if total <= 0:
            self.find(cid, fam, "unverifiable", cid, "the slices sum to zero")
            return {}, []
        shares = [100.0 * s["v"] / total for s in slices]
        drawn = [s for s in slices if s["sweep"] is not None]
        if len(slices) == 1 and not drawn:
            return {"slices": 1, "total": total}, shares
        dcx = cy0 = None
        if drawn:
            dcx = num(chart.attrs.get("data-cx"))
            cy0 = num(chart.attrs.get("data-cy"))
            if dcx is None or cy0 is None:
                dcx, cy0 = median([s["cx"] for s in drawn]), median([s["cy"] for s in drawn])
            rr = median([s["r"] for s in drawn])
            for s in drawn:
                if math.hypot(s["cx"] - dcx, s["cy"] - cy0) > CENTRE_TOL:
                    self.find(cid, fam, "centre", s["name"], f"{s['name']} is drawn around ({s['cx']:.1f}, "
                              f"{s['cy']:.1f}), not the shared centre ({dcx:.1f}, {cy0:.1f})")
                if abs(s["r"] - rr) > POS_TOL:
                    self.find(cid, fam, "radius", s["name"], f"{s['name']} has outer radius {s['r']:.1f} px, the "
                              f"others {rr:.1f} px — a longer slice reads as a bigger share")
        for s in slices:
            if s["sweep"] is None:
                self.find(cid, fam, "unverifiable", s["name"], f"{s['name']} has no arc to measure")
                continue
            exp = 360.0 * s["v"] / total
            if abs(s["sweep"] - exp) > ANGLE_TOL:
                self.find(cid, fam, "angle", s["name"], f"{s['name']} declares {s['v']:g} of {total:g} "
                          f"({100 * s['v'] / total:.1f} %) = {exp:.2f} deg but its arc sweeps {s['sweep']:.2f} deg")
        # contiguity in document order
        for prev, cur in zip(drawn, drawn[1:]):
            end = prev["start"] + prev["dth"]
            gap = math.degrees((cur["start"] - end + math.pi) % (2 * math.pi) - math.pi)
            if abs(gap) > ANGLE_TOL:
                self.find(cid, fam, "slice-gap", cur["name"], f"{cur['name']} starts {gap:+.2f} deg from where "
                          f"{prev['name']} ends: slices must meet")
        tot_sweep = sum(s["sweep"] for s in drawn)
        if len(drawn) == len(slices) and abs(tot_sweep - 360.0) > ANGLE_TOL * max(1, len(drawn) / 2):
            self.find(cid, fam, "whole", cid, f"the slices sweep {tot_sweep:.2f} deg in total, not 360")
        return {"slices": len(slices), "total": total, "centre": [round(dcx or 0, 2), round(cy0 or 0, 2)]}, shares

    # -- driver -------------------------------------------------------------------------
    def run(self, source: str):
        tree = Tree(source)
        charts = [el for el in tree.root.iter() if "data-chart" in el.attrs]
        for ci, chart in enumerate(charts):
            raw = chart.attrs.get("data-chart", "").strip()
            cid = chart.attrs.get("data-chart-id") or chart.attrs.get("id") or f"chart-{ci + 1}"
            invented = raw.lower().startswith("invented")
            fam = FAMILY_ALIASES.get(raw.lower()) if not invented else None
            members = self.chart_members(chart)
            info, shares = {}, []
            if fam in ("bar", "column", "stacked-bar", "stacked-column"):
                info = self.check_lengths(cid, fam, members, chart)
            elif fam in ("line", "scatter", "area"):
                info = self.check_positions(cid, fam, members, chart)
            elif fam in ("pie", "donut"):
                info, shares = self.check_pie(cid, fam, members, chart)
            else:
                self.notes.append(f"{self.name}:{cid}: family '{raw or '(empty)'}' has no geometry checker; its printed "
                                  "numbers are still checked against its data (an unknown form is never failed for "
                                  "being unknown)")
            fam_out = fam or (raw or "(empty)")
            bound, unbound = self.check_printed(chart, cid, fam_out, members, shares)
            self.charts.append({"file": self.name, "chart": cid, "family": fam_out, "invented": invented or fam is None,
                                "printed_numbers_bound": bound, "printed_numbers_unbound": unbound,
                                "fit": info, "findings": sum(1 for f in self.findings if f["chart"] == cid
                                                             and f["file"] == self.name)})
        return self


def check_source(source: str, name: str = "<input>") -> Checker:
    return Checker(name).run(source)


def gather(paths):
    files = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            files.extend(sorted(q for q in p.rglob("*") if q.suffix.lower() in (".svg", ".html", ".htm")))
        else:
            files.append(p)
    return files


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Chart honesty: drawn geometry vs declared data (B5).")
    ap.add_argument("inputs", nargs="*")
    ap.add_argument("--json", dest="json_out")
    a = ap.parse_args(argv)
    files = gather(a.inputs)
    if not files:
        emit({"gate": GATE, "status": "SKIPPED", "summary": "no input files"})
        return EXIT_SKIPPED
    findings, notes, charts = [], [], []
    for f in files:
        try:
            src = f.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as e:
            emit({"gate": GATE, "status": "ERROR", "summary": f"cannot read {f}: {e}"})
            return EXIT_ERROR
        c = check_source(src, str(f))
        findings += c.findings
        notes += c.notes
        charts += c.charts
    if not charts:
        res = {"gate": GATE, "status": "SKIPPED", "summary": "no element declares data-chart", "files": len(files),
               "charts": [], "findings": [], "notes": notes}
        code = EXIT_SKIPPED
    elif findings:
        kinds = sorted({f["kind"] for f in findings})
        res = {"gate": GATE, "status": "REVIEW", "summary": f"{len(findings)} finding(s) in {len(charts)} chart(s): "
               + ", ".join(kinds), "files": len(files), "charts": charts, "findings": findings, "notes": notes}
        code = EXIT_REVIEW
    else:
        res = {"gate": GATE, "status": "PASS", "summary": f"{len(charts)} chart(s): every mark matches its data on a "
               "robustly fitted scale and every printed number is bound", "files": len(files), "charts": charts,
               "findings": [], "notes": notes,
               "measures": "drawn geometry vs declared data-* values and printed numbers vs data; it does not judge "
                           "whether the chart type suits the data"}
        code = EXIT_PASS
    if a.json_out:
        Path(a.json_out).write_text(json.dumps(res, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    emit(res)
    return code


if __name__ == "__main__":
    sys.exit(main())
