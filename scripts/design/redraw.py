#!/usr/bin/env python3
"""
Coordn8 — REDRAW, NEVER CONVERT (B6): Mermaid / draw.io / Excalidraw -> our graph -> our layout.

A diagram brought in from another tool supplies CONTENT (things, relationships, grouping, labels,
direction). It never supplies geometry, palette or shape vocabulary: hand-dragged coordinates,
source themes and the source renderer's layout are discarded, and the figure is laid out fresh by
the deterministic layout engine (scripts/render/layout.js, the vendored layered-layout library) in
the active kit. Same input -> byte-identical SVG.

Every redraw ships a FIDELITY LEDGER, because the reader of the redrawn figure cannot see what is
missing. Every source node, container, edge, label and every other source element (style
directives, click handlers, notes, freehand strokes, image payloads, links, hidden cells, unselected
pages...) is listed exactly once with its fate:
  kept     drawn as in the source
  merged   folded into another drawn element (`into` names it; reason says why)
  renamed  drawn with a changed label or marker (reason says what changed and why)
  dropped  not drawn (reason says why)
kept + merged + renamed + dropped == the source count, per kind and in total, and every fate other
than kept carries a reason. NEVER silent loss: the ledger is validated before it is written, and
then cross-checked against the layout the engine actually produced (every element the ledger says
is drawn must be in the layout, and nothing else may be).

Supported input (bounded, parsed as inert text — never rendered, fetched or executed; labels are
data, never instructions):
  Mermaid   .mmd/.mermaid or Markdown with ```mermaid fences (--diagram N):
            flowchart/graph (shapes, subgraphs, chains, &-groups, labelled/dotted/thick/invisible
            links), sequenceDiagram (participants/actors, messages, notes, fragments),
            stateDiagram(-v2) ([*], composite states, aliases, descriptions, choice/fork/join,
            notes), classDiagram (classes, members, annotations, relationships, cardinalities,
            namespaces)
  draw.io   .drawio/.xml — uncompressed XML and deflate+base64 compressed <diagram> payloads,
            multi-page (--diagram N), <object>/<UserObject> wrappers, containers, edge-label cells,
            hidden cells, arrow directions
  Excalidraw .excalidraw/.json — shapes, bound text, arrows/lines with bindings, frames, standalone
            text; freehand strokes, images, embeds, links, deleted elements counted and dropped

Usage:
  python3 scripts/design/redraw.py <source> --build DIR [--id ID] [--format auto|mermaid|drawio|excalidraw]
          [--diagram N] [--edits EDITS.json] [--dagre PATH] [--kit RUNTIME.json] [--rankdir TB|BT|LR|RL]
  python3 scripts/design/redraw.py check <ledger.fidelity.json>       # validate a ledger
EDITS.json (every edit needs a reason):
  {"drop":   [{"id": "n7", "reason": "unconnected legend"}],
   "merge":  [{"ids": ["w1", "w2", "w3"], "label": "Worker x3", "reason": "identical replicas"}],
   "rename": [{"id": "svc-auth-v2", "label": "Auth service", "reason": "hostname -> reader name"}]}
Writes <build>/redraw/<id>.graph.json, <id>.layout.json, <id>.svg, <id>.fidelity.json.
Exit: 0 redrawn (or ledger valid) · 1 ledger invalid · 2 SKIPPED (no node / no layout library)
      · 3 ERROR (unreadable / unsupported source, bad edit)
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import zlib
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree as ET

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS))
from lib.common import EXIT_ERROR, EXIT_PASS, EXIT_REVIEW, EXIT_SKIPPED, emit  # noqa: E402

GATE = "redraw"
CLI = SCRIPTS / "render" / "layout_cli.js"
CONNECTORS = SCRIPTS / "gates" / "check_connectors.py"
FATES = ("kept", "merged", "renamed", "dropped")
KINDS = ("node", "container", "edge", "label", "other")
MAX_BYTES = 8 * 1024 * 1024
MAX_XML = 32 * 1024 * 1024


class RedrawError(Exception):
    pass


# =========================================================================== intermediate model
class IR:
    """Source content only: nodes (incl. containers), edges, other elements. No geometry kept
    except to infer a reading direction."""

    def __init__(self, fmt: str, grammar: str):
        self.format, self.grammar = fmt, grammar
        self.nodes: dict = {}          # id -> node dict (insertion order kept)
        self.edges: list = []
        self.others: list = []         # dropped-by-nature source elements
        self.rankdir = "TB"

    def node(self, nid, label_raw=None, shape="rect", parent=None, container=False):
        n = self.nodes.get(nid)
        if n is None:
            n = {"id": nid, "label_raw": nid if label_raw is None else label_raw, "shape": shape,
                 "parent": parent, "container": container, "members": [], "annotation": None,
                 "explicit_label": label_raw is not None}
            self.nodes[nid] = n
        else:
            if label_raw is not None and (not n["explicit_label"] or label_raw != nid):
                n["label_raw"], n["explicit_label"] = label_raw, True
            if shape and shape != "rect":
                n["shape"] = shape
            if parent is not None and n["parent"] is None:
                n["parent"] = parent
            n["container"] = n["container"] or container
        return n

    def edge(self, src, dst, label_raw="", **kw):
        e = {"id": kw.pop("id", None) or f"e{len(self.edges) + 1}", "from": src, "to": dst,
             "label_raw": label_raw or "", "dashed": False, "directed": True, "bidirectional": False,
             "invisible": False, "order": len(self.edges) + 1, "change": None, "dangling": None}
        e.update(kw)
        self.edges.append(e)
        return e

    def other(self, what, source="", reason=""):
        self.others.append({"id": f"other:{len(self.others) + 1}:{what}", "what": what,
                            "source": source[:200], "reason": reason})


def clean_text(value: str) -> str:
    """Flatten markup (HTML tags, <br>, entities, markdown emphasis) to plain text; inert."""
    if not value:
        return ""
    t = str(value)
    t = re.sub(r"<br\s*/?>|</p\s*>|</div\s*>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t).replace("\xa0", " ")
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in t.replace("\r", "\n").split("\n")]
    return " ".join(ln for ln in lines if ln).strip()


def unquote_label(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'`":
        s = s[1:-1]
    return s


# =========================================================================== Mermaid
MERMAID_KINDS = {"flowchart": "flowchart", "graph": "flowchart", "sequencediagram": "sequence",
                 "statediagram": "state", "statediagram-v2": "state", "classdiagram": "class",
                 "classdiagram-v2": "class"}
STYLE_DIRECTIVES = ("style ", "classdef ", "class ", "linkstyle ", "click ")
SHAPES = (("(((", ")))", "circle"), ("((", "))", "circle"), ("([", "])", "stadium"), ("{{", "}}", "hexagon"),
          ("[(", ")]", "cylinder"), ("[[", "]]", "subroutine"), ("[/", "/]", "parallelogram"),
          ("[\\", "\\]", "parallelogram"), ("[/", "\\]", "trapezoid"), ("[\\", "/]", "trapezoid"),
          ("[", "]", "rect"), ("(", ")", "round"), ("{", "}", "rhombus"), (">", "]", "asymmetric"))


def mermaid_blocks(text: str, is_md: bool):
    if not is_md:
        return [text]
    blocks, cur, fence = [], None, None
    for line in text.splitlines():
        s = line.strip()
        if cur is None:
            m = re.match(r"^(`{3,}|~{3,})\s*mermaid\b", s)
            if m:
                cur, fence = [], m.group(1)
            continue
        if s.startswith(fence[0] * len(fence)) and set(s) <= {fence[0]}:
            blocks.append("\n".join(cur))
            cur = None
            continue
        cur.append(line)
    return blocks


def _mask(s: str) -> str:
    """Blank quoted spans and bracketed node labels so operators inside them are not read."""
    out, depth, q = [], 0, None
    for ch in s:
        if q:
            out.append("_")
            if ch == q:
                q = None
            continue
        if ch == '"':
            q = ch
            out.append("_")
            continue
        if ch in "[({":
            depth += 1
            out.append(ch if depth == 1 else "_")
            continue
        if ch in "])}" and depth:
            depth -= 1
            out.append(ch if depth == 0 else "_")
            continue
        out.append("_" if depth else ch)
    return "".join(out)


def _split_top(s: str, sep: str):
    m, parts, start = _mask(s), [], 0
    for i, ch in enumerate(m):
        if ch == sep:
            parts.append(s[start:i])
            start = i + 1
    parts.append(s[start:])
    return parts


LAB_OP = re.compile(r"(?P<o1><?(?:--|==|-\.))\s+(?P<lab>[^\s].*?)\s+(?P<o2>(?:-{2,}|={2,}|\.-+)(?:>|x|o)?)(?=[\s\w\"(\[{]|$)")
PLAIN_OP = re.compile(r"<?(?:-\.+-|-{2,}|={2,}|~{3,})(?:>|x(?=[\s\w(\[{])|o(?=[\s\w(\[{]))?")


def _ops(text: str):
    m = _mask(text)
    found, occ = [], []
    for g in LAB_OP.finditer(m):
        tok = g.group("o1") + g.group("o2")
        lab = text[g.start("lab"):g.end("lab")]
        found.append((g.start(), g.end(), tok, lab))
        occ.append((g.start(), g.end()))
    for g in PLAIN_OP.finditer(m):
        if any(a <= g.start() < b for a, b in occ):
            continue
        end, lab = g.end(), ""
        k = end
        while k < len(text) and text[k] in " \t":
            k += 1
        if k < len(text) and text[k] == "|":
            close = text.find("|", k + 1)
            if close != -1:
                lab, end = text[k + 1:close], close + 1
        found.append((g.start(), end, g.group(), lab))
        occ.append((g.start(), end))
    return sorted(found)


def _op_style(tok: str):
    invisible = tok.startswith("~")
    dashed = "." in tok
    head_end = tok.endswith((">", "x", "o"))
    head_start = tok.startswith("<")
    return {"invisible": invisible, "dashed": dashed, "directed": head_end or head_start,
            "bidirectional": head_end and head_start, "reverse": head_start and not head_end}


def _node_expr(ir: IR, expr: str, parent, others_src: str):
    t = expr.strip().rstrip(";").strip()
    if not t:
        return None
    cls = re.search(r":::([\w-]+)\s*$", t)
    if cls:
        ir.other("class-attachment", others_src, "source styling class discarded; the active kit styles the redraw")
        t = t[:cls.start()].strip()
    m = re.match(r"^([\w][\w.\-]*)", t, re.U)
    if not m:
        return None
    nid, rest = m.group(1), t[m.end():].strip()
    if not rest:
        ir.node(nid, None, "rect", parent)
        return nid
    am = re.match(r"^@\{(.*)\}$", rest, re.S)
    if am:
        attrs = dict((k.strip().lower(), unquote_label(v)) for k, _, v in
                     (p.partition(":") for p in _split_top(am.group(1), ",")) if k.strip())
        ir.node(nid, attrs.get("label"), attrs.get("shape", "rect") or "rect", parent)
        return nid
    for o, c, shape in SHAPES:
        if rest.startswith(o) and rest.endswith(c) and len(rest) >= len(o) + len(c):
            ir.node(nid, unquote_label(rest[len(o):len(rest) - len(c)]), shape, parent)
            return nid
    return None


def parse_mermaid_flowchart(ir: IR, lines):
    containers = []
    for ln, raw in lines:
        for text in _split_top(raw, ";"):
            text = text.strip()
            if not text:
                continue
            low = text.lower()
            if low.startswith(STYLE_DIRECTIVES):
                what = "click-handler" if low.startswith("click ") else "style-directive"
                ir.other(what, text, "click targets are never followed" if what == "click-handler"
                         else "source styling discarded; the active kit styles the redraw")
                continue
            if low.startswith("direction "):
                continue
            if low.startswith("subgraph"):
                spec = text[len("subgraph"):].strip()
                parent = containers[-1] if containers else None
                cid = None
                m = re.match(r"^([\w][\w.\-]*)\s*\[(.*)\]$", spec)
                if m:
                    cid, lab = m.group(1), unquote_label(m.group(2))
                elif re.match(r"^[\w][\w.\-]*$", spec):
                    cid, lab = spec, None
                else:
                    cid, lab = f"subgraph-{sum(1 for n in ir.nodes.values() if n['container']) + 1}", unquote_label(spec)
                ir.node(cid, lab, "container", parent, container=True)
                containers.append(cid)
                continue
            if low == "end":
                if containers:
                    containers.pop()
                continue
            parent = containers[-1] if containers else None
            ops = _ops(text)
            if ops:
                segs, cur = [], 0
                for a, b, _t, _l in ops:
                    segs.append(text[cur:a])
                    cur = b
                segs.append(text[cur:])
                groups = []
                for sg in segs:
                    ids = [_node_expr(ir, p, parent, text) for p in _split_top(sg, "&")]
                    if not ids or any(i is None for i in ids):
                        raise RedrawError(f"malformed edge at line {ln}: {text[:80]!r}")
                    groups.append(ids)
                for i, (_a, _b, tok, lab) in enumerate(ops):
                    st = _op_style(tok)
                    for s in groups[i]:
                        for d in groups[i + 1]:
                            src, dst = (d, s) if st["reverse"] else (s, d)
                            ir.edge(src, dst, unquote_label(lab), dashed=st["dashed"], directed=st["directed"],
                                    bidirectional=st["bidirectional"], invisible=st["invisible"])
                continue
            if _node_expr(ir, text, parent, text) is None:
                raise RedrawError(f"cannot read line {ln}: {text[:80]!r}")


SEQ_MSG = re.compile(r"^(?P<a>[\w.:-]+?(?: [\w.:-]+?)*?)\s*(?P<op><<-->>|<<->>|-->>|->>|--x|-x|--\)|-\)|-->|->)\s*[+-]?\s*"
                     r"(?P<b>[\w.:-]+?(?: [\w.:-]+?)*?)\s*:\s*(?P<t>.*)$")


def parse_mermaid_sequence(ir: IR, lines):
    ir.rankdir = "LR"
    depth_stack = []
    for ln, text in lines:
        text = text.strip()
        if not text:
            continue
        low = text.lower()
        m = re.match(r"^(?:create\s+)?(participant|actor)\s+(?:\"([^\"]+)\"|(\S+))(?:\s+as\s+(.+))?$", text, re.I)
        if m:
            quoted, bare, alias = m.group(2), m.group(3), m.group(4)
            nid, lab = (alias or quoted, quoted) if quoted else (bare, alias or bare)
            ir.node(nid.strip(), lab.strip(), "actor" if m.group(1).lower() == "actor" else "lifeline")
            continue
        if low.startswith(STYLE_DIRECTIVES):
            ir.other("style-directive", text, "source styling discarded")
            continue
        if low == "autonumber" or low.startswith("autonumber "):
            ir.other("autonumber", text, "every message is numbered in the redraw anyway")
            continue
        if low.startswith(("activate ", "deactivate ")):
            ir.other("activation", text, "activation bars are not drawn by a layered redraw; message order is kept")
            continue
        if low.startswith("note "):
            ir.other("note", text, "notes are annotations; a layered redraw has no note slot (read them here)")
            continue
        fm = re.match(r"^(alt|opt|loop|par|critical|break|rect)\b\s*(.*)$", text, re.I)
        if fm:
            depth_stack.append(fm.group(1).lower())
            if fm.group(1).lower() == "rect":
                ir.other("highlight", text, "background highlight is styling")
            else:
                ir.other(f"fragment-{fm.group(1).lower()}", text, "fragment boxes are not drawn; the messages inside "
                         "keep their order numbers (the fragment condition is listed here)")
            continue
        if re.match(r"^(else|and|option)\b", text, re.I):
            ir.other("fragment-branch", text, "fragment branch condition is not drawn; listed here")
            continue
        if low == "end":
            if depth_stack:
                depth_stack.pop()
            continue
        mm = SEQ_MSG.match(text)
        if mm:
            a, b, op, t = mm.group("a").strip(), mm.group("b").strip(), mm.group("op"), mm.group("t")
            for p in (a, b):
                if p not in ir.nodes:
                    ir.node(p, None, "lifeline")
            ir.edge(a, b, t.strip(), dashed=op.startswith("--") or op.startswith("<<--"),
                    directed=True, bidirectional=op.startswith("<<"), sequence=True)
            continue
        raise RedrawError(f"cannot read sequence line {ln}: {text[:80]!r}")


def parse_mermaid_state(ir: IR, lines):
    containers, in_note = [], None
    pseudo = {}

    def endpoint(tok, role):
        tok = tok.strip()
        cls = re.search(r":::([\w-]+)\s*$", tok)
        if cls:
            ir.other("class-attachment", tok, "source styling class discarded")
            tok = tok[:cls.start()].strip()
        scope = containers[-1] if containers else None
        if tok == "[*]":
            key = (scope, "start" if role == "src" else "end")
            if key not in pseudo:
                nid = (f"{scope}__" if scope else "__") + key[1]
                pseudo[key] = nid
                ir.node(nid, key[1], "terminal", scope)
            return pseudo[key]
        if not re.match(r"^[\w][\w.\-]*$", tok):
            raise RedrawError(f"cannot read state endpoint {tok!r}")
        ir.node(tok, None, "state", scope)
        return tok

    for ln, text in lines:
        text = text.strip()
        if not text:
            continue
        if in_note is not None:
            if re.fullmatch(r"end\s+note", text, re.I):
                ir.other("note", in_note, "notes are annotations; a layered redraw has no note slot")
                in_note = None
            else:
                in_note += " " + text
            continue
        nm = re.fullmatch(r"note\s+(?:left|right)\s+of\s+[^\s:]+(?:\s*:\s*(.*))?", text, re.I)
        if nm:
            if nm.group(1) is None:
                in_note = text
            else:
                ir.other("note", text, "notes are annotations; a layered redraw has no note slot")
            continue
        low = text.lower()
        if low.startswith(STYLE_DIRECTIVES):
            ir.other("style-directive", text, "source styling discarded")
            continue
        if text == "}":
            if containers:
                containers.pop()
            continue
        if text == "--":
            ir.other("concurrency-separator", text, "concurrent-region separator is not drawn; both regions are")
            continue
        dm = re.match(r"^direction\s+(TB|TD|LR|RL|BT)$", text, re.I)
        if dm:
            if not containers:
                ir.rankdir = {"TD": "TB"}.get(dm.group(1).upper(), dm.group(1).upper())
            continue
        parent = containers[-1] if containers else None
        m = re.match(r"^state\s+([\w][\w.\-]*)\s*\{$", text, re.I)
        if m:
            ir.node(m.group(1), None, "container", parent, container=True)
            containers.append(m.group(1))
            continue
        m = re.match(r'^state\s+"(.*?)"\s+as\s+([\w][\w.\-]*)\s*(\{)?$', text, re.I)
        if m:
            ir.node(m.group(2), m.group(1), "container" if m.group(3) else "state", parent, container=bool(m.group(3)))
            if m.group(3):
                containers.append(m.group(2))
            continue
        m = re.match(r"^state\s+([\w][\w.\-]*)\s+<<(fork|join|choice)>>$", text, re.I)
        if m:
            ir.node(m.group(1), None, m.group(2).lower(), parent)
            continue
        if "-->" in text:
            a, b = text.split("-->", 1)
            lab = ""
            sep = re.search(r"(?<!:):(?!:)", b)
            if sep:
                lab, b = b[sep.end():], b[:sep.start()]
            s, d = endpoint(a, "src"), endpoint(b, "dst")
            ir.edge(s, d, lab.strip())
            continue
        m = re.match(r"^([\w][\w.\-]*)\s*:\s*(.+)$", text)
        if m:
            n = ir.node(m.group(1), None, "state", parent)
            n["members"].append(m.group(2).strip())
            continue
        m = re.match(r"^state\s+([\w][\w.\-]*)$", text, re.I)
        if m:
            ir.node(m.group(1), None, "state", parent)
            continue
        if re.fullmatch(r"[\w][\w.\-]*", text):
            ir.node(text, None, "state", parent)
            continue
        raise RedrawError(f"cannot read state line {ln}: {text[:80]!r}")
    if in_note is not None:
        raise RedrawError("unterminated state note")
    # state descriptions are the state's own text: fold them into its label as members
    for n in ir.nodes.values():
        if n["members"]:
            n["member_kind"] = "description"


CLASS_REL = re.compile(r'^(?P<a>[\w][\w.~]*)\s*(?:"(?P<ca>[^"]*)")?\s*(?P<op><\|--|--\|>|<\|\.\.|\.\.\|>|\*--|--\*|o--|--o|'
                       r'<--|-->|\.\.>|<\.\.|\.\.|--)\s*(?:"(?P<cb>[^"]*)")?\s*(?P<b>[\w][\w.~]*)\s*(?::\s*(?P<t>.*))?$')
REL_MEANING = {  # op -> (reverse?, relation word or None, dashed)
    "<|--": (True, "extends", False), "--|>": (False, "extends", False),
    "<|..": (True, "implements", True), "..|>": (False, "implements", True),
    "*--": (False, "is composed of", False), "--*": (True, "is composed of", False),
    "o--": (False, "aggregates", False), "--o": (True, "aggregates", False),
    "-->": (False, None, False), "<--": (True, None, False),
    "..>": (False, None, True), "<..": (True, None, True),
    "..": (False, "undirected", True), "--": (False, "undirected", False),
}


def _class_name(tok: str):
    return re.sub(r"~(.*?)~", r"<\1>", tok)


def parse_mermaid_class(ir: IR, lines):
    body, namespaces = None, []

    def cls(tok):
        nid = re.sub(r"~.*?~", "", tok)
        n = ir.node(nid, html.escape(_class_name(tok), quote=False) if "~" in tok else None, "class",
                    namespaces[-1] if namespaces else None)
        n["member_kind"] = "member"
        if "~" in tok:
            n["label_note"] = "generic marker ~T~ written as <T>"
        return n

    for ln, text in lines:
        text = text.strip()
        if not text:
            continue
        if body is not None:
            if text == "}":
                body = None
                continue
            am = re.match(r"^<<(.+)>>$", text)
            if am:
                body["annotation"] = am.group(1).strip()
            else:
                body["members"].append(text)
            continue
        low = text.lower()
        if low.startswith(("style ", "classdef ", "cssclass ", "click ", "link ", "callback ")):
            ir.other("style-directive" if not low.startswith(("click", "link", "callback")) else "click-handler", text,
                     "source styling / interaction discarded")
            continue
        if low.startswith("note"):
            ir.other("note", text, "notes are annotations; a layered redraw has no note slot")
            continue
        if low.startswith("direction "):
            d = text.split()[1].upper()
            ir.rankdir = {"TD": "TB"}.get(d, d)
            continue
        m = re.match(r"^namespace\s+([\w][\w.]*)\s*\{$", text)
        if m:
            ir.node(m.group(1), None, "container", namespaces[-1] if namespaces else None, container=True)
            namespaces.append(m.group(1))
            continue
        if text == "}" and namespaces:
            namespaces.pop()
            continue
        m = re.match(r"^class\s+([\w][\w.~]*)\s*(?:\[\"(.*)\"\])?\s*(\{)?\s*$", text)
        if m:
            n = cls(m.group(1))
            if m.group(2):
                n["label_raw"], n["explicit_label"] = m.group(2), True
            if m.group(3):
                body = n
            continue
        m = re.match(r"^<<(.+)>>\s+([\w][\w.~]*)$", text)
        if m:
            cls(m.group(2))["annotation"] = m.group(1).strip()
            continue
        rm = CLASS_REL.match(text)
        if rm:
            a, b = cls(rm.group("a"))["id"], cls(rm.group("b"))["id"]
            rev, word, dashed = REL_MEANING[rm.group("op")]
            src, dst = (b, a) if rev else (a, b)
            card = None
            if rm.group("ca") or rm.group("cb"):
                ca, cb = (rm.group("cb"), rm.group("ca")) if rev else (rm.group("ca"), rm.group("cb"))
                card = f"{ca or '?'} to {cb or '?'}"
            ir.edge(src, dst, (rm.group("t") or "").strip(), dashed=dashed, directed=word != "undirected",
                    relation=word if word != "undirected" else None, cardinality=card)
            continue
        m = re.match(r"^([\w][\w.~]*)\s*:\s*(.+)$", text)
        if m:
            cls(m.group(1))["members"].append(m.group(2).strip())
            continue
        raise RedrawError(f"cannot read class line {ln}: {text[:80]!r}")
    if body is not None:
        raise RedrawError("unterminated class body")


def parse_mermaid(text: str, is_md: bool, diagram: int):
    blocks = mermaid_blocks(text, is_md)
    if not blocks:
        raise RedrawError("no mermaid diagram found (no ```mermaid fence)")
    if not 0 <= diagram < len(blocks):
        raise RedrawError(f"diagram {diagram} not found (have 0..{len(blocks) - 1})")
    src = blocks[diagram]
    lines = []
    in_front = False
    for i, raw in enumerate(src.splitlines(), 1):
        s = raw.strip()
        if i == 1 and s == "---":
            in_front = True
            continue
        if in_front:
            if s == "---":
                in_front = False
            continue
        s = re.sub(r"%%.*$", "", raw).rstrip()
        if s.strip().startswith("%%"):
            continue
        lines.append((i, s))
    while lines and not lines[0][1].strip():
        lines.pop(0)
    if not lines:
        raise RedrawError("empty mermaid diagram")
    head = lines[0][1].strip().split()
    kind = MERMAID_KINDS.get(head[0].lower()) if head else None
    if kind is None:
        raise RedrawError(f"unsupported mermaid kind {head[0] if head else ''!r}: supported are flowchart/graph, "
                          "sequenceDiagram, stateDiagram(-v2), classDiagram")
    ir = IR("mermaid", kind)
    if kind == "flowchart" and len(head) > 1:
        ir.rankdir = {"TD": "TB"}.get(head[1].upper(), head[1].upper())
        if ir.rankdir not in ("TB", "BT", "LR", "RL"):
            ir.rankdir = "TB"
    body = lines[1:]
    {"flowchart": parse_mermaid_flowchart, "sequence": parse_mermaid_sequence,
     "state": parse_mermaid_state, "class": parse_mermaid_class}[kind](ir, body)
    for j in range(len(blocks)):
        if j != diagram:
            ir.other(f"unselected-diagram-{j}", blocks[j][:80], f"another diagram in the file; redraw it with --diagram {j}")
    return ir


# =========================================================================== draw.io
def _inflate(payload: str):
    try:
        raw = base64.b64decode(payload.strip(), validate=False)
    except Exception:  # noqa: BLE001
        return None
    for wbits in (-15, 15, 47):
        try:
            d = zlib.decompressobj(wbits)
            out = d.decompress(raw, MAX_XML + 1)
            if len(out) > MAX_XML:
                raise RedrawError("decoded diagram exceeds the size limit")
            return unquote(out.decode("utf-8", "replace"))
        except zlib.error:
            continue
    return None


def _style(s):
    out = {}
    for part in (s or "").split(";"):
        part = part.strip()
        if part:
            k, sep, v = part.partition("=")
            out[k.strip()] = v.strip() if sep else "1"
    return out


def _safe_xml(x: str):
    if "<!DOCTYPE" in x.upper() or "<!ENTITY" in x.upper():
        raise RedrawError("DTD/entity declarations are not supported")
    try:
        return ET.fromstring(x)
    except ET.ParseError as e:
        raise RedrawError(f"malformed XML ({e})")


def parse_drawio(text: str, diagram: int):
    t = text.strip()
    if "<mxfile" not in t and "<mxGraphModel" not in t:
        inf = _inflate(t)
        if not inf or "<mxGraphModel" not in inf:
            raise RedrawError("not a draw.io file (no mxfile / mxGraphModel / payload)")
        t = inf
    root = _safe_xml(t)
    pages = [root] if root.tag == "mxGraphModel" else root.findall("diagram")
    if not pages:
        raise RedrawError("mxfile contains no <diagram> pages")
    if not 0 <= diagram < len(pages):
        raise RedrawError(f"page {diagram} not found (have 0..{len(pages) - 1})")
    page = pages[diagram]
    ir = IR("drawio", "mxgraph")
    encoding = "uncompressed"
    model = page if page.tag == "mxGraphModel" else page.find(".//mxGraphModel")
    if model is None:
        inf = _inflate(page.text or "")
        if not inf:
            raise RedrawError(f"page {diagram}: cannot decode its compressed payload")
        model = _safe_xml(inf)
        if model.tag != "mxGraphModel":
            model = model.find(".//mxGraphModel")
        encoding = "deflate+base64"
    ir.encoding = encoding
    for j, p in enumerate(pages):
        if j != diagram:
            ir.other(f"unselected-page-{j}", p.get("name") or f"page {j}",
                     f"another page in the file; redraw it with --diagram {j}")
    rootel = model.find("root") if model is not None else None
    if rootel is None:
        raise RedrawError("page has no <root>")
    raw, order = {}, []
    for el in rootel:
        if el.tag in ("object", "UserObject"):
            cell = el.find("mxCell")
            if cell is None:
                continue
            cid, value, attrs = el.get("id") or "", el.get("label", ""), dict(el.attrib)
        elif el.tag == "mxCell":
            cell, cid, value, attrs = el, el.get("id") or "", el.get("value", ""), {}
        else:
            continue
        if not cid:
            continue
        if cid in raw:
            raise RedrawError(f"duplicate cell id {cid!r}")
        raw[cid] = {"cell": cell, "value": value, "attrs": attrs}
        order.append(cid)
    hidden_memo = {}

    def hidden(cid):
        seen = set()
        cur = cid
        while cur in raw and cur not in seen:
            if cur in hidden_memo:
                return hidden_memo[cur]
            seen.add(cur)
            if raw[cur]["cell"].get("visible") == "0":
                for s in seen:
                    hidden_memo[s] = True
                return True
            cur = raw[cur]["cell"].get("parent") or ""
        for s in seen:
            hidden_memo[s] = False
        return False

    vertices = {cid for cid in order if raw[cid]["cell"].get("vertex") == "1"}
    edges_ = {cid for cid in order if raw[cid]["cell"].get("edge") == "1"}
    # structural cells: the root (no parent) and layers (parent = root, neither vertex nor edge)
    for cid in order:
        c = raw[cid]["cell"]
        if cid not in vertices and cid not in edges_:
            if raw[cid]["value"].strip():
                ir.other("layer", raw[cid]["value"], "layer name is not drawn; its contents are")
            continue
    edge_label_cells = {}
    geo = {}
    for cid in order:
        if cid not in vertices:
            continue
        c = raw[cid]["cell"]
        st = _style(c.get("style"))
        par = c.get("parent")
        if par in edges_ or "edgeLabel" in st:
            edge_label_cells.setdefault(par, []).append(cid)
            continue
        if hidden(cid):
            ir.node(cid, clean_text(raw[cid]["value"]) or None, "hidden", None)["hidden"] = True
            continue
        g = c.find("mxGeometry")
        if g is not None:
            try:
                geo[cid] = (float(g.get("x", 0) or 0), float(g.get("y", 0) or 0),
                            float(g.get("width", 0) or 0), float(g.get("height", 0) or 0))
            except ValueError:
                pass
        label_raw = raw[cid]["value"]
        n = ir.node(cid, label_raw, "rect", par if par in vertices else None,
                    container=st.get("container") == "1" or "swimlane" in st or st.get("shape") == "swimlane")
        n["drawio_label_raw"] = label_raw
        if "image" in st and st.get("image"):
            ir.other("image-payload", cid, "embedded image is never re-embedded; the element is drawn as a labelled box")
        for k, v in sorted(raw[cid]["attrs"].items()):
            if k in ("id", "label", "placeholders") or not str(v).strip():
                continue
            if k == "link":
                ir.other("link", cid, "link target discarded and never followed")
            elif k == "tooltip":
                ir.other("tooltip", f"{cid}: {v}", "tooltip text is not drawn in a static figure (listed here)")
            else:
                ir.other(f"property-{k}", f"{cid}: {k}={v}", "custom property is metadata, not drawn (listed here)")
    # parent links resolved after every vertex is known
    for n in ir.nodes.values():
        c = raw[n["id"]]["cell"]
        par = c.get("parent")
        if par in ir.nodes and par != n["id"]:
            n["parent"] = par
            ir.nodes[par]["container"] = True
    for cid in order:
        if cid not in edges_:
            continue
        c = raw[cid]["cell"]
        st = _style(c.get("style"))
        src, dst = c.get("source"), c.get("target")
        sh = st.get("startArrow", "none") not in ("none", "0", "")
        eh = st.get("endArrow", "classic") not in ("none", "0", "")
        if sh and not eh:
            src, dst = dst, src
        label_raw = raw[cid]["value"]
        e = ir.edge(src, dst, label_raw, id=cid, dashed=st.get("dashed") == "1", directed=sh or eh,
                    bidirectional=sh and eh)
        e["label_cells"] = [(lc, raw[lc]["value"]) for lc in edge_label_cells.get(cid, [])]
        if hidden(cid):
            e["hidden"] = True
        if raw[cid]["attrs"].get("link"):
            ir.other("link", cid, "link target discarded and never followed")
    if geo:
        xs = [x for x, _, w, _ in geo.values()] + [x + w for x, _, w, _ in geo.values()]
        ys = [y for _, y, _, h in geo.values()] + [y + h for _, y, _, h in geo.values()]
        ir.rankdir = "LR" if (max(xs) - min(xs)) > 1.3 * (max(ys) - min(ys)) else "TB"
    for n in ir.nodes.values():
        n["label_raw"] = n["label_raw"] or ""
    return ir


# =========================================================================== Excalidraw
EX_NODE = {"rectangle": "rect", "ellipse": "ellipse", "diamond": "rhombus", "image": "image",
           "embeddable": "embed", "iframe": "embed"}


def parse_excalidraw(text: str):
    try:
        doc = json.loads(text, parse_constant=lambda t: (_ for _ in ()).throw(RedrawError(f"{t} is not finite")))
    except (ValueError, RecursionError) as e:
        raise RedrawError(f"not valid Excalidraw JSON ({e})")
    if not isinstance(doc, dict) or doc.get("type") != "excalidraw" or not isinstance(doc.get("elements"), list):
        raise RedrawError("not an Excalidraw scene (missing type: excalidraw / elements)")
    ir = IR("excalidraw", "scene")
    els = [e for e in doc["elements"] if isinstance(e, dict) and isinstance(e.get("id"), str)]
    ids = set()
    for e in els:
        if e["id"] in ids:
            raise RedrawError(f"duplicate element id {e['id']!r}")
        ids.add(e["id"])
    live = []
    for e in els:
        if e.get("isDeleted"):
            ir.other("deleted-element", e["id"], "deleted in the source (still stored in the file)")
        else:
            live.append(e)
    foldable = {e["id"] for e in live if e.get("type") in EX_NODE or e.get("type") in ("arrow", "line")}
    bound = {}
    for e in live:
        if e.get("type") == "text" and e.get("containerId") in foldable:
            bound.setdefault(e["containerId"], []).append((e["id"], e.get("text") or ""))
    frames = set()
    geo = []
    for e in live:
        t = e.get("type")
        if isinstance(e.get("link"), str) and e.get("link"):
            ir.other("link", e["id"], "element link discarded and never followed")
        if t in ("arrow", "line"):
            continue
        if t == "text":
            if e.get("containerId") in foldable:
                continue
            n = ir.node(e["id"], e.get("text") or "", "text")
        elif t in ("frame", "magicframe"):
            frames.add(e["id"])
            n = ir.node(e["id"], e.get("name") or "", "frame", container=True)
        elif t in EX_NODE:
            parts = bound.get(e["id"], [])
            n = ir.node(e["id"], " ".join(p for _, p in parts), EX_NODE[t])
            n["bound_text_ids"] = [i for i, _ in parts]
            if t == "image":
                ir.other("image-payload", e["id"], "image pixels never cross into the redraw; drawn as a labelled box")
            if t in ("embeddable", "iframe"):
                ir.other("embed", e["id"], "embedded content is never loaded; drawn as a labelled box")
        elif t == "freedraw":
            ir.other("freedraw-stroke", e["id"], "freehand stroke is decoration, not content")
            continue
        elif t in ("selection", "laser"):
            ir.other("editor-artifact", e["id"], "editor-only element, no content")
            continue
        else:
            ir.other(f"unknown-element-{t}", e["id"], "element type this redraw does not map; its meaning is not guessed")
            continue
        try:
            geo.append((float(e.get("x", 0)), float(e.get("y", 0)), float(e.get("width", 0)), float(e.get("height", 0))))
        except (TypeError, ValueError):
            pass
    for e in live:
        fid = e.get("frameId")
        if isinstance(fid, str) and fid in frames and e["id"] in ir.nodes and e["id"] != fid:
            ir.nodes[e["id"]]["parent"] = fid
    clusters = {}
    for e in live:
        for g in e.get("groupIds") or []:
            if isinstance(g, str):
                clusters.setdefault(g, 0)
                clusters[g] += 1
    for g in sorted(clusters):
        ir.other(f"group-cluster", g, "an unlabelled selection group is not drawn as a frame; its members are drawn")
    for e in live:
        t = e.get("type")
        if t not in ("arrow", "line"):
            continue
        sh = e.get("startArrowhead")
        eh = e.get("endArrowhead", "arrow" if t == "arrow" else None)

        def bid(key):
            b = e.get(key)
            return b.get("elementId") if isinstance(b, dict) and isinstance(b.get("elementId"), str) else None
        s, d = bid("startBinding"), bid("endBinding")
        if sh and not eh:
            s, d = d, s
        parts = bound.get(e["id"], [])
        ed = ir.edge(s, d, " ".join(p for _, p in parts), id=e["id"],
                     dashed=e.get("strokeStyle") in ("dashed", "dotted"), directed=bool(sh or eh),
                     bidirectional=bool(sh) and bool(eh))
        ed["bound_text_ids"] = [i for i, _ in parts]
    if geo:
        xs = [g[0] for g in geo] + [g[0] + g[2] for g in geo]
        ys = [g[1] for g in geo] + [g[1] + g[3] for g in geo]
        ir.rankdir = "LR" if (max(xs) - min(xs)) > 1.3 * (max(ys) - min(ys)) else "TB"
    return ir


# =========================================================================== ledger + graph
class Ledger:
    def __init__(self):
        self.entries = []

    def add(self, kind, sid, source, fate, reason="", **kw):
        e = {"kind": kind, "id": sid, "source": source, "fate": fate}
        if reason:
            e["reason"] = reason
        e.update({k: v for k, v in kw.items() if v is not None})
        self.entries.append(e)
        return e


def load_edits(path):
    if not path:
        return {"drop": [], "merge": [], "rename": []}
    try:
        ed = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise RedrawError(f"cannot read edits {path}: {e}")
    out = {"drop": ed.get("drop", []) or [], "merge": ed.get("merge", []) or [], "rename": ed.get("rename", []) or []}
    for kind in out:
        for item in out[kind]:
            if not isinstance(item, dict) or not str(item.get("reason", "")).strip():
                raise RedrawError(f"every {kind} edit needs a non-empty reason: {item!r}")
    return out


def build(ir: IR, edits, rid: str):
    """IR + edits -> (graph JSON, ledger entries). Every source element lands in the ledger once."""
    L = Ledger()
    nodes = ir.nodes
    for o in ir.others:
        L.add("other", o["id"], o["source"], "dropped", o["reason"], what=o["what"])
    known = set(nodes) | {e["id"] for e in ir.edges}
    for kind in ("drop", "rename"):
        for it in edits[kind]:
            if it.get("id") not in known:
                raise RedrawError(f"{kind} edit names unknown id {it.get('id')!r}")
    for it in edits["merge"]:
        ids = it.get("ids") or []
        if len(ids) < 2 or any(i not in nodes for i in ids):
            raise RedrawError(f"merge edit needs >= 2 known node ids: {ids!r}")
    drop_reason = {it["id"]: it["reason"] for it in edits["drop"]}
    rename = {it["id"]: it for it in edits["rename"]}
    merged_into, merge_reason, merge_label = {}, {}, {}
    for it in edits["merge"]:
        head = it["ids"][0]
        merge_label[head] = it.get("label")
        for i in it["ids"]:
            merged_into[i] = head
            merge_reason[i] = it["reason"]

    # ---- which nodes are drawn, and as what
    def own_text(n):
        if ir.format == "mermaid" and not n.get("explicit_label"):
            return n["id"]
        return clean_text(n["label_raw"]) if n["label_raw"] else ""

    def markup_changed(n):
        raw = n["id"] if (ir.format == "mermaid" and not n.get("explicit_label")) else (n["label_raw"] or "")
        return re.sub(r"\s+", " ", str(raw)).strip() != own_text(n)

    children = {nid: [] for nid in nodes}
    for n in nodes.values():
        if n["parent"] in children and n["parent"] != n["id"]:
            children[n["parent"]].append(n["id"])
    degree = {nid: 0 for nid in nodes}
    for e in ir.edges:
        for k in ("from", "to"):
            if e[k] in degree:
                degree[e[k]] += 1
    leaf_ok = {}
    for nid, n in nodes.items():
        if n.get("hidden"):
            leaf_ok[nid] = ("dropped", "hidden in the source (visible=0)")
        elif nid in drop_reason:
            leaf_ok[nid] = ("dropped", drop_reason[nid])
        elif (ir.format != "mermaid" and not own_text(n) and degree[nid] == 0 and not children[nid]
              and not n.get("members")):
            leaf_ok[nid] = ("dropped", "decorative shape: no label, no connections, no children")
        else:
            leaf_ok[nid] = None
    # a container is drawn as a GROUP only while something live sits in it; otherwise it is a node
    is_group = {nid: any(leaf_ok[c] is None for c in children[nid]) for nid in nodes}
    drawn_nodes = {nid for nid in nodes if not is_group[nid] and leaf_ok[nid] is None
                   and merged_into.get(nid, nid) == nid}

    def descendants_drawn(gid):
        return any(c in drawn_nodes or (is_group[c] and leaf_ok[c] is None and descendants_drawn(c))
                   for c in children[gid])
    drawn_groups = {gid for gid in nodes if is_group[gid] and leaf_ok[gid] is None and descendants_drawn(gid)}

    def group_of(nid):
        p = nodes[nid]["parent"]
        while p is not None and p not in drawn_groups:
            p = nodes[p]["parent"] if p in nodes else None
        return p

    def first_member(gid):
        for c in children[gid]:
            if c in drawn_nodes:
                return c
            if c in drawn_groups:
                r = first_member(c)
                if r:
                    return r
        return None

    graph_nodes, graph_groups = [], []
    for nid, n in nodes.items():
        txt = own_text(n)
        src = txt or nid
        target, final = None, None
        if is_group[nid]:
            if nid in drawn_groups:
                final = rename[nid]["label"] if nid in rename else txt
                L.add("container", nid, src, "renamed" if nid in rename else "kept",
                      rename[nid]["reason"] if nid in rename else "", drawn_as=f"group:{nid}")
                graph_groups.append({"id": nid, "label": final, "parent": group_of(nid)})
                target = f"group:{nid}"
            else:
                L.add("container", nid, src, "dropped", leaf_ok[nid][1] if leaf_ok[nid] else
                      "every member was dropped or merged away; nothing left to group")
        elif leaf_ok[nid] is not None:
            L.add("node", nid, src, "dropped", leaf_ok[nid][1])
        elif nid in merged_into:
            head = merged_into[nid]
            target = f"node:{head}"
            if nid == head:
                final = merge_label.get(head) or txt
                others = [m for m, h in merged_into.items() if h == head and m != head]
                changed = final != txt
                L.add("node", nid, src, "renamed" if changed else "kept",
                      f"absorbs {', '.join(others)}: {merge_reason[nid]}" if changed else "", drawn_as=target,
                      absorbs=others)
            else:
                L.add("node", nid, src, "merged", merge_reason[nid], into=head)
        else:
            target = f"node:{nid}"
            final = rename[nid]["label"] if nid in rename else txt
            L.add("node", nid, src, "renamed" if nid in rename else "kept",
                  rename[nid]["reason"] if nid in rename else "", drawn_as=target)
        # this element's own text
        if txt:
            if target is None:
                L.add("label", f"{nid}#label", txt, "dropped", "its element was not drawn (see the element's entry)")
            elif nid in merged_into and nid != merged_into[nid]:
                L.add("label", f"{nid}#label", txt, "merged", merge_reason[nid], into=target)
            elif final != txt:
                why = rename[nid]["reason"] if nid in rename else merge_reason.get(nid, "")
                L.add("label", f"{nid}#label", txt, "renamed", why, drawn_as=target, drawn_text=final)
            elif markup_changed(n):
                L.add("label", f"{nid}#label", txt, "renamed", n.get("label_note") or "source markup flattened to plain text",
                      drawn_as=target, drawn_text=final)
            else:
                L.add("label", f"{nid}#label", txt, "kept", drawn_as=target)
        extras = []
        if n.get("annotation"):
            extras.append(("annotation", f"«{n['annotation']}»"))
        for i, m in enumerate(n.get("members") or []):
            extras.append((f"{n.get('member_kind', 'member')}{i + 1}", clean_text(m)))
        folded = []
        for tag, t in extras:
            if target is None:
                L.add("label", f"{nid}#{tag}", t, "dropped", "its element was not drawn")
            else:
                why = ("state description folded into the state box text" if tag.startswith("description")
                       else "class annotation folded into the box text" if tag == "annotation"
                       else "class member folded into the box text")
                L.add("label", f"{nid}#{tag}", t, "merged", why, into=target)
                folded.append(t)
        if nid in drawn_nodes:
            text = final if final is not None else txt
            if folded:
                ann = [t for t in folded if t.startswith("«")]
                rest = [t for t in folded if not t.startswith("«")]
                text = " ".join(ann + [text]) + (": " + "; ".join(rest) if rest else "")
            graph_nodes.append({"id": nid, "label": text, "group": group_of(nid)})

    # ---- edges
    drawn_edges = []
    seen_pairs = {}
    for e in ir.edges:
        eid = e["id"]
        src_desc = f"{e['from']} -> {e['to']}"
        lab_text = clean_text(e["label_raw"])
        cells = e.get("label_cells") or []
        reason, fate, into, endpoints_note = None, None, None, []
        if e.get("hidden"):
            fate, reason = "dropped", "hidden in the source (visible=0)"
        elif e["invisible"]:
            fate, reason = "dropped", "invisible layout hint (~~~), not content"
        elif e["from"] not in nodes or e["to"] not in nodes:
            fate, reason = "dropped", "dangling: an endpoint is missing in the source"
        elif eid in drop_reason:
            fate, reason = "dropped", drop_reason[eid]
        a, b = e.get("from"), e.get("to")
        if fate is None:
            ends = []
            for end in (a, b):
                cur = end
                if cur in merged_into:
                    cur = merged_into[cur]
                    endpoints_note.append(f"endpoint '{end}' merged into '{cur}'")
                if cur in drawn_groups or (is_group.get(cur) and cur not in drawn_nodes):
                    fm = first_member(cur) if cur in drawn_groups else None
                    if fm is None:
                        fate, reason = "dropped", f"endpoint '{end}' is a container with nothing drawn in it"
                        break
                    endpoints_note.append(f"endpoint container '{cur}' is drawn as a group; the line attaches to "
                                          f"its first member '{fm}' (the layout connects nodes, not groups)")
                    cur = fm
                if cur not in drawn_nodes and fate is None:
                    why = leaf_ok.get(cur)
                    fate, reason = "dropped", f"endpoint '{end}' was not drawn" + (f" ({why[1]})" if why else "")
                    break
                ends.append(cur)
            if fate is None:
                a, b = ends
                if merged_into.get(e["from"]) and merged_into.get(e["from"]) == merged_into.get(e["to"]):
                    fate, reason = "dropped", f"both ends were merged into '{a}'; the line would be internal to it"
        changes = list(endpoints_note)
        label = lab_text
        if fate is None:
            if e.get("relation"):
                label = (lab_text + " — " if lab_text else "") + e["relation"]
                changes.append(f"relationship marker ({e['relation']}) written as label text: the redraw has one "
                               "arrowhead style")
            if e.get("cardinality"):
                label = (label + " " if label else "") + f"({e['cardinality']})"
                changes.append("cardinalities folded into the label")
            if e.get("sequence"):
                label = f"{e['order_seq']}. {label}" if label else f"{e['order_seq']}."
                changes.append("message order made explicit as a number: a layered layout does not show time")
            if not e["directed"]:
                changes.append("undirected link drawn with the house arrowhead (from -> to)")
            elif e["bidirectional"]:
                changes.append("two-way arrow drawn one way (from -> to); read it both ways")
            key = (a, b, label)
            if key in seen_pairs and endpoints_note:
                fate, into, reason = "merged", f"edge:{seen_pairs[key]}", "parallel duplicate after a merge"
        if fate is None:
            fate = "renamed" if changes else "kept"
            reason = "; ".join(changes) if changes else ""
            seen_pairs[(a, b, label)] = eid
            drawn_edges.append({"id": eid, "from": a, "to": b, "label": label or None, "dashed": bool(e["dashed"])})
        L.add("edge", eid, src_desc, fate, reason or "", into=into, drawn_as=f"edge:{eid}" if fate in ("kept", "renamed")
              else None, drawn_endpoints=[a, b] if fate in ("kept", "renamed") and [a, b] != [e["from"], e["to"]] else None)
        # edge label(s)
        if lab_text:
            if fate in ("kept", "renamed"):
                if label != lab_text:
                    L.add("label", f"{eid}#label", lab_text, "renamed", "label extended (see the edge's reason)",
                          drawn_as=f"edge:{eid}", drawn_text=label)
                elif clean_text(e["label_raw"]) != re.sub(r"\s+", " ", e["label_raw"]).strip():
                    L.add("label", f"{eid}#label", lab_text, "renamed", "source markup flattened to plain text",
                          drawn_as=f"edge:{eid}", drawn_text=label)
                else:
                    L.add("label", f"{eid}#label", lab_text, "kept", drawn_as=f"edge:{eid}")
            elif fate == "merged":
                L.add("label", f"{eid}#label", lab_text, "merged", "its edge was merged", into=into)
            else:
                L.add("label", f"{eid}#label", lab_text, "dropped", "its edge was not drawn")
        for lc, txt in cells:
            t = clean_text(txt)
            if not t:
                L.add("other", f"{lc}#empty-label-cell", lc, "dropped", "empty edge-label cell")
                continue
            if fate in ("kept", "renamed"):
                drawn_edges[-1]["label"] = " / ".join(p for p in [drawn_edges[-1]["label"], t] if p)
                L.add("label", f"{lc}#label", t, "merged", "edge-label cell folded into its connector's label",
                      into=f"edge:{eid}")
            elif fate == "merged":
                L.add("label", f"{lc}#label", t, "merged", "its edge was merged", into=into)
            else:
                L.add("label", f"{lc}#label", t, "dropped", "its edge was not drawn")
    graph = {"id": rid, "title": rid, "rankdir": ir.rankdir, "type": f"redraw:{ir.format}-{ir.grammar}",
             "nodes": graph_nodes, "edges": drawn_edges}
    if graph_groups:
        graph["groups"] = graph_groups
    return graph, L.entries


def summarise(entries):
    by_kind = {k: {f: 0 for f in FATES} for k in KINDS}
    for e in entries:
        by_kind.setdefault(e["kind"], {f: 0 for f in FATES})
        if e["fate"] in by_kind[e["kind"]]:
            by_kind[e["kind"]][e["fate"]] += 1
    source_counts = {k: sum(v.values()) for k, v in by_kind.items()}
    source_counts["total"] = sum(source_counts[k] for k in by_kind)
    fates = {f: sum(by_kind[k][f] for k in by_kind) for f in FATES}
    return by_kind, source_counts, fates


def validate_ledger(L) -> list:
    """Every problem with a ledger; [] means valid. Independent of how it was produced."""
    probs = []
    ents = L.get("entries")
    if not isinstance(ents, list):
        return ["ledger has no entries list"]
    sc = L.get("source_counts") or {}
    seen = set()
    counted = {k: 0 for k in KINDS}
    fate_tot = {f: 0 for f in FATES}
    for i, e in enumerate(ents):
        k, f = e.get("kind"), e.get("fate")
        if k not in KINDS:
            probs.append(f"entry {i}: unknown kind {k!r}")
            continue
        if f not in FATES:
            probs.append(f"entry {i} ({k} {e.get('id')!r}): unknown fate {f!r}")
            continue
        key = (k, e.get("id"))
        if key in seen:
            probs.append(f"entry {i}: {k} {e.get('id')!r} is listed twice")
        seen.add(key)
        counted[k] += 1
        fate_tot[f] += 1
        if f != "kept" and not str(e.get("reason", "")).strip():
            probs.append(f"{k} {e.get('id')!r} is {f} without a reason — silent loss is not allowed")
        if f == "merged" and not e.get("into"):
            probs.append(f"{k} {e.get('id')!r} is merged but does not say into what")
    for k in KINDS:
        if sc.get(k, 0) != counted[k]:
            probs.append(f"source has {sc.get(k, 0)} {k}(s) but the ledger accounts for {counted[k]}")
    if sc.get("total", 0) != sum(counted.values()):
        probs.append(f"source total {sc.get('total', 0)} != entries {sum(counted.values())}")
    if sum(fate_tot.values()) != sc.get("total", -1):
        probs.append("kept + merged + renamed + dropped != source total")
    declared = L.get("fates")
    if isinstance(declared, dict) and any(declared.get(f, 0) != fate_tot[f] for f in FATES):
        probs.append(f"declared fate totals {declared} do not match the entries {fate_tot}")
    return probs


def cross_check(graph, layout, entries) -> list:
    probs = []
    ln = {n["id"] for n in layout.get("nodes", [])}
    le = {e["id"] for e in layout.get("edges", [])}
    lg = {g["id"] for g in layout.get("groups", [])}
    gn = {n["id"] for n in graph["nodes"]}
    ge = {e["id"] for e in graph["edges"]}
    gg = {g["id"] for g in graph.get("groups", [])}
    if ln != gn:
        probs.append(f"layout nodes differ from the ledger's drawn nodes: missing {sorted(gn - ln)}, extra {sorted(ln - gn)}")
    if le != ge:
        probs.append(f"layout edges differ: missing {sorted(ge - le)}, extra {sorted(le - ge)}")
    if lg != gg:
        probs.append(f"layout groups differ: missing {sorted(gg - lg)}, extra {sorted(lg - gg)}")
    claimed = set()
    for e in entries:
        d = e.get("drawn_as")
        if d and e["fate"] in ("kept", "renamed"):
            claimed.add(d)
    for d in claimed:
        kind, _, i = d.partition(":")
        pool = {"node": ln, "edge": le, "group": lg}.get(kind, set())
        if i not in pool:
            probs.append(f"ledger says {d} is drawn but the layout has no such element")
    return probs


# =========================================================================== driver
def detect_format(path: Path, text: str, forced: str):
    if forced and forced != "auto":
        return forced
    suf = path.suffix.lower()
    name = path.name.lower()
    if name.endswith((".excalidraw", ".excalidraw.json")):
        return "excalidraw"
    if suf in (".drawio",) or name.endswith(".drawio.xml"):
        return "drawio"
    if suf in (".mmd", ".mermaid", ".md", ".markdown"):
        return "mermaid"
    t = text.lstrip()
    if t.startswith("{") and '"excalidraw"' in t[:400]:
        return "excalidraw"
    if "<mxfile" in t[:2000] or "<mxGraphModel" in t[:2000]:
        return "drawio"
    return "mermaid"


def find_dagre(arg):
    for c in (arg, os.environ.get("COORDN8_DAGRE"), SCRIPTS / "render" / "vendor" / "dagre.min.js"):
        if c and Path(c).is_file():
            return Path(c)
    return None


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", s.lower()).strip("-") or "diagram"


def redraw(args) -> int:
    src = Path(args.source)
    try:
        data = src.read_bytes()
    except OSError as e:
        emit({"gate": GATE, "status": "ERROR", "summary": f"cannot read {src}: {e}"})
        return EXIT_ERROR
    if len(data) > MAX_BYTES:
        emit({"gate": GATE, "status": "ERROR", "summary": f"source exceeds {MAX_BYTES} bytes"})
        return EXIT_ERROR
    text = data.decode("utf-8-sig", "replace")
    fmt = detect_format(src, text, args.format)
    stem = src.name.split(".")[0]
    rid = slug(args.id or stem)
    try:
        if fmt == "mermaid":
            ir = parse_mermaid(text, src.suffix.lower() in (".md", ".markdown"), args.diagram)
        elif fmt == "drawio":
            ir = parse_drawio(text, args.diagram)
        elif fmt == "excalidraw":
            ir = parse_excalidraw(text)
        else:
            raise RedrawError(f"unknown format {fmt!r}")
        seq = 0
        for e in ir.edges:
            if e.get("sequence"):
                seq += 1
                e["order_seq"] = seq
        edits = load_edits(args.edits)
        graph, entries = build(ir, edits, rid)
    except RedrawError as e:
        emit({"gate": GATE, "status": "ERROR", "summary": str(e)})
        return EXIT_ERROR
    if args.rankdir:
        graph["rankdir"] = args.rankdir
    by_kind, source_counts, fates = summarise(entries)
    ledger = {"schema": 1, "id": rid,
              "source": {"name": src.name, "sha256": hashlib.sha256(data).hexdigest(), "format": fmt,
                         "grammar": ir.grammar, "encoding": getattr(ir, "encoding", None)},
              "method": "redraw, never convert: source geometry, palette and renderer layout are discarded; content "
                        "is laid out fresh by the deterministic layout engine",
              "source_counts": source_counts, "fates": fates, "by_kind": by_kind,
              "drawn_counts": {"nodes": len(graph["nodes"]), "groups": len(graph.get("groups", [])),
                               "edges": len(graph["edges"])},
              "entries": entries}
    probs = validate_ledger(ledger)
    if probs:
        emit({"gate": GATE, "status": "ERROR", "summary": "internal: the ledger does not balance", "problems": probs})
        return EXIT_ERROR
    out = Path(args.build) / "redraw"
    out.mkdir(parents=True, exist_ok=True)
    gpath, lpath, spath, fpath = (out / f"{rid}.graph.json", out / f"{rid}.layout.json", out / f"{rid}.svg",
                                  out / f"{rid}.fidelity.json")
    gpath.write_text(json.dumps(graph, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    node = shutil.which("node")
    dagre = find_dagre(args.dagre)
    if not graph["nodes"]:
        ledger["verified_against_layout"] = False
        ledger["layout_note"] = "nothing left to draw"
        fpath.write_text(json.dumps(ledger, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        emit({"gate": GATE, "status": "REVIEW", "summary": "every source element was dropped; nothing to draw",
              "ledger": str(fpath)})
        return EXIT_REVIEW
    if not node or not dagre:
        ledger["verified_against_layout"] = False
        ledger["layout_note"] = "SKIPPED: " + ("node not found" if not node else "vendored layout library not found")
        fpath.write_text(json.dumps(ledger, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        emit({"gate": GATE, "status": "SKIPPED", "summary": ledger["layout_note"], "ledger": str(fpath)})
        return EXIT_SKIPPED
    cmd = [node, str(CLI), str(gpath), "--dagre", str(dagre), "--json", str(lpath), "--svg", str(spath), "--id", rid]
    if args.kit:
        cmd += ["--kit", args.kit]
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        emit({"gate": GATE, "status": "ERROR", "summary": "layout timed out"})
        return EXIT_ERROR
    if cp.returncode != 0:
        emit({"gate": GATE, "status": "ERROR", "summary": f"layout failed: {cp.stderr.strip()[-300:]}"})
        return EXIT_ERROR
    layout = json.loads(lpath.read_text(encoding="utf-8"))
    xprobs = cross_check(graph, layout, entries)
    ledger["verified_against_layout"] = not xprobs
    ledger["svg_sha256"] = hashlib.sha256(spath.read_bytes()).hexdigest()
    ledger["layout_warnings"] = layout.get("warnings", [])
    fpath.write_text(json.dumps(ledger, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    gate_status = None
    if CONNECTORS.is_file():
        g = subprocess.run([sys.executable, str(CONNECTORS), str(lpath)], capture_output=True, text=True, timeout=120)
        try:
            gate_status = json.loads(g.stdout.strip().splitlines()[0]).get("status")
        except (ValueError, IndexError):
            gate_status = "UNPARSED"
    if xprobs:
        emit({"gate": GATE, "status": "ERROR", "summary": "the layout does not match the ledger", "problems": xprobs,
              "ledger": str(fpath)})
        return EXIT_ERROR
    emit({"gate": GATE, "status": "PASS",
          "summary": f"{fmt} {ir.grammar}: {source_counts['total']} source element(s) -> {len(graph['nodes'])} node(s), "
                     f"{len(graph['edges'])} edge(s), {len(graph.get('groups', []))} group(s); fates {fates}",
          "svg": str(spath), "ledger": str(fpath), "layout": str(lpath), "connector_gate": gate_status,
          "source_counts": source_counts, "fates": fates})
    return EXIT_PASS


def check(path) -> int:
    try:
        L = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        emit({"gate": GATE, "status": "ERROR", "summary": f"cannot read ledger {path}: {e}"})
        return EXIT_ERROR
    probs = validate_ledger(L)
    if probs:
        emit({"gate": GATE, "status": "REVIEW", "summary": f"{len(probs)} ledger problem(s)", "problems": probs})
        return EXIT_REVIEW
    emit({"gate": GATE, "status": "PASS", "summary": f"ledger balances: {L.get('fates')} over "
          f"{(L.get('source_counts') or {}).get('total')} source element(s), every non-kept fate has a reason"})
    return EXIT_PASS


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "check":
        if len(argv) < 2:
            emit({"gate": GATE, "status": "SKIPPED", "summary": "usage: redraw.py check <ledger.json>"})
            return EXIT_SKIPPED
        return check(argv[1])
    ap = argparse.ArgumentParser(description="Redraw a Mermaid / draw.io / Excalidraw diagram with a fidelity ledger.")
    ap.add_argument("source")
    ap.add_argument("--build", required=True)
    ap.add_argument("--id")
    ap.add_argument("--format", default="auto", choices=["auto", "mermaid", "drawio", "excalidraw"])
    ap.add_argument("--diagram", type=int, default=0)
    ap.add_argument("--edits")
    ap.add_argument("--dagre")
    ap.add_argument("--kit")
    ap.add_argument("--rankdir", choices=["TB", "BT", "LR", "RL"])
    return redraw(ap.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
