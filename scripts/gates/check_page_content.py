#!/usr/bin/env python3
"""
Coordn8 gate — PAGE CONTENT RULES (C4, report-only heuristics).

Seven writing rules for the interactive page, each a pattern over the text (no model judgement).
It REPORTS leads; it never rewrites anything and it never fails a build on its own.

  C4.conclusion_first   the opening (the page's conclusion line, else its first paragraph) states the
                        answer; an opening that walks the mechanism first ("First it loads…",
                        "When a request arrives, the router…", "This section explains…") is flagged
  C4.outside_in         the first teaching section starts from the outside (what it is for, who uses
                        it, what comes in and out); a first section named after a code symbol or about
                        internals, or a code-dense first section, is flagged
  C4.exact_values       vague time/quantity words ("recently", "a few", "several", "soon", "lots of")
                        where an exact date or number belongs
  C4.estimate_marked    an unsourced round figure (1,000 / 50% / 10x / 2 seconds) with no estimate mark
                        (~, about, roughly, estimated) and no path:line chip in the same sentence
  C4.how_word           the delete-the-how-word test: filler that can be deleted with no loss of meaning
                        ("this is how", "the way that", "basically", "actually", "simply", "essentially",
                        "in order to", "just") — say what happens instead
  C4.effect_at_stage    a stage (a stepper step, a numbered step, "at this stage…") that describes the
                        FINAL result ("finally", "eventually", "the end result", "in the end") instead of
                        what that stage did then
  C4.attributes_table   a bullet list of >= 2 items where each item carries >= 3 attributes
                        ("cache: TTL 60 s, 512 MB, LRU") — that is a table

Input: a page content.json (the build_page schema), a built page .html, or .md/.txt.
    python3 scripts/gates/check_page_content.py content.json [--only rule,rule] [--json OUT]
Exit: 0 PASS (no leads) · 1 REVIEW (leads listed) · 2 SKIPPED (no input) · 3 ERROR. Each finding names the
rule, where it is, the text and the concrete fix. Python 3.9, standard library only.
"""
from __future__ import annotations

import argparse
import html as htmllib
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))
from lib.common import EXIT_ERROR, EXIT_PASS, EXIT_REVIEW, EXIT_SKIPPED, emit  # noqa: E402

GATE = "check_page_content"
I = re.IGNORECASE
RULES = ("conclusion_first", "outside_in", "exact_values", "estimate_marked", "how_word", "effect_at_stage",
         "attributes_table")

# ------------------------------------------------------------------ patterns
MECH_OPEN = re.compile(
    r"^\s*(?:(?:first(?:ly)?|to\s+(?:start|begin)|initially|step\s+(?:1|one))\b"
    r"|(?:when|once|after|before|as\s+soon\s+as)\s+(?:a|an|the|each|every|it|we|you)\b[^.]{0,80}?,\s*(?:the|it|we|a|an)\b"
    r"|(?:in\s+order\s+)?to\s+understand\b|let'?s\b|we(?:'ll|\s+will)\s+(?:start|begin|look|walk|go)\b"
    r"|(?:this|the)\s+(?:section|page|document|article|guide)\s+(?:will\s+)?(?:explains?|describes?|covers?|walks|looks|shows)\b"
    r"|here\s+(?:we|is\s+how)\b|in\s+this\s+(?:section|page|guide)\b"
    r"|(?:the\s+)?(?:code|function|method|class|module|script|handler|service|system|program)\s+(?:first\s+)?"
    r"(?:calls|reads|loads|loops|iterates|parses|fetches|imports|initiali[sz]es|instantiates|invokes|starts\s+by)\b"
    r"|it\s+(?:first\s+)?(?:calls|reads|loads|loops|iterates|parses|fetches|imports|initiali[sz]es|starts\s+by)\b)", I)
MECH_VERBS = re.compile(r"\b(?:calls|reads|loads|loops|iterates|parses|fetches|invokes|passes|instantiates|"
                        r"initiali[sz]es|imports|then)\b", I)
