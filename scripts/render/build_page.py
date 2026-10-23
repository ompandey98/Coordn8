#!/usr/bin/env python3
"""Coordn8 — build the single-file interactive page from a content JSON and the active kit (C1/C2/A3).

    python3 scripts/render/build_page.py <content.json> --design-system <design_system.json | kit.json> \
        --out <slug>.html [--plan-out page_plan.json] [--claims-out claims.json] [--layout-js PATH]

What it does
  * reads templates/page/{shell.html, page.css, page.js, components/*.html} and inlines all of it;
  * reads the ACTIVE kit (design_system.json `kit_spec` + `palette`, a design_system.json that only
    names a kit id, or a bare kit JSON) and inlines its `--c8-*` custom properties via
    design/kit_export.py — switching kits is a data change, never a template edit;
  * embeds a vendored font as base64 woff2 only when the kit lists it with an OFL licence AND the
    file is present; otherwise the kit's system font stacks are used;
  * renders every section (id / title / one-line summary / What / Why / How folded in <details> /
    components / sanitised custom_html), then marks the first use of each glossary term with an
    accessible tooltip (button + aria-describedby -> role=tooltip);
  * ENDS every page with the claims ledger (C3): claim -> path:line -> read | inferred. Claims given
    at the top level and claims given inside sections are listed together there; a page with no
    claims still ends with the ledger, which says so (and the build warns). `--claims-out` writes
    exactly the rows of that final ledger, so scripts/gates/check_claims.py can prove they match;
  * optional understanding checks (C6): `predict` (a pause before a reveal), `assumption_toggle`
    (change an assumption, see what breaks) and `self_check` (questions with explained answers).
    Their CSS and script are inlined only on a page that uses them; each works from the keyboard
    and the page reads completely without script;
  * refuses to write a page that would reach the network: any absolute or protocol-relative URL in
    content, custom HTML or a figure is removed (and reported); the finished file is re-scanned and
    the build fails if one survived.

The content schema is documented in templates/page/README.md. Prints one JSON summary line on
stdout. Exit 0 = built, 3 = the content, kit or template is unusable (nothing written).
Python 3.9, standard library only.
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
PKG = SCRIPTS.parent
TEMPLATE_DIR = PKG / "templates" / "page"
KIT_DIR = PKG / "data" / "style_kits"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from design.kit_export import _active, _derived_vars, _role_vars, to_css_vars  # noqa: E402
from design.pick_kit import skins, validate_kit  # noqa: E402

EXIT_OK = 0
EXIT_ERROR = 3
DEFAULT_SPEEDS = (0.75, 1.0, 1.25, 1.5, 2.0)
SOURCE_ROLES = ("deterministic", "model-judgement", "data-field", "vision")


class BuildError(Exception):
    pass


# ============================================================== templates
_PART_RE = re.compile(r"<!--\s*c8:part\s+([\w-]+)\s*-->\n?")
_SLOT_RE = re.compile(r"\{\{\{\s*([\w.-]+)\s*\}\}\}|\{\{\s*([\w.-]+)\s*\}\}")
_PART_CACHE: dict = {}


def load_parts(name: str, template_dir: Path = TEMPLATE_DIR) -> dict:
    key = (str(template_dir), name)
    if key in _PART_CACHE:
        return _PART_CACHE[key]
    p = template_dir / "components" / f"{name}.html"
    if not p.exists():
        raise BuildError(f"component template missing: {p}")
    text = p.read_text(encoding="utf-8")
    bits = _PART_RE.split(text)
    parts = {}
    if len(bits) == 1:
        parts["main"] = text
    for i in range(1, len(bits), 2):
        parts[bits[i]] = bits[i + 1]
    _PART_CACHE[key] = parts
    return parts


def fill(tpl: str, values: dict) -> str:
    """`{{name}}` is HTML-escaped, `{{{name}}}` is inserted as-is (callers pass only built or
    sanitised markup there). A slot with no value renders empty."""
    def sub(m):
        raw, esc = m.group(1), m.group(2)
        if raw is not None:
            v = values.get(raw, "")
            return "" if v is None else str(v)
        v = values.get(esc, "")
        return html.escape("" if v is None else str(v), quote=True)
    return _SLOT_RE.sub(sub, tpl)


def part(component: str, name: str, values: dict) -> str:
    parts = load_parts(component)
    if name not in parts:
        raise BuildError(f"component {component}.html has no part {name!r}")
    return fill(parts[name], values)


# ============================================================== URL + markup safety
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
_DATA_IMG_RE = re.compile(r"^data:image/(?:png|jpe?g|gif|webp);", re.I)
URL_ATTRS = {"href", "src", "xlink:href", "poster", "cite", "background", "longdesc", "data",
             "action", "formaction", "ping", "manifest", "codebase", "srcset", "lowsrc", "dynsrc"}
# anything that would make the browser fetch from elsewhere: fatal in a built page
LOAD_RE = re.compile(r"""(?:\b(?:src|href|poster|action|srcset|data)\s*=\s*["']?\s*(?:[a-z][a-z0-9+.-]*:)?//)"""
                     r"""|url\(\s*['"]?\s*(?:[a-z][a-z0-9+.-]*:)?//|@import\b""", re.I)
# a URL merely written in the text: allowed (it loads nothing) but reported, so the author can
# decide whether a reader needs it spelled out
TEXT_URL_RE = re.compile(r"\b(?:https?|ftp)://[^\s<>\"']+", re.I)


def safe_url(value: str, allow_data_image: bool = False) -> str | None:
    """A URL that cannot reach the network or run script: an in-page anchor or a relative path.
    Returns None for anything else (absolute, protocol-relative, javascript:, data: …)."""
    v = "".join(ch for ch in str(value) if ch >= " " and ch != "\x7f").strip()
    if not v:
        return None
    if v.startswith("#"):
        return v
    if v.startswith("//") or v.startswith("\\\\"):
        return None
    if _SCHEME_RE.match(v):
        if allow_data_image and _DATA_IMG_RE.match(v):
            return v
        return None
    return v


_CSS_IMPORT = re.compile(r"@import[^;]*;?", re.I)
_CSS_URL = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.I | re.S)
_CSS_BAD = re.compile(r"expression\s*\(|javascript:|behavior\s*:|-moz-binding", re.I)


def safe_css(css: str) -> str:
    css = _CSS_IMPORT.sub("", css)

    def url_sub(m):
        target = m.group(2).strip()
        if target.startswith("#") or _DATA_IMG_RE.match(target):
            return m.group(0)
        return "none"
    css = _CSS_URL.sub(url_sub, css)
    css = _CSS_BAD.sub("", css)
    return css.replace("</", "<\\/")


HTML_TAGS = {
    "a", "abbr", "article", "aside", "b", "bdi", "bdo", "blockquote", "br", "button", "caption",
    "cite", "code", "col", "colgroup", "data", "dd", "del", "details", "dfn", "div", "dl", "dt",
    "em", "figcaption", "figure", "footer", "h2", "h3", "h4", "h5", "h6", "header", "hr", "i", "img",
    "input", "ins", "kbd", "label", "li", "mark", "meter", "ol", "output", "p", "picture", "pre",
    "progress", "q", "rp", "rt", "ruby", "s", "samp", "section", "small", "span", "strong", "style",
    "sub", "summary", "sup", "table", "tbody", "td", "tfoot", "th", "thead", "time", "tr", "u", "ul",
    "var", "wbr", "fieldset", "legend",
}
SVG_TAGS = {
    "svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "tspan",
    "textpath", "title", "desc", "defs", "marker", "lineargradient", "radialgradient", "stop",
    "clippath", "mask", "pattern", "symbol", "use", "image", "filter", "fegaussianblur", "feoffset",
    "feblend", "feflood", "fecomposite", "femerge", "femergenode", "fecolormatrix", "fedropshadow",
}
DROP_WITH_CONTENT = {"script", "iframe", "object", "embed", "template", "noscript", "frame", "frameset",
                     "applet", "foreignobject", "math", "audio", "video", "source", "track", "form",
                     "select", "textarea", "head", "title_html"}