ANSWER_MARK = re.compile(r"\b(?:so\s+that|which\s+means|means\s+that|therefore|as\s+a\s+result|the\s+answer|"
                         r"always|never|only|every|no\s+\w+\s+(?:is|are|ever)|guarantees?|ensures?|decides?|is\s+why)\b", I)
INSIDE_TITLE = re.compile(r"(?:\b[a-z]+_[a-z0-9_]+\b|\b[a-z]+[A-Z][A-Za-z0-9]*\b|\w+\(\)|\b(?:internals?|implementation|"
                          r"helpers?|private|under\s+the\s+hood|data\s+structures?|class\s+hierarchy|algorithm|regex|"
                          r"parser|the\s+loop|variables?)\b)")
OUTSIDE_WORDS = re.compile(r"\b(?:what\s+(?:it|this)\s+(?:is|does)|for|who|users?|customers?|callers?|requests?|"
                           r"input|output|overview|purpose|answers?|result|outcome|why)\b", I)
VAGUE = re.compile(r"\b(?:(?<!least )(?<!most )recently|lately|soon|shortly|nowadays|these\s+days|a\s+while\s+(?:ago|back)|some\s+time\s+ago|"
                   r"a\s+few|several|a\s+(?:bunch|handful|couple)\s+of|lots?\s+of|plenty\s+of|numerous|countless|"
                   r"(?:many|most|some)\s+(?:of\s+the\s+)?(?:times|requests|users|cases|calls|files|items|records)|"
                   r"last\s+(?:week|month|year)|next\s+(?:week|month|year)|a\s+lot)\b", I)
ROUND = re.compile(r"(?<![\w.:/-])(?:\d{1,3}(?:,000)+|[1-9]0{2,}|[1-9]\d?0\s?%|[1-9]0?\s?(?:x|×)|"
                   r"[1-9]\d*(?:\.\d+)?\s?(?:k|m|million|billion|thousand)\b|"
                   r"[1-9]0?\s+(?:seconds?|minutes?|hours?|days?|weeks?|ms|milliseconds?))(?![\w.]|-\w)", I)
ESTIMATE_MARK = re.compile(r"~|≈|\b(?:about|around|roughly|approx(?:imately|\.)?|estimated?|estimates?|nearly|almost|"
                           r"up\s+to|at\s+most|at\s+least|on\s+the\s+order\s+of|ballpark|guess|(?:we|i)\s+expect)\b", I)
SOURCED = re.compile(r"\[\[[^\]]+\]\]|c8-chip|`[^`]*:\d+`|\b[\w./-]+\.\w{1,5}:\d+\b|\bmeasured\b|\bconfig(?:ured)?\b|"
                     r"\bset\s+(?:to|at)\b|\bdefault\b|\bconstant\b|\blimit\s+is\b", I)
HOW_WORDS = [
    (re.compile(r"\b(?:this|that|here)\s+is\s+how\b|\bhere'?s\s+how\b|\bthat'?s\s+how\b", I), "\"this is how\""),
    (re.compile(r"\bhow\s+(?:it|this|that)\s+works\s+is\b", I), "\"how it works is\""),
    (re.compile(r"\bthe\s+way\s+(?:that|in\s+which|it\s+works)\b", I), "\"the way that\""),
    (re.compile(r"\bbasically\b", I), "\"basically\""),
    (re.compile(r"\bactually\b", I), "\"actually\""),
    (re.compile(r"\bsimply\b", I), "\"simply\""),
    (re.compile(r"\bessentially\b", I), "\"essentially\""),
    (re.compile(r"\bliterally\b", I), "\"literally\""),
    (re.compile(r"\bin\s+order\s+to\b", I), "\"in order to\""),
    (re.compile(r"\bjust\s+(?=\w+s\b|\w+ed\b|(?:a|an|the)\b)", I), "\"just\""),
]
FINAL_WORDS = re.compile(r"\b(?:finally|eventually|ultimately|in\s+the\s+end|by\s+the\s+end|the\s+end\s+result|"
                         r"the\s+final\s+(?:result|output|answer|outcome|state|page|response)|will\s+(?:eventually|end\s+up)|"
                         r"ends\s+up)\b", I)
STAGE_SENT = re.compile(r"\b(?:at\s+this\s+(?:stage|step|point)|(?:in|at|during)\s+(?:the\s+)?(?:stage|step|phase)\s+\w+|"
                        r"(?:stage|step|phase)\s+\d+)\b", I)
ATTR_SPLIT = re.compile(r"\s*(?:,|;|\|| — | – | - |/)\s*")
CODE_SPAN = re.compile(r"`[^`]+`")
CHIP = re.compile(r"\[\[([^\]]+)\]\]")
ABBR = ("e.g.", "i.e.", "etc.", "vs.", "approx.", "No.", "cf.")


def split_sentences(par: str) -> list:
    s = par
    for a in ABBR:
        s = s.replace(a, a.replace(".", "\x01"))
    s = re.sub(r"(?<=\d)\.(?=\d)", "\x01", s)
    s = re.sub(r"(?<=\w)\.(?=\w)", "\x01", s)
    parts = re.split(r"(?<=[.!?])[\"')\]]?\s+(?=[\"'(\[`]?[A-Z0-9])", s)
    return [p.replace("\x01", ".").strip() for p in parts if p.strip()]


def plain(s) -> str:
    """Readable text: drop tags and the content schema's inline marks, keep chips as 'path:line'."""
    if s is None:
        return ""
    if isinstance(s, dict):
        s = s.get("html") or s.get("text") or ""
    if isinstance(s, (list, tuple)):
        return "\n\n".join(plain(x) for x in s)
    s = htmllib.unescape(re.sub(r"<[^>]+>", " ", str(s)))
    s = re.sub(r"\[([^\]]+)\]\([^)\s]+\)", r"\1", s).replace("**", "")
    return s