DROP_TAG_ONLY = {"html", "body", "link", "meta", "base", "font", "center"}
VOID_TAGS = {"br", "col", "hr", "img", "input", "wbr", "area", "source", "track"}
SVG_CASE = {n.lower(): n for n in (
    "viewBox preserveAspectRatio markerWidth markerHeight markerUnits refX refY gradientUnits "
    "gradientTransform patternUnits patternContentUnits patternTransform clipPathUnits maskUnits "
    "maskContentUnits textLength lengthAdjust startOffset stdDeviation spreadMethod "
    "linearGradient radialGradient clipPath textPath foreignObject feGaussianBlur feOffset feBlend "
    "feFlood feComposite feMerge feMergeNode feColorMatrix feDropShadow floodColor floodOpacity "
    "baseFrequency numOctaves kernelMatrix pathLength").split()}
INPUT_TYPES = {"checkbox", "radio", "range"}


class Sanitizer(HTMLParser):
    """Allow-list rebuild of untrusted markup (custom sections, figure SVG). Everything that could
    run script or load a resource from elsewhere is dropped and recorded in `removed`."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list = []
        self.stack: list = []
        self.skip: list = []
        self.removed: list = []
        self.in_style = False

    # -- helpers
    def _note(self, what: str):
        if len(self.removed) < 200:
            self.removed.append(what)

    def _attrs(self, tag: str, attrs) -> str:
        bits = []
        for name, value in attrs:
            n = (name or "").lower()
            v = "" if value is None else value
            if n.startswith("on"):
                self._note(f"<{tag}> event handler {n}")
                continue
            if n.startswith("xmlns") or n in ("srcdoc", "formaction", "action", "is", "autofocus"):
                if not n.startswith("xmlns"):
                    self._note(f"<{tag}> attribute {n}")
                continue
            if n == "xlink:href":
                n = "href"
            if n in URL_ATTRS:
                if n == "srcset":
                    self._note(f"<{tag}> srcset")
                    continue
                good = safe_url(v, allow_data_image=(n in ("src", "href") and tag in ("img", "image")))
                if good is None:
                    self._note(f"<{tag}> {n}={v[:80]!r}")
                    continue
                if tag == "use" and not good.startswith("#"):
                    self._note(f"<use> href={v[:80]!r}")
                    continue
                v = good
            if n == "style":
                v = safe_css(v)
            if tag == "input" and n == "type" and v.lower() not in INPUT_TYPES:
                self._note(f"<input type={v!r}>")
                v = "checkbox"
            n = SVG_CASE.get(n, n)
            bits.append(f' {n}="{html.escape(v, quote=True)}"')
        return "".join(bits)

    def _tag_name(self, tag: str) -> str:
        return SVG_CASE.get(tag, tag)

    # -- parser callbacks
    def handle_starttag(self, tag, attrs):
        self._start(tag, attrs, closed=False)

    def handle_startendtag(self, tag, attrs):
        self._start(tag, attrs, closed=True)

    def _start(self, tag, attrs, closed):
        tag = tag.lower()
        if self.skip:
            if tag in DROP_WITH_CONTENT and not closed and tag not in VOID_TAGS:
                self.skip.append(tag)
            return
        if tag == "h1":
            tag = "h2"
        if tag in DROP_WITH_CONTENT:
            self._note(f"<{tag}> removed with its content")
            if not closed and tag not in VOID_TAGS:
                self.skip.append(tag)
            return
        if tag in DROP_TAG_ONLY or (tag not in HTML_TAGS and tag not in SVG_TAGS):
            self._note(f"<{tag}> tag removed")
            return
        a = self._attrs(tag, attrs)
        name = self._tag_name(tag)
        if closed and tag not in VOID_TAGS:
            self.out.append(f"<{name}{a}/>" if tag in SVG_TAGS else f"<{name}{a}></{name}>")
            return
        self.out.append(f"<{name}{a}>")
        if tag not in VOID_TAGS:
            self.stack.append(tag)
            if tag == "style":
                self.in_style = True

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "h1":
            tag = "h2"
        if self.skip:
            if tag == self.skip[-1]:
                self.skip.pop()
            return
        if tag not in self.stack:
            return
        while self.stack:
            t = self.stack.pop()
            self.out.append(f"</{self._tag_name(t)}>")
            if t == "style":
                self.in_style = False
            if t == tag:
                break

    def handle_data(self, data):
        if self.skip:
            return
        if self.in_style:
            self.out.append(safe_css(data))
        else:
            self.out.append(html.escape(data, quote=False))

    def handle_comment(self, data):
        return

    def handle_decl(self, decl):
        return

    def handle_pi(self, data):
        return

    def unknown_decl(self, data):
        return

    def result(self) -> str:
        self.close()
        while self.stack:
            self.out.append(f"</{self._tag_name(self.stack.pop())}>")
        return "".join(self.out)


def sanitize(markup: str) -> tuple:
    s = Sanitizer()
    s.feed(str(markup))
    return s.result(), list(s.removed)


# ============================================================== rich text
def slugify(s: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")
    return out or "section"


class Ctx:
    def __init__(self, content: dict, content_dir: Path, layout_js: Path | None):
        self.content = content
        self.content_dir = content_dir
        self.layout_js = layout_js
        self.warnings: list = []
        self.removed: list = []
        self.ids: set = set()
        self.link_base = None
        base = content.get("source_link_base")
        if base:
            good = safe_url(base)
            if good is None:
                self.warn(f"source_link_base {base!r} is not a relative path — code chips will not link")
            else:
                self.link_base = good
        self.layout_modes: list = []
        self.plan_sections: list = []
        self.plan_reference: list = []
        self.widgets: list = []      # component templates whose css/script parts this page needs

    def use_widget(self, name: str):
        if name not in self.widgets:
            self.widgets.append(name)

    def warn(self, msg: str):
        self.warnings.append(msg)

    def unique(self, base: str) -> str:
        cand = slugify(base)
        n = 2
        out = cand
        while out in self.ids:
            out = f"{cand}-{n}"
            n += 1
        self.ids.add(out)
        return out


_REF_RE = re.compile(r"^(?P<path>[^\s:]+(?:/[^\s:]+)*)(?::(?P<line>\d+)(?:-(?P<end>\d+))?)?$")


def chip(ref: str, ctx: Ctx, href: str | None = None) -> str:
    ref = str(ref).strip()
    link = None
    if href:
        link = safe_url(href)
        if link is None:
            ctx.warn(f"code chip {ref!r}: href {href!r} is not a relative path — shown without a link")
    elif ctx.link_base:
        m = _REF_RE.match(ref)
        if m:
            link = ctx.link_base + m.group("path") + (f"#L{m.group('line')}" if m.group("line") else "")
    if link:
        return part("chip", "link", {"href": link, "ref": ref}).strip()
    return part("chip", "plain", {"ref": ref}).strip()


_CODE_SPAN = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_CHIP_MARK = re.compile(r"\[\[([^\]]+)\]\]")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def rich_inline(text, ctx: Ctx) -> str:
    """Escaped text with a tiny inline markup: `code`, **strong**, [[path:line]] chips,
    [label](#anchor-or-relative-path) links. Nothing else is interpreted."""
    if text is None:
        return ""
    if isinstance(text, dict) and "html" in text:
        clean, removed = sanitize(text["html"])
        ctx.removed += removed
        return clean
    s = str(text)
    out = []
    for i, seg in enumerate(_CODE_SPAN.split(s)):
        if i % 2 == 1:
            out.append(f"<code>{html.escape(seg, quote=False)}</code>")
            continue
        chips: list = []

        def keep_chip(m):
            chips.append(m.group(1))
            return f"\x00{len(chips) - 1}\x00"
        seg = _CHIP_MARK.sub(keep_chip, seg)
        links: list = []

        def keep_link(m):
            links.append((m.group(1), m.group(2)))
            return f"\x01{len(links) - 1}\x01"
        seg = _LINK.sub(keep_link, seg)
        seg = html.escape(seg, quote=False)
        seg = _BOLD.sub(lambda m: f"<strong>{m.group(1)}</strong>", seg)

        def put_link(m):
            label, target = links[int(m.group(1))]
            good = safe_url(target)
            if good is None:
                ctx.removed.append(f"link to {target[:80]!r}")
                return html.escape(label, quote=False)
            return f'<a href="{html.escape(good, quote=True)}">{html.escape(label, quote=False)}</a>'
        seg = re.sub("\x01(\\d+)\x01", put_link, seg)
        seg = re.sub("\x00(\\d+)\x00", lambda m: chip(chips[int(m.group(1))], ctx), seg)
        out.append(seg)
    return "".join(out)


def rich_block(value, ctx: Ctx) -> str:
    """Paragraphs (blank-line separated), '- ' bullet lists, a list of such strings, or
    {"html": "..."} (sanitised)."""
    if value is None or value == "":
        return ""
    if isinstance(value, dict) and "html" in value:
        clean, removed = sanitize(value["html"])
        ctx.removed += removed
        return clean
    if isinstance(value, (list, tuple)):
        return "\n".join(rich_block(v, ctx) for v in value)
    out = []
    for para in re.split(r"\n\s*\n", str(value).strip()):
        lines = [ln for ln in para.splitlines() if ln.strip()]
        if lines and all(ln.lstrip().startswith("- ") for ln in lines):
            items = "".join(f"<li>{rich_inline(ln.lstrip()[2:], ctx)}</li>" for ln in lines)
            out.append(f"<ul>{items}</ul>")
        else:
            out.append(f"<p>{rich_inline(' '.join(ln.strip() for ln in lines), ctx)}</p>")
    return "\n".join(out)


# ============================================================== components
def _graph_outline(graph: dict) -> str:
    nodes = {str(n.get("id")): n for n in graph.get("nodes") or [] if n.get("id") is not None}
    edges = [e for e in graph.get("edges") or [] if str(e.get("from")) in nodes and str(e.get("to")) in nodes]
    kids: dict = {}
    has_parent = set()
    for e in edges:
        kids.setdefault(str(e["from"]), []).append(e)
        has_parent.add(str(e["to"]))
    roots = [nid for nid in nodes if nid not in has_parent] or list(nodes)[:1]

    def label(nid):
        n = nodes[nid]
        txt = html.escape(str(n.get("label") or nid), quote=False)
        if n.get("ref"):
            txt += f' <code>{html.escape(str(n["ref"]), quote=False)}</code>'
        return txt

    def walk(nid, seen):
        if nid in seen:
            return f"{label(nid)} (see above)"
        seen = seen | {nid}
        sub = "".join(
            f'<li><span class="c8-when">when {html.escape(str(e.get("label") or "next"), quote=False)}</span> &rarr; '
            f'{walk(str(e["to"]), seen)}</li>' for e in kids.get(nid, []))
        return f"{label(nid)}{'<ul>' + sub + '</ul>' if sub else ''}"

    return '<ul class="c8-outline">' + "".join(f"<li>{walk(r, set())}</li>" for r in roots) + "</ul>"


def run_layout(graph: dict, ctx: Ctx) -> str | None:
    """Lay a graph out with scripts/render/layout.js when it and node are present. Tries the
    calling conventions in order and keeps the first that prints an <svg>."""
    lj = ctx.layout_js
    node = shutil.which("node")
    if not lj or not Path(lj).exists() or not node:
        return None
    with tempfile.TemporaryDirectory(prefix="c8page-") as td:
        gin = Path(td) / "graph.json"
        gout = Path(td) / "graph.svg"
        gin.write_text(json.dumps(graph), encoding="utf-8")
        attempts = ([node, str(lj), str(gin)], [node, str(lj), "--in", str(gin), "--out", str(gout)],
                    [node, str(lj)])
        for i, cmd in enumerate(attempts):
            try:
                cp = subprocess.run(cmd, capture_output=True, text=True, timeout=90,
                                    input=json.dumps(graph) if i == 2 else None)
            except (OSError, subprocess.TimeoutExpired):
                continue
            text = cp.stdout or ""
            if gout.exists():
                text = gout.read_text(encoding="utf-8")
            if cp.returncode == 0 and "<svg" in text:
                return text[text.index("<svg"):]
    ctx.warn("layout.js is present but produced no <svg> for a graph; the inline SVG (or the list) is used")
    return None


def _label_svg(svg: str, title: str) -> str:
    m = re.search(r"<svg\b[^>]*>", svg)
    if not m:
        return svg
    tag = m.group(0)
    new = tag
    if "role=" not in tag:
        new = new[:-1] + ' role="img"' + ">"
    if "aria-label" not in tag and "aria-labelledby" not in tag and title:
        new = new[:-1] + f' aria-label="{html.escape(title, quote=True)}"' + ">"
    if "focusable=" not in tag:
        new = new[:-1] + ' focusable="false"' + ">"
    return svg[:m.start()] + new + svg[m.end():]


def render_flow(comp: dict, ctx: Ctx, sec_id: str) -> str:
    fid = ctx.unique(comp.get("id") or f"{sec_id}-flow")
    title = str(comp.get("title") or "Decision flow")
    svg = None
    mode = None
    graph = comp.get("graph")
    if isinstance(graph, dict):
        svg = run_layout(graph, ctx)
        mode = "layout.js" if svg else None
    if svg is None:
        raw = comp.get("svg")
        if not raw and comp.get("svg_file"):
            p = (ctx.content_dir / str(comp["svg_file"])).resolve()
            if p.exists():
                raw = p.read_text(encoding="utf-8")
            else:
                ctx.warn(f"flow {fid}: svg_file {comp['svg_file']!r} not found")
        if raw:
            svg = raw
            mode = "inline-svg"
    if svg is not None:
        clean, removed = sanitize(svg)
        ctx.removed += [f"flow {fid}: {r}" for r in removed]
        svg = _label_svg(clean, title)
    outline = ""
    if isinstance(graph, dict):
        outline_list = _graph_outline(graph)
        if svg is None:
            mode = "outline"
            svg = outline_list
            ctx.warn(f"flow {fid}: no layout.js output and no inline SVG — drawn as an indented list")
        else:
            outline = part("flow", "outline", {"list": outline_list})
    if svg is None:
        raise BuildError(f"flow {fid} has neither an svg, an svg_file nor a graph")
    ctx.layout_modes.append({"flow": fid, "mode": mode})
    legend = ""
    if comp.get("legend", True):
        lg = comp.get("legend_labels") or {}
        legend = part("flow", "legend", {
            "deterministic": lg.get("deterministic", "Fixed rule in the code"),
            "model": lg.get("model-judgement", "A model's judgement"),
            "data": lg.get("data-field", "Read straight from a data field"),
            "vision": lg.get("vision", "Read from an image")})
    return part("flow", "figure", {"id": fid, "title": title, "svg": svg, "outline": outline,
                                   "caption": rich_inline(comp.get("caption") or "", ctx), "legend": legend})


def render_stepper(comp: dict, ctx: Ctx, sec_id: str) -> str:
    sid = ctx.unique(comp.get("id") or f"{sec_id}-example")
    steps = comp.get("steps") or []
    if not steps:
        raise BuildError(f"stepper {sid} has no steps")
    body = []
    for st in steps:
        hl = st.get("highlight") or []
        if isinstance(hl, str):
            hl = hl.split()
        body.append(part("stepper", "step", {"title": st.get("title") or "", "body": rich_block(st.get("body"), ctx),
                                             "highlight": " ".join(str(h) for h in hl)}))
    return part("stepper", "stepper", {"id": sid, "title": comp.get("title") or "Walk an example",
                                       "steps": "".join(body), "count": len(steps)})


def _cell(value, ctx: Ctx) -> str:
    if isinstance(value, dict):
        bits = []
        if value.get("text") is not None:
            bits.append(rich_inline(value["text"], ctx))
        if value.get("ref"):
            bits.append(chip(value["ref"], ctx, value.get("href")))
        if value.get("html"):
            bits.append(rich_inline({"html": value["html"]}, ctx))
        return " ".join(bits)
    return rich_inline(value, ctx)


def render_table(comp: dict, ctx: Ctx, sec_id: str) -> str:
    tid = ctx.unique(comp.get("id") or f"{sec_id}-{comp.get('type', 'table')}")
    cols = [str(c) for c in comp.get("columns") or []]
    rows = comp.get("rows") or []
    if not cols or not rows:
        raise BuildError(f"table {tid} needs columns and rows")
    row_header = bool(comp.get("row_header", True))
    head = "".join(f'<th scope="col">{html.escape(c, quote=False)}</th>' for c in cols)
    out = []
    for r in rows:
        if isinstance(r, dict):
            r = [r.get(c, "") for c in cols]
        cells = []
        for i, v in enumerate(r):
            lab = html.escape(cols[i] if i < len(cols) else "", quote=True)
            if i == 0 and row_header:
                cells.append(f'<th scope="row" data-label="{lab}">{_cell(v, ctx)}</th>')
            else:
                cells.append(f'<td data-label="{lab}">{_cell(v, ctx)}</td>')
        out.append("      <tr>" + "".join(cells) + "</tr>")
    after = rich_block(comp.get("after"), ctx) if comp.get("after") else ""
    return part("table", "table", {"id": tid, "kind": comp.get("type", "table"),
                                   "caption": comp.get("caption") or "", "head": head,
                                   "rows": "\n".join(out), "after": after})


def normalise_claims(rows: list) -> tuple:
    good, problems = [], []
    for i, c in enumerate(rows or [], 1):
        if not isinstance(c, dict):
            problems.append(f"claim #{i} is not an object")
            continue
        text = str(c.get("claim") or c.get("text") or "").strip()
        status = str(c.get("status") or c.get("basis") or "").strip().lower()
        ref = str(c.get("ref") or "").strip()
        if not text:
            problems.append(f"claim #{i} has no text")
        if status not in ("read", "inferred"):
            problems.append(f"claim #{i} ({text[:50]!r}) is marked neither read nor inferred")
        elif status == "read" and not ref:
            problems.append(f"claim #{i} ({text[:50]!r}) is marked read but points to no path:line")
        good.append({"id": str(c.get("id") or f"c{i}"), "text": text, "ref": ref, "status": status,
                     "href": c.get("href")})
    return good, problems


def render_claims(rows: list, ctx: Ctx, cid: str, caption: str) -> tuple:
    claims, problems = normalise_claims(rows)
    if problems:
        raise BuildError("claims ledger: " + "; ".join(problems))
    body = []
    for c in claims:
        where = chip(c["ref"], ctx, c.get("href")) if c["ref"] else '<span class="c8-when">no single line</span>'
        body.append(part("claims", "row", {
            "cid": c["id"], "status": c["status"], "ref": c["ref"], "claim": rich_inline(c["text"], ctx),
            "where": where, "status_label": "read in the code" if c["status"] == "read" else "inferred"}))
    n_read = sum(1 for c in claims if c["status"] == "read")
    n_inf = len(claims) - n_read
    tally = (f"{len(claims)} claim{'s' if len(claims) != 1 else ''}: {n_read} read in the code, "
             f"{n_inf} inferred.")
    return part("claims", "ledger", {"id": cid, "caption": caption, "rows": "".join(body), "tally": tally}), claims


def render_callout(comp: dict, ctx: Ctx, sec_id: str) -> str:
    kind = str(comp.get("kind") or ("warning" if comp.get("type") == "warning" else "note")).lower()
    if kind not in ("note", "warning"):
        kind = "note"
    label = str(comp.get("title") or ("Watch out" if kind == "warning" else "Good to know"))
    return part("callout", "callout", {"kind": kind, "label": label, "body": rich_block(comp.get("body"), ctx)})


def render_code(comp: dict, ctx: Ctx, sec_id: str) -> str:
    ref = comp.get("ref")
    code = html.escape(str(comp.get("code") or ""), quote=False)
    cap = chip(ref, ctx, comp.get("href")) if ref else ""
    extra = rich_inline(comp.get("caption"), ctx) if comp.get("caption") else ""
    return (f'<figure class="c8-code" data-c8-code><pre><code>{code}</code></pre>'
            f'<figcaption>{cap} {extra}</figcaption></figure>')


def render_chip(comp: dict, ctx: Ctx, sec_id: str) -> str:
    return f"<p>{chip(comp.get('ref', ''), ctx, comp.get('href'))}</p>"


def render_custom(comp: dict, ctx: Ctx, sec_id: str) -> str:
    clean, removed = sanitize(comp.get("html") or comp.get("custom_html") or "")
    ctx.removed += [f"custom ({sec_id}): {r}" for r in removed]
    return part("custom", "custom", {"name": comp.get("name") or "custom", "html": clean})


def _labels(comp: dict, key: str, default: str) -> str:
    v = comp.get(key)
    return str(v).strip() if isinstance(v, (str, int, float)) and str(v).strip() else default


def render_predict(comp: dict, ctx: Ctx, sec_id: str) -> str:
    """C6: 'predict what happens next' — the question, then the answer behind a real button.
    Without script the answer is simply shown (the button only appears when it can act)."""
    pid = ctx.unique(comp.get("id") or f"{sec_id}-predict")
    prompt = comp.get("prompt") or comp.get("question")
    reveal = comp.get("reveal") or comp.get("answer")
    if not prompt or not reveal:
        raise BuildError(f"predict {pid} needs a prompt and a reveal")
    ctx.use_widget("predict")
    return part("predict", "predict", {
        "id": pid, "prompt": rich_inline(prompt, ctx), "hint": rich_inline(comp.get("hint") or "", ctx),
        "reveal": rich_block(reveal, ctx),
        "label_open": _labels(comp, "label_open", "Show what happens"),
        "label_close": _labels(comp, "label_close", "Hide what happens")})


def render_assumption(comp: dict, ctx: Ctx, sec_id: str) -> str:
    """C6: 'change an assumption and see what breaks' — a role=switch flips between the state
    while the assumption holds and what breaks when it does not; named flow nodes in the same
    section are marked while it is switched. Without script both states are shown, labelled."""
    aid = ctx.unique(comp.get("id") or f"{sec_id}-assumption")
    assumption = comp.get("assumption")
    holds = comp.get("holds")
    breaks = comp.get("breaks")
    if not assumption or not holds or not breaks:
        raise BuildError(f"assumption_toggle {aid} needs an assumption, what happens while it holds "
                         f"(holds) and what breaks when it does not (breaks)")
    hl = comp.get("highlight") or []
    if isinstance(hl, str):
        hl = hl.split()
    ctx.use_widget("assumption_toggle")
    return part("assumption_toggle", "toggle", {
        "id": aid, "assumption": rich_inline(assumption, ctx), "holds": rich_block(holds, ctx),
        "breaks": rich_block(breaks, ctx), "highlight": " ".join(str(h) for h in hl),
        "switch_label": _labels(comp, "switch_label", "Change this assumption"),
        "holds_label": _labels(comp, "holds_label", "While it holds"),
        "breaks_label": _labels(comp, "breaks_label", "When it does not hold, this breaks")})


def render_self_check(comp: dict, ctx: Ctx, sec_id: str) -> str:
    """C6: a short self-check (three questions by default). Every question must carry its
    explanation; the answer and why also sit in a native <details>, so it works without script."""
    cid = ctx.unique(comp.get("id") or f"{sec_id}-self-check")
    qs = comp.get("questions") or []
    if not isinstance(qs, list) or not qs:
        raise BuildError(f"self_check {cid} has no questions")
    if len(qs) != 3:
        ctx.warn(f"self_check {cid} has {len(qs)} questions; three is the default (kept as given)")
    body = []
    for qi, q in enumerate(qs, 1):
        if not isinstance(q, dict):
            raise BuildError(f"self_check {cid}: question {qi} is not an object")
        text = q.get("q") or q.get("question")
        opts = q.get("options") or []
        explain = q.get("explain") or q.get("why")
        if not text or not isinstance(opts, list) or len(opts) < 2:
            raise BuildError(f"self_check {cid}: question {qi} needs a question and at least two options")
        if not explain:
            raise BuildError(f"self_check {cid}: question {qi} has no explanation (an answer without its "
                             f"reason teaches nothing)")
        ans = q.get("answer")
        if isinstance(ans, bool) or not isinstance(ans, int):
            ans = next((i for i, o in enumerate(opts) if str(o).strip() == str(ans).strip()), None)
        if ans is None or not (0 <= ans < len(opts)):
            raise BuildError(f"self_check {cid}: question {qi} names no valid answer (an option index or "
                             f"the option text)")
        qid = f"{cid}-q{qi}"
        options = "".join(part("self_check", "option", {"index": i, "text": rich_inline(o, ctx)})
                          for i, o in enumerate(opts))
        body.append(part("self_check", "question", {
            "qid": qid, "q": rich_inline(text, ctx), "answer": ans, "options": options,
            "answer_text": rich_inline(opts[ans], ctx), "explain": rich_block(explain, ctx)}))
    ctx.use_widget("self_check")
    n = len(qs)
    title = _labels(comp, "title", ("Three questions to check your understanding" if n == 3 else
                                    f"{n} question{'s' if n != 1 else ''} to check your understanding"))
    return part("self_check", "check", {"id": cid, "title": title, "questions": "".join(body), "count": n})


RENDERERS = {
    "flow": render_flow, "tree": render_flow, "diagram": render_flow,
    "stepper": render_stepper, "example": render_stepper,
    "outcomes": render_table, "table": render_table,
    "callout": render_callout, "note": render_callout, "warning": render_callout,
    "code": render_code, "chip": render_chip, "custom": render_custom,
    "predict": render_predict, "predict-next": render_predict,
    "assumption_toggle": render_assumption, "assumption": render_assumption, "assumption-toggle": render_assumption,
    "self_check": render_self_check, "self-check": render_self_check, "selfcheck": render_self_check,
}
PLAN_KIND = {"flow": "diagram", "tree": "diagram", "diagram": "diagram", "stepper": "stepper",
             "example": "stepper", "outcomes": "table", "table": "table", "claims": "table",
             "callout": "note", "note": "note", "warning": "note", "code": "code", "chip": "label",
             "custom": "custom", "predict": "prediction-pause", "predict-next": "prediction-pause",
             "assumption_toggle": "assumption-experiment", "assumption": "assumption-experiment",
             "assumption-toggle": "assumption-experiment", "self_check": "self-check", "self-check": "self-check",
             "selfcheck": "self-check"}
PLAN_INTERACTION = {"diagram": "switch the figure to full size; read it as a list",
                    "stepper": "step through the example with the buttons or the arrow keys",
                    "prediction-pause": "predict the next step, then reveal it",
                    "assumption-experiment": "flip the assumption and see what breaks",
                    "self-check": "answer the questions and read why each answer is right"}


def _plan_explains(comp: dict) -> str:
    """What a component explains, for the page plan: the author's `explains`, else what it shows."""
    for k in ("explains", "caption", "title", "prompt", "assumption"):
        v = comp.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    if str(comp.get("type") or "").lower() in ("self_check", "self-check", "selfcheck"):
        return "checks the reader can answer questions about this section"
    return ""


def render_component(comp: dict, ctx: Ctx, sec_id: str) -> str:
    ctype = str(comp.get("type") or "").lower()
    if ctype == "claims":
        cid = ctx.unique(comp.get("id") or f"{sec_id}-claims")
        markup, claims = render_claims(comp.get("rows") or comp.get("claims") or [], ctx, cid,
                                       comp.get("caption") or "Claims and where they come from")
        ctx.content.setdefault("_inline_claims", []).extend(claims)
        return markup
    fn = RENDERERS.get(ctype)
    if fn:
        return fn(comp, ctx, sec_id)
    # a component added to the library later: templates/page/components/<type>.html, filled from
    # the component's own keys (escaped), and nothing else — new behaviour hooks in via page.js
    # window.C8.register(<type>, init) on [data-c8-widget=<type>]
    if re.fullmatch(r"[a-z0-9-]+", ctype or "-") and (TEMPLATE_DIR / "components" / f"{ctype}.html").exists():
        parts = load_parts(ctype)
        if "css" in parts or "script" in parts:
            ctx.use_widget(ctype)
        tpl = (parts.get("main") or parts.get(ctype)
               or next(v for k, v in parts.items() if k not in ("css", "script")))
        vals = {k: v for k, v in comp.items() if isinstance(v, (str, int, float))}
        markup = fill(tpl.replace("{{{", "{{").replace("}}}", "}}"), vals)
        clean, removed = sanitize(markup)
        ctx.removed += removed
        return clean
    if comp.get("html"):
        return render_custom(comp, ctx, sec_id)
    raise BuildError(f"section {sec_id}: unknown component type {ctype!r} (no renderer, no "
                     f"templates/page/components/{ctype}.html, no html)")


# ============================================================== glossary (C2)
_TOKEN_RE = re.compile(r"<!--.*?-->|<(?:[^>\"']|\"[^\"]*\"|'[^']*')*>", re.S)
_TAGNAME_RE = re.compile(r"^</?\s*([a-zA-Z][\w:-]*)")
SKIP_GLOSS = {"code", "pre", "kbd", "samp", "script", "style", "svg", "a", "button", "summary", "h1",
              "h2", "h3", "h4", "h5", "h6", "textarea", "select", "option", "label", "title", "nav"}
_SKIP_ATTR_RE = re.compile(r"""class\s*=\s*["'][^"']*\bc8-(?:term-wrap|tip|chip)\b|data-no-gloss|role\s*=\s*["']tooltip""", re.I)
_VOID_RE = {"br", "col", "hr", "img", "input", "wbr", "area", "source", "track", "meta", "link"}


class Glossary:
    def __init__(self, entries: list, scope: str = "page"):
        self.entries = []
        for e in entries or []:
            term = str(e.get("term") or "").strip()
            if not term or not str(e.get("definition") or "").strip():
                continue
            forms = [term] + [str(a).strip() for a in e.get("aliases") or [] if str(a).strip()]
            forms = sorted(set(forms), key=len, reverse=True)
            pats = []
            for f in forms:
                esc = re.escape(html.escape(f, quote=False)).replace(r"\ ", r"\s+")
                pats.append(esc)
            rx = re.compile(r"(?<![\w&#-])(?:" + "|".join(pats) + r")(?![\w-])", re.I)
            self.entries.append({"key": slugify(term), "term": term, "definition": str(e["definition"]).strip(),
                                 "scope": (e.get("scope") or scope), "rx": rx, "longest": len(forms[0])})
        self.seen_page: set = set()
        self.seen_section: set = set()
        self.count = 0
        self.marks: list = []
        self.section = None

    def new_section(self, sid: str):
        self.seen_section = set()
        self.section = sid

    def _seen(self, e) -> bool:
        return e["key"] in (self.seen_section if e["scope"] == "section" else self.seen_page)

    def _mark_seen(self, e):
        self.seen_page.add(e["key"])
        self.seen_section.add(e["key"])

    def _term_html(self, e, matched: str) -> str:
        self.count += 1
        tip_id = f"c8-tip-{e['key']}-{self.count}"
        self.marks.append({"term": e["term"], "text": html.unescape(matched), "section": self.section, "tip": tip_id})
        return part("term", "term", {"key": e["key"], "tip_id": tip_id, "text": html.unescape(matched),
                                     "term": e["term"], "definition": e["definition"]}).strip()

    def _text(self, text: str) -> str:
        out = []
        rest = text
        while rest:
            best = None
            for e in self.entries:
                if self._seen(e):
                    continue
                m = e["rx"].search(rest)
                if m and (best is None or m.start() < best[1].start()
                          or (m.start() == best[1].start() and len(m.group(0)) > len(best[1].group(0)))):
                    best = (e, m)
            if best is None:
                out.append(rest)
                break
            e, m = best
            out.append(rest[:m.start()])
            out.append(self._term_html(e, m.group(0)))
            self._mark_seen(e)
            rest = rest[m.end():]
        return "".join(out)

    def apply(self, markup: str) -> str:
        if not self.entries or not markup:
            return markup
        out = []
        stack: list = []   # (tag, skip)
        pos = 0
        for m in _TOKEN_RE.finditer(markup):
            text = markup[pos:m.start()]
            if text:
                out.append(text if any(s for _, s in stack) else self._text(text))
            tok = m.group(0)
            out.append(tok)
            pos = m.end()
            if tok.startswith("<!--"):
                continue
            nm = _TAGNAME_RE.match(tok)
            if not nm:
                continue
            name = nm.group(1).lower()
            if tok.startswith("</"):
                for i in range(len(stack) - 1, -1, -1):
                    if stack[i][0] == name:
                        del stack[i:]
                        break
                continue
            if tok.endswith("/>") or name in _VOID_RE:
                continue
            skip = name in SKIP_GLOSS or bool(_SKIP_ATTR_RE.search(tok))
            stack.append((name, skip))
        tail = markup[pos:]
        if tail:
            out.append(tail if any(s for _, s in stack) else self._text(tail))
        return "".join(out)


# ============================================================== sections, page
def render_section(sec: dict, ctx: Ctx, gloss: Glossary) -> str:
    title = str(sec.get("title") or "").strip()
    if not title:
        raise BuildError("every section needs a title")
    sid = ctx.unique(sec.get("id") or title)
    sec["_id"] = sid
    summary = sec.get("summary")
    if not summary:
        ctx.warn(f"section {sid} has no one-line summary")
    order = sec.get("order") or ["what", "why", "components", "custom", "how"]
    blocks = []
    run: list = []
    comps_html: list = []
    how_slot: list = []
    plan_elems = []
    for i, comp in enumerate(sec.get("components") or []):
        if not isinstance(comp, dict):
            raise BuildError(f"section {sid}: component #{i + 1} is not an object")
        markup = render_component(comp, ctx, sid)
        (how_slot if comp.get("slot") == "how" else comps_html).append(markup)
        ctype = str(comp.get("type") or "").lower()
        # a known component's plan kind is fixed (a callout's `kind` is note/warning, not a visual);
        # a custom or library-added component says what it draws through its own `kind`
        kind = (PLAN_KIND[ctype] if ctype in PLAN_KIND and ctype != "custom"
                else str(comp.get("kind") or ctype or "custom"))
        plan_elems.append({"id": comp.get("id") or f"{sid}-{i + 1}", "kind": kind,
                           "explains": _plan_explains(comp),
                           "ref": comp.get("ref") or "", "region": "full"})

    def flush():
        if run:
            blocks.append(part("section", "wwh", {"blocks": "\n".join(run)}))
            run.clear()
    for key in order:
        if key in ("what", "why") and sec.get(key):
            run.append(part("section", key, {"text": rich_block(sec[key], ctx)}))
        elif key == "how" and (sec.get("how") or how_slot):
            run.append(part("section", "how", {"label": sec.get("how_label") or "How it works",
                                               "text": rich_block(sec.get("how"), ctx) + "\n".join(how_slot)}))
        elif key == "components" and comps_html:
            flush()
            blocks.append(part("section", "components", {"items": "\n".join(comps_html)}))
        elif key == "custom" and sec.get("custom_html"):
            flush()
            blocks.append(render_custom({"html": sec["custom_html"], "name": sec.get("custom_name") or sid}, ctx, sid))
            # markup cannot be introspected, so the author declares what a custom layout draws
            declared = sec.get("custom_elements") or [{"id": f"{sid}-custom", "kind": sec.get("custom_kind") or "custom",
                                                       "explains": sec.get("custom_explains") or ""}]
            for j, e in enumerate(declared):
                plan_elems.append({"id": e.get("id") or f"{sid}-custom-{j + 1}", "kind": e.get("kind") or "custom",
                                   "explains": e.get("explains") or "", "ref": e.get("ref") or "", "region": "full"})
    flush()
    gloss.new_section(sid)
    summary_html = gloss.apply(rich_inline(summary or "", ctx))
    body = gloss.apply("\n".join(blocks))
    visual = next((e for e in plan_elems if e["kind"] not in ("note", "label", "text")), None)
    acts = [PLAN_INTERACTION[e["kind"]] for e in plan_elems if e["kind"] in PLAN_INTERACTION]
    if 'class="c8-term"' in summary_html + body:
        acts.append("hover, focus or tap a marked term for its definition")
    if 'a class="c8-chip"' in body:
        acts.append("follow a path:line chip to the cited code")
    if 'class="c8-how"' in body:
        acts.append("unfold How it works")
    interaction = sec.get("interaction") or "; ".join(dict.fromkeys(acts))
    ctx.plan_sections.append({
        "id": sid, "idea": summary or title, "title": title,
        "primary_visual": sec.get("primary_visual") or (f"{visual['kind']}: {visual['explains']}" if visual else ""),
        "elements": plan_elems, "interaction": interaction, "uses_full_width": True, "not_video_scene": True})
    return part("section", "section", {"id": sid, "title": title, "summary": summary_html, "body": body})


def load_design(path: Path) -> tuple:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as ex:
        raise BuildError(f"cannot read design system {path}: {ex}")
    pal = data.get("palette") if isinstance(data.get("palette"), dict) else None
    skin = pal if pal and pal.get("roles") else None
    if isinstance(data.get("kit_spec"), dict):
        kit = data["kit_spec"]
    elif isinstance(data.get("kit"), str) and (KIT_DIR / f"{data['kit']}.json").exists():
        kit = json.loads((KIT_DIR / f"{data['kit']}.json").read_text(encoding="utf-8"))
    elif isinstance(data.get("theme"), dict) and isinstance(data.get("palette"), dict):
        kit, skin = data, None
    else:
        raise BuildError(f"{path} holds no kit_spec, no known kit id and is not a kit JSON")
    active = _active(kit, skin)
    errs = validate_kit(active)
    if errs:
        raise BuildError("the active kit fails the outcome rules: " + "; ".join(errs[:6]))
    return kit, skin, active


def print_skin_css(kit: dict, skin) -> str:
    """Paper is paper: under @media print the page uses the kit's LIGHT roles whatever skin the screen
    shows, so a dark-skin reader still prints dark ink on light paper. The values are the kit's own
    role declarations (never a literal chosen here), pinned with !important so they outrank the
    OS-scheme and data-theme blocks."""
    k = _active(kit, skin)
    light = skins(k)["light"]
    lines = _role_vars(light) + _derived_vars(k, light)
    pinned = ["  " + ln.strip().rstrip(";") + " !important;" for ln in lines if ln.strip()]
    return "\n".join(["/* print: the light roles, whatever the screen skin */", "@media print{", " :root{",
                      *[" " + ln for ln in pinned], " }", "}", ""])


def font_css(active: dict, design_path: Path, warnings: list) -> str:
    """@font-face for vendored OFL fonts that are actually on disk; nothing otherwise."""
    out = []
    theme = active.get("theme") or {}
    for v in theme.get("vendorable") or []:
        f = v.get("file")
        lic = str(v.get("licence") or v.get("license") or "")
        if not f:
            continue
        if not lic.upper().startswith("OFL"):
            warnings.append(f"font {v.get('name')!r} not embedded: licence {lic!r} is not OFL")
            continue
        cands = [Path(f)] if Path(f).is_absolute() else [design_path.parent / f, PKG / f]
        p = next((c for c in cands if c.exists()), None)
        if p is None or p.suffix.lower() != ".woff2":
            warnings.append(f"font {v.get('name')!r} not embedded: no .woff2 file at {f!r}")
            continue
        fam = str(v.get("family") or v.get("name")).replace('"', "")
        b64 = base64.b64encode(p.read_bytes()).decode("ascii")
        out.append(f'@font-face{{font-family:"{fam}";src:url(data:font/woff2;base64,{b64}) format("woff2");'
                   f'font-weight:{v.get("weight", "100 900")};font-style:normal;font-display:swap}}')
    return "\n".join(out)


def build(content: dict, content_dir: Path, design_path: Path, layout_js: Path | None,
          out_dir: Path | None = None) -> tuple:
    kit, skin, active = load_design(design_path)
    ctx = Ctx(content, content_dir, layout_js)
    title = str(content.get("title") or "").strip()
    if not title:
        raise BuildError("content needs a title")
    sections = content.get("sections") or []
    if not sections:
        raise BuildError("content needs at least one section")
    gloss = Glossary(content.get("glossary") or [], scope=str(content.get("glossary_scope") or "page"))

    gloss.new_section("top")
    conclusion = content.get("conclusion")
    if not conclusion:
        ctx.warn("no conclusion line — the page should open on its conclusion")
    hero = part("hero", "hero", {"title": title,
                                 "conclusion": gloss.apply(rich_inline(conclusion or "", ctx)),
                                 "lede": gloss.apply(rich_block(content.get("lede"), ctx))})
    ctx.ids.update({"top", "c8-main", "watch", "c8-toc-title"})
    sec_html = [render_section(s, ctx, gloss) for s in sections]

    # C3: the page ENDS with the claims ledger. Top-level claims and claims given inside sections are
    # listed together (an id seen twice is listed once); a page with no claims still ends with the
    # ledger, which says so, and the build warns.
    all_rows = []
    by_id: dict = {}
    for i, c in enumerate(content.get("claims") or [], 1):
        if isinstance(c, dict):
            c = dict(c)
            c.setdefault("id", f"c{i}")
            by_id[str(c["id"])] = c
        all_rows.append(c)
    for c in content.get("_inline_claims") or []:
        same = by_id.get(c["id"])
        if same is not None and str(same.get("claim") or same.get("text") or "").strip() == c["text"] \
                and str(same.get("ref") or "").strip() == c["ref"]:
            continue                      # the same claim shown in a section and listed at the top
        cid_, n = c["id"], 2
        while cid_ in by_id:
            cid_ = f"{c['id']}-{n}"
            n += 1
        row = {"id": cid_, "claim": c["text"], "ref": c["ref"], "status": c["status"], "href": c.get("href")}
        by_id[cid_] = row
        all_rows.append(row)
    sid = ctx.unique(content.get("claims_id") or "claims")
    cid = ctx.unique(sid + "-ledger")
    ctitle = content.get("claims_title") or "Where each claim comes from"
    if all_rows:
        ledger, claims = render_claims(all_rows, ctx, cid, content.get("claims_caption")
                                       or "Each claim, the line it comes from, and how it is known")
        csummary = content.get("claims_summary") or ("Every statement on this page, the file and line it was read "
                                                     "from, and whether it was read in the code or inferred.")
    else:
        claims = []
        ctx.warn("C3: the page records no claims; it ends with an empty ledger. If you cannot point to a "
                 "line, you do not know it: read more, or mark the claim inferred")
        ledger = part("claims", "ledger", {
            "id": cid, "caption": content.get("claims_caption") or "Claims and where they come from",
            "rows": "", "tally": "No claims are recorded for this page: nothing on it has been traced to a line."})
        csummary = content.get("claims_summary") or "No statement on this page has been traced to a file and line yet."
    sections_for_toc_extra = [(sid, ctitle)]
    sec_html.append(part("section", "section", {
        "id": sid, "title": ctitle, "summary": html.escape(csummary, quote=False),
        "body": part("section", "components", {"items": ledger})}))
    # the ledger is traceability apparatus (C3), not a teaching section: it is listed apart so
    # the density gate judges the sections that teach
    ctx.plan_reference.append({"id": sid, "idea": csummary, "title": ctitle, "role": "reference",
                               "primary_visual": "table: claim to path:line to read or inferred",
                               "elements": [{"id": cid, "kind": "table", "explains": "traceability of every claim",
                                             "region": "full"}],
                               "interaction": ("open the cited line from its chip" if ctx.link_base else
                                               "scan each claim against its path:line"),
                               "uses_full_width": True, "not_video_scene": True})

    toc_items = "".join(part("toc", "item", {"id": s["_id"], "title": s["title"]}) for s in sections)
    toc_items += "".join(part("toc", "item", {"id": i, "title": t}) for i, t in sections_for_toc_extra)
    toc = part("toc", "nav", {"label": content.get("toc_label") or "On this page", "items": toc_items})

    tabs, watch, read_attrs = "", "", ""
    captions_embedded = False
    video = content.get("video")
    if isinstance(video, dict) and video.get("src"):
        src = safe_url(video["src"])
        if src is None or src.startswith("#"):
            raise BuildError(f"video.src {video['src']!r} must be a relative path to the sibling mp4")
        track = ""
        if video.get("captions"):
            vtt = safe_url(video["captions"])
            if vtt is None or vtt.startswith("#"):
                raise BuildError(f"video.captions {video['captions']!r} must be a relative path")
            # Chromium refuses a relative <track> from a file:// page (each file is its own origin),
            # so a page opened by double-click would show no captions. When the .vtt is on disk at
            # build time it is embedded as a data: URL, which loads offline everywhere.
            for base in [d for d in (out_dir, content_dir) if d is not None]:
                vp = (Path(base) / vtt).resolve()
                if vp.is_file():
                    vtt = "data:text/vtt;base64," + base64.b64encode(vp.read_bytes()).decode("ascii")
                    captions_embedded = True
                    break
            else:
                ctx.warn("captions file not found next to the page at build time; the relative track loads "
                         "when the page is served, but not from file:// in Chromium-based browsers")
            track = part("video", "track", {"vtt": vtt, "lang": video.get("captions_lang") or content.get("lang") or "en",
                                            "label": video.get("captions_label") or "Captions"})
        speeds = []
        for r in video.get("speeds") or DEFAULT_SPEEDS:
            r = float(r)
            if 0.75 <= r <= 2.0:
                speeds.append(r)
        if 1.0 not in speeds:
            speeds.append(1.0)
        speeds = sorted(set(speeds))
        sp_html = "".join(part("video", "speed", {"rate": f"{r:g}", "label": f"{r:g}×"}) for r in speeds)
        tabs = part("video", "tabs", {})
        watch = part("video", "watch", {"title": video.get("title") or "Watch the walkthrough", "src": src,
                                        "track": track, "speeds": sp_html})
        read_attrs = ' role="tabpanel" aria-labelledby="c8-tab-read"'

    footer = part("footer", "footer", {"snapshot": content.get("snapshot") or "unrecorded",
                                       "date": content.get("build_date") or "undated"})
    claims_json = json.dumps({"claims": [{k: c[k] for k in ("id", "text", "ref", "status")} for c in claims],
                              "read": sum(1 for c in claims if c["status"] == "read"),
                              "inferred": sum(1 for c in claims if c["status"] == "inferred")},
                             ensure_ascii=False).replace("</", "<\\/")
    shell = (TEMPLATE_DIR / "shell.html").read_text(encoding="utf-8")
    page_css = (TEMPLATE_DIR / "page.css").read_text(encoding="utf-8")
    page_js = (TEMPLATE_DIR / "page.js").read_text(encoding="utf-8")
    kit_css = to_css_vars(kit, skin) + print_skin_css(kit, skin)
    for blob, label in ((page_css, "page.css"), (kit_css, "kit css")):
        if "</style" in blob.lower():
            raise BuildError(f"{label} contains a closing style tag")
    # component parts (C6 widgets and any library component that brings css/script): inlined only
    # on a page that uses them, after page.js so window.C8.register exists
    w_css, w_js = [], []
    for w in ctx.widgets:
        wp = load_parts(w)
        if wp.get("css"):
            w_css.append(f"/* component: {w} */\n" + wp["css"])
        if wp.get("script"):
            w_js.append(f'(function () {{\n  "use strict";\n  if (!window.C8) {{ return; }}\n{wp["script"]}}})();')
    if w_css:
        page_css = page_css + "\n" + "\n".join(w_css)
    if w_js:
        page_js = page_js + "\n" + "\n".join(w_js)
    if "</script" in page_js.lower():
        raise BuildError("page.js contains a closing script tag")
    fonts = font_css(active, design_path, ctx.warnings)
    out = fill(shell, {
        "lang": content.get("lang") or "en", "kit_id": active.get("id") or "kit", "title": title,
        "kit_css": kit_css, "font_css": fonts, "page_css": page_css,
        "kicker": content.get("kicker") or title, "tabs": tabs, "read_panel_attrs": read_attrs,
        "toc": toc, "hero": hero, "sections": "\n".join(sec_html), "watch": watch,
        "footer": footer, "claims_json": claims_json, "page_js": page_js})
    leaks = sorted({m.group(0) for m in LOAD_RE.finditer(out)})
    if leaks:
        raise BuildError(f"the built page would load from the network: {leaks[:5]}")
    for m in sorted({m.group(0) for m in TEXT_URL_RE.finditer(out)}):
        ctx.warn(f"a URL is written in the page text (it loads nothing): {m[:80]}")
    summary = {
        "kit": active.get("id"), "palette_family": (active.get("palette") or {}).get("family"),
        "sections": len(sections) + (1 if sections_for_toc_extra else 0),
        "claims_ledger": {"id": sid, "last": True}, "components_inlined": list(ctx.widgets),
        "claims": {"total": len(claims), "read": sum(1 for c in claims if c["status"] == "read"),
                   "inferred": sum(1 for c in claims if c["status"] == "inferred")},
        "glossary_marked": gloss.marks, "flows": ctx.layout_modes,
        "fonts_embedded": fonts.count("@font-face"),
        "removed_unsafe": ctx.removed, "warnings": ctx.warnings,
        "has_video": bool(watch), "captions_embedded": captions_embedded}
    plan = {"kind": "page", "sections": ctx.plan_sections, "reference_sections": ctx.plan_reference}
    return out, summary, plan, claims


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build the self-contained interactive page.")
    ap.add_argument("content", help="page content JSON (schema: templates/page/README.md)")
    ap.add_argument("--design-system", required=True, help="design_system.json or a kit JSON")
    ap.add_argument("--out", required=True, help="output <slug>.html")
    ap.add_argument("--plan-out", help="also write the page plan the visual_density gate reads")
    ap.add_argument("--claims-out", help="also write the claims as JSON for the facts gate")
    ap.add_argument("--layout-js", help="graph layout script (default: scripts/render/layout.js if present)")
    a = ap.parse_args(argv)
    cpath = Path(a.content)
    try:
        content = json.loads(cpath.read_text(encoding="utf-8"))
        if not isinstance(content, dict):
            raise BuildError("content JSON must be an object")
        lj = Path(a.layout_js) if a.layout_js else (HERE / "layout.js")
        page, summary, plan, claims = build(content, cpath.resolve().parent, Path(a.design_system),
                                            lj if lj.exists() else None, Path(a.out).resolve().parent)
    except (OSError, ValueError, BuildError) as ex:
        print(json.dumps({"tool": "build_page", "status": "ERROR", "summary": str(ex)}))
        return EXIT_ERROR
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    if a.plan_out:
        Path(a.plan_out).write_text(json.dumps(plan, indent=1, ensure_ascii=False), encoding="utf-8")
    if a.claims_out:
        Path(a.claims_out).write_text(json.dumps(
            {"claims": [{"id": c["id"], "text": c["text"], "ref": c["ref"], "status": c["status"], "kind": "claim"}
                        for c in claims]}, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"tool": "build_page", "status": "PASS", "out": str(out), "bytes": len(page.encode("utf-8")),
                      **summary}, ensure_ascii=False))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