def blocks_of(value) -> list:
    """A rich block -> its paragraphs and lists: [("p", text) | ("ul", [items])]."""
    out = []
    if value is None or value == "":
        return out
    if isinstance(value, (list, tuple)):
        for v in value:
            out += blocks_of(v)
        return out
    if isinstance(value, dict):
        return HtmlDoc.from_fragment(str(value.get("html") or "")).blocks
    for para in re.split(r"\n\s*\n", str(value).strip()):
        lines = [ln for ln in para.splitlines() if ln.strip()]
        if lines and all(re.match(r"^\s*(?:[-*•]|\d+[.)])\s+", ln) for ln in lines):
            out.append(("ul", [re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", ln) for ln in lines]))
        elif lines:
            out.append(("p", " ".join(ln.strip() for ln in lines)))
    return out


# ------------------------------------------------------------------ document model
class Doc:
    def __init__(self):
        self.opening = ""            # the conclusion line, else the first paragraph
        self.opening_where = "opening"
        self.sections: list = []     # {title, where, blocks, code_ratio}
        self.steps: list = []        # [(where, [step texts in order])]


class HtmlDoc(HTMLParser):
    """Reads a built page (or an HTML fragment) into paragraphs, lists, steps and section titles."""
    SKIP = {"script", "style", "nav", "footer", "svg", "button", "summary", "code", "pre", "table", "figure"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks: list = []
        self.sections: list = []
        self.conclusion = None
        self.steps: list = []
        self._skip = 0
        self._buf = None
        self._kind = None
        self._list = None
        self._li = None
        self._title = None
        self._stepper = None
        self._step = None
        self._code_chars = 0
        self._text_chars = 0
        self._stack: list = []
        self._skipped: list = []

    @classmethod
    def from_fragment(cls, markup: str) -> "HtmlDoc":
        p = cls()
        p.feed(markup)
        p.close()
        return p

    def _cur(self):
        return self.sections[-1]["blocks"] if self.sections else self.blocks

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class") or ""
        if tag in ("br", "img", "input", "hr", "meta", "link", "wbr"):
            return
        self._stack.append(tag)
        if tag == "section" and "data-c8-section" in a:
            self.sections.append({"title": "", "where": f"section {a.get('id') or len(self.sections) + 1}",
                                  "blocks": [], "code": 0, "chars": 0, "is_ledger": False})
        if self.sections and "data-c8-claims" in a:
            self.sections[-1]["is_ledger"] = True
        if "data-c8-stepper" in a:
            self._stepper = []
        if self._stepper is not None and "data-c8-step" in a:
            self._step = []
        if tag in self.SKIP or (tag == "header" and "c8-header" in cls):
            self._skip += 1
            self._skipped.append(tag)
            return
        if self._skip:
            return
        if tag == "h2" and self.sections:
            self._title = []
        elif tag == "p" and "c8-conclusion" in cls:
            self._buf, self._kind = [], "conclusion"
        elif tag == "p" and self._li is None:
            self._buf, self._kind = [], "p"
        elif tag in ("ul", "ol") and self._li is None:
            self._list = []
        elif tag == "li" and self._list is not None:
            self._li = []

    def handle_endtag(self, tag):
        if tag in ("br", "img", "input", "hr", "meta", "link", "wbr"):
            return
        if self._stack and tag in self._stack:
            while self._stack and self._stack.pop() != tag:
                pass
        if self._skip and self._skipped and tag == self._skipped[-1]:
            self._skipped.pop()
            self._skip -= 1
            return
        if self._skip:
            return
        if tag == "h2" and self._title is not None:
            self.sections[-1]["title"] = " ".join("".join(self._title).split())
            self._title = None
        elif tag == "p" and self._buf is not None and self._li is None:
            text = " ".join("".join(self._buf).split())
            if text:
                if self._kind == "conclusion":
                    self.conclusion = text
                else:
                    self._cur().append(("p", text))
                    if self._step is not None:
                        self._step.append(text)
            self._buf = None
        elif tag == "li" and self._li is not None:
            text = " ".join("".join(self._li).split())
            if self._step is not None and self._stepper is not None:
                self._stepper.append(" ".join(self._step + ([text] if not self._step else [])))
                self._step = None
            elif text:
                self._list.append(text)
            self._li = None
        elif tag in ("ul", "ol") and self._list is not None and self._li is None:
            if self._stepper is not None and tag == "ol":
                self.steps.append((self.sections[-1]["where"] if self.sections else "page", self._stepper))
                self._stepper = None
            elif self._list:
                self._cur().append(("ul", self._list))
            self._list = None

    def handle_data(self, data):
        if self.sections:
            n = len(data.strip())
            self.sections[-1]["chars"] += n
            if self._stack and self._stack[-1] in ("code", "pre"):
                self.sections[-1]["code"] += n
        if self._skip:
            return
        if self._title is not None:
            self._title.append(data)
        if self._li is not None:
            self._li.append(data)
        elif self._buf is not None:
            self._buf.append(data)


def doc_from_html(text: str) -> Doc:
    p = HtmlDoc.from_fragment(text)
    d = Doc()
    teach = [s for s in p.sections if not s["is_ledger"]]
    first_p = next((b[1] for b in p.blocks if b[0] == "p"), None)
    if p.conclusion:
        d.opening, d.opening_where = p.conclusion, "conclusion line"
    elif first_p:
        d.opening, d.opening_where = first_p, "first paragraph"
    elif teach:
        fp = next((b[1] for b in teach[0]["blocks"] if b[0] == "p"), "")
        d.opening, d.opening_where = fp, f"first paragraph of {teach[0]['where']}"
    for s in teach:
        d.sections.append({"title": s["title"], "where": s["where"], "blocks": s["blocks"],
                           "code_ratio": (s["code"] / s["chars"]) if s["chars"] else 0.0})
    d.steps = p.steps
    return d


def doc_from_content(c: dict) -> Doc:
    d = Doc()
    if c.get("conclusion"):
        d.opening, d.opening_where = plain(c["conclusion"]), "conclusion line"
    for i, sec in enumerate(c.get("sections") or [], 1):
        where = f"section {sec.get('id') or sec.get('title') or i}"
        blocks = []
        for key in ("summary", "what", "why", "how"):
            blocks += [(k, v if k == "ul" else plain(v)) for k, v in blocks_of(sec.get(key))]
        raw = " ".join(str(sec.get(k) or "") for k in ("summary", "what", "why", "how"))
        code = sum(len(m) for m in CODE_SPAN.findall(raw)) + sum(len(m) for m in CHIP.findall(raw))
        for comp in sec.get("components") or []:
            if not isinstance(comp, dict):
                continue
            t = str(comp.get("type") or "").lower()
            if t in ("stepper", "example"):
                d.steps.append((f"{where} / {comp.get('title') or 'stepper'}",
                                [plain(st.get("body")) + " " + plain(st.get("title")) for st in comp.get("steps") or []]))
            elif t in ("callout", "note", "warning"):
                blocks += [(k, v if k == "ul" else plain(v)) for k, v in blocks_of(comp.get("body"))]
            elif t == "code":
                code += len(str(comp.get("code") or ""))
                raw += str(comp.get("code") or "")
            elif t == "custom" or comp.get("html"):
                blocks += HtmlDoc.from_fragment(str(comp.get("html") or "")).blocks
        if sec.get("custom_html"):
            blocks += HtmlDoc.from_fragment(str(sec["custom_html"])).blocks
        d.sections.append({"title": str(sec.get("title") or ""), "where": where, "blocks": blocks,
                           "code_ratio": (code / len(raw)) if raw else 0.0})
    if not d.opening:
        lede = blocks_of(c.get("lede"))
        fp = next((v for k, v in lede if k == "p"), None)
        if fp:
            d.opening, d.opening_where = plain(fp), "first paragraph (lede)"
        elif d.sections:
            fp = next((v for k, v in d.sections[0]["blocks"] if k == "p"), "")
            d.opening, d.opening_where = fp, f"first paragraph of {d.sections[0]['where']}"
    return d


def doc_from_markdown(text: str) -> Doc:
    d = Doc()
    cur = None
    pre = []
    body = re.sub(r"```.*?```", lambda m: "\n" + " " * len(m.group(0)) + "\n", text, flags=re.S)
    for chunk in re.split(r"(?m)^(#{1,3}\s+.*)$", body):
        if re.match(r"^#{1,3}\s+", chunk or ""):
            level = len(chunk) - len(chunk.lstrip("#"))
            if level == 1:
                continue
            cur = {"title": chunk.lstrip("#").strip(), "where": f"section {chunk.lstrip('#').strip()}",
                   "blocks": [], "code_ratio": 0.0}
            d.sections.append(cur)
        else:
            bl = [(k, v if k == "ul" else plain(v)) for k, v in blocks_of(chunk)]
            (cur["blocks"] if cur else pre).extend(bl)
            if cur is not None:
                raw = chunk.strip()
                cur["code_ratio"] = (sum(len(m) for m in CODE_SPAN.findall(raw)) / len(raw)) if raw else 0.0
    fp = next((v for k, v in pre if k == "p"), None)
    if fp is None and d.sections:
        fp = next((v for k, v in d.sections[0]["blocks"] if k == "p"), "")
        d.opening_where = f"first paragraph of {d.sections[0]['where']}"
    else:
        d.opening_where = "first paragraph"
    d.opening = fp or ""
    # numbered lists in markdown are stages
    for m in re.finditer(r"(?ms)((?:^\s*\d+[.)]\s+.+?$\n?){2,})", text):
        items = [re.sub(r"^\s*\d+[.)]\s+", "", ln) for ln in m.group(1).splitlines() if ln.strip()]
        d.steps.append(("numbered list", items))
    return d


# ------------------------------------------------------------------ rules
def finding(rule: str, where: str, text: str, message: str) -> dict:
    return {"rule": f"C4.{rule}", "where": where, "text": text[:160], "message": message, "severity": "review"}


def all_sentences(d: Doc):
    if d.opening:
        for s in split_sentences(d.opening):
            yield d.opening_where, s
    for sec in d.sections:
        for k, v in sec["blocks"]:
            if k == "p":
                for s in split_sentences(v):
                    yield sec["where"], s
            else:
                for it in v:
                    yield sec["where"], it
    for where, steps in d.steps:
        for i, st in enumerate(steps, 1):
            for s in split_sentences(st):
                yield f"{where} step {i}", s


def r_conclusion_first(d: Doc) -> list:
    if not d.opening.strip():
        return [finding("conclusion_first", "opening", "", "the page has no opening line: open on the conclusion "
                        "(what the reader should know if they read nothing else)")]
    first = split_sentences(d.opening)[0] if split_sentences(d.opening) else d.opening
    mech = bool(MECH_OPEN.search(first)) or (len(MECH_VERBS.findall(first)) >= 2 and not ANSWER_MARK.search(first))
    if mech:
        return [finding("conclusion_first", d.opening_where, first,
                        "the opening walks the mechanism before the answer: lead with the conclusion (what it "
                        "decides or guarantees), then show how")]
    return []


def r_outside_in(d: Doc) -> list:
    if not d.sections:
        return []
    s0 = d.sections[0]
    text0 = " ".join(v if k == "p" else " ".join(v) for k, v in s0["blocks"])
    out = []
    if INSIDE_TITLE.search(s0["title"]) and not OUTSIDE_WORDS.search(s0["title"]):
        out.append(finding("outside_in", s0["where"], s0["title"],
                           "the first section is named after an internal piece; start outside (what it is for, "
                           "what goes in and comes out) and move inward"))
    elif s0["code_ratio"] > 0.25 and len(text0) > 40:
        out.append(finding("outside_in", s0["where"], s0["title"],
                           f"the first section is {round(100 * s0['code_ratio'])}% code: open on the outside view "
                           f"and bring the code in later sections"))
    return out


def r_exact_values(d: Doc) -> list:
    out = []
    for where, s in all_sentences(d):
        for m in VAGUE.finditer(s):
            out.append(finding("exact_values", where, s,
                               f"\"{m.group(0)}\" is vague: give the exact date or number (or mark it as an estimate)"))
    return out


def r_estimate_marked(d: Doc) -> list:
    out = []
    for where, s in all_sentences(d):
        if ESTIMATE_MARK.search(s) or SOURCED.search(s):
            continue
        for m in ROUND.finditer(s):
            out.append(finding("estimate_marked", where, s,
                               f"\"{m.group(0).strip()}\" reads as exact but carries no source: if it is an estimate "
                               f"mark it (~, about, estimated); if it was read, cite its path:line"))
            break
    return out


def r_how_word(d: Doc) -> list:
    out = []
    for where, s in all_sentences(d):
        for rx, label in HOW_WORDS:
            m = rx.search(s)
            if m:
                out.append(finding("how_word", where, s,
                                   f"{label} can be deleted with no loss: say what happens "
                                   f"(\"X does Y\") instead of announcing how"))
    return out


def r_effect_at_stage(d: Doc) -> list:
    out = []
    for where, steps in d.steps:
        for i, st in enumerate(steps[:-1], 1):          # the last stage may state the result
            m = FINAL_WORDS.search(st)
            if m:
                out.append(finding("effect_at_stage", f"{where} step {i}", st,
                                   f"stage {i} of {len(steps)} describes the final result (\"{m.group(0)}\"): say what "
                                   f"THIS stage did at that moment, and leave the result to the last stage"))
    for where, s in all_sentences(d):
        if "step" in where:
            continue
        if STAGE_SENT.search(s) and FINAL_WORDS.search(s):
            out.append(finding("effect_at_stage", where, s,
                               "a sentence about one stage describes the final result: state the effect at that "
                               "stage (what changed then)"))
    return out


def item_attributes(item: str) -> int:
    t = re.sub(r"^\s*(?:\*\*[^*]+\*\*|`[^`]+`|[^:—–]{1,40}?)\s*(?::|—|–)\s+", "", item, count=1) \
        if re.match(r"^\s*(?:\*\*[^*]+\*\*|`[^`]+`|[^:—–]{1,40}?)\s*(?::|—|–)\s+\S", item) else item
    parts = [p for p in ATTR_SPLIT.split(t) if p.strip()]
    kv = len(re.findall(r"\b\w[\w ]{0,20}?\s*[:=]\s*\S", t))
    return max(len(parts), kv)


def r_attributes_table(d: Doc) -> list:
    out = []
    for sec in d.sections:
        for k, v in sec["blocks"]:
            if k != "ul" or len(v) < 2:
                continue
            counts = [item_attributes(plain(it)) for it in v]
            rich = [c for c in counts if c >= 3]
            if len(rich) >= 2 and len(rich) >= (len(v) + 1) // 2:
                out.append(finding("attributes_table", sec["where"], " | ".join(plain(x) for x in v[:3]),
                                   f"{len(v)} bullets carry {min(rich)}+ attributes each: make it a table (one row "
                                   f"per item, one column per attribute)"))
    return out


RULE_FN = {"conclusion_first": r_conclusion_first, "outside_in": r_outside_in, "exact_values": r_exact_values,
           "estimate_marked": r_estimate_marked, "how_word": r_how_word, "effect_at_stage": r_effect_at_stage,
           "attributes_table": r_attributes_table}


def load_doc(path: Path) -> tuple:
    text = path.read_text(encoding="utf-8")
    suf = path.suffix.lower()
    if suf == ".json":
        data = json.loads(text)
        if not isinstance(data, dict) or not (data.get("sections") or data.get("conclusion")):
            raise ValueError("JSON input must be a page content object (title / conclusion / sections)")
        return doc_from_content(data), "content.json"
    if suf in (".html", ".htm") or text.lstrip().lower().startswith(("<!doctype", "<html")):
        return doc_from_html(text), "html"
    return doc_from_markdown(text), "markdown"


def check(path: Path, only=None) -> dict:
    d, kind = load_doc(path)
    rules = [r for r in RULES if not only or r in only]
    findings = []
    for r in rules:
        findings += RULE_FN[r](d)
    by_rule = {r: sum(1 for f in findings if f["rule"] == f"C4.{r}") for r in rules}
    return {"gate": GATE, "status": "REVIEW" if findings else "PASS", "input": str(path), "kind": kind,
            "rules_run": rules, "counts": by_rule, "findings": findings, "sections": len(d.sections),
            "summary": (f"{len(findings)} lead(s): " + ", ".join(f"{k} {v}" for k, v in by_rule.items() if v)
                        if findings else f"no leads across {len(rules)} content rules"),
            "advisory_note": "Patterns, not judgement: each lead is for a person to look at. Nothing is rewritten."}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="C4 page content rules (report-only heuristics).")
    ap.add_argument("input")
    ap.add_argument("--only", help="comma list of rules: " + ",".join(RULES))
    ap.add_argument("--json", help="also write the report here")
    a = ap.parse_args(argv)
    p = Path(a.input)
    if not p.exists():
        emit({"gate": GATE, "status": "SKIPPED", "summary": f"no input at {p}"})
        return EXIT_SKIPPED
    only = [x.strip().replace("C4.", "") for x in (a.only or "").split(",") if x.strip()]
    bad = [x for x in only if x not in RULES]
    if bad:
        emit({"gate": GATE, "status": "ERROR", "summary": f"unknown rule(s): {bad}"})
        return EXIT_ERROR
    try:
        rep = check(p, only or None)
    except (OSError, ValueError) as ex:
        emit({"gate": GATE, "status": "ERROR", "summary": f"unreadable input: {ex}"})
        return EXIT_ERROR
    if a.json:
        Path(a.json).write_text(json.dumps(rep, indent=1, ensure_ascii=False), encoding="utf-8")
    emit(rep)
    return EXIT_REVIEW if rep["findings"] else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
